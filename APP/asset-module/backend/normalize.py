"""髒資料正規化：把同一個東西的各種寫法收斂成標準名。

## 為什麼需要

同一份資料庫裡實際存在的（2026-07-19 真實資料）：
    Rocky Linux 9.7          ← Excel 匯入的
    Rocky Linux 9.7 (Blue Onyx)  ← facts 從機器上收的
    VMware VM                ← 人填的
    VMware Virtual Platform  ← DMI 讀出來的
不收斂就統計不出「我有幾台 Rocky 9.7」，因為它們是兩個不同的字串。

## 核心原則：原值永不改

正規化**不是**把原值改掉，是原值和標準值兩個都留：
    os              = "Rocky Linux 9.7 (Blue Onyx)"   ← 來源說什麼就是什麼，永不動
    os_canonical    = "Rocky Linux 9.7"               ← 算出來的標準名
哪天發現規則錯了，重跑就好；當初若直接覆蓋，原始資訊就永遠沒了。

## 兩層策略（單用一層都會壞）

1. **規則解析**：抓廠商＋產品＋版本。版本組合是無限的，字典列不完。
2. **別名字典**：規則橋不了的真實字差（Windows 11 → Microsoft Windows 11）。
   字典存 DB，人可以補，補完立刻生效。

查不到的**不亂猜**——標成 unmatched、原值原樣保留，並讓它出現在「待對應清單」，
人補一次就好。靜默猜錯比留白更糟：留白看得出來，猜錯看不出來。

## 為什麼不存成欄位（不做 normalized 欄位）

別名字典是會被人補的。存成欄位就會有「補了字典但舊資料還是舊的」的不同步問題，
而且要記得重跑。改成讀取時即時算：字典一補，全站立刻一致。
資料量到十萬筆再考慮加快取。
"""
from __future__ import annotations

import re

# ---- OS 規則：(比對樣式, 標準廠商, 標準產品名) ----
# 順序有意義：先具體後籠統（Windows Server 要排在 Windows 之前）。
_OS_RULES: list[tuple[re.Pattern, str, str]] = [
    # --- Windows（含無空格的 windows2022、桌面版）---
    (re.compile(r"\bwindows\s*server\b|\bwindows\s*20(1[26]|19|22)\b", re.I), "Microsoft", "Windows Server"),
    (re.compile(r"\bwindows\b", re.I), "Microsoft", "Windows"),
    # --- Linux 發行版（具體排前面：Oracle Linux／CoreOS 含 linux/red hat 字樣，要先攔）---
    (re.compile(r"\boracle\s*linux\b", re.I), "Oracle", "Oracle Linux"),
    (re.compile(r"\b(rhcos|red\s*hat\s*coreos|coreos)", re.I), "Red Hat", "Red Hat CoreOS"),
    (re.compile(r"\brocky\s*linux\b", re.I), "Rocky Enterprise Software Foundation", "Rocky Linux"),
    (re.compile(r"\balma\s*linux\b", re.I), "AlmaLinux OS Foundation", "AlmaLinux"),
    (re.compile(r"\bubuntu\b", re.I), "Canonical", "Ubuntu"),
    (re.compile(r"\bdebian\b", re.I), "Debian Project", "Debian"),
    (re.compile(r"\b(red\s*hat|redhat|rhel)", re.I), "Red Hat", "Red Hat Enterprise Linux"),
    (re.compile(r"\bcentos\b", re.I), "CentOS Project", "CentOS"),
    (re.compile(r"\b(suse|sles)\b", re.I), "SUSE", "SUSE Linux Enterprise"),
    (re.compile(r"\bfedora\b", re.I), "Fedora Project", "Fedora"),
    # --- Unix / IBM i ---
    (re.compile(r"\baix\b", re.I), "IBM", "AIX"),
    (re.compile(r"\bsolaris\b", re.I), "Oracle", "Solaris"),
    (re.compile(r"\bhp-?ux\b", re.I), "HPE", "HP-UX"),
    # IBM i（AS/400）。裸版號 VxRy 也算——但只認 V5~V7，理由見下面 _IBM_I_VR 的說明。
    (re.compile(r"\bibm\s*i\b|\bos/?400\b|\bi5/?os\b|^\s*v[5-7]r\d+", re.I), "IBM", "IBM i"),
    # --- 虛擬化平台（ESXi 當 OS）---
    (re.compile(r"\besxi?\b|\bvsphere\b", re.I), "VMware", "VMware ESXi"),
    # --- VMware 管理軟體（不是 ESXi 本身，是管理/監控平台，使用者 2026-08-13
    # 要求補上——資產用途欄常見「VMware Center」「VROPS」「vrni-platform-release」
    # 這幾個字樣，原本只認 ESXi/vSphere 沒收）---
    (re.compile(r"\bvcenter\b|\bvmware\s*center\b", re.I), "VMware", "VMware vCenter Server"),
    (re.compile(r"\bvrops\b|\bvrealize\s*operations\b", re.I), "VMware", "VMware vRealize Operations"),
    (re.compile(r"\bvrni\b|\bvrealize\s*network\s*insight\b", re.I), "VMware", "VMware vRealize Network Insight"),
    # --- 資料庫/應用軟體（使用者 2026-08-13 發現：登打人員常把資料庫軟體版本誤填進
    # OS 欄，技術偵測沒錯但維護權責是「軟體」組不是作業系統組，跟 os_category() 的
    # _SOFTWARE_PRODUCTS 搭配自動分進「軟體」分頁，不用每筆手動搬）---
    (re.compile(r"\bmariadb\b", re.I), "MariaDB Foundation", "MariaDB"),
    (re.compile(r"\bmysql\b", re.I), "Oracle", "MySQL"),
    (re.compile(r"\bpostgres(?:ql)?\b", re.I), "PostgreSQL Global Development Group", "PostgreSQL"),
    (re.compile(r"\bmongo\s*db\b", re.I), "MongoDB Inc.", "MongoDB"),
    (re.compile(r"\bsql\s*server\b|\bmssql\b", re.I), "Microsoft", "Microsoft SQL Server"),
    (re.compile(r"\boracle\s*database\b|\boracle\s*db\b", re.I), "Oracle", "Oracle Database"),
    (re.compile(r"\bdb2\b", re.I), "IBM", "IBM Db2"),
    (re.compile(r"\bhana\b", re.I), "SAP", "SAP HANA"),
    (re.compile(r"\bredis\b", re.I), "Redis Ltd.", "Redis"),
    # --- 網路設備 OS ---
    # 使用者 2026-08-13 實際發現：APIC（SDN 控制器）、ISE（身分識別引擎）是跟 NX-OS/IOS-XE
    # 完全不同生命週期的獨立產品，混進籠統的「Cisco Network OS」查不到 EOS（查的是個不存在
    # 的假產品名）。具體產品要排在籠統的 \bcisco\b 前面攔截，跟 Oracle Linux 要排在
    # 籠統 Linux 規則前面是同一個道理。
    (re.compile(r"\bapic\b", re.I), "Cisco", "Cisco APIC (ACI)"),
    (re.compile(r"\bcisco\s*ise\b|\bidentity\s*services\s*engine\b", re.I), "Cisco", "Cisco ISE"),
    (re.compile(r"\bnx-?os\b|\bios-?xe\b|\bcisco\b", re.I), "Cisco", "Cisco Network OS"),
    (re.compile(r"\bfortigate\b|\bfortios\b|\bfos\s*v?\d", re.I), "Fortinet", "FortiOS"),
    (re.compile(r"\bbig-?ip\b", re.I), "F5", "F5 BIG-IP"),
    (re.compile(r"\bjunos\b", re.I), "Juniper", "Junos"),
    (re.compile(r"voice\s*gateway", re.I), "", "Voice Gateway"),
    # --- BMC / 韌體 / 設備類型標籤（OS 欄填的其實是設備類型時，也收斂）---
    (re.compile(r"\bi?drac\b", re.I), "Dell", "Dell iDRAC (BMC)"),
    (re.compile(r"\bavamar\b", re.I), "Dell EMC", "Avamar"),
    (re.compile(r"\bunisphere\s*central\b", re.I), "Dell EMC", "Unisphere Central"),
    (re.compile(r"儲存設備|\bstorage\b", re.I), "", "儲存設備"),
    (re.compile(r"網路設備", re.I), "", "網路設備"),
    (re.compile(r"客製化系統", re.I), "", "客製化系統"),
    # 使用者 2026-08-13 實際發現：跟「網路設備」「儲存設備」同一批泛用設備類型標籤，
    # 之前漏收。
    (re.compile(r"電力設備", re.I), "", "電力設備"),
]

# 版本號：抓第一組看起來像版本的數字（9.7 / 22.04 / 13 / 2022）。
# 使用者 2026-08-13 實際發現：版本號緊黏在字母後面沒空格時（如「V15.4(3)M3」、
# 「ESXi7.0U3」），左邊要求 \b 會因為「字母＋數字」中間沒有邊界而抓不到開頭，
# регex 只好從小數點後面重新起頭，抓出「4」這種荒謬的殘缺版本號。改成左邊不強制
# 邊界（只保留右邊 \b，避免抓到數字後面黏著的雜訊字尾），兩種寫法都收得到完整版本。
_VERSION = re.compile(r"(\d+(?:\.\d+)*)\b")

# IBM i（AS/400）的版號格式：V<主版本>R<修訂版>，例如 V7R3 ＝ IBM i 7.3。
#
# 只認 V5~V7 是刻意的：IBM i 至今最高就是 7.5，沒有 V8 以上。實際資料裡的
# 「V10R3」是 **HMC（硬體管理台）** 的版本，不是作業系統；「V8R8.6.0」也不是
# IBM i 的版號格式。原本規則寫成 `v\d+r\d+` 通吃，把這些通通當成 IBM i，
# 使用者的月報因此多算了 6 台（他實際只有 11 台）。
#
# 認不出來的**不猜**——落到「未分類」讓人工判斷，那正是待處理清單該有的東西。
# 這是本檔開頭「查不到的不亂猜」原則，不因為想讓數字好看就破例。
_IBM_I_VR = re.compile(r"^v(\d+)r(\d+)", re.I)

# 代號／發行名：Rocky 的 (Blue Onyx)、Debian 的 (trixie)、Ubuntu 的 LTS 字樣。
# 這些不影響「是哪個版本」，是造成同物異名的主因，正規化時要拿掉。
_CODENAME = re.compile(r"[\(（][^）)]*[\)）]")
# ⚠️ 只去「不影響版本判讀」的字。**不可以**把 linux 當雜訊拿掉——
# 產品名本身就含它（Rocky Linux），先拆掉再比對規則就永遠比不到（實際踩過）。
_NOISE = re.compile(r"\b(lts)\b", re.I)

# ---- 機型規則 ----
_MODEL_RULES: list[tuple[re.Pattern, str, str]] = [
    # 使用者 2026-08-13 實際發現：ATEN KVM-over-IP 切換器／主機隨附的實體 KVM console
    # 埠，device_model 常直接寫「ATEN ... KVM」「Dell R330 Server KVM」，被下面那條
    # 抓「kvm」單字的虛擬化規則誤吃成「KVM Virtual Machine」——這些是實體周邊設備，
    # 不是虛擬機，順序上要排在虛擬化規則之前才會贏。ATEN 沿用跟 _MODEL_TO_OS 同一個
    # 廠牌名，方便追查是同一批設備。
    (re.compile(r"\baten\b", re.I), "ATEN", "ATEN KVM-over-IP Switch"),
    # VMware 的虛擬機在不同來源叫法不同：VMware VM / VMware Virtual Platform /
    # VMware7,1 …… 都是同一件事「這是一台 VMware 虛擬機」
    (re.compile(r"\bvmware\b", re.I), "VMware", "VMware Virtual Machine"),
    (re.compile(r"\b(kvm|qemu)\b", re.I), "QEMU", "KVM Virtual Machine"),
    (re.compile(r"\bvirtualbox\b", re.I), "Oracle", "VirtualBox VM"),
    (re.compile(r"\bhyper-?v\b|\bvirtual machine\b", re.I), "Microsoft", "Hyper-V Virtual Machine"),
    # 使用者 2026-08-13 實際發現：device_model 也會寫成「(VM)-Hyper」這種帶「(VM)」
    # 標記＋平台縮寫但沒打全「Hyper-V」的寫法，上面那條 \bhyper-?v\b 接不到（缺 V）。
    # 只在「(VM)」語境下才放寬到裸「hyper」，避免誤吃 Cisco HyperFlex 這類不相干產品
    # （HyperFlex 沒有「(VM)」前綴，不會被這條攔到）。
    (re.compile(r"\(vm\)[\s_-]*hyper\b", re.I), "Microsoft", "Hyper-V Virtual Machine"),
    # 使用者 2026-08-13 實際發現：device_model 欄常常只填「(VM)」這種泛用標記，
    # 沒說是哪個虛擬化平台——跟上面幾條「認得出平台」的規則分開，收斂成同一個
    # 「平台不明」桶，一樣算進「虛擬化」大分類（見 _HW_VM_PRODUCTS）。
    (re.compile(r"^\(?vm\)?$", re.I), "", "Virtual Machine (平台不明)"),
    (re.compile(r"\bproliant\b", re.I), "HPE", "ProLiant"),
    (re.compile(r"\bpoweredge\b", re.I), "Dell", "PowerEdge"),
    (re.compile(r"\bthinksystem\b|\bsystem x\b", re.I), "Lenovo", "ThinkSystem"),
    (re.compile(r"\bsupermicro\b|\bsuper micro\b", re.I), "Supermicro", "Supermicro Server"),

    # --- Cisco 網路設備：依家族分別歸類，同一台 Catalyst 因子型號寫法不同
    # （WS-C2960X-24TDL／C2960X-48TD-L／Catalyst C2960X-48TD-L）本來會被當成
    # 好幾種不同機型，這裡收斂成「家族」。⚠️ 順序有意義：具體家族要排在
    # 籠統家族前面，否則會被前面的規則搶先攔截（使用者 2026-08-11 要求補強，
    # 目的是讓硬體 EOS 對得上這裡收斂出來的家族名）。
    (re.compile(r"\bC9300X\b", re.I), "Cisco", "Cisco Catalyst 9300 series switch"),
    (re.compile(r"\bC9300\b", re.I), "Cisco", "Cisco Catalyst 9300 series switch"),
    (re.compile(r"\bC9200L?\b", re.I), "Cisco", "Cisco Catalyst 9200 series switch"),
    (re.compile(r"\bC2960\s*\+|\bC2960plus\b", re.I), "Cisco", "Cisco Catalyst 2960 Plus series switch"),
    (re.compile(r"\bC2960[SX]\b", re.I), "Cisco", "Cisco Catalyst 2960-X series switch"),
    (re.compile(r"\bC3850\b", re.I), "Cisco", "Cisco Catalyst 3850 series switch"),
    (re.compile(r"\bN9K-C9\d{3,4}", re.I), "Cisco", "Cisco Nexus 9300 series switch"),
    (re.compile(r"\bNEXUS[\s-]?3548\b", re.I), "Cisco", "Cisco Nexus 3548 switch"),
    (re.compile(r"\bISR\s*4451-?X\b", re.I), "Cisco", "Cisco ISR 4451-X router"),
    (re.compile(r"\bASA\s*-?\s*5506-?X\b", re.I), "Cisco", "Cisco ASA 5506-X firewall"),
    (re.compile(r"\bASA\s*-?\s*5512-?X?\b", re.I), "Cisco", "Cisco ASA 5512-X firewall"),
    (re.compile(r"\bASA\s*-?\s*5525-?X?(?:-K9)?\b", re.I), "Cisco", "Cisco ASA 5525-X firewall"),
    # 2900/1900 系列路由器：這兩個型號範圍在真實庫存裡只用來標 Cisco 路由器
    # （常帶 /K9 授權後綴），風險可控，不強求前面一定出現「Cisco」字樣。
    # (?<!\d) 而不是 \b：實測庫存有「Cisco2901/K9」這種廠牌型號沒空格黏在一起的寫法，
    # \b 在字母接數字的地方不會斷詞，抓不到。
    (re.compile(r"(?<!\d)29[0-5]1(?:[/-]K9)?\b", re.I), "Cisco", "Cisco 2900 series router (2901/2921/2951)"),
    (re.compile(r"(?<!\d)19(?:21|41)(?:[/-]K9)?\b", re.I), "Cisco", "Cisco 1900 series router (1921/1941)"),
    (re.compile(r"\bAPIC-?SERVER\b|\bAPIC-[ML]\d", re.I), "Cisco", "Cisco APIC (ACI) appliance"),

    # --- Juniper ---
    (re.compile(r"\bSRX\s*3(?:00|40)\b", re.I), "Juniper", "Juniper SRX300/SRX340 firewall"),
    (re.compile(r"\bEX\s*2200\b", re.I), "Juniper", "Juniper EX2200 switch"),

    # --- IBM / Lenovo x-series（廠牌決定歸哪個世代，同型號 Lenovo 接手後另算）---
    (re.compile(r"\bLenovo\b.*?\bx3550\s*M5\b|\bLenovo\s*[xX]3550\s*M5\b", re.I),
     "Lenovo", "Lenovo System x3550 M5 server"),
    (re.compile(r"\bLenovo\b.*?\bx3650\s*M5\b|\bLenovo\s*[xX]3650\s*M5\b", re.I),
     "Lenovo", "Lenovo System x3650 M5 server"),
    (re.compile(r"\bIBM\b.*?\bx3550\s*M[34]\b", re.I), "IBM", "IBM System x3550 M3/M4 server"),
    (re.compile(r"\bIBM\b.*?\bx3650\s*M[45]\b", re.I), "IBM", "IBM System x3650 M4/M5 server"),
    (re.compile(r"\b7042-CR6\b", re.I), "IBM", "IBM Power 7042-CR6 (Power 750/770 era)"),
    (re.compile(r"\b7063-CR[12]\b", re.I), "IBM", "IBM Power 7063-CR1/CR2 (Power10 S1022/S1024)"),
    (re.compile(r"\bS922\b", re.I), "IBM", "IBM Power S922 server"),
    (re.compile(r"\bS1024\b", re.I), "IBM", "IBM Power S1024 server"),
    (re.compile(r"\bFlashSystem\s*5100\b", re.I), "IBM", "IBM FlashSystem 5100 storage"),
    (re.compile(r"\bStorwize\s*V5000\b", re.I), "IBM", "IBM Storwize V5000 storage"),

    # --- F5 / EMC（沒空格的寫法「UnityXT380」子字串比對抓不到，需要規則收斂）---
    (re.compile(r"\bi2[68]00\b", re.I), "F5", "F5 BIG-IP i2600/i2800 series appliance"),
    (re.compile(r"\bi4800\b", re.I), "F5", "F5 BIG-IP i4800 appliance"),
    # 使用者 2026-08-13 實際發現內部命名常省略「Unity」只寫「EMC XT480」，
    # 兩種寫法都要接得住，否則同一顆陣列因為欄位寫法不同分不到同一個 canonical。
    (re.compile(r"\b(?:Unity\s*)?XT\s*3?80F?\b|\b(?:Unity\s*)?XT\s*4?80F?\b", re.I),
     "Dell EMC", "Dell/EMC Unity XT380/XT480F storage"),

    # --- Oracle 主機一體機（使用者 2026-08-13 點名）---
    (re.compile(r"\bODA\b|\bOracle\s*Database\s*Appliance\b", re.I), "Oracle", "Oracle Database Appliance (ODA)"),
]


def _fmt_fortigate(m: re.Match) -> tuple[str, str]:
    return "Fortinet", f"Fortinet FortiGate {m.group(1).upper()}"


def _fmt_paloalto(m: re.Match) -> tuple[str, str]:
    return "Palo Alto Networks", f"Palo Alto Networks PA-{m.group(1)}"


def _fmt_dell_poweredge(m: re.Match) -> tuple[str, str]:
    return "Dell", f"Dell PowerEdge R{m.group(1)}"


def _fmt_hpe_proliant(m: re.Match) -> tuple[str, str]:
    series = m.group(1).upper()
    num = m.group(2)
    gen = m.group(3)
    return "HPE", f"HPE ProLiant {series}{num}" + (f" Gen{gen}" if gen else "")


def _fmt_oracle_oda(m: re.Match) -> tuple[str, str]:
    model = re.sub(r"\s+", " ", m.group(1).strip()).upper()
    return "Oracle", f"Oracle Database Appliance (ODA) {model}"


def _fmt_oracle_exadata(m: re.Match) -> tuple[str, str]:
    # 使用者 2026-08-13 實際發現：device_model 欄位本身常常就寫著具體世代/規格
    # （「Oracle Exadata X10M High Capacity 1/4 Rack」），原本規則卻收斂成籠統的
    # 「Oracle Exadata」，把已經寫清楚的資訊丟掉——這是規則抓漏，不用猜，直接
    # 拿整段原字串當 canonical。實際資料裡偶爾會重複寫「Exadata Exadata」，
    # 這裡順手去重複；「ExadataCC」這種黏在一起看不懂意思的寫法不硬拆，原樣保留。
    text = re.sub(r"\s+", " ", m.string.strip())
    text = re.sub(r"\b(exadata)\b(\s+\1\b)+", r"\1", text, flags=re.I)
    if not re.match(r"^oracle\b", text, re.I):
        text = f"Oracle {text}"
    return "Oracle", text


def _fmt_hpe_3par(m: re.Match) -> tuple[str, str]:
    model = m.group(1)
    return "HPE", f"HPE 3PAR {model} storage" if model else "HPE 3PAR storage"


def _fmt_emc_ds_switch(m: re.Match) -> tuple[str, str]:
    return "Dell EMC", f"EMC DS{m.group(1).upper()} SAN Switch"


def _fmt_ibm_san_switch(m: re.Match) -> tuple[str, str]:
    return "IBM", f"IBM {m.group(1).upper()} SAN Switch"


# ---- 機型規則（動態帶版本碼）----
# Fortinet 這類型號種類太多，一個個寫死規則列不完；抓廠牌前綴＋型號碼，動態組出
# canonical，兩邊寫法怎麼變（Fortinet FG-101F／FortinetFG-60F／Fortinet_FG100D／
# 裸的 FG-101F）都收斂成同一種格式，才追得上這個系統實際看到的寫法多樣性。
_MODEL_CODE_RULES: list[tuple[re.Pattern, "Callable"]] = [
    # 使用者 2026-08-13 實際發現：「FortiGate」完整拼法（Forti+Gate）字母上根本不含
    # 連續的「fg」，跟下面兩條各自針對「fortinet+fg縮寫」「裸fg縮寫」寫的規則對不到，
    # 完整單字寫法要另外接。
    (re.compile(r"\bfortigate[_\s-]*(\d+[a-z]?)\b", re.I), _fmt_fortigate),
    (re.compile(r"fortinet[_\s-]*fg(?:ate)?[_\s-]*(\d+[a-z]?)", re.I), _fmt_fortigate),
    (re.compile(r"\bfg[_\s-]*(\d+[a-z]?)\b", re.I), _fmt_fortigate),
    (re.compile(r"\bpalo\s*alto\b.*?\bpa-?(\d{4})\b", re.I), _fmt_paloalto),
    # 使用者 2026-08-13 實際發現：「DELL R750」「HPE DL360 Gen10」這種業界慣用簡寫
    # （不帶完整產品線名「PowerEdge」「ProLiant」）在真實庫存裡佔多數，原本只認完整
    # 產品線名的規則（見下面 _MODEL_RULES 的 poweredge/proliant）大部分情況根本比對
    # 不到，vendor 抓不到就一路落到「其他」分類，即使一看就知道是伺服器。
    (re.compile(r"\bdell\b[\s-]*(?:poweredge[\s-]*)?r[\s-]*(\d{3,4})\b", re.I), _fmt_dell_poweredge),
    (re.compile(r"\bhpe?\b.*?\b(dl|ml|bl)[\s-]*(\d{3})(?:\s*g(?:en)?[\s-]*(\d+))?\b", re.I), _fmt_hpe_proliant),
    # 使用者 2026-08-13 實際發現：3PAR 是 HPE 收購的儲存產品線，原始字串常常只寫
    # 「3PAR 8400 SSMC」不帶「HPE」字樣，vendor 抓不到就落到「其他」。型號數字（8400）
    # 是選填，抓不到也至少知道是 HPE 3PAR 系列。
    (re.compile(r"\b3par\b(?:\s*(\d{3,4}))?", re.I), _fmt_hpe_3par),
    # 使用者 2026-08-13 實際發現：EMC DS 系列 SAN Switch（如 DS6630B）常常只在資產用途
    # 欄留下型號碼，device_model 只填籠統的「EMC SAN Switch」。IBM 也有 DS 開頭的
    # 儲存陣列型號（如 DS3524），裸抓「DS####」會誤把 IBM 產品掛成 Dell EMC，所以
    # 要求「emc」跟「ds####」在同一段文字裡才觸發，只在明確提到 EMC 品牌時才生效。
    # 同時要求附近有「switch」字樣，才敢斷定是交換器而非其他 DS 系列儲存設備。
    # ⚠️ 型號碼後面用 (?![a-z0-9]) 不用 \b：實測發現「DS3524使用」這種數字後面
    # 直接接中文字（無空格分隔）時，Python re 把中文字也算進「單字字元」，
    # \b 在數字跟中文交界處判斷不到邊界，整條規則悄悄漏接——這條規則的中文
    # 資產用途欄位很容易踩到，改用「後面不是英數字」這種更寬鬆的邊界判斷。
    (re.compile(r"\bemc\b.{0,40}?\bds\s*-?(\d{3,4}[a-z]?)(?![a-z0-9]).{0,20}?\bswitch\b", re.I),
     _fmt_emc_ds_switch),
    # 使用者 2026-08-13 實際發現：「IBM San Switch」這種 device_model 本身沒代碼，
    # 型號碼（DS5300／FS5300，兩種代碼各自代表不同型號，各自形成獨立 canonical）
    # 藏在資產用途／資產名稱欄（例：「SAN Switch」+「切換器(FS5300)」，或
    # 「IBM Storage DS3524使用」）。實測發現資產用途/名稱欄常常沒寫「IBM」，
    # 只有「SAN」（來自資產名稱「SAN Switch」），所以錨點字用「ibm」或「san」
    # 任一都算——不強制要求「switch」英文字，原始資料常寫中文「切換器」。
    (re.compile(r"\b(?:ibm|san)\b.{0,40}?\b((?:ds|fs)\s*-?\d{3,4}[a-z]?)(?![a-z0-9])", re.I),
     _fmt_ibm_san_switch),
    (re.compile(r"\b((?:ds|fs)\s*-?\d{3,4}[a-z]?)(?![a-z0-9]).{0,40}?\b(?:ibm|san)\b", re.I),
     _fmt_ibm_san_switch),
    # 使用者 2026-08-13 實際發現：ODA（Oracle Database Appliance）原本規則收斂成籠統
    # 的「Oracle Database Appliance (ODA)」，把「X7-2 HA」這種具體世代/規格代號
    # 丟掉了——資產用途/資產名稱欄常寫得出來（例：「ODA X7-2 HA」），要留住。
    (re.compile(r"\b(?:oda|oracle\s*database\s*appliance)\b.{0,20}?\b(x\d+-\d+(?:\s*-?\s*(?:ha|s|m))?)\b", re.I),
     _fmt_oracle_oda),
    # 使用者 2026-08-13 實際發現：Exadata 原本規則直接收斂成籠統的「Oracle Exadata」，
    # 但 device_model 欄位本身常常就寫著具體世代/規格（見 _fmt_oracle_exadata 說明），
    # 不用猜，是規則抓漏，改成拿整段原字串當 canonical。
    (re.compile(r"\bExadata\b", re.I), _fmt_oracle_exadata),
]

# ---- 設備型號 → OS 反推規則（第二層救援）----
# OS 欄是純版本號（如 15.2(2)E7）認不出時，同一台的設備型號常透露廠商，據此反推 OS 類型。
# 例：OS=15.2(2)E7 + 設備型號=Cisco Catalyst → Cisco Network OS。
_MODEL_TO_OS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bcisco\b|\bcatalyst\b|\bnexus\b|\bws-c|\bisr\d|\bc9[2-5]\d\d", re.I), "Cisco Network OS"),
    (re.compile(r"\baruba\b|\bprocurve\b", re.I), "Aruba OS"),
    (re.compile(r"\bjuniper\b|\b(ex|mx|srx)\d{3,}", re.I), "Junos"),
    (re.compile(r"\bfortigate\b|\bfortinet\b", re.I), "FortiOS"),
    (re.compile(r"\baten\b", re.I), "ATEN"),
    (re.compile(r"\bbig-?ip\b|\bf5\b", re.I), "F5 BIG-IP"),
]

KIND_OS = "os"
KIND_MODEL = "device_model"

# EOS 頁分類（使用者 2026-08-12 要求）：伺服器/主機作業系統跟網路設備韌體要分開，
# 因為要找不同的人維護。用 normalize_os() 已經算出來的 product 字串分桶，
# 不用另外寫一套規則——product 本來就是 _OS_RULES／_MODEL_TO_OS 裡定義好的標準名。
_HOST_OS_PRODUCTS = {
    "Windows", "Windows Server",
    "Oracle Linux", "Red Hat CoreOS", "Rocky Linux", "AlmaLinux", "Ubuntu", "Debian",
    "Red Hat Enterprise Linux", "CentOS", "SUSE Linux Enterprise", "Fedora",
    "AIX", "Solaris", "HP-UX", "IBM i",
    "VMware ESXi",  # 虛擬化平台的 host，是主機不是設備韌體
}
_FIRMWARE_PRODUCTS = {
    "Cisco Network OS", "FortiOS", "F5 BIG-IP", "Junos", "Aruba OS",
    # 使用者 2026-08-13 實際發現：APIC/ISE 之前為了查得到各自 EOS 而拆成獨立 product
    # （見 _OS_RULES），但拆完忘記把新 product 名加進這張分類表，變成「查得到 EOS
    # 但分類掉到未分類」——同一批都是網路設備組維護的東西，補進來。ATEN（KVM-over-IP
    # 切換器）、Voice Gateway 也是網路設備組維護範圍，一併補。
    "Cisco APIC (ACI)", "Cisco ISE", "ATEN", "Voice Gateway",
    # 使用者 2026-08-13 實際發現：Avamar（Dell EMC 備份/去重設備）也是網路設備組維護。
    "Avamar",
}
# 使用者 2026-08-13 要求：MySQL 這類資料庫軟體常被登打人員誤填進 OS 欄，技術偵測
# 沒錯但維護權責是「軟體」組——這批產品名有明確、封閉的清單（跟 host_os/firmware
# 一樣是具體產品名），可以直接自動分類，不用每筆手動搬。
_SOFTWARE_PRODUCTS = {
    "MySQL", "MariaDB", "PostgreSQL", "MongoDB", "Microsoft SQL Server",
    "Oracle Database", "IBM Db2", "SAP HANA", "Redis",
    # 使用者 2026-08-13 要求：VMware 管理/監控平台，不是 ESXi 虛擬化本身，維護
    # 權責跟資料庫軟體一樣算「軟體」不是作業系統。
    "VMware vCenter Server", "VMware vRealize Operations", "VMware vRealize Network Insight",
}
# 使用者 2026-08-13 要求：像「網路設備」「儲存設備」「客製化系統」這種登打人員填的
# 泛用設備類型標籤（_OS_RULES 裡對應到的 product 本身就是這幾個字），根本不是產品
# 名稱，不管怎麼補都問不出更精確的東西——跟「未分類」（規則認不出來，也許還查得到）
# 性質不同，獨立分「資訊不足」桶，一樣可以直接自動分類。
_INSUFFICIENT_PRODUCTS = {"網路設備", "儲存設備", "客製化系統", "電力設備"}
# 使用者 2026-08-13 要求：iDRAC（跟實體主機綁死的管理韌體 BMC）、Unisphere Central
# （EMC 儲存陣列的管理軟體）這類東西，維護權責跟隨它管理的硬體本身，不是獨立的
# 作業系統——跟 os 側 host_os/firmware/software/insufficient/other 五桶不同層級，
# api.py 的 eos_summary() 直接把這批 product 併進硬體側對應分類，不經過 os_category()。
# key=product 名稱，value=該併入硬體側哪個 family。之後遇到同類案例（HPE iLO／
# Lenovo XCC／Cisco CIMC 這類 BMC，或其他儲存管理軟體）比照加入。
HW_ROUTED_PRODUCTS = {
    "Dell iDRAC (BMC)": "主機設備",
    "Unisphere Central": "儲存設備",
}


def os_category(product: str | None, canonical: str | None = None) -> str:
    """把 normalize_os() 回傳的 product 分成五桶：host_os／firmware／software／
    insufficient／other。

    other＝規則認不出來的裸版本號，或原始值本身太籠統，故意不硬塞進其他桶——
    這桶正是使用者最需要人工補資料（或改判定資訊不足）的清單。

    ⚠️ 走「別名字典」路徑解出來的（method="alias"，見 normalize_os()）沒有 product/vendor
    （字典只存 raw_value→canonical，沒有結構化拆解），所以人工在「未分類」補完對應後，
    如果只看 product 會永遠分類不到、卡在「未分類」出不去，等於白補——2026-08-12
    實測踩到。退而求其次比對 canonical 字串開頭是哪個已知 product（canonical 慣例是
    "{product} {version}"），只要人補的標準名稱有照這個慣例填，就分得到正確類別。
    """
    if product in _HOST_OS_PRODUCTS:
        return "host_os"
    if product in _FIRMWARE_PRODUCTS:
        return "firmware"
    if product in _SOFTWARE_PRODUCTS:
        return "software"
    if product in _INSUFFICIENT_PRODUCTS:
        return "insufficient"
    if canonical:
        # 長字串先比對，避免「Windows」比「Windows Server」先比中而分錯類
        for p in sorted(_HOST_OS_PRODUCTS, key=len, reverse=True):
            if canonical.startswith(p):
                return "host_os"
        for p in sorted(_FIRMWARE_PRODUCTS, key=len, reverse=True):
            if canonical.startswith(p):
                return "firmware"
        for p in sorted(_SOFTWARE_PRODUCTS, key=len, reverse=True):
            if canonical.startswith(p):
                return "software"
        for p in sorted(_INSUFFICIENT_PRODUCTS, key=len, reverse=True):
            if canonical.startswith(p):
                return "insufficient"
    return "other"


# 「作業系統」分頁二/三層分組（使用者 2026-08-12 要求）：AIX/Linux/Windows/VMware，
# Linux 底下再依發行版分。純顯示用途，不影響 os_category() 判的 host_os/firmware/other。
_LINUX_DISTROS = {
    "Red Hat Enterprise Linux": "RHEL", "CentOS": "CentOS", "Debian": "Debian",
    "Oracle Linux": "Oracle Linux", "Ubuntu": "Ubuntu", "Rocky Linux": "Rocky Linux",
    "AlmaLinux": "AlmaLinux", "SUSE Linux Enterprise": "SUSE", "Fedora": "Fedora",
    "Red Hat CoreOS": "CoreOS",
}


def os_family(product: str | None, canonical: str | None = None) -> tuple[str, str | None]:
    """host_os 桶裡的項目再分二/三層顯示用：回傳 (family, linux_distro)。
    只有 family=="Linux" 時 linux_distro 才有值，其餘 family 一律 None。

    ⚠️ 跟 os_category() 同一個坑：走別名字典解出來的沒有 product，只看 product
    會讓人工補完對應的項目永遠分類不到、卡在「其他」出不去（2026-08-13 實測踩到：
    使用者把「ApplianceOS:Rocky8」補成「Rocky Linux 8」，因為走 alias 沒有 product，
    被分到「其他」而不是「Linux」）。一樣補上比對 canonical 字串開頭的退路。
    """
    if product in _LINUX_DISTROS:
        return "Linux", _LINUX_DISTROS[product]
    if product in ("AIX", "Solaris", "HP-UX", "IBM i"):
        return "AIX/Unix", None
    if product in ("Windows", "Windows Server"):
        return "Windows", None
    if product == "VMware ESXi":
        return "VMware", None
    if canonical:
        for p in sorted(_LINUX_DISTROS, key=len, reverse=True):
            if canonical.startswith(p):
                return "Linux", _LINUX_DISTROS[p]
        for p in ("AIX", "Solaris", "HP-UX", "IBM i"):
            if canonical.startswith(p):
                return "AIX/Unix", None
        for p in ("Windows Server", "Windows"):
            if canonical.startswith(p):
                return "Windows", None
        if canonical.startswith("VMware ESXi"):
            return "VMware", None
    return "其他", None


def os_platform_bucket(product: str | None, canonical: str | None) -> str | None:
    """使用者 2026-08-13 實際發現：首頁「作業系統平台」統計（manage_state.platform_of()）
    是一套獨立的土砲關鍵字比對，跟這裡 normalize_os() 這套已經很成熟的判斷（能靠
    device_model 反推、能查別名字典、覆蓋率遠遠更高）完全沒有共用結果——normalize_os()
    早就認得出「Palo Alto PAN-OS 9.1.9」「Cisco Network OS」「Red Hat Enterprise
    Linux 8.10」，首頁卻還是把這些丟進「未知」，因為兩套規則各管各的。

    這支函式讓 manage_state 直接復用 os_family()／os_category()／HW_ROUTED_PRODUCTS
    這幾個已經測過、覆蓋率高很多的判斷，不用重新發明關鍵字清單。回傳值是首頁平台
    卡片的分類鍵，None 代表「這套規則也判斷不出來，回去用舊的關鍵字猜」。
    """
    if product in HW_ROUTED_PRODUCTS:
        return "管理韌體(BMC)" if HW_ROUTED_PRODUCTS[product] != "網路設備" else "網路設備"
    if product == "IBM i":
        return "IBM i"  # os_family() 把 IBM i 併進 AIX/Unix，首頁要獨立卡片才拆開判斷
    family, linux_distro = os_family(product, canonical)
    if family == "Linux":
        # 首頁舊制 Linux 系是拆成 RHEL/CentOS/Debian/Oracle Linux/Linux(其他) 五個
        # 頂層桶餵給前端「展開看發行版」功能（PLATFORM_GROUPS.Linux），os_family()
        # 的 linux_distro 分類更細（含 Rocky/Ubuntu/AlmaLinux/SUSE/Fedora/CoreOS），
        # 這裡對回舊制的五桶，其餘全部併進「Linux(其他)」，維持前端相容。
        return {"RHEL": "RHEL", "CentOS": "CentOS", "Debian": "Debian",
                "Oracle Linux": "Oracle Linux"}.get(linux_distro, "Linux(其他)")
    if family == "VMware":
        return "VMware ESXi"  # os_family() 叫「VMware」，首頁卡片鍵是「VMware ESXi」，對齊命名
    if family != "其他":
        return family  # AIX/Unix／Windows
    bucket = os_category(product, canonical)
    if bucket == "firmware":
        return "網路設備"
    if bucket == "software":
        return "軟體"
    return None  # host_os/insufficient/other 都判不出實際平台，回去用舊邏輯猜


# 「硬體型號」分頁二/三層分組（使用者 2026-08-13 要求）：網路設備／主機設備／儲存設備／
# 虛擬化／其他，網路設備底下再依廠牌分（跟作業系統分頁的 Linux 分發行版是同一套邏輯）。
# 純顯示用途，跟 EOS 查詢（lookup_hardware_eos）完全無關，不影響查得到查不到日期。
_HW_NETWORK_VENDORS = {"Cisco", "Juniper", "F5", "Fortinet", "Palo Alto Networks", "Aruba", "Trend Micro", "Forcepoint", "ATEN"}
_HW_SERVER_VENDORS = {"HPE", "Dell", "Lenovo", "Supermicro", "IBM", "Oracle"}
_HW_VM_PRODUCTS = {
    "VMware Virtual Machine", "KVM Virtual Machine", "VirtualBox VM", "Hyper-V Virtual Machine",
    "Virtual Machine (平台不明)",
}
# 使用者 2026-08-13 要求：這些籠統兜底 canonical 本身就承認「型號不夠具體」，
# normalize_model() 對到這幾個時，會多花一步看 hint 有沒有更具體的型號（見
# normalize_model() 內的說明），不會滿足於兜底結果就提早收工。
_GENERIC_MODEL_FALLBACKS = _HW_VM_PRODUCTS | {
    "Oracle Database Appliance (ODA)", "HPE 3PAR storage",
}
# 使用者 2026-08-13 實際發現：儲存設備的原始寫法不是每次都乖乖帶「storage」字樣
# （SAN Switch／NAS／VPLEX／3PAR／Synology 這些都是儲存領域的專有詞，行家一看就知道，
# 但字面上不含「storage」），單靠 canonical.lower() 找 "storage" 子字串會漏接。
_HW_STORAGE_KEYWORDS = re.compile(r"\bstorage\b|\bnas\b|\bsan\s*switch\b|\bvplex\b|\b3par\b|\bsynology\b", re.I)
# 使用者 2026-08-13 實際發現：庫存裡大量型號寫法（「Cisco C9500-48Y4C」「IBM AS400」
# 「Lenovo Server」……）沒有一條 _MODEL_RULES/_MODEL_CODE_RULES 認得出具體型號，
# vendor 抓不到就全部落到「其他」，即使原始字串已經明講廠牌。與其窮舉每一種型號碼
# （Cisco 系列型號多到列不完），退而求其次：normalize_model() 認不出具體型號時，
# 直接從原始字串裡找有沒有出現已知廠牌名，抓得到就夠 hardware_family() 分類用了
# （canonical 仍保留原字串讓人／AI 之後補具體型號，不是每一筆都硬湊一個假型號名）。
_HW_VENDOR_ALIASES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bcisco\b", re.I), "Cisco"),
    (re.compile(r"\bjuniper\b", re.I), "Juniper"),
    (re.compile(r"\bf5\b|\bbig-?ip\b", re.I), "F5"),
    (re.compile(r"\bfortinet\b|\bfortigate\b", re.I), "Fortinet"),
    (re.compile(r"\bpalo\s*alto\b", re.I), "Palo Alto Networks"),
    (re.compile(r"\baruba\b", re.I), "Aruba"),
    # 使用者 2026-08-13 實際發現：TippingPoint IPS 是 Trend Micro（趨勢科技）收購/代管
    # 的產品線，原始型號字串不會出現「Trend」字樣，要用產品名反推廠牌。
    (re.compile(r"\btippingpoint\b", re.I), "Trend Micro"),
    (re.compile(r"\bforcepoint\b", re.I), "Forcepoint"),
    (re.compile(r"\bhpe?\b", re.I), "HPE"),
    (re.compile(r"\bdell\b", re.I), "Dell"),
    (re.compile(r"\blenovo\b", re.I), "Lenovo"),
    (re.compile(r"\bsupermicro\b", re.I), "Supermicro"),
    (re.compile(r"\bibm\b", re.I), "IBM"),
    (re.compile(r"\boracle\b", re.I), "Oracle"),
    # 使用者 2026-08-13 實際發現：EMC（現屬 Dell）SAN Switch／Storage 這類舊命名常常
    # 只寫「EMC」不寫「Dell」，原本這張表沒收，掃 hint 也認不出廠牌。
    (re.compile(r"\bemc\b", re.I), "Dell EMC"),
]


def _infer_hw_vendor(canonical: str | None) -> str | None:
    if not canonical:
        return None
    for pattern, vendor in _HW_VENDOR_ALIASES:
        if pattern.search(canonical):
            return vendor
    return None


def hardware_family(vendor: str | None, canonical: str | None) -> tuple[str, str | None]:
    """硬體型號桶裡的項目分二/三層顯示用：回傳 (family, vendor_sub)。
    family=="網路設備" 或 "主機設備" 時 vendor_sub 才有值（使用者 2026-08-13 要求：
    「主機設備」底下也要能像「網路設備」一樣依廠牌篩選，不是只有網路設備才需要）。

    先判虛擬化／儲存（canonical 名稱本身帶「storage」字樣，見 _MODEL_RULES 的儲存規則），
    再判廠牌——順序有意義：Oracle 廠牌同時橫跨 VirtualBox（虛擬化）跟 Exadata/ODA
    （主機一體機），必須先用產品名擋掉虛擬化，才輪得到廠牌判斷落到主機設備。
    vendor 是 None（normalize_model() 沒認出具體型號）時，退而求其次直接從 canonical
    原始字串找廠牌關鍵字（見 _infer_hw_vendor）。
    """
    if not vendor:
        vendor = _infer_hw_vendor(canonical)
    if canonical in _HW_VM_PRODUCTS:
        return "虛擬化", None
    if canonical and _HW_STORAGE_KEYWORDS.search(canonical):
        return "儲存設備", None
    if vendor in _HW_NETWORK_VENDORS:
        return "網路設備", vendor
    if vendor in _HW_SERVER_VENDORS:
        return "主機設備", vendor
    return "其他", None


def _clean(raw: str) -> str:
    s = _CODENAME.sub(" ", str(raw))       # 去掉 (Blue Onyx)／(trixie)
    s = _NOISE.sub(" ", s)                 # 去掉 LTS／GNU/Linux 這類不影響版本的字
    return re.sub(r"\s+", " ", s).strip()


def _load_aliases(conn, kind: str) -> dict[str, str]:
    """讀人工別名字典。查不到表就當空的——正規化不該因為字典還沒建就整個壞掉。"""
    try:
        return {
            str(r["raw_value"]).strip().lower(): r["canonical"]
            for r in conn.execute(
                "SELECT raw_value, canonical FROM normalize_alias WHERE kind = ?", (kind,)
            )
        }
    except Exception:  # noqa: BLE001 - 表不存在等情況
        return {}


def _load_canonical_overrides(conn, kind: str) -> dict[str, str]:
    """使用者 2026-08-13 要求：不管系統目前顯示的名稱是規則確認還是靠 hint 猜的，
    都要能直接改成正確名稱，改完永遠照這個為準——跟 _load_aliases() 不一樣，
    那個是「原始字串 → 標準名」，只對還沒被規則收斂掉的原始值有用；這裡是
    「系統目前算出來的 canonical → 使用者確認的正確 canonical」，對任何已經
    顯示出來的名稱都能用。查不到表就當空的，理由同 _load_aliases()。
    """
    if conn is None:
        return {}
    try:
        return {
            r["old_canonical"]: r["new_canonical"]
            for r in conn.execute(
                "SELECT old_canonical, new_canonical FROM normalize_canonical_override WHERE kind = ?",
                (kind,),
            )
        }
    except Exception:  # noqa: BLE001 - 表不存在等情況
        return {}


def normalize_os(raw, conn=None, device_model=None) -> dict:
    """把 OS 字串收斂成標準名。回 {raw, canonical, vendor, product, version, matched, method}。

    matched=False 代表「認不出來」——canonical 會原樣回傳 raw，**不亂猜**。
    OS 字串本身認不出時，若給了同一台的 device_model，用設備型號的廠商反推（第二層）。

    最後一步固定套用 normalize_canonical_override（見 normalize_model() 同名段落
    的說明）：不管上面走哪條路徑算出來的 canonical，使用者都能直接改名、改完
    永遠照使用者的為準（method 標成 "user_override"，product/version 不動，
    只是名稱顯示被覆寫）。
    """
    if raw is None or str(raw).strip() == "":
        return {"raw": raw, "canonical": None, "vendor": None, "product": None,
                "version": None, "matched": False, "method": "empty"}

    text = str(raw).strip()
    result = None

    # 別名字典優先：人工對應過的一定照人的意思
    if conn is not None:
        alias = _load_aliases(conn, KIND_OS).get(text.lower())
        if alias:
            result = {"raw": raw, "canonical": alias, "vendor": None, "product": None,
                      "version": None, "matched": True, "method": "alias"}

    if result is None:
        # 規則比對用**原字串**（產品名可能含被視為雜訊的字，如 Rocky Linux 的 linux）；
        # 版本抽取才用清理過的（去掉代號，否則會從 (trixie) 之類的括號內容誤抓數字）。
        cleaned = _clean(text)
        for pattern, vendor, product in _OS_RULES:
            if pattern.search(text):
                m = _VERSION.search(cleaned)
                version = m.group(1) if m else None
                # IBM i 的版號是 VxRy（V7R3 ＝ 7.3），泛用的 _VERSION 抓不對：
                # 它是 `(\d+(?:\.\d+)*)\b`，而 `V7R3` 的 7 後面接著 R（也是單字字元），
                # `\b` 因此不成立，正則跳過主版本、抓到結尾的 3 ——「V7R3」被讀成
                # 「IBM i 3」。2026-08-21 使用者做月報時發現：42 台 AS/400 全部版號錯，
                # 連帶在 EOS 對照表查無此物（「IBM i 3」不存在），50 台全被標成「需確認」。
                vr = _IBM_I_VR.match(text.strip()) if product == "IBM i" else None
                if vr:
                    version = f"{int(vr.group(1))}.{int(vr.group(2))}"
                canonical = f"{product} {version}".strip() if version else product
                result = {"raw": raw, "canonical": canonical, "vendor": vendor,
                          "product": product, "version": version,
                          "matched": True, "method": "rule"}
                break

    # 第二層：OS 字串認不出（多半是純版本號），但同一台的設備型號透露廠商。
    # 例：OS=15.2(2)E7 + 設備型號=Cisco Catalyst → Cisco Network OS。原 OS 值當版本留著。
    if result is None and device_model:
        for pattern, canonical in _MODEL_TO_OS:
            if pattern.search(str(device_model)):
                result = {"raw": raw, "canonical": canonical, "vendor": None,
                          "product": canonical, "version": text,
                          "matched": True, "method": "model-inferred"}
                break

    if result is None:
        # 認不出來：原值原樣留著，標成未對應讓它出現在待處理清單
        result = {"raw": raw, "canonical": text, "vendor": None, "product": None,
                  "version": None, "matched": False, "method": "unmatched"}

    if conn is not None:
        overrides = _load_canonical_overrides(conn, KIND_OS)
        if result["canonical"] in overrides:
            result = {**result, "canonical": overrides[result["canonical"]], "method": "user_override"}

    return result


def suggest_os_canonical(hint: str | None) -> str | None:
    """使用者 2026-08-13 要求：「未分類」清單裡，raw OS 值本身認不出來時，
    資產用途欄常常藏著看得懂的產品名（例：某些 VM 的資產用途直接寫軟體名稱）。
    這裡只負責「猜」，回傳的建議**不會自動套用**——刻意跟 normalize_os() 的正式
    判斷路徑分開，只在畫面上顯示成「建議」，要人工按「採用」才會真的寫進別名
    字典，避免猜錯的東西悄悄變成既定事實（呼應本檔案最上面「查不到的不亂猜」
    的原則——這裡不是例外，是把「猜」這件事往後挪一步，讓人來按確認鍵）。

    ⚠️ 呼叫端只能傳「資產用途」，不可以連「資產名稱」一起塞進來——實測發現資產
    名稱欄常常本身就填著跟 os 欄一樣的泛用類型標籤（「儲存設備」），會讓建議
    繞一圈猜回同一個沒有資訊量的字，甚至把型號數字誤接在後面變成語意不通的
    「儲存設備 3524」。

    兩層防呆：
    1. 命中 _INSUFFICIENT_PRODUCTS 這批泛用標籤（儲存設備／網路設備／客製化系統／
       電力設備）不算建議——建議了等於沒建議，是同一個沒資訊量的字繞回來。
    2. 只憑「品牌單字」命中、沒有版本號也沒有其他更具體線索的（目前只有 Cisco
       的裸 \\bcisco\\b 這條後備規則屬於此類——實測發現「Cisco Prime」這種管理
       軟體授權會被誤建議成「Cisco Network OS」），信心太弱不採用。
    """
    if not hint:
        return None
    # 使用者 2026-08-13 實際發現：「MongoDB_POC」這種底線接續命名，regex 的 \b
    # （單字邊界）在「b」跟「_」之間判斷不到邊界——底線在正規表達式裡算「單字
    # 字元」，\bdb\b 因此比對不到「db_POC」，整條規則悄悄漏接。資產用途欄底線
    # 命名很常見，先把底線換成空白再比對，不影響版本抽取（仍用原字串清理）。
    text = str(hint).strip().replace("_", " ")
    if not text:
        return None
    cleaned = _clean(text)
    for pattern, vendor, product in _OS_RULES:
        if product in _INSUFFICIENT_PRODUCTS:
            continue
        if pattern.search(text):
            m = _VERSION.search(cleaned)
            version = m.group(1) if m else None
            if product == "Cisco Network OS" and not version \
                    and not re.search(r"\bnx-?os\b|\bios-?xe\b|\bcatalyst\b|\bnexus\b", text, re.I):
                continue
            return f"{product} {version}".strip() if version else product
    return None


def _match_model_rules(text: str) -> dict | None:
    # 使用者 2026-08-13 實際發現：「Dell R330 Server  KVM」這種字串裡「KVM」單字
    # 只是隨附的實體 console 埠標記，不是虛擬機——_MODEL_CODE_RULES 的動態型號規則
    # （Dell R系列／HPE ProLiant／Fortinet…）比對到具體型號時明顯比 _MODEL_RULES
    # 裡認單字「kvm/virtual machine」這種籠統虛擬化規則更精準，優先順序要贏過它。
    #
    # 使用者 2026-08-13 另外實際發現：「CXS_DS5300」這種底線接續命名，regex 的 \b
    # （單字邊界）在底線兩側判斷不到邊界（底線在正規表達式裡算「單字字元」），
    # \bds\b 因此比對不到「_DS5300」，整條規則悄悄漏接。資產用途欄底線命名常見，
    # 先把底線換成空白再比對——跟 suggest_os_canonical() 同一個修法。
    text = text.replace("_", " ")
    for pattern, formatter in _MODEL_CODE_RULES:
        m = pattern.search(text)
        if m:
            vendor, canonical = formatter(m)
            return {"canonical": canonical, "vendor": vendor}
    for pattern, vendor, model in _MODEL_RULES:
        if pattern.search(text):
            return {"canonical": model, "vendor": vendor}
    return None


def normalize_model(raw, conn=None, hint=None) -> dict:
    """機型正規化。同一台 VMware 虛擬機在不同來源叫 VMware VM／VMware Virtual Platform，
    不收斂就會被統計成兩種機型。

    device_model 欄位常常只填籠統類別（如「EMC Storage」），真正的具體型號（XT480）
    記在別的欄位（資產名稱：「敦南 VM Storage EMC XT480」）——使用者 2026-08-13 實際
    發現這個情況。device_model 本身認不出來時，若給了 hint（通常是同一台的
    asset_name／資產用途），用同一套規則試著從 hint 裡撈出具體型號，救回來的
    method 標成 "hint" 以便日後追查是靠哪個欄位判斷的。

    最後一步固定套用 normalize_canonical_override：不管上面走哪條路徑算出來的
    canonical，使用者 2026-08-13 要求都能直接改名、改完永遠照使用者的為準
    （method 標成 "user_override"）。
    """
    if raw is None or str(raw).strip() == "":
        return {"raw": raw, "canonical": None, "vendor": None,
                "matched": False, "method": "empty"}

    text = str(raw).strip()
    result = None

    if conn is not None:
        alias = _load_aliases(conn, KIND_MODEL).get(text.lower())
        if alias:
            result = {"raw": raw, "canonical": alias, "vendor": None,
                      "matched": True, "method": "alias"}

    if result is None:
        hit = _match_model_rules(text)
        # 使用者 2026-08-13 實際發現：device_model 只填籠統標記（「(VM)」「ODA」不帶
        # 世代碼、「3PAR」不帶型號數字）時，即使對到規則，也不代表真的沒有更具體的
        # 型號資訊——資產用途欄常常寫著實際的完整型號（例：3PAR 8400 SSMC、
        # ODA X7-2 HA）。這幾個籠統兜底規則本來就是承認「不夠具體」的最後手段，
        # 遇到 hint 有更具體的匹配時要優先採用 hint，而不是滿足於兜底結果。
        if hit and hit["canonical"] in _GENERIC_MODEL_FALLBACKS and hint:
            hint_hit = _match_model_rules(str(hint))
            if hint_hit:
                result = {"raw": raw, "canonical": hint_hit["canonical"], "vendor": hint_hit["vendor"],
                          "matched": True, "method": "hint"}

        if result is None and hit:
            result = {"raw": raw, "canonical": hit["canonical"], "vendor": hit["vendor"],
                      "matched": True, "method": "rule"}

        if result is None and hint:
            hint_hit = _match_model_rules(str(hint))
            if hint_hit:
                result = {"raw": raw, "canonical": hint_hit["canonical"], "vendor": hint_hit["vendor"],
                          "matched": True, "method": "hint"}
            else:
                # 使用者 2026-08-13 要求「舉一反三」：不要每次遇到新型號寫法（3PAR 只是
                # 舉例）都手動加一條專屬正則。device_model／hint 都沒比對到具體型號
                # 規則時，退而求其次用 hardware_family() 那套鬆散廠牌關鍵字表
                # （Cisco/Dell/HPE/IBM/Oracle…）反過來掃 hint，抓得到廠牌至少比完全
                # 沒廠牌好——canonical 保留原始 device_model 讓人／AI 之後補具體型號。
                hint_vendor = _infer_hw_vendor(str(hint))
                if hint_vendor:
                    result = {"raw": raw, "canonical": text, "vendor": hint_vendor,
                              "matched": False, "method": "hint-vendor"}

        if result is None:
            result = {"raw": raw, "canonical": text, "vendor": None,
                      "matched": False, "method": "unmatched"}

    if conn is not None:
        overrides = _load_canonical_overrides(conn, KIND_MODEL)
        if result["canonical"] in overrides:
            result = {**result, "canonical": overrides[result["canonical"]], "method": "user_override"}

    return result


def pending_values(conn) -> dict:
    """列出「規則和字典都認不出來」的原值＝待人工對應清單。

    刻意用即時查詢而不是另建一張 pending 表：pending 的定義就是
    「現在還認不出來的值」，另存一份就會有不同步的問題（字典補了、pending 沒清）。
    """
    out = {KIND_OS: [], KIND_MODEL: []}
    # OS：連同 device_model 一起判（第二層設備型號反推救得回的就不算 pending）。
    seen: dict[str, int] = {}
    for r in conn.execute(
        "SELECT os AS v, device_model AS m, COUNT(*) AS n FROM hardware "
        "WHERE os IS NOT NULL AND os != '' GROUP BY os, device_model"
    ):
        if not normalize_os(r["v"], conn, r["m"])["matched"]:
            seen[r["v"]] = seen.get(r["v"], 0) + r["n"]
    out[KIND_OS] = [{"raw_value": k, "count": v} for k, v in
                    sorted(seen.items(), key=lambda x: -x[1])]
    # 機型：照舊，不需交叉。
    seen = {}
    for r in conn.execute(
        "SELECT device_model AS v, COUNT(*) AS n FROM hardware "
        "WHERE device_model IS NOT NULL AND device_model != '' GROUP BY device_model"
    ):
        if not normalize_model(r["v"], conn)["matched"]:
            seen[r["v"]] = r["n"]
    out[KIND_MODEL] = [{"raw_value": k, "count": v} for k, v in
                       sorted(seen.items(), key=lambda x: -x[1])]
    return out


# ---- 診斷外掛：這個功能出問題時，我需要看到的東西 ----
try:
    import diagnostics

    @diagnostics.register("normalize")
    def _diag(conn) -> dict:
        """給判斷過程，不給原始資料：每個原值走了哪條路、命中什麼、結果是什麼。"""
        trace = []
        for kind, column, fn in ((KIND_OS, "os", normalize_os),
                                 (KIND_MODEL, "device_model", normalize_model)):
            for r in conn.execute(
                f"SELECT {column} AS v, COUNT(*) AS n FROM hardware "
                f"WHERE {column} IS NOT NULL AND {column} != '' GROUP BY {column}"
            ):
                res = fn(r["v"], conn)
                trace.append({"kind": kind, "raw": r["v"], "count": r["n"],
                              "canonical": res["canonical"], "method": res["method"],
                              "matched": res["matched"]})
        aliases = [dict(r) for r in conn.execute(
            "SELECT kind, raw_value, canonical FROM normalize_alias")]
        return {
            "trace": trace,
            "alias_count": len(aliases),
            "aliases": aliases,
            "pending": pending_values(conn),
        }
except ImportError:  # 診斷模組不在時不影響正規化本身
    pass
