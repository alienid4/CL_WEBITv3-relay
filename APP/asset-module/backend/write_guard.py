"""機櫃類寫入的守門（2026-10-25）：誰做的＋（可選）登入停用時要輸入管理密碼。

背景：221 與公司端目前都「停用登入」（使用者決定），所以任何人都能拖拉搬移、合併、位置更正。
  · 稽核一定要留下「誰」：有登入就是帳號；沒登入寫「未登入」，再附來源 IP（activity.client_ip，只當參考，
    X-Forwarded-For 可被偽造，不拿來授權）。
  · 設定「登入停用時，寫入動作要輸入管理密碼」（app_settings write_password_when_auth_disabled，預設關）：
    打開且登入停用時，這幾類寫入（拖拉搬移、調高度、合併／拆開、已不在櫃上、位置更正、4-5 確認／判讀錯了）
    都要帶 admin 帳號的密碼（X-Admin-Password 標頭），由伺服器用既有的密碼雜湊驗證；明文不落地。
    密碼錯太多次（同一來源 10 分鐘 5 次）就先擋 10 分鐘。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

SETTING = "write_password_when_auth_disabled"
HEADER = "x-admin-password"
MAX_FAILS = 5
MAX_FAILS_ALL = 50          # 不分來源的總上限（換來源也繞不過）
WINDOW_MIN = 10

_FAIL_SQL = """CREATE TABLE IF NOT EXISTS write_guard_fail (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ip TEXT, at TEXT NOT NULL)"""


class NeedPassword(Exception):
    """要輸入管理密碼（API 回 428，畫面跳出輸入框）。"""


class TooManyFails(Exception):
    """密碼錯太多次，暫時擋住（API 回 429）。"""


def enabled(conn: sqlite3.Connection) -> bool:
    r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (SETTING,)).fetchone()
    return bool(r and str(r[0]).strip() in ("1", "true", "on"))


def set_enabled(conn: sqlite3.Connection, on: bool) -> None:
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (SETTING, "1" if on else "0"))
    conn.commit()


def actor(session, request) -> str:
    """稽核用的「誰」：帳號（沒有就寫未登入）＋來源 IP。"""
    import activity

    name = None
    try:
        name = session["username"] if session is not None else None
    except (KeyError, IndexError, TypeError):
        name = None
    if not name or "未登入" in str(name):
        name = "未登入"
    ip = None
    try:
        ip = activity.client_ip(request)
    except Exception:  # noqa: BLE001 - 取不到來源 IP 只是少一個參考欄位，不影響寫入
        ip = None
    return f"{name}@{ip}" if ip else name


def _socket_ip(request) -> str:
    """限流用的來源＝真正的連線位址（request.client.host），不看 X-Forwarded-For（那個呼叫端可以亂填）。"""
    client = getattr(request, "client", None) if request is not None else None
    return (getattr(client, "host", None) or "") if client else ""


def _recent_fails(conn, ip: str) -> tuple[int, int]:
    conn.execute(_FAIL_SQL)
    since = (datetime.now() - timedelta(minutes=WINDOW_MIN)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("DELETE FROM write_guard_fail WHERE at < ?", (since,))      # 過期的順手清掉
    mine = conn.execute("SELECT COUNT(*) FROM write_guard_fail WHERE COALESCE(ip,'') = ? AND at >= ?", (ip, since)).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM write_guard_fail WHERE at >= ?", (since,)).fetchone()[0]
    return mine, total


def admin_exists(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM users WHERE username = 'admin' AND COALESCE(password_hash,'') != ''").fetchone() is not None


def check(conn: sqlite3.Connection, request, password: str | None = None) -> None:
    """設定打開＋登入停用時：驗 admin 密碼。其他情況直接放行（有登入時權限照既有規則走）。"""
    import auth
    from _app_shared import auth_disabled

    if not (enabled(conn) and auth_disabled(conn)) and password is None:
        return
    ip = _socket_ip(request)
    mine, total = _recent_fails(conn, ip)
    if mine >= MAX_FAILS or total >= MAX_FAILS_ALL:
        raise TooManyFails(f"管理密碼錯太多次，請 {WINDOW_MIN} 分鐘後再試")
    pw = password if password is not None else (request.headers.get(HEADER) if request is not None else None)
    if not pw:
        raise NeedPassword("登入停用中，這個動作要輸入管理密碼")
    row = conn.execute("SELECT password_hash FROM users WHERE username = 'admin'").fetchone()
    if not row or not auth.verify_password(pw, row["password_hash"]):
        conn.execute("INSERT INTO write_guard_fail (ip, at) VALUES (?, ?)", (ip, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        raise NeedPassword("管理密碼不對")
