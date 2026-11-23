"""掃描診斷（唯讀）：網段掃描「為什麼沒掃出東西」，值班人員在畫面上直接看，不用上機器查（2026-11-22）。

全部由本機資料庫算，**不連任何主機、不寫任何資料**。資料庫查不到的事（掃描服務有沒有在跑、防火牆有沒有放行探測埠）
一律列在 `unknowns`，寫「查不到」，不猜。

## 判定規則（每一條都附證據數字；門檻放在下面常數，測試釘住）

| 代碼 | 判準 | 嚴重度 |
|---|---|---|
| scan_off | 掃描總開關 scan_enabled＝0 | 失敗 |
| no_segments | connections 裡沒有任何「網段型」來源（沒設網段；掃描器會退而只掃本機所在網段） | 失敗 |
| all_disabled | 有網段型來源，但全部 enabled＝0 | 失敗 |
| never_scanned | scan_history 與 scan_coverage 都沒有任何紀錄 | 沒收到 |
| last_batch_zero | 最近一批（最新 scan_time）涵蓋 ≥1 個網段，但成功列（scan_ok=1）0 筆 | 失敗 |
| fail_ratio_high | 最近一批涵蓋網段中失敗（coverage.ok=0 或有 scan_ok=0 的列）占比 ≥ FAIL_RATIO | 失敗 |
| segment_failed | 最近一批某網段失敗（占比未達門檻時逐段列） | 只收到一半 |
| segment_zero_streak | 某啟用網段最近連續 ≥ ZERO_STREAK 批成功掃完卻 0 台 | 只收到一半 |
| stale | 最近一次掃描距今 > STALE_DAYS 天 | 過期 |
| schedule_overdue | 排程時間已過 SCHEDULE_GRACE_MIN 分鐘仍沒有「排程」觸發的掃描 | 過期 |
| quiet_deferred | quiet_window_pending 有 scan_schedule 待補跑（盤中靜默窗延後） | 過期 |
| run_failed | 最近一次 scan_runs 狀態 failed | 失敗 |

排程模式 weekly／daily／interval 的「應該在什麼時候跑過」用同一套算法；全部啟用網段都設了自己的排程時間時，
全域排程本來就不會觸發，不做 schedule_overdue 判定（寫在證據裡）。
"""
from __future__ import annotations

import inspect
import sqlite3
from datetime import datetime, timedelta

#: 最近一批失敗網段占比 ≥ 此值 → 整體掃描器異常（對應「掃描器逾時比例過高」）
FAIL_RATIO = 0.5
#: 單一網段連續幾批成功掃完卻 0 台才報
ZERO_STREAK = 3
#: 最近一次掃描距今超過幾天算太久沒掃
STALE_DAYS = 2
#: 排程時間過了幾分鐘仍沒觸發才算沒跑（排程器每分鐘檢查一次，給一點緩衝）
SCHEDULE_GRACE_MIN = 30
#: 每個網段往回看幾批
HISTORY_BATCHES = 10
#: 單網段「只收到一半」：最近一批比前一批少這個比例以上（前一批至少 MIN_PREV 台才比）
DROP_RATIO = 0.5
MIN_PREV = 4

UNKNOWNS = [
    "掃描服務（排程執行緒）此刻有沒有在運作：資料庫查不到，需到主機看服務狀態",
    "防火牆是否放行探測埠、掃描機能不能連到目標網段：資料庫查不到，需從掃描機實測",
]


def _parse(s) -> datetime | None:
    if not s:
        return None
    t = str(s).strip().replace("T", " ")[:19]
    try:
        return datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            return datetime.strptime(t[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            return None


def _now() -> datetime:
    import db

    return _parse(db._now_local()) or datetime.now()


def _fmt(d: datetime | None) -> str | None:
    return d.strftime("%Y-%m-%d %H:%M") if d else None


def _age(d: datetime | None, now: datetime) -> dict:
    if not d:
        return {"hours": None, "text": "查不到"}
    h = max(0.0, (now - d).total_seconds() / 3600)
    text = f"{int(h * 60)} 分鐘前" if h < 1 else f"{h:.1f} 小時前" if h < 48 else f"{h / 24:.1f} 天前"
    return {"hours": round(h, 1), "text": text}


def probe_settings() -> dict:
    """探測埠與逾時：取自 net_scan 的常數與預設參數，不是寫死文字。"""
    import net_scan

    timeout = inspect.signature(net_scan.NetworkSweepSource.__init__).parameters["timeout"].default
    icmp = inspect.signature(net_scan._probe_host).parameters["icmp"].default
    return {"ports": list(net_scan.PROBE_PORTS), "timeout_seconds": timeout,
            "icmp_fallback": bool(icmp), "arp_fallback": True}


class DiagnosisUnavailable(Exception):
    """診斷本身算不出來（資料庫鎖住、損壞、核心表缺失…）：回結構化的 diagnosis_unavailable，不 500、也不假裝診斷正常。"""


def _missing_schema(exc: Exception) -> bool:
    """只有「沒這張表／沒這個欄位」才算舊庫相容；鎖、損壞、磁碟錯誤、SQL 語法都不是。"""
    msg = str(exc).lower()
    return "no such table" in msg or "no such column" in msg


def _q(conn, sql, params=()):
    """查不到（舊庫沒表／沒欄位）回 None，不丟例外；診斷頁不能因為少一張表就整頁 500。
    其他 OperationalError（鎖、損壞、磁碟錯誤）不是缺表——丟 DiagnosisUnavailable，由 diagnose 回「診斷不可用」（2.070.1）。"""
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        if _missing_schema(exc):
            return None
        raise DiagnosisUnavailable(f"{type(exc).__name__}: {exc}") from exc


def _seg_key(x) -> str:
    import scan_scope
    t = str(x or "").strip()
    return scan_scope.norm_cidr(t) or t


def _scan_connections(conn) -> list[dict]:
    """跟 run_real_scan.scan_targets 同一套判斷：解得開的 CIDR 或類型含「網路掃描」才是網段來源。"""
    from run_real_scan import _is_cidr
    import scan_scope

    out = []
    for c in conn.execute("SELECT * FROM connections ORDER BY name").fetchall():
        target = (c["target"] or "").strip()
        ctype = c["connection_type"] or ""
        if not (_is_cidr(target) or "網路掃描" in ctype):
            continue
        try:
            enabled = c["enabled"] is None or bool(c["enabled"])
        except (IndexError, KeyError):
            enabled = True
        out.append({"id": c["id"], "name": c["name"], "target": target, "norm": scan_scope.norm_cidr(target) or target,
                    "enabled": enabled, "scan_time": (c["scan_time"] or "").strip() or None,
                    "last_scan_at": c["last_scan_at"], "last_status": c["last_status"]})
    return out


def _segment_batches(conn, raw: str, norm: str) -> list[dict]:
    """這個網段最近 HISTORY_BATCHES 批：{time, failed, count}，新到舊。
    批次清單以 scan_coverage 為準（涵蓋紀錄才分得出「掃了 0 台」與「沒掃」）；沒有涵蓋紀錄才退回 scan_history。"""
    keys = (raw, norm) if raw != norm else (raw,)
    ph = ",".join("?" for _ in keys)
    cov = _q(conn, f"SELECT scan_time, MIN(ok) ok FROM scan_coverage WHERE cidr IN ({ph}) "
                   f"GROUP BY scan_time ORDER BY scan_time DESC LIMIT ?", (*keys, HISTORY_BATCHES))
    times: list[tuple[str, bool]] = []
    if cov:
        times = [(r["scan_time"], r["ok"] == 0) for r in cov]
    else:
        hist = _q(conn, f"SELECT scan_time, MIN(scan_ok) ok FROM scan_history WHERE segment IN ({ph}) "
                        f"GROUP BY scan_time ORDER BY scan_time DESC LIMIT ?", (*keys, HISTORY_BATCHES)) or []
        times = [(r["scan_time"], r["ok"] == 0) for r in hist]
    out = []
    for t, cov_failed in times:
        rows = _q(conn, f"SELECT scan_ok, COUNT(*) n FROM scan_history WHERE scan_time = ? AND segment IN ({ph}) "
                        f"GROUP BY scan_ok", (t, *keys)) or []
        n_ok = sum(r["n"] for r in rows if r["scan_ok"] == 1)
        n_bad = sum(r["n"] for r in rows if r["scan_ok"] == 0)
        out.append({"time": t, "failed": bool(cov_failed or n_bad), "count": n_ok})
    return out


def _latest_batch_time(conn) -> str | None:
    best = None
    for sql in ("SELECT MAX(scan_time) t FROM scan_history", "SELECT MAX(scan_time) t FROM scan_coverage"):
        r = _q(conn, sql)
        if r and r[0]["t"] and (best is None or str(r[0]["t"]) > best):
            best = str(r[0]["t"])
    return best


def _expected_schedule_time(sched: dict, now: datetime) -> datetime | None:
    """排程「最近一次應該已經觸發」的時間點。interval 模式沒有固定時刻，回 None（另用間隔判斷）。"""
    try:
        hh, mm = str(sched["time"]).split(":")
        base = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    except (ValueError, KeyError, AttributeError):
        return None
    if sched["mode"] == "daily":
        return base if now >= base else base - timedelta(days=1)
    if sched["mode"] == "weekly":
        back = (now.weekday() - int(sched["weekday"])) % 7
        t = base - timedelta(days=back)
        return t if t <= now else t - timedelta(days=7)
    return None


def diagnose(conn: sqlite3.Connection, now: datetime | None = None) -> dict:
    """診斷入口。核心表缺失、資料庫鎖住／損壞時回結構化的 diagnosis_unavailable（不 500、不假裝正常）。"""
    now = now or _now()
    try:
        return _diagnose(conn, now)
    except DiagnosisUnavailable as exc:
        return _unavailable(now, str(exc))
    except sqlite3.OperationalError as exc:        # 核心查詢（connections、scan_runs、設定表）缺表／缺欄，或鎖、損壞
        why = "核心資料表或欄位不存在（升級中或資料庫不完整）" if _missing_schema(exc) else "資料庫讀取失敗"
        return _unavailable(now, f"{why}：{type(exc).__name__}: {exc}")


def _unavailable(now: datetime, detail: str) -> dict:
    """診斷算不出來：形狀跟正常結果一致（畫面不會因缺鍵而炸），狀態＝失敗、原因碼 diagnosis_unavailable。"""
    msg = "診斷無法執行（讀不到掃描相關資料）：這不代表掃描正常，也不代表掃描異常"
    return {
        "status": "失敗", "diagnosis_unavailable": True, "headline": msg, "now": now.strftime("%Y-%m-%d %H:%M:%S"),
        "causes": [{"code": "diagnosis_unavailable", "level": "失敗", "title": "診斷無法執行", "evidence": detail,
                    "fix": "確認資料庫可讀、系統已升級完成後重新整理", "link": "/v2/settings?tab=identity&sub=schedule"}],
        "schedule": {"enabled": None, "mode": None, "time": None, "interval_hours": None, "weekday_label": None,
                     "last_schedule_run": None, "last_schedule_age": "查不到", "all_segments_custom": False,
                     "latest_run": None, "recent_runs": []},
        "quiet_window": None,
        "latest_batch": {"time": None, "age": "查不到", "age_hours": None, "found": None, "segments_covered": None, "segments_failed": None},
        "segments": [],
        "summary": {"segments": 0, "enabled": 0, "disabled": 0, "zero_now": 0, "failed_now": 0, "causes": 1},
        "probe": probe_settings(),
        "thresholds": {"fail_ratio": FAIL_RATIO, "zero_streak": ZERO_STREAK, "stale_days": STALE_DAYS,
                       "schedule_grace_min": SCHEDULE_GRACE_MIN},
        "unknowns": UNKNOWNS,
    }


def _diagnose(conn: sqlite3.Connection, now: datetime) -> dict:
    import scan_service
    from db import get_latest_scan_run

    causes: list[dict] = []

    def cause(code, level, title, evidence, **extra):
        causes.append({"code": code, "level": level, "title": title, "evidence": evidence, **extra})

    # ---- 排程 ----
    sched = scan_service.get_schedule(conn)
    last_sched = scan_service.get_last_schedule_run_time(conn)
    run = get_latest_scan_run(conn)
    runs = _q(conn, "SELECT id, trigger, status, found_count, error, started_at, finished_at "
                    "FROM scan_runs ORDER BY id DESC LIMIT 5")
    recent_runs = [dict(r) for r in (runs or [])]

    # ---- 網段 ----
    conns = _scan_connections(conn)
    enabled_conns = [c for c in conns if c["enabled"]]
    seg_rows: list[dict] = []
    last_failed_segs: list[dict] = []
    latest_time = _latest_batch_time(conn)
    for c in conns:
        batches = _segment_batches(conn, c["target"], c["norm"])
        last = batches[0] if batches else None
        prev = next((b for b in batches[1:] if not b["failed"]), None)
        streak = 0
        for b in batches:
            if b["failed"] or b["count"] != 0:
                break
            streak += 1
        delta = (last["count"] - prev["count"]) if (last and not last["failed"] and prev) else None
        age = _age(_parse(last["time"]) if last else None, now)
        if not c["enabled"]:
            status = "待設定"
        elif not last:
            status = "沒收到"
        elif last["failed"]:
            status = "失敗"
        elif last["count"] == 0:
            status = "沒收到"
        elif age["hours"] is not None and age["hours"] > STALE_DAYS * 24:
            status = "過期"
        elif prev and prev["count"] >= MIN_PREV and last["count"] <= prev["count"] * (1 - DROP_RATIO):
            status = "只收到一半"
        else:
            status = "正常"
        row = {"id": c["id"], "name": c["name"], "target": c["target"], "enabled": c["enabled"],
               "enabled_text": "啟用" if c["enabled"] else "停用", "own_schedule": c["scan_time"],
               "last_scan_at": c["last_scan_at"], "last_time": last["time"] if last else None,
               "last_age": age["text"], "covered_ok": bool(last and not last["failed"]),
               "covered_text": "沒有涵蓋紀錄" if not last else "失敗" if last["failed"] else "成功涵蓋",
               "last_count": None if (not last or last["failed"]) else last["count"],
               "prev_count": prev["count"] if prev else None, "delta": delta,
               "zero_streak": streak, "batches": len(batches), "status": status,
               "is_last_batch": bool(last and latest_time and last["time"] == latest_time)}
        seg_rows.append(row)
        if c["enabled"] and last and last["failed"] and row["is_last_batch"]:
            last_failed_segs.append(row)

    # ---- 最近一批整體 ----
    batch_total = batch_cov = batch_failed = None
    source_mismatch = None
    if latest_time:
        r = _q(conn, "SELECT COUNT(*) n FROM scan_history WHERE scan_time = ? AND scan_ok = 1", (latest_time,))
        batch_total = r[0]["n"] if r else None
        cov_rows = _q(conn, "SELECT cidr, ok FROM scan_coverage WHERE scan_time = ?", (latest_time,)) or []
        hist_fail_rows = _q(conn, "SELECT DISTINCT segment FROM scan_history WHERE scan_time = ? AND scan_ok = 0", (latest_time,))
        hist_fail = {_seg_key(r["segment"]) for r in (hist_fail_rows or []) if r["segment"]}
        if cov_rows:
            cov_all = {_seg_key(r["cidr"]) for r in cov_rows}
            cov_fail = {_seg_key(r["cidr"]) for r in cov_rows if r["ok"] == 0}
            # 失敗＝涵蓋紀錄 ok=0 或掃描列 scan_ok=0 任一邊說失敗（2.070.1：只看 coverage 會被「誤標成功」騙過）
            failed_set = cov_fail | hist_fail
            batch_cov, batch_failed = len(cov_all | failed_set), len(failed_set)
            if cov_fail != hist_fail:
                source_mismatch = {"cov_only": sorted(cov_fail - hist_fail), "hist_only": sorted(hist_fail - cov_fail)}
        else:                                    # 沒有涵蓋紀錄：只剩失敗列可看
            batch_failed = len(hist_fail) if hist_fail_rows is not None else None
    latest_dt = _parse(latest_time)
    latest_age = _age(latest_dt, now)

    # ---- 原因判定 ----
    if not sched["enabled"]:
        cause("scan_off", "失敗", "掃描總開關是關的", "系統設定 scan_enabled＝0，排程不會觸發任何掃描", fix="到系統設定開啟自動掃描排程",
              link="/v2/settings?tab=identity&sub=schedule")
    if not conns:
        cause("no_segments", "失敗", "沒有設定任何要掃的網段",
              "connections 裡沒有網段型來源；掃描器此時只會掃本機所在網段（fallback），不會掃正式網段",
              fix="到掃描範圍加入要掃的網段", link="/v2/settings?tab=identity&sub=schedule")
    elif not enabled_conns:
        cause("all_disabled", "失敗", "所有網段都被停用",
              f"{len(conns)} 個網段來源，enabled 都是 0；排程跑了也不會掃任何網段",
              fix="到掃描範圍啟用要掃的網段", link="/v2/settings?tab=identity&sub=schedule")
    if not latest_time:
        cause("never_scanned", "沒收到", "查不到任何掃描紀錄",
              "scan_history 與 scan_coverage 都是空的：從來沒有掃描跑完過（或資料庫被清過）", fix="確認排程已開並手動重掃一次",
              link="/v2/settings?tab=identity&sub=schedule")
    else:
        if batch_cov and batch_total == 0:
            cause("last_batch_zero", "失敗", "最近一次整批掃到 0 台",
                  f"{latest_time[:16]} 那一批涵蓋 {batch_cov} 個網段，成功列 0 筆（其中 {batch_failed or 0} 段失敗）；"
                  "整批 0 台通常是掃描機連不出去，不是網段全空", fix="從掃描機實測探測埠是否放行", link="/v2/onboard?tab=gaps")
        if batch_cov and batch_failed is not None:
            ratio = batch_failed / batch_cov
            if ratio >= FAIL_RATIO:
                cause("fail_ratio_high", "失敗", "掃描失敗／逾時的網段占比過高",
                      f"最近一批 {batch_failed}/{batch_cov} 個網段失敗（{ratio:.0%}，門檻 {FAIL_RATIO:.0%}）；"
                      "失敗＝掃描逾時、格式錯誤或盤中靜默中斷（scan_ok＝0）", fix="看失敗網段的原因，確認掃描機連線",
                      link="/v2/onboard?tab=gaps", numbers={"failed": batch_failed, "covered": batch_cov})
            else:
                for s in last_failed_segs:
                    cause("segment_failed", "只收到一半", f"網段 {s['target']} 最近一次掃描失敗",
                          f"{s['last_time'][:16]} 該網段失敗（scan_ok＝0），整批失敗占比 {ratio:.0%} 未達門檻", segment=s["target"],
                          fix="看這個網段能不能從掃描機連到", link="/v2/onboard?tab=gaps")
        if latest_age["hours"] is not None and latest_age["hours"] > STALE_DAYS * 24:
            cause("stale", "過期", "太久沒有掃描",
                  f"最近一次掃描 {latest_time[:16]}（{latest_age['text']}），超過 {STALE_DAYS} 天", fix="確認排程與服務是否正常",
                  link="/v2/settings?tab=identity&sub=schedule", numbers={"hours": latest_age["hours"]})
    for s in seg_rows:
        if s["enabled"] and s["zero_streak"] >= ZERO_STREAK:
            cause("segment_zero_streak", "只收到一半", f"網段 {s['target']} 連續 {s['zero_streak']} 次掃到 0 台",
                  f"最近 {s['zero_streak']} 批該網段都成功掃完但 0 台（門檻 {ZERO_STREAK} 批）；可能整段下線、或掃描機到該段不通",
                  segment=s["target"], fix="確認該網段是否還在用；不用掃就停用", link="/v2/onboard?tab=gaps",
                  numbers={"streak": s["zero_streak"]})
    if source_mismatch:
        parts = []
        if source_mismatch["hist_only"]:
            parts.append("掃描列記失敗但涵蓋紀錄記成功的網段：" + "、".join(source_mismatch["hist_only"][:5]))
        if source_mismatch["cov_only"]:
            parts.append("涵蓋紀錄記失敗但沒有失敗掃描列的網段：" + "、".join(source_mismatch["cov_only"][:5]))
        cause("source_mismatch", "只收到一半", "資料來源不一致（涵蓋紀錄與掃描列對不上）",
              f"{str(latest_time)[:16]} 那一批：" + "；".join(parts) + "；失敗數已兩邊合併計算，但來源本身需要查",
              fix="看掃描寫入流程是否中途中斷", link="/v2/onboard?tab=gaps", numbers={"failed": batch_failed, "covered": batch_cov})
    if run and run["status"] == "failed":
        cause("run_failed", "失敗", "最近一次掃描記為失敗",
              f"{(run['started_at'] or '')[:16]} 的 {run['trigger']} 掃描 failed：{run['error'] or '（沒有錯誤訊息）'}",
              fix="依錯誤訊息處理", link="/v2/settings?tab=identity&sub=schedule")

    # 排程是否該跑而沒跑（全部啟用網段都有自己的排程時間時，全域排程本來就不觸發）
    all_custom = bool(enabled_conns) and all(c["scan_time"] for c in enabled_conns)
    overdue_note = None
    if sched["enabled"] and not all_custom:
        if sched["mode"] == "interval":
            limit = timedelta(hours=sched["interval_hours"] * 1.5)
            if last_sched is None or now - last_sched > limit:
                overdue_note = f"每 {sched['interval_hours']} 小時的排程，最近一次排程觸發 {_fmt(last_sched) or '查不到（沒有排程紀錄）'}"
        else:
            exp = _expected_schedule_time(sched, now)
            if exp and now - exp > timedelta(minutes=SCHEDULE_GRACE_MIN) and (last_sched is None or last_sched < exp):
                overdue_note = f"應該在 {_fmt(exp)} 觸發，最近一次排程觸發是 {_fmt(last_sched) or '查不到（沒有排程紀錄）'}"
        if overdue_note:
            cause("schedule_overdue", "過期", "排程時間已過但沒有跑", overdue_note, fix="確認排程服務是否在運作；也可手動重掃",
                  link="/v2/settings?tab=identity&sub=schedule")

    quiet = None
    try:
        import quiet_window as qw

        pend = [p for p in qw.pending_list(conn) if p["entry"] == "scan_schedule"]
        in_window = qw.in_quiet_window(conn, now)
        quiet = {"in_window_now": bool(in_window), "pending": pend[0] if pend else None}
        if pend:
            cause("quiet_deferred", "過期", "排程掃描被盤中靜默窗延後",
                  f"補跑待辦狀態 {pend[0]['state']}（重試 {pend[0]['attempts']} 次，更新 {str(pend[0]['updated_at'])[:16]}）：{pend[0]['detail'] or '—'}；"
                  "窗外會自動補跑一次", fix="等窗外補跑；急的話到掃描排程頁手動重掃", link="/v2/settings?tab=identity&sub=schedule")
    except Exception as exc:  # noqa: BLE001 - 靜默窗資訊查不到不擋診斷，如實寫「查不到」
        quiet = {"in_window_now": None, "pending": None, "error": f"查不到：{exc}"}

    # ---- 整體狀態（六種固定狀態詞，由重到輕）----
    levels = {c["level"] for c in causes}
    if "scan_off" in {c["code"] for c in causes} or {"no_segments", "all_disabled"} & {c["code"] for c in causes}:
        status = "待設定"
    elif "never_scanned" in {c["code"] for c in causes}:
        status = "沒收到"
    elif "失敗" in levels:
        status = "失敗"
    elif "過期" in levels:
        status = "過期"
    elif "只收到一半" in levels:
        status = "只收到一半"
    else:
        status = "正常"
    headline = {
        "正常": "掃描正常，沒有發現會讓掃不出東西的原因" + (f"（最近一批 {batch_total} 台）" if batch_total is not None else ""),
        "待設定": "掃描還沒設定好：" + "；".join(c["title"] for c in causes if c["code"] in ("scan_off", "no_segments", "all_disabled")),
        "沒收到": "查不到任何掃描紀錄，從來沒有掃描結果進來",
    }.get(status, f"發現 {len(causes)} 個可能原因：" + "；".join(c["title"] for c in causes[:3]) + ("…" if len(causes) > 3 else ""))

    return {
        "status": status, "headline": headline, "now": now.strftime("%Y-%m-%d %H:%M:%S"),
        "causes": causes,
        "schedule": {"enabled": sched["enabled"], "mode": sched["mode"], "time": sched["time"], "interval_hours": sched["interval_hours"],
                     "weekday_label": sched["weekday_label"], "last_schedule_run": _fmt(last_sched),
                     "last_schedule_age": _age(last_sched, now)["text"], "all_segments_custom": all_custom,
                     "latest_run": dict(run) if run else None, "recent_runs": recent_runs},
        "quiet_window": quiet,
        "latest_batch": {"time": latest_time, "age": latest_age["text"], "age_hours": latest_age["hours"], "found": batch_total,
                         "segments_covered": batch_cov, "segments_failed": batch_failed},
        "segments": seg_rows,
        "summary": {"segments": len(conns), "enabled": len(enabled_conns), "disabled": len(conns) - len(enabled_conns),
                    "zero_now": sum(1 for s in seg_rows if s["enabled"] and s["last_count"] == 0),
                    "failed_now": sum(1 for s in seg_rows if s["enabled"] and s["covered_text"] == "失敗"),
                    "causes": len(causes)},
        "probe": probe_settings(),
        "thresholds": {"fail_ratio": FAIL_RATIO, "zero_streak": ZERO_STREAK, "stale_days": STALE_DAYS,
                       "schedule_grace_min": SCHEDULE_GRACE_MIN},
        "unknowns": UNKNOWNS,
    }
