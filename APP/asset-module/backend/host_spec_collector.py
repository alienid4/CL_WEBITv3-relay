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
    """/proc/mdstat → 軟體 RAID 陣列摘要字串（md0 raid1 (active)）。免 root。

    ⚠️ 形狀不動（`list[str]`）：畫面的 `raidList` 把它跟 `raid_controllers` 併起來
    直接 `join('；')`。要成員碟請用 `parse_mdstat_detail()`。
    """
    return [f"{a['name']} {a['level']} ({a['state']})" for a in parse_mdstat_detail(text)]


def parse_mdstat_detail(text: str) -> list[dict]:
    """/proc/mdstat → 每個軟體 RAID 陣列的層級、狀態與**成員碟**（2026-10-07，搬遷重建）。

    為什麼要成員碟：重建時要知道 `md0` 是由**哪兩顆**組起來的，以及誰是
    spare（`(S)`）、誰已經掉出陣列（`(F)`）。只有「md0 raid1 active」這句話，
    重建的人得自己猜要拿哪兩顆磁碟——猜錯就是把資料蓋掉。

    典型輸出::

        md0 : active raid1 sdb1[1] sda1[0]
              976630464 blocks super 1.2 [2/2] [UU]

    `degraded` 由 `[2/1]` 這種「應有/實到」推出來。**推不出來留 None，不填 False**——
    「不知道有沒有降級」與「確定沒降級」是兩件事，後者會讓人安心得毫無根據。
    """
    out: list[dict] = []
    cur: dict | None = None
    for ln in text.splitlines():
        m = re.match(r"^(md\d+)\s*:\s*(active|inactive)\s*(\(.*?\))?\s*(\S+)?\s*(.*)$", ln)
        if m:
            members = []
            for tok in (m.group(5) or "").split():
                mm = re.match(r"^(\S+?)\[(\d+)\](\(.\))?$", tok)
                if mm:
                    flag = (mm.group(3) or "").strip("()")
                    members.append({
                        "dev": mm.group(1), "role": mm.group(2),
                        # S=spare、F=faulty、W=write-mostly；沒旗標就是正常成員
                        "flag": flag or None,
                    })
            cur = {"name": m.group(1), "state": m.group(2), "level": m.group(4) or None,
                   "members": members, "blocks": None, "degraded": None, "status": None}
            out.append(cur)
            continue
        if cur is None:
            continue
        b = re.search(r"^\s+(\d+)\s+blocks", ln)
        if b:
            cur["blocks"] = int(b.group(1))
        cnt = re.search(r"\[(\d+)/(\d+)\]", ln)
        if cnt:
            want, have = int(cnt.group(1)), int(cnt.group(2))
            cur["degraded"] = have < want
        st = re.search(r"\[([U_]+)\]", ln)
        if st:
            cur["status"] = st.group(1)
    return out


def parse_dmidecode_dimm(text: str) -> dict:
    """dmidecode -t 17 → 已插記憶體條數＋每條容量/速率/**插槽位置**。空槽不算進 count。

    2026-10-07（搬遷重建）：原本只取 size／speed。重建時「幾條、多大」不夠用——
    **要知道哪一條插在哪個槽**（`Locator`／`Bank Locator`）。換機之後插錯槽，
    記憶體交錯（interleaving）配置會不同，效能差一截而且現場看不出原因。
    料號（`Part Number`）是「要配一樣的」時候的依據，一併收。

    ⚠️ **刻意不收 `Serial Number`**：重建要回答的是「這個槽該插什麼」，
    不是「這一條實體記憶體是哪一條」。收了只是多一份可對到實體資產的識別字。

    ⚠️ `empty_slots` 是「有槽但沒插」的清單，**它不是空值**。
    沒有它就分不出「8 槽插滿 8 條」與「16 槽只插 8 條」——後者擴充不用換，前者要。
    """
    blocks = re.split(r"(?=^Handle .*DMI type 17)", text, flags=re.MULTILINE)
    mods, empty = [], []

    def _f(block: str, label: str) -> str | None:
        m = re.search(rf"^\s*{label}:\s*(.+)$", block, re.MULTILINE)
        v = m.group(1).strip() if m else ""
        # dmidecode 問不到時會印這幾種字樣。它們等於「沒有值」，不是值本身——
        # 照字面存下來，畫面上會出現「製造商：Not Specified」這種假資料。
        return None if (not v or v.lower() in ("unknown", "not specified", "none")) else v

    for b in blocks:
        if "DMI type 17" not in b:
            continue
        loc = _f(b, "Locator")
        sv = _f(b, "Size")
        if not sv or "No Module" in sv:
            if loc:
                empty.append(loc)
            continue
        speed = re.search(r"Configured Memory Speed:\s*(.+)|Speed:\s*(.+)", b)
        sp = ""
        if speed:
            sp = (speed.group(1) or speed.group(2) or "").strip()
        mods.append({
            # 既有兩鍵語意不動（畫面 dimmText 只讀 size），以下為新增鍵
            "size": sv,
            "speed": sp or None,
            "locator": loc,
            "bank": _f(b, "Bank Locator"),
            "type": _f(b, "Type"),
            "manufacturer": _f(b, "Manufacturer"),
            "part_number": _f(b, "Part Number"),
        })
    out = {"count": len(mods), "modules": mods}
    if empty:
        out["empty_slots"] = empty
    return out


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
    """逐磁碟 smartctl -a → {disk, model, health, capacity, serial, firmware, kind}。@@DISK 分段。

    2026-10-07（搬遷重建）新增 capacity／serial／firmware／kind。
    **容量是重建的必要欄**：只有型號的話，同一個型號會有 480G/960G 多種規格，
    配錯容量 LV 佈局就擺不回去。`kind`（SSD／HDD）由轉速欄推——
    轉速是 `Solid State Device` 就是 SSD，有數字就是 HDD，**推不出來就留 None 不猜**。
    """
    out = []
    cur = None
    for ln in text.splitlines():
        if ln.startswith("@@DISK "):
            cur = {"disk": ln[7:].strip(), "model": None, "health": None,
                   "capacity": None, "serial": None, "firmware": None, "kind": None}
            out.append(cur)
        elif cur is not None:
            m = re.search(r"(?:Device Model|Model Number|Product):\s*(.+)", ln)
            if m and not cur["model"]:
                cur["model"] = m.group(1).strip()
            h = re.search(r"(?:overall-health self-assessment test result|SMART Health Status):\s*(.+)", ln)
            if h:
                cur["health"] = h.group(1).strip()
            c = re.search(r"(?:User Capacity|Total NVM Capacity|Namespace 1 Size/Capacity):\s*(.+)", ln)
            if c and not cur["capacity"]:
                cur["capacity"] = c.group(1).strip()
            s = re.search(r"Serial [Nn]umber:\s*(.+)", ln)
            if s and not cur["serial"]:
                cur["serial"] = s.group(1).strip()
            f = re.search(r"(?:Firmware Version|Revision):\s*(.+)", ln)
            if f and not cur["firmware"]:
                cur["firmware"] = f.group(1).strip()
            r = re.search(r"Rotation Rate:\s*(.+)", ln)
            if r and not cur["kind"]:
                rv = r.group(1).strip()
                cur["kind"] = "SSD" if "solid state" in rv.lower() else ("HDD" if re.search(r"\d", rv) else None)
    return [d for d in out if any(d.get(k) for k in ("model", "health", "capacity", "serial"))]


def parse_multipath(text: str) -> list[str]:
    """multipath -ll → 多路徑對應的**名稱清單**。

    ⚠️ 這支刻意只回名稱、形狀不動（`list[str]`）：資產詳細頁的 `mpaths` 直接
    `join('、')` 印出來，改成物件會變成一排 `[object Object]`。
    要 WWID／LUN／路徑明細請用 `parse_multipath_detail()`（同一份原文、兩個視角）。
    """
    return [m["name"] for m in parse_multipath_detail(text)]


def parse_multipath_detail(text: str) -> list[dict]:
    """multipath -ll → 每個多路徑對應的完整身分（2026-10-07，搬遷重建）。

    為什麼非要不可：**WWID 才是磁碟的身分**，`mpatha` 這個名字是本機 bindings
    檔分配的流水號。搬遷後若 bindings 重建，`mpatha` 很可能指到另一顆 LUN——
    而 fstab 寫的是 `/dev/mapper/mpatha`。只存名稱等於沒存，接回去會接錯。

    典型輸出（Brocade/一般 DM-multipath）::

        mpatha (3600508b4000156d700012000000b0000) dm-3 HP,HSV200
        size=100G features='1 queue_if_no_path' hwhandler='0' wp=rw
        |-+- policy='round-robin 0' prio=1 status=active
        | `- 1:0:0:1 sdb 8:16 active ready running
        `-+- policy='round-robin 0' prio=1 status=enabled
          `- 2:0:0:1 sdc 8:32 active ready running

    `paths` 的每一筆帶 `hctl`（H:C:T:**L** 的 L 就是 LUN 號）、裝置名與狀態字。
    **路徑數本身就是一個要比對的事實**：搬遷後只剩一條路＝備援沒接回來，
    平常完全正常，直到那條路壞掉。
    """
    out: list[dict] = []
    cur: dict | None = None
    for raw_ln in text.splitlines():
        ln = raw_ln.rstrip()
        if not ln.strip():
            continue
        # 對應首行：名稱 (WWID) [dm-N] [廠牌,型號]。WWID 容許 16 進位與底線。
        head = re.match(r"^(\S+)\s+\(([0-9a-fA-FxX_-]+)\)\s*(dm-\d+)?\s*(.*)$", ln)
        if head and not ln.startswith(("|", "`", " ", "\t", "size=")):
            cur = {"name": head.group(1), "wwid": head.group(2),
                   "dm": head.group(3), "vendor_model": (head.group(4) or "").strip() or None,
                   "size": None, "write_protect": None, "paths": []}
            out.append(cur)
            continue
        if cur is None:
            continue
        sz = re.search(r"size=(\S+)", ln)
        if sz:
            cur["size"] = sz.group(1)
            wp = re.search(r"wp=(\S+)", ln)
            if wp:
                cur["write_protect"] = wp.group(1)
            continue
        # 路徑行：`- 1:0:0:1 sdb 8:16 active ready running
        p = re.search(r"(\d+:\d+:\d+:\d+)\s+(\S+)\s+(\d+:\d+)\s+(.*)$", ln)
        if p:
            hctl = p.group(1)
            cur["paths"].append({
                "hctl": hctl,
                "lun": hctl.rsplit(":", 1)[1],      # H:C:T:L 的 L
                "dev": p.group(2),
                "major_minor": p.group(3),
                "state": " ".join(p.group(4).split()) or None,
            })
    for m in out:
        m["path_count"] = len(m["paths"])
    return out


def parse_raid_hw(text: str) -> dict:
    """硬體 RAID 控制器的唯讀 show 輸出 → 控制器／虛擬磁碟／實體磁碟（2026-10-07）。

    為什麼要做：原本這段原文只被壓成 `raid_hw: True`（「有裝 RAID 工具」）。
    **而重建最需要的就是這段**：RAID 層級、條帶大小、哪幾顆實體碟組成哪個陣列。
    這些全在 `raw_json` 裡躺著，一次都沒被判讀過。

    支援兩種家族的輸出（指令都已在 sudoers 白名單內，不必新增權限）：

    * **storcli／perccli**（LSI/MegaRAID、Dell PERC）—— `/call show` 的 TOPOLOGY 表
    * **ssacli**（HPE Smart Array）—— `ctrl all show config` 的縮排段落

    ⚠️ 兩家的欄位名與排版完全不同，**刻意不壓成同一組欄位**：壓了兩邊都失真。
    `source` 欄記這份是哪一家解出來的，看的人才知道該怎麼讀。
    ⚠️ 解不出來不等於沒有 RAID。`parsed=False` 時 `note` 會講明「原文在
    raw_json，格式沒認出來」—— 那是要人去看原文，不是這台沒有陣列。
    """
    t = text or ""
    if not t.strip():
        return {"parsed": False, "source": None, "note": "沒有輸出（可能沒裝 RAID 工具或沒授權）"}

    low = t.lower()
    if "ssacli" in low or "smart array" in low or "logicaldrive" in low:
        return _parse_ssacli(t)
    if "storcli" in low or "perccli" in low or "topology" in low or re.search(r"^Controller\s*=\s*\d+", t, re.MULTILINE):
        return _parse_storcli(t)
    return {"parsed": False, "source": None,
            "note": "有輸出但格式沒認出來（原文已存在 raw_json，請貼實際輸出來補解析）"}


def _parse_storcli(text: str) -> dict:
    """storcli／perccli `/call show` → 控制器與虛擬磁碟。

    TOPOLOGY 表的欄位順序是 `DG Arr Row EID:Slot DID Type State BT Size ...`，
    其中 `Type` 那一欄對 DG 層是 RAID 層級（RAID1／RAID5…）、對碟層是 `DRIVE`。
    """
    out: dict = {"parsed": True, "source": "storcli", "controllers": [], "virtual_drives": [], "drives": []}
    for m in re.finditer(r"^\s*Product Name\s*=\s*(.+)$", text, re.MULTILINE):
        out["controllers"].append({"model": m.group(1).strip()})
    for m in re.finditer(r"^\s*FW Package Build\s*=\s*(.+)$", text, re.MULTILINE):
        if out["controllers"]:
            out["controllers"][-1]["firmware"] = m.group(1).strip()
    # VD 清單：`0/0   RAID1 Optl  RW     Yes     RWBD  -   ON  278.464 GB`
    for ln in text.splitlines():
        vd = re.match(r"^\s*(\d+)/(\d+)\s+(RAID\d+)\s+(\S+)\s+(\S+)\s+\S+\s+(\S+)\s+\S*\s*(\S+)\s+([\d.]+\s*\w+)",
                      ln)
        if vd:
            out["virtual_drives"].append({
                "dg": vd.group(1), "vd": vd.group(2), "raid_level": vd.group(3),
                "state": vd.group(4), "access": vd.group(5),
                "cache": vd.group(6), "size": vd.group(8).strip(),
            })
            continue
        # 實體碟：`0:1   9  Onln  0 278.464 GB SAS HDD N N 512B ST300MM0008 U`
        pd = re.match(r"^\s*(\d+:\d+)\s+(\d+)\s+(\S+)\s+(\d+)\s+([\d.]+\s*\w+)\s+(\S+)\s+(\S+)", ln)
        if pd:
            out["drives"].append({
                "eid_slot": pd.group(1), "did": pd.group(2), "state": pd.group(3),
                "dg": pd.group(4), "size": pd.group(5).strip(),
                "interface": pd.group(6), "medium": pd.group(7),
            })
    if not (out["controllers"] or out["virtual_drives"] or out["drives"]):
        return {"parsed": False, "source": "storcli",
                "note": "認得是 storcli 系列，但表格沒解出內容（原文在 raw_json）"}
    return out


def _parse_ssacli(text: str) -> dict:
    """ssacli `ctrl all show config` → 陣列與邏輯磁碟（HPE Smart Array）。

    輸出是縮排段落：`array A` 底下掛 `logicaldrive 1 (279.4 GB, RAID 1, OK)`
    與 `physicaldrive 1I:1:1 (port 1I:box 1:bay 1, SAS, 300 GB, OK)`。
    """
    out: dict = {"parsed": True, "source": "ssacli", "controllers": [], "virtual_drives": [], "drives": []}
    cur_array = None
    for ln in text.splitlines():
        s = ln.strip()
        c = re.match(r"^(Smart Array \S+|HPE Smart Array \S+)\s+in Slot\s+(\S+)", s)
        if c:
            out["controllers"].append({"model": c.group(1), "slot": c.group(2)})
            continue
        a = re.match(r"^array\s+(\S+)", s, re.IGNORECASE)
        if a:
            cur_array = a.group(1).rstrip("()")
            continue
        ld = re.match(r"^logicaldrive\s+(\S+)\s*\(([^)]*)\)", s, re.IGNORECASE)
        if ld:
            parts = [p.strip() for p in ld.group(2).split(",")]
            lvl = next((p for p in parts if p.upper().startswith("RAID")), None)
            out["virtual_drives"].append({
                "array": cur_array, "vd": ld.group(1),
                "size": parts[0] if parts else None,
                "raid_level": lvl, "state": parts[-1] if len(parts) > 1 else None,
            })
            continue
        pd = re.match(r"^physicaldrive\s+(\S+)\s*\(([^)]*)\)", s, re.IGNORECASE)
        if pd:
            parts = [p.strip() for p in pd.group(2).split(",")]
            out["drives"].append({
                "array": cur_array, "id": pd.group(1),
                "position": parts[0] if parts else None,
                "size": next((p for p in parts if re.match(r"^[\d.]+\s*[GTM]B$", p, re.IGNORECASE)), None),
                "state": parts[-1] if len(parts) > 1 else None,
            })
    if not (out["controllers"] or out["virtual_drives"] or out["drives"]):
        return {"parsed": False, "source": "ssacli",
                "note": "認得是 ssacli，但段落沒解出內容（原文在 raw_json）"}
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


#: `spec_json` 裡「要靠哪一條指令的原文才算得出來」的對照。
#: 重判讀時用它分辨五件事（這五件在畫面上長得一模一樣，混在一起等於沒收）：
#:   · 這台**形態上就不會有**（VM 沒有實體 RAID 控制器）→ not_applicable
#:   · 這台**根本沒收過**（整份 raw_json 是空的）→ not_collected
#:   · 那條指令**沒跑**（raw_json 有料但沒有這個鍵）→ not_run
#:   · 跑了但**沒有輸出**（沒裝工具／沒授權）→ no_output
#:   · 跑了有輸出但**解不出來** → unparsed，要人看原文（那是我們的缺陷）
_LINUX_EXTRA_SOURCES = {
    "dimm": "dimm", "bios": "bios", "disks": "smart",
    "multipath": "multipath", "multipath_detail": "multipath",
    "raid_sw": "mdstat", "raid_sw_detail": "mdstat",
    "raid_hw": "raid_hw", "raid_hw_detail": "raid_hw",
    "gpu": "lspci", "raid_controllers": "lspci",
    "kernel": "uname", "uptime": "uptime", "tools_missing": "tools",
}

#: 虛擬機**形態上不可能有**的項目 → 空白的原因是「不適用」，不是「缺資料」。
#:
#: 為什麼一定要分出來（2026-10-07 使用者指出）：VM 的硬體 RAID 之前被歸成
#: 「未收集（重收一次就有）」。那句話對 VM 是錯的——重收一百次也不會有。
#: 使用者照著去重收、再看到同一片空白，是我們的文案在騙人。
#:
#: 刻意只列**真的不可能**的兩項，其餘寧可留在「未裝／未查」：
#:   · raid_hw：VM 看到的是 hypervisor 給的虛擬磁碟，沒有實體 RAID 控制器
#:   · disks（SMART）：虛擬磁碟沒有 SMART 資料
#: 不列 multipath／dimm／bios——VM **真的可能有**（iSCSI／FC passthrough、
#: 虛擬 DIMM、虛擬 BIOS 都會有輸出）。標成「不適用」會是沒有證據的斷言，
#: 而且方向更糟：把「其實該有卻沒收到」寫成「本來就沒有」，沒人會再去查。
_VM_NOT_APPLICABLE = {
    "raid_hw": "VM，無 RAID", "raid_hw_detail": "VM，無 RAID",
    "disks": "VM，無 SMART",
}

#: 「未裝 X」要講得出是哪個工具（六字以內，照使用者示範的長度）。
_SRC_TOOL = {
    "multipath": "multipath", "mdstat": "mdraid", "raid_hw": "RAID 工具",
    "dimm": "dmidecode", "bios": "dmidecode", "smart": "smartctl",
    "lspci": "lspci", "uname": "uname", "uptime": "uptime", "tools": "tools",
}


def _is_vm_row(is_vm, device_model) -> bool:
    """這一列是不是虛擬機。**VM 判定只能有一份定義**。

    `is_vm` 旗標不可靠（上游 CMDB 常常沒填），所以也認 `device_model` 上的
    標記。原本這段寫在 `run_collection` 裡，`reparse_stored` 要用就得抄一份
    ——同 `_platform_of` 手抄本的教訓，抽出來共用。
    """
    dm = device_model or ""
    return (bool(is_vm) or "(VM)" in dm or dm.strip().upper() == "VM"
            or "virtual" in dm.lower())


def build_linux_spec_extra(raw: dict) -> dict:
    """Linux 的 `spec_json` 內容。**收集與重判讀共用這一份**（2026-10-07）。

    為什麼抽出來：重判讀（`reparse_stored`）必須產出跟收集完全一樣的結構。
    各寫一份的話，哪天改了解析只有一邊跟上，而「重判讀過的機器」與
    「剛收過的機器」會長得不一樣——那種不一致最難查，因為兩邊各自看都正常。
    （同 `_platform_of` 那個手抄本的教訓：同一件事只能有一份定義。）

    `raw` 是收集時存進 `host_spec.raw_json` 的那個 dict，所以這支函式
    **不碰任何機器**，純文字進、結構出。
    """
    c = parse_lscpu(raw.get("lscpu", ""))
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
        # ---- 2026-10-07 搬遷重建：同一份原文的「完整身分」視角 ----
        # 舊鍵（multipath／raid_sw／raid_hw）形狀一律不動，畫面照舊能用；
        # 重建要的明細放在 *_detail，新畫面讀這幾個。
        "multipath_detail": parse_multipath_detail(raw.get("multipath", "")),
        "raid_sw_detail": parse_mdstat_detail(raw.get("mdstat", "")),
        "raid_hw": bool((raw.get("raid_hw", "") or "").strip()),   # 有硬體 RAID 工具輸出（原文存 raw_json）
        "raid_hw_detail": parse_raid_hw(raw.get("raid_hw", "")),
    }
    # dimm 空（count=0）就不放，避免畫面出現空殼
    if not extra["dimm"].get("count"):
        extra.pop("dimm", None)
    # raid_hw_detail 的 parsed=False 要**留著**：它帶的 note 正是「為什麼沒有內容」，
    # 被 `if v` 濾掉就又變成一片空白了。
    rhd = extra.pop("raid_hw_detail", None)
    out = {k: v for k, v in extra.items() if v}
    if rhd and (rhd.get("parsed") or rhd.get("note")):
        out["raid_hw_detail"] = rhd
    return out


def extra_coverage(raw: dict, extra: dict, is_vm: bool = False) -> dict:
    """每個明細項目「空白是哪一種空白」的逐項交代（2026-10-07，文案同日改）。

    ## 五種空白必須分得出來

    這份資料的用途是出事時拿得出來，所以空白不可以一律寫「無資訊」——
    那等於沒分。`state` 一共六種（一種有料、五種空白）：

    | state           | label 例      | 意思                           |
    |-----------------|---------------|--------------------------------|
    | collected       | None          | 有內容                         |
    | not_applicable  | `VM，無 RAID` | 形態上不會有，重收也不會有     |
    | not_collected   | `未收集`      | 這台整份沒收過                 |
    | not_run         | `未查`        | 收過，但沒跑這條指令           |
    | no_output       | `未裝 multipath` | 跑了沒輸出（沒裝／沒授權）  |
    | unparsed        | `解析失敗`    | 有原文解不出——**我們的缺陷**  |

    ## 為什麼回 label 而不是讓畫面自己翻

    畫面上只有一格的寬度。使用者示範的長度就是 `硬體 RAID    VM，無 RAID`
    ——**六個字以內、不寫成句子**。長句子（`本台為虛擬機，無實體 RAID 控制器`）
    塞不進去，塞進去就被截斷，截斷後反而分不出是哪一種空白。

    所以短文案放 `label`（畫面直接印），完整理由留在 `reason`（點進去才看）。
    翻譯表只能有一份，放在後端——放前端的話 API 的其他消費者（匯出、報表）
    就得各自再抄一份。

    `unparsed` 另外給 `raw_key`：那是我們自己的解析缺陷，畫面要能點進去看原文，
    否則留了證據卻沒有路徑走到它。
    """
    out: dict = {}
    # 整份 raw 是空的＝這台沒收過。跟「收過但這條沒跑」是兩件事：
    # 前者要去納管／收集，後者要去看收集腳本為什麼跳過那條。
    never_collected = not raw
    for key, src in _LINUX_EXTRA_SOURCES.items():
        if is_vm and key in _VM_NOT_APPLICABLE:
            out[key] = {"state": "not_applicable",
                        "label": _VM_NOT_APPLICABLE[key],
                        "reason": "這台是虛擬機，形態上沒有這個項目（重新收集不會有，"
                                  "不是資料缺漏）"}
            continue
        if never_collected:
            out[key] = {"state": "not_collected", "label": "未收集",
                        "reason": "這台沒有任何收集紀錄（raw_json 是空的）"}
            continue
        if src not in raw:
            out[key] = {"state": "not_run", "label": "未查",
                        "reason": f"這台的收集紀錄裡沒有 `{src}` 這條指令的輸出"
                                  "（收集時間早於這個項目，重收一次就有）"}
            continue
        if not (raw.get(src) or "").strip():
            tool = _SRC_TOOL.get(src, src)
            out[key] = {"state": "no_output", "label": f"未裝 {tool}",
                        "reason": f"`{src}` 指令跑了但沒有輸出（工具沒裝、或唯讀 sudo 沒授權）"}
            continue
        val = extra.get(key)
        if val in (None, [], {}, False) or (isinstance(val, dict) and val.get("parsed") is False):
            out[key] = {"state": "unparsed", "label": "解析失敗", "raw_key": src,
                        "reason": f"`{src}` 有原文但解不出內容（原文已在 raw_json，可貼出來補解析）"}
            continue
        out[key] = {"state": "collected", "label": None, "reason": None}
    return out


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
        spec_extra = build_linux_spec_extra(raw)
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


def reparse_stored(conn, only_serial: str | None = None) -> dict:
    """把**已經在 DB 裡的** `host_spec.raw_json` 重新判讀一次（2026-10-07，搬遷重建第一批）。

    ## 為什麼有這支

    收集時每一條指令的原文都存進了 `raw_json`，但解析器當年只取走其中一小部分：
    `raid_hw` 整段 RAID 組態被壓成一個 `True`、`multipath` 只留名稱不留 WWID、
    `mdstat` 只留一句摘要不留成員碟、DIMM 不留插槽位置。
    那些內容**一直都在**，只是沒有人判讀過。

    所以這一刀**完全不碰任何機器**：不連線、不跑指令、不需要權限、不需要真機驗證。
    純粹是「把眼前流過去又存下來的東西，重新讀一遍」。風險等於零，CP 值最高。

    ## 不會蓋掉什麼

    * 只改 `spec_json`，**不動** `cpu`／`cores`／`mem_mb`／`storage*`／`model`／`serial`／`raw_json`
    * `net_notes` 是收集器寫進 `spec_json` 的「為什麼收不到」，**原樣保留**
      （它不是從 raw_json 算出來的，重算會把它弄丟）
    * `collected_at` **不更新**——重判讀沒有讓資料變新，改了會讓人以為剛收過
      （這是誠實度問題，不是潔癖：搬遷前看新鮮度要看得準）

    ## 只做 Linux

    AIX 的 `spec_json` 只有 `disks_aix`／`lpar_id`／`notes`，沒有可再挖的原文；
    Windows 目前沒有 host_spec。所以非 linux 的列一律回報 `skipped`，**不是失敗**。
    """
    import json

    # LEFT JOIN hardware 只為了拿 is_vm／device_model——coverage 要分得出
    # 「VM 形態上沒有」與「該有卻沒收到」。LEFT 是刻意的：資產表沒有對應列時
    # is_vm 為 NULL → 當成非 VM → 退回「未查／未裝」的舊說法。
    # 那是**保守的方向**：寧可說「沒查到」要人去看，不要擅自宣告「本來就沒有」。
    sql = ("SELECT hs.asset_serial AS asset_serial, hs.platform AS platform, "
           "hs.raw_json AS raw_json, hs.spec_json AS spec_json, "
           "hw.is_vm AS is_vm, hw.device_model AS device_model "
           "FROM host_spec hs "
           "LEFT JOIN hardware hw ON hw.asset_serial = hs.asset_serial")
    params: list = []
    if only_serial:
        sql += " WHERE hs.asset_serial = ?"
        params.append(only_serial)
    rows = conn.execute(sql, params).fetchall()

    updated, skipped, no_raw, unchanged = [], [], [], []
    for r in rows:
        serial = r["asset_serial"]
        if (r["platform"] or "") != "linux":
            skipped.append({"asset_serial": serial,
                            "reason": f"平台是 {r['platform'] or '未知'}，沒有可再判讀的原文"})
            continue
        try:
            raw = json.loads(r["raw_json"]) if r["raw_json"] else None
        except (ValueError, TypeError):
            raw = None
        if not isinstance(raw, dict) or not raw:
            # ⚠️ 這不是「這台沒有 RAID」，是「這台沒有留原文可以重判」。
            # 兩者混在一起，就會有人以為查過了。
            no_raw.append({"asset_serial": serial,
                           "reason": "這台的 host_spec 沒有留下 raw_json（收集時間早於原文保存），"
                                     "要重收一次才有東西可以判讀"})
            continue
        try:
            old = json.loads(r["spec_json"]) if r["spec_json"] else {}
        except (ValueError, TypeError):
            old = {}
        if not isinstance(old, dict):
            old = {}

        new = build_linux_spec_extra(raw)
        # 收集器寫進去、不是從 raw 算來的欄位要留著（否則重判讀會把理由弄丟）
        for keep in ("net_notes", "notes"):
            if keep in old and keep not in new:
                new[keep] = old[keep]
        new["coverage"] = extra_coverage(
            raw, new, is_vm=_is_vm_row(r["is_vm"], r["device_model"]))
        new["reparsed_at"] = _now(conn)

        # 比對時不看 reparsed_at，否則每一次都會算成「有變」
        if {k: v for k, v in new.items() if k != "reparsed_at"} == \
           {k: v for k, v in old.items() if k != "reparsed_at"}:
            unchanged.append(serial)
            continue
        conn.execute("UPDATE host_spec SET spec_json = ? WHERE asset_serial = ?",
                     (json.dumps(new, ensure_ascii=False), serial))
        gained = sorted(k for k in new if k not in old)
        updated.append({"asset_serial": serial, "gained": gained})
    conn.commit()
    return {"total": len(rows), "updated": len(updated), "unchanged": len(unchanged),
            "skipped": len(skipped), "no_raw": len(no_raw),
            "updated_detail": updated[:50], "skipped_detail": skipped[:20],
            "no_raw_detail": no_raw[:20]}


def _now(conn) -> str:
    """本地時間字串。走 Python 明確寫入（決策 T6），不倚賴資料表 DEFAULT。"""
    return conn.execute("SELECT datetime('now','localtime')").fetchone()[0]


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
        # VM 無 PSU，跳過 dmidecode（判定走 _is_vm_row，與 reparse 共用同一份定義）
        is_vm = _is_vm_row(t["is_vm"], t["device_model"])
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
