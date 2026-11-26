"""使用者 11-14：「逾期未重開機」「這不需要」「這也不用」→ 設定 track_uptime_enabled 預設 "0"：
1.4 追蹤處理、首頁待處理、每日異動「開機過久（本次新增）」與摘要信那一行、1.3 頁籤都不出現；計算照留，設 "1" 全部回來。
全部假資料（10.99.x）。"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import daily_digest as dd  # noqa: E402
import daily_risk  # noqa: E402
import db  # noqa: E402
import v2_today  # noqa: E402
import verify_numbers as vn  # noqa: E402


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    c = db.get_connection()
    yield c
    c.close()


def _on(c, v):
    c.execute("INSERT INTO app_settings (key, value) VALUES ('track_uptime_enabled', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (v,))
    c.commit()


def test_預設關(conn):
    assert daily_risk.uptime_enabled(conn) is False
    _on(conn, "1")
    assert daily_risk.uptime_enabled(conn) is True


def test_追蹤處理_關著沒有逾期未重開機這一類_打開就回來_卡片等於下鑽(conn):
    t = v2_today.track(conn, record=False)
    assert "uptime" not in {c["key"] for c in t["categories"]} and not any(r["cat"] == "uptime" for r in t["rows"])
    vn.RESULTS.clear()
    before = vn.FAILS
    vn.check_v2_today(conn)
    assert vn.FAILS == before, [r for r in vn.RESULTS if r["ok"] is False]
    _on(conn, "1")
    t = v2_today.track(conn, record=False)
    assert "uptime" in {c["key"] for c in t["categories"]}
    vn.RESULTS.clear()
    vn.check_v2_today(conn)
    assert vn.FAILS == before, [r for r in vn.RESULTS if r["ok"] is False]


def test_每日異動_關著沒有開機過久類別_摘要信也不寫(conn):
    db.insert_hardware(conn, asset_serial="A-1", hostname="h1", ip="10.99.1.1", os="RHEL 9", asset_status="使用中")
    dd.write_snapshot(conn, datetime(2026, 10, 22, 6, 0))
    dd.write_snapshot(conn, datetime(2026, 10, 23, 6, 0))
    out = dd.diff_for(conn)
    assert "uptime_risk" not in [c["key"] for c in out["categories"]] and out["counts"].get("uptime_risk", 0) == 0
    _subject, body = dd.compose(dict(dd.DEFAULTS), out, None)
    assert "開機超過" not in body
    _on(conn, "1")
    out = dd.diff_for(conn)
    assert "uptime_risk" in [c["key"] for c in out["categories"]]
    assert "開機超過" in dd.compose(dict(dd.DEFAULTS), out, None)[1]


def test_設備風險API_帶開關_資料照算(conn):
    import api
    import auth
    from fastapi.testclient import TestClient

    def _ov():
        cn = db.get_connection()
        try:
            yield cn
        finally:
            cn.close()
    api.app.dependency_overrides[api.get_db] = _ov
    try:
        db.create_user(conn, "admin", auth.hash_password("test-password-123"))
        conn.execute("UPDATE users SET role = 'admin' WHERE username = 'admin'")
        conn.commit()
        cl = TestClient(api.app, raise_server_exceptions=False)
        assert cl.post("/api/auth/login", json={"username": "admin", "password": "test-password-123"}).status_code == 200
        r = cl.get("/api/risk").json()
        assert r["uptime_enabled"] is False and "uptime" in r                       # 關著：資料照給，畫面不顯示
        _on(conn, "1")
        assert cl.get("/api/risk").json()["uptime_enabled"] is True
    finally:
        api.app.dependency_overrides.clear()
