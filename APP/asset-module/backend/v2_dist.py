"""10.2 分布統計（2.x 第四批）：只放台數（用電與空間在 5.1 機房平面圖）。

· 機房分布／作業系統／環境別：2.1 主機查詢同一份 v2_hosts.machines（一台一列、退役不算），所以每個維度的加總
  ＝2.1「全部登記台數」。每格分「全部／非虛擬機／虛擬機／運作中」，數字＝下鑽列數。
  2.007.3（審查）：「虛擬機」改用 2.1「虛擬機」頁籤同一個定義（count_bridge 角色 vm），各維度加總＝2.1 虛擬機頁籤；
  原本用類型標記（is_vm／型號）判，221 差 131 台（3,310 vs 3,441），同一個詞兩個數字。
  「非虛擬機」＝全部－虛擬機（不叫「實體」：2.1／9.2 的實體設備是依機身／框算的單位數，不是一台一列）。
· 業務系統排行：3.1 業務系統清單同一份 v2_systems.listing（台數＝系統履歷）。一台可屬多個系統，排行不加總。
唯讀。
"""
from __future__ import annotations

import sqlite3
from typing import Any

DIMS = {"site": "所在地", "os": "作業系統大類", "osver": "作業系統版本", "env": "環境別"}
KINDS = {"all": "全部", "phys": "非虛擬機", "vm": "虛擬機", "online": "運作中"}


def _dim_value(r: dict, dim: str) -> str:
    if dim == "os":
        return (r["badge"] or {}).get("os_class") or "未知"
    if dim == "osver":
        return r.get("os_ver") or "未知"
    return r[dim] or "未填"


def _vm_keys(conn) -> set[str]:
    """2.1「虛擬機」頁籤同一個定義（v2_views.vm_keys，主機履歷同一支）。"""
    import v2_views
    return v2_views.vm_keys(conn)


def _kind(r: dict, kind: str, vm_keys: set[str]) -> bool:
    vm = r["key"] in vm_keys
    return {"all": True, "phys": not vm, "vm": vm, "online": r["status"]["key"] == "online"}[kind]


def summary(conn: sqlite3.Connection) -> dict[str, Any]:
    import v2_hosts
    import v2_systems

    ms = v2_hosts.machines(conn)
    vk = _vm_keys(conn)
    out: dict[str, Any] = {"total": len(ms), "dims": {}}
    for dim in DIMS:
        g: dict[str, dict] = {}
        for r in ms:
            b = g.setdefault(_dim_value(r, dim), {"value": _dim_value(r, dim), **{k: 0 for k in KINDS}, "measured": 0})
            for k in KINDS:
                if _kind(r, k, vk):
                    b[k] += 1
            if r.get("os_src") == "實測":      # 其中有幾台的作業系統是納管後系統收到的（不是 CIA 登記的）
                b["measured"] += 1
        out["dims"][dim] = sorted(g.values(), key=lambda x: (-x["all"], x["value"]))
    # 有機櫃資料的機房（5.1 機房平面圖看得到的那些）：只有這些在畫面上給「用電／空間 →5.1」連結
    import rack_layout
    out["dc_rooms"] = sorted({m["room"] for m in rack_layout.machines(conn) if m.get("kind") == "rack" and m.get("room")})
    sy = v2_systems.listing(conn)["rows"]
    out["systems"] = sorted(({k: s[k] for k in ("api_id", "name", "class_group", "sys_class", "severity", "hosts", "records", "unit")}
                             for s in sy), key=lambda s: (-s["hosts"], s["api_id"]))
    return out


def drill(conn: sqlite3.Connection, dim: str, value: str, kind: str, _ms: list | None = None,
          _vk: set | None = None) -> dict[str, Any]:
    """_ms／_vk：呼叫端已經算好的主機清單與虛擬機集合（verify_numbers 逐格核對時共用一份，不必每格重算 2 秒）。"""
    import v2_hosts

    if dim not in DIMS or kind not in KINDS:
        raise ValueError("不認得的維度或清單")
    vk = _vk if _vk is not None else _vm_keys(conn)
    ms = [r for r in (_ms if _ms is not None else v2_hosts.machines(conn)) if _dim_value(r, dim) == value and _kind(r, kind, vk)]
    return {"label": f"{DIMS[dim]}：{value}（{KINDS[kind]}）",
            "rows": [{"key": r["key"], "name": r["name"], "ip": r["ip"], "type": r["type"], "system": r["system"],
                      "where": r["where"], "env": r["env"], "status_label": r["status_label"],
                      "os_text": r.get("os_text") or "", "os_src": r.get("os_src") or ""} for r in ms]}
