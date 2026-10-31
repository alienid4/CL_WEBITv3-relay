"""Q42（2026-10-17）/adopt 打開很慢：回應壓縮＋manage-state 直接回 JSONResponse。

釘住兩件事：
1. 大回應有 gzip（1.16MB → 約 86KB；經 Tailscale／公司網路時傳輸本身就是秒級）。
2. 直接回 JSONResponse 不改變內容：跟 manage_state.summarize 逐欄相同。
IP 一律 10.99.x（範例）。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
import manage_state  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _client(tmp):
    db_path = Path(tmp) / "t.db"
    db.init_db(db_path)

    def _override():
        conn = db.get_connection(db_path)
        try:
            yield conn
        finally:
            conn.close()

    api.app.dependency_overrides[api.get_db] = _override
    client = TestClient(api.app)
    conn = db.get_connection(db_path)
    try:
        db.create_user(conn, "tester", auth.hash_password("s3cure-pass!"))
        for i in range(300):
            db.insert_hardware(conn, asset_serial=f"HW-{i}", hostname=f"host{i}",
                               ip=f"10.99.{i // 200}.{i % 200 + 1}", asset_status="使用中")
        conn.commit()
    finally:
        conn.close()
    assert client.post("/api/auth/login",
                       json={"username": "tester", "password": "s3cure-pass!"}).status_code == 200
    return client, db_path


def test_manage_state_大回應有壓縮_內容跟summarize一樣():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            r = client.get("/api/manage-state", headers={"Accept-Encoding": "gzip"})
            assert r.status_code == 200
            assert r.headers.get("content-encoding") == "gzip"
            body = r.json()
            conn = db.get_connection(db_path)
            try:
                want = manage_state.summarize(conn)
            finally:
                conn.close()
            assert body == want
            assert len(body["items"]) == 300

            # 沒說要 gzip 的用戶端（curl 預設、agent）照舊拿原文
            r2 = client.get("/api/manage-state", headers={"Accept-Encoding": "identity"})
            assert r2.headers.get("content-encoding") is None and r2.json() == want

            s = client.get("/api/manage-state?summary=true").json()
            assert "items" not in s and s["counts"] == want["counts"]
        finally:
            api.app.dependency_overrides.clear()


def test_小回應不壓():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        try:
            r = client.get("/api/collect/dispatch/gap", headers={"Accept-Encoding": "gzip"})
            assert r.status_code == 200 and r.headers.get("content-encoding") is None
        finally:
            api.app.dependency_overrides.clear()


def test_ips_of_快取不被呼叫端汙染():
    a = manage_state.ips_of("10.99.1.1,10.99.1.2")
    a.append("x")
    assert manage_state.ips_of("10.99.1.1,10.99.1.2") == ["10.99.1.1", "10.99.1.2"]
    assert manage_state.ips_of(None) == [] and manage_state.ips_of("0.0.0.0") == []
