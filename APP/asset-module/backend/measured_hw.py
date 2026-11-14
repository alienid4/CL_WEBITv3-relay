"""硬體欄位的實測優先（v1.477.0，2026-10-20，PRECEDENCE1 延伸）。

使用者：實測贏過一切——CPU 數、記憶體、磁碟、型號、序號都一樣。「實測 RAM 8G、RVTools 寫 16G →
RVTools 是舊的（VM 改過大小），以實測為準」。

比對（一台機器＝同一台的全部登記，system_stats.collapse_machines）：
  - CPU：實測 host_spec.cores  vs RVTools vCPU「CPUs」
  - 記憶體：實測 host_spec.mem_mb（OS 看到的 MemTotal，會比配置少一點）vs RVTools vMemory「Size MiB」
    → 差超過 MEM_TOLERANCE（15%）才算不符（吸收核心保留、MiB/MB 換算）
  - 磁碟：實測 storage_json.total_gb vs RVTools vDisk「Capacity MiB」加總 → 只列疑似（OS 看到的跟配置的本來就不一樣）
  - 型號：實測 host_spec.model vs CIA 設備機型 → 只列疑似（寫法太多，VM 一律略過）
  - MAC：CIA MAC 不等於任何實測**實體**網卡 → 校正成帶登記 IP 的實體網卡 MAC（2026-10-21，實測為準；
    虛擬／teaming／loopback 不參與；全部 MAC 列在依據與 CIA 回報表「其他 MAC」）
哪些可以「校正」寫在 correction_fields.json（cpu／mem 允許 collect；disk／model 預設只列疑似）。

回傳的列跟 measured_mismatch.rows 同一個格式，多一欄 against（CIA／RVTools）：
CIA 回報表只收 against=CIA 的；RVTools 舊值只在系統校正清單看（那是 vCenter 那邊要更新，不是 CIA）。
CIA／RVTools 原始資料一律不改。
"""
from __future__ import annotations

import json
import re

MEM_TOLERANCE = 0.15
DISK_TOLERANCE = 0.25


def _int(v):
    try:
        return int(float(str(v).strip().split()[0]))
    except (TypeError, ValueError, IndexError):
        return None


def _rv_extra(conn) -> tuple[dict, dict]:
    """RVTools 額外分頁：(依 VM UUID, 依 VM 名) → {cpus, mem_mib, disk_mib, at}。"""
    by_uuid: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    import machine_identity

    def slot(d):
        u = str(d.get("VM UUID") or "").strip().lower()
        n = machine_identity.norm_name(str(d.get("VM") or ""))
        e = by_uuid.setdefault(u, {}) if u else by_name.setdefault(n, {})
        if u and n:
            by_name.setdefault(n, e)
        return e
    try:
        cur = conn.execute(
            "SELECT source, payload, collected_at FROM source_record WHERE source IN "
            "('vcenter_extra:vCPU', 'vcenter_extra:vMemory', 'vcenter_extra:vDisk')")
        found = cur.fetchall()
    except Exception:  # noqa: BLE001 - 舊庫沒有 source_record：沒有 RVTools 規格可比
        return {}, {}
    for r in found:
        try:
            d = json.loads(r["payload"] or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(d, dict):
            continue
        e = slot(d)
        e["at"] = max(str(e.get("at") or ""), str(r["collected_at"] or "")) or None
        if r["source"].endswith("vCPU") and _int(d.get("CPUs")) is not None:
            e["cpus"] = _int(d.get("CPUs"))
        elif r["source"].endswith("vMemory") and _int(d.get("Size MiB")) is not None:
            e["mem_mib"] = _int(d.get("Size MiB"))
        elif r["source"].endswith("vDisk") and _int(d.get("Capacity MiB")) is not None:
            e.setdefault("_disks", {})[str(d.get("Disk UUID") or d.get("Disk") or len(e.get("_disks", {})))] = \
                _int(d.get("Capacity MiB"))
    for e in list(by_uuid.values()) + list(by_name.values()):
        if "_disks" in e:
            e["disk_mib"] = sum(e["_disks"].values())
    return by_uuid, by_name


def _spec(conn) -> dict[str, dict]:
    out: dict[str, dict] = {}
    try:
        rows = conn.execute("SELECT asset_serial, cores, mem_mb, storage_json, model, collected_at "
                            "FROM host_spec WHERE collected_at IS NOT NULL").fetchall()
    except Exception:  # noqa: BLE001 - 舊庫沒有 host_spec：沒有實測規格可比
        return {}
    for r in rows:
        total = None
        try:
            sj = json.loads(r["storage_json"] or "null")
            total = float(sj.get("total_gb")) if isinstance(sj, dict) and sj.get("total_gb") is not None else None
        except (TypeError, ValueError, AttributeError):
            total = None
        out[r["asset_serial"]] = {"cores": _int(r["cores"]), "mem_mb": _int(r["mem_mb"]), "disk_gb": total,
                                  "model": (r["model"] or "").strip() or None, "at": r["collected_at"]}
    return out


def _norm_mac(v) -> str:
    h = "".join(ch for ch in str(v or "").lower() if ch in "0123456789abcdef")
    return h if len(h) == 12 and h != "0" * 12 else ""


def _fmt_mac(h: str) -> str:
    return ":".join(h[i:i + 2] for i in range(0, 12, 2))


#: 虛擬／teaming／loopback／通道介面（比對與校正都不看；舊資料沒有 nic_type 時靠名稱／型號判）
_VIRTUAL_NIC = re.compile(r"vEthernet|Multiplexor|Hyper-V Virtual Ethernet|Microsoft .*Virtual|Loopback|Teredo|"
                          r"isatap|6to4|WAN Miniport|Kernel Debug|^(lo|docker|veth|virbr|br-|bond|team|tun|tap|"
                          r"cali|flannel|cni|kube|vnet)", re.IGNORECASE)


def _is_physical(r) -> bool:
    t = (r.get("nic_type") or "").strip()
    if t:
        return t == "實體"
    return not (_VIRTUAL_NIC.search(str(r.get("nic_name") or "")) or _VIRTUAL_NIC.search(str(r.get("model") or "")))


def _nics(conn) -> dict:
    """實測網卡（asset_nic：Linux／AIX SSH、Windows WinRM 收的）：{asset_serial: [列]}。全部 MAC 都留（含虛擬）。"""
    out: dict = {}
    try:
        found = [dict(r) for r in conn.execute("SELECT * FROM asset_nic WHERE mac IS NOT NULL AND mac != '' "
                                               "ORDER BY asset_serial, nic_name")]
    except Exception:  # noqa: BLE001 - 舊庫沒有 asset_nic：沒有實測 MAC 可比
        return {}
    for r in found:
        r["_mac"] = _norm_mac(r["mac"])
        if r["_mac"]:
            out.setdefault(r["asset_serial"], []).append(r)
    return out


def _primary_nic(nics: list, ip) -> dict | None:
    """主要實體網卡：帶著登記 IP 的那張；IP 在 team／bond 上就取它的實體成員；
    再退：有 gateway 的實體卡 → 有 IP 的實體卡 → 第一張實體卡。"""
    phys = [n for n in nics if _is_physical(n)]
    if not phys:
        return None
    ip = (ip or "").strip()

    def has_ip(n):
        return bool(ip) and ip in [x.strip() for x in f"{n.get('ips') or ''},{n.get('ip') or ''}".split(",")]
    for n in nics:
        if has_ip(n):
            if _is_physical(n):
                return n
            mem = [m for m in phys if (m.get("bond") or "") == n.get("nic_name")]
            if mem:
                return mem[0]
    return (next((n for n in phys if n.get("gateway")), None) or next((n for n in phys if n.get("ip")), None)
            or phys[0])


def _mac_list(nics: list) -> str:
    """全部 MAC（含虛擬／teaming）：`aa:bb:… 介面名（類型，IP）`。"""
    out = []
    for n in nics:
        t = n.get("nic_type") or ("實體" if _is_physical(n) else "虛擬")
        ips = n.get("ips") or n.get("ip") or ""
        out.append(f"{_fmt_mac(n['_mac'])} {n.get('nic_name') or ''}（{t}{'，' + ips if ips else ''}）")
    return "；".join(out)


def _mac_judge(hw: dict, nics: list) -> dict | None:
    """CIA MAC 等於任一實體網卡 MAC → None（對得上）；否則校正成主要實體網卡的 MAC。沒得比 → None。"""
    cm = _norm_mac(hw.get("mac"))
    if not cm or not nics:
        return None
    if cm in {n["_mac"] for n in nics if _is_physical(n)}:
        return None
    p = _primary_nic(nics, hw.get("ip"))
    if p is None:
        return None
    others = [n for n in nics if n["_mac"] != p["_mac"]]
    return {"cia": _fmt_mac(cm), "measured": _fmt_mac(p["_mac"]), "nic": p.get("nic_name"),
            "at": max((str(n.get("collected_at") or "") for n in nics), default="") or None,
            "via_serial": p.get("asset_serial"), "all_macs": _mac_list(nics), "other_macs": _mac_list(others)}


def mac_view(conn, hw: dict) -> dict | None:
    """資產頁「⟲ 系統校正」用（跟系統校正清單同一份判定）：同一台全部登記的實測網卡一起看。"""
    import system_stats

    by = _nics(conn)
    if not by:
        return None
    rows_ = [dict(r) for r in conn.execute("SELECT * FROM hardware")]
    for g in system_stats.collapse_machines(rows_).values():
        if any(r["asset_serial"] == hw.get("asset_serial") for r in g):
            return _mac_judge(hw, [n for r in g for n in by.get(r["asset_serial"], [])])
    return _mac_judge(hw, by.get(hw.get("asset_serial"), []))


def rows(conn, hw_rows: list[dict], cfg: dict, rec) -> list[dict]:
    """hw_rows：全部 hardware（dict）。rec：measured_mismatch._rec（同格式）。"""
    import lifecycle
    import machine_identity
    import system_stats

    spec = _spec(conn)
    nic = _nics(conn)
    if not spec and not nic:
        return []
    rv_uuid, rv_name = _rv_extra(conn)

    def can(field, src):
        return any(f["key"] == field and src in f.get("correct_by", []) for f in cfg["fields"])

    out: list[dict] = []
    for g in system_stats.collapse_machines(hw_rows).values():
        # MAC（2026-10-21，實測為準）：CIA MAC 等於任一實體網卡 → 對得上；否則校正成帶登記 IP 的實體網卡 MAC。
        # 虛擬／teaming／loopback 不參與比對也不當校正值，但全部 MAC 列在依據裡（使用者：多 MAC 都要記錄起來）。
        gn = [n for r in g for n in nic.get(r["asset_serial"], [])]
        for hw in (g if gn else []):
            s = hw["asset_serial"]
            if (not s or str(s).startswith(lifecycle.NON_CIA_PREFIXES)
                    or (hw.get("asset_status") or "").strip() in lifecycle.RETIRED_STATUS):
                continue
            mv = _mac_judge(hw, gn)
            if mv:
                x = rec(hw, "mac", mv["cia"], mv["measured"], "collect", mv["at"], mv["via_serial"],
                        "corrected" if can("mac", "collect") else "suspect", cfg)
                x["against"] = "CIA"
                x["other_macs"] = mv["other_macs"]
                x["note"] = f"主要網卡 {mv['nic']}；全部 MAC：{mv['all_macs']}"
                out.append(x)
        sp = max((spec[r["asset_serial"]] for r in g if r["asset_serial"] in spec),
                 key=lambda x: str(x["at"] or ""), default=None)
        if sp is None:
            continue
        rv = None
        for r in g:
            u = str(r.get("vm_uuid") or "").strip().lower()
            rv = rv or (rv_uuid.get(u) if u else None) or rv_name.get(machine_identity.norm_name(r.get("hostname")))
        for hw in g:
            s = hw["asset_serial"]
            if not s or str(s).startswith(lifecycle.NON_CIA_PREFIXES):
                continue
            if (hw.get("asset_status") or "").strip() in lifecycle.RETIRED_STATUS:
                continue
            if rv:
                if sp["cores"] and rv.get("cpus") and sp["cores"] != rv["cpus"]:
                    x = rec(hw, "cpu", str(rv["cpus"]), str(sp["cores"]), "collect", sp["at"], s,
                            "corrected" if can("cpu", "collect") else "suspect", cfg)
                    x["against"] = "RVTools"
                    x["needs"] = ""             # vCenter 那邊的舊值：資訊，不用 CIA 改
                    out.append(x)
                if sp["mem_mb"] and rv.get("mem_mib"):
                    a, b = sp["mem_mb"], rv["mem_mib"]
                    if abs(a - b) / max(b, 1) > MEM_TOLERANCE:
                        x = rec(hw, "mem", f"{round(b / 1024, 1)} GB", f"{round(a / 1024, 1)} GB", "collect",
                                sp["at"], s, "corrected" if can("mem", "collect") else "suspect", cfg)
                        x["against"] = "RVTools"
                        x["needs"] = ""
                        out.append(x)
                if sp["disk_gb"] and rv.get("disk_mib"):
                    a, b = sp["disk_gb"], rv["disk_mib"] / 1024
                    if abs(a - b) / max(b, 1) > DISK_TOLERANCE:
                        x = rec(hw, "disk", f"{round(b)} GB", f"{round(a)} GB", "collect", sp["at"], s,
                                "corrected" if can("disk", "collect") else "suspect", cfg)
                        x["against"] = "RVTools"
                        x["needs"] = ""
                        out.append(x)
            cm, mm = str(hw.get("device_model") or "").strip(), sp["model"]
            if cm and mm and hw.get("is_vm") != 1 and not any(k in mm.lower() for k in ("vmware", "virtual", "kvm")):
                a, b = cm.lower(), mm.lower()
                if a not in b and b not in a:
                    x = rec(hw, "model", cm, mm, "collect", sp["at"], s,
                            "corrected" if can("model", "collect") else "suspect", cfg)
                    x["against"] = "CIA"
                    out.append(x)
    return out
