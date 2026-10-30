"""V2「新增帳號」：POST /api/access/users（管理員限定，只能建 user／viewer，回應與紀錄不含密碼）。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import access_policy as ap  # noqa: E402
import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ROLE_AWARE_TESTS = True
_PW = "s3cure-pass!"                 # 測試用假密碼
NEW_PW = "Zx9-correct-horse"         # 測試用假密碼（>=12）


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.delenv(api.AUTH_DISABLED_ENV, raising=False)
    p = tmp_path / "t.db"
    db.init_db(p)

    def _o():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _o
    c = db.get_connection(p)
    for n, r in (("admin", "admin"), ("alice", "user"), ("vera", "viewer")):
        db.create_user(c, n, auth.hash_password(_PW), role=r)
    c.close()

    class E:
        path = p

        def client(self, name):
            cl = TestClient(api.app)
            assert cl.post("/api/auth/login", json={"username": name, "password": _PW}).status_code == 200
            return cl

        def conn(self):
            return db.get_connection(p)

    yield E()
    api.app.dependency_overrides.clear()


def _body(**kw):
    d = {"username": "newbie", "display_name": "新同仁", "level": "user", "password": NEW_PW}
    d.update(kw)
    return d


def test_已登記為管理員寫入():
    assert ("POST", "/api/access/users") in ap.ADMIN_WRITE


@pytest.mark.parametrize("level", ["user", "viewer"])
def test_admin可建user與viewer_且新帳號能登入(env, level):
    r = env.client("admin").post("/api/access/users", json=_body(level=level))
    assert r.status_code == 200, r.text
    assert r.json()["level"] == level and r.json()["username"] == "newbie"
    c = env.conn()
    assert c.execute("SELECT role FROM users WHERE username='newbie'").fetchone()[0] == level
    c.close()
    cl = TestClient(api.app)
    assert cl.post("/api/auth/login", json={"username": "newbie", "password": NEW_PW}).status_code == 200


def test_不能由此建出admin(env):
    r = env.client("admin").post("/api/access/users", json=_body(level="admin"))
    assert r.status_code == 400
    c = env.conn()
    assert c.execute("SELECT COUNT(*) FROM users WHERE username='newbie'").fetchone()[0] == 0
    c.close()


def test_非admin_403_未登入_401(env):
    assert env.client("alice").post("/api/access/users", json=_body()).status_code == 403
    assert env.client("vera").post("/api/access/users", json=_body()).status_code == 403
    assert TestClient(api.app).post("/api/access/users", json=_body()).status_code == 401


@pytest.mark.parametrize("name", ["admin", "ADMIN", "alice", "Alice", "a", "ab", "bad name", "x;drop", "中文名", "-lead", "a" * 40])
def test_重複或非法帳號_400(env, name):
    assert env.client("admin").post("/api/access/users", json=_body(username=name)).status_code == 400


@pytest.mark.parametrize("pw", ["short", "12345678901", "aaaaaaaaaaaa", "xxnewbiexxxxxx", "p" * 200, ""])
def test_弱密碼_400(env, pw):
    assert env.client("admin").post("/api/access/users", json=_body(password=pw)).status_code == 400


def test_回應_log_稽核不含密碼_且稽核有寫(env):
    cl = env.client("admin")
    r = cl.post("/api/access/users", json=_body())
    assert r.status_code == 200 and NEW_PW not in r.text and "password" not in r.text.lower()
    c = env.conn()
    aud = c.execute("SELECT actor, target, new_level FROM access_level_audit WHERE target='newbie'").fetchall()
    assert len(aud) == 1 and aud[0]["actor"] == "admin" and aud[0]["new_level"] == "user"
    dump = "\n".join(str(tuple(x)) for t in ("access_level_audit", "activity_log", "export_log")
                     for x in c.execute(f"SELECT * FROM {t}").fetchall())
    assert NEW_PW not in dump
    acts = c.execute("SELECT username, action, path FROM activity_log WHERE action='create_user'").fetchall()
    assert acts and acts[0]["username"] == "admin" and "newbie" in acts[0]["path"]
    h = c.execute("SELECT password_hash FROM users WHERE username='newbie'").fetchone()[0]
    assert h != NEW_PW and auth.verify_password(NEW_PW, h)
    c.close()


def test_缺CSRF_token_403(env):
    cl = env.client("admin")
    r = cl.post("/api/access/users", json=_body(), headers={"X-CSRF-Token": ""})
    assert r.status_code == 403
    c = env.conn()
    assert c.execute("SELECT COUNT(*) FROM users WHERE username='newbie'").fetchone()[0] == 0
    c.close()


# ---- 獨立審查者＋Codex 審查後補 ----
def test_稽核寫不進去_帳號不會被建立(env):
    c = env.conn()
    c.execute("CREATE TRIGGER t_block_audit BEFORE INSERT ON access_level_audit BEGIN SELECT RAISE(ABORT, 'audit down'); END")
    c.commit()
    n0 = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    c.close()
    cl = env.client("admin")
    try:
        r = cl.post("/api/access/users", json=_body(username="ghost01"))
        assert r.status_code >= 400
    except Exception:  # noqa: BLE001 - TestClient 可能直接把伺服器端例外丟出來；重點是帳號沒被建
        pass
    c = env.conn()
    assert c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == n0
    assert c.execute("SELECT 1 FROM users WHERE username='ghost01'").fetchone() is None
    c.close()


def test_太好猜的密碼被拒(env):
    cl = env.client("admin")
    for pw in ["abcdabcdabcd", "ababababababab", "abcdefghijkl", "123456789012", "Password12345"]:
        r = cl.post("/api/access/users", json=_body(username="weak01", password=pw))
        assert r.status_code == 400, (pw, r.status_code, r.text)
