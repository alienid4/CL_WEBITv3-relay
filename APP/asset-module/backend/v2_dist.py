"""10.2 分布統計（2.x 第四批）：只放台數（用電與空間在 5.1 機房平面圖）。

· 機房分布／作業系統／環境別：2.1 主機查詢同一份 v2_hosts.machines（一台一列、退役不算），所以每個維度的加總
  ＝2.1「全部登記台數」。每格分「全部／非虛擬機／虛擬機／運作中」，數字＝下鑽列數。
  2.007.3（審查）：「虛擬機」改用 2.1「虛擬機」頁籤同一個定義（count_bridge 角色 vm），各維度加總＝2.1 虛擬機頁籤；
  原本用類型標記（is_vm／型號）判，221 差 131 台（3,310 vs 3,441），同一個詞兩個數字。
  「非虛擬機」＝全部－虛擬機（不叫「實體」：2.1／9.2 的實體設備是依機身／框算的單位數，不是一台一列）。
· 業務系統排行：3.1 業務系統清單同一份 v2_systems.listing（台數＝系統履歷）。一台可屬多個系統，排行不加總。
· 機房×環境（2.051.0，V66）：同一份 machines，所在地×環境別交叉計數；每格＝drill(site, 所在地, env=環境) 列數，
  列合計＝所在地維度「全部」、欄合計＝環境別維度「全部」、總和＝total（verify_numbers check_v2_dist 釘住）。
· 快速查詢（2.051.0，V66）：打主機名／IP／序號／業務系統，回這台落在各維度的哪一格（值與下鑽同一個 _dim_value）。
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
    out["matrix"] = matrix(ms)
    sy = v2_systems.listing(conn)["rows"]
    out["systems"] = sorted(({k: s[k] for k in ("api_id", "name", "class_group", "sys_class", "severity", "hosts", "records", "unit")}
                             for s in sy), key=lambda s: (-s["hosts"], s["api_id"]))
    return out


def matrix(ms: list[dict]) -> dict[str, Any]:
    """機房×環境：一列一個所在地、一欄一個環境別（kind＝全部）。值用 _dim_value，跟下鑽同一個判斷。"""
    cells: dict[str, dict[str, int]] = {}
    env_tot: dict[str, int] = {}
    for r in ms:
        site, env = _dim_value(r, "site"), _dim_value(r, "env")
        row = cells.setdefault(site, {})
        row[env] = row.get(env, 0) + 1
        env_tot[env] = env_tot.get(env, 0) + 1
    envs = sorted(env_tot, key=lambda e: (e == "未填", -env_tot[e], e))
    rows = [{"site": s, "all": sum(v.values()), "by_env": v} for s, v in cells.items()]
    rows.sort(key=lambda x: (x["site"] == "未填", -x["all"], x["site"]))
    return {"envs": envs, "env_totals": env_tot, "rows": rows, "total": len(ms)}


def lookup(conn: sqlite3.Connection, q: str, limit: int = 200) -> dict[str, Any]:
    """快速查詢：主機名／IP／序號／業務系統包含 q 的機器（同一份 machines，一台一列），回它在各維度的值。"""
    import v2_hosts

    ql = (q or "").strip().lower()
    if not ql:
        return {"q": q, "total": 0, "limit": limit, "rows": []}
    vk = _vm_keys(conn)
    hits = [r for r in v2_hosts.machines(conn)
            if ql in " ".join(str(r.get(k) or "") for k in ("name", "hostname", "ip", "serial", "system")).lower()]
    return {"q": q, "total": len(hits), "limit": limit,
            "rows": [{"key": r["key"], "name": r["name"], "ip": r["ip"], "system": r["system"],
                      "site": _dim_value(r, "site"), "env": _dim_value(r, "env"), "os": _dim_value(r, "os"),
                      "osver": _dim_value(r, "osver"), "vm": "虛擬機" if r["key"] in vk else "非虛擬機",
                      "status_label": r["status_label"]} for r in hits[:limit]]}


def drill(conn: sqlite3.Connection, dim: str, value: str, kind: str, _ms: list | None = None,
          _vk: set | None = None, env: str | None = None) -> dict[str, Any]:
    """_ms／_vk：呼叫端已經算好的主機清單與虛擬機集合（verify_numbers 逐格核對時共用一份，不必每格重算 2 秒）。
    env：機房×環境的格子（dim＝site 再加環境別條件）；None＝不限環境。"""
    import v2_hosts

    if dim not in DIMS or kind not in KINDS:
        raise ValueError("不認得的維度或清單")
    if env is not None and dim != "site":
        raise ValueError("環境條件只能配所在地")
    vk = _vk if _vk is not None else _vm_keys(conn)
    ms = [r for r in (_ms if _ms is not None else v2_hosts.machines(conn)) if _dim_value(r, dim) == value and _kind(r, kind, vk)
          and (env is None or _dim_value(r, "env") == env)]
    label = f"{DIMS[dim]}：{value}" + (f"×環境別：{env}" if env is not None else "") + f"（{KINDS[kind]}）"
    return {"label": label,
            "rows": [{"key": r["key"], "name": r["name"], "ip": r["ip"], "type": r["type"], "system": r["system"],
                      "where": r["where"], "env": r["env"], "status_label": r["status_label"],
                      "os_text": r.get("os_text") or "", "os_src": r.get("os_src") or ""} for r in ms]}
