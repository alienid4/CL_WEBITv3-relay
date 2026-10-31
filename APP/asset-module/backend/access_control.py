"""Q91：權限等級管理＋下載／匯出紀錄（append-only）。

· 使用者清單與等級異動（異動一律寫 access_level_audit）。
· 匯出紀錄 export_log：由 api.py 的中介層在「回應是檔案下載」時寫入；前端表格匯出（POST /api/export/table-xlsx）
  由端點自己用 request.state 補上「匯出名稱／列數」。紀錄只增不改不刪（schema 有 trigger 擋）。
· 畫面表格匯出的「大量／含帳號欄位 → admin」判斷。
"""
from __future__ import annotations

import re
import sqlite3
from urllib.parse import unquote

import auth

#: 畫面表格匯出：超過這麼多列算「大量匯出」，要管理員。
BULK_ROWS = 2000
#: 表頭／標題出現這些字 → 視為含帳號資料，要管理員。
_ACCOUNT_WORDS = re.compile(r"帳號|account|密碼|passw|sudo|員編|憑證|credential|金鑰|\buid\b", re.I)
_MAX_SUMMARY = 500


# ------------------------------------------------------------------ 使用者與等級
def list_users(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, username, role, created_at FROM users ORDER BY username").fetchall()
    out = []
    for r in rows:
        role = r["role"] if r["role"] in auth.LEVELS else ("admin" if r["username"] == "admin" else "user")
        out.append({"id": r["id"], "username": r["username"], "level": role,
                    "level_label": auth.LEVEL_LABEL[role], "created_at": r["created_at"],
                    "locked": r["username"] == "admin"})
    return out


class LevelError(ValueError):
    pass


def set_level(conn: sqlite3.Connection, target: str, new_level: str, actor: str | None, ip: str | None) -> dict:
    """改某帳號的等級並寫稽核。不能改 admin 這個帳號；不能把最後一個管理員降級。"""
    if new_level not in auth.LEVELS:
        raise LevelError(f"等級只能是 {'／'.join(auth.LEVELS)}")
    row = conn.execute("SELECT id, username, role FROM users WHERE username = ?", (target,)).fetchone()
    if row is None:
        raise LevelError(f"沒有這個帳號：{target}")
    old = row["role"] if row["role"] in auth.LEVELS else ("admin" if target == "admin" else "user")
    if target == "admin" and new_level != "admin":
        raise LevelError("admin 這個帳號固定是管理員，不能降級")
    if old == "admin" and new_level != "admin":
        n_admin = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]
        if n_admin <= 1:
            raise LevelError("至少要留一個管理員，不能把最後一個管理員降級")
    if old == new_level:
        return {"username": target, "level": new_level, "changed": False}
    conn.execute("UPDATE users SET role = ? WHERE username = ?", (new_level, target))
    conn.execute(
        "INSERT INTO access_level_audit (actor, target, old_level, new_level, ip) VALUES (?, ?, ?, ?, ?)",
        (actor, target, old, new_level, ip))
    conn.commit()
    return {"username": target, "level": new_level, "changed": True}


_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$")
MIN_PASSWORD_LEN = 12
MAX_PASSWORD_LEN = 128
#: 由「新增帳號」建出來的等級。admin 不能由此建出（升管理員只走改等級端點）。
CREATABLE_LEVELS = ("user", "viewer")


_COMMON_PASSWORDS = {"password1234", "password12345", "123456789012", "qwertyuiop12", "qwerty123456",
                     "letmein12345", "admin1234567", "welcome12345", "changeme1234", "iloveyou1234"}


def _too_predictable(pw: str) -> bool:
    """太好猜：短區塊重複（abcdabcdabcd）、連號（abcdefghijkl／123456789012）、常見密碼。"""
    low = pw.lower()
    if low in _COMMON_PASSWORDS:
        return True
    for n in range(1, 5):                                   # 長度 1～4 的區塊重複到整個密碼
        if len(low) % n == 0 and low == low[:n] * (len(low) // n):
            return True
    steps = {ord(b) - ord(a) for a, b in zip(low, low[1:])}
    return steps <= {1} or steps <= {-1}                    # 全部遞增或全部遞減 1


def create_account(conn: sqlite3.Connection, username: str, display_name: str | None, level: str,
                   password: str, actor: str | None, ip: str | None) -> dict:
    """管理員新增帳號（只能建 user／viewer）。密碼只進雜湊，不回傳、不寫任何紀錄。寫 access_level_audit。"""
    username = (username or "").strip()
    display_name = (display_name or "").strip()[:60]
    if not _USERNAME_RE.match(username):
        raise LevelError("帳號只能用英數字與 . _ -，3～32 字，開頭要是英數字")
    if level not in CREATABLE_LEVELS:
        raise LevelError("新增帳號的等級只能是 一般(user) 或 只能看(viewer)；管理員請建好後用「改等級」")
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LEN:
        raise LevelError(f"密碼至少 {MIN_PASSWORD_LEN} 碼")
    if len(password) > MAX_PASSWORD_LEN:
        raise LevelError(f"密碼最多 {MAX_PASSWORD_LEN} 碼")
    if password.lower() == username.lower() or username.lower() in password.lower():
        raise LevelError("密碼不可包含帳號名稱")
    if len(set(password)) < 4 or _too_predictable(password):
        raise LevelError("密碼太好猜（重複、連號或常見密碼），請改用較難猜的")
    # 預檢＋建立帳號＋稽核放在同一個交易：稽核寫不進去就整筆不建（審查：不可建了帳號卻沒有紀錄）；
    # BEGIN IMMEDIATE 讓兩個並行請求排隊，大小寫不同的同名帳號不會同時通過預檢。
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        if conn.execute("SELECT 1 FROM users WHERE lower(username) = lower(?)", (username,)).fetchone():
            raise LevelError(f"帳號已存在：{username}")
        conn.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                     (username, auth.hash_password(password), level))
        note = f"建立帳號（{display_name}）" if display_name else "建立帳號"
        conn.execute(
            "INSERT INTO access_level_audit (actor, target, old_level, new_level, ip) VALUES (?, ?, ?, ?, ?)",
            (actor, username, note, level, ip))
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise LevelError(f"帳號已存在：{username}") from exc
    except BaseException:
        conn.rollback()
        raise
    return {"username": username, "display_name": display_name, "level": level,
            "level_label": auth.LEVEL_LABEL[level]}


def list_level_audit(conn: sqlite3.Connection, limit: int = 500) -> list[dict]:
    rows = conn.execute(
        "SELECT id, at, actor, target, old_level, new_level, ip FROM access_level_audit ORDER BY id DESC LIMIT ?",
        (max(1, min(int(limit), 5000)),)).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ 匯出紀錄
def note_export(request, *, name: str | None = None, rows: int | None = None, summary: str | None = None) -> None:
    """端點告訴中介層「這次匯出的名稱／列數／條件」，中介層寫紀錄時帶上。"""
    st = request.state
    if name is not None:
        st.export_name = name
    if rows is not None:
        st.export_rows = rows
    if summary is not None:
        st.export_summary = summary


def filename_from_disposition(value: str | None) -> str | None:
    if not value:
        return None
    m = re.search(r"filename\*=UTF-8''([^;]+)", value, re.I)
    if m:
        return unquote(m.group(1))[:200]
    m = re.search(r'filename="?([^";]+)"?', value, re.I)
    return m.group(1)[:200] if m else None


def is_attachment(response) -> bool:
    cd = response.headers.get("content-disposition", "")
    return "attachment" in cd.lower()


#: 匯出紀錄的「條件」：這些鍵的值照存（截 80 字）。清單來自前端實際送出的匯出／下載／頁面篩選鍵；
#: 識別用的鍵（key＝機器鍵、username、asset_serial、ip、hostname…）也在內——稽核要看得出「匯出了誰的什麼」。
_SAFE_FILTER_KEYS = frozenset({
    "tab", "status", "env", "room", "from", "to", "page", "size", "limit", "offset", "sort", "order", "bucket",
    "role", "kind", "type", "platform", "os", "format", "fmt", "days", "scope", "box", "level", "mode", "only",
    "key", "mk", "username", "asset_serial", "serial", "ip", "hostname", "host", "machine", "system", "state", "st",
    "stage", "sub", "view", "priv", "cluster", "api", "verdict", "filter_field", "filter_value", "os_source",
    "location", "unit", "step", "sev", "scan", "scan_status", "rule", "gk", "rack", "task", "col", "val", "lg",
    "tag", "action", "os_class", "hide_builtin", "cols", "start", "reason", "port", "ym", "risk", "registered", "show", "trial"})
#: 自由輸入的搜尋字：只記字數
_FREE_TEXT_KEYS = frozenset({"q", "kw", "keyword", "search"})
#: 名稱含這些字樣就遮值。注意不含單獨的 "key"（那是機器鍵）；只有 api_key／secret_key 這類才算機密。
_SECRET_KEY_HINTS = ("secret", "token", "passw", "pwd", "passphrase", "credential", "cookie", "session", "auth",
                     "api_key", "apikey", "api-key", "access_key", "accesskey", "secret_key", "private_key", "privatekey")
_MAX_VALUE = 80
_MAX_SUMMARY_TOTAL = 400


def safe_query_summary(query: str | None) -> str:
    """把網址查詢字串變成可以永久留存的條件摘要（export_log 是只增不改的稽核表，寫進去就拿不掉）。
    · 名稱含 secret／token／passw／pwd／credential／cookie／session／auth／api_key 等：只記「<鍵>=<已遮蔽>」。
    · q／kw（自由輸入的搜尋字）：只記字數。
    · 白名單內的鍵（含 key、username、asset_serial 這類識別）：值截到 80 字。　· 其他鍵：只記鍵名，不記值。
    · 整段最多 400 字。"""
    from urllib.parse import parse_qsl

    if not query:
        return ""
    parts: list[str] = []
    for k, v in parse_qsl(query, keep_blank_values=True):
        kl = k.lower()
        if any(h in kl for h in _SECRET_KEY_HINTS):
            parts.append(f"{k[:40]}=<已遮蔽>")
        elif kl in _FREE_TEXT_KEYS:
            parts.append(f"{kl}=<{len(v)}字>")
        elif kl in _SAFE_FILTER_KEYS:
            parts.append(f"{k}={v[:_MAX_VALUE]}")
        else:
            parts.append(f"{k[:40]}=<略>")
    return "&".join(parts)[:_MAX_SUMMARY_TOTAL]


def ensure_tables(conn: sqlite3.Connection) -> None:
    """還沒跑過新版 init_db 的舊資料庫（沒有 export_log／access_level_audit）：把 schema.sql 裡 Q91 那一段補建起來。
    正式服務啟動時 init_db 一定會建；這是讓「舊 DB 直接被新程式打開」也不會因為缺表而無法下載。"""
    from pathlib import Path

    text = (Path(__file__).with_name("schema.sql")).read_text(encoding="utf-8")
    marker = "-- ===== Q91 下載／匯出紀錄"
    conn.executescript(text[text.index(marker):])


def log_export(conn: sqlite3.Connection, *, username: str | None, level: str | None, ip: str | None,
               method: str, path: str, name: str | None, filename: str | None,
               rows: int | None, summary: str | None, status: int) -> None:
    try:
        _insert_export(conn, username, level, ip, method, path, name, filename, rows, summary, status)
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc):
            raise
        ensure_tables(conn)
        _insert_export(conn, username, level, ip, method, path, name, filename, rows, summary, status)


def _insert_export(conn, username, level, ip, method, path, name, filename, rows, summary, status) -> None:
    import audit_digest

    summary = audit_digest.tag_if_login_disabled(conn, summary)         # 登入停用時的匿名操作一眼可辨
    conn.execute(
        "INSERT INTO export_log (username, level, ip, method, path, export_name, filename, row_count, "
        "filter_summary, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (username, level, ip, method, path, name, filename, rows, (summary or "")[:_MAX_SUMMARY] or None, status))
    conn.commit()


def list_export_log(conn: sqlite3.Connection, limit: int = 1000) -> list[dict]:
    rows = conn.execute(
        "SELECT id, at, username, level, ip, method, path, export_name, filename, row_count, filter_summary, status "
        "FROM export_log ORDER BY id DESC LIMIT ?", (max(1, min(int(limit), 10000)),)).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ 畫面表格匯出
def table_export_needs_admin(title: str | None, headers: list | None, n_rows: int) -> str | None:
    """畫面表格匯出是否要管理員。要的話回原因（給 403 訊息用），不用回 None。"""
    if n_rows > BULK_ROWS:
        return f"大量匯出（{n_rows} 列，超過 {BULK_ROWS} 列）"
    text = " ".join([title or ""] + [str(h) for h in (headers or [])])
    if _ACCOUNT_WORDS.search(text):
        return "含帳號相關欄位"
    return None
