"""2.1 主機查詢（2.x，2026-10-27）：合併舊的資產查詢（逐筆）、主機清單（一台一列）、掃描結果。

預設一台一列（machine_key，跟值班卡／主機履歷同一套分組）；切換「顯示每筆登記」看同一台的每一筆。
狀態一律用 existence.compute（偵測狀態）那一份，類型用 v2_views.type_badge，不另算。

2.003.1（一致性）：「實體設備／虛擬機／管理口」三個頁籤的單位與數字＝對帳橋（count_bridge）的試算，
跟 1.1 今日概況、9.2 數量對帳同一份——同一個詞在相鄰頁面必須是同一個數字。
  實體設備＝count_bridge.TRIAL「physical」的每一個部分（伺服器／儲存／網路／其他、LPAR 依機身、一體機依框、
           同箱元件依框、疑似登記錯誤、ESXi、有機櫃的找不到本體管理口），一列＝對帳橋算一台的那個單位；
  虛擬機  ＝角色 vm 的每一筆；管理口＝角色 mgmt_attached＋mgmt_orphan（對帳橋「管理口」試算）。
全部登記台數是一台一列（machine_key），包含對帳橋不算進實體設備／主機的：帳外待確認、無法判斷、端點、
重複登記只留一列等，所以不等於上面三個頁籤相加（畫面 InfoNote 講明）。
頁籤：全部登記台數｜實體設備｜虛擬機｜管理口｜核心系統｜逾期未偵測｜未偵測到（僅 CIA 登記）｜未登錄 CIA｜已納管；每個頁籤的筆數＝列數（verify_numbers 核）。
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

import fleet_cache as _fleet_cache

TABS = [("all", "全部登記台數"), ("physical", "實體設備"), ("vm", "虛擬機"), ("mgmt", "管理口"), ("core", "核心系統"),
        ("stale", "逾期未偵測"), ("missing", "未偵測到（僅 CIA 登記）"), ("no_cia", "未登錄 CIA"), ("onboarded", "已納管")]
#: 這三個頁籤一列＝對帳橋的一個單位（不是一台一列）
BRIDGE_TABS = {"physical": "physical", "vm": None, "mgmt": "mgmt"}


def _status(m: dict | None) -> dict:
    import v2_views

    if not m:
        return {"key": "unknown", "label": "無資料（不在偵測範圍）"}
    return v2_views.status_of(m)


def in_tab(r: dict, tab: str) -> bool:
    return {"all": True, "core": r["core"], "stale": r["stale"], "missing": r["status"]["key"] == "missing",
            "no_cia": not r["has_cia"], "onboarded": r["onboarded"]}[tab]


def bridge_units(items: list[dict], trial_key: str) -> list[dict]:
    """對帳橋某個試算（physical／hosts／mgmt）拆成「一個單位一列」：跟 count_bridge.measure 同一套算法
    （units 照角色單位、frames 依框、rack 只算有機櫃的），所以列數＝試算值。回 [{role, how, unit, items}]。"""
    import count_bridge as cb

    parts = next(p for k, _, _, p in cb.TRIAL if k == trial_key)
    out = []
    for role, how in parts:
        want = {how} if how in cb.RACK_STATUS else set()
        h = "units" if want else how
        xs = [x for x in items if x["role"] == role and (not want or x["rack_status"] in want)]
        if h == "os":
            xs = [x for x in xs if x["os_instance"]]
            h = "rows"
        if h == "units":
            h = {"rows": "rows", "frames": "frames", "none": "none"}[cb.ROLE_UNITS_BY[role]]
        if h == "none":
            continue
        if h == "frames":
            by: dict[str, list] = {}
            for x in xs:
                by.setdefault(x["frame"], []).append(x)
            out += [{"role": role, "how": how, "unit": f"{role}:{f}", "items": g} for f, g in by.items()]
        else:
            out += [{"role": role, "how": how, "unit": f"{role}:{x['asset_serial']}", "items": [x]} for x in xs]
    return out


def bridge_rows(conn: sqlite3.Connection, ms: list[dict], items: list[dict], tab: str) -> list[dict]:
    """實體設備／虛擬機／管理口頁籤：一列＝對帳橋的一個單位，欄位沿用那一台（machine_key）的主機列。"""
    import count_bridge as cb

    by_key = {r["key"]: r for r in ms}
    if tab == "vm":
        units = [{"role": "vm", "how": "rows", "unit": f"vm:{x['asset_serial']}", "items": [x]} for x in items if x["role"] == "vm"]
    else:
        units = bridge_units(items, BRIDGE_TABS[tab])
    out = []
    for u in units:
        x = u["items"][0]
        base = by_key.get(x["machine_key"]) or {
            "key": x["machine_key"], "name": x["hostname"] or x["asset_serial"], "ip": x["ip"], "serial": x["asset_serial"],
            "badge": {"code": "?", "label": "無資料", "vm": False}, "type": "無資料", "system": "", "api_ids": [], "core": False,
            "where": "", "room": None, "rack": None, "status": {"key": "unknown", "label": "無資料"}, "status_label": "無資料",
            "last_seen": None, "onboarded": False, "stale": False, "has_cia": x["side"] == "CIA", "records": 1, "retired": False}
        row = {**base, "uid": u["unit"], "role": u["role"], "role_label": cb.ROLE_LABEL[u["role"]],
               "unit_records": len(u["items"]), "side": x["side"]}
        if x.get("rack_status") == "rack" and x.get("rack"):      # 這個單位自己的機櫃位置（對帳橋同一筆），與 5.1 同口徑
            row.update(room=x.get("room"), rack=x["rack"], where=f"{x.get('room') or ''} {x['rack']}".strip() + (f"・{x['u']}U" if x.get("u") else ""))
        out.append(row)
    return out


def machines(conn: sqlite3.Connection, include_retired: bool = False) -> list[dict[str, Any]]:
    """一台一列（跨請求快取：資料庫沒變不重算，2.1 換頁／排序不用每次 1 秒多）。"""
    return _machines_cached(conn, include_retired)


@_fleet_cache.cached   # 跨請求快取：資料庫沒變就不重算（fleet_cache）
def _machines_cached(conn: sqlite3.Connection, include_retired: bool) -> list[dict[str, Any]]:
    return _machines(conn, include_retired)


_OSVER_OTHER = "其他（非 AIX／Windows／Linux）"
_RE_AIX_LEVEL = re.compile(r"(?<!\d)([5-7])(\d)00-\d{2}(?:-\d{2})?(?:-\d{4})?")      # oslevel -s：7200-05-09-2446 → 7.2


def os_version_of(conn_measured: dict | None, group: list, badge: dict) -> tuple[str, str, str]:
    """一台機器的「作業系統版本」統計值（2.007.51，使用者：「AIX／WINDOWS／LINUX 三大類，不用細分 LINUX」）。
    回 (版本標籤, 來源, 原始文字)。來源＝「實測」（納管後系統收到的 OS）或「登記」（CIA）——
    使用者 2026-10-29：「納管以後資訊一律以系統查到的為主，CIA 沒查到準確」，所以有實測就用實測。
    Linux 只算一行不分發行版；Windows／AIX 分版本（Windows Server 2012 與 2012 R2 分開）。"""
    import collected_os
    import v2_views

    m = None
    for r in group:
        m = (conn_measured or {}).get(r["asset_serial"])
        if m:
            break
    fam = collected_os.apply_os_class(badge.get("os_class") or "", m["platform"] if m else None)
    if fam == "未知":
        return "未知（沒有作業系統資料）", "登記", ""
    if fam not in ("AIX", "Windows", "Linux"):
        return _OSVER_OTHER, "登記", ""
    if m:
        text = collected_os.display_text(m["platform"], m.get("os"))
    else:
        text = next((str(r["os"]).strip() for r in group if str(r["os"] or "").strip()), "")
    src = "實測" if m else "登記"
    if fam == "Linux":
        return "Linux", src, text
    if fam == "AIX":
        lv = _RE_AIX_LEVEL.search(text)
        if lv:
            return f"AIX {lv.group(1)}.{lv.group(2)}", src, text
        dot = re.search(r"(?<!\d)([5-7])\.(\d)(?!\d)", text)         # AIX 7.1.5.0 → 7.1
        return (f"AIX {dot.group(1)}.{dot.group(2)}" if dot else "AIX（版本不明）"), src, text
    lab = v2_views.os_logo("WIN", text, "")[2]
    if re.search(r"\br2\b", text, re.I) and lab in ("Windows Server 2012", "Windows Server 2008"):
        lab += " R2"
    return (lab if lab not in ("Windows", "Windows Server") else "Windows（版本不明）"), src, text


def _machines(conn: sqlite3.Connection, include_retired: bool = False) -> list[dict[str, Any]]:
    """一台一列。"""
    import existence
    import oncall
    import rack_layout
    import system_stats
    import v2_views

    groups, of = oncall._groups(conn)
    vk = v2_views.vm_keys(conn)
    ex = existence.compute(conn)["machines"]
    ex_by_serial = {s: m for m in ex for s in m["serials"]}
    names = v2_views._sysnames(conn)
    import collected_os
    measured = collected_os.measured_map(conn)         # 納管後系統收到的 OS（有就以它為準）
    core_apis = {r["api_id"] for r in conn.execute("SELECT api_id, sys_class, sys_class_override FROM business_system")
                 if "核心" in ((r["sys_class_override"] or r["sys_class"]) or "")}
    out = []
    for k, g in groups.items():
        retired = k.startswith("ret:")
        if retired and not include_retired:
            continue
        rep = g[0]
        m = next((ex_by_serial.get(r["asset_serial"]) for r in g if r["asset_serial"] in ex_by_serial), None)
        apis = sorted({(r["api_id"] or "").strip() for r in g if (r["api_id"] or "").strip()})
        # 位置取「有機櫃位置的那一筆」（同一台多筆登記時代表筆可能是收集來的、沒填機房機櫃；2.004.0 修正：
        # 原本一律看代表筆，板橋 18 台在 5.1 有機櫃、在 2.1 卻顯示「未填」）
        loc = next((r for r in g if rack_layout.parse_rack(r["rack_no"])["kind"] == "rack"), rep)
        pr = rack_layout.parse_rack(loc["rack_no"])
        room = system_stats.normalize_location(loc["physical_location"])
        where = (f"{room or ''} {pr['rack']}".strip() + (f"・{pr['u_start']}U" if pr.get("u_start") else "")) \
            if pr["kind"] == "rack" else ("虛擬機" if pr["kind"] == "vm" else (room or ""))
        badge = v2_views.with_vm(v2_views.type_badge(rep), k, vk)   # 虛擬機＝對帳橋角色 vm（全站一個定義）
        osv, osv_src, osv_text = os_version_of(measured, g, badge)
        out.append({
            "os_ver": osv, "os_src": osv_src, "os_text": osv_text,
            "key": k, "name": v2_views.name_of(rep), "ip": rep["ip"], "serial": rep["asset_serial"], "badge": badge,
            "type": f"{badge.get('version_label') or badge['label']}{'（' + badge.get('vm_label', '虛擬機') + '）' if badge['vm'] else ''}",
            "system": "、".join(f"{a} {names.get(a) or ''}".strip() for a in apis), "api_ids": apis,
            "core": bool(set(apis) & core_apis), "where": where,
            "room": room if pr["kind"] == "rack" else None, "rack": pr["rack"] if pr["kind"] == "rack" else None, "status": _status(m),
            "status_label": _status(m)["label"], "last_seen": (m or {}).get("last_seen_any"),
            "onboarded": bool((m or {}).get("onboarded")), "stale": _status(m)["key"] == "stale",
            "has_cia": bool((m or {}).get("has_cia")), "records": len(g), "retired": retired,
            # 10.2 分布統計用（2.006.0）：所在地（不限有機櫃位置）與環境別，取有值的第一筆
            "site": system_stats.normalize_location(next((r["physical_location"] for r in g if (r["physical_location"] or "").strip()), None)) or "未填",
            "env": next(((r["environment"] or "").strip() for r in g if (r["environment"] or "").strip()), "") or "未填",
        })
    # 2.006.4（找碴 C6）：資料不完整的列排到最後——名稱是括號說明、名稱就是 IP、或類型／系統／位置全是無資料。
    # 只改排列順序，不改任何台數（verify_numbers 的頁籤筆數照舊）。
    for r in out:
        r["junk"] = is_junk(r)
    out.sort(key=lambda r: (r["retired"], r["junk"], r["name"].lower()))
    return out


_RE_BARE_IP = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def is_junk(r: dict) -> bool:
    """資料不完整的一列（排序用）：括號開頭的名稱、名稱就是 IP、類型／業務系統／位置三項都沒有。"""
    n = (r.get("name") or "").strip()
    if not n or n[0] in "(（[【" or _RE_BARE_IP.match(n):
        return True
    return (r.get("badge") or {}).get("code") == "?" and not r.get("system") and not r.get("where")


#: 2.1 畫面實際用到的欄位（2.006.4，找碴 C8：原本整列回傳，一次 2.9 MB）
LIST_FIELDS = ("uid", "no", "key", "name", "ip", "serial", "type", "system", "where", "room", "rack", "status_label",
               "onboarded", "has_cia", "records", "role_label", "unit_records", "vmware")


def _slim(r: dict) -> dict:
    return {k: r[k] for k in LIST_FIELDS if k in r}


def registrations(conn: sqlite3.Connection, include_retired: bool = False) -> list[dict[str, Any]]:
    """顯示每筆登記：同一台的每一筆 CIA／其他來源登記各一列（欄位跟舊資產查詢一樣是登記原值）。"""
    import oncall
    import v2_views

    groups, _ = oncall._groups(conn)
    out = []
    for k, g in groups.items():
        if k.startswith("ret:") and not include_retired:
            continue
        mname = v2_views.name_of(g[0])
        for r in g:
            s = r["asset_serial"] or ""
            src = "DY 動態資產" if s.startswith("DYN-") else "vCenter／RVTools" if s.startswith("VC-") else \
                "自動建立" if s.startswith("AUTO-") else "CIA"
            out.append({"key": k, "machine": mname, "serial": s, "source": src, "hostname": r["hostname"], "ip": r["ip"],
                        "os": r["os"], "status": r["asset_status"], "api_id": r["api_id"], "rack": r["rack_no"],
                        "model": r["device_model"], "location": r["physical_location"]})
    return out


OS_SOURCES = {"facts": "OS 欄有值", "guessed": "OS 為掃描推測（沒有真 OS）", "unknown": "OS 欄空白（含退役）"}


def _has_real_os(os_value) -> bool:
    """跟 manage_state.composition 的 has_real_os 同一條規則：非空且不是字面 N/A。"""
    return bool(os_value) and str(os_value).strip().upper() != "N/A"


def os_filtered_records(conn: sqlite3.Connection, os_source: str | None = None, os_class: str | None = None) -> list[dict]:
    """對帳頁「OS 欄有值／掃描推測／未知／平台」那幾列的下鑽：逐筆登記，筆數＝對帳頁該列數字（加總＝下鑽）。
    · facts／guessed：現役登記（與 composition.os_from_facts／os_guessed 同母體、同判定）。
    · unknown：data_quality.os_unknown 的母體是整張 hardware（含退役）、判定是 os 空白，所以這裡也含退役。
    · os_class：現役登記，分類走 asset_classify.classify_all（與首頁 by_os_class 同一支）。"""
    if os_source and os_source not in OS_SOURCES:
        raise ValueError(f"不認得的 OS 來源：{os_source}")
    recs = registrations(conn, include_retired=(os_source == "unknown"))
    if os_source == "facts":
        recs = [r for r in recs if _has_real_os(r["os"])]
    elif os_source == "guessed":
        recs = [r for r in recs if not _has_real_os(r["os"])]
    elif os_source == "unknown":
        recs = [r for r in recs if not str(r["os"] or "").strip()]
    if os_class:
        import asset_classify

        want = {x.strip() for x in os_class.split(",") if x.strip()}
        cls = asset_classify.classify_all(conn)
        recs = [r for r in recs if cls.get(r["serial"], {}).get("os_class") in want]
    for i, r in enumerate(recs, 1):
        r["no"] = i
        r.pop("model", None)
        r.pop("location", None)
    return recs


_IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def sort_key(v):
    """跟前端 useSort 同一套比較：IP 依數值、數字依數值、其他依文字（空值另外排最後，見 page_rows）。"""
    if isinstance(v, bool):
        return (1, int(v), "")
    if isinstance(v, (int, float)):
        return (1, v, "")
    s = str(v)
    if _IPV4.match(s):
        n = 0
        for p in s.split("."):
            n = n * 256 + int(p)
        return (0, n, "")
    try:
        return (1, float(s), "")
    except ValueError:
        return (2, 0, s.lower())


def page_rows(rows: list[dict], sort: str = "no", desc: bool = False, page: int = 1, size: int = 0) -> tuple[list[dict], int]:
    """伺服器端排序＋分頁（2.006.4，找碴 C7／C8）：空值永遠排最後；size=0＝全部（匯出、內部核數用）。"""
    sort = "no" if sort in ("", "__no") else sort
    full = [r for r in rows if r.get(sort) not in (None, "")]
    empty = [r for r in rows if r.get(sort) in (None, "")]
    full.sort(key=lambda r: sort_key(r[sort]), reverse=desc)
    out = full + empty
    if size and size > 0:
        page = max(1, page)
        out = out[(page - 1) * size: page * size]
    return out, len(rows)


def listing(conn: sqlite3.Connection, tab: str = "all", per_record: bool = False, q: str | None = None,
            page: int = 1, size: int = 0, sort: str = "no", desc: bool = False,
            os_source: str | None = None, os_class: str | None = None) -> dict:
    """畫面一次要的：頁籤筆數＋目前頁籤的列。實體設備／虛擬機／管理口的筆數＝對帳橋試算（同一份 items）。
    page／size／sort／desc＝伺服器端分頁排序（size=0 回全部；verify_numbers 用全部核「頁籤筆數＝列數」）。"""
    import count_bridge as cb

    if tab not in dict(TABS):
        raise ValueError(f"不認得的頁籤：{tab}")
    ms = machines(conn)
    for r in ms:
        r["uid"] = r["key"]
    cbo = cb.compute(conn)
    items = cbo["items"]
    trial = {t["key"]: t["value"] for t in cbo["summary"]["trial"]}
    vm_units = cb.measure(items, "vm")
    counts = {t: sum(1 for r in ms if in_tab(r, t)) for t, _ in TABS if t not in BRIDGE_TABS}
    counts.update({"physical": trial["physical"], "vm": vm_units, "mgmt": trial["mgmt"]})
    # 項次（找碴 C19）：預設排序下的固定編號，篩選、換排序、匯出都沿用同一個號碼。
    # 一台一列的頁籤用「全部登記台數」裡的位置（同一台在每個頁籤同一個號碼）；對帳橋頁籤一列是一個單位，在該頁籤內編號。
    for i, r in enumerate(ms, 1):
        r["no"] = i
        r["vmware"] = (r.get("badge") or {}).get("code") == "ESX" or (r.get("badge") or {}).get("os_class") == "虛擬化平台"
    rows = bridge_rows(conn, ms, items, tab) if tab in BRIDGE_TABS else [r for r in ms if in_tab(r, tab)]
    if tab in BRIDGE_TABS:
        rows.sort(key=lambda r: (r.get("junk", True), (r.get("name") or "").lower()))
        for i, r in enumerate(rows, 1):
            r["no"] = i
    if q:
        ql = q.strip().lower()
        rows = [r for r in rows if ql in f"{r['name']} {r['ip'] or ''} {r['serial']} {r['system']} {r['where']}".lower()]
    roles = {}
    for x in items:
        roles[x["role"]] = roles.get(x["role"], 0) + 1
    shown, total = page_rows(rows, sort, desc, page, size)
    out = {"tabs": [{"key": t, "label": lab, "count": counts[t]} for t, lab in TABS], "tab": tab, "rows": [_slim(r) for r in shown],
           "total": total, "page": max(1, page) if size else 1, "size": size,
           "per_record": per_record, "bridge_tab": tab in BRIDGE_TABS,
           # 「全部登記台數」跟三個對帳橋頁籤的差：哪些角色不算進實體設備／虛擬機／管理口（給 InfoNote 講明）
           "not_in_bridge_tabs": [{"role": k, "label": cb.ROLE_LABEL[k], "rows": n} for k, n in roles.items()
                                  if k in ("offbook_pending", "unknown", "endpoint", "duplicate")],
           "bridge_rows": len(items), "bridge_machines": len({x["machine_key"] for x in items})}
    if os_source or os_class:
        # 逐筆登記篩選（對帳頁 OS 那幾列下鑽用）：filter_rows 取代一台一列的表，筆數＝filter_total
        frs = os_filtered_records(conn, os_source or None, os_class or None)
        fshown, ftotal = page_rows(frs, sort, desc, page, size)
        out["filter"] = {"os_source": os_source or None, "os_class": os_class or None,
                         "label": "、".join(x for x in (OS_SOURCES.get(os_source or ""), f"平台：{os_class}" if os_class else "") if x)}
        out["filter_rows"] = fshown
        out["filter_total"] = ftotal
    if per_record:
        keys = {r["key"] for r in shown}
        no = {r["key"]: r["no"] for r in rows}
        recs = [x for x in registrations(conn) if x["key"] in keys]
        recs.sort(key=lambda x: (no.get(x["key"], 0), x["serial"]))
        for i, x in enumerate(recs, 1):
            x["no"] = i
            x.pop("model", None)
            x.pop("location", None)
        out["records"] = recs
    return out
