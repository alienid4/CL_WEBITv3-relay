"""告警一鍵通知（2.044.2，需求 R16，使用者 2026-11-04）：告警列上的「通知」按鈕。

規則（需求表 R16＋開工前定案）：
  · 未按不送：只有人按「通知」→ 看過收件人與內容 → 按「確認送出」才排進佇列。
  · 送前先顯示收件人與內容（preview，不寫任何表）。
  · 送出一律走既有 teams_notify 佇列（啟用開關、Workflow 網址驗證、對外送出已確認、登入停用 fail-closed、重試／不確定、
    投遞紀錄全部沿用）；Teams 通知沒啟用、或目前送不出去（hold_reason）時直接拒絕，不假裝排進去。
  · 每次送出留一筆 alert_manual_notice（事件 ID、誰、何時、第幾次、投遞鍵）；狀態讀 teams_notify_delivery：
    已送出＝成功，放棄／過期／取消＝失敗，不確定＝不確定，其餘＝處理中。
  · 同一則告警通知過再按＝重送：要多勾「確認重送」才送，訊息標題寫「第 N 次（重送）」，紀錄也看得出是重送。
  · 收件人＝Teams 通知設定的那個頻道（或寄信模式的收件信箱）。告警聯絡資訊裡的 AP email 寫在訊息內容裡，
    這一版**不會**直接寄給 AP（收件規則還沒定，見需求表缺口 12）。
  · 權限：只能看的帳號沒有按鈕、後端也擋（403）；登入停用時不允許（需要真實身分留紀錄）。

資料函式不 commit，由路由與稽核同一交易提交。
"""
from __future__ import annotations

import json
import sqlite3

SCHEMA = """CREATE TABLE IF NOT EXISTS alert_manual_notice (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL, at TEXT NOT NULL, by TEXT NOT NULL,
    seq INTEGER NOT NULL, resend INTEGER NOT NULL DEFAULT 0,
    channel TEXT, target TEXT, delivery_key TEXT, title TEXT, summary TEXT)"""
RESULT = {"sent": "成功", "dead": "失敗", "expired": "失敗", "cancelled": "失敗", "skipped": "失敗",
          "uncertain": "不確定", "pending": "處理中", "sending": "處理中", "failed": "處理中（重試中）"}


class NotifyRefused(Exception):
    """送不出去（Teams 沒啟用、設定不完整、沒確認重送…）：路由回 409，訊息直接給畫面。"""


def ensure(conn) -> None:
    conn.execute(SCHEMA)


def _event(conn, event_id: int) -> dict:
    import wug_alert as wa
    if not wa._has(conn, "alert_event"):
        raise LookupError("沒有這筆告警事件")
    r = conn.execute("SELECT * FROM alert_event WHERE id = ?", (int(event_id),)).fetchone()
    if r is None:
        raise LookupError("沒有這筆告警事件")
    row = wa._row(r)
    import alert_contact
    alert_contact.attach(conn, [row])
    # R18（2.046.0）：磁碟類告警附這台最近一次 housekeeping 的候選摘要（唯讀掛鉤；摘要也進 preview_hash，掃描結果變了要重新預覽）
    if _is_disk(row):
        import dir_housekeeping
        # 2.046.1：先用事件已對應的資產，IP 唯一才退回 IP（不猜）
        row["hk_brief"] = dir_housekeeping.brief_for_event(conn, row.get("asset_serial") if row.get("match") == "matched" else None,
                                                           row.get("ip"))
    return row


def history(conn, event_id: int | None = None, limit: int = 500) -> list[dict]:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='alert_manual_notice'").fetchone():
        return []
    has_d = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='teams_notify_delivery'").fetchone() is not None
    q = ("SELECT n.*, d.status AS d_status, d.last_error AS d_error, d.sent_at AS d_sent FROM alert_manual_notice n "
         "LEFT JOIN teams_notify_delivery d ON d.event_key = n.delivery_key " if has_d else
         "SELECT n.*, NULL AS d_status, NULL AS d_error, NULL AS d_sent FROM alert_manual_notice n ")
    args: list = []
    if event_id is not None:
        q += "WHERE n.event_id = ? "
        args.append(int(event_id))
    rows = conn.execute(q + "ORDER BY n.id DESC LIMIT ?", (*args, max(1, min(int(limit), 5000)))).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["__no"] = r["id"]
        d["result"] = RESULT.get(r["d_status"] or "", "未知（投遞紀錄已清掉）" if r["delivery_key"] else "未知")
        d["kind_label"] = f"第 {r['seq']} 次（重送）" if r["resend"] else "第 1 次"
        out.append(d)
    return out


def _target(conn) -> tuple[dict, str, str | None]:
    """回 (Teams 設定, 收件人文字, 送不出去的原因或 None)。"""
    import teams_notify as tn
    try:
        cfg = tn.load_settings(conn)
    except tn.SettingsCorrupt as exc:
        return {}, "—", f"Teams 通知設定讀不出來：{exc}"
    host = tn.webhook_host(conn)
    label = (f"信箱 {host}" if host else "信箱（未設定）") if cfg.get("channel") == "email" else \
        (f"Teams 頻道（Workflow：{host}）" if host else "Teams 頻道（Workflow 網址未設定）")
    if not cfg.get("enabled"):
        return cfg, label, "Teams 通知沒有啟用（系統設定 → 通知設定 → Teams 通知）"
    return cfg, label, tn.hold_reason(conn, cfg)


DUR_PLACEHOLDER = "〔持續時間〕"
TITLE_MAX, LINE_MAX = 300, 400      # 跟 wug_alert.build_card 的截斷一致（跳脫後的長度）


def _fit(text: str, n: int) -> str:
    """把一行修到「跳脫後 ≤ n」：送出的卡片不會再被 build_card 截斷，預覽看到的就是實際送出的（2.046.1，GPT P1）。
    被修掉的寫「…」，不悄悄消失。"""
    import teams_notify as tn
    if len(tn.md_escape(text)) <= n:
        return text
    t = text
    while t and len(tn.md_escape(t + "…")) > n:
        t = t[:-1]
    return t + "…"


def _message(ev: dict, seq: int, user: str, dur: str | None = None) -> tuple[str, list[str]]:
    """訊息全文。dur 給值就用它取代持續時間（hash 用固定占位，見 _hash）。"""
    import wug_alert as wa
    what = ev["monitor"] if ev["monitor"] != wa.DEVICE_MONITOR else "設備"
    state = "未恢復" if ev["open"] else f"已恢復（{str(ev['ended_at'])[:16]}）"
    head = "📣 告警通知" + (f"（第 {seq} 次・重送）" if seq > 1 else "")
    title = f"{head}：{ev['who']}（{ev['ip'] or '無 IP'}）{what} {ev['wug_state'] or 'Down'}，{state}，已 {dur if dur is not None else wa._dur_txt(ev['duration_min'])}"
    lines = [
        f"業務系統：{(ev['api_id'] or '未登記') + ' ' + (ev['c_system_name'] if ev['c_system_name'] != '未提供' else '')}".rstrip(),
        f"分級：{ev['c_sys_class']}・AP 團隊：{ev['c_ap_team']}・單位：{ev['c_unit']}",
        f"AP email：{ev['c_email']}",
        f"開始 {ev['started_at']}・分級 {ev['tier_label']}・{ev['market'] or ''}".rstrip("・"),
        f"WhatsUp：{ev['device_name'] or '—'}・{ev['status_text'] or ev['wug_state'] or '—'}",
        f"請處理：{action_text(ev)}",
        f"通知人：{user}・告警事件 #{ev['id']}",
    ]
    return _fit(title, TITLE_MAX), [_fit(x, LINE_MAX) for x in lines]


_DISK_RE = ("disk", "磁碟", "space", "filesystem", "file system", "volume", "partition", "分割")


def _is_disk(ev: dict) -> bool:
    txt = f"{ev.get('monitor') or ''} {ev.get('status_text') or ''}".lower()
    return any(k in txt for k in _DISK_RE)


def action_text(ev: dict) -> str:
    """B5（2.044.3）：訊息裡的「請處理：…」。磁碟類先寫清理（附 housekeeping 候選摘要，R18），其他寫確認狀態並回報。"""
    if _is_disk(ev):
        return "請檢查並清理磁碟空間（housekeeping）；" + (ev.get("hk_brief") or "目錄清理：尚未掃描")
    if not ev.get("open"):
        return "告警已恢復，請確認原因並回報是否需要後續處理"
    return "請確認設備／服務狀態，處理後回報結果"


def recipient_text(label: str) -> str:
    """B4：畫面與紀錄上的收件人說明一律講清楚 AP email 不會收到。"""
    return f"會送到：{label}（AP email 只寫在內容裡，不會寄給 AP）"


def _hash(ev: dict, label: str, channel: str | None, seq: int, user: str) -> str:
    """A6（2.044.4）：hash＝實際要送出的訊息＋收件人＋通道。持續分鐘數每分鐘都變，換成固定占位 DUR_PLACEHOLDER 再算；
    其餘任何一個字（主機、IP、狀態、開始／恢復時間、裝置、開盤、五欄、第幾次）變了 hash 就不同。
    2.046.1（GPT P1）：對「最後真正送出去的卡片」（build_card 跳脫、截斷之後那份 JSON）算；_message 已先把每行修到不會被截。"""
    import hashlib
    import wug_alert as wa
    title, lines = _message(ev, seq, user, dur=DUR_PLACEHOLDER)
    card = wa.build_card(title, lines)
    body = json.dumps({"card": card, "to": label, "ch": channel}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:32]


def preview(conn, event_id: int, user: str) -> dict:
    """送出前給人看：收件人、內容、之前通知過幾次。不寫任何表。回 preview_hash，送出時要帶回來。"""
    ev = _event(conn, event_id)
    past = history(conn, event_id)
    seq = len(past) + 1
    cfg, label, hold = _target(conn)
    title, lines = _message(ev, seq, user)
    return {"event_id": ev["id"], "who": ev["who"], "recipient": recipient_text(label), "target": label, "can_send": hold is None,
            "hold_reason": hold, "title": title, "lines": lines, "seq": seq, "is_resend": seq > 1, "history": past,
            "contact": {k: ev["c_" + k] for k in ("ap_team", "unit", "system_name", "sys_class", "email")},
            "preview_hash": _hash(ev, label, cfg.get("channel"), seq, user),
            "note": "直接寄給 AP 要等收件規則定案（使用者決定）；這一版只送 Teams 通知設定的頻道。"}


def send(conn, event_id: int, user: str, resend: bool = False, preview_hash: str = "") -> dict:
    """確認送出：排進 teams_notify 佇列＋留一筆紀錄。**不 commit**。
    要帶 preview 回的 preview_hash；重算不同（收件人、聯絡資訊、告警狀態、次數在預覽後變了）就拒絕，請重新預覽。"""
    import teams_notify as tn
    import wug_alert as wa
    ev = _event(conn, event_id)
    past = history(conn, event_id)
    seq = len(past) + 1
    if seq > 1 and not resend:
        raise NotifyRefused(f"這則告警已經通知過 {len(past)} 次（最近 {past[0]['at']}，{past[0]['by']}），要再送請勾「確認重送」")
    cfg, label, hold = _target(conn)
    if hold:
        raise NotifyRefused(f"送不出去：{hold}")
    if not preview_hash or preview_hash != _hash(ev, label, cfg.get("channel"), seq, user):
        raise NotifyRefused("收件人或內容在預覽後變了，請關掉重新按「通知」預覽一次")
    ensure(conn)
    tn.ensure(conn)
    now = tn.now_taipei()
    title, lines = _message(ev, seq, user)
    key = f"manual:{ev['id']}:{seq}:{now.strftime('%Y%m%d%H%M%S%f')}"
    sev = "critical" if ev["tier"] == "red" else "warning"
    added = tn._enqueue(conn, key, "wug_manual", f"alert:{ev['id']}", sev, title, wa.build_card(title, lines), now)
    if not added:
        raise NotifyRefused("佇列裡已有同一筆（剛剛按過），請稍候看結果")
    conn.execute("INSERT INTO alert_manual_notice (event_id, at, by, seq, resend, channel, target, delivery_key, title, summary) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (ev["id"], tn._ts(now), user, seq, 1 if seq > 1 else 0, cfg.get("channel"), label, key, title[:300], "\n".join(lines)[:4000]))
    return {"event_id": ev["id"], "seq": seq, "is_resend": seq > 1, "recipient": label, "delivery_key": key,
            "result": RESULT["pending"], "title": title}


def counts(conn, event_ids: list[int]) -> dict[int, dict]:
    """告警列上顯示「已通知 N 次・最後結果」用：event_id → {n, last_result, last_at}。"""
    out: dict[int, dict] = {}
    if not event_ids:
        return out
    for h in history(conn, None, 5000):
        e = h["event_id"]
        if e not in out:
            out[e] = {"n": 0, "last_result": h["result"], "last_at": h["at"]}
        out[e]["n"] += 1
    return {k: v for k, v in out.items() if k in set(event_ids)}


def attach(conn, rows: list[dict]) -> list[dict]:
    """告警事件列加上 notified_n／notified_text（已通知幾次、最後結果）。"""
    try:
        c = counts(conn, [int(r["id"]) for r in rows if r.get("id") is not None])
    except sqlite3.Error:
        c = {}         # 紀錄表讀不到：只少「已通知」那一欄，畫面寫「—」
    for r in rows:
        x = c.get(r.get("id"))
        r["notified_n"] = x["n"] if x else 0
        r["notified_text"] = f"{x['n']} 次・{x['last_result']}（{str(x['last_at'])[5:16]}）" if x else ""
    return rows
