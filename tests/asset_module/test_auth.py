"""S6 done_when 驗證：checks 全 PASS（review:true/large risk——密碼/session安全性要仔細審）。
覆蓋：密碼雜湊不可逆、登入成功/失敗、session cookie、登出撤銷session、過期session被拒、
帳號不存在跟密碼錯誤回一樣的錯誤訊息（防帳號枚舉）。
"""
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import auth  # noqa: E402
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


def _create_user(db_path, username, password):
    conn = db.get_connection(db_path)
    try:
        db.create_user(conn, username, auth.hash_password(password))
    finally:
        conn.close()


def test_password_hash_is_not_reversible_and_verifies_correctly():
    hashed = auth.hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert auth.verify_password("correct horse battery staple", hashed) is True
    assert auth.verify_password("wrong password", hashed) is False


def test_login_success_sets_cookie_and_me_returns_username():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _create_user(db_path, "王小明", "s3cure-pass!")

            resp = client.post(
                "/api/auth/login", json={"username": "王小明", "password": "s3cure-pass!"}
            )
            assert resp.status_code == 200
            assert resp.json() == {"username": "王小明"}
            assert api.auth.SESSION_COOKIE_NAME in resp.cookies

            me_resp = client.get("/api/auth/me")
            assert me_resp.status_code == 200
            assert me_resp.json() == {"username": "王小明"}
        finally:
            api.app.dependency_overrides.clear()


def test_login_wrong_password_and_nonexistent_user_return_same_error():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _create_user(db_path, "王小明", "s3cure-pass!")

            wrong_pw = client.post(
                "/api/auth/login", json={"username": "王小明", "password": "wrong"}
            )
            no_user = client.post(
                "/api/auth/login", json={"username": "沒有這個人", "password": "whatever"}
            )
            assert wrong_pw.status_code == 401
            assert no_user.status_code == 401
            assert wrong_pw.json()["detail"] == no_user.json()["detail"]  # 不能讓人枚舉帳號存在與否
        finally:
            api.app.dependency_overrides.clear()


def test_me_without_login_is_401():
    with tempfile.TemporaryDirectory() as tmp:
        client, _db_path = _client(tmp)
        try:
            resp = client.get("/api/auth/me")
            assert resp.status_code == 401
        finally:
            api.app.dependency_overrides.clear()


def test_logout_revokes_session_immediately():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _create_user(db_path, "王小明", "s3cure-pass!")
            client.post("/api/auth/login", json={"username": "王小明", "password": "s3cure-pass!"})
            assert client.get("/api/auth/me").status_code == 200

            logout_resp = client.post("/api/auth/logout")
            assert logout_resp.status_code == 200

            assert client.get("/api/auth/me").status_code == 401
        finally:
            api.app.dependency_overrides.clear()


def test_expired_session_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            conn = db.get_connection(db_path)
            try:
                user_id = db.create_user(conn, "王小明", auth.hash_password("s3cure-pass!"))
                expired_at = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                db.create_session(conn, "expired-token-abc123", user_id, expired_at)
            finally:
                conn.close()

            client.cookies.set(api.auth.SESSION_COOKIE_NAME, "expired-token-abc123")
            resp = client.get("/api/auth/me")
            assert resp.status_code == 401
        finally:
            api.app.dependency_overrides.clear()


if __name__ == "__main__":
    test_password_hash_is_not_reversible_and_verifies_correctly()
    test_login_success_sets_cookie_and_me_returns_username()
    test_login_wrong_password_and_nonexistent_user_return_same_error()
    test_me_without_login_is_401()
    test_logout_revokes_session_immediately()
    test_expired_session_is_rejected()
    print("S6 test_auth.py: PASS")
