"""S10 done_when 驗證：checks 全 PASS，密碼欄位write-only不回顯。
review:true/high-risk——這片直接碰帳密儲存，測試要逐一確認每個回應路徑都沒洩漏密碼。
"""
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import auth  # noqa: E402
import db  # noqa: E402
import api  # noqa: E402
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


def _login(client, db_path, username="tester", password=_STUB_CREDENTIAL):
    conn = db.get_connection(db_path)
    try:
        db.create_user(conn, username, auth.hash_password(password))
    finally:
        conn.close()
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200


def test_all_connection_endpoints_require_login():
    with tempfile.TemporaryDirectory() as tmp:
        client, _db_path = _client(tmp)
        try:
            body = {"name": "vCenter", "target": "10.0.0.1", "port": 443}
            assert client.get("/api/connections").status_code == 401
            assert client.post("/api/connections", json=body).status_code == 401
            assert client.put("/api/connections/1", json=body).status_code == 401
            assert client.delete("/api/connections/1").status_code == 401
            assert client.post("/api/connections/1/test").status_code == 401
        finally:
            api.app.dependency_overrides.clear()


def test_create_and_list_never_return_password_field():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            resp = client.post(
                "/api/connections",
                json={
                    "name": "vCenter-機房A",
                    "connection_type": "vCenter",
                    "target": "10.0.0.1",
                    "port": 443,
                    "username": "readonly",
                    "password": "real-secret-value",
                },
            )
            assert resp.status_code == 200
            body = resp.json()
            assert "password" not in body
            assert body["has_password"] is True

            list_resp = client.get("/api/connections")
            assert list_resp.status_code == 200
            rows = list_resp.json()
            assert len(rows) == 1
            assert "password" not in rows[0]
            assert "real-secret-value" not in list_resp.text  # 整個回應內容都不能出現明文密碼

            # 資料庫裡實際確實存了密碼（不是沒存，是API層不回顯）
            conn = db.get_connection(db_path)
            try:
                raw = db.get_connection_by_id(conn, rows[0]["id"])
                assert raw["password"] == "real-secret-value"
            finally:
                conn.close()
        finally:
            api.app.dependency_overrides.clear()


def test_update_without_password_keeps_existing_password():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            created = client.post(
                "/api/connections",
                json={"name": "SNMP-A", "target": "10.0.0.2", "port": 161, "password": "original-pw"},
            ).json()

            updated = client.put(
                f"/api/connections/{created['id']}",
                json={"name": "SNMP-A改名", "target": "10.0.0.2", "port": 161},  # 沒帶password
            )
            assert updated.status_code == 200
            assert "password" not in updated.json()

            conn = db.get_connection(db_path)
            try:
                raw = db.get_connection_by_id(conn, created["id"])
                assert raw["password"] == "original-pw"  # 密碼沒被清空
                assert raw["name"] == "SNMP-A改名"  # 其他欄位有更新
            finally:
                conn.close()
        finally:
            api.app.dependency_overrides.clear()


def test_update_with_new_password_changes_it():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            created = client.post(
                "/api/connections",
                json={"name": "SNMP-B", "target": "10.0.0.3", "port": 161, "password": "old-pw"},
            ).json()

            client.put(
                f"/api/connections/{created['id']}",
                json={"name": "SNMP-B", "target": "10.0.0.3", "port": 161, "password": "new-pw"},
            )

            conn = db.get_connection(db_path)
            try:
                raw = db.get_connection_by_id(conn, created["id"])
                assert raw["password"] == "new-pw"
            finally:
                conn.close()
        finally:
            api.app.dependency_overrides.clear()


def test_delete_connection_and_404_for_missing():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            created = client.post(
                "/api/connections", json={"name": "temp", "target": "10.0.0.4", "port": 22}
            ).json()

            resp = client.delete(f"/api/connections/{created['id']}")
            assert resp.status_code == 200

            assert client.get("/api/connections").json() == []
            assert client.delete(f"/api/connections/{created['id']}").status_code == 404
            assert client.put(
                f"/api/connections/{created['id']}", json={"name": "x", "target": "y"}
            ).status_code == 404
        finally:
            api.app.dependency_overrides.clear()


def test_connection_test_endpoint_reports_real_reachability():
    """真的開一個本機TCP server測試「綠」；用不太可能開放的低號port測試「紅」，
    不是mock掉connect結果——這條測的是S10自己標榜「不是假裝成功」這件事本身。
    """
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            open_port = server.getsockname()[1]

            _login(client, db_path)
            reachable = client.post(
                "/api/connections",
                json={"name": "reachable", "target": "127.0.0.1", "port": open_port},
            ).json()
            unreachable = client.post(
                "/api/connections",
                json={"name": "unreachable", "target": "127.0.0.1", "port": 1},
            ).json()

            ok_resp = client.post(f"/api/connections/{reachable['id']}/test")
            assert ok_resp.status_code == 200
            assert ok_resp.json()["last_status"] == "綠"

            bad_resp = client.post(f"/api/connections/{unreachable['id']}/test")
            assert bad_resp.status_code == 200
            assert bad_resp.json()["last_status"] == "紅"
        finally:
            server.close()
            api.app.dependency_overrides.clear()


def test_connection_test_without_port_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            created = client.post(
                "/api/connections", json={"name": "no-port", "target": "10.0.0.5"}
            ).json()
            resp = client.post(f"/api/connections/{created['id']}/test")
            assert resp.status_code == 400
        finally:
            api.app.dependency_overrides.clear()


if __name__ == "__main__":
    test_all_connection_endpoints_require_login()
    test_create_and_list_never_return_password_field()
    test_update_without_password_keeps_existing_password()
    test_update_with_new_password_changes_it()
    test_delete_connection_and_404_for_missing()
    test_connection_test_endpoint_reports_real_reachability()
    test_connection_test_without_port_is_rejected()
    print("S10 test_connections_api.py: PASS")
