"""非緊急通知每天一則彙整（2.060.19，S6；使用者 11-11：「Teams 一天只收到 1 則彙整＋必要的立即告警」）。

哪些進彙整（不立即發）：WhatsUp 🟡 摘要、磁碟「準備」級、磁碟暴增（終點沒到緊急門檻的）。
哪些照舊立即發：🔴 斷線／升級／短暫事故、恢復、磁碟「緊急」、監控中斷（這些不經過這裡）。

做法：各來源把要講的行 add() 進 notice_digest_item（不發）；排程器每分鐘 tick()，到設定時間（預設 08:00）
把前一次彙整之後累積的一次組成一則 Teams 訊息排進佇列。每天最多一則。
設定 app_settings.notice_digest_time：'HH:MM'＝每天彙整；空字串＝關閉彙整、各來源照舊立即發（退回舊行為）。
只寫本機表、排 Teams 佇列（C 類：只通知，不連任何主機）。
"""
from __future__ import annotations

import re
from datetime import datetime

KEY_TIME = "notice_digest_time"
KEY_LAST = "notice_digest_last_day"
DEFAULT_TIME = "08:00"
PER_SOURCE = 5                       # 每一類最多列 5 行，其餘「另有 N 項」
SOURCES = {"wug": "WhatsUp 告警（非緊急、短暫已恢復）", "wug_flap": "WhatsUp 反覆斷線（今天斷了幾次）", "wug_longterm": "WhatsUp 長期未恢復", "disk": "磁碟準備級", "disk_np": "磁碟緊急（測試／備援等非正式機）", "surge": "磁碟暴增"}
LINK_PATH = {"wug": "/v2/incident?tab=wug", "wug_flap": "/v2", "wug_longterm": "/v2", "disk": "/v2/storage", "disk_np": "/v2/storage", "surge": "/v2/storage"}

SCHEMA = ("CREATE TABLE IF NOT EXISTS notice_digest_item (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, "
          "source TEXT NOT NULL, line TEXT NOT NULL, sent_at TEXT, delivery_key TEXT)")


def ensure(conn) -> None:
    conn.execute(SCHEMA)


def _get(conn, key: str, default: str | None = None) -> str | None:
    r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return r[0] if r and r[0] is not None else default


def _set(conn, key: str, value: str) -> None:
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def digest_time(conn) -> str | None:
    """'HH:MM'；None＝彙整關閉（各來源立即發）。設定壞掉當成預設 08:00（寧可晚一點發，也不要洗版）。"""
    try:
        v = _get(conn, KEY_TIME, DEFAULT_TIME)
    except Exception:  # noqa: BLE001 - app_settings 還沒建（測試或全新庫）→ 用預設
        v = DEFAULT_TIME
    v = (v or "").strip()
    if not v:
        return None
    return v if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v) else DEFAULT_TIME


def set_time(conn, v: str) -> str | None:
    v = (v or "").strip()
    if v and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
        raise ValueError("彙整時間要是 HH:MM（例如 08:00），留空＝不彙整、立即發")
    _set(conn, KEY_TIME, v)
    return v or None


def enabled(conn) -> bool:
    return digest_time(conn) is not None


def add(conn, source: str, lines: list[str], now: datetime) -> bool:
    """彙整開著就把這幾行存起來、回 True（呼叫端不要再立即發）；關著回 False（呼叫端照舊立即發）。不 commit。"""
    if not enabled(conn):
        return False
    ensure(conn)
    at = now.strftime("%Y-%m-%d %H:%M:%S")
    conn.executemany("INSERT INTO notice_digest_item (at, source, line) VALUES (?,?,?)", [(at, source, x) for x in lines if x])
    return True


def pending(conn) -> list[dict]:
    ensure(conn)
    return [dict(r) for r in conn.execute("SELECT id, at, source, line FROM notice_digest_item WHERE sent_at IS NULL ORDER BY id")]


def build(conn, items: list[dict], now: datetime) -> tuple[str, list[str], str | None]:
    """(第一行, 內文行, 連結)。第一行帶日期時間＋總項數；每一類一個小標題，一類最多 5 行。"""
    import notice_fmt as nf
    first = f"🟡 每日彙整 {now:%Y-%m-%d %H:%M}｜非緊急：{len(items)} 項"
    lines: list[str] = []
    srcs = [s for s in SOURCES if any(i["source"] == s for i in items)] + sorted({i["source"] for i in items} - set(SOURCES))
    for s in srcs:
        xs = [i["line"] for i in items if i["source"] == s]
        lines.append(f"【{SOURCES.get(s, s)}】{len(xs)} 項")
        lines += nf.cap(xs, unit="項", limit=PER_SOURCE)
    link = nf.link_of(conn, LINK_PATH.get(srcs[0], "/v2/incident") if srcs else "/v2/incident")
    return first, lines, link


def _flush(conn, now: datetime) -> dict:
    """把累積的組成一則排進 Teams。Teams 沒開（或設定壞掉）＝不動，留著等開了再送（不吞掉、不假裝送過）。"""
    import notice_fmt as nf
    import teams_notify as tn
    if not _has_table(conn):
        return {"status": "empty", "items": 0}
    items = pending(conn)
    if not items:
        return {"status": "empty", "items": 0}
    try:
        teams_on = tn.load_settings(conn)["enabled"]
    except tn.SettingsCorrupt:
        teams_on = False
    if not teams_on:
        return {"status": "teams_off", "items": len(items)}
    first, lines, link = build(conn, items, now)
    tn.ensure(conn)
    key = f"digest:{now:%Y%m%d%H%M%S%f}"               # 每次一把新鍵：同一天重送（改時間、重設）不會被佇列去重吞掉
    tn._enqueue(conn, key, "notice_digest", "digest", "warning", first, nf.card(first, lines, link, color="Warning"), now)
    conn.execute(f"UPDATE notice_digest_item SET sent_at = ?, delivery_key = ? WHERE id IN ({','.join('?' * len(items))})",
                 (now.strftime("%Y-%m-%d %H:%M:%S"), key, *[i["id"] for i in items]))
    return {"status": "queued", "items": len(items), "key": key}


def _has_table(conn) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='notice_digest_item'").fetchone() is not None


def tick(conn, now: datetime | None = None) -> dict:
    """到設定時間、今天還沒彙整過 → 把累積的組成一則排進 Teams。自己 commit。
    彙整被關掉時還有累積的＝馬上送出（不讓它們卡著，審查 P2）；Teams 沒開＝留著，下次 tick 再試。"""
    import teams_notify as tn
    now = now or tn.now_taipei()
    t = digest_time(conn)
    if t is None:
        out = _flush(conn, now)
        if out["status"] == "queued":
            conn.commit()
        return {**out, "status": "disabled" if out["status"] != "queued" else "flushed"}
    day = now.strftime("%Y-%m-%d")
    if now.strftime("%H:%M") < t or (_get(conn, KEY_LAST, "") or "") >= day:
        return {"status": "not_yet"}
    out = _flush(conn, now)
    if out["status"] == "teams_off":
        return out                                     # 今天不算彙整過，Teams 開了之後同一天會再試
    if _has_table(conn):
        conn.execute("DELETE FROM notice_digest_item WHERE sent_at IS NOT NULL AND sent_at < date(?, '-90 day')", (day,))
    _set(conn, KEY_LAST, day)
    conn.commit()
    return out
