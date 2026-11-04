"""Windows 主機規格（硬體資訊分頁）：WinRM／PSRP ＋ CIM，唯讀、非管理員盡量收得到。

2026-10-21 公司 Windows Server 2022（v1.480.2）：硬體資訊分頁「尚未收集主機規格」、網卡／HBA「尚未收集」，
按「點此立即收集」只回「這台平台暫不支援收集（Windows 待補 WinRM）」。Linux 的 host_spec 早就有，
Windows 這條一直沒接。這支補上，**寫進同一張 host_spec／asset_nic／asset_hba**（同一個畫面、同一份
系統校正比對：measured_hw 讀 host_spec.cores／mem_mb，MAC 讀 asset_nic），不另開一套。

收什麼（全部 CIM，行程內查詢，不起子行程）：
  · CPU          Win32_Processor（型號、插槽數、每槽核心、邏輯處理器、時脈）
  · 記憶體        Win32_ComputerSystem.TotalPhysicalMemory ＋ Win32_PhysicalMemory（插槽、容量、速率、料號）
  · BIOS         Win32_BIOS
  · 機型／序號／UUID  Win32_ComputerSystem、Win32_ComputerSystemProduct
  · 磁碟          Win32_DiskDrive（＋ MSFT_PhysicalDisk 補 SSD/HDD、健康狀態）；容量用 Win32_LogicalDisk
  · 網卡          Win32_NetworkAdapter（**全部有 MAC 的介面**，標實體／虛擬／teaming；2026-10-21 使用者「多 MAC 都要記錄起來」）
                  ＋ Configuration（全部 IP）＋ MSFT_NetAdapter 補驅動／VLAN、LBFO 補 team
  · FC HBA       MSFT_InitiatorPort（root\\Microsoft\\Windows\\Storage）；再試 root\\WMI MSFC_*（多半要管理員）
  · VM 判定      機型／製造商（Hyper-V「Virtual Machine」、VMware…）→ VM 的 HBA 顯示「此機未使用」

拿不到的項目**一律印 ERR 行**（共用 winrm_collector._PS_ERR_FN 約定），轉成「權限不足（需 X）」講出來，
不可以靜默變成「沒有」（test_no_silent_catch 同一精神）。

SOC：只用 PSRP 送 script（不寫檔、不 Bypass、不編碼、不 IEX、不 Add-Type、不起 exe）；
test_agent_scripts_security 的 Windows payload 守門已納入本模組的 *_PS。

⚠️ 依文件撰寫、以假 CIM 輸出測過；**尚未在真機驗過**（開發環境沒有 Windows 收集目標）。
第一次在公司機收若欄位對不上，原文存在 host_spec.raw_json["win_spec"]，貼出來依實際微調。
"""
from __future__ import annotations

import re

import winrm_collector

# ---- 送到目標機的 PowerShell（唯讀）----
WIN_SPEC_PS = r"""
$ErrorActionPreference='SilentlyContinue'
""" + winrm_collector._PS_ERR_FN + r"""
function W3V($v) { (("" + $v) -replace '[\t\r\n]+', ' ').Trim() }
function W3Hex($b) { if ($b) { ($b | ForEach-Object { '{0:X2}' -f $_ }) -join '' } else { '' } }
"@@BEGIN"
try {
  $cs = Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop
  "CS`t" + (W3V $cs.Manufacturer) + "`t" + (W3V $cs.Model) + "`t" + $cs.TotalPhysicalMemory + "`t" + $cs.NumberOfProcessors + "`t" + $cs.NumberOfLogicalProcessors + "`t" + $cs.HypervisorPresent
} catch { W3Err 'cs' $_ }
try {
  $p = Get-CimInstance -ClassName Win32_ComputerSystemProduct -ErrorAction Stop | Select-Object -First 1
  "CSP`t" + (W3V $p.Vendor) + "`t" + (W3V $p.Name) + "`t" + (W3V $p.IdentifyingNumber) + "`t" + (W3V $p.UUID)
} catch { W3Err 'csp' $_ }
try {
  $b = Get-CimInstance -ClassName Win32_BIOS -ErrorAction Stop | Select-Object -First 1
  $d = ''; if ($b.ReleaseDate) { $d = $b.ReleaseDate.ToString('yyyy-MM-dd') }
  "BIOS`t" + (W3V $b.Manufacturer) + "`t" + (W3V $b.SMBIOSBIOSVersion) + "`t" + $d + "`t" + (W3V $b.SerialNumber)
} catch { W3Err 'bios' $_ }
try {
  $o = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
  $bt = ''; if ($o.LastBootUpTime) { $bt = $o.LastBootUpTime.ToString('yyyy-MM-dd HH:mm:ss') }
  "OS`t" + (W3V $o.Caption) + "`t" + (W3V $o.Version) + "`t" + $bt + "`t" + (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
} catch { W3Err 'os' $_ }
try {
  $n = 0
  Get-CimInstance -ClassName Win32_Processor -ErrorAction Stop | ForEach-Object {
    $n++
    "CPU`t" + (W3V $_.SocketDesignation) + "`t" + (W3V $_.Name) + "`t" + $_.NumberOfCores + "`t" + $_.NumberOfLogicalProcessors + "`t" + $_.MaxClockSpeed
  }
  if ($n -eq 0) { "ERR`tcpu`tEMPTY`t" }
} catch { W3Err 'cpu' $_ }
try {
  $n = 0
  Get-CimInstance -ClassName Win32_PhysicalMemory -ErrorAction Stop | ForEach-Object {
    $n++
    "DIMM`t" + (W3V $_.DeviceLocator) + "`t" + (W3V $_.BankLabel) + "`t" + $_.Capacity + "`t" + $_.ConfiguredClockSpeed + "`t" + $_.Speed + "`t" + (W3V $_.Manufacturer) + "`t" + (W3V $_.PartNumber) + "`t" + $_.SMBIOSMemoryType
  }
  if ($n -eq 0) { "ERR`tdimm`tEMPTY`t" }
  $slots = (Get-CimInstance -ClassName Win32_PhysicalMemoryArray -ErrorAction Stop | Measure-Object -Property MemoryDevices -Sum).Sum
  if ($slots) { "SLOTS`t" + $slots }
} catch { W3Err 'dimm' $_ }
try {
  Get-CimInstance -ClassName Win32_DiskDrive -ErrorAction Stop | ForEach-Object {
    "DISK`t" + $_.Index + "`t" + (W3V $_.Model) + "`t" + $_.Size + "`t" + (W3V $_.InterfaceType) + "`t" + (W3V $_.SerialNumber) + "`t" + (W3V $_.FirmwareRevision) + "`t" + (W3V $_.MediaType) + "`t" + (W3V $_.Status)
  }
} catch { W3Err 'disk' $_ }
try {
  Get-CimInstance -Namespace 'root\Microsoft\Windows\Storage' -ClassName MSFT_PhysicalDisk -ErrorAction Stop | ForEach-Object {
    "PDISK`t" + $_.DeviceId + "`t" + (W3V $_.FriendlyName) + "`t" + $_.MediaType + "`t" + $_.BusType + "`t" + $_.HealthStatus + "`t" + $_.Size
  }
} catch { W3Err 'pdisk' $_ }
try {
  Get-CimInstance -ClassName Win32_LogicalDisk -Filter "DriveType=3" -ErrorAction Stop | ForEach-Object {
    "VOL`t" + $_.DeviceID + "`t" + $_.Size + "`t" + $_.FreeSpace + "`t" + (W3V $_.FileSystem)
  }
} catch { W3Err 'vol' $_ }
try {
  Get-CimInstance -ClassName Win32_NetworkAdapter -ErrorAction Stop | Where-Object { $_.MACAddress } | ForEach-Object {
    "NIC`t" + $_.Index + "`t" + (W3V $_.NetConnectionID) + "`t" + (W3V $_.Name) + "`t" + (W3V $_.MACAddress) + "`t" + $_.Speed + "`t" + $_.NetConnectionStatus + "`t" + (W3V $_.ServiceName) + "`t" + $_.PhysicalAdapter
  }
  Get-CimInstance -ClassName Win32_NetworkAdapterConfiguration -Filter "IPEnabled=True" -ErrorAction Stop | ForEach-Object {
    "NCFG`t" + $_.Index + "`t" + (($_.IPAddress) -join ',') + "`t" + (($_.IPSubnet) -join ',') + "`t" + (($_.DefaultIPGateway) -join ',')
  }
} catch { W3Err 'nic' $_ }
try {
  Get-CimInstance -Namespace 'root\StandardCimv2' -ClassName MSFT_NetAdapter -ErrorAction Stop | ForEach-Object {
    "NETA`t" + (W3V $_.Name) + "`t" + (W3V $_.DriverDescription) + "`t" + (W3V $_.DriverVersionString) + "`t" + $_.VlanID
  }
} catch { W3Err 'neta' $_ }
try {
  Get-CimInstance -Namespace 'root\StandardCimv2' -ClassName MSFT_NetLbfoTeamMember -ErrorAction Stop | ForEach-Object {
    "TEAM`t" + (W3V $_.Name) + "`t" + (W3V $_.Team)
  }
} catch { W3Err 'team' $_ }
try {
  Get-CimInstance -Namespace 'root\Microsoft\Windows\Storage' -ClassName MSFT_InitiatorPort -ErrorAction Stop | ForEach-Object {
    "IPORT`t" + (W3V $_.InstanceName) + "`t" + (W3V $_.PortAddress) + "`t" + (W3V $_.NodeAddress) + "`t" + $_.ConnectionType + "`t" + (($_.OperationalStatus) -join ',')
  }
} catch { W3Err 'iport' $_ }
try {
  Get-CimInstance -Namespace 'root\WMI' -ClassName MSFC_FCAdapterHBAAttributes -ErrorAction Stop | ForEach-Object {
    "FCA`t" + (W3V $_.InstanceName) + "`t" + (W3V $_.Model) + "`t" + (W3Hex $_.NodeWWN) + "`t" + (W3V $_.DriverVersion)
  }
  Get-CimInstance -Namespace 'root\WMI' -ClassName MSFC_FibrePortHBAAttributes -ErrorAction Stop | ForEach-Object {
    $a = $_.Attributes
    "FCP`t" + (W3V $_.InstanceName) + "`t" + (W3Hex $a.PortWWN) + "`t" + (W3Hex $a.NodeWWN) + "`t" + $a.PortSpeed + "`t" + $a.PortSupportedSpeed + "`t" + $a.PortState
  }
} catch { W3Err 'msfc' $_ }
"@@END"
"""

#: 權限不足時要補什麼（給人看的「需 X」）
PERM_NEED = {
    "cs": "WMI root\\cimv2 讀取權限", "csp": "WMI root\\cimv2 讀取權限", "bios": "WMI root\\cimv2 讀取權限",
    "os": "WMI root\\cimv2 讀取權限", "cpu": "WMI root\\cimv2 讀取權限",
    "dimm": "WMI root\\cimv2 讀取權限", "disk": "WMI root\\cimv2 讀取權限",
    "pdisk": "root\\Microsoft\\Windows\\Storage 讀取權限", "vol": "WMI root\\cimv2 讀取權限",
    "nic": "WMI root\\cimv2 讀取權限", "neta": "root\\StandardCimv2 讀取權限",
    "team": "root\\StandardCimv2 讀取權限",
    "iport": "root\\Microsoft\\Windows\\Storage 讀取權限（多半需系統管理員）",
    "msfc": "root\\WMI 讀取權限（MSFC_* 需系統管理員）",
}
ITEM_LABEL = {
    "cs": "機型／記憶體總量", "csp": "序號／UUID", "bios": "BIOS", "os": "OS／開機時間", "cpu": "CPU",
    "dimm": "記憶體插槽", "disk": "磁碟", "pdisk": "磁碟類型／健康", "vol": "磁碟容量（磁碟區）",
    "nic": "網卡", "neta": "網卡驅動／VLAN", "team": "網卡 Team", "iport": "FC HBA（InitiatorPort）",
    "msfc": "FC HBA（MSFC）",
}
#: 這些項目缺了只是少一點細節，不算「部分收到」（例：沒裝 LBFO team、MSFC 類別不存在＝沒有 FC 驅動）
_OPTIONAL = {"team", "neta", "pdisk"}

_VM_PATTERNS = (
    (r"vmware", "VMware"), (r"virtual machine|hyper-v", "Hyper-V"), (r"kvm|qemu|red hat|ovirt", "KVM"),
    (r"xen|hvm domu", "Xen"), (r"virtualbox", "VirtualBox"), (r"openstack|nova", "OpenStack"),
    (r"nutanix|ahv", "Nutanix AHV"), (r"google compute", "GCE"), (r"amazon ec2", "AWS EC2"),
)
#: 不是真的網路介面（沒有對外 MAC 意義）：WAN Miniport、核心偵錯、通道介面、loopback
_PSEUDO_NIC = re.compile(r"WAN Miniport|Kernel Debug|Teredo|isatap|6to4|IP-HTTPS|Loopback|"
                         r"Microsoft ISATAP|Wi-Fi Direct Virtual", re.IGNORECASE)
_NOTFOUND = re.compile(r"Invalid class|Invalid namespace|無效的類別|無效的命名空間|Not found|找不到|0x80041010|0x8004100e",
                       re.IGNORECASE)

# Win32_PhysicalMemory.SMBIOSMemoryType
_MEM_TYPE = {20: "DDR", 21: "DDR2", 24: "DDR3", 26: "DDR4", 34: "DDR5", 35: "LPDDR5"}
# MSFT_PhysicalDisk.MediaType／BusType／HealthStatus
_MEDIA = {"3": "HDD", "4": "SSD", "5": "SCM"}
_BUS = {"1": "SCSI", "2": "ATAPI", "3": "ATA", "6": "FC", "7": "USB", "8": "RAID", "9": "iSCSI",
        "10": "SAS", "11": "SATA", "12": "SD", "13": "MMC", "15": "Virtual", "16": "Storage Spaces",
        "17": "NVMe"}
_HEALTH = {"0": "Healthy", "1": "Warning", "2": "Unhealthy", "5": "Unknown"}
# HBA_PORTSPEED 位元（Gbit）／HBA_PORTSTATE
_FC_SPEED = {1: 1, 2: 2, 4: 10, 8: 4, 16: 8, 32: 16, 64: 32, 128: 20, 256: 40, 2048: 64, 4096: 128}
_FC_STATE = {"1": "Unknown", "2": "Online", "3": "Offline", "4": "Bypassed", "5": "Diagnostics",
             "6": "Linkdown", "7": "Error", "8": "Loopback"}


def _int(v) -> int | None:
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None


def _none(v):
    v = (v or "").strip()
    return None if (not v or v.lower() in ("unknown", "not specified", "none", "to be filled by o.e.m.",
                                           "default string", "system serial number")) else v


def parse(text: str) -> dict:
    """把 WIN_SPEC_PS 的輸出切成 {kind: [欄位列...]}＋errors。純文字進、結構出，不碰網路。"""
    rec: dict[str, list[list[str]]] = {}
    errors = winrm_collector.parse_errors(text)
    for raw in (text or "").splitlines():
        line = raw.rstrip("\r\n")
        if "\t" not in line or line.startswith("ERR\t"):
            continue
        kind, *cols = line.split("\t")
        rec.setdefault(kind.strip(), []).append([c.strip() for c in cols])
    return {"rec": rec, "errors": errors, "complete": "@@END" in (text or "")}


def _cols(row: list[str], n: int) -> list[str]:
    return (row + [""] * n)[:n]


def virt_of(manufacturer: str | None, model: str | None) -> str | None:
    """機型／製造商 → 虛擬化平台名（不是 VM 回 None）。"""
    s = f"{manufacturer or ''} {model or ''}".lower()
    for pat, name in _VM_PATTERNS:
        if re.search(pat, s):
            return name
    return None


def _prefix(mask: str | None) -> str | None:
    """255.255.255.0 → '24'（asset_nic.subnet 跟 Linux 一樣存 CIDR 前綴）。"""
    try:
        parts = [int(x) for x in (mask or "").split(".")]
        if len(parts) != 4:
            return None
        return str(sum(bin(p).count("1") for p in parts))
    except ValueError:
        return None


def _wwn(h: str | None) -> str | None:
    import net_collector

    h = (h or "").strip()
    return net_collector._fmt_wwn(h) if h and set(h.lower()) - {"0"} else None


def explain(err: dict) -> str:
    item = err.get("item") or ""
    label = ITEM_LABEL.get(item, item)
    if err.get("kind") == "DENIED":
        return f"{label}：權限不足（需 {PERM_NEED.get(item, '系統管理員權限')}）"
    if err.get("kind") == "EMPTY":
        return f"{label}：沒有回任何資料（非管理員常見；需 {PERM_NEED.get(item, '系統管理員權限')}）"
    return f"{label}：收集失敗（{(err.get('message') or '沒有訊息')[:120]}）"


def build(text: str, is_vm_hint: bool = False) -> dict:
    """WIN_SPEC_PS 原文 → host_spec_collector.store() 吃的結構＋NIC／HBA（net_collector.store_net 吃的）。"""
    p = parse(text)
    rec, errs = p["rec"], p["errors"]
    err_by = {e["item"]: e for e in errs}

    cs = _cols((rec.get("CS") or [[]])[0], 6)
    csp = _cols((rec.get("CSP") or [[]])[0], 4)
    bios = _cols((rec.get("BIOS") or [[]])[0], 4)
    os_ = _cols((rec.get("OS") or [[]])[0], 4)
    manufacturer, model = _none(cs[0]), _none(cs[1])
    virt = virt_of(manufacturer, model) or virt_of(_none(csp[0]), _none(csp[1]))
    is_vm = bool(virt) or is_vm_hint

    # CPU：每個 Win32_Processor 實例＝一個插槽
    cpus = [_cols(r, 5) for r in rec.get("CPU", [])]
    cpu_name = next((_none(c[1]) for c in cpus if _none(c[1])), None)
    cpu_name = re.sub(r"\s+", " ", cpu_name) if cpu_name else None
    logical = sum(_int(c[3]) or 0 for c in cpus) or _int(cs[4])
    phys = sum(_int(c[2]) or 0 for c in cpus) or None
    sockets = len(cpus) or _int(cs[3])
    total_mem = _int(cs[2])
    mem_mb = round(total_mem / (1024 * 1024)) if total_mem else None

    # 記憶體條
    mods = []
    for r in rec.get("DIMM", []):
        loc, bank, cap, cfg_sp, sp, mfr, part, mtype = _cols(r, 8)
        c = _int(cap)
        if not c:
            continue
        speed = _int(cfg_sp) or _int(sp)
        mods.append({"size": f"{round(c / 1024 ** 3)} GB" if c >= 1024 ** 3 else f"{round(c / 1024 ** 2)} MB",
                     "speed": f"{speed} MT/s" if speed else None, "locator": _none(loc), "bank": _none(bank),
                     "type": _MEM_TYPE.get(_int(mtype) or -1), "manufacturer": _none(mfr),
                     "part_number": _none(part)})
    dimm = {"count": len(mods), "modules": mods} if mods else None
    slots = _int(_cols((rec.get("SLOTS") or [[]])[0], 1)[0])
    if dimm and slots and slots > len(mods):
        dimm["slots_total"] = slots

    # 磁碟：Win32_DiskDrive 為主，MSFT_PhysicalDisk 依編號補 SSD/HDD 與健康
    pd = {c[0]: c for c in (_cols(r, 6) for r in rec.get("PDISK", []))}
    disks = []
    for r in rec.get("DISK", []):
        idx, dmodel, size, bus, serial, fw, _media, status = _cols(r, 8)
        x = pd.get(idx)
        sz = _int(size)
        disks.append({
            "disk": f"Disk {idx}", "model": _none(dmodel),
            "health": (_HEALTH.get(x[4]) if x else None) or _none(status),
            "capacity": f"{round(sz / 1000 ** 3)} GB" if sz else None,
            "serial": _none(serial), "firmware": _none(fw),
            "kind": _MEDIA.get(x[2]) if x else None,
            "bus": (_BUS.get(x[3]) if x else None) or _none(bus),
        })

    # 磁碟區（Storage 依掛載點，跟 Linux df 退路同一種 by=mount）
    vols = []
    for r in rec.get("VOL", []):
        dev, size, free, _fs = _cols(r, 4)
        sz = _int(size)
        if sz:
            vols.append({"name": dev, "size_gb": round(sz / 1024 ** 3),
                         "free_gb": round((_int(free) or 0) / 1024 ** 3) if free else None})
    storage = ({"by": "mount", "items": vols, "total_gb": sum(v["size_gb"] for v in vols) or None}
               if vols else {"by": None, "items": [], "total_gb": None})

    boot, now = os_[2], os_[3]
    uptime = None
    try:
        from datetime import datetime
        d = datetime.strptime(now, "%Y-%m-%d %H:%M:%S") - datetime.strptime(boot, "%Y-%m-%d %H:%M:%S")
        uptime = f"up {d.days} days, {d.seconds // 3600} hours（開機 {boot}）"
    except ValueError:
        uptime = None

    extra = {
        "cpu_sockets": sockets,
        "cpu_cores_per_socket": round(phys / sockets) if phys and sockets else None,
        "cpu_threads_per_core": round(logical / phys) if logical and phys else None,
        "cpu_mhz": _int(_cols(cpus[0], 5)[4]) if cpus else None,
        "kernel": " ".join(x for x in (_none(os_[0]), f"({os_[1]})" if os_[1] else "") if x) or None,
        "uptime": uptime,
        "dimm": dimm,
        "bios": {k: v for k, v in {"vendor": _none(bios[0]), "version": _none(bios[1]),
                                    "date": _none(bios[2])}.items() if v} or None,
        "disks": disks,
        "uuid": _none(csp[3]),
        "virt": virt,
    }
    notes = [explain(e) for e in errs
             if e["item"] not in _OPTIONAL and e["item"] not in ("iport", "msfc")]
    if notes:
        extra["notes"] = notes
    extra = {k: v for k, v in extra.items() if v}

    nics, net_notes = _nics(rec, err_by)
    hbas, hba_note = _hbas(rec, err_by, is_vm)
    if hba_note:
        net_notes["hbas"] = hba_note
    if hbas and not any(h.get("wwpn") for h in hbas):
        net_notes["hba_wwpn"] = "查得到 FC 埠但拿不到 WWPN（root\\WMI MSFC_* 多半需系統管理員）"

    spec = {"cpu": cpu_name, "cores": logical, "mem_mb": mem_mb, "model": model,
            "serial": _none(csp[2]) or _none(bios[3]), "vendor": manufacturer}
    import host_spec_collector

    spec["storage"] = host_spec_collector._storage_text(storage)
    return {"platform": "windows", "spec": spec, "storage": storage, "psu": None,
            "spec_json": extra, "raw": {"win_spec": text or ""},
            "nics": nics, "hbas": hbas, "net_notes": net_notes, "is_vm": is_vm,
            "errors": errs, "complete": p["complete"]}


def _nics(rec: dict, err_by: dict) -> tuple[list[dict], dict]:
    notes: dict = {}
    cfg = {c[0]: c for c in (_cols(r, 4) for r in rec.get("NCFG", []))}
    neta = {c[0]: c for c in (_cols(r, 4) for r in rec.get("NETA", []))}
    team = {c[0]: c[1] for c in (_cols(r, 2) for r in rec.get("TEAM", []))}
    teams = set(team.values())
    nics = []
    for r in rec.get("NIC", []):
        idx, conn_id, name, mac, speed, status, svc, physical = _cols(r, 8)
        if not _none(mac) or _PSEUDO_NIC.search(name or ""):
            continue
        nm = _none(conn_id) or _none(name) or f"NIC {idx}"
        c = cfg.get(idx)
        ips = [x for x in (c[1].split(",") if c else []) if x and ":" not in x]   # 先 IPv4
        masks = [x for x in (c[2].split(",") if c else []) if x and "." in x]
        gws = [x for x in (c[3].split(",") if c else []) if x and ":" not in x]
        na = neta.get(nm)
        sp = _int(speed)
        ntype = ("teaming" if (nm in teams or re.search(r"multiplexor|team", name or "", re.I)) else
                 "實體" if physical.lower() == "true" else "虛擬")
        allips = [x for x in (c[1].split(",") if c else []) if x]
        nics.append({
            "nic_name": nm, "mac": mac.lower(), "nic_type": ntype, "ips": ",".join(allips) or None,
            "state": {"2": "up", "7": "down", "0": "down"}.get(status, status or None),
            "ip": ips[0] if ips else None, "subnet": _prefix(masks[0]) if masks else None,
            "gateway": gws[0] if gws else None,
            "vlan": (na[3] if na and na[3] not in ("", "0") else None), "bond": team.get(nm),
            # Win32 Speed 是 bit/s；未連線時是 2^63-1（不是速率）
            "speed_cur": round(sp / 1_000_000) if sp and sp < 10 ** 13 else None, "speed_max": None,
            "driver": " ".join(x for x in (_none(svc), _none(na[2]) if na else None) if x) or None,
            "model": _none(name) or (_none(na[1]) if na else None),
        })
    if not nics:
        e = err_by.get("nic")
        notes["nics"] = (explain(e) if e else
                         "Win32_NetworkAdapter 沒有回任何實體網卡——Windows 不可能沒有網卡，"
                         "所以是查詢被擋或收集不完整，不是這台沒有網卡。")
    notes["nic_speed"] = "Windows 只收得到目前連線速率，額定速率沒有標準 CIM 欄位——沒有額定值就判斷不了降速。"
    return nics, notes


def _hbas(rec: dict, err_by: dict, is_vm: bool) -> tuple[list[dict], str | None]:
    """FC 埠。MSFT_InitiatorPort（ConnectionType=1）為主，MSFC 補速率／狀態。

    「此機未使用」只在**有依據**時講：VM、或查詢成功但沒有 FC 埠、或 FC 類別根本不存在（沒有 FC 驅動）。
    兩條都被擋就講權限不足——那不是沒有。
    """
    fca = {c[0]: c for c in (_cols(r, 4) for r in rec.get("FCA", []))}
    ports: dict[str, dict] = {}
    for r in rec.get("FCP", []):
        inst, pw, nw, spd, sup, st = _cols(r, 6)
        sp = _int(spd) or 0
        supported = [g for bit, g in _FC_SPEED.items() if (_int(sup) or 0) & bit]
        key = (pw or inst).lower()
        ports[key] = {"inst": inst, "wwpn": _wwn(pw), "wwnn": _wwn(nw) or _wwn((fca.get(inst) or ["", "", ""])[2]),
                      "speed_cur": _FC_SPEED.get(sp), "speed_max": max(supported) if supported else None,
                      "port_state": _FC_STATE.get(st, st or None)}
    for r in rec.get("IPORT", []):
        inst, pw, nw, ctype, ops = _cols(r, 5)
        if ctype != "1":
            continue
        key = (pw or inst).lower()
        cur = ports.setdefault(key, {"inst": inst, "wwpn": _wwn(pw), "wwnn": _wwn(nw), "speed_cur": None,
                                     "speed_max": None, "port_state": None})
        cur["wwpn"] = cur.get("wwpn") or _wwn(pw)
        cur["wwnn"] = cur.get("wwnn") or _wwn(nw)
        if not cur.get("port_state") and ops:
            cur["port_state"] = "Online" if "2" in ops.split(",") else f"OperationalStatus {ops}"
    hbas = []
    for i, (_k, h) in enumerate(sorted(ports.items())):
        hbas.append({"hba_name": f"fc{i}", "wwpn": h["wwpn"], "wwnn": h["wwnn"], "speed_cur": h["speed_cur"],
                     "speed_max": h["speed_max"], "port_state": h["port_state"]})
    if hbas:
        return hbas, None
    if is_vm:
        return [], "此機未使用（VM：儲存走 hypervisor，沒有實體 FC HBA）"
    ie, me = err_by.get("iport"), err_by.get("msfc")
    if ie is None:
        return [], "此機未使用（MSFT_InitiatorPort 查得到，但沒有 FC 埠）"
    if me is None or (me.get("kind") == "FAIL" and _NOTFOUND.search(me.get("message") or "")):
        return [], "此機未使用（沒有 FC 驅動程式／FC 類別不存在）"
    if "DENIED" in (ie.get("kind"), me.get("kind")):
        return [], ("FC HBA：權限不足（需系統管理員）——MSFT_InitiatorPort／MSFC_* 都被拒，"
                    "不是這台沒有 HBA")
    return [], f"FC HBA：查詢失敗（{(ie.get('message') or me.get('message') or '沒有訊息')[:120]}）"


# ---- 連線（WinRM／PSRP，憑證庫取、用完即丟、留稽核）----
def fetch(conn, ip: str, runner=None, timeout: int = 90) -> str:
    """跑 WIN_SPEC_PS 回原文。runner(ip, script) 可注入（測試不碰網路）。失敗 raise，訊息可行動。"""
    if runner is not None:
        return runner(ip, WIN_SPEC_PS)
    import credential_store

    cred_name = credential_store.pick_for_host(conn, ip, kind="winrm")
    if not cred_name:
        raise RuntimeError("沒有可用的收集憑證——請先在系統設定新增 WinRM 服務帳號")
    cred = credential_store.get_for_use(conn, cred_name)
    if cred is None:
        raise RuntimeError(f"憑證「{cred_name}」解不開（加密金鑰可能已更換），請重新設定")
    username, password = cred
    try:
        out = winrm_collector._run(ip, username, password, WIN_SPEC_PS, timeout)
    except Exception as exc:  # noqa: BLE001 - 原因如實回報（含開通指引）
        msg = str(exc)[:300]
        if "actively refused" in msg or "timed out" in msg or "Max retries" in msg:
            msg = f"{msg}｜{winrm_collector.enable_hint()}"
        credential_store.audit_use(conn, cred_name, ip, False, msg)
        raise RuntimeError(msg) from exc
    finally:
        password = None  # noqa: F841 - 明確結束密碼生命週期
    credential_store.audit_use(conn, cred_name, ip, True, "主機規格收集成功")
    return out


def collect_and_store(conn, asset_serial: str, ip: str, is_vm: bool = False, runner=None) -> dict:
    """收一台 Windows 的規格＋NIC／HBA，寫進 host_spec／asset_nic／asset_hba。

    回 {"ok": bool, "error"?: str, "notes": [...], "net_notes": {...}}。一個欄位都沒收到＝失敗（不寫空殼）。
    """
    import host_spec_collector
    import net_collector

    if runner is None:
        import win_onboard

        # Windows 勾選才收（2026-10-22）：沒納管的不登入（第二道；第一道是只挑 collect_ok=1）
        blocked = win_onboard.collect_block(conn, ip, asset_serial)
        if blocked:
            return {"ok": False, "error": blocked}
    text = fetch(conn, ip, runner=runner)
    res = build(text, is_vm_hint=is_vm)
    if not has_data(res):
        why = "；".join(explain(e) for e in res["errors"])[:300]
        return {"ok": False, "error": ("WinRM 連上了但一個硬體欄位都沒收到"
                                       + (f"：{why}" if why else "（腳本沒有回資料）"))}
    host_spec_collector.store(conn, asset_serial, ip, res)
    net = {"nics": res["nics"], "hbas": res["hbas"]}
    for r in net["nics"] + net["hbas"]:
        c, m = r.get("speed_cur"), r.get("speed_max")
        r["downgraded"] = 1 if (c and m and c < m) else 0
    net_collector.store_net(conn, asset_serial, net)
    if res["net_notes"]:
        host_spec_collector._merge_notes(conn, asset_serial, res["net_notes"])
    if res["hbas"]:
        try:
            import san_collector
            san_collector.reconcile_hba_san(conn, only_serial=asset_serial)
        except Exception as exc:  # noqa: BLE001 - SAN 反查失敗不影響規格；理由留在 notes
            res["spec_json"].setdefault("notes", []).append(f"SAN 反查失敗：{str(exc)[:120]}")
    return {"ok": True, "notes": res["spec_json"].get("notes", []), "net_notes": res["net_notes"],
            "summary": summary(res)}


def has_data(res: dict) -> bool:
    """有沒有收到**任何一個硬體欄位**（notes 不算——全部被拒時 notes 有字，但那不是資料）。"""
    s = res.get("spec") or {}
    x = {k: v for k, v in (res.get("spec_json") or {}).items() if k != "notes"}
    return bool(any(s.get(k) for k in ("cpu", "cores", "mem_mb", "model", "serial", "storage"))
                or (res.get("storage") or {}).get("items") or x or res.get("nics"))


def summary(res: dict) -> str:
    s = res["spec"]
    parts = []
    if s.get("cores"):
        parts.append(f"CPU {s['cores']} 緒")
    if s.get("mem_mb"):
        parts.append(f"記憶體 {round(s['mem_mb'] / 1024)} GB")
    parts.append(f"網卡 {len(res['nics'])}")
    parts.append(f"HBA {len(res['hbas'])}" if res["hbas"] else "HBA 此機未使用" if (
        res["net_notes"].get("hbas", "").startswith("此機未使用")) else "HBA 未收到")
    return "、".join(parts)
