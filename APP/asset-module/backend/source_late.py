"""資料沒進來主動通知（SI-3，2026-11-22）：每種中繼資料訂「最晚多久要到」，逾時就排一則 Teams，恢復再排一則。

- 判斷依據：relay_import_batches 裡該來源最後一次 status='imported' 的 imported_at（本系統自己的時鐘，不信對方填的 collected_at）。
- 只判斷「曾經收過」的來源：從沒收過的（例如這台沒開 vCOps 直抓）不算逾時，免得沒啟用的來源天天吵。
- 去重：同一來源逾時只在「剛變逾時」那一輪推一次，之後每天最多再提醒一次；恢復只推一次。狀態存 app_settings.source_late_state。
- Teams 沒開：只記狀態（畫面／下鑽照常看得到），不排佇列。
- 設定：app_settings.source_late（JSON）{"enabled": true, "hours": {來源: 小時}}；沒設就用 DEFAULT_HOURS。壞掉＝照預設。
- 下鑽：通知卡片按鈕連到 1.5 每日檢查「資料來源」分頁；status() 回逐來源明細（最後到達、已過幾小時、門檻、是否逾時）。
只讀本機表、寫 app_settings、排 Teams 佇列；不連任何主機。由 scan_service._notice_digest_tick 每分鐘呼叫一次（內部自己節流 10 分鐘），
不是獨立排程入口。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

log = logging.getLogger("source_late")
SETTING_KEY = "source_late"
STATE_KEY = "source_late_state"
THROTTLE_MIN = 10
FMT = "%Y-%m-%d %H:%M:%S"
# 來源（relay_import_batches.source）→ (畫面名稱, 預設最晚幾小時要到)。數字取各來源實際排程再放寬：
# 效能每天一批→30 小時；上次輪循每 30 分鐘一批→2 小時；VMware 每日檢查一天三批→8 小時。
SOURCES: dict[str, tuple[str, float]] = {
    "whatsup": ("WhatsUp 效能／磁碟", 30.0),
    "whatsup_lastpoll": ("WhatsUp 上次輪循", 2.0),
    "aria_dailycheck": ("VMware 每日檢查", 8.0),
    "aria_host": ("vCOps 主機資料", 30.0),
    "aria_inventory": ("vCOps 清單", 30.0),
    "unity": ("Unity 儲存健康", 30.0),
}
DEFAULT_HOURS = {k: v[1] for k, v in SOURCES.items()}


def _has(conn, t: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() is not None


def load_settings(conn) -> dict:
    cur: dict = {}
    r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (SETTING_KEY,)).fetchone()
    if r and r[0]:
        try:
            cur = json.loads(r[0])
        except ValueError:
            cur = {}
        if not isinstance(cur, dict):
            cur = {}
    hours = dict(DEFAULT_HOURS)
    given = cur.get("hours")
    for k, v in (given.items() if isinstance(given, dict) else []):
        try:
            h = float(v)
        except (TypeError, ValueError):
            continue
        if k in SOURCES and 0 < h <= 24 * 60:      # 不認得的來源名、不合理的數字一律忽略（照預設）
            hours[k] = h
    return {"enabled": cur.get("enabled", True) is not False, "hours": hours}


def _parse(t: str | None) -> datetime | None:
    try:
        return datetime.strptime((t or "")[:19], FMT)
    except ValueError:
        return None


def status(conn, now: datetime) -> list[dict]:
    """逐來源明細（下鑽用）。state：ok／late／never（從沒收過，不判逾時）。"""
    cfg = load_settings(conn)
    have = _has(conn, "relay_import_batches")
    out = []
    for src, (label, _) in SOURCES.items():
        limit = cfg["hours"][src]
        last = None
        if have:
            r = conn.execute("SELECT MAX(imported_at) FROM relay_import_batches WHERE source = ? AND status = 'imported'", (src,)).fetchone()
            last = r[0] if r else None
        at = _parse(last)
        if at is None:
            out.append({"source": src, "label": label, "last_at": None, "age_h": None, "limit_h": limit, "state": "never"})
            continue
        age_raw = (now - at).total_seconds() / 3600        # 判斷用原始值；顯示才四捨五入（否則 2 小時 1 分會被進位成沒逾時）
        out.append({"source": src, "label": label, "last_at": last, "age_h": round(age_raw, 1), "limit_h": limit,
                    "state": "late" if age_raw > limit else "ok"})
    return out


def _load_state(conn) -> dict:
    r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (STATE_KEY,)).fetchone()
    try:
        st = json.loads(r[0]) if r and r[0] else {}
    except ValueError:
        st = {}
    return st if isinstance(st, dict) else {}


def _save_state(conn, st: dict) -> None:
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (STATE_KEY, json.dumps(st, ensure_ascii=False)))


def _push(conn, now: datetime, title: str, facts: list[tuple[str, str]], sev: str, key: str) -> str:
    import teams_notify as tn
    try:
        tcfg = tn.load_settings(conn)
        if not tcfg["enabled"]:
            return "recorded"
    except Exception:  # noqa: BLE001 - Teams 設定讀不到：只記狀態，下鑽畫面照樣看得到逾時
        return "recorded"
    tn.ensure(conn)
    card = tn._card(title, "Attention" if sev == "warning" else "Good", facts, link=tn._link(tcfg, "/v2/dailycheck?tab=sources"))
    tn._enqueue(conn, key, "source_late", "source_late", sev, title, card, now)
    return "queued"


def check(conn, now: datetime | None = None, force: bool = False) -> dict:
    """評估所有來源；有新逾時／仍逾時隔天提醒／恢復才排通知。自己 commit。節流 10 分鐘（force=True 略過，測試用）。"""
    import teams_notify as tn
    now = now or tn.now_taipei()
    cfg = load_settings(conn)
    if not cfg["enabled"]:
        return {"status": "disabled"}
    st = _load_state(conn)
    last = _parse(st.get("_checked_at"))
    if not force and last and 0 <= (now - last).total_seconds() < THROTTLE_MIN * 60:
        return {"status": "throttled"}
    day = now.strftime("%Y-%m-%d")
    rows = status(conn, now)
    new_late, still_late, back = [], [], []
    for r in rows:
        s = st.get(r["source"]) or {}
        if r["state"] == "late":
            if not s.get("late"):
                new_late.append(r)
                # notified_day 先留空：只有真的排進 Teams 佇列才寫（2.070.1，審查 p2#14；Teams 沒開時先記「已判斷」，稍後開了同日還補得到）
                st[r["source"]] = {"late": True, "since": now.strftime(FMT), "notified_day": None}
            elif s.get("notified_day") != day:
                still_late.append(r)
        elif s.get("late"):
            if r["state"] == "ok" and _parse(r["last_at"]):
                back.append((r, s.get("since")))
            # 恢復通知只在「前一狀態 late、目前 ok、last_at 有效」才送；late→never（歷史被清、表異常）不送「已收到新資料（None）」（審查 p2#13）
            st[r["source"]] = {"late": False}
    pushed = []
    late_now = new_late + still_late
    if late_now:
        facts = [(r["label"], f"最後到達 {r['last_at']}（已 {r['age_h']:g} 小時，門檻 {r['limit_h']:g} 小時）") for r in late_now]
        first = f"資料沒進來：{len(late_now)} 種來源逾時"
        res = _push(conn, now, first, facts, "warning", f"srclate:{now.strftime('%Y%m%d%H%M')}:late")
        pushed.append(("late", res))
        if res == "queued":                       # 真的排進佇列才算今天通知過
            for r in late_now:
                st[r["source"]]["notified_day"] = day
    if back:
        facts = [(r["label"], f"已收到新資料（{r['last_at']}）；逾時自 {since}") for r, since in back]
        pushed.append(("back", _push(conn, now, f"資料恢復進來：{len(back)} 種來源", facts, "info",
                                      f"srclate:{now.strftime('%Y%m%d%H%M')}:back")))
    st["_checked_at"] = now.strftime(FMT)
    _save_state(conn, st)
    conn.commit()
    return {"status": "ok", "late": [r["source"] for r in late_now], "recovered": [r["source"] for r, _ in back], "pushed": pushed}
