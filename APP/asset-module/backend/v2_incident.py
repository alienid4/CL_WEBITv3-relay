"""4.1 事件應變（2.x，2026-10-27）的「通報聯絡」：聯絡名單＋通知信草稿。

只讀 incident.event_view（範圍展開、系統、值班電話、聯絡人都是它算的），不另算範圍。
通知信只產生文字讓使用者自己複製寄出——系統不寄信。
"""
from __future__ import annotations

import csv
import io
import sqlite3
from typing import Any


def contacts(view: dict) -> list[dict[str, Any]]:
    """聯絡名單：每個受影響的業務系統一列（值班電話＋聯絡人）；沒有對到系統的主機另列（AP／SP 登記人）。"""
    rows = []
    for s in view["expand"]["systems"]:
        ppl = s.get("contacts") or []
        rows.append({"kind": "業務系統", "ref": s["api_id"], "name": s.get("name") or s["api_id"],
                     "level": s.get("level_label"), "machines": len(s.get("machines") or []),
                     "oncall_phone": s.get("oncall_phone") or "",
                     "people": "、".join(f"{p['name']}（{p.get('role') or ''}{'，' + p['phone'] if p.get('phone') else ''}）"
                                        for p in ppl),
                     "machine_names": "、".join((s.get("machine_names") or [])[:20])})
    for m in view["expand"]["machines"]:
        if m.get("api_ids"):
            continue
        rows.append({"kind": "主機（無業務系統）", "ref": m["asset_serial"], "name": m["name"], "level": None,
                     "machines": 1, "oncall_phone": "", "people": "、".join([*(m.get("ap") or []), *(m.get("sp") or [])]),
                     "machine_names": m["name"]})
    return rows


def mail_draft(view: dict) -> dict[str, str]:
    """通知信草稿（純文字，使用者複製後自己寄）。計畫停機與異常事件用不同的句型。"""
    ev, exp = view["event"], view["expand"]
    planned = (ev.get("category") or "incident") == "planned"
    sysl = view["expand"]["systems"]
    head = "【計畫停機通知】" if planned else "【異常事件通知】"
    if ev.get("mode") == "drill":
        head = "【演練・非真實事件】" + head       # 演練的通知信草稿必須明標，避免被當成真的通報
    subject = f"{head}{ev['title']}（{ev['started_at'][:16]}）"
    lines = ["各位好：", ""]
    if planned:
        lines.append(f"預計於 {ev['started_at'][:16]} 進行「{ev['title']}」，期間下列業務系統可能受影響，請協助確認並安排應變。")
    else:
        lines.append(f"{ev['started_at'][:16]} 發生「{ev['title']}」，下列業務系統可能受影響，請協助確認服務狀態並回報。")
    lines += ["", f"影響範圍：{exp.get('scope_text') or '（未設定）'}",
              f"範圍內主機（含停機設備本身）：{exp['counts']['machines']} 台（實體 {exp['counts']['physical']}、虛擬 {exp['counts']['vms']}）",
              f"範圍內主機所屬業務系統：{exp['counts']['systems']} 套", ""]
    for i, s in enumerate(sysl, 1):
        lines.append(f"{i}. {s['api_id']} {s.get('name') or ''}（{s.get('level_label') or ''}，{len(s.get('machines') or [])} 台）")
    if not sysl:
        lines.append("（範圍內沒有對到業務系統的主機）")
    if ev.get("note"):
        lines += ["", f"說明：{ev['note']}"]
    lines += ["", "如有疑問請與系統架構科聯繫。", "", "（本信由資產盤點模組產生草稿，請確認內容後再寄出）"]
    return {"subject": subject, "body": "\n".join(lines)}


def notify(conn: sqlite3.Connection, eid: int) -> dict:
    import incident

    view = incident.event_view(conn, eid)
    return {"contacts": contacts(view), "mail": mail_draft(view),
            "no_phone": [s["api_id"] for s in view["expand"]["systems"] if not s.get("oncall_phone")]}


def contacts_csv(conn: sqlite3.Connection, eid: int) -> str:
    rows = notify(conn, eid)["contacts"]
    buf = io.StringIO()
    w = csv.writer(buf)
    import export_safe

    w.writerow(["項次", "類別", "代號", "名稱", "等級", "主機數", "值班電話", "聯絡人", "主機"])
    for i, r in enumerate(rows, 1):
        w.writerow(export_safe.row([i, r["kind"], r["ref"], r["name"], r["level"] or "", r["machines"], r["oncall_phone"],
                                    r["people"], r["machine_names"]]))   # 防公式注入（審查 B1）
    return "﻿" + buf.getvalue()
