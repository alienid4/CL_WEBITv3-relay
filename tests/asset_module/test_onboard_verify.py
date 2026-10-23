"""POST /api/onboard/verify：本機執行納管指令跑完後立即試連驗證。
覆蓋：成功回 hostname/os、失敗回可行動的原因、沒有資產記錄時自動補一筆最小記錄。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import auth  # noqa: E402
import db  # noqa: E402
import api  # noqa: E402
import manage_state  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


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
        db.create_user(conn, "tester", auth.hash_password("s3cure-pass!"))
    finally:
        conn.close()
    resp = client.post("/api/auth/login", json={"username": "tester", "password": "s3cure-pass!"})
    assert resp.status_code == 200


def test_verify_without_login_is_401():
    with tempfile.TemporaryDirectory() as tmp:
        client, _db_path = _client(tmp)
        try:
            resp = client.post("/api/onboard/verify", json={"ip": "10.0.0.5", "platform": "linux"})
            assert resp.status_code == 401
        finally:
            api.app.dependency_overrides.clear()


def test_verify_success_returns_hostname_and_os(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)

            def fake_collect(conn, only_serial=None, **kw):
                conn.execute(
                    "UPDATE hardware SET hostname = ?, os = ? WHERE asset_serial = ?",
                    ("AP01", "Ubuntu 22.04", only_serial),
                )
                conn.commit()
                return {"updated": 1, "failed": [], "candidates": 1}

            monkeypatch.setattr(manage_state, "collect_facts_into_assets", fake_collect)

            resp = client.post("/api/onboard/verify", json={"ip": "10.0.0.5", "platform": "linux"})
            assert resp.status_code == 200
            body = resp.json()
            assert body["ok"] is True
            assert body["hostname"] == "AP01"
            assert body["os"] == "Ubuntu 22.04"

            # 沒有預先建資產記錄，端點要自動補一筆最小記錄
            conn = db.get_connection(db_path)
            try:
                row = conn.execute("SELECT * FROM hardware WHERE ip = '10.0.0.5'").fetchone()
            finally:
                conn.close()
            assert row is not None
            assert row["hostname"] == "AP01"
        finally:
            api.app.dependency_overrides.clear()


def test_verify_failure_returns_actionable_error(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)

            def fake_collect(conn, only_serial=None, **kw):
                return {
                    "updated": 0,
                    "failed": [{"asset_serial": only_serial, "error": "沒有可用的收集憑證"}],
                    "candidates": 1,
                }

            monkeypatch.setattr(manage_state, "collect_facts_into_assets", fake_collect)

            resp = client.post("/api/onboard/verify", json={"ip": "10.0.0.9", "platform": "windows"})
            assert resp.status_code == 200
            body = resp.json()
            assert body["ok"] is False
            assert "沒有可用的收集憑證" in body["error"]
        finally:
            api.app.dependency_overrides.clear()


def test_verify_rejects_unknown_platform():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            resp = client.post("/api/onboard/verify", json={"ip": "10.0.0.5", "platform": "solaris"})
            assert resp.status_code == 400
        finally:
            api.app.dependency_overrides.clear()
