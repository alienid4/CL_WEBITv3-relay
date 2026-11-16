"""Q91：CSRF 防護（double-submit，token 綁 session）。

CSRF_RAW_CLIENT：不套 conftest 幫 TestClient 自動補 X-CSRF-Token 的相容層——這裡就是要測「沒補會怎樣」。

機制（也寫在 docs/ui-v2/Q91設計_2026-10-29.md）：
  登入時除了 httponly 的 session cookie，再發一顆 JS 讀得到的 csrf_token cookie（= HMAC(session token)）。
  前端每個 POST/PUT/PATCH/DELETE 把它抄進 X-CSRF-Token 標頭；後端中介層比對。
  跨站攻擊者讓得了瀏覽器帶 cookie，但讀不到 cookie、也加不了自訂標頭，所以過不了。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

CSRF_RAW_CLIENT = True
_PW = "s3cure-pass!"          # 測試用假密碼


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.delenv(api.AUTH_DISABLED_ENV, raising=False)
    p = tmp_path / "t.db"
    db.init_db(p)

    def _override():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _override
    c = db.get_connection(p)
    db.create_user(c, "admin", auth.hash_password(_PW), role="admin")
    db.create_user(c, "alice", auth.hash_password(_PW), role="user")
    c.close()
    yield p
    api.app.dependency_overrides.clear()


def _login(name="admin"):
    cl = TestClient(api.app)
    r = cl.post("/api/auth/login", json={"username": name, "password": _PW})
    assert r.status_code == 200
    return cl


def _token(cl):
    return auth.csrf_token_for(cl.cookies.get(auth.SESSION_COOKIE_NAME))


def test_登入發出csrf_cookie_且JS讀得到_內容綁session(env):
    cl = TestClient(api.app)
    r = cl.post("/api/auth/login", json={"username": "admin", "password": _PW})
    sc = r.headers.get_list("set-cookie")
    sess = [c for c in sc if c.startswith(auth.SESSION_COOKIE_NAME + "=")][0]
    csrf = [c for c in sc if c.startswith(auth.CSRF_COOKIE_NAME + "=")][0]
    assert "httponly" in sess.lower() and "httponly" not in csrf.lower()
    assert "samesite=lax" in sess.lower() and "samesite=lax" in csrf.lower()
    assert cl.cookies.get(auth.CSRF_COOKIE_NAME) == _token(cl)


def test_寫入沒帶或帶錯token_被擋_帶對才過(env):
    cl = _login()
    body = {"items": []}
    r = cl.put("/api/nav/favorites", json=body)
    assert r.status_code == 403 and r.json()["code"] == "csrf"
    assert cl.put("/api/nav/favorites", json=body, headers={"X-CSRF-Token": ""}).status_code == 403
    assert cl.put("/api/nav/favorites", json=body, headers={"X-CSRF-Token": "x" * 64}).status_code == 403
    assert cl.put("/api/nav/favorites", json=body, headers={"X-CSRF-Token": _token(cl)}).status_code == 200
    # PATCH／DELETE 也一樣
    assert cl.delete("/api/deps/1").status_code == 403
    assert cl.delete("/api/deps/1", headers={"X-CSRF-Token": _token(cl)}).status_code != 403


def test_別人的token不能用(env):
    a, b = _login("admin"), _login("alice")
    r = b.put("/api/nav/favorites", json={"items": []}, headers={"X-CSRF-Token": _token(a)})
    assert r.status_code == 403 and r.json()["code"] == "csrf"


def test_讀取不需要token(env):
    cl = _login()
    assert cl.get("/api/dashboard/stats").status_code == 200


def test_登入本身豁免_沒帶cookie的請求不檢查(env):
    # 登入：帶著舊的（過期）cookie 重新登入也不能被 CSRF 擋
    cl = TestClient(api.app)
    cl.cookies.set(auth.SESSION_COOKIE_NAME, "stale-token")
    assert cl.post("/api/auth/login", json={"username": "admin", "password": _PW}).status_code == 200
    # 完全沒 cookie：沒有環境憑證可借用 → 不由 CSRF 擋（由登入檢查回 401）
    anon = TestClient(api.app)
    assert anon.put("/api/nav/favorites", json={"items": []}).status_code == 401


def test_agent端點用X_Agent_Key_沒cookie_不被CSRF擋(env):
    r = TestClient(api.app).post("/api/agent/facts", json={}, headers={"X-Agent-Key": "bogus"})
    assert r.status_code == 401 and "agent key" in r.json()["detail"]      # 是 agent 驗證擋的，不是 CSRF


def test_登出也要token_並清掉兩顆cookie(env):
    cl = _login()
    assert cl.post("/api/auth/logout").status_code == 403
    r = cl.post("/api/auth/logout", headers={"X-CSRF-Token": _token(cl)})
    assert r.status_code == 200
    dels = " ".join(r.headers.get_list("set-cookie")).lower()
    assert auth.SESSION_COOKIE_NAME in dels and auth.CSRF_COOKIE_NAME in dels


def test_me會補發csrf_cookie_升版前就登入的人不用重新登入(env):
    cl = _login()
    cl.cookies.delete(auth.CSRF_COOKIE_NAME)
    assert cl.put("/api/nav/favorites", json={"items": []}).status_code == 403      # 沒 token
    cl.get("/api/auth/me")                                                          # 前端 fetchMe 會做這件事
    assert cl.cookies.get(auth.CSRF_COOKIE_NAME) == _token(cl)
    assert cl.put("/api/nav/favorites", json={"items": []},
                  headers={"X-CSRF-Token": cl.cookies.get(auth.CSRF_COOKIE_NAME)}).status_code == 200


def test_登入停用且沒登入_寫入照舊可用(env, monkeypatch):
    monkeypatch.setenv(api.AUTH_DISABLED_ENV, "1")
    cl = TestClient(api.app)
    assert cl.put("/api/nav/favorites", json={"items": []}).status_code == 200
    assert auth.CSRF_COOKIE_NAME not in " ".join(cl.get("/api/auth/me").headers.get_list("set-cookie"))   # 沒有 session 就沒有 token 可發


def test_M3_登入停用加殘留cookie_寫入照常可用_且me會清掉壞cookie(env, monkeypatch):
    monkeypatch.setenv(api.AUTH_DISABLED_ENV, "1")
    cl = TestClient(api.app)
    cl.cookies.set(auth.SESSION_COOKIE_NAME, "stale-after-restore")
    assert cl.put("/api/nav/favorites", json={"items": []}).status_code == 200      # 以前：403 csrf，卡到清 cookie
    r = cl.get("/api/auth/me")
    assert r.status_code == 200 and r.json()["level"] == "admin"
    assert auth.SESSION_COOKIE_NAME in " ".join(r.headers.get_list("set-cookie"))   # 清除用的 Set-Cookie


def test_M3_登入啟用時殘留cookie仍照常被擋(env):
    cl = TestClient(api.app)
    cl.cookies.set(auth.SESSION_COOKIE_NAME, "stale")
    assert cl.put("/api/nav/favorites", json={"items": []}).status_code == 403


def test_豁免清單固定_改它是資安決定():
    assert auth.CSRF_EXEMPT == frozenset({("POST", "/api/auth/login")})
    assert auth.CSRF_SAFE_METHODS == frozenset({"GET", "HEAD", "OPTIONS"})


def test_token比對是常數時間且不接受空字串():
    assert auth.csrf_ok(None, None) is True            # 沒 session cookie：不適用
    assert auth.csrf_ok("tok", None) is False
    assert auth.csrf_ok("tok", "") is False
    assert auth.csrf_ok("tok", auth.csrf_token_for("tok")) is True
    assert auth.csrf_ok("tok", auth.csrf_token_for("tok2")) is False


def test_samesite可由環境變數改嚴(monkeypatch):
    monkeypatch.setenv("WEBIT3_COOKIE_SAMESITE", "strict")
    assert auth.cookie_samesite() == "strict"
    monkeypatch.setenv("WEBIT3_COOKIE_SAMESITE", "none")        # 不接受 none
    assert auth.cookie_samesite() == "lax"
    monkeypatch.setenv("WEBIT3_COOKIE_SECURE", "1")
    assert auth.cookie_secure() is True
