"""夜間速查「跟平常比」（N1，2026-11-15 夜間衝刺；定案 docs/codex-review/2026-11-15/05 v2 第 6 點）。

只讀既有資料、不連主機、不建表：

* 同時段＝現在 ±2 小時（台北時間）；看最近 7 天（不含今天這一段），**至少 4 天有樣本**才算有基準，不足＝「基準不足」。
* 多數決：出現天數 > 樣本天數的一半＝「平常是這樣」；剛好一半＝不確定，不標紅。
* 維護時段（4.1 告警事件標「維護中」、例行時段 routine_window）裡的樣本不算。
* 查詢失敗（資料壞掉、讀不到）≠ 沒有：分開放在 query_failed，不當成「沒樣本」也不當成「正常」。
* 磁碟差值＝現在 − 基準中位數（百分點）：漲 ≥ 10 黃、≥ 20 紅。
* 服務：只有「自動啟動」且平常在跑、現在停的才紅。即時檢測的 failed_units 在 Windows＝自動啟動沒在跑（可紅）；
  Linux 是 systemctl --failed，看不出 enabled 與否 → 最多黃。

資料來源（逐日歷史有多少就用多少）：
  · 磁碟：relay_disk_util（WhatsUp 每日磁碟序列，時間是 UTC）——有逐點時間，可以真的做 ±2 小時多數決。
  · 監聽埠：health_snapshot（每週六＋手動）與 night_check_snapshot（夜間緊急檢查存的快照）；
    另外 host_service（7.4 服務盤點）只記第一次看到／消失時間，用來推「本來在聽、最近不見了」（標推論、最多黃）。
  · 服務（自動啟動）：只有 night_check_snapshot 裡的即時檢測結果有 failed_units；**沒有逐日收集**，
    所以多數主機會是「基準不足」。要補的收集寫在回傳的 needs。
"""
from __future__ import annotations

import gzip
import json
import re
import sqlite3
import statistics
from datetime import datetime, timedelta

FMT = "%Y-%m-%d %H:%M:%S"
DAYS = 7
MIN_DAYS = 4
WINDOW_H = 2
DISK_AMBER = 10.0
DISK_RED = 20.0
NOW_DISK_MAX_AGE_H = 26          # 沒有即時結果時，WhatsUp 磁碟「現在值」最舊可以用到多久（每日批次）
PORT_RECENT_H = 24               # host_service：最近這麼久內消失／出現才算「現在變了」
NEEDS = {
    "service": "服務（自動啟動）沒有逐日收集：要在每日收集加唯讀的服務清單（Linux systemctl list-unit-files --state=enabled＋is-active；"
               "Windows Get-Service StartType=Automatic 與狀態），或每晚固定時段做一次即時檢測存快照，才做得出 7 天多數決",
    "port": "監聽埠只有每週六基準快照與夜間緊急檢查快照，7 天內湊不到 4 天；要每日收集監聽埠（服務盤點每天跑一次並保留逐日紀錄）",
    "disk": "這台在 WhatsUp 磁碟中繼沒有最近 7 天的資料（不在 WhatsUp 磁碟報表範圍，或中繼沒送來）",
}


def _has(conn, t: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() is not None


def _p(s) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(str(s).strip()[:19].replace("T", " "), FMT)
    except ValueError:
        return None


def _f(d: datetime | None) -> str | None:
    return d.strftime(FMT) if d else None


def windows(now: datetime) -> list[tuple[str, datetime, datetime]]:
    """最近 7 天、每天同一時段的 [起, 迄]（台北時間）。第 0 個＝昨天這時候。"""
    out = []
    for d in range(1, DAYS + 1):
        c = now - timedelta(days=d)
        out.append((c.date().isoformat(), c - timedelta(hours=WINDOW_H), c + timedelta(hours=WINDOW_H)))
    return out


def _day_of(wins, t: datetime) -> str | None:
    for day, a, b in wins:
        if a <= t <= b:
            return day
    return None


class _Maint:
    """這台的維護時段：4.1 告警事件標維護中的期間＋例行時段（每天固定的 HH:MM～HH:MM）。"""

    def __init__(self, conn, mk: str, ips: list[str], now: datetime):
        self.spans: list[tuple[datetime, datetime]] = []
        self.routine: list[dict] = []
        self._in = lambda *_a: False
        if _has(conn, "alert_event"):
            q = "SELECT started_at, ended_at, last_seen FROM alert_event WHERE tier = 'maint' AND (machine_key = ?"
            a: list = [mk]
            if ips:
                q += " OR ip IN (%s)" % ",".join("?" * len(ips))
                a += ips
            for r in conn.execute(q + ")", a):
                s, e = _p(r["started_at"]), _p(r["ended_at"]) or _p(r["last_seen"]) or now
                if s:
                    self.spans.append((s, e))
        try:
            import alert_attention as aa
            self.routine = aa.active_map(conn, now.date().isoformat()).get(mk, [])
            self._in = aa.in_span
        except (ImportError, sqlite3.Error):     # 沒有例行時段表／模組：只用告警維護期間
            self.routine = []

    def hit(self, t: datetime) -> bool:
        if any(a <= t <= b for a, b in self.spans):
            return True
        return any(self._in(w["start_hhmm"], w["end_hhmm"], t) for w in self.routine)


def disk_norm(label: str | None) -> str:
    """WhatsUp 磁碟名稱（'C:\\ Label:Sys…'、'/var'）與即時檢測掛載點（'C:'、'/var'）對成同一把鍵。"""
    s = str(label or "").strip()
    m = re.match(r"^([A-Za-z]):", s)
    if m:
        return m.group(1).upper() + ":"
    m = re.search(r"(/[^\s]*)", s)
    if m:
        return m.group(1).rstrip("/") or "/"
    return s.lower()


def _vote(days_present: int, sample_days: int) -> str:
    """多數決：> 一半＝usual；< 一半＝unusual；剛好一半＝tie（不確定，不標紅）。"""
    if days_present * 2 > sample_days:
        return "usual"
    if days_present * 2 == sample_days:
        return "tie"
    return "unusual"


# ------------------------------------------------------------------ 磁碟
def _disk_component(conn, ips, wins, maint: _Maint, now: datetime, live: dict | None, failed: list) -> dict:
    comp = {"name": "disk", "label": "磁碟用量", "status": "none", "sample_days": 0, "changes": [], "same": [], "tie": []}
    if not _has(conn, "relay_disk_util") or not ips:
        comp["reason"] = NEEDS["disk"]
        return comp
    import daily_check
    import disk_alert
    try:
        pats = disk_alert.exclude_list(conn)
    except Exception:  # noqa: BLE001 - 排除清單讀不到：照全部磁碟算（只是可能多列一顆假磁碟）
        pats = []
    lo = (now - timedelta(days=DAYS + 1)).date().isoformat()
    per: dict[str, dict[str, list[float]]] = {}       # 磁碟鍵 → 日 → 值
    latest: dict[str, tuple[datetime, float, str]] = {}
    days_any: set[str] = set()
    ph = ",".join("?" * len(ips))
    for r in conn.execute(f"SELECT host, day, disk, data FROM relay_disk_util WHERE host IN ({ph}) AND day >= ?", (*ips, lo)):
        if daily_check._is_swap(r["disk"]) or disk_alert.excluded(r["disk"], pats):
            continue
        try:
            d = json.loads(gzip.decompress(r["data"]).decode())
            ts, avg = d.get("ts") or [], d.get("avg") or []
        except (OSError, ValueError, TypeError, AttributeError):
            failed.append(f"WhatsUp 磁碟 {r['host']} {r['disk']} {r['day']} 這一天的資料讀不出來")
            continue
        key = disk_norm(r["disk"])
        for t, v in zip(ts, avg):
            if not isinstance(v, (int, float)):
                continue
            try:
                lt = datetime.strptime(f"{r['day']} {t}"[:19], FMT) + timedelta(hours=8)   # 序列是 UTC
            except ValueError:
                continue
            if lt <= now and (key not in latest or lt > latest[key][0]):
                latest[key] = (lt, float(v), r["disk"])
            if maint.hit(lt):
                continue
            day = _day_of(wins, lt)
            if day:
                per.setdefault(key, {}).setdefault(day, []).append(float(v))
                days_any.add(day)
    live_disks = {}
    if live:
        import optical_media
        for x in live.get("disks") or []:
            if isinstance(x, dict) and x.get("mount") and isinstance(x.get("use_pct"), (int, float)) and not optical_media.is_optical(x["mount"]):
                live_disks[disk_norm(x["mount"])] = float(x["use_pct"])
    comp["sample_days"] = len(days_any)
    if not per:
        comp["reason"] = NEEDS["disk"]
        return comp
    ok_any = False
    for key in sorted(per):
        dv = per[key]
        if len(dv) < MIN_DAYS:
            comp["tie"].append({"what": f"{key} 基準不足（7 天同時段只有 {len(dv)} 天有資料）"})
            continue
        ok_any = True
        base = statistics.median(statistics.median(v) for v in dv.values())
        if key in live_disks:
            cur, since, src = live_disks[key], (live or {}).get("checked_at") or _f(now), "即時檢測"
        elif key in latest and now - latest[key][0] <= timedelta(hours=NOW_DISK_MAX_AGE_H):
            cur, since, src = latest[key][1], _f(latest[key][0]), "WhatsUp"
        else:
            comp["tie"].append({"what": f"{key} 沒有現在的值（WhatsUp 最近一點超過 {NOW_DISK_MAX_AGE_H} 小時、也沒有即時檢測）"})
            continue
        delta = round(cur - base, 1)
        txt = f"{key} {cur:g}%（平常 {base:.0f}%，{'+' if delta >= 0 else ''}{delta:g}）"
        if delta >= DISK_RED:
            comp["changes"].append({"level": "red", "what": f"硬碟漲很多：{txt}", "since": since, "source": src, "kind": "disk"})
        elif delta >= DISK_AMBER:
            comp["changes"].append({"level": "amber", "what": f"硬碟漲了：{txt}", "since": since, "source": src, "kind": "disk"})
        else:
            comp["same"].append(f"{key} {cur:g}%")
    comp["status"] = "ok" if ok_any else "insufficient"
    if not ok_any:
        comp["reason"] = f"磁碟：7 天同時段有資料的天數不到 {MIN_DAYS} 天"
    return comp


# ------------------------------------------------------------------ 監聽埠
def _snap_rows(conn, ips: list[str], mk: str, lo: str) -> list[dict]:
    """health_snapshot＋night_check_snapshot：[{at, ports(set)|None, failed(list)|None, platform, src}]。"""
    out = []
    if _has(conn, "health_snapshot") and ips:
        ph = ",".join("?" * len(ips))
        for r in conn.execute(f"SELECT taken_at, ports_json, reachable FROM health_snapshot WHERE ip IN ({ph}) AND taken_at >= ?", (*ips, lo)):
            try:
                ports = {int(x) for x in json.loads(r["ports_json"] or "[]")}
            except (ValueError, TypeError):
                ports = None
            if r["reachable"]:
                out.append({"at": _p(r["taken_at"]), "ports": ports, "failed": None, "platform": None, "src": "每週健康基準"})
    if _has(conn, "night_check_snapshot"):
        for r in conn.execute("SELECT at, result_json FROM night_check_snapshot WHERE machine_key IN (?, ?) AND at >= ?",
                                 (mk, "ret:" + mk, lo)):
            try:
                res = json.loads(r["result_json"] or "{}")
            except ValueError:
                res = None
            if not isinstance(res, dict) or not res.get("reachable"):
                continue
            ports = {int(p["port"]) for p in res.get("ports") or [] if isinstance(p, dict) and str(p.get("port", "")).isdigit()}
            out.append({"at": _p(r["at"]), "ports": ports if res.get("ports") is not None else None,
                        "failed": list(res.get("failed_units") or []) if res.get("services_checked", True) else None,
                        "platform": res.get("platform"), "src": "夜間緊急檢查"})
    return [x for x in out if x["at"]]


def _port_component(conn, ips, mk, wins, maint, now, live, snaps, failed) -> dict:
    comp = {"name": "port", "label": "監聽埠", "status": "none", "sample_days": 0, "changes": [], "same": [], "tie": []}
    days: dict[str, set[int]] = {}
    for s in snaps:
        if s["ports"] is None or maint.hit(s["at"]):
            continue
        d = _day_of(wins, s["at"])
        if d:
            days.setdefault(d, set()).update(s["ports"])
    comp["sample_days"] = len(days)
    cur = None
    if live and live.get("reachable") and live.get("ports") is not None:
        cur = {int(p["port"]) for p in live.get("ports") or [] if isinstance(p, dict) and str(p.get("port", "")).isdigit()}
    if len(days) >= MIN_DAYS and cur is not None:
        n = len(days)
        allp = set().union(*days.values()) | cur
        for port in sorted(allp):
            present = sum(1 for v in days.values() if port in v)
            vote = _vote(present, n)
            if vote == "tie":
                comp["tie"].append({"what": f"埠 {port}：{present}/{n} 天有聽，不確定"})
            elif vote == "usual" and port not in cur:
                comp["changes"].append({"level": "amber", "what": f"監聽埠不見了：{port}（{present}/{n} 天都在聽）",
                                        "since": live.get("checked_at"), "source": "即時檢測", "kind": "port"})
            elif vote == "unusual" and port in cur:
                comp["changes"].append({"level": "amber", "what": f"新出現的監聽埠：{port}（{n} 天裡只有 {present} 天有）",
                                        "since": live.get("checked_at"), "source": "即時檢測", "kind": "port"})
            elif port in cur:
                comp["same"].append(f"埠 {port}")
        comp["status"] = "ok"
    elif len(days) >= MIN_DAYS:
        comp["status"] = "insufficient"
        comp["reason"] = "監聽埠有基準，但還沒做即時檢測（沒有現在的值可以比）"
    else:
        comp["status"] = "insufficient" if days else "none"
        comp["reason"] = f"監聽埠：7 天同時段只有 {len(days)} 天有樣本（要 {MIN_DAYS} 天）。" + NEEDS["port"]
    # host_service（7.4 服務盤點）：只有第一次看到／消失時間——推「最近不見了／最近才出現」，標推論、最多黃
    if _has(conn, "host_service") and ips:
        ph = ",".join("?" * len(ips))
        try:
            rows = [dict(r) for r in conn.execute(f"SELECT port, proto, process, service_guess, first_seen, last_seen, gone_at "
                                                  f"FROM host_service WHERE ip IN ({ph})", ips)]
        except sqlite3.Error as exc:
            failed.append(f"服務盤點讀不到（{type(exc).__name__}）")
            rows = []
        edge = now - timedelta(hours=PORT_RECENT_H)
        oldest = min((_p(r["first_seen"]) for r in rows if _p(r["first_seen"])), default=None)
        mature = oldest is not None and now - oldest >= timedelta(days=MIN_DAYS)
        known = {c["what"].split("：")[1].split("（")[0] for c in comp["changes"] if "：" in c["what"]}
        for r in rows:
            nm = f"{r['port']}" + (f"（{r['process'] or r['service_guess']}）" if (r["process"] or r["service_guess"]) else "")
            fs, gone = _p(r["first_seen"]), _p(r["gone_at"])
            if str(r["port"]) in known:
                continue
            if gone and gone >= edge and fs and gone - fs >= timedelta(days=MIN_DAYS):
                comp["changes"].append({"level": "amber", "what": f"監聽埠不見了：{nm}（服務盤點：{r['first_seen'][:10]} 起一直在聽）",
                                        "since": r["gone_at"], "source": "服務盤點（推論）", "kind": "port"})
            elif fs and fs >= edge and not gone and mature:
                comp["changes"].append({"level": "amber", "what": f"新出現的監聽埠：{nm}（服務盤點第一次看到）",
                                        "since": r["first_seen"], "source": "服務盤點（推論）", "kind": "port"})
        if rows and comp["status"] == "none":
            comp["status"] = "insufficient"
    return comp


# ------------------------------------------------------------------ 服務（自動啟動）
def _service_component(wins, maint, live, snaps) -> dict:
    comp = {"name": "service", "label": "服務（自動啟動）", "status": "none", "sample_days": 0, "changes": [], "same": [], "tie": []}
    days: dict[str, set[str]] = {}
    for s in snaps:
        if s["failed"] is None or maint.hit(s["at"]):
            continue
        d = _day_of(wins, s["at"])
        if d:
            days.setdefault(d, set()).update(s["failed"])
    comp["sample_days"] = len(days)
    cur = None
    if live and live.get("reachable") and live.get("failed_units") is not None:
        cur = {str(x) for x in live.get("failed_units") or []}
    if len(days) < MIN_DAYS:
        comp["status"] = "insufficient" if days else "none"
        comp["reason"] = f"服務：7 天同時段只有 {len(days)} 天有樣本（要 {MIN_DAYS} 天）。" + NEEDS["service"]
        return comp
    if cur is None:
        comp["status"] = "insufficient"
        comp["reason"] = "服務有基準，但還沒做即時檢測（沒有現在的值可以比）"
        return comp
    n = len(days)
    win = str((live or {}).get("platform") or "").lower().startswith("win")
    for u in sorted(cur | set().union(*days.values())):
        stopped_days = sum(1 for v in days.values() if u in v)
        running_vote = _vote(n - stopped_days, n)         # 平常在跑＝沒出現在停止清單的天數過半
        if running_vote == "tie":
            comp["tie"].append({"what": f"服務 {u}：{n - stopped_days}/{n} 天在跑，不確定"})
        elif running_vote == "usual" and u in cur:
            comp["changes"].append({"level": "red" if win else "amber",
                                    "what": f"服務停了：{u}（{'自動啟動、' if win else ''}{n - stopped_days}/{n} 天都在跑）"
                                            + ("" if win else "（Linux 看不出是否開機自動啟動，不標紅）"),
                                    "since": live.get("checked_at"), "source": "即時檢測", "kind": "service"})
        elif running_vote == "unusual" and u not in cur:
            comp["changes"].append({"level": "amber", "what": f"本來停的服務現在在跑：{u}（{n} 天裡有 {stopped_days} 天是停的）",
                                    "since": live.get("checked_at"), "source": "即時檢測", "kind": "service"})
    comp["status"] = "ok"
    return comp


# ------------------------------------------------------------------ 對外
def diff(conn: sqlite3.Connection, machine_key: str, now: datetime | None = None, live: dict | None = None) -> dict:
    """這台跟「平常」比。live＝即時檢測結果（health-check 單台 item）；沒有就只用既有資料的「現在值」。
    回 {status: ok|insufficient|none|error, days, reason, changes[], same_count, same_brief, components[], query_failed[], needs[]}。"""
    import host_ops
    now = now or datetime.now()
    out = {"status": "none", "days": DAYS, "window_h": WINDOW_H, "min_days": MIN_DAYS, "reason": None, "changes": [],
           "same_count": 0, "same_brief": "", "components": [], "query_failed": [], "needs": [], "at": _f(now)}
    try:
        info = host_ops.machine_info(conn, machine_key)
    except LookupError as exc:
        out["reason"] = str(exc)
        return out
    try:
        mk = info["key"][4:] if info["key"].startswith("ret:") else info["key"]
        wins = windows(now)
        maint = _Maint(conn, mk, info["ips"], now)
        failed: list[str] = []
        lo = _f(now - timedelta(days=DAYS + 1))
        snaps = _snap_rows(conn, info["ips"], mk, lo)
        comps = [_disk_component(conn, info["ips"], wins, maint, now, live, failed),
                 _port_component(conn, info["ips"], mk, wins, maint, now, live, snaps, failed),
                 _service_component(wins, maint, live, snaps)]
    except sqlite3.Error as exc:
        out.update(status="error", reason=f"讀基準資料失敗：{type(exc).__name__}: {exc}")
        return out
    out["components"] = [{k: c.get(k) for k in ("name", "label", "status", "sample_days", "reason", "tie")} for c in comps]
    out["query_failed"] = failed
    out["needs"] = [NEEDS[c["name"]] for c in comps if c["status"] != "ok" and c["name"] in ("service", "port")]
    changes = [x for c in comps for x in c["changes"]]
    changes.sort(key=lambda x: (0 if x["level"] == "red" else 1, x["what"]))
    out["changes"] = changes
    same = [s for c in comps for s in c["same"]]
    out["same_count"] = len(same)
    out["same_brief"] = "、".join(same[:3]) + ("…" if len(same) > 3 else "")
    if any(c["status"] == "ok" for c in comps):
        out["status"] = "ok"
        miss = [c["label"] for c in comps if c["status"] != "ok"]
        out["reason"] = ("沒有基準的部分：" + "、".join(miss)) if miss else None
    elif any(c["status"] == "insufficient" for c in comps) or changes:
        out["status"] = "insufficient"
        out["reason"] = "基準不足：" + "；".join(c["reason"] for c in comps if c.get("reason"))
    else:
        out["reason"] = "沒有基準：最近 7 天沒有這台的磁碟、監聽埠、服務樣本"
    if failed and out["status"] != "ok":
        out["reason"] = (out["reason"] or "") + f"（另有 {len(failed)} 筆資料讀不出來，見 query_failed）"
    return out
