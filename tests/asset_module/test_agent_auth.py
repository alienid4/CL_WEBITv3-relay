"""Push agent 機器對機器驗證：require_host_key 對有效/撤銷/不存在的 key 各測一次，
/api/agent/facts 確認 upsert 正確且不信任 body 裡的主機識別欄位（防冒充）。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import agent_auth  # noqa: E402
import db  # noqa: E402
import api  # noqa: E402
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


def _seed_asset(db_path, asset_serial):
    conn = db.get_connection(db_path)
    try:
        db.insert_hardware(conn, asset_serial=asset_serial, ip="10.0.0.1", hostname="AP01")
    finally:
        conn.close()


def test_facts_without_key_is_401():
    with tempfile.TemporaryDirectory() as tmp:
        client, _db_path = _client(tmp)
        try:
            resp = client.post("/api/agent/facts", json={"metrics": []})
            assert resp.status_code == 401
        finally:
            api.app.dependency_overrides.clear()


def test_facts_with_invalid_key_is_401():
    with tempfile.TemporaryDirectory() as tmp:
        client, _db_path = _client(tmp)
        try:
            resp = client.post(
                "/api/agent/facts",
                json={"metrics": []},
                headers={"X-Agent-Key": "not-a-real-key"},
            )
            assert resp.status_code == 401
        finally:
            api.app.dependency_overrides.clear()


def test_facts_with_revoked_key_is_401():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _seed_asset(db_path, "AP01")
            conn = db.get_connection(db_path)
            try:
                key = agent_auth.issue_host_key(conn, "AP01")
                agent_auth.revoke_host_key(conn, "AP01")
            finally:
                conn.close()

            resp = client.post(
                "/api/agent/facts", json={"metrics": []}, headers={"X-Agent-Key": key}
            )
            assert resp.status_code == 401
        finally:
            api.app.dependency_overrides.clear()


def test_facts_upsert_overwrites_same_metric_not_duplicates():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _seed_asset(db_path, "AP01")
            conn = db.get_connection(db_path)
            try:
                key = agent_auth.issue_host_key(conn, "AP01")
            finally:
                conn.close()

            body = {"metrics": [
                {"key": "disk_used_pct", "value": 40.0, "unit": "%",
                 "collected_at": "2026-08-14T00:00:00Z"},
            ]}
            r1 = client.post("/api/agent/facts", json=body, headers={"X-Agent-Key": key})
            assert r1.status_code == 200

            body["metrics"][0]["value"] = 55.5
            r2 = client.post("/api/agent/facts", json=body, headers={"X-Agent-Key": key})
            assert r2.status_code == 200

            conn = db.get_connection(db_path)
            try:
                rows = conn.execute(
                    "SELECT * FROM host_metric_latest WHERE asset_serial = 'AP01'"
                ).fetchall()
            finally:
                conn.close()
            assert len(rows) == 1
            assert rows[0]["value"] == 55.5


        finally:
            api.app.dependency_overrides.clear()


def test_facts_ignores_spoofed_asset_serial_in_body():
    """body 沒有 asset_serial 欄位可以帶——就算硬塞進去也要被忽略，寫入的一定是
    key 驗證出來的那台，不是任何 request body 裡自稱的主機。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _seed_asset(db_path, "AP01")
            _seed_asset(db_path, "AP02")
            conn = db.get_connection(db_path)
            try:
                key_ap01 = agent_auth.issue_host_key(conn, "AP01")
            finally:
                conn.close()

            body = {
                "asset_serial": "AP02",  # 試圖冒充 AP02，應該被忽略
                "metrics": [{"key": "disk_used_pct", "value": 99.0, "unit": "%",
                             "collected_at": "2026-08-14T00:00:00Z"}],
            }
            resp = client.post("/api/agent/facts", json=body, headers={"X-Agent-Key": key_ap01})
            assert resp.status_code == 200

            conn = db.get_connection(db_path)
            try:
                ap01_rows = conn.execute(
                    "SELECT * FROM host_metric_latest WHERE asset_serial = 'AP01'"
                ).fetchall()
                ap02_rows = conn.execute(
                    "SELECT * FROM host_metric_latest WHERE asset_serial = 'AP02'"
                ).fetchall()
            finally:
                conn.close()
            assert len(ap01_rows) == 1
            assert len(ap02_rows) == 0
        finally:
            api.app.dependency_overrides.clear()


def test_stage_requires_existing_asset():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            auth_test_helpers_login(client, db_path)
            resp = client.post("/api/agent/stage", json={"asset_serial": "NOPE"})
            assert resp.status_code == 404
        finally:
            api.app.dependency_overrides.clear()


def test_stage_issues_key_and_returns_files():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            auth_test_helpers_login(client, db_path)
            _seed_asset(db_path, "AP01")

            resp = client.post("/api/agent/stage", json={"asset_serial": "AP01"})
            assert resp.status_code == 200
            body = resp.json()
            assert body["asset_serial"] == "AP01"
            assert "agent_key" in body["files"]
            assert "push_agent.sh" in body["files"]
            assert "install.sh" in body["files"]
            assert "collector_url" in body["files"]

            # 剛核發的 key 應該能立刻拿去打 /api/agent/facts
            resp2 = client.post(
                "/api/agent/facts", json={"metrics": []},
                headers={"X-Agent-Key": body["files"]["agent_key"]},
            )
            assert resp2.status_code == 200
        finally:
            api.app.dependency_overrides.clear()


def auth_test_helpers_login(client, db_path):
    """/api/agent/stage 掛了 require_auth（是人在按上機按鈕），要先登入。"""
    import auth

    conn = db.get_connection(db_path)
    try:
        db.create_user(conn, "tester", auth.hash_password("s3cure-pass!"))
    finally:
        conn.close()
    resp = client.post("/api/auth/login", json={"username": "tester", "password": "s3cure-pass!"})
    assert resp.status_code == 200
