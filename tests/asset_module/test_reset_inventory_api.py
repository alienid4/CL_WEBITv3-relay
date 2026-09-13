"""清空盤點資料這支端點，從 HTTP 進去到資料真的不見，整條跑一遍。

2026-09-09 公司機回報「我按了清空，但資料都還在」。當下的證據只能證明
**沒清成功**（`import_log` 還有列，而它在清空名單裡），證明不了卡在哪一環。

所以把這條路徑釘死：確認字串、真的刪掉、commit 有生效、該留的有留。
下次再出現同樣症狀，就能直接排除「程式壞了」這個可能，把力氣花在對的地方。
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

_STUB_CREDENTIAL = "test-password-123"


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


def _login(client, db_path):
    conn = db.get_connection(db_path)
    try:
        db.create_user(conn, "tester", auth.hash_password(_STUB_CREDENTIAL))
    finally:
        conn.close()
    assert client.post("/api/auth/login",
                       json={"username": "tester", "password": _STUB_CREDENTIAL}).status_code == 200


def _seed(db_path):
    conn = db.get_connection(db_path)
    try:
        for i in range(3):
            db.insert_hardware(conn, asset_serial=f"HW-{i}", ip=f"10.0.0.{i}", hostname=f"H{i}")
        db.create_import_log(conn, imported_by="tester", hardware_count=3,
                             personnel_count=0, software_count=0, error_count=0,
                             source="cia_excel", file_name="x.xlsx")
        conn.execute("INSERT INTO onboard_audit (target_ip, trigger, ok) VALUES ('10.0.0.1','manual',1)")
        conn.commit()
    finally:
        conn.close()


def _counts(db_path):
    conn = db.get_connection(db_path)
    try:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("hardware", "import_log", "onboard_audit")}
    finally:
        conn.close()


def test_打對確認字串會真的清掉_而且commit有生效():
    """重點在「換一條連線去查還是空的」——沒 commit 的話這裡就會抓到。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            _seed(db_path)
            assert _counts(db_path)["hardware"] == 3

            r = client.post("/api/admin/reset-inventory", json={"confirm": "ClearALL"})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["ok"] is True
            assert body["total_rows"] >= 4, body

            after = _counts(db_path)
            assert after["hardware"] == 0, "資產沒被清掉"
            assert after["import_log"] == 0, "匯入紀錄沒被清掉"
        finally:
            api.app.dependency_overrides.clear()


def test_納管紀錄要留下來(  ):
    """v1.69.0 的修正：清空之後目標主機上的帳號還在，紀錄不能跟著消失。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            _seed(db_path)
            assert client.post("/api/admin/reset-inventory",
                               json={"confirm": "ClearALL"}).status_code == 200
            assert _counts(db_path)["onboard_audit"] == 1, "納管紀錄被清掉了"
        finally:
            api.app.dependency_overrides.clear()


def test_確認字串不對就不可以動到任何一列():
    """大小寫、空白都算不對。**而且失敗時一列都不能少。**"""
    for bad in ("clearall", "CLEARALL", "ClearAll", " ClearALL", "ClearALL ", ""):
        with tempfile.TemporaryDirectory() as tmp:
            client, db_path = _client(tmp)
            try:
                _login(client, db_path)
                _seed(db_path)
                r = client.post("/api/admin/reset-inventory", json={"confirm": bad})
                assert r.status_code == 400, f"{bad!r} 竟然被接受了"
                assert _counts(db_path)["hardware"] == 3, f"{bad!r} 被擋下卻還是刪了資料"
            finally:
                api.app.dependency_overrides.clear()


def test_沒登入不可以清空():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _seed(db_path)
            r = client.post("/api/admin/reset-inventory", json={"confirm": "ClearALL"})
            assert r.status_code == 401, r.status_code
            assert _counts(db_path)["hardware"] == 3
        finally:
            api.app.dependency_overrides.clear()


def test_任何指向清空名單的表都必須自己也在名單裡():
    """這條是整個檔案裡最重要的一條。

    2026-09-09 公司機按清空一直失敗，查了很久才知道：做 CI 圖譜時新增的
    `ci_node` 有外鍵指向 `hardware`，卻沒人記得把它加進清空名單。於是
    `DELETE FROM hardware` 直接違反外鍵、整支端點 500，畫面只顯示
    「清空失敗，請稍後再試」——**看不出是哪裡卡住**。

    這種漏法一定會再發生：以後每加一張指向資產的表，都要有人「記得」回來
    改這份名單。靠記得的規則遲早會漏，所以改成**掃整個 schema 自動檢查**。

    新增表之後這條測試紅了，就是在告訴你：這張表要嘛加進 _RESET_TABLES，
    要嘛加進下面的豁免清單並寫清楚為什麼。
    """
    import sqlite3 as _sq

    #: 指向清空名單但**刻意不清**的表，每一筆都要有理由。
    EXEMPT = {
        # 納管紀錄：清空之後目標主機上的帳號還在，紀錄不能跟著消失（v1.69.0）。
        # 它沒有外鍵指向 hardware，列在這裡是為了讓意圖白紙黑字。
        "onboard_audit",
    }

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "schema.db"
        db.init_db(db_path)
        conn = _sq.connect(str(db_path))
        conn.row_factory = _sq.Row
        try:
            tables = [r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            targets = set(api._RESET_TABLES)
            missing = {}
            changed = True
            while changed:          # 遞移：指向 ci_node 的 ci_edge 也算
                changed = False
                for t in tables:
                    if t in targets or t in EXEMPT:
                        continue
                    for fk in conn.execute(f"PRAGMA foreign_key_list({t})"):
                        if fk["table"] in targets:
                            missing[t] = fk["table"]
                            targets.add(t)
                            changed = True
                            break
            assert not missing, (
                f"這些表指向清空範圍內的表，但自己不在 _RESET_TABLES 裡：{missing}。"
                "清空時會被外鍵擋住整個失敗。請把它們加進 _RESET_TABLES"
                "（排在被指向的表前面），或加進本測試的 EXEMPT 並寫明理由")
        finally:
            conn.close()


def test_子表要排在父表前面():
    """外鍵是**當場**檢查的（`defer_foreign_keys` 在交易外設定不會生效），
    所以刪除順序是有意義的，不是排版問題。"""
    import sqlite3 as _sq

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "schema.db"
        db.init_db(db_path)
        conn = _sq.connect(str(db_path))
        conn.row_factory = _sq.Row
        try:
            order = {t: i for i, t in enumerate(api._RESET_TABLES)}
            for t in api._RESET_TABLES:
                for fk in conn.execute(f"PRAGMA foreign_key_list({t})"):
                    parent = fk["table"]
                    if parent in order and parent != t:
                        assert order[t] < order[parent], (
                            f"{t} 指向 {parent}，但排在它後面——"
                            f"刪到 {parent} 時 {t} 還有資料，外鍵會擋住")
        finally:
            conn.close()
