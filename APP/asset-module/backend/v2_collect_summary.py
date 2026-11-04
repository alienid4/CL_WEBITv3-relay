"""主機履歷「收集摘要」：每一類收集最後一次成功收到東西的時間、距今天數、是否過期（codex 207）。

只讀既有欄位，不改收集邏輯、不連目標機：
  · 規格（作業系統／硬體）、服務、帳號、軟體＝asset_collect_status.compute 給的 last_collected_at
    （與資產明細頁、主機履歷同一份計算）；
  · 健康檢查＝health_snapshot 該主機 IP 最新一筆 taken_at（v2_sections.conn_health，與「健康檢查」頁籤同一支）。
門檻：app_settings 鍵 collect_stale_days（天，整數 1–365），沒設或不合法＝預設 8 天。
狀態三種，不能混：
  fresh＝距今不超過門檻；stale＝超過門檻（資料已過期）；never＝這類從未成功取得（未取得）。
VMware 設備（ESXi／vCenter）不由本系統收集（天條），整張摘要標「不適用」而不是一排「未取得」。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

DEFAULT_STALE_DAYS = 8
SETTING_KEY = "collect_stale_days"

# 類別代號 → 畫面名稱（順序＝畫面順序）
KINDS = [("spec", "作業系統與硬體規格"), ("service", "服務"), ("account", "帳號"), ("software", "軟體"), ("health", "健康檢查")]


def stale_days(conn) -> int:
    import db

    raw = db.get_setting(conn, SETTING_KEY, None)
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_STALE_DAYS
    return n if 1 <= n <= 365 else DEFAULT_STALE_DAYS


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    raw = str(ts).strip()
    if re.search(r"(Z|[+-]\d{2}:?\d{2})$", raw) and len(raw) > 10:        # 帶時區：轉成台北時間（本機資料庫存的是台北牆上時間）
        try:
            from datetime import timedelta, timezone
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt.astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None) if dt.tzinfo else dt
        except ValueError:
            return None
    s = raw.replace("T", " ")[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def classify(ts: str | None, now: datetime, limit: int) -> dict:
    """單一類別：時間 → {state, last, days, label}。時間讀不懂也算「未取得」，不猜。"""
    t = _parse(ts)
    if t is None:
        return {"state": "never", "last": None, "days": None, "state_label": "未取得"}
    age = now - t
    if age < timedelta(minutes=-5):                    # 未來的成功時間是異常資料：不當成「今天、正常」
        return {"state": "never", "last": str(ts)[:19], "days": None, "state_label": "未取得（時間異常）"}
    days = max(0, age.days)
    if age > timedelta(days=limit):                    # 超過門檻即過期（8 天 1 小時也算）；剛好 8 天仍正常
        return {"state": "stale", "last": str(ts)[:19], "days": days, "state_label": "資料已過期"}
    return {"state": "fresh", "last": str(ts)[:19], "days": days, "state_label": "正常"}


def _last_health_ok(ctx) -> str | None:
    """該主機任一 IP「連得上」的最新一筆健檢時間（連不上的紀錄不算成功）。沒有這張表（舊 DB）回 None。"""
    import sqlite3

    best = None
    try:
        for ip in ctx.ips:
            r = ctx.conn.execute("SELECT MAX(taken_at) AS m FROM health_snapshot WHERE ip = ? AND reachable = 1", (ip,)).fetchone()
            if r and r["m"] and (best is None or r["m"] > best):
                best = r["m"]
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc):
            raise
    return best


def summary(conn, key: str, now: datetime | None = None) -> dict:
    import v2_sections
    import v2_views

    now = now or datetime.now()
    limit = stale_days(conn)
    ctx = v2_sections.Ctx(conn, key)
    base = {"key": ctx.key, "stale_days": limit, "setting_key": SETTING_KEY}
    if v2_views.is_vmware(conn, ctx.groups[ctx.key]):
        return {**base, "applicable": False, "reason": "VMware 設備不由本系統收集，資訊來源是 vCenter／RVTools", "rows": []}
    cs = (ctx.detail().get("collect_status") or {})
    times = {
        "spec": (cs.get("spec") or {}).get("last_collected_at"),
        "service": (cs.get("service") or {}).get("last_collected_at"),
        "account": (cs.get("account") or {}).get("last_collected_at"),
        "software": (cs.get("software") or {}).get("last_collected_at"),
    }
    times["health"] = _last_health_ok(ctx)
    rows = []
    for n, (k, label) in enumerate(KINDS, 1):
        rows.append({"__no": n, "kind": k, "label": label, **classify(times.get(k), now, limit)})
    return {**base, "applicable": True, "reason": None, "targeted": bool(cs.get("targeted")), "rows": rows,
            "counts": {s: sum(1 for r in rows if r["state"] == s) for s in ("fresh", "stale", "never")}}
