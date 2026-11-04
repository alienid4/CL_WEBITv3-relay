"""3.1 業務系統（2.x 第四批）清單：一個業務系統一列，可用中文名稱、代號、使用單位、保管人、主機的資產名稱搜尋（藍圖任務 9）。

台數口徑＝系統履歷（blast_radius.system_map）同一套：該系統代號的登記列（SQL TRIM(api_id) 相同）
用 system_stats.collapse_machines 分組，退役不算台。所以清單上的「主機（台）」＝點進系統履歷看到的台數。
唯讀，不寫任何資料。
"""
from __future__ import annotations

import sqlite3
from typing import Any

_COLS = ("asset_serial, hostname, ip, os, environment, asset_status, asset_name, usage_unit, custodian, availability, "
         "physical_location, device_model, group_name, asset_purpose, hw_serial, api_id")


def _key(api_id: str | None) -> str:
    """跟 SQL TRIM() 一樣只去掉半形空白（system_map 用 TRIM(api_id) = ? 找主機）。"""
    return (api_id or "").strip(" ")


def listing(conn: sqlite3.Connection) -> dict[str, Any]:
    import blast_radius
    import system_stats

    by: dict[str, list] = {}
    for r in conn.execute(f"SELECT {_COLS} FROM hardware WHERE api_id IS NOT NULL AND TRIM(api_id) != ''"):
        by.setdefault(_key(r["api_id"]), []).append(r)
    bs = {_key(r["api_id"]): r for r in conn.execute(
        "SELECT api_id, name, ap_department, ap_owner, COALESCE(NULLIF(TRIM(sys_class_override),''), sys_class) AS c "
        "FROM business_system")}
    rows = []
    for a, rs in sorted(by.items()):
        g = system_stats.collapse_machines(rs)
        live = [k for k in g if not k.startswith("ret:")]
        b = bs.get(a)
        names = sorted({r["asset_name"] for r in rs if r["asset_name"]})
        name = (b["name"] if b and b["name"] else None) or (names[0] if names else a)
        units = sorted({r["usage_unit"] for r in rs if r["usage_unit"]})
        cust = sorted({r["custodian"] for r in rs if r["custodian"]})
        rooms = sorted({system_stats.normalize_location(g[k][0]["physical_location"]) or "未填" for k in live})
        odd = a != a.strip()                      # 代號含全形空白等不可見字元（半形空白以外）
        rows.append({
            "api_id": a, "name": name, "unit": "、".join(units[:3]) + ("…" if len(units) > 3 else ""),
            "custodian": "、".join(cust[:3]) + ("…" if len(cust) > 3 else ""),
            "ap_department": b["ap_department"] if b else None, "ap_owner": b["ap_owner"] if b else None,
            "sys_class": b["c"] if b else None, "class_group": blast_radius.class_group(b["c"] if b else None),
            "severity": "重大" if max((r["availability"] or 0) for r in rs) >= 3 else "一般",
            "hosts": len(live), "records": len(rs), "retired_records": sum(len(g[k]) for k in g if k.startswith("ret:")),
            "rooms": "、".join(rooms), "note": "代號含不可見空白字元，請於 CIA 更正" if odd else "",
            "search": " ".join([a, name, *names, *units, *cust, (b["ap_owner"] or "") if b else ""]).lower(),
        })
    return {"rows": rows, "total": len(rows), "hosts": sum(r["hosts"] for r in rows)}
