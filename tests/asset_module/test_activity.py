"""在線人數與操作紀錄。

2026-08-26 使用者要求：「在左上角顯示在線人數，我要知道誰在用，LOG 紀錄也要」。

這裡守的是**讓數字可信**的那幾條，不是「功能有沒有回 200」：
1. 在線是「最近 N 分鐘有活動」，不是「session 沒過期」——分不清會長期高估
2. 「0 人在線」要能分辨「真的沒人」與「根本沒在記」
3. GET 不進紀錄、非 GET 要進（全記會把真正要查的東西淹掉）
4. 失敗的動作也要留（誰試了什麼但沒成功，正是稽核要看的）
5. 登入失敗要記下他打的帳號（對外不透露 ≠ 對內不記錄）
"""
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import activity  # noqa: E402
import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_PW = "test-password-123"


def _client(tmp, username="tester"):
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
        db.create_user(conn, username, auth.hash_password(_PW))
        db.insert_hardware(conn, asset_serial="HW-1", hostname="a", ip="10.99.0.1",
                           asset_status="在用")
        conn.commit()
    finally:
        conn.close()
    return client, db_path


def _login(client, username="tester"):
    r = client.post("/api/auth/login", json={"username": username, "password": _PW})
    assert r.status_code == 200, r.text
    return r


def _rows(db_path, **kw):
    conn = db.get_connection(db_path)
    try:
        return activity.list_log(conn, **kw)["rows"]
    finally:
        conn.close()


# ===== 在線 =====

def test_剛登入還沒打過API就不算在線():
    """在線的定義是「有活動」。只發了 login 這一支、還沒真的用系統的人，
    在畫面上顯示成「正在使用」是誤導——這條同時擋住「拿 sessions 表當在線人數」
    那種寫法（那樣寫這裡就會是 1）。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client)
            conn = db.get_connection(db_path)
            try:
                assert activity.online_users(conn)["count"] == 0
            finally:
                conn.close()
        finally:
            api.app.dependency_overrides.clear()


def test_打過API之後算在線且看得到是誰():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client)
            client.get("/api/auth/me")            # 這一下會寫心跳
            o = client.get("/api/online").json()
            assert o["count"] == 1
            assert o["users"][0]["username"] == "tester"
            assert o["users"][0]["last_seen_at"]
            assert o["never_recorded"] is False
        finally:
            api.app.dependency_overrides.clear()


def test_超過視窗就不算在線_session還沒過期也一樣():
    """這是整個功能最重要的一條：session TTL 有好幾小時，開著分頁去吃飯的人
    session 還在。把「session 沒過期」當成在線會長期高估，而「現在有幾個人在用」
    是會拿來決定要不要重啟服務的資訊，虛胖的數字會害人白等。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client)
            client.get("/api/auth/me")
            conn = db.get_connection(db_path)
            try:
                # 把心跳往回撥到視窗之外，session 本身完全沒動（仍未過期）
                stale = (datetime.now()
                         - timedelta(minutes=activity.ONLINE_WINDOW_MINUTES + 1)
                         ).strftime("%Y-%m-%d %H:%M:%S")
                conn.execute("UPDATE sessions SET last_seen_at = ?", (stale,))
                conn.commit()
                # session 的 expires_at 存的是 UTC（auth._now_iso），
                # 拿本地時間比會在 UTC+8 這種時區得到錯誤結論
                assert conn.execute(
                    "SELECT COUNT(*) FROM sessions WHERE expires_at > ?",
                    (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),)
                ).fetchone()[0] == 1, "前提：session 仍未過期"
                assert activity.online_users(conn)["count"] == 0
                assert activity.online_users(conn)["never_recorded"] is False
            finally:
                conn.close()
        finally:
            api.app.dependency_overrides.clear()


def test_從來沒記過要跟沒人在線分得出來():
    """「0 人在線」有兩種意思：真的沒人，或這功能根本沒在記（剛升級、migration
    沒跑）。畫面上分不出來的話，人會拿一個假的 0 去做決定。"""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "t.db"
        db.init_db(db_path)
        conn = db.get_connection(db_path)
        try:
            o = activity.online_users(conn)
            assert o["count"] == 0
            assert o["never_recorded"] is True
            assert o["last_activity_at"] is None
            assert o["window_minutes"] == activity.ONLINE_WINDOW_MINUTES
        finally:
            conn.close()


def test_心跳有節流_同一秒連打不會每次都寫():
    """一個頁面開起來會打十幾支 API。每支都寫 DB 是十幾次沒必要的寫入，
    而 SQLite 單寫者、會跟匯入那種長交易搶鎖。"""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "t.db"
        db.init_db(db_path)
        conn = db.get_connection(db_path)
        try:
            db.create_user(conn, "tester", auth.hash_password(_PW))
            user = conn.execute("SELECT * FROM users WHERE username='tester'").fetchone()
            token, _ = auth.issue_session(conn, user["id"])
            conn.commit()

            class _Req:
                headers: dict = {}
                client = None

            s = auth.resolve_session(conn, token)
            activity.touch_session(conn, s, _Req())
            first = conn.execute("SELECT last_seen_at FROM sessions").fetchone()[0]
            assert first

            # 再打一次：節流內，last_seen_at 不該被改動
            s2 = auth.resolve_session(conn, token)
            conn.execute("UPDATE sessions SET last_ip = 'sentinel'")
            conn.commit()
            activity.touch_session(conn, s2, _Req())
            row = conn.execute("SELECT last_seen_at, last_ip FROM sessions").fetchone()
            assert row[0] == first
            assert row[1] == "sentinel", "節流期間不該覆寫"
        finally:
            conn.close()


# ===== 操作紀錄 =====

def test_GET不進紀錄_非GET才進():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client)
            client.get("/api/assets")
            paths = [r["path"] for r in _rows(db_path)]
            assert "/api/assets" not in paths, "GET 不該進紀錄（會把真正要查的淹掉）"

            client.put("/api/classify", json={"asset_serials": ["HW-1"], "category": None})
            rows = _rows(db_path)
            hit = [r for r in rows if r["path"] == "/api/classify"]
            assert len(hit) == 1
            assert hit[0]["method"] == "PUT"
            assert hit[0]["username"] == "tester"
            assert hit[0]["action"] == "change"
            assert hit[0]["duration_ms"] is not None
        finally:
            api.app.dependency_overrides.clear()


def test_失敗的動作也要留下來():
    """誰試了什麼但沒成功，正是稽核最想看的一種。只記成功的話，
    「有人一直在試改別人的東西」這件事會完全看不見。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client)
            r = client.put("/api/classify",
                           json={"asset_serials": ["HW-1"], "category": "亂打的分類"})
            assert r.status_code == 400
            hit = [x for x in _rows(db_path) if x["path"] == "/api/classify"]
            assert len(hit) == 1 and hit[0]["status"] == 400
        finally:
            api.app.dependency_overrides.clear()


def test_未登入打API也會留紀錄_username是空的():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            r = client.put("/api/classify", json={"asset_serials": ["HW-1"], "category": None})
            assert r.status_code == 401
            hit = [x for x in _rows(db_path) if x["path"] == "/api/classify"]
            assert len(hit) == 1
            assert hit[0]["username"] is None
            assert hit[0]["status"] == 401
        finally:
            api.app.dependency_overrides.clear()


def test_登入成功與失敗都記_失敗要記下他打的帳號():
    """對外一律回「帳號或密碼錯誤」不透露帳號存不存在，但**對內要記**——
    連續對同一個帳號試錯是要看得出來的訊號。不透露 ≠ 不記錄。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            bad = client.post("/api/auth/login",
                              json={"username": "somebody", "password": "wrong"})
            assert bad.status_code == 401
            _login(client)
            client.post("/api/auth/logout")

            rows = _rows(db_path)
            by_action = {r["action"]: r for r in rows}
            assert by_action["login_failed"]["username"] == "somebody"
            assert by_action["login_failed"]["status"] == 401
            assert by_action["login"]["username"] == "tester"
            assert by_action["logout"]["username"] == "tester"
            # 登入相關只由端點記一次，middleware 不該重複記
            assert sum(1 for r in rows if r["path"] == "/api/auth/login") == 2
        finally:
            api.app.dependency_overrides.clear()


def test_紀錄不含request_body():
    """body 裡可能有密碼、真實主機名、人員姓名電話。複製一份進另一張表只是
    多開一個外洩面——稽核要的是「誰做了什麼動作」，不是「他打了什麼字」。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            client.post("/api/auth/login",
                        json={"username": "tester", "password": "SuperSecret!123"})
            conn = db.get_connection(db_path)
            try:
                dump = " ".join(
                    str(v) for r in conn.execute("SELECT * FROM activity_log")
                    for v in tuple(r)
                )
            finally:
                conn.close()
            assert "SuperSecret" not in dump
        finally:
            api.app.dependency_overrides.clear()


def test_清理只刪超過保留期的():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "t.db"
        db.init_db(db_path)
        conn = db.get_connection(db_path)
        try:
            old = (datetime.now() - timedelta(days=activity.RETAIN_DAYS + 5)
                   ).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("INSERT INTO activity_log (at, method, path) VALUES (?,?,?)",
                         (old, "PUT", "/api/old"))
            conn.commit()
            activity.log(conn, username="tester", ip=None, method="PUT", path="/api/new")

            assert activity.purge_old(conn) == 1
            left = [r["path"] for r in activity.list_log(conn)["rows"]]
            assert left == ["/api/new"]
        finally:
            conn.close()


def test_摘要看得出誰在用():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client)
            for _ in range(3):
                client.put("/api/classify",
                           json={"asset_serials": ["HW-1"], "category": None})
            s = client.get("/api/activity/summary").json()
            by = {r["username"]: r["n"] for r in s["by_user"]}
            assert by["tester"] >= 3
            assert s["login_failed"] == 0
            assert client.get("/api/activity/summary", params={"days": 0}).status_code == 400
        finally:
            api.app.dependency_overrides.clear()


def test_未登入打不到這幾支端點():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        try:
            for path in ("/api/online", "/api/activity", "/api/activity/summary"):
                assert client.get(path).status_code == 401, path
        finally:
            api.app.dependency_overrides.clear()
