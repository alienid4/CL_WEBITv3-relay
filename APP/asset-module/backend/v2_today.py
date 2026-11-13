"""1.1 今日（R9）與 1.4 追蹤處理（R10）（2.044.0，使用者 2026-11-04）。只讀，邏輯都是組裝既有計算，不另算台數。

使用者原話：「今日概況應該是哪台主機有報修、有報告警、哪個 CPU 前 10%、memory swap 最高，這才是今日；
保固到期每天都 30 台，一年才做完，永遠放首頁不恰當」。

## 今日（today）＝台北時間 00:00 到現在

  · 告警分兩段（2.044.3 GPT A2＋B1）：「現在仍在告警」＝還開著的（不管哪天開始，昨天開始的標「昨日起」）；
    「今天發生過」＝今天 00:00 之後開始的（含已恢復，列開始／恢復／持續）。件數＝各自的列數。
    告警功能沒啟用＝「告警未啟用」，從沒收到過資料＝「未收集」，心跳逾時＝「資料中斷」——這三種都**不是 0 件**。
  · 今天的事件＝incident_event 今天開立或今天結案的（4.1 同一張表）。
  · 今天的資產異動＝daily_digest 今天那份快照的比對（1.2 每日異動同一支 diff_for），標快照時間；
    今天還沒有快照＝「未收集（最近一份 YYYY-MM-DD）」，不是 0 件。
  · 開盤尖峰（2.045.0）＝opening_peak.summary：跳板機收盤後從 Aria 匯出、走中繼匯入的 ESXi 主機檔；今天還沒收到寫「尚未匯入」，不放數字。
  · 長期待辦只留一行：追蹤處理共幾項、今天新出現幾項、今天解除幾項。

## 追蹤處理（track）＝原首頁那張待處理表搬過來，展開成一筆一列

每一類的筆數＝該類的列數（摘要卡＝下鑽，verify_numbers 核），每一列跟它原本那一頁同一支計算：
  進行中事件＝incident.list_events（open）、掃描無回應＝scan_health.issues（未處理）、
  保固＝daily_risk.contract_tab（已過期＋90 天內）、單點風險／逾期未重開機／硬體異常＝daily_risk.risk_overview、
  容量預警＝storage_capacity.alerts。
來源沒有資料的類別照列、標「無資料（原因）」，不悄悄消失。第一版唯讀（指派／回覆／結案是第二階段）。

「今天新增／解除」（2.044.3 GPT A3：GET 不寫入）：排程每小時一次＋15:05 後一次把當下每一列（含對象與說明）
存進 track_snapshot／track_snapshot_item；畫面只讀。今日新增＝今天最新快照有、昨天以前最後一份沒有；
今日解除＝昨天最後一份或今天較早的快照有、最新快照沒有（快照之間消失的），可以列出是哪幾件。
只是衍生紀錄，不是資產資料；保存 60 天。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

TRACK_KEEP_DAYS = 60
TODAY_MAX = 2000          # 「今天發生過」明細上限（件數另用 COUNT，不受影響）

CATS = (
    ("incident_open", "進行中事件", "/v2/incident?tab=open"),
    ("scan_empty", "掃描無回應", "/v2/changes?tab=issues"),
    ("contract", "保固／維護合約", "/v2/risk?tab=contract"),
    ("spof", "單點風險", "/v2/risk?tab=spof"),
    ("uptime", "逾期未重開機", "/v2/risk?tab=uptime"),
    ("capacity", "容量預警", "/v2/storage?tab=low"),
    ("hardware", "硬體異常", "/v2/risk?tab=hardware"),
)
CAT_LABEL = {k: lab for k, lab, _ in CATS}
LEVEL_ORDER = {"高": 0, "處理": 1, "中": 1, "一般": 2}


def _now() -> datetime:
    import quiet_window as qw
    return qw.local_now()


def _mlink(key) -> str | None:
    from urllib.parse import quote
    return f"/v2/machine/{quote(str(key), safe='')}" if key else None


# ===================================================================== 追蹤處理
def _rows_incident(conn) -> tuple[list[dict], dict]:
    import incident
    out = []
    for e in incident.list_events(conn):
        if e.get("status") != "open":
            continue
        formal = e.get("mode") == "formal"
        out.append({"id": f"incident_open:{e['id']}", "target": e["title"], "ip": None, "systems": None,
                    "detail": f"{e['type']}・{e['mode_label']}・開始 {str(e['started_at'])[:16]}",
                    "level": "高" if formal else "中", "since": str(e["started_at"])[:10], "link": f"/v2/incident/{e['id']}"})
    return out, {}


def _rows_scan(conn) -> tuple[list[dict], dict]:
    import scan_health
    out = []
    for r in scan_health.issues(conn, is_read=False):
        out.append({"id": f"scan_empty:{r['id']}", "target": "網段掃描", "ip": None, "systems": None,
                    "detail": r["hostname"], "level": "高", "since": str(r["detected_at"] or "")[:10],
                    "link": "/v2/changes?tab=issues"})
    return out, {}


def _rows_risk(rk: dict) -> dict[str, tuple[list[dict], dict]]:
    import daily_risk as dr
    res: dict[str, tuple[list[dict], dict]] = {}
    ct = rk["contract"]
    rows = []
    for r in ct["rows"]:
        if r["band"] not in (dr.BAND_EXPIRED, dr.BAND_90):
            continue
        left = r["days_left"]
        rows.append({"id": f"contract:{r['key']}", "target": r["hostname"] or r["serial"], "ip": r["ip"], "systems": r["systems"],
                     "detail": f"合約迄日 {r['contract_end']}（{'已過期 ' + str(-left) + ' 天' if left is not None and left < 0 else '剩 ' + str(left) + ' 天'}）"
                               + (f"・{r['vendor']}" if r.get("vendor") else ""),
                     "level": "高" if r["band"] == dr.BAND_EXPIRED else "中", "since": r["contract_end"], "link": _mlink(r["key"]),
                     "notify": r.get("notify")})
    res["contract"] = (rows, {} if ct.get("has_data") else {"status": "nodata", "note": "無資料（未匯入保固資料）"})
    sp = rk["spof"]
    res["spof"] = ([{"id": f"spof:{r['api_id']}", "target": f"{r['api_id']} {r['name'] or ''}".strip(), "ip": None, "systems": None,
                     "detail": f"{r['reason']}・正式 {r['prod_count']} 台・{r['rooms'] or '機房未填'}",
                     "level": "高" if r["reason"] == dr.SPOF_SINGLE else "中", "since": None,
                     "link": f"/v2/system/{r['api_id']}", "notify": r.get("notify")} for r in sp["rows"]], {})
    up = rk["uptime"]
    res["uptime"] = ([{"id": f"uptime:{r['key']}", "target": r["hostname"] or r["serial"], "ip": r["ip"], "systems": r["systems"],
                       "detail": f"{r['days']} 天未重開機（上次開機 {r['last_boot']}）", "level": "中", "since": None,
                       "link": _mlink(r["key"]), "notify": r.get("notify")} for r in up["rows"]],
                     {} if up["collected"] else {"status": "nodata", "note": "無資料（尚未收集到任何主機的開機時間）"})
    hw = rk["hardware"]
    res["hardware"] = ([{"id": f"hardware:{r['key']}", "target": r["hostname"] or r["serial"], "ip": r["ip"], "systems": r["systems"],
                         "detail": r["detail"], "level": r.get("level") or "高", "since": None, "link": _mlink(r["key"]), "notify": r.get("notify")}
                        for r in hw["rows"]],
                       {} if hw["collected"] else {"status": "nodata", "note": "無資料（尚未收集到任何主機的硬體規格）"})
    return res


def _rows_capacity(conn) -> tuple[list[dict], dict]:
    import storage_capacity as sc
    ds = sc.datastores(conn)
    ph = sc.physical(conn)
    al = sc.alerts(ds, ph)
    fr = sc.freshness(conn, ds)
    rows = []
    for v in al["volumes"]:
        rows.append({"id": f"capacity:ds:{v['id']}", "target": v.get("name") or v["id"], "ip": None, "systems": None,
                     "detail": f"Datastore 剩餘 {v['free_mib'] / 1024:,.0f} GB（門檻 {sc.rule_text(al['lun_rule'])}）・{v.get('room') or ''}".rstrip("・"),
                     "level": v.get("level_label") or "中", "since": None, "link": "/v2/storage?tab=low"})
    for p in al["physical"]:
        used = p.get("used_pct")
        rows.append({"id": f"capacity:host:{p['asset_serial']}", "target": p["name"], "ip": p["ip"], "systems": p.get("api_id"),
                     "detail": (f"檔案系統使用率 {used:g}%" if used is not None else f"檔案系統剩餘 {p['free_pct']:g}%"),
                     "level": p.get("level_label") or "中", "since": None, "link": _mlink(p["asset_serial"])})
    has_mount = any(r["by"] == "mount" for r in ph["rows"])
    meta: dict = {}
    if not fr["has_data"] and not has_mount:
        meta = {"status": "nodata", "note": "無資料（尚未匯入 RVTools，也沒有收集到實體主機檔案系統）"}
    elif not fr["has_data"]:
        meta = {"note": "Datastore 無資料（尚未匯入 RVTools），只列實體主機檔案系統"}
    elif fr["stale"]:
        meta = {"note": f"Datastore 資料已 {fr['age_days']} 天未更新（最近 RVTools 匯入 {str(fr['as_of'])[:10]}）"}
    if al.get("physical_uncollected"):
        meta["note"] = "；".join(x for x in (meta.get("note"), f"檔案系統剩餘未收集 {al['physical_uncollected']} 台（不列入、不是 0）") if x)
    return rows, meta


# ── 追蹤處理快照（2.044.3，GPT A3）：排程每小時一次＋盤後一次寫，GET 只讀 ───────────────────────
SNAP_SQL = [
    """CREATE TABLE IF NOT EXISTS track_snapshot (
        id INTEGER PRIMARY KEY AUTOINCREMENT, taken_at TEXT NOT NULL, day TEXT NOT NULL, kind TEXT NOT NULL, items INTEGER)""",
    """CREATE TABLE IF NOT EXISTS track_snapshot_item (
        snapshot_id INTEGER NOT NULL, item TEXT NOT NULL, cat TEXT, cat_label TEXT, level TEXT, target TEXT, ip TEXT, detail TEXT, link TEXT,
        PRIMARY KEY (snapshot_id, item))""",
]
CLOSE_AT = (15, 5)          # 盤後那一次：15:05 以後當天第一個 tick


def _snap_ready(conn) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='track_snapshot'").fetchone() is not None


def snapshot_due(conn, now: datetime) -> str | None:
    """回要寫的快照種類（hourly／close）或 None。同一小時只一份；15:05 後當天沒有 close 就補一份 close。"""
    if not _snap_ready(conn):
        return "hourly"
    day = now.strftime("%Y-%m-%d")
    if (now.hour, now.minute) >= CLOSE_AT and not conn.execute(
            "SELECT 1 FROM track_snapshot WHERE day = ? AND kind = 'close'", (day,)).fetchone():
        return "close"
    hour = now.strftime("%Y-%m-%d %H")
    if not conn.execute("SELECT 1 FROM track_snapshot WHERE substr(taken_at, 1, 13) = ?", (hour,)).fetchone():
        return "hourly"
    return None


def write_snapshot(conn, now: datetime | None = None, kind: str = "hourly") -> int:
    """算一次追蹤處理、把每一列（含對象與說明，解除時才列得出是哪幾件）存起來。自己 commit。"""
    now = now or _now()
    t = _compute(conn, now)
    for s in SNAP_SQL:
        conn.execute(s)
    cur = conn.execute("INSERT INTO track_snapshot (taken_at, day, kind, items) VALUES (?,?,?,?)",
                       (now.strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d"), kind, len(t["rows"])))
    sid = cur.lastrowid
    conn.executemany("INSERT OR IGNORE INTO track_snapshot_item (snapshot_id, item, cat, cat_label, level, target, ip, detail, link) "
                     "VALUES (?,?,?,?,?,?,?,?,?)",
                     [(sid, r["id"], r["cat"], r["cat_label"], r["level"], r["target"], r.get("ip"), r.get("detail"), r.get("link"))
                      for r in t["rows"]])
    cut = (now - timedelta(days=TRACK_KEEP_DAYS)).strftime("%Y-%m-%d")
    old = [r[0] for r in conn.execute("SELECT id FROM track_snapshot WHERE day < ?", (cut,))]
    if old:
        conn.execute(f"DELETE FROM track_snapshot_item WHERE snapshot_id IN ({','.join('?' for _ in old)})", old)
        conn.execute(f"DELETE FROM track_snapshot WHERE id IN ({','.join('?' for _ in old)})", old)
    conn.commit()
    return sid


def tick(conn, now: datetime | None = None) -> dict:
    """排程器每分鐘叫一次（scan_service._track_snapshot_tick）：到點才寫。純本機計算（B 類，不連任何主機）。"""
    now = now or _now()
    kind = snapshot_due(conn, now)
    if not kind:
        return {"status": "idle"}
    return {"status": "written", "kind": kind, "snapshot_id": write_snapshot(conn, now, kind)}


def _items(conn, sid: int) -> dict[str, dict]:
    return {r["item"]: dict(r) for r in conn.execute("SELECT * FROM track_snapshot_item WHERE snapshot_id = ?", (sid,))}


def changes(conn, now: datetime) -> dict:
    """今日變化（只讀快照）：
      · 基準＝今天 00:00 之前最後一份快照；最新＝今天最後一份快照。
      · 今日新增＝最新有、基準沒有。
      · 今日解除＝基準或今天較早的快照有、最新沒有（快照之間消失的），列得出是哪幾件（存了對象與說明）。"""
    empty = {"prev_day": None, "baseline_at": None, "latest_at": None, "new": None, "resolved": None, "new_ids": [], "resolved_items": []}
    if not _snap_ready(conn):
        return {**empty, "reason": "無資料（還沒有任何追蹤處理快照；排程每小時寫一次）"}
    day = now.strftime("%Y-%m-%d")
    snaps = conn.execute("SELECT id, taken_at, day FROM track_snapshot WHERE day = ? ORDER BY taken_at, id", (day,)).fetchall()
    base = conn.execute("SELECT id, taken_at, day FROM track_snapshot WHERE day < ? ORDER BY taken_at DESC, id DESC LIMIT 1", (day,)).fetchone()
    if not snaps:
        return {**empty, "reason": "無資料（今天還沒有追蹤處理快照；排程每小時寫一次）"}
    latest = _items(conn, snaps[-1]["id"])
    out = {**empty, "latest_at": snaps[-1]["taken_at"]}
    if base is None:
        return {**out, "reason": "無資料（沒有昨天以前的快照可比，明天起才算得出今天的變化）"}
    old = _items(conn, base["id"])
    seen: dict[str, dict] = dict(old)
    for s in snaps[:-1]:
        for k, v in _items(conn, s["id"]).items():
            seen.setdefault(k, v)
    new = sorted(set(latest) - set(old))
    gone = sorted(set(seen) - set(latest))
    items = []
    for i, k in enumerate(gone, 1):
        v = seen[k]
        items.append({"__no": i, "id": k, "cat": v["cat"], "cat_label": v["cat_label"], "level": v["level"], "target": v["target"],
                      "ip": v["ip"], "detail": v["detail"], "link": v["link"], "was_in": "昨日" if k in old else "今日較早"})
    return {**out, "prev_day": base["day"], "baseline_at": base["taken_at"], "new": len(new), "resolved": len(gone),
            "new_ids": new, "resolved_items": items, "reason": None}


def track(conn: sqlite3.Connection, now: datetime | None = None, record: bool = False) -> dict:
    """追蹤處理（GET 用，只讀）：現在每一類的列＋今日變化（讀排程寫的快照）。record 參數只留相容，不再寫入。"""
    now = now or _now()
    t = _compute(conn, now)
    ch = changes(conn, now)
    newset = set(ch["new_ids"])
    for r in t["rows"]:
        r["is_new"] = r["id"] in newset
    t["changes"] = ch
    return t


def _compute(conn: sqlite3.Connection, now: datetime) -> dict:
    import daily_risk

    per: dict[str, tuple[list[dict], dict]] = {}

    def run(key, fn):
        try:
            per[key] = fn()
        except Exception as exc:  # noqa: BLE001 - 一類算不出來不能讓整頁掛掉，但要講出來（標無法計算）
            per[key] = ([], {"status": "error", "note": f"無法計算（{type(exc).__name__}）"})

    run("incident_open", lambda: _rows_incident(conn))
    run("scan_empty", lambda: _rows_scan(conn))
    try:
        per.update(_rows_risk(daily_risk.risk_overview(conn, now)))
    except Exception as exc:  # noqa: BLE001 - 同上
        for k in ("contract", "spof", "uptime", "hardware"):
            per[k] = ([], {"status": "error", "note": f"無法計算（{type(exc).__name__}）"})
    run("capacity", lambda: _rows_capacity(conn))

    rows, cats = [], []
    up_on = daily_risk.uptime_enabled(conn)          # 使用者 11-14：逾期未重開機預設不列（track_uptime_enabled=1 才回來）
    for key, label, link in CATS:
        if key == "uptime" and not up_on:
            continue
        rs, meta = per.get(key, ([], {}))
        rs = sorted(rs, key=lambda r: (LEVEL_ORDER.get(r["level"], 9), str(r["target"] or "")))
        for r in rs:
            r["cat"], r["cat_label"] = key, label
            rows.append(r)
        cats.append({"key": key, "label": label, "link": link, "n": len(rs), "high": sum(1 for r in rs if r["level"] == "高"),
                     "status": meta.get("status", "ok"), "note": meta.get("note")})
    for i, r in enumerate(rows, 1):
        r["__no"] = i
    return {"generated_at": now.strftime("%Y-%m-%d %H:%M:%S"), "categories": cats, "rows": rows, "total": len(rows),
            "high": sum(1 for r in rows if r["level"] == "高")}


# ===================================================================== 今日
SCOPES = ("policy", "all")


def group_key(r: dict) -> str:
    """「同一批」＝同一分鐘開始＋同一個來源設備（WhatsUp 設備 ID；沒有就用 IP）。
    例：同一台 AP 主機十幾個程序監控（AppA.exe、AppB.exe…）在同一分鐘一起亮 → 收成一列。"""
    src = str(r.get("wug_device_id") or r.get("ip") or r.get("who") or "")
    if not src:                                         # 來源全空：不知道是不是同一台，用告警自己的 id，絕不合併
        src = f"id:{r.get('id')}"
    return f"{str(r.get('started_at') or '')[:16]}|{src}"


def group_alerts(rows: list[dict]) -> list[dict]:
    """把告警列收成「同分鐘＋同來源」一組一列；每組帶 ids（下鑽＝這幾筆；加總＝下鑽：各組 n 加總＝rows 筆數）。
    組的順序跟 rows 第一次出現的順序一樣；只有一筆的組 n＝1，照樣一列。"""
    groups: dict[str, dict] = {}
    for r in rows:
        k = group_key(r)
        g = groups.get(k)
        if g is None:
            g = groups[k] = {**r, "group": k, "n": 0, "ids": [], "monitors": []}
        g["n"] += 1
        g["ids"].append(r["id"])
        g["monitors"].append(r.get("monitor") or "")
        if r.get("tier") == "red":                      # 一組裡有 🔴 就整組標 🔴（最嚴重的不能被收起來）
            g["tier"], g["tier_label"] = r["tier"], r.get("tier_label")
        g["duration_min"] = max(int(g.get("duration_min") or 0), int(r.get("duration_min") or 0))
    out = []
    for i, g in enumerate(groups.values(), 1):
        mons = list(dict.fromkeys(m for m in g.pop("monitors") if m))
        g["monitor"] = "、".join(mons[:3]) + (f" 等 {g['n']} 項" if g["n"] > 3 else "")
        g["__no"] = i
        out.append(g)
    return out


def scope_filter(rows: list[dict], cfg: dict, scope: str = "policy") -> tuple[list[dict], dict]:
    """通知範圍＝4.1 告警設定的 notify_policies（Teams 只推這些）；判斷沿用 wug_alert.policy_keywords／policy_ok，不另寫一份。
    scope＝all 或沒設關鍵字 → 全部。回 (篩後的列, 範圍說明)。"""
    import wug_alert as wa
    kws = wa.policy_keywords(cfg.get("notify_policies"))
    use = scope != "all" and bool(kws)
    out = [r for r in rows if wa.policy_ok(r.get("policy"), kws)] if use else list(rows)
    label = (f"只列 Teams 通知範圍（動作政策含：{'、'.join(kws)}）" if use
             else ("全部（含不在 Teams 通知範圍的）" if kws else "全部（4.1 告警設定沒有指定通知的動作政策，Teams 也是全部推）"))
    return out, {"mode": "policy" if use else "all", "requested": scope if scope in SCOPES else "policy", "keywords": kws,
                 "n_all": len(rows), "hidden": len(rows) - len(out), "label": label}


def _attention(conn, now: datetime, cfg: dict, scope: str, open_rows: list[dict], start: str, st: dict) -> dict:
    """2.066.x（使用者 11-13）：「需要注意」＝只列非例行、斷滿門檻還沒恢復的；其他進摺疊區（alert_attention.judge，通知同一支）。
    已恢復的取「今天恢復」的（含昨天開始、今天才恢復的跨午夜事件），套同一個範圍。心跳壞了＝監控盲區，不能說沒事。"""
    import alert_attention as aa
    import wug_alert as wa
    closed = [wa._row(r) for r in conn.execute(
        "SELECT * FROM alert_event WHERE ended_at IS NOT NULL AND ended_at >= ? AND tier != 'maint' ORDER BY started_at DESC, id DESC", (start,))]         if wa._has(conn, "alert_event") else []
    closed, _ = scope_filter(closed, cfg, scope)
    import alert_contact
    alert_contact.attach(conn, closed)
    out = aa.judge(conn, open_rows, closed, now)
    out["groups"] = group_alerts(out["rows"])
    hb = st.get("heartbeat")
    out["blind"] = None if hb == "ok" else (
        "監控盲區：還沒收到任何 WhatsUp 告警資料，看不到就不代表沒事" if hb == "never"
        else f"監控盲區：告警資料中斷（最後收到 {st.get('last_received')}），之後斷線看不到；下面是中斷前的狀態，不代表沒事")
    try:
        out["suggestions"] = aa.suggestion_count(conn, now)
    except sqlite3.Error:                         # 建議只是提示，算不出來不擋首頁；照實標
        out["suggestions"] = None
    return out


def _alerts_now(conn, now: datetime, scope: str = "policy") -> dict:
    """首頁「現在仍在告警」＋「今天發生過」。夜間速查（night_alerts）也讀這一份，不另算。
    scope：policy（預設）＝跟 Teams 同一個主機組政策範圍（4.1 notify_policies；沒設＝全部）；all＝看全部。"""
    import wug_alert as wa
    start = now.strftime("%Y-%m-%d 00:00:00")
    try:
        cfg = wa.load_settings(conn)
    except ValueError as exc:
        return {"status": "error", "label": str(exc), "rows": [], "groups": [], "n": None, "as_of": None, "today": {"rows": [], "n": None}}
    if not cfg["enabled"]:
        return {"status": "disabled", "label": "告警未啟用", "rows": [], "groups": [], "n": None, "as_of": None, "today": {"rows": [], "n": None}}
    st = wa.status(conn, now)
    # 口徑（2.044.3，GPT A2＋B1，R9 驗收改這樣）：
    #   rows＝現在仍在告警（不管哪天開始；昨天以前開始的標「昨日起」）
    #   today.rows＝今天 00:00 之後開始的（含已恢復），列開始／恢復／持續；兩段的件數＝各自的列數（同一份）
    rows, sc = scope_filter([r for r in wa.events(conn, state="open", limit=20000) if r["tier"] != "maint"], cfg, scope)
    # P2（2.044.4）：件數不受明細上限影響；明細最多 TODAY_MAX 筆，超過就標「已截斷」，不靜默少算。
    # 通知範圍要逐列看政策，所以今天的列整批讀進來再篩（一天上千筆，量級可接受）
    today_all = [wa._row(r) for r in conn.execute(
        "SELECT * FROM alert_event WHERE started_at >= ? AND tier != 'maint' ORDER BY started_at DESC, id DESC", (start,))] \
        if wa._has(conn, "alert_event") else []
    today_scoped, _ = scope_filter(today_all, cfg, scope)
    n_all, n_open = len(today_scoped), sum(1 for r in today_scoped if r["open"])
    today_rows = today_scoped[:TODAY_MAX]
    import alert_contact
    import alert_notify
    for rs in (rows, today_rows):
        alert_contact.attach(conn, rs)       # R15：告警聯絡五欄（AP 團隊、單位、系統名稱、分級、email）
        alert_notify.attach(conn, rs)        # R16：已人工通知幾次、最後結果
        for i, r in enumerate(rs, 1):
            r["__no"] = i
            r["new_today"] = str(r["started_at"]) >= start
            r["since_note"] = "" if r["new_today"] else ("昨日起" if str(r["started_at"])[:10] == (now - timedelta(days=1)).strftime("%Y-%m-%d")
                                                          else f"{str(r['started_at'])[5:10]} 起")
    truncated = n_all > len(today_rows)
    today = {"rows": today_rows, "n": n_all, "still_open": n_open, "recovered": n_all - n_open, "shown": len(today_rows),
             "truncated": truncated, "label": f"已截斷：只列最近 {len(today_rows)} 筆，今天共 {n_all} 件（件數是全部，不受截斷影響）" if truncated else None}
    groups = group_alerts(rows)
    attention = _attention(conn, now, cfg, scope, rows, start, st)
    if st["heartbeat"] == "never":
        return {"status": "uncollected", "label": "未收集（還沒收到任何 WhatsUp 告警資料）", "rows": rows, "groups": groups, "n": None,
                "as_of": None, "started_today": None, "scope": sc, "attention": attention, "today": {**today, "n": None}}
    out = {"status": "ok", "label": None, "rows": rows, "groups": groups, "n": len(rows), "as_of": st["last_received"], "started_today": n_all,
           "maintenance": st["maintenance"], "scope": sc, "heartbeat_min": int(cfg["heartbeat_min"]), "attention": attention, "today": today}
    if st["heartbeat"] == "down":
        out.update(status="stale", label=f"資料中斷（最後收到 {st['last_received']}）：下面是中斷前的狀態，不代表現在沒有告警")
    return out


def _incidents_today(conn, now: datetime) -> dict:
    import incident
    day = now.strftime("%Y-%m-%d")
    rows = []
    for e in incident.list_events(conn):
        opened = str(e.get("started_at") or "")[:10] == day or str(e.get("created_at") or "")[:10] == day
        closed = str(e.get("closed_at") or "")[:10] == day
        if not (opened or closed):
            continue
        rows.append({"id": e["id"], "title": e["title"], "type": e["type"], "mode_label": e["mode_label"],
                     "started_at": e["started_at"], "closed_at": e.get("closed_at"),
                     "what": "今日開立・今日結案" if opened and closed else ("今日開立" if opened else "今日結案"),
                     "state": "進行中" if e.get("status") == "open" else "已結案", "link": f"/v2/incident/{e['id']}"})
    for i, r in enumerate(rows, 1):
        r["__no"] = i
    return {"status": "ok", "rows": rows, "n": len(rows), "as_of": now.strftime("%Y-%m-%d %H:%M:%S")}


def _changes_today(conn, now: datetime) -> dict:
    """1.1「今天的資產異動」。2.065.3（驗收疑點 6）：每日異動排程（快照／摘要信）失敗時，把失敗講在這一區的說明行，
    不可以只是「今天還沒有快照」——那會看起來像還沒到點，其實是壞了。"""
    import daily_digest
    out = _changes_today_core(conn, now)
    te = daily_digest.tick_error_label(daily_digest.tick_error(conn))
    if te:
        out["tick_error"] = te
        out["label"] = te + (f"；{out['label']}" if out.get("label") else "")
    return out


def _changes_today_core(conn, now: datetime) -> dict:
    import daily_digest
    import v2_views
    day = now.strftime("%Y-%m-%d")
    if not daily_digest.tables_exist(conn):
        return {"status": "uncollected", "label": "未收集（還沒有任何每日快照）", "rows": [], "n": None, "as_of": None}
    ds = daily_digest.dates(conn)
    if day not in ds:
        last = ds[0] if ds else None
        return {"status": "uncollected", "label": f"未收集（今天還沒有快照{'；最近一份 ' + last if last else ''}）",
                "rows": [], "n": None, "as_of": None, "latest": last}
    ch = daily_digest.diff_for(conn, day)
    snap = ch.get("snapshot") or {}
    if not ch.get("prev_date"):
        return {"status": "uncollected", "label": "無資料（只有今天一份快照，沒有前一份可比）", "rows": [], "n": None,
                "as_of": snap.get("created_at")}
    labels = dict(daily_digest.CATEGORIES)
    # 審查 P2-1：件數＝daily_digest.change_total（收集中斷是常態類，不算異動），收集中斷另列「收集中斷 M 台」
    counts = ch.get("counts") or {}
    rows = [{"key": k, "label": labels.get(k, k), "n": v, "link": f"/v2/changes?tab=daily&cat={k}&date={day}"}
            for k, v in counts.items() if v and k not in daily_digest.STATE_KEYS]
    for i, r in enumerate(rows, 1):
        r["__no"] = i
    fr = v2_views.changes_freshness(conn, ch)
    gap = int(counts.get("collect_gap", 0) or 0)
    out = {"status": "ok", "label": f"收集中斷 {gap} 台（另列，不算異動）" if gap else None, "rows": rows,
           "n": daily_digest.change_total(counts), "as_of": snap.get("created_at"), "prev_date": ch["prev_date"],
           "collect_gap": {"n": gap, "link": f"/v2/changes?tab=daily&cat=collect_gap&date={day}"}}
    if fr.get("no_new_input"):
        out.update(status="stale", label=fr["reason"] + (f"；收集中斷 {gap} 台（另列，不算異動）" if gap else ""))
    return out


def today(conn: sqlite3.Connection, now: datetime | None = None, record: bool = True, scope: str = "policy") -> dict:
    """scope：首頁告警的範圍（policy＝跟 Teams 同一個主機組政策範圍，預設；all＝看全部）。"""
    now = now or _now()
    out: dict = {"now": now.strftime("%Y-%m-%d %H:%M:%S"), "since": now.strftime("%Y-%m-%d 00:00"), "date": now.strftime("%Y-%m-%d")}

    def safe(key, fn):
        try:
            out[key] = fn()
        except Exception as exc:  # noqa: BLE001 - 一區算不出來不能讓首頁掛掉，但要講出來
            out[key] = {"status": "error", "label": f"無法計算（{type(exc).__name__}）", "rows": [], "n": None, "as_of": None}

    safe("alerts", lambda: _alerts_now(conn, now, scope))
    safe("incidents", lambda: _incidents_today(conn, now))
    safe("changes", lambda: _changes_today(conn, now))
    try:
        t = track(conn, now, record=record)
        out["track"] = {"status": "ok", "total": t["total"], "high": t["high"], "changes": {k: t["changes"][k] for k in ("prev_day", "new", "resolved", "reason")},
                        "as_of": t["generated_at"], "nodata": [c["label"] for c in t["categories"] if c["status"] != "ok"]}
    except Exception as exc:  # noqa: BLE001 - 同上
        out["track"] = {"status": "error", "label": f"無法計算（{type(exc).__name__}）", "total": None, "as_of": None}
    def _opening():
        import opening_peak
        return opening_peak.summary(conn, now)
    safe("opening", _opening)            # 2.045.0：開盤尖峰＝跳板機收盤後送來的 Aria 檔；今天還沒收到寫「尚未匯入」，不放數字
    st_txt = {"ok": None, "disabled": "未啟用", "uncollected": "未收集", "stale": "資料中斷", "error": "無法計算"}
    out["sources"] = [
        {"key": "alerts", "label": "告警（WhatsUp）", "as_of": out["alerts"].get("as_of"), "state": st_txt.get(out["alerts"]["status"])},
        {"key": "incidents", "label": "事件", "as_of": out["incidents"].get("as_of"), "state": st_txt.get(out["incidents"]["status"])},
        {"key": "changes", "label": "資產異動快照", "as_of": out["changes"].get("as_of"),
         "state": "來源未更新" if out["changes"]["status"] == "stale" else st_txt.get(out["changes"]["status"])},
        {"key": "track", "label": "追蹤處理", "as_of": out["track"].get("as_of"), "state": st_txt.get(out["track"]["status"])},
        {"key": "opening", "label": "開盤尖峰（Aria）", "as_of": out["opening"].get("collected_at"),
         "state": None if out["opening"]["status"] == "ok" else {"pending": "尚未匯入", "holiday": "休市"}.get(out["opening"]["status"], "無法計算")},
    ]
    return out
