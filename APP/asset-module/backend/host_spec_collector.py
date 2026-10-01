"""主機硬體規格採集：已納管的主機用唯讀指令撈 CPU／記憶體／Storage／型號／序號。

2026-09-13 使用者：納管過的機器這些抓得到——AIX 用 prtconf／lsvg／lspv，Linux 用
lscpu／/proc/meminfo／lsblk，Windows 用 WMI。跑在已納管主機、用收集金鑰（不用再打密碼），
全部唯讀、非 root（沿用 software_collector 那套 runner）。

⚠️ Linux 格式標準、可信；**AIX／Windows 依文件撰寫、尚未在真機驗過**（本機環境無法測），
第一次收若欄位對不上，貼實際輸出來我依實際微調。收不到的欄位留白給現場（使用者：需要人盤的人去盤）。
"""
from __future__ import annotations

import re

# ---- 指令（唯讀）----
# 多數免 root；vgs/dmidecode 需 root，走納管時佈的唯讀 sudoers（NOPASSWD、只讀不改設定）。
# 已納管但還沒更新 sudoers 的舊機，sudo 會被拒 → `|| true` 讓它安靜留白，不整批中斷。
CMD_LINUX = {
    "lscpu": "LC_ALL=C lscpu 2>/dev/null",
    "meminfo": "cat /proc/meminfo 2>/dev/null",
    "lsblk": "lsblk -bdno NAME,SIZE,TYPE 2>/dev/null",
    # Storage 依 VG（Root/Data/Backup…）——使用者要的分組視角，不是單一總量。
    "vgs": ("sudo -n /usr/sbin/vgs --noheadings --units g -o vg_name,vg_size,vg_free 2>/dev/null"
            " || sudo -n /sbin/vgs --noheadings --units g -o vg_name,vg_size,vg_free 2>/dev/null || true"),
    # 沒有 LVM 的機器：退回掛載點視角（免 root）
    "df": "LC_ALL=C df -PBG -x tmpfs -x devtmpfs -x overlay 2>/dev/null",
    # PSU 瓦特數＋數量（實體機；VM 無 PSU，collect() 會依 is_vm 跳過）
    "psu": ("sudo -n /usr/sbin/dmidecode -t 39 2>/dev/null"
            " || sudo -n /sbin/dmidecode -t 39 2>/dev/null || true"),
    # 以下皆免 root：kernel / 開機時間 / PCI 裝置（GPU、RAID 控制器）/ 軟體 RAID
    "uname": "uname -sr 2>/dev/null",
    "uptime": "uptime -p 2>/dev/null || cat /proc/uptime 2>/dev/null",
    "lspci": "lspci 2>/dev/null || /usr/sbin/lspci 2>/dev/null || true",
    "mdstat": "cat /proc/mdstat 2>/dev/null",
    # 探測哪些選用工具沒裝——收不到才分得出「沒裝」還是「壞了」（不是靜默留白）
    "tools": ('for t in lspci ethtool smartctl dmidecode multipath lvm; do '
              'command -v "$t" >/dev/null 2>&1 || echo "$t"; done'),
    # ---- 第二批（需唯讀 sudoers，2026-09-14 使用者核准）----
    "dimm": ("sudo -n /usr/sbin/dmidecode -t 17 2>/dev/null || sudo -n /sbin/dmidecode -t 17 2>/dev/null || true"),
    "bios": ("sudo -n /usr/sbin/dmidecode -t 0 2>/dev/null || sudo -n /sbin/dmidecode -t 0 2>/dev/null || true"),
    # 每顆實體磁碟的 SMART（型號＋健康）；先用 lsblk 列磁碟再逐顆問
    "smart": ('for d in $(lsblk -dno NAME,TYPE 2>/dev/null | awk \'$2=="disk"{print $1}\'); do '
              'echo "@@DISK $d"; sudo -n /usr/sbin/smartctl -a "/dev/$d" 2>/dev/null '
              '|| sudo -n /sbin/smartctl -a "/dev/$d" 2>/dev/null || true; done'),
    "multipath": ("sudo -n /usr/sbin/multipath -ll 2>/dev/null || sudo -n /sbin/multipath -ll 2>/dev/null || true"),
    # 硬體 RAID 陣列（依廠牌，唯讀 show；有裝才有）
    "raid_hw": ('sudo -n /usr/sbin/storcli64 /call show 2>/dev/null '
                '|| sudo -n /opt/MegaRAID/storcli/storcli64 /call show 2>/dev/null '
                '|| sudo -n /usr/sbin/perccli64 /call show 2>/dev/null '
                '|| sudo -n /usr/sbin/ssacli ctrl all show config 2>/dev/null || true'),
}
CMD_AIX = {
    "prtconf": "LC_ALL=C prtconf 2>/dev/null",
    "lspv": "lspv 2>/dev/null",
    "lsvg": "lsvg 2>/dev/null",
    # 每個 VG 的容量：先列 VG，再逐個 lsvg 取 TOTAL PPs 換算
    "lsvg_sizes": 'for v in `lsvg 2>/dev/null`; do echo "@@VG $v"; lsvg $v 2>/dev/null; done',
    # 每顆實體磁碟的容量與掛在哪個 VG（2026-09-22 B-24）。
    # `lspv hdiskN` 是 ODM 查詢，**非 root 讀得到**；`bootinfo -s` 要 root，不用。
    # 使用者要的 Storage 不只是「總共幾 G」，還要看得出是哪幾顆、哪個 VG。
    "lspv_detail": ("for d in `lspv 2>/dev/null | awk '{print $1}'`; do "
                    'echo "@@PV $d"; lspv "$d" 2>/dev/null; done'),
    # 機器序號的退路：prtconf 拿不到時用 ODM（非 root）
    "sys0": "lsattr -El sys0 2>/dev/null",
    # LPAR 編號與名稱。非 LPAR 環境回 `-1 NULL`。
    # 這是「這台是 LPAR 不是 VM」的直接證據（使用者：aix 不能說是 vm 要說 lpar）。
    "uname_l": "uname -L 2>/dev/null",
}

# ---- Linux 解析 ----
def parse_lscpu(text: str) -> dict:
    out = {}
    for ln in text.splitlines():
        if ":" not in ln:
            continue
        k, v = ln.split(":", 1)
        k, v = k.strip(), v.strip()
        if k == "Model name":
            out["cpu_model"] = v
        elif k == "CPU(s)":
            out["cpu_count"] = v                 # 邏輯處理器數（含 HT）
        elif k == "Socket(s)":
            out["sockets"] = v
        elif k == "Core(s) per socket":
            out["cores_per_socket"] = v
        elif k == "Thread(s) per core":
            out["threads_per_core"] = v
        elif k in ("CPU max MHz", "CPU MHz"):
            out.setdefault("mhz", v)
    return out


def parse_lspci_devices(text: str) -> dict:
    """lspci → 挑出 GPU/顯示卡與 RAID 控制器（型號）。免 root。"""
    gpu, raid = [], []
    for ln in text.splitlines():
        low = ln.lower()
        # 格式：`3b:00.0 Ethernet controller: Intel ...` — 冒號後是類別，再冒號後是型號
        desc = ln.split(":", 2)[-1].strip() if ln.count(":") >= 2 else ln
        if "vga compatible controller" in low or "3d controller" in low or "display controller" in low:
            gpu.append(desc)
        elif "raid bus controller" in low or "raid controller" in low:
            raid.append(desc)
    return {"gpu": gpu, "raid_controllers": raid}


def parse_mdstat(text: str) -> list[str]:
    """/proc/mdstat → 軟體 RAID 陣列摘要（md0 : active raid1 …）。免 root。"""
    out = []
    for ln in text.splitlines():
        m = re.match(r"(md\d+)\s*:\s*(active|inactive)\s+(\S+)", ln)
        if m:
            out.append(f"{m.group(1)} {m.group(3)} ({m.group(2)})")
    return out


def parse_dmidecode_dimm(text: str) -> dict:
    """dmidecode -t 17 → 已插記憶體條數＋每條容量/速率。空槽（No Module Installed）不算。"""
    blocks = re.split(r"(?=^Handle .*DMI type 17)", text, flags=re.MULTILINE)
    mods = []
    for b in blocks:
        if "DMI type 17" not in b:
            continue
        size = re.search(r"^\s*Size:\s*(.+)$", b, re.MULTILINE)
        sv = size.group(1).strip() if size else ""
        if not sv or "No Module" in sv or sv.lower() == "unknown":
            continue
        speed = re.search(r"Configured Memory Speed:\s*(.+)|Speed:\s*(.+)", b)
        sp = ""
        if speed:
            sp = (speed.group(1) or speed.group(2) or "").strip()
        mods.append({"size": sv, "speed": sp or None})
    return {"count": len(mods), "modules": mods}


def parse_dmidecode_bios(text: str) -> dict:
    """dmidecode -t 0 → BIOS/UEFI 版本與日期。"""
    ver = re.search(r"^\s*Version:\s*(.+)$", text, re.MULTILINE)
    date = re.search(r"Release Date:\s*(.+)", text)
    vendor = re.search(r"Vendor:\s*(.+)", text)
    out = {}
    if ver:
        out["version"] = ver.group(1).strip()
    if date:
        out["date"] = date.group(1).strip()
    if vendor:
        out["vendor"] = vendor.group(1).strip()
    return out


def parse_smart(text: str) -> list[dict]:
    """逐磁碟 smartctl -a → {disk, model, health}。@@DISK 分段。"""
    out = []
    cur = None
    for ln in text.splitlines():
        if ln.startswith("@@DISK "):
            cur = {"disk": ln[7:].strip(), "model": None, "health": None}
            out.append(cur)
        elif cur is not None:
            m = re.search(r"(?:Device Model|Model Number|Product):\s*(.+)", ln)
            if m and not cur["model"]:
                cur["model"] = m.group(1).strip()
            h = re.search(r"(?:overall-health self-assessment test result|SMART Health Status):\s*(.+)", ln)
            if h:
                cur["health"] = h.group(1).strip()
    return [d for d in out if d.get("model") or d.get("health")]


def parse_multipath(text: str) -> list[str]:
    """multipath -ll → 每個多路徑對應（名稱＋路徑數）。粗略解析，主要看「有幾條多路徑」。"""
    out = []
    for ln in text.splitlines():
        # 典型首行：`mpatha (360...) dm-3 VENDOR,MODEL`
        m = re.match(r"(\S+)\s+\(([0-9a-fx]+)\)", ln)
        if m:
            out.append(m.group(1))
    return out


def parse_meminfo(text: str) -> int | None:
    m = re.search(r"MemTotal:\s+(\d+)\s*kB", text)
    return round(int(m.group(1)) / 1024) if m else None    # → MB


def parse_lsblk_bytes(text: str) -> int | None:
    total = 0
    found = False
    for ln in text.splitlines():
        parts = ln.split()
        if len(parts) >= 3 and parts[2] == "disk" and parts[1].isdigit():
            total += int(parts[1]); found = True
    return total if found else None                         # bytes


def _g2i(s: str) -> int | None:
    """'100.00g' / '200G' → 100 / 200（四捨五入整數 GB）。認不出回 None。"""
    m = re.match(r"([\d.]+)\s*[gG]?", (s or "").strip())
    return round(float(m.group(1))) if m else None


def parse_vgs(text: str) -> list[dict]:
    """`vgs --noheadings --units g -o vg_name,vg_size,vg_free` → 每個 VG 的容量。
    使用者要的分組視角（Root VG／Data VG／Backup VG…）。沒有 LVM → 空清單，交給 df 退路。"""
    out = []
    for ln in text.splitlines():
        p = ln.split()
        if len(p) >= 2 and _g2i(p[1]) is not None:
            out.append({"name": p[0], "size_gb": _g2i(p[1]),
                        "free_gb": _g2i(p[2]) if len(p) >= 3 else None})
    return out


def parse_df(text: str) -> list[dict]:
    """`df -PBG` → 依掛載點的容量（沒有 LVM 時的退路）。跳過 pseudo/boot 這種雜訊。"""
    out = []
    for ln in text.splitlines()[1:]:                        # 跳表頭
        p = ln.split()
        if len(p) < 6:
            continue
        mnt = p[5]
        if mnt in ("/boot", "/boot/efi") or mnt.startswith(("/dev", "/run", "/sys", "/proc")):
            continue
        size = _g2i(p[1])
        if size:
            out.append({"name": mnt, "size_gb": size, "free_gb": _g2i(p[3])})
    return out


def parse_dmidecode_psu(text: str) -> dict:
    """`dmidecode -t 39` → PSU 數量＋各自最大瓦特數（實體機才有）。
    數量以 DMI type 39 的 handle 數為準；瓦特取 Max Power Capacity。"""
    blocks = re.split(r"(?=^Handle .*DMI type 39)", text, flags=re.MULTILINE)
    watts, count = [], 0
    for b in blocks:
        if "DMI type 39" not in b:
            continue
        count += 1
        m = re.search(r"Max Power Capacity:\s*([\d]+)\s*W", b)
        if m:
            watts.append(int(m.group(1)))
    return {"count": count, "watts": watts}


def parse_aix_vg_sizes(text: str) -> list[dict]:
    """`for v in $(lsvg); do echo @@VG v; lsvg v; done` → 每個 VG 容量。
    lsvg 的 TOTAL PPs 行：`TOTAL PPs: 799 (204544 megabytes)` → GB。"""
    out, cur = [], None
    for ln in text.splitlines():
        if ln.startswith("@@VG "):
            cur = ln[5:].strip()
            out.append({"name": cur, "size_gb": None})
        elif cur:
            m = re.search(r"TOTAL PPs:.*\((\d+)\s*megabytes\)", ln)
            if m and out:
                out[-1]["size_gb"] = round(int(m.group(1)) / 1024)
    return [v for v in out if v["name"]]


def parse_aix_pv_detail(text: str) -> list[dict]:
    """`lspv hdiskN` 逐顆 -> [{name, vg, size_gb, free_gb}]。

    `lspv hdisk0` 的相關欄位：

    * ``VOLUME GROUP:     rootvg``
    * ``TOTAL PPs:        799 (204544 megabytes)``
    * ``FREE PPs:         100 (25600 megabytes)``

    VG 是 `None` 表示這顆**沒有掛進任何 VG** -- 那是要有人知道的事
    （買了容量沒在用，或是準備要換掉的碟），不是雜訊。
    """
    out: list[dict] = []
    cur: dict | None = None
    for ln in (text or "").splitlines():
        if ln.startswith("@@PV "):
            cur = {"name": ln[5:].strip(), "vg": None, "size_gb": None, "free_gb": None}
            out.append(cur)
            continue
        if cur is None:
            continue
        m = re.search(r"VOLUME GROUP:\s*(\S+)", ln)
        if m and m.group(1).lower() != "none":
            cur["vg"] = m.group(1)
        m = re.search(r"TOTAL PPs:.*?\((\d+)\s*megabytes\)", ln)
        if m:
            cur["size_gb"] = round(int(m.group(1)) / 1024)
        m = re.search(r"FREE PPs:.*?\((\d+)\s*megabytes\)", ln)
        if m:
            cur["free_gb"] = round(int(m.group(1)) / 1024)
    return [p for p in out if p["name"]]


def parse_aix_sys0(text: str) -> dict:
    """`lsattr -El sys0` -> {serial, model}。prtconf 拿不到時的退路（非 root）。"""
    out: dict = {}
    for ln in (text or "").splitlines():
        parts = ln.split()
        if len(parts) >= 2 and parts[0] == "systemid":
            out["serial"] = parts[1]
        elif len(parts) >= 2 and parts[0] == "modelname":
            out["model"] = parts[1]
    return out


# ---- AIX 解析 ----
def parse_prtconf(text: str) -> dict:
    out = {}
    pats = {
        "model": r"System Model:\s*(.+)",
        "serial": r"Machine Serial Number:\s*(.+)",
        "cpu_count": r"Number Of Processors:\s*(\d+)",
        "cpu_model": r"Processor Type:\s*(.+)",
        "cpu_clock": r"Processor Clock Speed:\s*(.+)",
        "mem": r"Memory Size:\s*(\d+)\s*MB",
    }
    for key, pat in pats.items():
        m = re.search(pat, text)
        if m:
            out[key] = m.group(1).strip()
    return out


def parse_aix_disks(lspv_text: str, lsvg_text: str) -> dict:
    disks = [ln.split()[0] for ln in lspv_text.splitlines() if ln.strip()]
    vgs = [ln.strip() for ln in lsvg_text.splitlines() if ln.strip()]
    return {"disk_count": len(disks), "disks": disks, "vgs": vgs}


def _gb(b):
    return round(b / (1024 ** 3)) if isinstance(b, int) else None


def _storage_text(storage: dict) -> str | None:
    """把分組結果壓成一行摘要（畫面/舊欄位相容）：`rootvg 100G、datavg 200G` 或 `500 GB`。"""
    items, by = storage.get("items"), storage.get("by")
    if items and by in ("vg", "mount"):
        return "、".join(f"{v['name']} {v['size_gb']}G" for v in items if v.get("size_gb"))
    return f"{storage['total_gb']} GB" if storage.get("total_gb") else None


def collect(runner, host: str, platform: str, is_vm: bool = False) -> dict:
    """用 runner 跑該平台的唯讀指令，回 {spec, storage, psu, raw}。收不到的鍵就沒有。
    storage＝依 VG（Linux vgs／AIX lsvg）或掛載點（df 退路）的分組；psu＝實體機的 PSU 瓦特＋數量。"""
    raw: dict[str, str] = {}
    spec: dict = {}
    storage: dict = {"by": None, "items": [], "total_gb": None}
    psu: dict | None = None
    spec_extra: dict = {}
    if platform == "aix":
        for k, cmd in CMD_AIX.items():
            raw[k] = runner(host, cmd)
        p = parse_prtconf(raw.get("prtconf", ""))
        sys0 = parse_aix_sys0(raw.get("sys0", ""))
        vgsz = parse_aix_vg_sizes(raw.get("lsvg_sizes", ""))
        pvs = parse_aix_pv_detail(raw.get("lspv_detail", ""))
        if vgsz:
            tot = sum(v["size_gb"] for v in vgsz if v.get("size_gb")) or None
            storage = {"by": "vg", "items": vgsz, "total_gb": tot}
        spec = {
            "cpu": " ".join(x for x in (p.get("cpu_model"), p.get("cpu_clock")) if x) or None,
            "cores": p.get("cpu_count"),
            "mem_mb": int(p["mem"]) if p.get("mem") else None,
            # prtconf 在部分機器上要 root 才吐完整內容；拿不到就退回 ODM 的 sys0。
            # 兩邊都沒有才留空 -- 留空的意思是「沒收到」，不是「這台沒有序號」。
            "model": p.get("model") or sys0.get("model"),
            "serial": p.get("serial") or sys0.get("serial"),
            "vendor": "IBM",
        }
        notes = []
        if not vgsz:
            notes.append("`lsvg` 沒有回任何 VG：AIX 至少會有 rootvg，所以這是指令被擋或連線問題，"
                         "不是這台沒有儲存空間。")
        if pvs:
            orphan = [d["name"] for d in pvs if not d["vg"]]
            if orphan:
                notes.append(f"有 {len(orphan)} 顆磁碟沒掛進任何 VG（{', '.join(orphan)}）："
                             "容量算得進總數，但目前沒有在用。")
        spec_extra = {k: v for k, v in {
            "disks_aix": pvs,
            "lpar_id": (raw.get("uname_l", "") or "").strip() or None,
            "notes": notes,
        }.items() if v}
        # AIX/Power 的 PSU 是 frame 級 → 走 HMC 收，不在主機這裡（見 VIOS/HMC 離線匯入）
    else:   # linux（含 RHEL/Rocky…）
        for k, cmd in CMD_LINUX.items():
            raw[k] = runner(host, cmd)
        c = parse_lscpu(raw.get("lscpu", ""))
        mem = parse_meminfo(raw.get("meminfo", ""))
        vg = parse_vgs(raw.get("vgs", ""))
        if vg:
            storage = {"by": "vg", "items": vg,
                       "total_gb": sum(v["size_gb"] for v in vg if v.get("size_gb")) or None}
        else:
            dfi = parse_df(raw.get("df", ""))
            if dfi:
                storage = {"by": "mount", "items": dfi,
                           "total_gb": sum(v["size_gb"] for v in dfi if v.get("size_gb")) or None}
            else:
                storage = {"by": "disk", "items": [], "total_gb": _gb(parse_lsblk_bytes(raw.get("lsblk", "")))}
        if not is_vm:                       # VM 無 PSU；dmidecode -t 39 走唯讀 sudoers
            pp = parse_dmidecode_psu(raw.get("psu", ""))
            if pp["count"]:
                psu = pp
        spec = {"cpu": c.get("cpu_model"), "cores": c.get("cpu_count"), "mem_mb": mem,
                "model": None, "serial": None, "vendor": None}
        dev = parse_lspci_devices(raw.get("lspci", ""))
        extra = {
            "cpu_sockets": c.get("sockets"),
            "cpu_cores_per_socket": c.get("cores_per_socket"),
            "cpu_threads_per_core": c.get("threads_per_core"),
            "cpu_mhz": c.get("mhz"),
            "kernel": (raw.get("uname", "").strip() or None),
            "uptime": (raw.get("uptime", "").strip() or None),
            "gpu": dev["gpu"], "raid_controllers": dev["raid_controllers"],
            "raid_sw": parse_mdstat(raw.get("mdstat", "")),
            # 缺哪些選用工具（沒裝＝那些欄位收不到，直接講明，不讓人以為壞了）
            "tools_missing": [t for t in raw.get("tools", "").split() if t],
            # 第二批（需 sudoers）：DIMM 插槽、BIOS、磁碟 SMART、多路徑
            "dimm": parse_dmidecode_dimm(raw.get("dimm", "")),
            "bios": parse_dmidecode_bios(raw.get("bios", "")),
            "disks": parse_smart(raw.get("smart", "")),
            "multipath": parse_multipath(raw.get("multipath", "")),
            "raid_hw": bool((raw.get("raid_hw", "") or "").strip()),   # 有硬體 RAID 工具輸出（原文存 raw_json）
        }
        # dimm 空（count=0）就不放，避免畫面出現空殼
        if not extra["dimm"].get("count"):
            extra.pop("dimm", None)
        spec_extra = {k: v for k, v in extra.items() if v}
    spec["storage"] = _storage_text(storage)
    return {"platform": platform, "spec": spec, "storage": storage, "psu": psu,
            "spec_json": spec_extra, "raw": raw}


def _platform_of(conn, ip: str, os_val: str | None, asset_serial: str | None = None) -> str:
    """決定用哪一組指令。**呼叫正典 `manage_state.collect_platform_of`，不自己判。**

    ⚠️ 2026-09-22：這裡原本自己寫了一份 `if "aix" in os_val.lower()`。
    AIX 納管成功後 os 變成 `oslevel -s` 的輸出（`7200-05-09-2446`，**沒有 "aix" 字樣**），
    這份手抄本就失效了——於是本檔 14 處 AIX 處理形同虛設，
    拿 Linux 指令去問 AIX，硬體資訊全空。BOSS 觀察到的「寫了但沒生效」就是這個。

    正典那支會依序看：這筆的 os → **同一台其他登記的 os** → `uname -s` → 埠號。
    自己抄一份的代價就是這次這樣：正典修好了，手抄本沒跟上。
    （這正是 B-03／B-13「同一台只有一份定義」要防的事，只是這次的對象是平台判定。）
    """
    import manage_state
    return manage_state.collect_platform_of(conn, ip, os_val, asset_serial=asset_serial)


def store(conn, asset_serial: str, ip: str, res: dict) -> None:
    import json
    s = res["spec"]
    storage_json = json.dumps(res.get("storage") or {}, ensure_ascii=False)
    psu_json = json.dumps(res["psu"], ensure_ascii=False) if res.get("psu") else None
    spec_json = json.dumps(res.get("spec_json") or {}, ensure_ascii=False) if res.get("spec_json") else None
    conn.execute(
        "INSERT INTO host_spec (asset_serial, ip, platform, cpu, cores, mem_mb, storage, "
        "storage_json, psu_json, spec_json, model, serial, vendor, raw_json, collected_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now','localtime')) "
        "ON CONFLICT(asset_serial) DO UPDATE SET ip=excluded.ip, platform=excluded.platform, "
        "cpu=excluded.cpu, cores=excluded.cores, mem_mb=excluded.mem_mb, storage=excluded.storage, "
        "storage_json=excluded.storage_json, psu_json=excluded.psu_json, spec_json=excluded.spec_json, "
        "model=excluded.model, serial=excluded.serial, vendor=excluded.vendor, "
        "raw_json=excluded.raw_json, collected_at=datetime('now','localtime')",
        (asset_serial, ip, res["platform"], s.get("cpu"), s.get("cores"), s.get("mem_mb"),
         s.get("storage"), storage_json, psu_json, spec_json, s.get("model"), s.get("serial"),
         s.get("vendor"), json.dumps(res.get("raw", {}), ensure_ascii=False)),
    )
    conn.commit()



def _merge_notes(conn, asset_serial: str, notes: dict) -> None:
    """把「為什麼收不到」寫進 host_spec.spec_json，畫面才講得出來。

    ⚠️ 不寫進 DB 的話，這些理由只活在這次 API 回應裡 -- 使用者下次開資產頁
    看到的還是一片空白。「收不到要講原因」要講的是**看頁面的時候**，
    不是只在按下收集的那一秒。
    """
    import json

    row = conn.execute("SELECT spec_json FROM host_spec WHERE asset_serial = ?",
                       (asset_serial,)).fetchone()
    if row is None:
        return
    try:
        cur = json.loads(row["spec_json"]) if row["spec_json"] else {}
    except (ValueError, TypeError):
        cur = {}
    cur["net_notes"] = notes
    conn.execute("UPDATE host_spec SET spec_json = ? WHERE asset_serial = ?",
                 (json.dumps(cur, ensure_ascii=False), asset_serial))
    conn.commit()




def _any_spec(res: dict) -> bool:
    """這次到底有沒有收到**任何**一個規格欄位。

    全空代表指令沒跑起來（連不上／帳號錯／平台判錯），不是「這台沒有 CPU」。
    """
    s = res.get("spec") or {}
    if any(s.get(k) for k in ("cpu", "cores", "mem_mb", "model", "serial", "storage")):
        return True
    st = res.get("storage") or {}
    return bool(st.get("items") or st.get("total_gb") or res.get("spec_json"))


def _why_empty(conn, asset_serial: str) -> str:
    """一個欄位都沒收到時的原因。**優先用 SSH 那一跳的事實**，不要用猜的。"""
    import manage_state as _ms

    try:
        row = conn.execute("SELECT * FROM collect_ssh_trace WHERE asset_serial = ?",
                           (asset_serial,)).fetchone()
    except Exception:  # noqa: BLE001
        row = None
    v = _ms.trace_verdict(row)
    return ("一個硬體欄位都沒收到——指令沒跑起來，不是這台沒有硬體。" + v.get("text", ""))[:400]



def _acct(conn, asset_serial, platform: str) -> str:
    """這一台的收集帳號。**每台一個答案**（`hardware.collect_account`，NULL 就走全域）。

    2026-09-24 收集帳號統一成 webit3sc 的遷移需要它：全域一次翻＝ 09-23 那個
    「好好的機器變成 0/9」的機隊放大版。驗過的那一台才寫新帳號，其餘不受影響。
    """
    import collect_account_migration as cam

    return cam.account_for_host(conn, asset_serial, platform)


def _save_trace(conn, run, serial: str, ip: str) -> None:
    """把這一跳的 SSH 事實落庫（離開碼／stderr／指令原文）。

    ⚠️ **失敗的時候也要寫**——那才是最需要看診斷的時候。
    2026-09-22 真機第一次驗證：AIX 四類全空，但我們證明不了是「SSH 沒連上」
    還是「連上了但指令跑不起來」，因為證據在 stderr 裡而它被丟掉了。

    注入 runner 的測試沒有 save()，所以先問過再叫。
    """
    if not hasattr(run, "save"):
        return
    try:
        run.save(conn, serial, ip)
    except Exception:  # noqa: BLE001 - 診斷寫不進去不可以反過來害收集失敗
        pass


def run_collection(conn, trigger: str = "manual", only_serial: str | None = None) -> dict:
    """對已納管（collect_ok=1）主機逐台收硬體規格。Windows 暫不支援（WinRM 規格待補）。"""
    import manage_state

    import manage_state as _ms

    q, _p = _ms.collect_targets_sql(
        conn, only_serial,
        "SELECT asset_serial, ip, os, is_vm, device_model FROM hardware "
        "WHERE collect_ok = 1 AND ip IS NOT NULL AND ip != ''")
    params: list = list(_p)
    targets = conn.execute(q, params).fetchall()

    # ⚠️ 2026-09-23：收集帳號**要按平台決定，而且要在迴圈裡決定**。
    # AIX 的收集帳號是 8 字元的 webit3sc（max_logname 限制），不是 webit3scan。
    # 原本這裡在迴圈外呼叫 get_collect_account(conn)（沒帶平台），於是：
    #   · 一律用 webit3scan -> 那個帳號在 AIX 上不存在 -> SSH 認證失敗
    #   · 每一條指令都回空字串 -> 畫面顯示「收不到」，看起來像指令有問題
    # 真機實測（test1T，公司機）四樣全空就是這個原因。
    # 放在迴圈外還有第二個問題：一批機器混著 Linux 與 AIX 時只會有一個答案。
    ok, failed, unsupported = [], [], []
    skipped: list[dict] = []
    net_notes: dict = {}
    if only_serial and not targets:
        _why = manage_state.why_not_collectable(conn, only_serial)
        if _why:
            skipped.append({"asset_serial": only_serial, "reason": _why})
    for t in targets:
        ip, serial = t["ip"], t["asset_serial"]
        platform = _platform_of(conn, ip, t["os"], t["asset_serial"])
        if platform == "windows":
            unsupported.append({"asset_serial": serial, "ip": ip,
                                "reason": "Windows 硬體規格待用 WinRM 收（尚未支援）"})
            continue
        # VM 無 PSU，跳過 dmidecode（旗標不可靠 → 也認 device_model 的 (VM) 標記）
        dm = (t["device_model"] or "")
        is_vm = bool(t["is_vm"]) or "(VM)" in dm or dm.strip().upper() == "VM" or "virtual" in dm.lower()
        try:
            runner = manage_state._runner_for(
                ip, manage_state.COLLECTOR_KEY_DEFAULT,
                account=_acct(conn, serial, platform))
            res = collect(runner, ip, platform, is_vm=is_vm)
            # ⚠️ 2026-09-23：`collect()` 收到空輸出**不會拋例外**，照樣寫一列空的規格，
            # 然後被算成 ok——畫面顯示「硬體規格 收到 N 筆」，點進去每一欄都是「—」。
            # 那是**假成功**，而且比失敗更糟：失敗會有人去查，假成功不會。
            # 一個欄位都沒收到就是沒收到，要講原因（SSH 那一跳的事實在 trace 裡）。
            if not _any_spec(res):
                _save_trace(conn, runner, serial, ip)
                failed.append({"asset_serial": serial, "ip": ip,
                               "error": _why_empty(conn, serial)})
                continue
            store(conn, serial, ip, res)
            # 同一趟收 NIC/HBA 明細（共用連線）。失敗不影響規格結果，
            # **但不可以吞掉**：吞掉的話畫面上「網卡 0」跟「這台沒網卡」一樣。
            try:
                import net_collector
                net = net_collector.collect_net(runner, ip, platform)
                net_collector.store_net(conn, serial, net)
                if net.get("notes"):
                    net_notes[serial] = net["notes"]
                    _merge_notes(conn, serial, net["notes"])
                # 用剛收到的 HBA WWPN 反查已收的 SAN 資料，補 fabric/switch/port/zone/target
                if net.get("hbas"):
                    import san_collector
                    san_collector.reconcile_hba_san(conn, only_serial=serial)
            except Exception as exc:  # noqa: BLE001
                # 2026-09-22：原本這裡是 `pass`。整批 AIX 的網卡／HBA 收集
                # 不管為什麼失敗都一聲不吭，使用者看到的只有空白。
                net_notes[serial] = {"error": f"網卡／HBA 收集失敗：{str(exc)[:200]}"}
                _merge_notes(conn, serial, net_notes[serial])
            ok.append({"asset_serial": serial, "ip": ip, "platform": platform})
            _save_trace(conn, runner, serial, ip)
        except Exception as exc:  # noqa: BLE001 — 單台失敗不中斷整批
            failed.append({"asset_serial": serial, "ip": ip, "error": str(exc)[:200]})
            _save_trace(conn, locals().get("runner"), serial, ip)
    return {"total": len(targets), "ok": len(ok), "failed": len(failed),
            "unsupported": len(unsupported), "fail_detail": failed[:20],
            "skipped": skipped,
            # 「收到了但不完整」的理由（權限、這台真的沒有 FC…）。
            # 畫面要把這些講出來，不可以只顯示空白或 0。
            "net_notes": net_notes}
