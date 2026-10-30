"""已納管主機「機器自己講的 OS」（2026-10-18）。

## 為什麼要有

使用者：DYN-<ip> 那台納管進來了，2-1 寫「設備」，實際是 AIX。

原因：AIX 納管成功後，facts 收集把 hardware.os 寫成 `oslevel -s` 的輸出（例如
`7200-05-09-2446`）——**那串裡面沒有 "aix" 三個字**。收集指令那一側早在 2026-09-22 就修過
（manage_state.platform_from_os 認得這個格式），但分類那一側（os_class 大類、pipeline 的 OS 類型）
沒跟上：大類掉「其他」、OS 類型掉「設備」。資產登記（CIA）的作業系統欄空白的機器也一樣，
明明已經登入進去收過了，畫面還用「推測」或「設備」講它。

## 優先序（全站同一份）

    人工指定（os_override） > 收集到的 OS（已納管主機） > 資產登記 OS 欄 > 其他推測

人工指定在呼叫端已經是最外層（os_override.resolve 包在外面），這支只負責第二層。

## 只蓋「泛用」或「三大 OS 之間」的結論，不蓋特定類別

收集到的 OS 只會是 aix／linux／windows 三種，所以：
  - 登記判成 其他／未知／設備／未填／推測… → 改成收集到的 OS（這次要修的）
  - 登記判成 Linux／Windows／AIX 但跟機器講的不一樣 → 以機器為準（真資料優先）
  - 登記判成 虛擬化平台／網路設備／儲存設備／IBM i／OpenShift／管理介面 → **不動**。
    那些是刻意的分類；尤其 VMware 判定維持 fail-closed（天條：ESXi／vCenter 不納管），
    不能因為「它也回了一個 Linux 字樣」就把 vCenter 翻成 Linux。

⚠️ 這支**只影響顯示與統計的分類**，不影響納管資格：納管白名單
（onboard_eligibility.classify_os／not_onboardable）讀的是資產登記的作業系統欄，不讀這裡
（tests/asset_module/test_collected_os.py 釘住）。

## 「收集到」的證據

  1. hardware.collect_ok = 1 的那筆，它的 os 欄（已納管主機的 os 是 facts 收集寫進去的），
     用 manage_state.platform_from_os 判——跟收集時決定要跑哪組指令是同一支，
     認得 `oslevel -s` 格式。
  2. host_spec.platform = 'aix'：規格收集以 AIX 指令集跑過一輪。只採 AIX——linux／windows
     有可能是埠號猜的退路（facts_collector.detect_platform 只會回 linux／windows），不算證據。

## 證據屬於「機器」，不屬於某一筆登記（v1.473.0，2026-10-20）

公司機 SECSVR194-068T：CIA 寫 Windows Server 2019、IP .168；實際以 .68 SSH 納管＋收集，
機器講 RedHat 9.4、576 個 rpm。主機名相同＝同一台（machine_key），但 facts 只寫進「真的 SSH
進去的那一筆」（.68 那筆），CIA 那筆的 os 仍是 Windows——以前 platform_map 只看「自己這筆的
os」，於是分類、EOS 全照 CIA 講 Windows。

現在 measured_map 以**機器**為單位找證據（system_stats.collapse_machines，全站同一把尺）：
同一台裡挑最可信的一筆實測——
  1. os_cia 有值且 os ≠ os_cia（Q80：facts 寫過這筆，CIA 原值已搬到 os_cia）
  2. 帳外那筆（DYN-/VC-/AUTO-/ADOPT-：沒有 CIA 值，os 只會是收集或掃描寫的）
  3. 其餘 collect_ok=1 的自己這筆（舊行為：分不出是 CIA 原值還是收集值，只給自己用）
1、2 會套到同一台的其他登記，**但只套到「登記大類可被蓋」的那幾筆**（REPLACEABLE_OS_CLASS）
——登記成虛擬化平台／網路／儲存設備的不會因為兄弟筆收到 Linux 就被翻掉（VMware fail-closed）。
CIA 匯入的原值一律不動；這裡只決定「顯示／統計／EOS 用哪個值」。
"""
from __future__ import annotations

import sqlite3

#: 收集平台 → os_class 大類（os_class_groups.json 的類名）
OS_CLASS_OF = {"aix": "AIX", "linux": "Linux", "windows": "Windows"}

#: 可被收集證據改寫的 os_class 大類：泛用的兩類＋三大 OS 本身
REPLACEABLE_OS_CLASS = frozenset({"其他", "未知", "AIX", "Linux", "Windows"})


def _os_type_of() -> dict[str, str]:
    import pipeline as p
    return {"aix": p.OS_AIX, "linux": p.OS_LINUX, "windows": p.OS_WINDOWS}


def _replaceable_os_type() -> frozenset:
    import pipeline as p
    return frozenset({p.OS_APPLIANCE, p.OS_UNSET, p.OS_GUESS_LINUX, p.OS_GUESS_WINDOWS,
                      p.OS_LINUX, p.OS_WINDOWS, p.OS_AIX})


def _not_cia(serial) -> bool:
    import lifecycle
    return bool(serial) and str(serial).startswith(lifecycle.NON_CIA_PREFIXES)


def display_text(platform: str, os_text: str | None) -> str:
    """實測 OS 給人看的字樣。AIX 的 oslevel -s（7200-05-09-2446）沒有 AIX 字樣，補上。"""
    t = (os_text or "").strip()
    if platform == "aix" and "aix" not in t.lower():
        return f"AIX {t}".strip()
    return t


def registry_row(r) -> dict:
    """這筆的「CIA 登記」版本：os 換回 os_cia（Q80：facts 寫過的正本，CIA 原值在 os_cia）。"""
    d = dict(r)
    cia = str(d.get("os_cia") or "").strip()
    if cia:
        d["os"] = cia
    return d


_CACHE: dict = {}


def _cache_key(conn):
    """同一個資料庫、資料沒變（data_version＋本連線 total_changes）就沿用上次算的結果。"""
    try:
        path = conn.execute("PRAGMA database_list").fetchone()[2]
        ver = conn.execute("PRAGMA data_version").fetchone()[0]
    except sqlite3.Error:
        return None     # 拿不到指紋就不快取（照算，只是慢）
    return (path, id(conn), ver, conn.total_changes)


def measured_map_if_cached(conn) -> dict[str, dict] | None:
    """measured_map 的快取還有效（資料沒變過）就回它，否則 None——呼叫端改用 measured_for 單台查。
    給逐台迴圈用：沒有寫庫的迴圈（例如納管狀態刷新）全程共用一份；邊收邊寫的迴圈不會每台整庫重算。"""
    key = _cache_key(conn)
    if key is not None and key[0] and _CACHE.get("key") == key:
        return _CACHE["val"]
    return None


def measured_map(conn) -> dict[str, dict]:
    """（有快取，見 _measured_map）"""
    key = _cache_key(conn)
    if key is not None and key[0] and _CACHE.get("key") == key:
        return _CACHE["val"]
    val = _measured_map(conn)
    if key is not None and key[0]:
        _CACHE.update(key=key, val=val)
    return val


def _measured_map(conn) -> dict[str, dict]:
    """asset_serial → 這台機器的實測 OS：{platform, os, collected_at, via_serial, via_ip, strong}。

    沒有收集證據的序號不在回傳裡。strong＝確定是收集寫的（上面 1、2 種），只有 strong 會
    套到同一台的其他登記；畫面「（實測）」字樣也只認 strong。
    """
    import manage_state
    import system_stats

    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM hardware")]
    except sqlite3.Error:
        return {}       # 還沒有 hardware 表（初始化途中）：沒有任何實測證據，分類照登記
    spec_aix: dict[str, str | None] = {}
    try:
        for r in conn.execute(
                "SELECT asset_serial, collected_at FROM host_spec WHERE platform = 'aix' "
                "AND collected_at IS NOT NULL"):
            spec_aix[r["asset_serial"]] = r["collected_at"]
    except sqlite3.Error:
        pass        # 舊庫沒有 host_spec：只用 hardware 的證據

    own = _own_entries(rows, spec_aix)
    acct_by_serial, acct_by_ip = _account_os(conn)
    rv_map = rvtools_map(conn, rows) if _rvtools_can_correct_os() else {}

    out: dict[str, dict] = {}
    for group in system_stats.collapse_machines(rows).values():
        best = _best_of_group(group, own, acct_by_serial, acct_by_ip)
        # 第三證人：RVTools（guest OS）。排在收集之後、CIA 之前（PRECEDENCE1），correction_fields.json 開著才用。
        # 221 實測（2026-10-20）：RVTools 的 OS 欄常是 VM 設定檔的「類型」（「Windows Server 2016 or later」、
        # RHCOS 設成 RHEL 8），同平台內的版本差有 189 筆是假的——所以只在 CIA 空白或平台家族不同時才蓋（_rv_overrides）。
        rv = None
        if best is None and _rvtools_can_correct_os():
            rvs = [rv_map[r["asset_serial"]] for r in group if r["asset_serial"] in rv_map]
            rv = max(rvs, key=lambda e: (str(e["imported_at"] or ""), e["vm_name"] or ""), default=None)
            if rv is not None:
                p = manage_state.platform_from_os(rv.get("guest_os"))
                rv = None if p not in OS_CLASS_OF else {
                    "platform": p, "os": str(rv["guest_os"]).strip(), "collected_at": rv["imported_at"],
                    "via_serial": rv["resolved_serial"], "via_ip": rv["ip"], "strong": False,
                    "source": "rvtools", "vm_name": rv["vm_name"]}
        for r in group:
            s = r["asset_serial"]
            got, replaceable = _resolve_row(r, own, best)
            if got is not None:
                out[s] = got
            elif rv is not None and replaceable and _rv_overrides(registry_row(r).get("os"), rv["platform"]):
                out[s] = rv
    return out


def _own_entries(rows, spec_aix: dict) -> dict[str, dict]:
    """每一筆登記自己身上的收集證據（hardware.collect_ok=1 的 os、或 host_spec 跑過 AIX）。"""
    import manage_state

    own: dict[str, dict] = {}
    for r in rows:
        s = r.get("asset_serial")
        os_text = str(r.get("os") or "").strip()
        p = manage_state.platform_from_os(os_text) if r.get("collect_ok") == 1 and os_text else None
        if p in OS_CLASS_OF:
            cia = str(r.get("os_cia") or "").strip()
            strong = (bool(cia) and cia != os_text) or _not_cia(s)
            own[s] = {"platform": p, "os": display_text(p, os_text),
                      "collected_at": r.get("collect_checked_at"), "via_serial": s,
                      "via_ip": str(r.get("ip") or "").strip() or None, "strong": strong,
                      "source": "collect"}
        elif s in spec_aix:
            own[s] = {"platform": "aix", "os": display_text("aix", os_text) if os_text else "AIX",
                      "collected_at": spec_aix[s], "via_serial": s,
                      "via_ip": str(r.get("ip") or "").strip() or None, "strong": True,
                      "source": "collect"}
    return own


def _best_of_group(group, own: dict, acct_by_serial: dict, acct_by_ip: dict) -> dict | None:
    """同一台挑一筆實測：hardware 上的收集值優先（facts 寫過 CIA 正本＞帳外那筆），
    其次帳號收集順手收的 /etc/os-release（host_account.os_id／os_version）；同級取最近收集的。"""
    strongs = [own[r["asset_serial"]] for r in group
               if r["asset_serial"] in own and own[r["asset_serial"]]["strong"]]
    best = max(strongs, key=lambda e: (2, not _not_cia(e["via_serial"]),
                                       str(e["collected_at"] or ""), e["via_serial"]),
               default=None)
    if best is None:
        accts = [acct_by_serial[r["asset_serial"]] for r in group if r["asset_serial"] in acct_by_serial]
        accts += [acct_by_ip[ip] for ip in {str(r.get("ip") or "").strip() for r in group}
                  if ip and ip in acct_by_ip]
        best = max(accts, key=lambda e: (str(e["collected_at"] or ""), e["via_ip"] or ""),
                   default=None)
    return best


def _resolve_row(r, own: dict, best: dict | None) -> tuple[dict | None, bool]:
    """這一筆登記最後採用哪一份收集證據（RVTools 之外）。回 (證據或 None, 登記大類可不可被蓋)。

    登記成虛擬化平台／網路／儲存設備的不可被兄弟筆的實測翻掉（VMware fail-closed）。"""
    import os_class

    mine = own.get(r["asset_serial"])
    replaceable = os_class.classify_row(registry_row(r)) in REPLACEABLE_OS_CLASS
    if mine and mine["strong"]:
        return mine, replaceable
    if best is not None and replaceable:
        return best, replaceable
    if mine:
        return mine, replaceable        # 自己這筆收過（分不出是不是 CIA 原值）仍排在 RVTools 之前
    return None, replaceable


def measured_for(conn, asset_serial: str) -> dict | None:
    """單一序號的實測 OS——跟 measured_map 同一套規則（_own_entries／_best_of_group／_resolve_row），
    但只撈「同一台」那幾筆，不掃全庫。**不含 RVTools**：那是 vCenter 設定檔的類型，不是機器自己講的。

    收集／略過判斷用（2026-10-21 使用者：「組態檢核／略過判斷一律照實測 OS」）：
    collect_platform_explain 在迴圈裡逐台呼叫，measured_map 每寫一次庫就要整庫重算，放迴圈裡是 N²。
    回 measured_map 的同一種 dict，另加 strong（只有 strong 才算「確定是實測」）。沒有證據回 None。
    """
    import manage_state
    import system_stats

    if not asset_serial:
        return None
    serials = manage_state.same_machine_serials(conn, asset_serial)
    if not serials:
        return None
    ph = ",".join("?" * len(serials))
    rows = [dict(r) for r in conn.execute(
        f"SELECT * FROM hardware WHERE asset_serial IN ({ph})", serials)]
    spec_aix: dict[str, str | None] = {}
    try:
        for r in conn.execute(
                f"SELECT asset_serial, collected_at FROM host_spec WHERE platform = 'aix' "
                f"AND collected_at IS NOT NULL AND asset_serial IN ({ph})", serials):
            spec_aix[r["asset_serial"]] = r["collected_at"]
    except sqlite3.Error:
        pass        # 舊庫沒有 host_spec：只用 hardware 的證據
    group = next((g for g in system_stats.collapse_machines(rows).values()
                  if any(r["asset_serial"] == asset_serial for r in g)), None)
    if not group:
        return None
    ips = sorted({str(r.get("ip") or "").strip() for r in group} - {""})
    acct_by_serial, acct_by_ip = _account_os(conn, serials=[r["asset_serial"] for r in group], ips=ips)
    own = _own_entries(group, spec_aix)
    best = _best_of_group(group, own, acct_by_serial, acct_by_ip)
    me = next(r for r in group if r["asset_serial"] == asset_serial)
    got, _ = _resolve_row(me, own, best)
    return got


def _rv_overrides(cia_os, rv_platform: str) -> bool:
    """RVTools guest OS 只在「CIA 沒填」或「CIA 認得出、而且是不同平台家族」時才蓋 CIA（PRECEDENCE1）。
    CIA 填了但認不出平台（CoreOS4.20、RHCOS 4…）不蓋：RVTools 那欄常是 VM 設定檔類型（RHCOS 設 RHEL 8）。"""
    import manage_state

    t = str(cia_os or "").strip()
    if not t:
        return True
    p = manage_state.platform_from_os(t)
    return p is not None and p != rv_platform


def _rvtools_can_correct_os() -> bool:
    import json
    import os

    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "correction_fields.json")
    try:
        with open(p, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return False    # 設定讀不到：RVTools 不校正（保守）
    return any(f.get("key") == "os" and "rvtools" in f.get("correct_by", []) for f in cfg.get("fields", []))


def rvtools_map(conn, rows: list[dict] | None = None) -> dict[str, dict]:
    """asset_serial → RVTools（vInfo，source_record.source='vcenter'）對到這筆的那一列（最近匯入）。

    對法（全站同一把尺的精神，不另訂規則）：
      1. 匯入時就掛到這筆（resolved_hardware_id）
      2. VM 的 DNS Name／VM 名去網域、小寫＝這筆主機名（machine_identity.norm_name），且不是通用名
         ——CIA 正本即使不是 RVTools 匯進來的，也看得到 vCenter 那邊怎麼講這台。
    退役登記不對（主機名會回收）。回 {vm_name, hostname, guest_os, ip, vi_sdk_server, esxi_host,
    cluster, powerstate, imported_at, resolved_serial, linked}。linked＝這筆就是匯入時掛的那筆。
    """
    import json

    import lifecycle
    import machine_identity

    if rows is None:
        try:
            rows = [dict(r) for r in conn.execute(
                "SELECT id, asset_serial, hostname, ip, is_vm, asset_status FROM hardware")]
        except sqlite3.Error:
            return {}   # 還沒有 hardware 表：沒有要對的登記
    try:
        recs = conn.execute(
            "SELECT sr.payload, sr.collected_at, sr.resolved_hardware_id, h.asset_serial AS rs "
            "FROM source_record sr LEFT JOIN hardware h ON h.id = sr.resolved_hardware_id "
            "WHERE sr.source = 'vcenter'").fetchall()
    except sqlite3.Error:
        return {}       # 舊庫沒有 source_record：沒有 RVTools 證人
    by_name: dict[str, dict] = {}
    by_hw: dict[int, dict] = {}
    for r in recs:
        try:
            pl = json.loads(r["payload"] or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(pl, dict):
            continue

        def _s(k):  # RVTools 匯出的格子可能是數字（Excel 讀成 float）：一律當字串，空的給 None
            v = pl.get(k)
            return str(v).strip() or None if v not in (None, "") else None
        w = {"vm_name": _s("vm_name"), "hostname": _s("hostname"),
             "guest_os": _s("os"), "ip": _s("ip"),
             "vi_sdk_server": _s("vi_sdk_server"), "esxi_host": _s("esxi_host"),
             "cluster": _s("cluster"), "powerstate": _s("powerstate"),
             "imported_at": r["collected_at"], "resolved_serial": r["rs"]}
        newer = lambda d, k: str(w["imported_at"] or "") >= str((d.get(k) or {}).get("imported_at") or "")  # noqa: E731
        if r["resolved_hardware_id"] is not None and newer(by_hw, r["resolved_hardware_id"]):
            by_hw[r["resolved_hardware_id"]] = w
        for nm in {machine_identity.norm_name(w["hostname"]), machine_identity.norm_name(w["vm_name"])}:
            if nm and not machine_identity.is_generic(nm) and newer(by_name, nm):
                by_name[nm] = w
    out: dict[str, dict] = {}
    for r in rows:
        if (r.get("asset_status") or "").strip() in lifecycle.RETIRED_STATUS:
            continue
        w = by_hw.get(r.get("id"))
        if w is not None:
            out[r["asset_serial"]] = {**w, "linked": True}
            continue
        nm = machine_identity.norm_name(r.get("hostname"))
        w = by_name.get(nm) if nm else None
        # 只用名字對：這筆要是 VM（is_vm）或 IP 也相同，不然同名的實體機／AIX 會吃到別台 VM 的 guest OS
        if w is not None and (r.get("is_vm") == 1
                              or (w["ip"] and w["ip"] == str(r.get("ip") or "").strip())):
            out[r["asset_serial"]] = {**w, "linked": False}
    return out


def _account_os(conn, serials=None, ips=None) -> tuple[dict[str, dict], dict[str, dict]]:
    """帳號收集（SSH 登入後讀 /etc/os-release）順手記的 OS：(依序號, 依 IP) 各取最近一次。
    給 serials／ips＝只查這幾筆（measured_for 單台用），不給＝全庫。"""
    import manage_state

    by_s: dict[str, dict] = {}
    by_ip: dict[str, dict] = {}
    where, prm = "", []
    if serials is not None or ips is not None:
        s_l, i_l = list(serials or []) or [""], list(ips or []) or [""]
        where = (f" AND (asset_serial IN ({','.join('?' * len(s_l))}) "
                 f"OR trim(ip) IN ({','.join('?' * len(i_l))}))")
        prm = s_l + i_l
    try:
        cur = conn.execute(
            "SELECT asset_serial, ip, os_family, os_id, os_version, MAX(last_seen) AS seen "
            "FROM host_account WHERE gone_at IS NULL AND trim(coalesce(os_id, '')) <> ''"
            + where + " GROUP BY asset_serial, ip, os_family, os_id, os_version", prm)
        found = cur.fetchall()
    except sqlite3.Error:
        return {}, {}       # 舊庫沒有 host_account／缺欄位：少一種證據，照舊判
    for r in found:
        text = " ".join(x for x in (str(r["os_id"] or "").strip(), str(r["os_version"] or "").strip()) if x)
        fam = str(r["os_family"] or "").strip().lower()
        p = fam if fam in OS_CLASS_OF else manage_state.platform_from_os(text)
        if p not in OS_CLASS_OF:
            continue
        e = {"platform": p, "os": display_text(p, text), "collected_at": r["seen"],
             "via_serial": r["asset_serial"], "via_ip": str(r["ip"] or "").strip() or None,
             "strong": True, "source": "collect"}
        for key, d in ((r["asset_serial"], by_s), (e["via_ip"], by_ip)):
            if key and str(e["collected_at"] or "") >= str((d.get(key) or {}).get("collected_at") or ""):
                d[key] = e
    return by_s, by_ip


def platform_map(conn) -> dict[str, str]:
    """asset_serial → 收集到的平台（aix／linux／windows）。沒有收集證據的不在回傳裡。
    以機器為單位（見 measured_map）。"""
    return {s: m["platform"] for s, m in measured_map(conn).items()}


def apply_os_class(registry_class: str, collected: str | None) -> str:
    """登記判出的大類 + 收集到的平台 → 最終大類（人工指定之外的那一層）。"""
    if collected in OS_CLASS_OF and registry_class in REPLACEABLE_OS_CLASS:
        return OS_CLASS_OF[collected]
    return registry_class


def apply_os_type(registry_type: str, collected: str | None) -> str:
    """同上，給 pipeline 那套 OS 類型（漏斗／2-1 已納管表的 OS 欄）。"""
    if collected in OS_CLASS_OF and registry_type in _replaceable_os_type():
        return _os_type_of()[collected]
    return registry_type
