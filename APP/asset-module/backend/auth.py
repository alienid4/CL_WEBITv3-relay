"""S6：登入驗證（D8 — 本機帳密，不整合AD/SSO。review:true/large risk）。

機制：
- 密碼一律用 werkzeug.security 的 salted hash 儲存（scrypt-based），不存明文、不用可逆加密。
- Session 用伺服器端可查詢/可撤銷的 opaque token（`secrets.token_urlsafe`），不是JWT——
  好處是登出＝直接刪掉那筆 session，不用額外搞黑名單，符合D8「本機帳密、MVP不搞複雜」的精神。
- Cookie 標記 httponly（前端JS讀不到，防XSS偷token）＋samesite=lax。secure旗標預設關閉，
  因為部署目標是內網IP走http（D11/D18已定案的內網簡化精神一致），之後若真的上https要記得打開。
"""
from __future__ import annotations

import hashlib
import hmac
import os
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


# ===== Q91：權限等級 =====
# 三級：viewer 只能看 < user 一般 < admin 管理員。等級存在 users.role，session 查詢時一併帶出。
LEVELS = ("viewer", "user", "admin")
LEVEL_LABEL = {"viewer": "只能看", "user": "一般", "admin": "管理員"}


def level_rank(level: str) -> int:
    return LEVELS.index(level)


def session_level(session) -> str:
    """這個 session 的權限等級。

    · sqlite3.Row（真實登入）：讀 users.role；欄位空／不認得 → 帳號名 admin 才是 admin，其餘當「一般」。
    · 其他（登入停用時給的假 session 自帶 role=admin；測試用的假 dict 沒帶 role → 當「一般」，**fail-closed**）。
    · None（不該發生：require_auth 沒過就不會走到這）→ 最低級。
    """
    if session is None:
        return "viewer"
    try:
        role = session["role"]
    except (KeyError, IndexError, TypeError):
        role = None
    if role in LEVELS:
        return role
    if role is not None:
        return "viewer"          # 不認得的等級字串：給最低級，不猜
    try:
        name = session["username"]
    except (KeyError, IndexError, TypeError):
        name = None
    return "admin" if name == "admin" else "user"


def is_admin(session) -> bool:
    return session_level(session) == "admin"


# ===== Q91：CSRF（double-submit，綁 session）=====
# 瀏覽器帶的 session cookie 是 httponly，攻擊者網頁讀不到；CSRF token = HMAC(session token)，
# 另外放進「JS 讀得到」的 csrf_token cookie，前端每個會改東西的請求把它抄進 X-CSRF-Token 標頭。
# 跨站攻擊者能「讓瀏覽器帶 cookie」，但讀不到 cookie 值、也不能在跨站請求加自訂標頭（會先被 CORS 預檢擋）。
CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "x-csrf-token"
CSRF_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
#: 不檢查 CSRF 的 (方法, 路徑)。登入：還沒有 session，沒有「被借用的身分」可言。
#: 另外「請求根本沒帶 session cookie」也不檢查（沒有環境憑證可被借用）——例如 push agent 用 X-Agent-Key。
CSRF_EXEMPT = frozenset({("POST", "/api/auth/login")})


def csrf_token_for(session_token: str) -> str:
    return hmac.new(b"webit3-csrf-v1", session_token.encode("utf-8"), hashlib.sha256).hexdigest()


def csrf_ok(session_cookie: str | None, header_value: str | None) -> bool:
    if not session_cookie:
        return True
    return bool(header_value) and hmac.compare_digest(header_value, csrf_token_for(session_cookie))


def cookie_samesite() -> str:
    """session／csrf cookie 的 SameSite。預設 lax（嚴格 strict 會讓「從信件／外部連結點進來」第一次看起來沒登入）。
    要更嚴格：環境變數 WEBIT3_COOKIE_SAMESITE=strict。"""
    v = os.environ.get("WEBIT3_COOKIE_SAMESITE", "lax").strip().lower()
    return v if v in ("lax", "strict") else "lax"


def cookie_secure() -> bool:
    """上 https 後設 WEBIT3_COOKIE_SECURE=1。"""
    return os.environ.get("WEBIT3_COOKIE_SECURE", "").strip() in ("1", "true", "yes")


def set_session_cookies(response, session_token: str) -> None:
    """登入／/me 補發用：session（httponly）＋csrf（JS 可讀）。"""
    max_age = int(SESSION_TTL.total_seconds())
    response.set_cookie(key=SESSION_COOKIE_NAME, value=session_token, httponly=True,
                        samesite=cookie_samesite(), secure=cookie_secure(), max_age=max_age)
    set_csrf_cookie(response, session_token)


def set_csrf_cookie(response, session_token: str) -> None:
    response.set_cookie(key=CSRF_COOKIE_NAME, value=csrf_token_for(session_token), httponly=False,
                        samesite=cookie_samesite(), secure=cookie_secure(),
                        max_age=int(SESSION_TTL.total_seconds()))
