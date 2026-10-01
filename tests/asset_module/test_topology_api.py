"""M2 系統聯通圖 API：CRUD + 驗證 + 級聯刪除 + 寫端點需登入。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import auth  # noqa: E402
import db  # noqa: E402
import api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_PW = "test-password-123"


def _client(tmp):
    db_path = Path(tmp) / "test.db"
    db.init_db(db_path)

    def _override_get_db():
        conn = db.get_connection(db_path)
        try:
            yield conn
        finally:
            conn.close()

    api.app.dependency_overrides[api.get_db] = _override_get_db
    return TestClient(api.app), db_path


def _login(client, db_path, username="tester"):
    conn = db.get_connection(db_path)
    try:
        db.create_user(conn, username, auth.hash_password(_PW))
    finally:
        conn.close()
    assert client.post("/api/auth/login", json={"username": username, "password": _PW}).status_code == 200


def test_讀寫端點都要登入():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        try:
            assert client.post("/api/systems", json={"id": "a", "label": "A"}).status_code == 401
            assert client.put("/api/systems/a", json={"label": "B"}).status_code == 401
            assert client.delete("/api/systems/a").status_code == 401
            assert client.post("/api/deps", json={"source": "a", "target": "b"}).status_code == 401
            assert client.delete("/api/deps/1").status_code == 401
            # 讀端點也要登入：這張圖含系統依賴與 SPOF 標記，等於「打哪台全倒」的地圖，
            # 是最不該公開的資料。原本設計為讀免登入，已改掉。健康檢查請用 /api/version。
            assert client.get("/api/topology").status_code == 401
        finally:
            api.app.dependency_overrides.clear()


def test_topology_預設空的():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            data = client.get("/api/topology").json()
            assert data == {"systems": [], "deps": []}
        finally:
            api.app.dependency_overrides.clear()


def test_建立系統與依賴並讀回():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            assert client.post("/api/systems", json={"id": "core", "label": "核心", "health": "ok"}).status_code == 200
            assert client.post("/api/systems", json={"id": "cdb", "label": "客戶DB", "is_spof": True}).status_code == 200
            r = client.post("/api/deps", json={"source": "core", "target": "cdb", "dep_type": "DB 連線"})
            assert r.status_code == 200
            data = client.get("/api/topology").json()
            assert {s["id"] for s in data["systems"]} == {"core", "cdb"}
            assert len(data["deps"]) == 1 and data["deps"][0]["source"] == "core"
            assert next(s for s in data["systems"] if s["id"] == "cdb")["is_spof"] == 1
        finally:
            api.app.dependency_overrides.clear()


def test_驗證_代碼格式_健康度_重複_自我依賴():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            assert client.post("/api/systems", json={"id": "bad id!", "label": "X"}).status_code == 400
            assert client.post("/api/systems", json={"id": "x", "label": "X", "health": "purple"}).status_code == 400
            assert client.post("/api/systems", json={"id": "x", "label": "X"}).status_code == 200
            assert client.post("/api/systems", json={"id": "x", "label": "重複"}).status_code == 409
            assert client.post("/api/deps", json={"source": "x", "target": "x"}).status_code == 400
            assert client.post("/api/deps", json={"source": "x", "target": "nope"}).status_code == 400
        finally:
            api.app.dependency_overrides.clear()


def test_刪除系統級聯移除依賴():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            client.post("/api/systems", json={"id": "a", "label": "A"})
            client.post("/api/systems", json={"id": "b", "label": "B"})
            client.post("/api/deps", json={"source": "a", "target": "b"})
            assert client.delete("/api/systems/b").status_code == 200
            data = client.get("/api/topology").json()
            assert {s["id"] for s in data["systems"]} == {"a"}
            assert data["deps"] == []  # 依賴被級聯刪除
        finally:
            api.app.dependency_overrides.clear()


def test_系統健康度由關聯主機推導_無主機者標成人工(tmp_path, monkeypatch):
    """M2 深化：把系統關聯到主機，健康度自動推導，取代人手動標。

    原本 9 個系統的 ok/warn/err 全是人填的——圖上說「客戶資料庫 ok」，
    但沒人知道現在是不是真的 ok。現在有關聯主機的由實際狀態推導；
    沒有關聯主機的**必須明確標成 manual**，否則等於讓人相信一個沒根據的綠燈。
    """
    import manage_state as ms
    import db as _db

    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    _db.init_db()
    conn = _db.get_connection()
    try:
        conn.execute("INSERT INTO systems (id,label,health) VALUES ('sys_ok','有主機且正常','err')")
        conn.execute("INSERT INTO systems (id,label,health) VALUES ('sys_bad','有主機但失聯','ok')")
        conn.execute("INSERT INTO systems (id,label,health) VALUES ('sys_none','沒關聯主機','ok')")
        # sys_ok 的主機：登記＋掃得到＋收得到 → 已納管 → ok
        _db.insert_hardware(conn, asset_serial="H-1", ip="10.5.0.1", hostname="h1",
                            api_id="sys_ok", environment="正式")
        # sys_bad 的主機：登記了但掃不到 → 失聯 → err
        _db.insert_hardware(conn, asset_serial="H-2", ip="10.5.0.2", hostname="h2",
                            api_id="sys_bad", environment="正式")
        conn.execute("UPDATE hardware SET collect_ok=1 WHERE asset_serial='H-1'")
        conn.execute(
            "INSERT INTO scan_history (scan_time, ip, hostname, scan_ok) "
            "VALUES ('2026-07-19 12:00:00','10.5.0.1','h1',1)")
        conn.commit()

        h = ms.system_health(conn)
    finally:
        conn.close()

    # 有主機的：推導出來，且**覆蓋掉人填的錯誤值**（sys_ok 人填 err，實際是 ok）
    assert h["sys_ok"]["health"] == "ok" and h["sys_ok"]["health_source"] == "derived"
    assert len(h["sys_ok"]["hosts"]) == 1

    # 主機失聯 → 系統 err，即使人填 ok
    assert h["sys_bad"]["health"] == "err" and h["sys_bad"]["health_source"] == "derived"

    # 沒關聯主機 → 沿用人工值，但要標明來源是 manual
    assert h["sys_none"]["health"] == "ok" and h["sys_none"]["health_source"] == "manual"
    assert h["sys_none"]["hosts"] == []


def test_系統健康度取最差的那台():
    """一個系統跑在多台主機上時，只要有一台出事，系統就不能算健康——
    取最好的那台會讓故障被藏起來。"""
    import manage_state as ms
    assert ms._HEALTH_RANK["err"] > ms._HEALTH_RANK["warn"] > ms._HEALTH_RANK["ok"]
    assert ms._STATE_TO_HEALTH[ms.LOST] == "err"
    assert ms._STATE_TO_HEALTH[ms.NOT_ONBOARDED] == "warn"   # 看不到 ≠ 壞了，但也絕不是 ok
    assert ms._STATE_TO_HEALTH[ms.ONBOARDED] == "ok"
