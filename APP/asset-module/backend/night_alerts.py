"""夜間速查「目前告警中」（AUD-03）：只讀本機已快取的 WhatsUp 現況（whatsup_alerts），不做任何對外查詢、不寫庫。

資料由既有的 WhatsUp 輪詢（whatsup_service.poll，窗外排程或人工觸發）寫進來；這裡只把它「誠實地」端出來：

* 來源沒啟用／從沒成功輪詢過 ＝ 「未取得」。**不能**顯示成「目前沒有告警」。
* 快取超過新鮮度門檻（app_settings night_alert_fresh_min，預設 30 分鐘，管理員可改）＝ 「資料過舊」，
  過舊的 0 筆也不能說沒有告警。
* 來源有效、資料新鮮、0 筆 ＝ 才說「目前沒有告警（資料時間 HH:MM）」。
* 對回資產用 host_ops._match（IP＋主機名一致才算對上）；歧義列「待核」，不猜、不連結。
* 磁碟告警的分割區沿用 host_ops.classify_problem；抽不到寫「未取得」。

數字（嚴重／警告／未對應）與下鑽清單來自同一份 rows（counts 由 rows 數出，前端用同一組欄位篩）。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

import host_ops
from db import get_setting

K_FRESH = "night_alert_fresh_min"
DEFAULT_FRESH_MIN = 30
MIN_FRESH_MIN = 5
MAX_FRESH_MIN = 1440
NO_DATA = host_ops.NO_DATA
FMT = "%Y-%m-%d %H:%M:%S"
FUTURE_TOLERANCE_MIN = 5          # 資料時間晚於現在超過這麼久＝時鐘或時區有問題

#: 狀態（whatsup_constants.STATE_LABEL 的中文）→ 嚴重度。其餘一律「警告」。
CRITICAL_STATES = frozenset({"當機", "嚴重", "無回應", "錯誤"})
CAT_LABEL = host_ops.CAT_LABEL
MATCH_LABEL = {"matched": "已對應", "pending": "待核（不確定是哪一台）", "none": "未登記（資產庫對不到）"}


def _table(conn: sqlite3.Connection, name: str) -> bool:
    return host_ops._table(conn, name)


def fresh_minutes(conn: sqlite3.Connection) -> int:
    try:
        v = int(get_setting(conn, K_FRESH, str(DEFAULT_FRESH_MIN)) or DEFAULT_FRESH_MIN)
    except (TypeError, ValueError):
        v = DEFAULT_FRESH_MIN
    return min(max(v, MIN_FRESH_MIN), MAX_FRESH_MIN)


def save_fresh_minutes(conn: sqlite3.Connection, minutes: int) -> int:
    if not isinstance(minutes, int) or isinstance(minutes, bool) or not MIN_FRESH_MIN <= minutes <= MAX_FRESH_MIN:
        raise ValueError(f"新鮮度門檻要是 {MIN_FRESH_MIN}～{MAX_FRESH_MIN} 分鐘的整數")
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (K_FRESH, str(minutes)))
    return minutes


def _parse(s) -> datetime | None:
    """轉成本機時間的 naive datetime。純 'YYYY-MM-DD HH:MM:SS' 視為本機時間；ISO 帶 Z／+08:00 先換算成本機時間再比，不丟掉時區。"""
    if s is None:
        return None
    t = str(s).strip()
    try:
        if len(t) >= 19 and t[10] == " " and not t.endswith("Z") and "+" not in t[19:] and "-" not in t[19:]:
            return datetime.strptime(t[:19], FMT)
        d = datetime.fromisoformat(t)
        return d.astimezone().replace(tzinfo=None) if d.tzinfo else d
    except (TypeError, ValueError):
        return None


def severity_of(state: str | None) -> str:
    return "嚴重" if (state or "") in CRITICAL_STATES else "警告"


def _row(a: dict, idx, now: datetime) -> dict:
    items, trunc = host_ops.classify_problem(a.get("problem"))
    cats = []
    for c, _p, _t in items:
        lab = CAT_LABEL.get(c, c)
        if lab not in cats:
            cats.append(lab)
    disk = [p for c, p, _t in items if c == "disk"]
    if disk:
        uniq = list(dict.fromkeys(p or NO_DATA for p in disk))
        part_text = "、".join([p for p in uniq if p != NO_DATA] + ([NO_DATA] if NO_DATA in uniq else []))
    else:
        part_text = None                       # 不是磁碟類：這欄不適用（畫面顯示「—」）
    if not (a.get("problem") or "").strip():
        cats = [NO_DATA]
    res, cand, why = host_ops._match(idx, a.get("ip"), a.get("name"))
    kind = "matched" if res == "ip_name" else res
    key = next(iter(cand)) if kind == "matched" and len(cand) == 1 else None
    since = _parse(a.get("since"))
    dur = int((now - since).total_seconds() // 60) if since and now >= since else None
    return {"device_id": a["device_id"], "name": a.get("name") or "", "ip": a.get("ip") or "", "state": a.get("state") or "",
            "severity": severity_of(a.get("state")), "problem": a.get("problem") or "", "since": a.get("since"),
            "duration_min": dur, "categories": cats, "partitions": part_text, "truncated": bool(trunc),
            "match": kind, "match_label": MATCH_LABEL[kind], "match_note": why, "machine_key": key,
            "machine_name": idx.label(key) if key else None, "candidates": len(cand) if kind == "pending" else 0}


def counts_of(rows: list[dict]) -> dict:
    return {"total": len(rows),
            "critical": sum(1 for r in rows if r["severity"] == "嚴重"),
            "warning": sum(1 for r in rows if r["severity"] == "警告"),
            "unmapped": sum(1 for r in rows if r["match"] != "matched")}


def build(conn: sqlite3.Connection, now: datetime | None = None) -> dict:
    """純讀。回 {status, status_label, message, rows, counts, source, data_time, age_min, fresh_min}。
    status：not_enabled／never／stale（過舊）／clock（資料時間在未來）／unconfirmed（最近輪詢失敗）／ok（可信，rows 可能是 0 筆）。"""
    now = now or host_ops._now()
    fresh = fresh_minutes(conn)
    flag = conn.execute("SELECT enabled FROM feature_flags WHERE module_key = 'whatsup_api'").fetchone()
    module_on = bool(flag and flag["enabled"])
    setting_on = get_setting(conn, "whatsup_enabled", "0") == "1"
    enabled = module_on and setting_on
    interval = get_setting(conn, "whatsup_interval_min", None)
    runs_ok = _table(conn, "whatsup_runs")
    lr = conn.execute("SELECT at, status, message FROM whatsup_runs ORDER BY id DESC LIMIT 1").fetchone() if runs_ok else None
    ok = conn.execute("SELECT at, status FROM whatsup_runs WHERE status IN ('ok','partial') ORDER BY id DESC LIMIT 1").fetchone() if runs_ok else None
    source = {"module_enabled": module_on, "polling_enabled": setting_on, "enabled": enabled, "interval_min": int(interval) if (interval or "").isdigit() else None,
              "last_run_at": lr["at"] if lr else None, "last_run_status": lr["status"] if lr else None,
              "last_run_message": (lr["message"] if lr else None) or None}
    base = {"rows": [], "counts": counts_of([]), "source": source, "data_time": None, "age_min": None, "fresh_min": fresh, "last_failed": False}
    if not enabled:
        why = "WhatsUp 模組未啟用" if not module_on else "WhatsUp 定期輪詢未啟用"
        return {**base, "status": "not_enabled", "status_label": "未取得",
                "message": f"{why}，所以本頁沒有告警資料。這不代表目前沒有告警。"}
    if ok is None:
        return {**base, "last_failed": bool(lr and lr["status"] == "failed"), "status": "never", "status_label": "未取得",
                "message": "WhatsUp 已啟用，但還沒有成功輪詢過，所以沒有告警資料。這不代表目前沒有告警。"}
    data_at = _parse(ok["at"])
    raw_age = int((now - data_at).total_seconds() // 60) if data_at else None
    future = raw_age is not None and raw_age < -FUTURE_TOLERANCE_MIN
    age = None if raw_age is None else max(raw_age, 0)
    rows_raw = [dict(r) for r in conn.execute("SELECT * FROM whatsup_alerts")] if _table(conn, "whatsup_alerts") else []
    idx = host_ops._Idx(conn)
    rows = [_row(a, idx, now) for a in rows_raw]
    rows.sort(key=lambda r: (0 if r["severity"] == "嚴重" else 1, r["since"] or "9999", r["name"], r["device_id"]))
    for i, r in enumerate(rows, 1):
        r["__no"] = i                                  # 固定項次：以預設排序為準
    last_failed = bool(lr and lr["status"] == "failed")
    stale = age is None or age > fresh
    out = {**base, "rows": rows, "counts": counts_of(rows), "data_time": ok["at"], "age_min": age, "last_failed": last_failed}
    hhmm = (ok["at"] or "")[11:16]
    if future:
        return {**out, "age_min": None, "status": "clock", "status_label": "時間異常（資料時間在未來）",
                "message": f"時間異常：資料時間 {ok['at']} 比現在晚 {-raw_age} 分鐘（伺服器時鐘或時區可能有誤），無法判斷新不新鮮。下面是當時的快取；沒列出的不代表沒有告警。"}
    if stale:
        return {**out, "status": "stale", "status_label": "資料過舊",
                "message": (f"資料過舊：最後一次成功取得是 {ok['at']}（{age} 分鐘前，門檻 {fresh} 分鐘）。" if age is not None
                            else f"資料過舊：最後成功時間無法判讀（{ok['at']}）。") + "下面是當時的快取，不是現在的狀況；沒列出的也不代表沒有告警。"}
    if last_failed:
        return {**out, "status": "unconfirmed", "status_label": "無法確認（最近輪詢失敗）",
                "message": f"無法確認：最近一次輪詢失敗，目前狀況不明。下面是最後成功（{ok['at']}）時的快取；沒列出的不代表沒有告警。"}
    return {**out, "status": "ok", "status_label": "資料可用",
            "message": (f"目前沒有告警（資料時間 {hhmm}）" if not rows else f"資料時間 {hhmm}（{age} 分鐘前）")}
