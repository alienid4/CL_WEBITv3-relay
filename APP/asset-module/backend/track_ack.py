"""1.4 追蹤處理／1.3 設備風險「標已處理」（2.066.24）。

一筆＝追蹤處理的一列（id 跟 v2_today.track 的列 id 同一把，例 hardware:<machine_key>、capacity:ds:<id>）。
- 標已處理：記誰、何時、備註、幾天後再看（1～30 天，預設 7）。期間內畫面預設不列，「已處理」可看。
- 到期後如果問題還在，自動回到清單（不用人去取消）；問題消失就自然不出現。
- 取消：寫 cleared_at，不刪原紀錄（稽核要看得到誰標過）。
- 只記處理狀態，不改任何資產／風險資料；台數口徑（track 的 total／high）不受影響。
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta

DEFAULT_DAYS = 7
MAX_DAYS = 30
_ID_RE = re.compile(r"^[a-z_]+:[^\s]{1,300}$")

SQL = """CREATE TABLE IF NOT EXISTS track_ack (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item TEXT NOT NULL,              -- 追蹤處理列 id
    cat TEXT,
    target TEXT,
    note TEXT,
    acked_by TEXT NOT NULL,
    acked_at TEXT NOT NULL,
    until TEXT NOT NULL,             -- YYYY-MM-DD：這一天起回到清單
    cleared_by TEXT,
    cleared_at TEXT)"""


def ensure(conn: sqlite3.Connection) -> None:
    conn.execute(SQL)
    conn.execute("CREATE INDEX IF NOT EXISTS ix_track_ack_item ON track_ack(item)")


def _has(conn) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='track_ack'").fetchone() is not None


def _now() -> datetime:
    return datetime.now()


def active(conn, now: datetime | None = None) -> dict[str, dict]:
    """目前有效的標記：item → {by, at, note, until}。同一列多筆取最新。表不存在＝沒有標記（只讀，不建表）。"""
    if not _has(conn):
        return {}
    today = (now or _now()).strftime("%Y-%m-%d")
    out: dict[str, dict] = {}
    for r in conn.execute("SELECT item, note, acked_by, acked_at, until FROM track_ack "
                          "WHERE cleared_at IS NULL AND until > ? ORDER BY id", (today,)):
        out[r["item"]] = {"by": r["acked_by"], "at": r["acked_at"], "note": r["note"] or "", "until": r["until"]}
    return out


def mark(conn, item: str, actor: str, note: str | None = None, days: int | None = None,
         cat: str | None = None, target: str | None = None, now: datetime | None = None) -> dict:
    """標已處理（不 commit）。同一列已有有效標記＝蓋過（舊的寫 cleared）。"""
    item = (item or "").strip()
    if not _ID_RE.match(item):
        raise ValueError("項目代號不正確")
    d = DEFAULT_DAYS if days in (None, "") else int(days)
    if not 1 <= d <= MAX_DAYS:
        raise ValueError(f"天數要在 1～{MAX_DAYS} 天")
    note = (note or "").strip()[:500]
    now = now or _now()
    ensure(conn)
    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE track_ack SET cleared_by = ?, cleared_at = ? WHERE item = ? AND cleared_at IS NULL", (actor, ts, item))
    until = (now.date() + timedelta(days=d)).isoformat()
    conn.execute("INSERT INTO track_ack (item, cat, target, note, acked_by, acked_at, until) VALUES (?,?,?,?,?,?,?)",
                 (item, (cat or "")[:40] or None, (target or "")[:200] or None, note or None, actor, ts, until))
    return {"item": item, "by": actor, "at": ts, "note": note, "until": until}


def clear(conn, item: str, actor: str, now: datetime | None = None) -> int:
    """取消標記（不 commit）。回取消幾筆；0＝本來就沒有。"""
    if not _has(conn):
        return 0
    ts = (now or _now()).strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute("UPDATE track_ack SET cleared_by = ?, cleared_at = ? WHERE item = ? AND cleared_at IS NULL", (actor, ts, item))
    return cur.rowcount
