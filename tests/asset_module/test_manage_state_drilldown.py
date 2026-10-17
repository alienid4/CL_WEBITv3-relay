"""首頁「對帳明細」已納管／已收集真下鑽（2026-10-18）。

以前這兩列的連結是死的（連到 /assets、/san 整份清單，沒帶篩選），點進去的筆數
跟首頁寫的數字完全對不上。改成打 `/api/manage-state?state=<state>` 後，
這支測試釘住：這支 API 回的 `total`／`items` 長度，一定等於首頁
`stateCount()` 讀到的同一份 `manage_state.summarize()["counts"][state]`——
不是另外重算一次，筆數才保證對得起來。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
import manage_state as ms  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_STUB_CREDENTIAL = "test-password-123"
SCAN_TIME = "2026-10-18 09:00:00"


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
    client = TestClient(api.app)
    conn = db.get_connection(db_path)
    try:
        db.create_user(conn, "tester", auth.hash_password(_STUB_CREDENTIAL))
        conn.commit()
    finally:
        conn.close()
    assert client.post(
        "/api/auth/login", json={"username": "tester", "password": _STUB_CREDENTIAL}
    ).status_code == 200
    return client, db_path


def _seed(db_path):
    conn = db.get_connection(db_path)
    try:
        # 兩台已納管（登記＋掃得到＋收得到）、一台未納管（收不到）
        db.insert_hardware(conn, asset_serial="A-OK", hostname="ok1", ip="10.0.1.1",
                           environment="正式")
        db.insert_hardware(conn, asset_serial="A-OK2", hostname="ok2", ip="10.0.1.2",
                           environment="正式")
        db.insert_hardware(conn, asset_serial="A-NO", hostname="no1", ip="10.0.1.3",
                           environment="正式")
        conn.execute("UPDATE hardware SET collect_ok=1 WHERE asset_serial IN ('A-OK','A-OK2')")
        for ip, hn in (("10.0.1.1", "ok1"), ("10.0.1.2", "ok2"), ("10.0.1.3", "no1")):
            conn.execute(
                "INSERT INTO scan_history (scan_time, ip, hostname, scan_ok) "
                "VALUES (?, ?, ?, 1)", (SCAN_TIME, ip, hn))
        conn.commit()
    finally:
        conn.close()


def test_state下鑽筆數等於首頁數字():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        _seed(db_path)

        summary = client.get("/api/manage-state", params={"summary": 1}).json()
        assert "items" not in summary and "rep_items" not in summary
        n = summary["counts"][ms.ONBOARDED]
        assert n == 2, "A-OK／A-OK2 兩台登記＋掃得到＋收得到＝已納管"

        detail = client.get("/api/manage-state", params={"state": ms.ONBOARDED}).json()
        assert detail["state"] == ms.ONBOARDED
        # 核心不變式：首頁數字 = 下鑽頁 total = 下鑽頁 items 的實際筆數
        assert detail["total"] == n
        assert len(detail["items"]) == n
        assert {it["asset_serial"] for it in detail["items"]} == {"A-OK", "A-OK2"}


def test_不合法狀態回400且列出合法值():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        r = client.get("/api/manage-state", params={"state": "亂寫的狀態"})
        assert r.status_code == 400
        assert ms.ONBOARDED in r.json()["detail"]


def test_每個合法狀態下鑽都對得起來():
    """不只測已納管——全部 11 態都要滿足「下鑽筆數＝summarize 的 counts」。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        _seed(db_path)
        summary = client.get("/api/manage-state", params={"summary": 1}).json()
        for state in ms.ALL_STATES:
            detail = client.get("/api/manage-state", params={"state": state}).json()
            assert detail["total"] == summary["counts"][state], state
            assert len(detail["items"]) == summary["counts"][state], state
