"""1.5 每日檢查：容量成長與估計滿載（2.070.x，規格來源＝DailyCheck v2.4.6 的 Storage／硬碟趨勢）。

純函式為主（analyze）＋兩個唯讀歷史讀取（Pool、硬碟）。**不另建歷史表**：
  * Storage Pool 歷史＝既有 `storage_pool_daily`（Unity 匯入每天寫一列、同一天後到的蓋先到的＝同日去重，主鍵 serial+pool_id+day）。
    5.2 儲存容量頁用的是同一份，所以 1.5 與 5.2 的 Pool 使用率是同一份資料，不會兩套數字。
  * 檔案系統歷史＝`storage_fs_daily`（Unity 收集腳本 v3 起；storage_relay 匯入時同日去重、留 40 天，跟 Pool 用同一把 analyze）。
  * 硬碟歷史＝既有 `wug_lp_value`（上次輪循，每批一點）：每一天只取「當天最後一個完整批次」的值，最多回看 TREND_KEEP_DAYS 天。
這樣口徑只有一把尺：使用率一律先 disk_level.norm 成一位小數再比（89.9 不是第一級、90.0 是）。

analyze() 的規則（對照 v2.4.6，2.070.x GPT 審查後）：
  * 前次＝該對象自己「目前採用那一批的收集日以前」最近一筆有效值（日期要是 YYYY-MM-DD、使用率 0～100 的數字；壞值、壞日期、NaN、布林不採用）；目前這一批本身排除。
  * 較前次、上升／下降中：只看「現在 vs 前次」；容量差 >1%（擴縮容）或任一邊沒有容量＝不比、不跨過該點。
  * 每日成長與估計天數：同容量的連續歷史 ＋ 現在，至少 3 個不同日期才估（最近 GROWTH_WINDOW_DAYS 天，最舊點到現在）；不足寫「樣本不足，不估算」。
  * 估計兩個天數：到第一級門檻、到滿載。排序：已滿 → 已在第一級 → 到第一級天數 → 到滿載天數 → 使用率；「下降中」只改說明。
"""
from __future__ import annotations

import math
import sqlite3
from datetime import date, datetime, timedelta

import disk_level

TREND_KEEP_DAYS = 40            # 歷史最多看幾天（規格：留 40 天）
GROWTH_WINDOW_DAYS = 14         # 每日成長用最近幾天
PEAK_DAYS = 7                   # 「7 天最高」
CAP_CHANGE_PCT = 1.0            # 容量差超過這個 % ＝擴縮容

MIN_SAMPLES = 3                 # 估計天數至少要幾個不同日期的有效點（含現在）
NOTE_NO_PREV = "待累積"
NOTE_FEW = "樣本不足，不估算"
NOTE_RESIZED = "容量有變，不估算"
NOTE_UNCOMPARABLE = "容量不可比，不估算"
NOTE_FLAT = "未增加"


def _day(v) -> date | None:
    if not isinstance(v, str) or len(v) != 10:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def _pct(v) -> float | None:
    """有效使用率：數字（不是布林、不是 NaN／∞）、0～100；回一位小數。其他＝None（壞值不採用）。"""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f) or f < 0 or f > 100:
        return None
    return disk_level.norm(f)


def _cap(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f > 0 else None


def _cmp(a, b) -> str:
    """兩個容量能不能比：ok＝差在 1% 內；resized＝差超過 1%（擴縮容）；unknown＝任一邊沒有容量（不可比，不跨過該點）。"""
    a, b = _cap(a), _cap(b)
    if a is None or b is None:
        return "unknown"
    return "resized" if abs(a - b) * 100 / b > CAP_CHANGE_PCT else "ok"


def clean(points, today: date) -> list[dict]:
    """歷史點 → 今天以前、有效、每天一筆（同一天後者蓋前者）、舊到新、最多 TREND_KEEP_DAYS 天。points: [{day, pct, total?}]。"""
    by_day: dict[date, dict] = {}
    edge = today - timedelta(days=TREND_KEEP_DAYS)
    for p in points or []:
        d, v = _day(p.get("day")), _pct(p.get("pct"))
        if d is None or v is None or d >= today or d < edge:
            continue
        by_day[d] = {"day": d, "pct": v, "total": _cap(p.get("total"))}
    return [by_day[k] for k in sorted(by_day)]


def analyze(cur_pct, cur_total, points, today: date, first_level: float = disk_level.HIGH_PCT, compare_cap: bool = True) -> dict:
    """一個對象（Pool／一顆硬碟）的趨勢。回傳都是可排序的數字或 None，加上白話說明。

    {pct, prev, prev_day, delta, direction, growth, samples, days_to_first, days_to_full, tier, peak7, falling_high, note, trend_note}
    * 前次／較前次／上升下降中：只看「今天 vs 前次」（delta）；長期斜率是另一欄（growth），兩者不混成一個狀態。
    * 每日成長與估計天數：同容量的連續歷史 ＋ 現在 ≥ MIN_SAMPLES 個不同日期才估；不足寫「樣本不足，不估算」（前次、較前次照給）。
    * 容量：compare_cap=True（Pool）時任一邊沒有容量＝「容量不可比」、差 >1%＝「容量有變」，都不跨過該點；硬碟沒有容量（compare_cap=False）不判。
    * 估計兩個天數：到第一級門檻（days_to_first，已在第一級＝0）、到滿載（days_to_full，已滿＝0）。
    * tier（排序用）：0 已滿、1 已在第一級、2 有到第一級天數、3 其他。「下降中」只影響說明，不改 tier。"""
    cur = _pct(cur_pct)
    out = {"pct": cur, "prev": None, "prev_day": None, "delta": None, "direction": None, "growth": None, "samples": 0, "days_to_first": None,
           "days_to_full": None, "tier": 4, "peak7": None, "falling_high": False, "note": "查不到使用率", "trend_note": "查不到使用率"}
    if cur is None:
        return out
    hist = clean(points, today)
    peak_from = today - timedelta(days=PEAK_DAYS - 1)
    out["peak7"] = max([cur] + [h["pct"] for h in hist if h["day"] >= peak_from])
    high = cur >= first_level
    out["samples"] = 1
    block = None                                       # 不能估天數的原因（None＝可以估）
    if not hist:
        block = NOTE_NO_PREV
    else:
        prev = hist[-1]
        out["prev"], out["prev_day"] = prev["pct"], prev["day"].isoformat()
        st = _cmp(cur_total, prev["total"]) if compare_cap else "ok"
        if st == "resized":
            block = NOTE_RESIZED
        elif st == "unknown":
            block = NOTE_UNCOMPARABLE
        else:
            out["delta"] = round(cur - prev["pct"], 1)
            series = []
            for h in reversed(hist):                    # 同容量的連續歷史（從最近往回，遇到擴縮容或容量不明就斷），且在回看範圍內
                if (compare_cap and _cmp(cur_total, h["total"]) != "ok") or (today - h["day"]).days > GROWTH_WINDOW_DAYS:
                    break
                series.append(h)
            out["samples"] = len(series) + 1
            if out["samples"] < MIN_SAMPLES:
                block = NOTE_FEW
            else:
                base = series[-1]
                out["growth"] = round((cur - base["pct"]) / (today - base["day"]).days, 2)
    d = out["delta"]
    out["direction"] = None if d is None else "下降中" if d < 0 else "上升中" if d > 0 else "持平"
    out["falling_high"] = bool(high and d is not None and d < 0)
    g = out["growth"]
    if cur >= 100:
        out["days_to_first"] = out["days_to_full"] = 0
    elif high:
        out["days_to_first"] = 0
        if g is not None and g > 0:
            out["days_to_full"] = int(math.ceil((100 - cur) / g))
    elif g is not None and g > 0:
        out["days_to_first"] = int(math.ceil((first_level - cur) / g))
        out["days_to_full"] = int(math.ceil((100 - cur) / g))
    out["tier"] = 0 if cur >= 100 else 1 if high else 2 if out["days_to_first"] is not None else 3
    # 說明：下降中只是說明（不取消容量優先級）；估計天數寫兩個（到第一級、到滿載）
    parts = []
    if out["falling_high"]:
        parts.append(f"下降中，仍≥{first_level:g}%（{PEAK_DAYS} 天最高 {out['peak7']:g}%）")
    elif d is not None and d < 0:
        parts.append("下降中")
    if cur >= 100:
        est = "已滿"
    elif high:
        est = None if out["falling_high"] else "已在第一級"
        if out["days_to_full"] is not None:
            est = (est + "，" if est else "") + f"約 {out['days_to_full']} 天滿載"
    elif g is not None and g > 0:
        est = f"約 {out['days_to_first']} 天到第一級、{out['days_to_full']} 天滿載"
    else:
        est = None
    if block:
        est = f"{est}；{block}" if est else block
    elif est is None and g is not None:
        est = NOTE_FLAT
    out["note"] = est or ""
    if est:
        parts.append(est)
    out["trend_note"] = "；".join(parts)
    return out


def sort_key(t: dict) -> tuple:
    """要處理的排序：已滿 → 已在第一級 → 到第一級天數少的 → 到滿載天數少的 → 使用率高的。「下降中」只是說明，不取消容量優先級。"""
    full = t.get("days_to_full")
    return (t.get("tier", 4), t.get("days_to_first") or 0, full if full is not None else 10 ** 9, -(t.get("pct") if t.get("pct") is not None else -1))


# ---------------------------------------------------------------- 歷史讀取（唯讀、不建表）
def _has(conn, t: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (t,)).fetchone() is not None


def _pool_pct(used, total):
    return round(used * 100 / total, 1) if isinstance(used, int) and not isinstance(used, bool) and isinstance(total, int) and total > 0 else None


def pool_history(conn: sqlite3.Connection, serial: str, today: date, exclude_batch: str | None = None) -> dict[str, list[dict]]:
    """這台 Unity 每個 Pool 的每日使用率（storage_pool_daily）。{pool_id: [{day, pct, total}]}。表不存在＝空。
    today＝目前採用那一批的收集日；歷史明確排除「目前這一批」與當天以後（昨日批次當現況時不會跟自己比）。"""
    if not _has(conn, "storage_pool_daily"):
        return {}
    edge = (today - timedelta(days=TREND_KEEP_DAYS)).isoformat()
    out: dict[str, list[dict]] = {}
    for r in conn.execute("SELECT pool_id, day, size_used, size_total, batch_id FROM storage_pool_daily WHERE serial = ? AND day >= ? AND day < ? ORDER BY day",
                          (serial, edge, today.isoformat())):
        if exclude_batch is not None and r["batch_id"] == exclude_batch:
            continue
        out.setdefault(r["pool_id"], []).append({"day": r["day"], "pct": _pool_pct(r["size_used"], r["size_total"]), "total": r["size_total"]})
    return out


def pools_for(conn: sqlite3.Connection, serial: str, today: date, first_level: float, batch: str | None = None) -> list[dict]:
    """Pool 現況（storage_pool，最新一批）＋趨勢。容量讀不到的 Pool 也列（pct=None＝未收集，不是正常）。"""
    if not _has(conn, "storage_pool"):
        return []
    hist = pool_history(conn, serial, today, batch)
    rows = []
    for r in conn.execute("SELECT pool_id, name, size_used, size_total FROM storage_pool WHERE serial = ? ORDER BY name, pool_id", (serial,)):
        t = analyze(_pool_pct(r["size_used"], r["size_total"]), r["size_total"], hist.get(r["pool_id"], []), today, first_level)
        rows.append({**t, "pool_id": r["pool_id"], "name": r["name"] or r["pool_id"]})
    rows.sort(key=sort_key)
    return rows


def disk_history(conn: sqlite3.Connection, group_path: str, today: date, exclude_batch: str | None = None) -> dict[tuple, list[dict]]:
    """上次輪循群組裡每顆碟的每日使用率：{(device_id, item): [{day, pct, total=None}]}。
    每天只取「這個群組有硬碟值的最後一個完整批次」（收集時間、再 batch_id 排序，同時間也穩定），不是每批都讀；
    today＝目前採用那一批的收集日，目前這一批與當天以後都排除。保留期受 wug_lastpoll.VALUE_KEEP_DAYS（約 30 天）限制。"""
    if not (_has(conn, "wug_lp_batch") and _has(conn, "wug_lp_value")):
        return {}
    import wug_lastpoll as lp
    edge = (today - timedelta(days=min(TREND_KEEP_DAYS, lp.VALUE_KEEP_DAYS))).isoformat()
    last: dict[str, str] = {}
    for r in conn.execute("SELECT b.batch_id, b.collected_at FROM wug_lp_batch b WHERE b.complete = 1 AND b.collected_at >= ? AND b.collected_at < ? "
                          "AND EXISTS (SELECT 1 FROM wug_lp_value v WHERE v.batch_id = b.batch_id AND v.group_path = ? AND v.metric = 'disk') "
                          "ORDER BY b.collected_at, b.batch_id", (edge, today.isoformat(), group_path)):
        if exclude_batch is not None and r["batch_id"] == exclude_batch:
            continue
        last[str(r["collected_at"])[:10]] = r["batch_id"]
    out: dict[tuple, list[dict]] = {}
    for day in sorted(last):
        for r in conn.execute("SELECT device_id, item, pct FROM wug_lp_value WHERE batch_id = ? AND group_path = ? AND metric = 'disk'", (last[day], group_path)):
            out.setdefault((r["device_id"], r["item"]), []).append({"day": day, "pct": r["pct"], "total": None})
    return out


# ---------------------------------------------------------------- 檔案系統（Unity 腳本 v3；storage_fs／storage_fs_daily）
def fs_history(conn: sqlite3.Connection, serial: str, today: date, exclude_batch: str | None = None) -> dict[str, list[dict]]:
    """這台 Unity 每個檔案系統的每日使用率（storage_fs_daily）。{fs_id: [{day, pct, total}]}。表不存在＝空。
    規則同 pool_history：today＝目前採用那一批的收集日，歷史排除目前這一批與當天以後。"""
    if not _has(conn, "storage_fs_daily"):
        return {}
    edge = (today - timedelta(days=TREND_KEEP_DAYS)).isoformat()
    out: dict[str, list[dict]] = {}
    for r in conn.execute("SELECT fs_id, day, size_used, size_total, batch_id FROM storage_fs_daily WHERE serial = ? AND day >= ? AND day < ? ORDER BY day",
                          (serial, edge, today.isoformat())):
        if exclude_batch is not None and r["batch_id"] == exclude_batch:
            continue
        out.setdefault(r["fs_id"], []).append({"day": r["day"], "pct": _pool_pct(r["size_used"], r["size_total"]), "total": r["size_total"]})
    return out


def filesystems_for(conn: sqlite3.Connection, serial: str, today: date, first_level: float, batch: str | None = None) -> dict:
    """檔案系統現況（storage_fs，最新一批）＋趨勢，用跟 Pool 同一把尺 analyze。
    回 {status（read｜failed｜not_collected）, msg, rows}。表不存在或這台沒有狀態列＝not_collected（舊資料／舊腳本，不是「0 個」）。
    status＝read 但清單筆數跟收集時記的個數對不上＝failed（只收到一半，不判正常）。使用量讀不到的檔案系統也列（pct=None＝未收集，不是正常）。"""
    miss = {"status": "not_collected", "msg": "沒有檔案系統資料（收集腳本要更新到 v3）", "rows": []}
    if not (_has(conn, "storage_fs_status") and _has(conn, "storage_fs")):
        return miss
    st = conn.execute("SELECT status, msg, n_total FROM storage_fs_status WHERE serial = ?", (serial,)).fetchone()
    if st is None:
        return miss
    if st["status"] != "read":
        return {"status": st["status"], "msg": st["msg"] or "", "rows": []}
    hist = fs_history(conn, serial, today, batch)
    rows = []
    for r in conn.execute("SELECT fs_id, name, size_used, size_total FROM storage_fs WHERE serial = ? ORDER BY name, fs_id", (serial,)):
        t = analyze(_pool_pct(r["size_used"], r["size_total"]), r["size_total"], hist.get(r["fs_id"], []), today, first_level)
        rows.append({**t, "fs_id": r["fs_id"], "name": r["name"] or r["fs_id"]})
    if st["n_total"] is not None and st["n_total"] != len(rows):
        return {"status": "failed", "msg": f"只收到一半的檔案系統（收集時記 {st['n_total']} 個、現在只有 {len(rows)} 個）", "rows": []}
    rows.sort(key=sort_key)
    return {"status": "read", "msg": "", "rows": rows}
