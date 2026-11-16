"""並行請求不能炸 SQLite 執行緒限制。

真實故障：使用者回報「資產查詢偶發『資產資料載入失敗，請稍後再試』」。先前被判定為
「暫時性/舊快取，硬重整就好」——是誤判。本機重現後從後端日誌抓到真正的例外：

    sqlite3.ProgrammingError: SQLite objects created in a thread can only be used
    in that same thread. The object was created in thread id 26480 and this is thread id 8952.

病根：FastAPI 把 sync 的 get_db 依賴和 sync 路由函式都丟進 anyio threadpool，但不保證
兩者在同一條 worker 執行緒。單一請求時通常剛好同一條所以看不出問題；資產查詢頁一次
並行打 5 支 API，執行緒被打散就爆——這就是「偶發」的來源。

修正在 db.get_connection() 加 check_same_thread=False（每個請求各自持有連線，
不會被兩條執行緒同時使用，只是可能在 worker 間交手）。

⚠️ 哪一支測試「真的」在守這個 bug（實測過，別誤會）：
把修正還原（拿掉 check_same_thread=False）重跑，只有 test_連線可以跨執行緒交手 會紅；
底下兩支走 TestClient 的並行測試照樣綠——因為 TestClient 的執行緒模型跟真實 uvicorn
不一樣，重現不了 worker 交手。所以 db 層那支才是真正的守門，HTTP 那兩支是行為佐證，
不要把它們的綠燈當作「並行沒問題」的保證。
"""
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import auth  # noqa: E402
import db  # noqa: E402
import api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_STUB_CREDENTIAL = "test-password-123"

# 資產查詢頁實際會同時打的那幾支（就是這組並行把 bug 炸出來的）
ASSETS_PAGE_ENDPOINTS = [
    "/api/assets",
    "/api/personnel",
    "/api/software",
    "/api/assets/field-groups",
    "/api/field-meta",
]


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
        db.insert_hardware(
            conn, asset_serial="HW-CONC-1", hostname="conc-host", ip="10.9.9.9",
            environment="正式",
        )
        conn.commit()
    finally:
        conn.close()
    assert client.post(
        "/api/auth/login", json={"username": "tester", "password": _STUB_CREDENTIAL}
    ).status_code == 200
    return client, db_path


def test_資產頁的並行請求全部成功():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        try:
            with ThreadPoolExecutor(max_workers=len(ASSETS_PAGE_ENDPOINTS)) as pool:
                results = list(
                    pool.map(lambda ep: (ep, client.get(ep)), ASSETS_PAGE_ENDPOINTS)
                )
            failures = [
                f"{ep} -> {resp.status_code} {resp.text[:160]}"
                for ep, resp in results
                if resp.status_code != 200
            ]
            assert not failures, "並行請求有失敗（很可能又是跨執行緒共用連線）：\n" + "\n".join(failures)
        finally:
            api.app.dependency_overrides.clear()


def test_同一支端點被連續並行打也不會壞():
    """加重版：同一支端點 20 個並行，逼 threadpool 把依賴與路由排到不同 worker。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        try:
            with ThreadPoolExecutor(max_workers=10) as pool:
                responses = list(pool.map(lambda _: client.get("/api/assets"), range(20)))
            bad = [r for r in responses if r.status_code != 200]
            assert not bad, f"{len(bad)}/20 個並行請求失敗，第一個：{bad[0].text[:200]}"
            # 資料要真的回得出來，不是回空殼
            assert all(len(r.json()) == 1 for r in responses)
        finally:
            api.app.dependency_overrides.clear()


def test_連線可以跨執行緒交手():
    """直接針對 db 層：連線在 A 執行緒建立、B 執行緒使用，不該拋 ProgrammingError。
    這正是 FastAPI threadpool 實際會做的事。"""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        db.init_db(db_path)
        conn = db.get_connection(db_path)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                rows = pool.submit(
                    lambda: conn.execute("SELECT COUNT(*) AS c FROM hardware").fetchone()
                ).result()
            assert rows["c"] == 0
        finally:
            conn.close()
