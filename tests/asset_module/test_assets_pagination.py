"""資產查詢分頁。

背景：`/api/assets` 原本一律回傳全部，前端也是 v-for 全渲染。資產上千筆時，
回應體積跟 DOM 節點數都會線性成長，畫面會卡。

相容性是這次的重點：**不帶 limit 時行為必須跟以前完全一樣**（回純陣列、回全部），
否則所有既有呼叫端都得跟著改。總筆數放 X-Total-Count 標頭而不是把回應包成
{items, total}——標頭是加法，改結構是破壞性變更。
"""
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


def _client(tmp, rows=25):
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
        for i in range(rows):
            db.insert_hardware(
                conn, asset_serial=f"HW-{i:04d}", hostname=f"host-{i:03d}",
                ip=f"10.0.0.{i}", environment="正式",
            )
        conn.commit()
    finally:
        conn.close()
    assert client.post(
        "/api/auth/login", json={"username": "tester", "password": _STUB_CREDENTIAL}
    ).status_code == 200
    return client


def test_不帶limit時行為與以前完全相同():
    """相容性守門：既有前端沒帶分頁參數，必須照樣拿到全部、且是純陣列。"""
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(tmp, rows=25)
        try:
            r = client.get("/api/assets")
            body = r.json()
            assert r.status_code == 200
            assert isinstance(body, list), "回應必須維持純陣列，不能包成物件"
            assert len(body) == 25
            assert r.headers["X-Total-Count"] == "25"
        finally:
            api.app.dependency_overrides.clear()


def test_分頁切片正確且不重不漏():
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(tmp, rows=25)
        try:
            seen = []
            for offset in (0, 10, 20):
                r = client.get("/api/assets", params={"limit": 10, "offset": offset})
                page = r.json()
                assert r.headers["X-Total-Count"] == "25", "總數要是篩選後的總數，不是本頁筆數"
                seen.extend(x["asset_serial"] for x in page)
            assert len(seen) == 25, f"三頁加起來應該剛好 25 筆，實際 {len(seen)}"
            assert len(set(seen)) == 25, "頁與頁之間有重複"
            assert seen == sorted(seen), "排序在分頁後被打亂了"
        finally:
            api.app.dependency_overrides.clear()


def test_總筆數是篩選後的數量():
    """X-Total-Count 要反映「這個條件下共有幾筆」，前端才算得出總頁數。"""
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(tmp, rows=25)
        try:
            r = client.get("/api/assets", params={"q": "host-01", "limit": 2})
            total = int(r.headers["X-Total-Count"])
            all_matched = client.get("/api/assets", params={"q": "host-01"}).json()
            assert total == len(all_matched)
            assert len(r.json()) <= 2
        finally:
            api.app.dependency_overrides.clear()


def test_超出範圍的offset回空陣列而不是報錯():
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(tmp, rows=5)
        try:
            r = client.get("/api/assets", params={"limit": 10, "offset": 999})
            assert r.status_code == 200
            assert r.json() == []
            assert r.headers["X-Total-Count"] == "5"
        finally:
            api.app.dependency_overrides.clear()


def test_亂填的分頁參數被擋下():
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(tmp, rows=5)
        try:
            assert client.get("/api/assets", params={"limit": 0}).status_code == 400
            assert client.get("/api/assets", params={"limit": 99999}).status_code == 400
            assert client.get("/api/assets", params={"limit": 5, "offset": -1}).status_code == 400
        finally:
            api.app.dependency_overrides.clear()


def test_分頁與掃描狀態篩選可以並用():
    """下鑽連結會同時帶 scan_status 與環境；加上分頁後總數仍要是篩選後的。"""
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(tmp, rows=6)
        try:
            conn = db.get_connection(Path(tmp) / "test.db")
            try:
                for ip in ("10.0.0.0", "10.0.0.1", "10.0.0.2"):
                    conn.execute(
                        "INSERT INTO scan_history (scan_time, ip, hostname, segment, scan_ok) "
                        "VALUES ('2026-07-18 10:00:00', ?, '', '10.0.0.0/24', 1)",
                        (ip,),
                    )
                conn.commit()
            finally:
                conn.close()

            full = client.get("/api/assets", params={"scan_status": "overlap"})
            assert len(full.json()) == 3

            paged = client.get("/api/assets", params={"scan_status": "overlap", "limit": 2})
            assert len(paged.json()) == 2
            assert paged.headers["X-Total-Count"] == "3", "總數要是篩選後的 3，不是全部 6"
        finally:
            api.app.dependency_overrides.clear()
