"""2.x 新版畫面（A1 首頁、A2 病歷表）的彙整（2026-10-27，v2.001.0）。

原則：只組裝既有的正典計算，不另訂規則。
  比對（搜尋）＝oncall.resolve（值班一頁卡同一套：資產序號 → IP → 主機名 → APID → 部分比對）
  一台的資料＝oncall.card（existence、blast_radius.impact、daily_digest.machine_history 都在裡面）
  類型＝os_class（OS 大類）＋count_bridge 的角色（VM、一體機元件、管理口…）對到全站同一套類型標記
  在哪裡、同櫃幾台＝rack_layout（跟 4-5 機架同一份箱子）
  首頁數字＝count_bridge（實體設備／主機）、今天要處理＝daily_digest.diff_for＋daily_risk
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

# ===== 類型標記（全站同一套）=====
# 簡寫、文字、是不是「設備」（淺灰框）——顏色在前端 main.css 的 --type-* 變數
TYPE_LABEL = {
    "LNX": "Linux", "WIN": "Windows", "AIX": "AIX", "400": "AS/400（IBM i）", "ORA": "Oracle 一體機",
    "OCP": "OpenShift", "ESX": "VMware ESXi", "NET": "網路設備", "STO": "儲存", "HMC": "HMC", "?": "無資料",
}
_OS_CLASS_CODE = {"Linux": "LNX", "Windows": "WIN", "AIX": "AIX", "IBM i": "400", "虛擬化平台": "ESX",
                  "網路設備": "NET", "儲存設備": "STO"}
_RE_OCP = re.compile(r"openshift|coreos|\bocp\b|\brhcos\b", re.I)


def type_badge(row, role: str | None = None) -> dict:
    """一筆登記的類型標記。row：hardware 列（dict／Row）。role：count_bridge 角色（有就帶，沒有就只看 OS 大類）。
    回 {code, label, vm, guessed, os_class}。guessed＝OS 欄沒有值、類型是從型號／群組推出來的。"""
    import count_bridge
    import manage_state as ms
    import os_class

    keys = row.keys() if hasattr(row, "keys") else ()

    def g(k):
        return row[k] if k in keys else None
    oc = os_class.classify_row(row)
    unknown = set(os_class.load_rules().get("unknown_values", ()))
    os_txt = (g("os") or "").strip()
    guessed = (not os_txt or os_txt in unknown) and oc not in ("未知",)
    model = g("device_model") or ""
    rules = count_bridge.load_rules()
    code = _OS_CLASS_CODE.get(oc)
    if re.search(rules.get("appliance_model_regex") or r"$^", model, re.I):
        code = "ORA"                                  # count_bridge 的一體機型號（Exadata／ODA）
    elif re.search(rules.get("mgmt_box_model_regex") or r"$^", model, re.I) or "hmc" in (os_txt + " " + model).lower():
        code = "HMC"
    elif code == "LNX" and _RE_OCP.search(f"{os_txt} {g('hostname') or ''}"):
        code = "OCP"
    elif role in ("phys_storage",):
        code = "STO"
    elif role in ("phys_network",):
        code = "NET"
    if code is None:
        code = "?"
    vm = role == "vm" or bool(ms.is_vm_value(g("is_vm"), model))
    note = None
    if code == "ESX" and vm:
        # ESXi 本身是實體機；CIA 標成 VM（型號 (VM)／is_vm）多半是登記錯——照 OS 大類顯示 ESX、不掛 VM，但講出來不悄悄改
        vm = False
        note = "CIA 標成 VM，請確認"
    logo, tile, ver = os_logo(code, os_txt, model)
    return {"code": code, "label": TYPE_LABEL[code], "vm": vm, "guessed": bool(guessed and code != "?"), "os_class": oc,
            "logo": logo, "tile": tile, "version_label": ver, "note": note}


# 作業系統 → 圖示代號＋顯示文字（版本）。發行版與版本用 normalize.normalize_os（全站同一支），不另比對。
# AIX、AS/400 依使用者指示不用 logo：文字方塊「AIX」「A400」。網路／儲存／HMC 用通用圖示。
_DISTRO = (("red hat coreos", "openshift", "RHCOS"), ("red hat", "rhel", "RHEL"), ("centos", "centos", "CentOS"),
           ("rocky", "rocky", "Rocky"), ("oracle linux", "oracle", "Oracle Linux"), ("ubuntu", "ubuntu", "Ubuntu"),
           ("debian", "debian", "Debian"), ("suse", "suse", "SLES"))


def os_logo(code: str, os_txt: str, model: str = "") -> tuple[str | None, str, str]:
    """回 (logo 代號或 None, 方塊上的字, 旁邊的文字＋版本)。logo 是 None 時前端畫文字方塊。"""
    import normalize

    n = normalize.normalize_os(os_txt) if os_txt else {"product": None, "version": None, "canonical": None}
    prod = (n.get("product") or "").lower()
    ver = n.get("version") or ""

    def lab(name):
        return f"{name} {ver}".strip()
    if code in ("LNX", "OCP"):
        for key, logo, name in _DISTRO:
            if key in prod:
                return logo, code, lab(name)
        if code == "OCP":
            return "openshift", "OCP", lab("OpenShift")
        return "linux", "LNX", lab("Linux")
    if code == "WIN":
        return "windows", "WIN", (n.get("canonical") or "Windows") if n.get("product") else "Windows"
    if code == "ESX":
        return "esxi", "ESX", lab("ESXi")
    if code == "AIX":
        return None, "AIX", lab("AIX")
    if code == "400":
        return None, "A400", lab("IBM i")
    if code == "ORA":
        return "oracle", "ORA", (model or "Oracle 一體機")
    if code in ("NET", "STO", "HMC"):
        return None, code, TYPE_LABEL[code]
    return None, "?", "無資料"


# ===== 偵測狀態（決策 12：運作中／逾期未偵測／未偵測到／無資料）=====

def status_of(m: dict | None) -> dict:
    """偵測狀態（2.003.1 統一定義；主機履歷的狀態、2.1「逾期未偵測」頁籤都用這一個，同一個詞同一個意思）：
      運作中＝existence「計入台數」（存在可信度達門檻）；
      逾期未偵測＝沒達門檻、但曾經有來源看到過（最後偵測時間有值）；
      未偵測到＝沒達門檻、也從沒有任何來源看到過（只有登記）；
      無資料（已退役）＝已退役。m＝existence.compute 的一台。"""
    if not m:
        return {"key": "unknown", "label": "無資料（不在偵測範圍）"}
    if m.get("retired"):
        return {"key": "unknown", "label": "無資料（已退役）"}
    if m.get("counted"):
        return {"key": "online", "label": "運作中"}
    if m.get("last_seen_any"):
        return {"key": "stale", "label": "逾期未偵測"}
    return {"key": "missing", "label": "未偵測到"}


def status_chip(card_what: dict, collect: dict) -> dict:
    """運作中／逾期未偵測／未偵測到／無資料（原因）。依 existence 的等級（同一份 compute）。"""
    lv = card_what.get("exist_level")
    if card_what.get("retired"):
        return {"key": "unknown", "label": "無資料（已退役）"}
    if lv in ("onboarded", "inventory", "scan"):
        return {"key": "online", "label": "運作中"}
    if lv == "collect_stale":
        return {"key": "stale", "label": "逾期未偵測"}
    if lv == "cia_only":
        return {"key": "unknown", "label": "無資料（僅 CIA 登記，未曾偵測到）"}
    if lv == "none":
        return {"key": "missing", "label": "未偵測到"}
    return {"key": "unknown", "label": "無資料（沒有偵測證據）"}


# ===== 虛擬機（全站一個定義，2.007.4）=====

def vm_keys(conn) -> set[str]:
    """哪些機器是虛擬機：對帳橋角色 vm 的登記所屬那一台（2.1 虛擬機頁籤、10.2 虛擬機、主機履歷「類型」同一個定義）。"""
    import count_bridge
    return {x["machine_key"] for x in count_bridge.compute(conn)["items"] if x["role"] == "vm"}


def with_vm(badge: dict, key: str, vk: set[str]) -> dict:
    """類型標記的「虛擬機」改用 vm_keys（審查：原本用 is_vm／型號判，221 有約 131 台在 2.1 列為虛擬機、主機履歷卻寫實體機）。
    v2.007.6：VMware 平台有明確 VM 證據的也是虛擬機（vCenter 一體機、巢狀 ESXi），畫面寫「虛擬機（VMware 設備）」，
    天條狀態照樣看得到；note（「CIA 標成 VM，請確認」）只在角色判不是 VM 時保留。"""
    vm = key in vk
    out = {**badge, "vm": vm}
    if vm and badge.get("code") == "ESX":
        out["note"] = None
        out["vm_label"] = "虛擬機（VMware 設備）"
    elif vm:
        out["vm_label"] = "虛擬機"
    return out


# ===== 搜尋（A1 打字就出）=====

def _light(conn, k: str, g: list, sysname: dict, vk: set[str] | None = None) -> dict:
    import rack_layout
    import system_stats

    r = g[0]
    pr = rack_layout.parse_rack(r["rack_no"])
    room = system_stats.normalize_location(r["physical_location"])
    where = f"{room or ''} {pr['rack']}".strip() if pr["kind"] == "rack" else ("VM" if pr["kind"] == "vm" else (room or ""))
    api = next(((x["api_id"] or "").strip() for x in g if (x["api_id"] or "").strip()), "")
    return {"key": k, "hostname": name_of(r), "ip": r["ip"], "asset_serial": r["asset_serial"],
            "api_id": api or None, "system": sysname.get(api) if api else None, "where": where,
            "retired": k.startswith("ret:"), "badge": with_vm(type_badge(r), k, vk if vk is not None else vm_keys(conn))}


def name_of(r) -> str:
    """畫面上的名字：主機名；空的或 N/A 用資產名稱（rack_layout.display_name，機櫃頁同一條），再沒有用 IP、資產序號。"""
    import rack_layout

    keys = r.keys() if hasattr(r, "keys") else ()
    an = r["asset_name"] if "asset_name" in keys else None
    n = rack_layout.display_name(r["hostname"], an, None)
    if n and n.strip().upper() not in rack_layout._NA:
        return n
    return (r["ip"] or "").split(",")[0].strip() or r["asset_serial"]


def _sysnames(conn) -> dict:
    return {b["api_id"]: b["name"] for b in conn.execute("SELECT api_id, name FROM business_system")}


def search(conn: sqlite3.Connection, q: str, limit: int = 8) -> dict:
    """打字就出：機器（oncall 同一套比對）＋系統（APID／系統名稱含這串字，跟 4-1 搜尋的系統角度同一條）。"""
    import blast_radius
    import oncall

    kw = (q or "").strip()
    if len(kw) < 2:
        return {"q": kw, "top": None, "machines": [], "systems": [], "total": 0}
    groups, _ = oncall._groups(conn)
    how, keys = oncall.resolve(conn, kw, groups)
    names = _sysnames(conn)
    vk = vm_keys(conn)
    machines = [_light(conn, k, groups[k], names, vk) for k in keys[:limit]]
    qn = blast_radius.search_norm(kw)
    counts: dict[str, int] = {}
    for k, g in groups.items():
        if k.startswith("ret:"):
            continue
        for a in {(r["api_id"] or "").strip() for r in g if (r["api_id"] or "").strip()}:
            counts[a] = counts.get(a, 0) + 1
    systems = [{"api_id": a, "name": n, "machines": counts.get(a, 0)} for a, n in sorted(names.items())
               if qn and (qn in blast_radius.search_norm(a) or qn in blast_radius.search_norm(n or ""))][:limit]
    top = None
    if how == "APID" and systems:
        top = {"kind": "system", **next((s for s in systems if s["api_id"].lower() == kw.lower()), systems[0])}
    elif machines and how != "部分比對" and len(keys) == 1:
        top = {"kind": "machine", **machines[0]}
    elif machines and len(keys) == 1:
        top = {"kind": "machine", **machines[0]}
    return {"q": kw, "matched_by": how, "top": top, "machines": machines, "systems": systems, "total": len(keys),
            "services": search_services(conn, kw, limit)}


def search_services(conn: sqlite3.Connection, kw: str, limit: int = 8) -> list[dict]:
    """服務／埠（2.007.14，使用者：「舊版有搜尋，現在還有嗎」——舊版搜得到 nginx、1521 這種，新版要補上）：
    數字＝埠號完全相同；文字＝程式名或服務名含這串字。只列現役（gone_at 為空），一台一個埠一列。"""
    try:
        if kw.isdigit():
            rows = conn.execute(
                "SELECT hs.ip, hs.port, hs.process, hs.service_guess, hs.exposure, hs.asset_serial, h.hostname "
                "FROM host_service hs LEFT JOIN hardware h ON h.asset_serial = hs.asset_serial "
                "WHERE hs.gone_at IS NULL AND hs.port = ? ORDER BY hs.ip LIMIT ?", (int(kw), limit + 1)).fetchall()
        else:
            like = f"%{kw}%"
            rows = conn.execute(
                "SELECT hs.ip, hs.port, hs.process, hs.service_guess, hs.exposure, hs.asset_serial, h.hostname "
                "FROM host_service hs LEFT JOIN hardware h ON h.asset_serial = hs.asset_serial "
                "WHERE hs.gone_at IS NULL AND (hs.process LIKE ? OR hs.service_guess LIKE ?) ORDER BY hs.ip LIMIT ?",
                (like, like, limit + 1)).fetchall()
    except sqlite3.OperationalError:   # 還沒收集過服務（表不存在）：沒有服務可搜，不是錯
        return []
    return [{"ip": r["ip"], "port": r["port"], "service": r["service_guess"] or r["process"] or "未知服務",
             "hostname": r["hostname"] or r["ip"], "key": r["asset_serial"] or r["ip"],
             "local_only": r["exposure"] == "localhost"} for r in rows[:limit]]


# ===== 病歷表（A2）=====

def _same_system(conn, groups: dict, api: str, me: str, names: dict) -> dict:
    keys = [k for k, g in groups.items() if not k.startswith("ret:") and any((r["api_id"] or "").strip() == api for r in g)]
    vk = vm_keys(conn)
    rows = [_light(conn, k, groups[k], names, vk) for k in sorted(keys, key=lambda k: (groups[k][0]["hostname"] or "").lower())]
    others = [r for r in rows if r["key"] != me]
    return {"api_id": api, "name": names.get(api), "count": len(others), "machines": others}


def _rack_info(conn, key: str, serials: list[str]) -> dict | None:
    import rack_layout

    ms = rack_layout.machines(conn)
    m = next((x for x in ms if x["key"] == key), None) or next(
        (x for x in ms if x.get("asset_serial") in serials), None)
    if not m or m["kind"] != "rack" or not m.get("rack"):
        return {"kind": m["kind"] if m else None, "room": m["room"] if m else None} if m else None
    det = rack_layout.rack_detail(conn, m["room"], m["rack"], ms=ms)
    me = next((d for d in det["devices"] if d["key"] == m["key"]), None)
    head = (me or {}).get("merged_into") or (me or {}).get("mgmt_of") or m["key"]
    boxes = [d for d in det["devices"] if d["state"] == "active" and d.get("box_role") in rack_layout.COUNTED and d["key"] != head]
    return {"kind": "rack", "room": m["room"], "rack": m["rack"], "u_start": m["u_start"], "u_end": m["u_end"],
            "u": rack_layout._u_text(m), "box_count": det["box_count"], "others": len(boxes),
            "other_keys": [d["key"] for d in boxes], "other_names": [d["name"] for d in boxes],
            "box_role": (me or {}).get("box_role"), "position_corrected": m.get("position_corrected")}


def _conclusion(name: str, badge: dict, systems: list[dict], rack: dict | None, esxi: list[dict], status: dict) -> str:
    """一句話摘要（正式用語，決策 12）：「業務系統 好麥證券 的 AIX 主機，位於內湖機房 B2 櫃。」"""
    owner = (systems[0]["name"] or systems[0]["api_id"]) if systems else None
    label = badge.get("version_label") or badge["label"]
    if badge["code"] == "?":
        kind = "類型未知的設備"
    elif badge["code"] in ("NET", "STO", "HMC"):
        kind = badge["label"]
    elif badge["code"] == "ORA":
        kind = f"Oracle 一體機（{label}）"
    else:
        kind = f"{label} {'虛擬機' if badge['vm'] else '主機'}"
    s = f"業務系統「{owner}」的 {kind}" if owner else f"未對應業務系統的 {kind}"
    if rack and rack.get("kind") == "rack":
        s += f"，位於{rack['room']} {rack['rack']} 櫃"
    elif esxi:
        s += f"，執行於 ESXi {esxi[0]['label']}"
    if status["key"] != "online":
        s += f"；偵測狀態：{status['label']}"
    return s + "。"


def _measured_of(conn, g: list) -> dict | None:
    """這台機器「系統自己查到的」OS／CPU／記憶體／磁碟（2.007.51，使用者：「納管以後資訊一律以系統查到的為主，
    CIA 資訊都沒查到準確，例如 OS 版本、DISK、CPU、RAM」）。沒納管、沒收集過回 None（畫面照舊顯示 CIA）。
    OS 來源＝collected_os（全站同一份）；CPU／記憶體／磁碟＝host_spec（measured_hw 同一份），同一台的多筆登記取最新一筆。"""
    import collected_os
    import measured_hw
    import v2_hosts

    serials = [r["asset_serial"] for r in g]
    out: dict = {}
    me = next((collected_os.measured_for(conn, s) for s in serials if s), None)
    if me:
        text = collected_os.display_text(me["platform"], me.get("os"))
        lab, _src, _t = v2_hosts.os_version_of({s: me for s in serials}, g, {"os_class": collected_os.OS_CLASS_OF.get(me["platform"], "")})
        out["os"] = {"text": text, "label": lab, "at": me.get("collected_at")}
    spec = measured_hw._spec(conn)
    sp = max((spec[s] for s in serials if s in spec), key=lambda x: str(x.get("at") or ""), default=None)
    if sp and (sp.get("cores") or sp.get("mem_mb") or sp.get("disk_gb")):
        out["hw"] = {"cores": sp.get("cores"), "mem_gb": round(sp["mem_mb"] / 1024, 1) if sp.get("mem_mb") else None,
                     "disk_gb": round(sp["disk_gb"]) if sp.get("disk_gb") else None, "at": sp.get("at")}
    return out or None


def machine(conn: sqlite3.Connection, q: str) -> dict:
    """A2 病歷表：q＝machine key／資產序號／IP／主機名（oncall 同一套解析）。
    對到一台回 mode=machine；多台回 candidates（不自動挑）；沒有回 none。"""
    import count_bridge
    import oncall

    kw = (q or "").strip()
    groups, of = oncall._groups(conn)
    names = _sysnames(conn)
    if kw in groups:
        keys, how = [kw], "代號"
    else:
        how, keys = oncall.resolve(conn, kw, groups)
        if how == "APID":
            return {"mode": "system", "q": kw, "api_id": kw,
                    "candidates": [_light(conn, k, groups[k], names, vm_keys(conn)) for k in keys]}
    if not keys:
        return {"mode": "none", "q": kw, "reason": "資產庫裡找不到（IP／主機名稱／資產編號／業務系統代號 都比對過）"}
    if len(keys) > 1:
        return {"mode": "candidates", "q": kw, "matched_by": how,
                "candidates": [_light(conn, k, groups[k], names, vm_keys(conn)) for k in keys[:50]], "total": len(keys)}
    key = keys[0]
    g = groups[key]
    card = oncall.card(conn, key, g)
    serials = card["what"]["serials"]
    roles = {i["asset_serial"]: i["role"] for i in count_bridge.compute(conn)["items"] if i["asset_serial"] in serials}
    rep = g[0]
    badge = with_vm(type_badge(rep, roles.get(rep["asset_serial"])), key, vm_keys(conn))
    import existence
    exm = next((existence.for_serial(conn, s) for s in card["what"]["serials"]), None)
    status = status_of(exm) if exm else status_chip(card["what"], card["collect"])
    rack = _rack_info(conn, key, serials)
    same = [_same_system(conn, groups, s["api_id"], key, names) for s in card["systems"]]
    core = next((s for s in card["systems"] if "核心" in (s.get("sys_class") or "")), None)
    imp = card["impact"]
    impacted = {s["api_id"]: {"api_id": s["api_id"], "name": s["name"], "severity": s.get("severity"), "own": True}
                for s in card["systems"]}
    for s in (imp.get("system_list") or []):
        impacted.setdefault(s["api_id"], {**s, "own": False})
    hw_serial = next((r["hw_serial"] for r in g if r["hw_serial"]), None)
    ex_onboarded = card["collect"].get("status") == "fresh"
    name = name_of(rep)
    return {
        "mode": "machine", "q": kw, "matched_by": how, "key": key, "name": name,
        "badge": badge, "status": status, "roles": roles, "vmware": (vm := is_vmware(conn, g)),
        # 天條前已被當一般主機納管的 VMware（onboard_eligibility.onboarded_esxi 同一個條件：collect_ok=1 或有收集帳號）
        "vmware_legacy": bool(vm and (ex_onboarded or _had_access(conn, g))),
        "core": {"label": "核心系統", "api_id": core["api_id"]} if core else None,
        "conclusion": _conclusion(name, badge, card["systems"], rack, card["where"]["esxi"], status),
        "who_is": {**card["what"], "hw_serial": hw_serial, "hw_serial_note": None if hw_serial else "無資料（CIA 未填）"},
        "measured": _measured_of(conn, g),
        "systems": card["systems"], "same_system": same,
        "people": card["who"],
        "where": {**card["where"], "rack_info": rack},
        "alive": {"status": status, "last_seen_any": card["collect"].get("last_seen_any"),
                  "exist_label": card["what"].get("exist_label"), "exist_score": card["what"].get("exist_score"),
                  "onboarded": ex_onboarded, "collect": card["collect"]},
        "impact": {**imp, "systems_all": list(impacted.values()), "at_least": len(impacted),
                   "deps_note": None if imp.get("deps_available") else "系統之間的上下游資料還不夠，實際可能更多"},
        "changes": card["changes"],
    }


def _had_access(conn, g: list) -> bool:
    """天條前已被當一般主機納管（onboard_eligibility.onboarded_esxi 同一個判斷）。"""
    import onboard_eligibility as oe

    sig = oe.legacy_signals(conn)
    return any(oe.had_collector_access(r, sig) for r in g)


def is_vmware(conn: sqlite3.Connection, g: list) -> bool:
    """這台是不是 VMware 設備（ESXi／vCenter）：跟納管、即時檢測同一個入口（onboard_eligibility.judge_for_asset）。
    主機履歷用它把「即時檢測」「前往納管」藏起來（天條：VMware 不納管、不經 SSH 收集；2.006.1 找碴 C2）。"""
    import onboard_eligibility as oe

    names = oe.esxi_host_names(conn)
    for r in g:
        got = oe.judge_for_asset(conn, r, esxi_names=names)
        if got and got[0] == "esxi":
            return True
    return False


def connections(conn: sqlite3.Connection, key: str, limit: int = 300) -> dict:
    """連線頁簽：這台（全部 IP）觀察到的連線（host_connection，service 收集時存的），對方 IP 對回機器。"""
    import manage_state as ms
    import oncall

    groups, _ = oncall._groups(conn)
    g = groups.get(key)
    if g is None:
        raise LookupError("查無這台")
    ips = sorted({ip for r in g for ip in ms.ips_of(r["ip"])})
    try:
        q = ",".join("?" for _ in ips) or "''"
        rows = [dict(r) for r in conn.execute(
            f"SELECT direction, local_port, peer_ip, peer_port, count, first_seen, last_seen FROM host_connection "
            f"WHERE host_ip IN ({q}) ORDER BY last_seen DESC, count DESC LIMIT ?", (*ips, limit))]
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc):
            raise
        return {"ips": ips, "rows": [], "reason": "還沒有連線資料：這台還沒讀取過服務"}
    by_ip: dict[str, str] = {}
    for k, gg in groups.items():
        for r in gg:
            for ip in ms.ips_of(r["ip"]):
                by_ip.setdefault(ip, r["hostname"] or r["asset_serial"])
    for r in rows:
        r["peer_name"] = by_ip.get(r["peer_ip"])
    return {"ips": ips, "rows": rows,
            "reason": None if rows else "沒有觀察到連線：未納管或尚未收集服務資料"}


def registrations(conn: sqlite3.Connection, key: str) -> dict:
    """所有登記資料：這台每一筆 CIA／其他來源登記的原始欄位。"""
    import oncall

    groups, _ = oncall._groups(conn)
    g = groups.get(key)
    if g is None:
        raise LookupError("查無這台")
    out = []
    for r in g:
        d = {k: r[k] for k in r.keys()}
        pers = [dict(p) for p in conn.execute("SELECT * FROM personnel WHERE asset_serial = ?", (r["asset_serial"],))]
        out.append({"asset_serial": r["asset_serial"], "fields": d, "personnel": pers})
    return {"key": key, "records": out}


def history(conn: sqlite3.Connection, key: str, days: int = 90) -> dict:
    import daily_digest

    mkey = key[4:] if key.startswith("ret:") else key
    return {"key": key, "days": days, "items": daily_digest.machine_history(conn, mkey, days)}


# ===== 每日異動的資料新鮮度（2.006.6，找碴 C16）=====

#: 每日快照比對的輸入來源：掃描、收集、CIA 匯入。三個都沒有比「前一份快照」新，比對出來的 0 只代表沒有新資料。
_INPUTS = (("scan_history", "scan_time", "網段掃描"), ("host_spec", "collected_at", "主機收集"),
           ("import_log", "imported_at", "資料匯入"))


def latest_input(conn: sqlite3.Connection, before: str | None = None) -> tuple[str | None, str | None]:
    """掃描／收集／匯入最後一筆的時間與來源；before＝只看這個時間（含）以前的。"""
    latest, src = None, None
    for t, col, label in _INPUTS:
        try:
            v = conn.execute(f"SELECT MAX({col}) FROM {t}" + (f" WHERE {col} <= ?" if before else ""),
                             (before,) if before else ()).fetchone()[0]
        except sqlite3.OperationalError as exc:
            if "no such" not in str(exc):
                raise
            continue
        if v and (latest is None or str(v) > latest):
            latest, src = str(v), label
    return latest, src


def changes_freshness(conn: sqlite3.Connection, ch: dict) -> dict:
    """ch＝daily_digest.diff_for 的結果。回 {today, latest_input, latest_source, no_new_input, reason}。
    no_new_input＝前一份快照之後，掃描／收集／匯入都沒有新資料（今天 0 異動不代表沒有異動）。"""
    import daily_digest
    from datetime import date

    latest, src = latest_input(conn)
    prev = daily_digest.load_run(conn, ch.get("prev_date")) if ch.get("prev_date") and daily_digest.tables_exist(conn) else None
    prev_at = (prev or {}).get("created_at")
    today = date.today().isoformat()
    is_today = ch.get("date") == today
    no_new = bool(prev_at) and (latest is None or latest < prev_at)
    reason = None
    if not ch.get("date"):
        reason = "無資料（還沒有任何快照）"
    elif not is_today:
        reason = f"無資料（今日未收集；最近一份快照 {ch['date']}）"
    elif no_new:
        reason = f"資料來源未更新（最後一筆{src or '資料'} {latest or '無'}，早於前一份快照 {prev_at}）：比對結果不代表無異動"
    return {"today": is_today, "latest_input": latest, "latest_source": src, "no_new_input": no_new, "reason": reason}


def week_changes(conn: sqlite3.Connection, days: int = 7, today=None) -> dict:
    """1.1「本週異動」（2026-10-28，找碴 C25）：最近 N 天每一天的每日異動筆數＝1.2 每日異動同一支 daily_digest.diff_for
    （點日期就是 1.2 那一天，數字一樣）。那天沒有快照＝「無資料（未收集）」；有快照但沒有前一份可比＝「無資料（無前一份快照）」。
    最後一天另用 changes_freshness 判斷資料來源有沒有更新。"""
    import daily_digest
    from datetime import date, timedelta

    today = today or date.today()
    cfg = daily_digest.load_settings(conn)
    have = set(daily_digest.dates(conn))
    labels = dict(daily_digest.CATEGORIES)
    rows, totals = [], {k: 0 for k in labels}
    latest = None
    for i in range(days):
        d = (today - timedelta(days=i)).isoformat()
        if d not in have:
            rows.append({"date": d, "n": None, "status": "無資料（未收集）", "detail": "", "link": None})
            continue
        ch = daily_digest.diff_for(conn, d, cfg)
        if latest is None:
            latest = ch
        if not ch.get("prev_date"):
            rows.append({"date": d, "n": None, "status": "無資料（無前一份快照）", "detail": "", "link": f"/v2/changes?tab=daily&date={d}"})
            continue
        cur_at = ((ch.get("snapshot") or {}).get("created_at"))
        prev_at = (daily_digest.load_run(conn, ch["prev_date"]) or {}).get("created_at")
        inp, _ = latest_input(conn, cur_at)
        if prev_at and (inp is None or inp < prev_at) and not sum((ch.get("counts") or {}).values()):
            # 兩份快照之間掃描／收集／匯入都沒有新資料、又比出 0：0 不代表沒有異動（找碴 C16 同一條）。
            # 比出有異動的照列（例：存在可信度隨時間下降，不需要新資料也會發生）
            rows.append({"date": d, "n": None, "status": f"無資料（資料來源未更新，最後一筆 {inp or '無'}）", "detail": "",
                         "link": f"/v2/changes?tab=daily&date={d}"})
            continue
        counts = ch.get("counts") or {}
        n = sum(counts.values())
        for k, v in counts.items():
            totals[k] = totals.get(k, 0) + v
        rows.append({"date": d, "n": n, "status": f"對比 {ch['prev_date']}",
                     "detail": "、".join(f"{labels.get(k, k)} {v}" for k, v in counts.items() if v),
                     "link": f"/v2/changes?tab=daily&date={d}"})
    fr = changes_freshness(conn, latest) if latest else {"reason": "無資料（還沒有任何快照）"}
    return {"days": days, "rows": rows, "totals": [{"key": k, "label": labels[k], "n": v} for k, v in totals.items()],
            "freshness": fr}


# ===== 首頁（A1）=====

def home(conn: sqlite3.Connection) -> dict:
    """今天要處理＋底下兩個數字（count_bridge）。

    2.006.3（找碴 C1）：原本只收每日異動、硬碟、合約過期，1.3 有 66 台逾期未重開機、4.1 有進行中事件，首頁卻綠底寫
    「待處理 0 項」。現在每一項都跟它下鑽那頁**同一支計算**：
      進行中事件＝incident.list_events（4.1「進行中」頁籤）
      逾期未重開機／硬體異常／單點風險／保固過期＝daily_risk.risk_overview（1.3 各頁籤）
      容量預警＝storage_capacity.alerts（5.2「容量預警」頁籤）
      每日異動＝daily_digest.diff_for（1.2 每日異動）、掃描無回應＝scan_health（1.2 掃描異常）
    算不出來的項目照樣列出、標「無法計算」，不可以悄悄消失（消失＝看起來沒事）。"""
    import count_bridge
    import daily_digest
    import daily_risk
    import scan_health

    todo: list[dict] = []

    def add(key, label, n, tone, link, note=None):
        todo.append({"key": key, "label": label, "n": n, "tone": tone, "link": link, "note": note})

    def failed(key, label, link, exc):
        add(key, f"{label}：無法計算（{type(exc).__name__}）", None, "warn", link)

    n_scan = scan_health.open_count(conn)       # 掃描器可能壞了：排第一（2026-10-28）
    if n_scan:
        add("scan_empty", "掃描無回應（掃描器可能無法連線）", n_scan, "bad", "/v2/changes?tab=issues")
    try:
        import incident
        oc = incident.mode_counts(incident.list_events(conn), open_only=True)   # 演練不混進正式；未分類獨立計
        if oc["formal"]:
            add("incident_open", "進行中事件（正式）", oc["formal"], "bad", "/v2/incident?tab=open&mode=formal")
        if oc["unclassified"]:
            add("incident_open_unclassified", "進行中事件（未分類歷史）", oc["unclassified"], "warn", "/v2/incident?tab=open&mode=unclassified")
    except Exception as exc:  # noqa: BLE001 - 一項算不出來不能讓首頁掛掉，但要講出來
        failed("incident_open", "進行中事件", "/v2/incident", exc)

    cfg = daily_digest.load_settings(conn)
    try:
        ch = daily_digest.diff_for(conn, None, cfg)
    except ValueError:
        ch = {"counts": {}, "categories": [], "date": None, "snapshot": None}
    labels = {c["key"]: c["label"] for c in ch.get("categories", [])}
    order = ("exist_down", "missing", "location", "ip", "hostname", "system", "os", "collect_gap",
             "storage_low", "uptime_risk", "count_change", "added")
    for k in order:
        n = (ch.get("counts") or {}).get(k, 0)
        if n:
            add(k, f"每日異動：{labels.get(k, k)}", n, "bad" if k in ("exist_down", "missing") else
                ("warn" if k in ("location", "ip", "hostname", "system", "os", "collect_gap") else "plain"),
                f"/v2/changes?tab=daily&cat={k}" + (f"&date={ch['date']}" if ch.get("date") else ""))
    try:
        rk = daily_risk.risk_overview(conn)
        up, hw, sp, ct = rk["uptime"], rk["hardware"], rk["spof"], rk["contract"]
        # 查不到≠沒有（審查 M2）：來源沒有資料時列「無資料（原因）」，不悄悄消失、也不讓首頁變綠
        if not up["collected"]:
            add("uptime", "逾期未重開機：無資料（尚未收集到任何主機的開機時間）", None, "warn", "/v2/risk?tab=uptime")
        elif up["count"]:
            add("uptime", f"逾期未重開機（{up['threshold']} 天以上）", up["count"], "warn", "/v2/risk?tab=uptime",
                f"已收集規格 {up['collected']} 台中")
        if not hw["collected"]:
            add("hardware", "硬體異常：無資料（尚未收集到任何主機的硬體規格）", None, "warn", "/v2/risk?tab=hardware")
        elif hw["count"]:
            add("hardware", "硬體異常", hw["count"], "warn", "/v2/risk?tab=hardware", f"已收集規格 {hw['collected']} 台中")
        if sp["count"]:
            add("spof", "單點風險（業務系統）", sp["count"], "warn", "/v2/risk?tab=spof")
        exp = (ct.get("counts") or {}).get("expired") or 0
        if not ct.get("has_data"):
            add("contract", "保固到期：無資料（未匯入保固資料）", None, "warn", "/v2/risk?tab=contract")
        elif exp:
            add("contract", "維護合約已過期", exp, "warn", "/v2/risk?tab=contract")
    except Exception as exc:  # noqa: BLE001 - 風險清單算不出來不能讓首頁掛掉，但要講出來
        failed("risk_error", "設備風險", "/v2/risk", exc)
    try:
        import storage_capacity as sc
        ds = sc.datastores(conn)
        ph = sc.physical(conn)
        al = sc.alerts(ds, ph)
        fr = sc.freshness(conn, ds)
        has_mount = any(r["by"] == "mount" for r in ph["rows"])   # 實體主機檔案系統（SSH 收集）有沒有資料
        # key 用 storage_alert（審查 M1：storage_low 是每日異動的分類 key，同名會互相蓋掉）
        # 審查 N1：容量預警有兩個來源（RVTools datastore＋收集到的實體主機檔案系統）。有預警就一定列；
        # 兩個來源都沒資料才整項寫無資料；只缺 datastore 時另列一項講 datastore 那半邊無資料。
        if al["count"]:
            add("storage_alert", f"容量預警（剩餘 < {al['threshold_pct']:g}%）", al["count"], "warn", "/v2/storage?tab=low",
                f"資料日期 {str(fr['as_of'])[:10]}，已 {fr['age_days']} 天未更新" if fr["stale"] else None)
        elif not fr["has_data"] and not has_mount:
            add("storage_alert", "容量預警：無資料（尚未匯入 RVTools，也沒有收集到實體主機檔案系統）", None, "warn", "/v2/storage")
        if not fr["has_data"] and (al["count"] or has_mount):
            add("storage_ds_nodata", "容量預警（Datastore）：無資料（尚未匯入 RVTools）", None, "warn", "/v2/storage")
        if fr["has_data"] and fr["stale"]:
            # 0 筆預警也要講：資料過期時「沒有預警」不可信（審查 M2）
            add("storage_stale", f"儲存容量資料已 {fr['age_days']} 天未更新（最近 RVTools 匯入 {str(fr['as_of'])[:10]}）",
                None, "warn", "/v2/storage")
    except Exception as exc:  # noqa: BLE001 - 同上
        failed("storage_alert", "容量預警", "/v2/storage?tab=low", exc)

    try:
        fr = changes_freshness(conn, ch)
        if fr["reason"]:
            add("changes_stale", "每日異動：" + fr["reason"], None, "warn", "/v2/changes")
    except Exception as exc:  # noqa: BLE001 - 同上
        failed("changes_stale", "每日異動資料新鮮度", "/v2/changes", exc)
    snap = ch.get("snapshot") or {}
    trial = {t["key"]: t for t in count_bridge.compute(conn)["summary"]["trial"]}
    return {"todo": todo, "as_of": snap.get("created_at"), "date": ch.get("date"),
            "physical": trial.get("physical", {}).get("value"), "hosts": trial.get("hosts", {}).get("value")}
