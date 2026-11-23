"""2.1 主機頁納管前的資格查詢 GET /api/onboard/target-check（2.069.3，唯讀）。

守三件事：要登入；VMware（ESXi／vCenter）與退役登記回 ok=False 並寫原因；判定本身出錯一律 ok=False（fail-closed）。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
import onboard_eligibility  # noqa: E402
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
    client = TestClient(api.app)
    conn = db.get_connection(db_path)
    try:
        db.create_user(conn, "tester", auth.hash_password(_PW), role="admin")
        db.insert_hardware(conn, asset_serial="E-3", hostname="esx80", ip="10.99.8.14", os="VMware ESXi 8.0.3", asset_status="使用中")
        db.insert_hardware(conn, asset_serial="L-1", hostname="lnx01", ip="10.99.8.21", os="Red Hat Enterprise Linux 9", asset_status="使用中")
        db.insert_hardware(conn, asset_serial="R-1", hostname="old01", ip="10.99.8.22", os="Red Hat Enterprise Linux 7", asset_status="報廢")
        conn.commit()
    finally:
        conn.close()
    assert client.post("/api/auth/login", json={"username": "tester", "password": _PW}).status_code == 200
    return client


def test_要登入():
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(tmp)
        try:
            client.cookies.clear()
            assert client.get("/api/onboard/target-check", params={"ips": "10.99.8.21"}).status_code == 401
        finally:
            api.app.dependency_overrides.clear()


def test_一般機器可以_VMware與退役不行():
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(tmp)
        try:
            r = client.get("/api/onboard/target-check", params={"ips": "10.99.8.21,10.99.8.14,10.99.8.22"})
            assert r.status_code == 200
            it = r.json()["items"]
            assert it["10.99.8.21"] == {"ok": True, "why": None, "serial": "L-1"}
            assert it["10.99.8.14"]["ok"] is False and "VMware" in it["10.99.8.14"]["why"]
            assert it["10.99.8.22"]["ok"] is False and "退役" in it["10.99.8.22"]["why"]
        finally:
            api.app.dependency_overrides.clear()


def test_判定出錯_一律不給(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(onboard_eligibility, "login_target_refusal", boom)
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(tmp)
        try:
            it = client.get("/api/onboard/target-check", params={"ips": "10.99.8.21"}).json()["items"]
            assert it["10.99.8.21"]["ok"] is False and it["10.99.8.21"]["why"] == onboard_eligibility.ELIGIBILITY_FAIL_ERROR and "RuntimeError" not in it["10.99.8.21"]["why"]
        finally:
            api.app.dependency_overrides.clear()


def test_空的與太多都回400():
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(tmp)
        try:
            assert client.get("/api/onboard/target-check").status_code == 400
            many = ",".join(f"10.99.9.{i}" for i in range(1, 22))
            assert client.get("/api/onboard/target-check", params={"ips": many}).status_code == 400
        finally:
            api.app.dependency_overrides.clear()
