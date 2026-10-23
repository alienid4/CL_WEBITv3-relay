"""在線人數與操作紀錄。

2026-08-26 使用者要求：「在左上角顯示在線人數，我要知道誰在用，LOG 紀錄也要」。

## 「在線」的定義：最近 N 分鐘真的送過 request

**不是「session 沒過期」。** session TTL 是好幾小時，開著分頁去吃飯的人 session
還在，但他不在用；拿 session 表當在線人數會長期高估，而且高估的方向最糟——
「現在有 5 個人在用，先別重啟服務」是會拿來做決定的資訊，虛胖的數字會讓人白等。

所以 `sessions.last_seen_at` 由 `require_auth` 每次請求更新，在線＝
`last_seen_at` 在 `ONLINE_WINDOW_MINUTES` 分鐘內。

## 心跳為什麼要節流

一個頁面開起來會打十幾支 API，每支都寫一次 DB 是十幾次沒必要的寫入（SQLite
單寫者，而且會跟匯入那種長交易搶鎖）。同一個 session 在 `TOUCH_THROTTLE_SECONDS`
秒內只寫一次——代價是 last_seen_at 最多落後這麼多秒，對「誰在線上」完全夠用。

## 為什麼掛在 require_auth 而不是 middleware

`require_auth` 是**唯一能保證覆蓋所有端點**的地方：`test_auth_coverage.py` 會檢查
每支新端點都掛了它（白名單只有 login／logout／version 三支）。掛在別的地方，
新頁面漏記時沒有任何測試會紅，等到要查的時候才發現那段時間是空白的。

而且它手上已經有 session row 與連線，判斷「要不要寫」不用多一次查詢。

## 紀錄記什麼、不記什麼

記：**非 GET 的請求**（會改東西的）＋ 登入／登出／登入失敗。
不記：GET（佔九成流量、稽核價值低，全記會把真正要查的東西淹掉）。

⚠️ **不記 request body。** body 裡可能有密碼、真實主機名、人員姓名電話；
把它們複製一份進另一張表只是多開一個外洩面。稽核要的是「誰做了什麼動作」，
不是「他打了什麼字」——要看值改成什麼，看資料本身的 updated_at 與既有的
doc_download_audit／credential_use_audit。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

NOW = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731 - 本地時間，決策T6

#: 幾分鐘內有活動算「在線」。太短會讓正在讀畫面的人閃掉（看報表五分鐘不點任何東西
#: 很正常），太長就回到「session 沒過期」那種虛胖。
ONLINE_WINDOW_MINUTES = 10

#: 同一個 session 幾秒內只寫一次心跳。
TOUCH_THROTTLE_SECONDS = 60

#: 操作紀錄保留幾天。無上限的話這張表會一直長，SQLite 備份與 VACUUM 都會跟著變慢。
RETAIN_DAYS = 180


def client_ip(request) -> str | None:
    """取來源 IP。有反向代理時 request.client.host 會變成代理自己的位址，
    所以先看 X-Forwarded-For 的第一段。

    ⚠️ X-Forwarded-For 是**呼叫端可以偽造的**，這裡只當「參考用的來源提示」，
    不拿它做任何授權判斷（授權一律走 session cookie）。
    """
    xff = request.headers.get("x-forwarded-for") if request else None
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first[:64]
    client = getattr(request, "client", None) if request else None
    return getattr(client, "host", None) if client else None


def touch_session(conn: sqlite3.Connection, session: sqlite3.Row, request) -> None:
    """更新這個 session 的心跳。節流：距離上次未滿 TOUCH_THROTTLE_SECONDS 就不寫。

    **不能讓這裡的失敗影響請求本身**——心跳是附加資訊，為了它讓整支 API 500
    是本末倒置。所以整段包 try/except（呼叫端也包了一層，這裡是第二道）。
    """
    now = NOW()
    try:
        last = session["last_seen_at"]
    except (IndexError, KeyError):
        last = None            # 舊 DB 還沒跑 migration，當成沒心跳過
    if last:
        try:
            delta = datetime.strptime(now, "%Y-%m-%d %H:%M:%S") - \
                datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
            if delta.total_seconds() < TOUCH_THROTTLE_SECONDS:
                return
        except ValueError:
            pass               # 時間字串壞掉就當作要更新，不要因此永遠不寫
    ua = (request.headers.get("user-agent") or "")[:200] if request else None
    conn.execute(
        "UPDATE sessions SET last_seen_at = ?, last_ip = ?, user_agent = ? WHERE token = ?",
        (now, client_ip(request), ua, session["token"]),
    )
    conn.commit()


def online_users(conn: sqlite3.Connection) -> dict:
    """誰在線上。一人一列（同一個人開兩個瀏覽器算一個人，但 sessions 數另外給）。

    回傳裡一定要有 `window_minutes` 與 `last_activity_at`：
    「0 人在線」有兩種意思——真的沒人，或者這功能根本沒在記（剛升級、migration
    沒跑）。畫面上分不出來的話，人會拿一個假的 0 去做決定。
    """
    cutoff = (datetime.now() - timedelta(minutes=ONLINE_WINDOW_MINUTES)).strftime(
        "%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        "SELECT users.username AS username, MAX(sessions.last_seen_at) AS last_seen_at, "
        "       COUNT(*) AS sessions, "
        "       MAX(sessions.last_ip) AS ip "
        "FROM sessions JOIN users ON users.id = sessions.user_id "
        "WHERE sessions.last_seen_at IS NOT NULL AND sessions.last_seen_at >= ? "
        "GROUP BY users.username ORDER BY last_seen_at DESC",
        (cutoff,),
    ).fetchall()

    ever = conn.execute(
        "SELECT MAX(last_seen_at) AS t FROM sessions WHERE last_seen_at IS NOT NULL"
    ).fetchone()
    last_activity = ever["t"] if ever else None

    return {
        "count": len(rows),
        "window_minutes": ONLINE_WINDOW_MINUTES,
        "users": [
            {"username": r["username"], "last_seen_at": r["last_seen_at"],
             "sessions": r["sessions"], "ip": r["ip"],
             "idle_seconds": _idle_seconds(r["last_seen_at"])}
            for r in rows
        ],
        # 從來沒有任何心跳 → 這個功能還沒開始記，不是「沒人用」
        "last_activity_at": last_activity,
        "never_recorded": last_activity is None,
    }


def _idle_seconds(last_seen: str | None) -> int | None:
    if not last_seen:
        return None
    try:
        d = datetime.now() - datetime.strptime(last_seen, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return max(0, int(d.total_seconds()))


def log(conn: sqlite3.Connection, *, username: str | None, ip: str | None,
        method: str, path: str, status: int | None = None,
        duration_ms: int | None = None, action: str = "change") -> None:
    """寫一筆操作紀錄。失敗不可以影響請求本身（同 touch_session 的理由）。"""
    conn.execute(
        "INSERT INTO activity_log (at, username, ip, method, path, status, duration_ms, action) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (NOW(), username, ip, method, path[:300], status, duration_ms, action),
    )
    conn.commit()


def list_log(conn: sqlite3.Connection, *, username: str | None = None,
             action: str | None = None, since: str | None = None,
             limit: int = 200, offset: int = 0) -> dict:
    """操作紀錄清單。伺服器端分頁——這張表會長到幾十萬列，全撈會把瀏覽器打死。"""
    limit = max(1, min(int(limit), 1000))
    where, args = [], []
    if username:
        where.append("username = ?")
        args.append(username)
    if action:
        where.append("action = ?")
        args.append(action)
    if since:
        where.append("at >= ?")
        args.append(since)
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    total = conn.execute(f"SELECT COUNT(*) AS n FROM activity_log{clause}", args).fetchone()["n"]
    rows = conn.execute(
        f"SELECT id, at, username, ip, method, path, status, duration_ms, action "
        f"FROM activity_log{clause} ORDER BY at DESC, id DESC LIMIT ? OFFSET ?",
        [*args, limit, offset],
    ).fetchall()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "rows": [dict(r) for r in rows],
        "retain_days": RETAIN_DAYS,
    }


def log_summary(conn: sqlite3.Connection, days: int = 7) -> dict:
    """最近 N 天誰做了幾次。用來回答「誰在用這套系統」——在線人數只看得到當下。"""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        "SELECT username, COUNT(*) AS n, MAX(at) AS last_at FROM activity_log "
        "WHERE at >= ? AND username IS NOT NULL GROUP BY username ORDER BY n DESC",
        (since,),
    ).fetchall()
    failed = conn.execute(
        "SELECT COUNT(*) AS n FROM activity_log WHERE at >= ? AND action = 'login_failed'",
        (since,),
    ).fetchone()["n"]
    return {
        "days": days,
        "by_user": [dict(r) for r in rows],
        "login_failed": failed,
        "total": sum(r["n"] for r in rows),
    }


def purge_old(conn: sqlite3.Connection, retain_days: int = RETAIN_DAYS) -> int:
    """清掉超過保留期的紀錄。開機時跑一次。

    刪除是不可逆的，所以保留期刻意設得長（180 天）而且寫在常數裡看得到，
    不是埋在某個 SQL 裡的魔術數字。"""
    cutoff = (datetime.now() - timedelta(days=retain_days)).strftime("%Y-%m-%d %H:%M:%S")
    n = conn.execute("DELETE FROM activity_log WHERE at < ?", (cutoff,)).rowcount
    if n:
        conn.commit()
    return n
