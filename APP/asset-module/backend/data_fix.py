"""資料品質分數＋分派待補（v1.499.1，2026-10-23）。

使用者怎麼用：每週看一次「哪個單位、哪個系統的 CIA 缺什麼」，把那個單位的缺漏清單匯出 Excel 發給他補。

* 單位＝台（system_stats.machine_key），只看帳內（CIA）現役機器。退役、帳外（DYN-/VC-/AUTO-）不算。
* 每台逐項檢查（ITEMS），分數＝已填項 ÷ 應填項。應填項可在 app_settings `dq_required` 設定。
  「不適用」不算應填：VM 沒有機櫃 U、也不看型號是不是籠統描述。
* 負責單位用 CIA「使用單位」（usage_unit）分組。為什麼不是另外幾個：
    - 盤點單位（inventory_department）／擁有者（owner）在資料裡全部是同一個值（資訊架構部），分不出誰；
    - 保管者（custodian）是 SP 自己人，不是要補資料的人；
    - 業務系統的 AP 部門（business_system.ap_department）是「系統」屬性，掛在 APID 上——
      這個維度另外有「依系統」分組；CIA 這一筆由誰維護，看的是使用單位。
* 每週一份快照（dq_snapshot，ISO 週一筆），滿兩週就畫得出趨勢。快照在讀取時補（本週還沒有才寫）。
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3

import system_report
import system_stats

SET_REQUIRED = "dq_required"

#: (key, 中文, 只對實體機)
ITEMS = [
    ("room", "機房", False),
    ("rack_u", "機櫃U", True),
    ("owner", "負責人", False),
    ("custodian", "保管人", False),
    ("api_id", "APID", False),
    ("model", "型號（非籠統）", True),
    ("os", "OS", False),
    ("env", "環境", False),
    ("exist", "存在可信度（不只登記）", False),
]
ITEM_KEYS = [i[0] for i in ITEMS]
ITEM_LABEL = {k: lbl for k, lbl, _p in ITEMS}
PHYSICAL_ONLY = {k for k, _l, p in ITEMS if p}
NO_UNIT = "（使用單位未填）"
NO_APID = "（未填 APID）"


def required_items(conn) -> list[str]:
    try:
        r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (SET_REQUIRED,)).fetchone()
        v = json.loads(r[0]) if r and r[0] else None
    except (sqlite3.Error, ValueError, TypeError):  # 設定壞掉就用預設（全部九項），畫面顯示目前採用的項目
        v = None
    if not isinstance(v, list):
        return list(ITEM_KEYS)
    out = [k for k in ITEM_KEYS if k in v]
    return out or list(ITEM_KEYS)


def save_required(conn, keys: list[str]) -> list[str]:
    bad = [k for k in keys if k not in ITEM_KEYS]
    if bad or not keys:
        raise ValueError(f"項目不認得或是空的：{bad}")
    conn.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES (?,?)",
                 (SET_REQUIRED, json.dumps([k for k in ITEM_KEYS if k in keys])))
    conn.commit()
    return required_items(conn)


def _filled(v) -> bool:
    return v is not None and str(v).strip() != ""


def machines(conn) -> list[dict]:
    """帳內現役的每一台：各項有沒有填、缺哪些、分數。"""
    import manage_state
    import rack_layout

    req = required_items(conn)
    hw: dict[str, sqlite3.Row] = {r["asset_serial"]: r for r in conn.execute(
        "SELECT asset_serial, hostname, ip, os, environment, user_name, usage_unit, is_vm, device_model, "
        "api_id, rack_no FROM hardware")}
    names = {}
    try:
        names = {(r[0] or "").strip(): r[1] for r in conn.execute("SELECT api_id, name FROM business_system")}
    except sqlite3.Error:  # 系統名稱只是顯示用
        pass
    out = []
    for m in rack_layout.machines(conn):
        if m["retired"]:
            continue
        rows = [hw[s] for s in m["serials"] if s in hw]
        inb = [r for r in rows if not system_report.is_off_book(r["asset_serial"])]
        if not inb:
            continue
        first = lambda f: next((str(r[f]).strip() for r in inb if _filled(r[f])), None)  # noqa: E731
        is_vm = m["kind"] in ("vm",) or any(manage_state.is_vm_value(r["is_vm"], r["device_model"]) for r in inb)
        filled = {
            "room": m["room"] not in ("未填", None, ""),
            "rack_u": (m["kind"] == "rack" and bool(m["u_start"])) or m["kind"] == "removed",
            "owner": _filled(first("user_name")),
            "custodian": _filled(m.get("custodian")),
            "api_id": bool(m["api_ids"]),
            "model": bool(m["model"]) and not m["spec_generic"],
            "os": _filled(first("os")),
            "env": _filled(first("environment")),
            "exist": m["exist_level"] not in ("cia_only", "none", None),
        }
        applicable = [k for k in req if not (is_vm and k in PHYSICAL_ONLY)]
        missing = [k for k in applicable if not filled[k]]
        unit = first("usage_unit") or NO_UNIT
        out.append({
            "key": m["key"], "serial": m["asset_serial"], "hostname": first("hostname") or m["name"],
            "ip": m["ip"], "room": m["room"], "rack": m["rack_raw"], "model": m["model"],
            "api_ids": m["api_ids"], "systems": "、".join(f"{a} {names.get(a) or ''}".strip() for a in m["api_ids"]) or None,
            "unit": unit, "is_vm": is_vm,
            "required": len(applicable), "filled": len(applicable) - len(missing),
            "missing": missing, "missing_text": "、".join(ITEM_LABEL[k] for k in missing),
            "score": round(100 * (len(applicable) - len(missing)) / len(applicable), 1) if applicable else 100.0,
        })
    out.sort(key=lambda x: (x["score"], x["serial"]))
    return out


def _group(ms: list[dict], keyf) -> list[dict]:
    g: dict[str, dict] = {}
    for m in ms:
        for k in keyf(m):
            x = g.setdefault(k, {"key": k, "machines": [], "required": 0, "filled": 0, "missing_machines": 0,
                                 "missing_by_item": {i: 0 for i in ITEM_KEYS}})
            x["machines"].append(m["key"])
            x["missing_machines"] += bool(m["missing"])
            x["required"] += m["required"]
            x["filled"] += m["filled"]
            for i in m["missing"]:
                x["missing_by_item"][i] += 1
    out = []
    for x in g.values():
        x["machine_count"] = len(x["machines"])
        x["missing_total"] = sum(x["missing_by_item"].values())
        x["score"] = round(100 * x["filled"] / x["required"], 1) if x["required"] else 100.0
        out.append(x)
    out.sort(key=lambda r: (-r["missing_total"], r["key"]))
    return out


def iso_week(d: _dt.date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def overview(conn, now: _dt.datetime | None = None, snapshot: bool = True) -> dict:
    now = now or _dt.datetime.now()
    ms = machines(conn)
    req = required_items(conn)
    total_req = sum(m["required"] for m in ms)
    total_filled = sum(m["filled"] for m in ms)
    by_item = {i: sum(1 for m in ms if i in m["missing"]) for i in ITEM_KEYS}
    units = _group(ms, lambda m: [m["unit"]])
    systems = _group(ms, lambda m: m["api_ids"] or [NO_APID])
    out = {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "items": [{"key": k, "label": lbl, "physical_only": p, "required": k in req} for k, lbl, p in ITEMS],
        "machine_count": len(ms),
        "missing_machines": sum(1 for m in ms if m["missing"]),
        "required_total": total_req, "filled_total": total_filled,
        "score": round(100 * total_filled / total_req, 1) if total_req else 100.0,
        "missing_by_item": by_item,
        "machines": ms, "units": units, "systems": systems,
    }
    if snapshot:
        take_snapshot(conn, out, now)
    out["trend"] = trend(conn)
    return out


def take_snapshot(conn, ov: dict, now: _dt.datetime) -> bool:
    """本週（ISO 週）還沒有快照才寫一份。回 True＝這次有寫。"""
    wk = iso_week(now.date())
    try:
        if conn.execute("SELECT 1 FROM dq_snapshot WHERE week = ?", (wk,)).fetchone():
            return False
        conn.execute(
            "INSERT INTO dq_snapshot (week, taken_at, machine_count, missing_machines, required_total, "
            "filled_total, score, by_item_json, by_unit_json) VALUES (?,?,?,?,?,?,?,?,?)",
            (wk, now.strftime("%Y-%m-%d %H:%M:%S"), ov["machine_count"], ov["missing_machines"],
             ov["required_total"], ov["filled_total"], ov["score"],
             json.dumps(ov["missing_by_item"], ensure_ascii=False),
             json.dumps({u["key"]: u["score"] for u in ov["units"]}, ensure_ascii=False)))
        conn.commit()
        return True
    except sqlite3.Error:  # 舊庫還沒建表（db.py init 前）：只是沒有趨勢，分數本身照算
        return False


def trend(conn) -> list[dict]:
    try:
        return [dict(r) for r in conn.execute(
            "SELECT week, taken_at, machine_count, missing_machines, score FROM dq_snapshot ORDER BY week")]
    except sqlite3.Error:  # 同上：沒有表＝沒有趨勢
        return []
