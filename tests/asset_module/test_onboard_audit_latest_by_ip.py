"""/api/onboard/audit/latest-by-ip（2026-10-18）：adopt.vue「收集結果」表新增的
「上次納管時間／納管帳號」兩欄，批量查這批 IP 各自最後一次納管稽核紀錄。

為什麼要有：那張表一次可能列幾百個 IP，對每個 IP 各打一次既有的
`/api/onboard/audit` 太貴；而且那支是全站最後 100 筆，不保證包含到查詢想要的那個 IP。
這支一次把一批 IP 的「各自最後一筆」查回來，查不到的 IP 不在回傳裡（前端顯示「—」，
不可以顯示成 0 或空字串讓人以為是「查過、沒有」）。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_PW = "test-password-123"


def _client(tmp):
    db_path = Path(tmp) / "t.db"
    db.init_db(db_path)

    def _override_get_db():
        conn = db.get_connection(db_path)
        try:
            yield conn
        finally:
            conn.close()

    api.app.dependency_overrides[api.get_db] = _override_get_db
    client = TestClient(api.app)
    conn = db.get_connection(db_path)
    try:
        db.create_user(conn, "tester", auth.hash_password(_PW))
        conn.commit()
    finally:
        conn.close()
    assert client.post(
        "/api/auth/login", json={"username": "tester", "password": _PW}
    ).status_code == 200
    return client, db_path


def _audit(db_path, ip, ok, login_user="webit3sc", when="2026-10-17 10:00:00"):
    conn = db.get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO onboard_audit (target_ip, platform, login_user, trigger, "
            "triggered_by, ok, stage, message, created_at) "
            "VALUES (?, 'linux', ?, 'manual', 'tester', ?, 'connect', '', ?)",
            (ip, login_user, 1 if ok else 0, when))
        conn.commit()
    finally:
        conn.close()


def test_只回每個ip最後一次_查不到的不在回傳裡():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        # 192.0.2.1 試過兩次：先失敗、後來成功——要拿到的是後面那筆
        _audit(db_path, "192.0.2.1", ok=False, when="2026-10-16 09:00:00")
        _audit(db_path, "192.0.2.1", ok=True, when="2026-10-17 09:00:00")
        _audit(db_path, "192.0.2.2", ok=True, when="2026-10-15 09:00:00")

        r = client.get("/api/onboard/audit/latest-by-ip",
                       params={"ips": "192.0.2.1,192.0.2.2,192.0.2.99"})
        assert r.status_code == 200
        out = r.json()
        assert set(out) == {"192.0.2.1", "192.0.2.2"}, \
            "查不到紀錄的 IP（192.0.2.99）不該補一筆假資料進來"
        assert out["192.0.2.1"]["created_at"] == "2026-10-17 09:00:00", "要最後一筆，不是第一筆"
        assert out["192.0.2.1"]["ok"] == 1
        assert out["192.0.2.1"]["login_user"] == "webit3sc"
        # 這張表的鐵律：永遠不含憑證
        assert "password" not in out["192.0.2.1"]


def test_ips空字串回空dict_不炸掉():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        r = client.get("/api/onboard/audit/latest-by-ip", params={"ips": ""})
        assert r.status_code == 200
        assert r.json() == {}


def test_要登入才能查():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "t.db"
        db.init_db(db_path)

        def _override_get_db():
            conn = db.get_connection(db_path)
            try:
                yield conn
            finally:
                conn.close()
        api.app.dependency_overrides[api.get_db] = _override_get_db
        client = TestClient(api.app)
        r = client.get("/api/onboard/audit/latest-by-ip", params={"ips": "192.0.2.1"})
        assert r.status_code == 401


def test_POST版_跟GET同一份_失敗帶短原因_且不記進操作紀錄():
    """2026-10-18：一次幾百上千個 IP 時網址太長，改用 POST（唯讀）。最後一次失敗帶 fail。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        _audit(db_path, "192.0.2.1", ok=True, when="2026-10-16 09:00:00")
        _audit(db_path, "192.0.2.1", ok=False, when="2026-10-18 20:50:00")
        r = client.post("/api/onboard/audit/latest-by-ip", json={"ips": ["192.0.2.1", "192.0.2.9"]})
        assert r.status_code == 200
        out = r.json()
        assert set(out) == {"192.0.2.1"}
        assert out["192.0.2.1"]["fail"]["at"] == "2026-10-18 20:50:00"
        assert out["192.0.2.1"]["fail"]["short"]
        assert out == client.get("/api/onboard/audit/latest-by-ip",
                                 params={"ips": "192.0.2.1,192.0.2.9"}).json()
    # 唯讀的 POST 不寫操作紀錄（不然每開一次頁面就多一筆，真正改東西的紀錄會被淹掉）
    assert "/api/onboard/audit/latest-by-ip" in api._READONLY_POST_PATHS
