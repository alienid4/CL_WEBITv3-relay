"""告警「需要注意」判斷＋例行時段（2.066.x，使用者 11-13：「系統首頁才是重點；Email／Teams 留著但照系統判斷；只有非例行的斷線才要人注意」）。

## 判斷（每一筆告警事件 alert_event 都有一個結果＋理由，首頁、通知、下鑽讀同一支 judge）

* 需要注意＝還沒恢復、從開始算已斷 ≥ N 分（app_settings alert_attention_min，預設 10）、而且**現在**不在這台的例行時段內。
  例行時段結束時還沒恢復 → 那一刻起就是需要注意（判斷用「現在」，不用開始時間）。
* 其他都進「例行／已自動恢復」（摺疊，件數可下鑽，不丟資料）：
  未滿 N 分仍斷（young）、例行時段內仍斷（routine_now）、N 分內自動恢復（auto_recovered）、例行時段內恢復（routine_recovered）、
  曾需注意已恢復（recovered）。反覆斷線（今天同一台 ≥ FLAP_MIN 次）另列一台一行；**不會**因為反覆斷線就列需要注意，
  只在每日彙整加一行「今天斷了 N 次」。
* 一個事件＝alert_event 一列（設備＋監控項目的一次問題，跨午夜、收集端重啟、重複快照都沿用同一列；見 wug_alert.apply_snapshot）。
* 「同一台」：對到資產用 machine_key；對不到用 ip:<IP>（例行時段也用同一把鍵）。

## 例行時段

* 手動：管理員在主機頁登記每天 HH:MM–HH:MM（可跨午夜、可設到期日），每次新增／修改／刪除都留紀錄（誰、何時、改前、改後）。
* 建議：最近 14 天同一台、同一時刻 ±15 分、有 ≥ 10 天都斷過 → 列「建議例行」，**管理員確認才生效**，拒絕的不再建議。
  這台的告警紀錄不滿 14 天的不建議。建議是讀的時候算的（GET 不寫庫）。
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta

K_MIN = "alert_attention_min"
K_OPEN_MIN = "alert_attention_open_min"     # 開盤時段（事件的 market＝開盤）用的門檻
K_LONG_H = "alert_longterm_hours"           # 斷超過這麼多小時還沒恢復＝長期未恢復（使用者 11-14：.14 需要注意 99 件，很多斷好幾天）
DEFAULT_LONG_H = 24
LONG_LIMITS = (1, 720)
K_CUTOVER = "alert_attention_cutover"        # 第一次用新規則排通知的時間：之前就開的事件不推「發生」（避免升級當下洗版）
DEFAULT_MIN = 10
DEFAULT_OPEN_MIN = 5          # 使用者 11-14：開盤時段斷 5 分才算需要注意
MIN_LIMITS = (0, 240)                        # 0＝一斷就算
COLLECT_CAP_MIN = 60                         # 讀不到監控明細（設備不是 Down）最多當收集問題這麼久，超過就列需要注意
FLAP_MIN = 3                       # 今天同一台斷了幾次以上算反覆斷線
SUGGEST_DAYS = 14
SUGGEST_HIT_DAYS = 10
SUGGEST_TOL_MIN = 15
FMT = "%Y-%m-%d %H:%M:%S"
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

MONREAD_MONITOR = "(監控明細讀取失敗)"       # 同 wug_alert.MONREAD_MONITOR：收集問題，不是設備故障
BUCKETS = (
    ("longterm", "長期未恢復"),
    ("young", "未滿門檻仍斷"),
    ("collect", "監控明細讀不到（收集問題）"),
    ("routine_now", "例行時段內仍斷"),
    ("auto_recovered", "門檻內自動恢復"),
    ("routine_recovered", "例行時段內恢復"),
    ("recovered", "超過門檻、已恢復"),
)
BUCKET_LABEL = dict(BUCKETS)

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS routine_window (
        id INTEGER PRIMARY KEY AUTOINCREMENT, host_key TEXT NOT NULL, start_hhmm TEXT NOT NULL, end_hhmm TEXT NOT NULL,
        expires_on TEXT, note TEXT, source TEXT NOT NULL DEFAULT 'manual', status TEXT NOT NULL DEFAULT 'active',
        created_by TEXT, created_at TEXT NOT NULL, updated_by TEXT, updated_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_routine_window_host ON routine_window(host_key, status)",
    """CREATE TABLE IF NOT EXISTS routine_window_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, window_id INTEGER, host_key TEXT NOT NULL, action TEXT NOT NULL,
        who TEXT, at TEXT NOT NULL, before_json TEXT, after_json TEXT)""",
    """CREATE TABLE IF NOT EXISTS routine_suggestion_decision (
        id INTEGER PRIMARY KEY AUTOINCREMENT, host_key TEXT NOT NULL, start_hhmm TEXT NOT NULL, end_hhmm TEXT NOT NULL,
        decision TEXT NOT NULL, who TEXT, at TEXT NOT NULL)""",
]


def ensure(conn: sqlite3.Connection) -> None:
    for s in SCHEMA:
        conn.execute(s)


def _has(conn, t: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() is not None


def _p(t) -> datetime | None:
    try:
        return datetime.strptime(str(t)[:19], FMT) if t else None
    except ValueError:
        return None


def _setting(conn, key: str, default: int) -> int:
    try:
        r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        v = int(r[0]) if r and str(r[0]).strip() else default
    except (TypeError, ValueError, sqlite3.Error):
        v = default
    return min(max(v, MIN_LIMITS[0]), MIN_LIMITS[1])


def threshold(conn) -> int:
    """非開盤時段的門檻（分）。"""
    return _setting(conn, K_MIN, DEFAULT_MIN)


def thresholds(conn) -> dict:
    """{"min": 非開盤, "open": 開盤}。事件發生在開盤時段（market＝開盤）用 open。"""
    try:
        r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (K_LONG_H,)).fetchone()
        h = int(r[0]) if r and str(r[0]).strip() else DEFAULT_LONG_H
    except (TypeError, ValueError, sqlite3.Error):
        h = DEFAULT_LONG_H
    return {"min": threshold(conn), "open": _setting(conn, K_OPEN_MIN, DEFAULT_OPEN_MIN),
            "long_h": min(max(h, LONG_LIMITS[0]), LONG_LIMITS[1])}


def th_for(r, th: dict) -> int:
    return th["open"] if (_g(r, "market") or "") == "開盤" else th["min"]


def _chk_min(v) -> int:
    if not isinstance(v, int) or isinstance(v, bool) or not MIN_LIMITS[0] <= v <= MIN_LIMITS[1]:
        raise ValueError(f"需要注意的門檻要是 {MIN_LIMITS[0]}～{MIN_LIMITS[1]} 分鐘的整數（0＝一斷就算）")
    return v


def _put(conn, key: str, v: int) -> None:
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(v)))


def save_threshold(conn, minutes, open_minutes=None, longterm_hours=None) -> dict:
    """非開盤門檻；open_minutes／longterm_hours 給了一起存。"""
    if longterm_hours is not None and (not isinstance(longterm_hours, int) or isinstance(longterm_hours, bool)
                                       or not LONG_LIMITS[0] <= longterm_hours <= LONG_LIMITS[1]):
        raise ValueError(f"長期未恢復的小時數要是 {LONG_LIMITS[0]}～{LONG_LIMITS[1]} 的整數")
    _put(conn, K_MIN, _chk_min(minutes))
    if longterm_hours is not None:
        _put(conn, K_LONG_H, longterm_hours)
    if open_minutes is not None:
        _put(conn, K_OPEN_MIN, _chk_min(open_minutes))
    return thresholds(conn)


def cutover(conn, now: datetime | None = None) -> str | None:
    """新規則上線（第一次排通知）的時間；now 給了且還沒記就記下來（不 commit）。"""
    r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (K_CUTOVER,)).fetchone()
    if r and r[0]:
        return r[0]
    if now is None:
        return None
    _put(conn, K_CUTOVER, now.strftime(FMT))
    return now.strftime(FMT)


def _g(r, k):
    if isinstance(r, sqlite3.Row):
        return r[k] if k in r.keys() else None
    return r.get(k)


def host_key(r) -> str:
    """同一台的鍵：對到資產用 machine_key；對不到用 ip:<IP>；連 IP 都沒有用 wug:<設備 ID>。"""
    g = lambda k: _g(r, k)  # noqa: E731
    mk = (g("machine_key") or "").strip()
    if mk:
        return mk
    ip = (g("ip") or "").strip()
    return f"ip:{ip}" if ip else f"wug:{g('wug_device_id') or ''}"


def _mins(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def in_span(start: str, end: str, t: datetime) -> bool:
    """t 的時刻是否在每天 start–end 內（可跨午夜）。start==end 不允許登記（_validate 擋），這裡當作不在。"""
    a, b, m = _mins(start), _mins(end), t.hour * 60 + t.minute
    if a == b:
        return False
    return a <= m < b if a < b else (m >= a or m < b)


# ── 例行時段 ─────────────────────────────────────────────────────────
def windows(conn, key: str | None = None, include_inactive: bool = False) -> list[dict]:
    if not _has(conn, "routine_window"):
        return []
    q, a = "SELECT * FROM routine_window", []
    w = [] if include_inactive else ["status = 'active'"]
    if key:
        w.append("host_key = ?")
        a.append(key)
    q += (" WHERE " + " AND ".join(w) if w else "") + " ORDER BY host_key, start_hhmm, id"
    return [dict(r) for r in conn.execute(q, a)]


def active_map(conn, today: str) -> dict[str, list[dict]]:
    """host_key → 有效的例行時段（沒過期的）。"""
    out: dict[str, list[dict]] = {}
    for w in windows(conn):
        if w["expires_on"] and w["expires_on"] < today:
            continue
        out.setdefault(w["host_key"], []).append(w)
    return out


def occ_start(w: dict, t: datetime) -> datetime | None:
    """t 落在 w 的哪一次（哪一天的那一段）→ 那一段的開始時間；不在時段內回 None。跨午夜的凌晨部分算前一天開始的那段。"""
    if not in_span(w["start_hhmm"], w["end_hhmm"], t):
        return None
    a = _mins(w["start_hhmm"])
    base = t.replace(hour=a // 60, minute=a % 60, second=0, microsecond=0)
    return base if t >= base else base - timedelta(days=1)


def window_at(wmap: dict, key: str, t: datetime) -> dict | None:
    for w in wmap.get(key, []):
        if in_span(w["start_hhmm"], w["end_hhmm"], t):
            return w
    return None


def routine_for(wmap: dict, key: str, started: datetime | None, t: datetime) -> dict | None:
    """例行時段只管「在這一段時段開始之後才發生」的事件：t 在時段內、而且事件開始時間不早於這一段的開始。
    時段開始前就已經斷的（審查 P2-2），照一般規則判斷。"""
    if started is None:
        return None
    for w in wmap.get(key, []):
        o = occ_start(w, t)
        if o is not None and started >= o:
            return w
    return None


def _validate(start: str, end: str, expires_on: str | None) -> None:
    if not HHMM.match(start or "") or not HHMM.match(end or ""):
        raise ValueError("時段要是 HH:MM（00:00～23:59）")
    if start == end:
        raise ValueError("開始與結束不能一樣（不接受「整天」例行時段）")
    if expires_on and not re.match(r"^\d{4}-\d{2}-\d{2}$", expires_on):
        raise ValueError("到期日要是 YYYY-MM-DD")


def _log(conn, wid, key, action, who, now, before, after) -> None:
    conn.execute("INSERT INTO routine_window_log (window_id, host_key, action, who, at, before_json, after_json) VALUES (?,?,?,?,?,?,?)",
                 (wid, key, action, who, now.strftime(FMT), json.dumps(before, ensure_ascii=False) if before else None,
                  json.dumps(after, ensure_ascii=False) if after else None))


_PUB = ("start_hhmm", "end_hhmm", "expires_on", "note", "source", "status")


def add_window(conn, key: str, start: str, end: str, expires_on: str | None, note: str, who: str, now: datetime, source: str = "manual") -> dict:
    ensure(conn)
    key = (key or "").strip()
    if not key:
        raise ValueError("缺主機")
    expires_on = (expires_on or "").strip() or None
    _validate(start, end, expires_on)
    cur = conn.execute("INSERT INTO routine_window (host_key, start_hhmm, end_hhmm, expires_on, note, source, created_by, created_at) VALUES (?,?,?,?,?,?,?,?)",
                       (key, start, end, expires_on, (note or "").strip()[:200] or None, source, who, now.strftime(FMT)))
    row = dict(conn.execute("SELECT * FROM routine_window WHERE id = ?", (cur.lastrowid,)).fetchone())
    _log(conn, row["id"], key, "add", who, now, None, {k: row[k] for k in _PUB})
    return row


def update_window(conn, wid: int, start: str, end: str, expires_on: str | None, note: str, who: str, now: datetime) -> dict:
    ensure(conn)
    old = conn.execute("SELECT * FROM routine_window WHERE id = ? AND status = 'active'", (wid,)).fetchone()
    if old is None:
        raise LookupError("找不到這個例行時段")
    expires_on = (expires_on or "").strip() or None
    _validate(start, end, expires_on)
    conn.execute("UPDATE routine_window SET start_hhmm=?, end_hhmm=?, expires_on=?, note=?, updated_by=?, updated_at=? WHERE id=?",
                 (start, end, expires_on, (note or "").strip()[:200] or None, who, now.strftime(FMT), wid))
    row = dict(conn.execute("SELECT * FROM routine_window WHERE id = ?", (wid,)).fetchone())
    _log(conn, wid, old["host_key"], "update", who, now, {k: old[k] for k in _PUB}, {k: row[k] for k in _PUB})
    return row


def delete_window(conn, wid: int, who: str, now: datetime) -> None:
    """不真的刪：標 deleted，留紀錄。"""
    ensure(conn)
    old = conn.execute("SELECT * FROM routine_window WHERE id = ? AND status = 'active'", (wid,)).fetchone()
    if old is None:
        raise LookupError("找不到這個例行時段")
    conn.execute("UPDATE routine_window SET status='deleted', updated_by=?, updated_at=? WHERE id=?", (who, now.strftime(FMT), wid))
    _log(conn, wid, old["host_key"], "delete", who, now, {k: old[k] for k in _PUB}, None)


def window_log(conn, key: str | None = None) -> list[dict]:
    if not _has(conn, "routine_window_log"):
        return []
    q = "SELECT * FROM routine_window_log" + (" WHERE host_key = ?" if key else "") + " ORDER BY id DESC"
    rows = [dict(r) for r in conn.execute(q, (key,) if key else ())]
    for i, r in enumerate(rows, 1):
        r["__no"] = i
    return rows


# ── 建議例行（讀的時候算；確認才生效）──────────────────────────────────
def _hhmm(m: int) -> str:
    m %= 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def _cdist(a: int, b: int) -> int:
    d = abs(a - b) % 1440
    return min(d, 1440 - d)


def suggestions(conn, now: datetime, key: str | None = None) -> list[dict]:
    """最近 SUGGEST_DAYS 天同一台、同一時刻 ±SUGGEST_TOL_MIN 分、有 ≥ SUGGEST_HIT_DAYS 天斷過 → 建議。
    這台的告警紀錄不滿 SUGGEST_DAYS 天（最早一筆晚於 14 天前）不建議；已有涵蓋的有效時段或被拒絕過的不再建議。"""
    if not _has(conn, "alert_event"):
        return []
    since = (now - timedelta(days=SUGGEST_DAYS)).strftime(FMT)
    first: dict[str, str] = {}
    per: dict[str, list[tuple[str, int, int, dict]]] = {}
    # 每台最早一筆（判斷紀錄滿不滿 14 天）：用 SQL 聚合，不把整張表讀進來
    for r in conn.execute("SELECT machine_key, ip, wug_device_id, MIN(started_at) AS a FROM alert_event WHERE tier != 'maint' "
                          "GROUP BY COALESCE(NULLIF(machine_key, ''), CASE WHEN COALESCE(ip, '') != '' THEN 'ip:' || ip ELSE 'wug:' || wug_device_id END)"):
        k = host_key(r)
        first[k] = min(first.get(k, r["a"]), r["a"])
    for r in conn.execute("SELECT * FROM alert_event WHERE tier != 'maint' AND started_at >= ? ORDER BY started_at, id", (since,)):
        k = host_key(r)
        if key and k != key:
            continue
        st = _p(r["started_at"])
        if st is None:
            continue
        end = _p(r["ended_at"]) or now
        per.setdefault(k, []).append((st.strftime("%Y-%m-%d"), st.hour * 60 + st.minute, max(1, int((end - st).total_seconds() // 60)),
                                      {"who": r["hostname"] or r["device_name"] or r["ip"] or r["wug_device_id"], "ip": r["ip"]}))
    wmap = active_map(conn, now.strftime("%Y-%m-%d"))
    rejected = {}
    if _has(conn, "routine_suggestion_decision"):
        for d in conn.execute("SELECT host_key, start_hhmm, decision FROM routine_suggestion_decision"):
            rejected.setdefault(d["host_key"], []).append((_mins(d["start_hhmm"]), d["decision"]))
    out = []
    for k, evs in per.items():
        if first.get(k, "9999") > since:                    # 紀錄不滿 14 天
            continue
        used: list[int] = []
        for _day, m0, _d, _w in sorted(evs, key=lambda e: e[1]):
            if any(_cdist(m0, u) <= SUGGEST_TOL_MIN * 2 for u in used):
                continue
            near = [e for e in evs if _cdist(e[1], m0) <= SUGGEST_TOL_MIN]
            days = sorted({e[0] for e in near})
            if len(days) < SUGGEST_HIT_DAYS:
                continue
            used.append(m0)
            starts = sorted(e[1] for e in near)
            s = starts[0] if max(starts) - min(starts) <= 2 * SUGGEST_TOL_MIN else m0 - SUGGEST_TOL_MIN
            s = (s // 5) * 5
            dur = max(e[2] for e in near)
            e_ = s + min(max(dur, 10) + SUGGEST_TOL_MIN, 240)
            e_ = ((e_ + 4) // 5) * 5
            start, end = _hhmm(s), _hhmm(e_)
            if any(_cdist(_mins(w["start_hhmm"]), s) <= SUGGEST_TOL_MIN for w in wmap.get(k, [])):
                continue
            if any(_cdist(m, s) <= SUGGEST_TOL_MIN for m, _dec in rejected.get(k, [])):
                continue
            out.append({"host_key": k, "who": near[0][3]["who"], "ip": near[0][3]["ip"], "start_hhmm": start, "end_hhmm": end,
                        "days": len(days), "of_days": SUGGEST_DAYS, "events": len(near), "max_min": dur,
                        "reason": f"最近 {SUGGEST_DAYS} 天有 {len(days)} 天在 {_hhmm(m0)} 前後 {SUGGEST_TOL_MIN} 分內斷線（最長 {dur} 分）"})
    out.sort(key=lambda x: (x["host_key"], x["start_hhmm"]))
    for i, r in enumerate(out, 1):
        r["__no"] = i
    return out


_SUG_CACHE: dict = {}


def suggestion_count(conn, now: datetime) -> int:
    """首頁用的建議筆數：同一天、告警表與例行時段都沒變就用上次算的（每台機器的程序內快取，不寫庫）。"""
    sig = [now.strftime("%Y-%m-%d")]
    for t in ("alert_event", "routine_window", "routine_suggestion_decision"):
        sig.append(conn.execute(f"SELECT COUNT(*), MAX(id) FROM {t}").fetchone()[:] if _has(conn, t) else None)
    try:
        db_id = conn.execute("PRAGMA database_list").fetchone()[2]
    except (sqlite3.Error, TypeError, IndexError):        # 拿不到檔名就不共用快取（照算）
        db_id = id(conn)
    key = (db_id, repr(sig))
    if key not in _SUG_CACHE:
        if len(_SUG_CACHE) > 64:
            _SUG_CACHE.clear()
        _SUG_CACHE[key] = len(suggestions(conn, now))
    return _SUG_CACHE[key]


def decide(conn, key: str, start: str, end: str, decision: str, who: str, now: datetime) -> dict:
    """confirm＝建立一個例行時段（source=suggested）；reject＝記下來不再建議。"""
    ensure(conn)
    if decision not in ("confirm", "reject"):
        raise ValueError("decision 要是 confirm 或 reject")
    _validate(start, end, None)
    conn.execute("INSERT INTO routine_suggestion_decision (host_key, start_hhmm, end_hhmm, decision, who, at) VALUES (?,?,?,?,?,?)",
                 (key, start, end, decision, who, now.strftime(FMT)))
    if decision == "confirm":
        return {"window": add_window(conn, key, start, end, None, "由建議例行確認", who, now, source="suggested")}
    _log(conn, None, key, "reject_suggestion", who, now, {"start_hhmm": start, "end_hhmm": end}, None)
    return {"rejected": True}


# ── 判斷 ────────────────────────────────────────────────────────────
def _dur(r, now: datetime) -> int:
    st = _p(_g(r, "started_at"))
    end = _p(_g(r, "ended_at")) or now
    if st is None:
        return int(_g(r, "duration_min") or 0)
    return max(0, int((end - st).total_seconds() // 60))


def _wtxt(w: dict) -> str:
    return f"{w['start_hhmm']}–{w['end_hhmm']}（{'建議確認' if w.get('source') == 'suggested' else '手動登記'}）"


def notified(r) -> bool:
    """已推過「發生」（或舊規則的「升級」）＝已列過需要注意。"""
    n = _g(r, "notified") or ""
    return ",open," in n or ",escalate," in n


def judge_one(r, now: datetime, th, wmap: dict) -> tuple[str, str]:
    """回 (bucket, 理由)。bucket：attention 或 BUCKETS 其中之一。r 可以是 dict 或 sqlite3.Row；th＝thresholds() 或一個整數（兩個時段同門檻）。"""
    th = th if isinstance(th, dict) else {"min": th, "open": th}
    long_h = th.get("long_h", DEFAULT_LONG_H)
    k = host_key(r)
    d = _dur(r, now)
    n = th_for(r, th)
    tag = f"開盤門檻 {n} 分" if (_g(r, "market") or "") == "開盤" else f"門檻 {n} 分"
    st = _p(_g(r, "started_at"))
    if _g(r, "ended_at") is None:
        if d >= long_h * 60:
            return "longterm", (f"已斷 {d // 60} 小時 {d % 60} 分（超過 {long_h} 小時），列長期未恢復，不列需要注意、不再推；"
                                + ("推過的「發生」恢復時照樣推恢復" if notified(r) else "恢復時不推"))
        if notified(r):
            return "attention", f"已斷 {d} 分，已推過通知、還沒恢復（推過的不會因為進入例行時段而消失）"
        if _g(r, "monitor") == MONREAD_MONITOR and (_g(r, "wug_state") or "").strip().lower() != "down":
            if d < COLLECT_CAP_MIN:
                return "collect", f"已 {d} 分讀不到這台的監控明細，設備狀態不是 Down，先當收集問題；滿 {COLLECT_CAP_MIN} 分還這樣就列需要注意"
            return "attention", f"已 {d} 分讀不到這台的監控明細（超過 {COLLECT_CAP_MIN} 分，不能再當收集問題）"
        w = routine_for(wmap, k, st, now)
        if w:
            return "routine_now", f"已斷 {d} 分，在例行時段 {_wtxt(w)} 開始之後才斷；時段結束還沒恢復就會列入需要注意"
        if d >= n:
            return "attention", f"已斷 {d} 分（{tag}），還沒恢復，不在例行時段內" + ("（讀不到監控明細，但設備狀態是 Down）" if _g(r, "monitor") == MONREAD_MONITOR else "")
        return "young", f"已斷 {d} 分，未滿{tag}；滿了還沒恢復就會列入需要注意"
    if notified(r):
        return "recovered", f"曾斷 {d} 分，當時列需要注意並已通知，已恢復"
    w = routine_for(wmap, k, st, st) if st else None
    end = _p(_g(r, "ended_at"))
    if w and end and occ_start(w, st) and end <= occ_start(w, st) + timedelta(minutes=(_mins(w["end_hhmm"]) - _mins(w["start_hhmm"])) % 1440):
        return "routine_recovered", f"在例行時段 {_wtxt(w)} 內斷線、也在時段內恢復（{d} 分）"
    if d < n:
        return "auto_recovered", f"{d} 分內自動恢復（未滿{tag}）"
    return "recovered", f"曾斷 {d} 分（≥ {tag}），沒在例行時段內，已恢復"


def is_attention(conn, r, now: datetime, th=None, wmap: dict | None = None) -> bool:
    th = thresholds(conn) if th is None else th
    wmap = active_map(conn, now.strftime("%Y-%m-%d")) if wmap is None else wmap
    return judge_one(r, now, th, wmap)[0] == "attention"


def judge(conn, open_rows: list[dict], closed_rows: list[dict], now: datetime) -> dict:
    """首頁「需要注意」與摺疊區。open_rows／closed_rows 是已套好範圍（Q9）的告警列；每一列加 judge／judge_reason。
    加總＝下鑽：attention.n＝rows 筆數；各 bucket 的 n＝它的 rows 筆數；需要注意＋未滿＋例行中＝還開著的全部。"""
    th = thresholds(conn)
    n = th["min"]
    wmap = active_map(conn, now.strftime("%Y-%m-%d"))
    att, buckets = [], {k: [] for k, _ in BUCKETS}
    open_rows = [dict(r) for r in open_rows]               # 複本：不動到呼叫端那一份的項次
    closed_rows = [dict(r) for r in closed_rows]
    for r in open_rows + closed_rows:
        b, why = judge_one(r, now, th, wmap)
        r["judge"], r["judge_reason"], r["judge_label"] = b, why, "需要注意" if b == "attention" else BUCKET_LABEL[b]
        (att if b == "attention" else buckets[b]).append(r)
    for rs in [att] + list(buckets.values()):
        for i, r in enumerate(rs, 1):
            r["__no"] = i
    # 反覆斷線：今天同一台 ≥ FLAP_MIN 次（開著的＋今天的已恢復），一台一行；不影響需要注意
    day = now.strftime("%Y-%m-%d")
    per: dict[str, list[dict]] = {}
    seen = set()
    for r in list(open_rows) + list(closed_rows):
        if r["id"] in seen or str(r["started_at"])[:10] != day:
            continue
        seen.add(r["id"])
        per.setdefault(host_key(r), []).append(r)
    flap = [{"host_key": k, "who": rs[0].get("who") or k, "ip": rs[0].get("ip"), "n": outages(rs), "events": len(rs), "ids": [x["id"] for x in rs],
             "reason": f"今天斷了 {outages(rs)} 次（{len(rs)} 筆告警）；反覆斷線不列需要注意，只在每日彙整寫一行"}
            for k, rs in per.items() if outages(rs) >= FLAP_MIN]
    flap.sort(key=lambda x: (-x["n"], x["who"]))
    for i, f in enumerate(flap, 1):
        f["__no"] = i
    return {"min": n, "open_min": th["open"], "long_h": th["long_h"], "rows": att, "n": len(att),
            "buckets": [{"key": k, "label": lab, "n": len(buckets[k]), "rows": buckets[k]} for k, lab in BUCKETS],
            "flapping": flap}


def outages(rs) -> int:
    """同一台今天斷了幾次＝不同的開始分鐘數（同一次斷線多個監控項目一起亮只算一次）。"""
    return len({str(r["started_at"])[:16] for r in rs})


def longterm_digest(conn, now: datetime, rows: list, long_h: int) -> int:
    """長期未恢復寫進每日彙整：整份一行「長期未恢復 N 台：前幾台…」，台數變了換掉還沒送出的那行。彙整關著＝不送。不 commit。"""
    import notice_digest as nd
    if not nd.enabled(conn):
        return 0
    hosts: dict[str, str] = {}
    for r in rows:
        st = _p(r["started_at"])
        if r["ended_at"] is None and st and (now - st).total_seconds() >= long_h * 3600:
            hosts.setdefault(host_key(r), r["hostname"] or r["device_name"] or r["ip"] or host_key(r))
    nd.ensure(conn)
    old = conn.execute("SELECT id, line FROM notice_digest_item WHERE source = 'wug_longterm' AND sent_at IS NULL").fetchall()
    names = sorted(hosts.values())
    line = (f"⏳ 長期未恢復（超過 {long_h} 小時）{len(names)} 台：" + "、".join(names[:5]) + (f" 等 {len(names)} 台" if len(names) > 5 else "")) if names else None
    if line and any(o["line"] == line for o in old):
        return 0
    for o in old:
        conn.execute("DELETE FROM notice_digest_item WHERE id = ?", (o["id"],))
    if line:
        nd.add(conn, "wug_longterm", [line], now)
        return 1
    return 0


def flap_digest(conn, now: datetime, rows: list) -> int:
    """反覆斷線寫進每日彙整：一台一行「今天斷了 N 次」，次數變了就換掉同一台還沒送出的那行。彙整關著＝不送（只在畫面看）。不 commit。"""
    import notice_digest as nd
    if not nd.enabled(conn):
        return 0
    day = now.strftime("%Y-%m-%d")
    per: dict[str, list] = {}
    for r in rows:
        if str(r["started_at"])[:10] == day:
            per.setdefault(host_key(r), []).append(r)
    nd.ensure(conn)
    n = 0
    for k, rs in per.items():
        if outages(rs) < FLAP_MIN:
            continue
        r0 = rs[0]
        who = r0["hostname"] or r0["device_name"] or r0["ip"] or k
        tag = f"〔{k}〕"
        line = f"🔁 {who}（{r0['ip'] or '無 IP'}）今天斷了 {outages(rs)} 次{tag}"
        old = [o for o in conn.execute("SELECT id, line FROM notice_digest_item WHERE source = 'wug_flap' AND sent_at IS NULL").fetchall()
               if o["line"].endswith(tag)]                    # 比字尾（不用 LIKE：鍵裡的 _ % 會被當萬用字元）
        if any(o["line"] == line for o in old):
            continue
        for o in old:
            conn.execute("DELETE FROM notice_digest_item WHERE id = ?", (o["id"],))
        nd.add(conn, "wug_flap", [line], now)
        n += 1
    return n
