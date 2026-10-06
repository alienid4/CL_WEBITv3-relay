"""S6：登入驗證（D8 — 本機帳密，不整合AD/SSO。review:true/large risk）。

機制：
- 密碼一律用 werkzeug.security 的 salted hash 儲存（scrypt-based），不存明文、不用可逆加密。
- Session 用伺服器端可查詢/可撤銷的 opaque token（`secrets.token_urlsafe`），不是JWT——
  好處是登出＝直接刪掉那筆 session，不用額外搞黑名單，符合D8「本機帳密、MVP不搞複雜」的精神。
- Cookie 標記 httponly（前端JS讀不到，防XSS偷token）＋samesite=lax。secure旗標預設關閉，
  因為部署目標是內網IP走http（D11/D18已定案的內網簡化精神一致），之後若真的上https要記得打開。
"""
from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from werkzeug.security import check_password_hash, generate_password_hash

SESSION_TTL = timedelta(hours=8)
SESSION_COOKIE_NAME = "session_token"

# 帳號不存在時仍要跑一次雜湊驗證（比對這組假hash），讓「帳號不存在」跟「密碼錯誤」耗時
# 一致——否則帳號不存在直接return省下scrypt運算的那幾十毫秒，會變成可被側錄的時間差，
# 讓錯誤訊息文字一樣但還是能靠時間枚舉出哪些帳號真的存在。
_DUMMY_HASH = generate_password_hash("this-is-not-a-real-account-timing-safety-only")


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return check_password_hash(password_hash, password)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def issue_session(conn: sqlite3.Connection, user_id: int) -> tuple[str, str]:
    """建立新 session，回傳 (token, expires_at)。"""
    from db import create_session  # 延後匯入避免循環相依

    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + SESSION_TTL).strftime("%Y-%m-%d %H:%M:%S")
    create_session(conn, token, user_id, expires_at)
    return token, expires_at


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> sqlite3.Row | None:
    """驗證帳密，成功回傳 user row，失敗回傳 None。"""
    from db import get_user_by_username

    user = get_user_by_username(conn, username)
    hash_to_check = user["password_hash"] if user is not None else _DUMMY_HASH
    password_ok = verify_password(password, hash_to_check)
    if user is None or not password_ok:
        return None
    return user


def resolve_session(conn: sqlite3.Connection, token: str | None) -> sqlite3.Row | None:
    """依cookie token查有效session，回傳含username的row；token缺失/過期/不存在都回傳None。"""
    from db import get_valid_session

    if not token:
        return None
    return get_valid_session(conn, token, _now_iso())
