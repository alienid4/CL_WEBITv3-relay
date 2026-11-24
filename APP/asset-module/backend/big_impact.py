"""夜間速查「影響範圍大」自動提醒（Teams／信件，kind=big_impact）。

使用者 11-16：「受影響超過 3 台，自動發給值班的你，內容寫『請通知主管』」。
口徑＝night_desk 現有 impact.big（受影響開機中主機 > BIG_HOSTS、machine_key 去重），不自創。
發到現有那一組目標（使用者自己），**不發給主管本人**；主管聯絡只是寫在卡片上給他看。

規則：
- 只挑「今晚通知清單中、未恢復、impact.big 為真、impact 算得出來（reason 為空、hosts 有值）」的項目。
- event_key＝big:{item_id}（item_id 用 night_desk 的穩定編號）；source_ref＝big:{日期}:{machine_key}，
  同一台同一天最多一則（rep 項目換了也不會再發）。
- severity 一律 critical＝照既有規則不受靜默時段限制（影響範圍大屬緊急）；有效期用 EXPIRE_HOURS。
- 發之前再檢查一次仍未恢復（process_queue 呼叫 still_open）。
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from urllib.parse import quote

log = logging.getLogger("big_impact")

KIND = "big_impact"
MAX_SYSTEMS = 3
NO_MANAGER = "主管聯絡資料查不到"


def candidates(conn, now: datetime) -> list[dict]:
    """今晚通知清單中符合條件的項目（只讀）。"""
    import night_desk
    v = night_desk.notified(conn, None, now)
    out = []
    for it in v.get("items") or []:
        imp = it.get("impact") or {}
        if not it.get("open") or not imp.get("big"):
            continue
        if imp.get("reason") is not None or imp.get("hosts") is None:
            continue                                    # 算不出來不猜
        if not it.get("machine_key"):
            continue
        out.append(it)
    return out


def manager_text(contacts: list[dict]) -> str:
    """取 role 含「主管」且有姓名或電話的；沒有就寫查不到。"""
    got = []
    for c in contacts or []:
        if "主管" in str(c.get("role") or "") and (c.get("name") or c.get("phone")):
            got.append("・".join(x for x in (c.get("name"), c.get("phone")) if x))
    return "主管：" + "；".join(got) if got else NO_MANAGER


def _details(conn, it: dict) -> tuple[list[str], str]:
    """(業務系統名稱, 主管聯絡文字)；讀不到就空／查不到，不擋提醒本身。"""
    import night_desk
    try:
        w = night_desk._World(conn)
        imp = night_desk.impact(conn, w, it["machine_key"], it.get("platform"))
        names = [s.get("name") for s in imp.get("systems") or [] if s.get("name")]
        names = list(dict.fromkeys(names))
        mgr = manager_text(night_desk.contacts_of(conn, it["machine_key"], w))
        return names, mgr
    except Exception as exc:  # noqa: BLE001 - 細節讀不到不能讓緊急提醒發不出去；主管寫查不到、系統名略過
        log.error("影響範圍大提醒補細節失敗（%s）", type(exc).__name__)
        return [], NO_MANAGER


def build(conn, cfg: dict, it: dict, demo: bool = False) -> tuple[str, dict]:
    import notice_fmt as nf
    import teams_notify as tn
    tag = "【示範】" if demo else ""
    names, mgr = it["_demo"] if demo else _details(conn, it)
    imp = it["impact"]
    n_sys = int(imp.get("systems") or 0)
    sys_txt = f"{n_sys} 個業務系統"
    if names:
        sys_txt += "（" + "、".join(names[:MAX_SYSTEMS]) + ("等" if len(names) > MAX_SYSTEMS else "") + "）"
    first = f"{nf.EMOJI['critical']} {tag}影響範圍大，請通知主管"
    lines = [f"哪台：{it.get('name') or '—'}・{it.get('ip') or '—'}",
             f"什麼事：{it.get('title') or '—'}（{it.get('notified_at') or '—'} 通知）",
             f"影響：{imp['hosts']} 台／{sys_txt}",
             mgr]
    link = tn._link(cfg, "/v2/night?host=" + quote(str(it["machine_key"]), safe=":"))
    title = f"{tag}影響範圍大｜{it.get('name') or it.get('ip') or ''}"
    return title, tn._fit(nf.card(first, lines, link=link, color="Attention"))


def demo_item() -> dict:
    return {"machine_key": "demo-host-01", "name": "demo-esxi-01", "ip": "10.99.0.21", "title": "Ping 無回應（示範）",
            "notified_at": "2026-11-16 02:10:00", "platform": "esxi", "impact": {"hosts": 5, "systems": 4, "big": True, "reason": None},
            "_demo": (["示範系統甲", "示範系統乙", "示範系統丙", "示範系統丁"], NO_MANAGER)}


def enqueue(conn: sqlite3.Connection, cfg: dict, now: datetime) -> list[str]:
    """排進佇列（event_key 唯一＝重複 tick 不重發；同一台同一天最多一則）。"""
    import teams_notify as tn
    if not cfg.get("big_impact_notify"):
        return []
    try:
        items = candidates(conn, now)
    except Exception as exc:  # noqa: BLE001 - 速查讀不到不能拖垮投遞；下一分鐘再試，原因進日誌
        log.error("影響範圍大提醒讀夜間速查失敗（%s），下一輪再試", type(exc).__name__)
        return []
    # 以「夜班日」（中午到隔天中午）為單位去重：跨午夜的同一事件不會因日曆日換了而重發（獨立審查 P2）
    day = (now - timedelta(hours=12)).strftime("%Y-%m-%d")
    out = []
    for it in items:
        try:
            key, ref = f"big:{it['id']}", f"big:{day}:{it['machine_key']}"
            if conn.execute("SELECT 1 FROM teams_notify_delivery WHERE event_key = ? OR (kind = ? AND source_ref = ?)", (key, KIND, ref)).fetchone():
                continue
            title, card = build(conn, cfg, it)
            if tn._enqueue(conn, key, KIND, ref, "critical", title, card, now):
                out.append(key)
        except Exception as exc:  # noqa: BLE001 - 單一項目組卡/寫入失敗不能中斷整個 tick（獨立審查 P2）
            log.error("影響範圍大提醒排入失敗（%s），略過這一項", type(exc).__name__)
    conn.commit()
    return out


def still_open(conn, source_ref: str, now: datetime) -> bool | None:
    """送出前再檢查：True＝仍未恢復且仍算影響範圍大；False＝已恢復或不符；None＝讀不到（保留待送）。"""
    try:
        mk = source_ref.split(":", 2)[2]
        return any(it["machine_key"] == mk for it in candidates(conn, now))
    except Exception as exc:  # noqa: BLE001
        log.error("影響範圍大提醒送出前複查失敗（%s）", type(exc).__name__)
        return None


def preview(conn, cfg: dict, now: datetime) -> tuple[str, dict, dict]:
    """只讀；沒有符合的就回示範文字行。"""
    try:
        items = candidates(conn, now)
    except Exception:  # noqa: BLE001 - 預覽讀不到就給示範，數字區標讀不到
        items = []
    if items:
        title, card = build(conn, cfg, items[0])
        return title, card, {"count": len(items), "demo": False}
    title, card = build(conn, cfg, demo_item(), demo=True)
    return title, card, {"count": 0, "demo": True}
