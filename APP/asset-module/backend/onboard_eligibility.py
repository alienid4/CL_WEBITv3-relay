"""這台機器「可不可以被納管」——以資產庫登記的作業系統為準的白名單。

## 為什麼要有這一支（2026-09-08 使用者拍板）

批次納管原本只看「有沒有開 22」。問題是**一大堆不是伺服器的東西也開 22**：
交換器、儲存設備、F5、FortiGate、KVM 切換器、還有伺服器的管理卡（iDRAC）。
它們都吃 SSH、banner 也常自報 Linux/Unix，`useradd` 下去有的還會成功。

查 221 實際資料（4784 筆）之後才知道規模——`os` 欄不是伺服器作業系統的有 1051 筆：

    128 網路設備   83 儲存設備   52 Idrac(BMC)   30 APIC   cisco/juniper/HP 交換器
    21 V7R3 / 18 V7R5(IBM i)   6 BIG-IP   6 fortigate   8 ATEN(KVM)   Avamar

**為什麼是白名單不是黑名單**：上面那些值我一個也猜不到，黑名單漏掉一個廠牌
就是在網通設備上建帳號。白名單的失敗方向是「不認得就跳過」，那是對的方向。

## CoreOS／RHCOS 要單獨排除

221 上有約 167 台 CoreOS／RHCOS，那是 **OpenShift 節點**。它們**確實是 Linux、
SSH 進得去、useradd 也會成功**，所以任何「像 Linux 就納管」的判法都會掃到它們。
但 CoreOS 是不可變作業系統，本機帳號會在節點重佈時消失（收集之後會無聲失效），
而且那批機器歸 OpenShift 管。使用者 2026-09-08 原話：「CoreOS／RHCOS不能動他們」。

所以順序很重要：**先比對不可變清單，再比對 Linux 清單**。

## 這裡只回答「可不可以」，不回答「要不要」

環境別（正式／備援不碰）是另一層判斷，留在 batch_onboard_service。
一個函式只回答一件事，兩件混在一起以後沒人敢改。
"""
from __future__ import annotations

import re

#: 不可變作業系統：是 Linux，但**不可以**在上面建本機帳號（見模組說明）。
#: 一定要排在 LINUX_OK 前面比對——"Container Linux" 也會命中 LINUX_OK。
IMMUTABLE_OS = re.compile(
    r"coreos|rhcos|flatcar|container\s*linux|talos|bottlerocket", re.I)

#: 可以納管的伺服器 Linux。白名單：**不在這裡面的一律不跑**。
#: 結尾那個泛用的 `linux` 是給「Linux」「SUSE Linux Enterprise」這類寫法的，
#: 放最後，而且前面已經先擋掉不可變 OS 與設備。
LINUX_OK = re.compile(
    r"red\s*hat|rhel|centos|rocky|almalinux|oracle\s*linux|"
    r"debian|ubuntu|suse|sles|linux", re.I)

WINDOWS_OS = re.compile(r"windows", re.I)

#: 儲存設備：從 appliance 裡獨立出來（2026-10-13 使用者：「是 storage
#: 你怎麼抓不出來，可把 storage 獨立分類出來」）。原本網通／儲存／管理卡全擠在
#: appliance 一類，看不出哪些是儲存——221 實際資料裡儲存設備就有 83 筆。
#:
#: 規則用 `system_stats.STORAGE_RE`——**全站只有一條**。分佈統計的 Storage 分群、
#: pipeline 的 Storage/SAN 類別讀的是同一條；在這裡另寫一套會變成同一件事兩個
#: 答案，改了一邊另一邊靜靜地不一致。
#:
#: 比對順序必須在 WINDOWS_OS 之後：STORAGE_RE 含裸的 `storage`，順序寫錯會把
#: 「Windows Storage Server」這台 Windows 伺服器吃掉，漏掉一台該納管的機器。
def _storage_re():
    import system_stats
    return system_stats.STORAGE_RE

AIX_OS = re.compile(r"\baix\b", re.I)
ESXI_OS = re.compile(r"esxi|vmware", re.I)

#: 空值的各種寫法。人工填表填出來的，不是程式產的，所以要寬一點。
_BLANK = {"", "-", "--", "n/a", "na", "n.a.", "無", "不明", "未知"}


def classify_os(os_text: str | None) -> tuple[str, str]:
    """回 (類別, 給人看的理由)。

    類別：
      linux      —— 可以走這支批次納管
      immutable  —— 是 Linux 但不可以動（OpenShift 節點）
      windows    —— 走 WinRM，不是這支
      aix        —— 走 ksh 腳本，不是這支
      esxi       —— 虛擬化平台本身，不納管
      appliance  —— 認不出來的：網通／儲存／管理卡等設備
      unknown    —— 資產庫沒填作業系統，無從判斷
    """
    raw = (os_text or "").strip()
    if raw.lower() in _BLANK:
        return "unknown", "資產庫沒填作業系統——判不出這是伺服器還是設備，不自動納管"
    if IMMUTABLE_OS.search(raw):
        return "immutable", (
            f"{raw}——OpenShift 節點（不可變作業系統）。"
            "本機帳號會在節點重佈時消失，且該批機器歸 OpenShift 管，不納管")
    if ESXI_OS.search(raw):
        return "esxi", f"{raw}——虛擬化平台本身，不建本機帳號"
    if WINDOWS_OS.search(raw):
        return "windows", f"{raw}——要走 WinRM 流程，這支不做"
    if AIX_OS.search(raw):
        return "aix", f"{raw}——AIX 要走 ksh 腳本，批次這支只做 Linux"
    if _storage_re().search(raw):
        return "storage", f"{raw}——儲存設備，不建本機帳號"
    if LINUX_OK.search(raw):
        return "linux", raw
    return "appliance", (
        f"{raw}——不在可納管的作業系統清單裡（網通／儲存／管理卡之類的設備）。"
        "認不出來就不碰，不是漏掉")


def is_onboardable(os_text: str | None) -> bool:
    """只有明確認得出是伺服器 Linux 才回 True。**認不出來一律 False。**"""
    return classify_os(os_text)[0] == "linux"


# ===== 收集分派用：這台「不可以納管」嗎？（2026-09-14）=====
# 使用者：「很明顯看得出是 VMware，可能是 ESXi……雖然它有 SSH，但應該要有一個標記，
# 這樣我就會不去納管它」「我的定義雖然是 Linux，但這種設備我不能納管」。
#
# 收集分派（/adopt 的收集結果表）原本只看 22 有沒有開，開了就去試連、就列進「要憑證」。
# 批次納管那支已經照作業系統白名單擋了，但分派表沒有，畫面上看不出哪台是 ESXi。
#
# 證據兩種，任一成立就不納管，理由寫清楚是哪一種：
#   1. 資產庫登記的作業系統（classify_os）是 ESXi／OpenShift 節點／設備
#   2. vCenter（RVTools）匯入把這台列為 ESXi 主機——作業系統欄沒填或填錯也抓得到

import json as _json
import re as _re

#: 不納管的類別 → 給人看的標籤。**公開名稱**：收集分派要用它把不納管的那幾台
#: 拆成「儲存幾台、ESXi 幾台」（2026-10-13 使用者：統計方塊是合計的，看不出其中
#: 幾台是儲存設備）。呼叫端一律用這個名字，不要去存取底線開頭的私有名。
#:
#: ⚠️ 這張表的鍵**就是** `classify_os` 會回的類別，而 `not_onboardable()` 靠
#: `kind in NOT_ONBOARDABLE_LABEL` 決定要不要擋——所以不要把 classify_os
#: 不會回的值塞進來（banner 自報的那一類見下面的 KIND_BANNER）。
NOT_ONBOARDABLE_LABEL = {
    "esxi": "VMware ESXi",
    "immutable": "OpenShift 節點",
    "storage": "儲存設備",
    "appliance": "設備（不在可納管作業系統清單）",
}

#: SSH banner 自報身分而擋下來的那一類（見本檔最後的 `product_from_banner`）。
#: 它不是 `classify_os` 的結論，所以**刻意不放進 NOT_ONBOARDABLE_LABEL**。
#:
#: 為什麼要給它一個類別：分派結果的「不納管」方塊要拆成各類別的數字，
#: 如果 banner 擋下來的那幾台沒有類別，拆出來的小計就加不回方塊上的總數——
#: 使用者看到「不納管 11」底下只列到 8，只能自己猜剩下 3 台去哪了。
KIND_BANNER = "banner"
_KIND_BANNER_LABEL = "SSH banner 自報的設備"

#: 網路設備：從 appliance 裡再拆出來（2026-10-13 使用者：/adopt 的「SSH 收集」頁簽
#: 混著 Aruba 等網路設備，要獨立一個頁簽看得出幾台）。
#:
#: ⚠️ **不另寫規則**：「這是不是網路設備」全站只有一份——`manage_state._PLATFORM_RULES`
#: 的「網路設備」那一條（首頁平台統計、pipeline 的 OS 類型漏斗都讀它，使用者 2026-08-13
#: 逐條補過 aruba／fortinet／f5／big-ip…）。這裡只是呼叫 `manage_state.platform_of`。
#:
#: 為什麼不改 `classify_os` 讓它直接回 network：classify_os 的 appliance 有很多讀者
#: （pipeline 的 OS 漏斗、批次納管、manage_state），它們各自再細分；在這裡多一個類別
#: 會讓那些讀者突然收到認不得的值。所以只在「不納管的類別」這一層細分——
#: **擋不擋的結論不變**（appliance 本來就擋），變的只是擋下來之後歸哪一類。
#:
#: 也**不**從資產名稱／用途（自由文字）推網路設備：「網管主機」「網路監控」那種
#: 用途寫在一般 Linux 伺服器上很常見，拿來擋會把該納管的伺服器擋掉。
KIND_NETWORK = "network"
_KIND_NETWORK_LABEL = "網路設備"


def is_network_device(text) -> bool:
    """這段文字（作業系統欄／banner 自報的產品名）是不是網路設備。規則見 KIND_NETWORK。"""
    if not text:
        return False
    import manage_state
    return manage_state.platform_of(str(text)) == "網路設備"


def kind_from_banner(product) -> str:
    """SSH banner 自報的產品名 → 不納管的類別。

    banner 認得出來的東西多半有明確歸屬（NetApp 是儲存、Cisco 是網路設備、ESXi 是 ESXi），
    一律丟進「banner」會讓 /adopt 的儲存／網路／ESXi 頁簽漏算這幾台。
    儲存與網路都用全站唯一那條規則判；ESXi 只認產品名寫明 ESXi 的——
    `VMware Avi Load Balancer` 也帶 VMware 字樣，但它是負載平衡器，不是虛擬化平台。
    認不出歸屬的（iDRAC、Avi、FortiGate…）維持 banner。
    """
    p = str(product or "")
    if not p:
        return KIND_BANNER
    import system_stats
    if system_stats.STORAGE_RE.search(p):
        return "storage"
    if _re.search(r"\besxi\b", p, _re.I):
        return "esxi"
    if is_network_device(p):
        return KIND_NETWORK
    return KIND_BANNER


def kind_label(kind) -> str:
    """不納管類別 → 給人看的標籤。

    認不出來的類別**回原字串**，不回空字串也不丟例外：這是顯示用的，
    吞掉未知類別只會讓畫面少一格數字、對不上總數，比印出生字串更難查。
    """
    if kind == KIND_BANNER:
        return _KIND_BANNER_LABEL
    if kind == KIND_NETWORK:
        return _KIND_NETWORK_LABEL
    return NOT_ONBOARDABLE_LABEL.get(kind) or str(kind or "")


#: 自由文字（資產名稱／用途）只採信這幾種**正面認出來**的結論。
#: 不含 appliance——那一類代表「認不出來」，不是證據。
_FROM_FREE_TEXT = ("storage", "esxi", "immutable")

_IPV4 = _re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def _name_keys(name) -> set[str]:
    """主機名比對鍵：原字串大寫＋去網域的短名（IP 不去網域，不然 10.x 會變成 10）。"""
    s = str(name or "").strip().upper()
    if not s:
        return set()
    if _IPV4.match(s):
        return {s}
    # [B-03] 以前「見點就砍」（s.split(".")[0]）：堆疊成員「…-254.254-1／-2」會被砍成同一個短名。
    # 改用 system_stats.strip_domain——只有真的網域才去，跟全站「同一台」的判斷同一條。
    import system_stats
    return {s, system_stats.strip_domain(s)}


def esxi_host_names(conn) -> set[str]:
    """vCenter（RVTools）匯入裡被列為 ESXi 主機的名字／IP（正規化後）。"""
    out: set[str] = set()
    try:
        rows = conn.execute(
            "SELECT payload FROM source_record WHERE source = 'vcenter'").fetchall()
    except Exception:  # noqa: BLE001 - 舊 DB 沒有這張表就當沒有證據
        return out
    for r in rows:
        try:
            d = _json.loads(r[0] or "{}")
        except (ValueError, TypeError):
            continue
        if isinstance(d, dict) and d.get("esxi_host"):
            out |= _name_keys(d["esxi_host"])
    return out


def not_onboardable(os_text, hostname=None, ip=None,
                    esxi_names=frozenset(), extra_text=None) -> tuple[str, str, str] | None:
    """回 (類別, 標籤, 依據)；可以試著納管就回 None。

    `extra_text` = 資產名稱＋資產用途（2026-10-13）。
    使用者指著一台 EMC UnityXT480 NAS 問「是 storage 你怎麼抓不出來」——
    它的作業系統欄是 `N/A`，classify_os 只讀 os 欄，所以回 unknown；
    但「EMC UnityXT480 NAS (iSCSI target)」就寫在資產用途裡，證據一直都在，
    只是判定的人沒去看（normalize_model 早就靠這兩欄猜出 "…storage"）。

    ⚠️ 只有 os 欄判不出來（unknown）時才看這兩欄，而且**只採信會擋下來的結論**：
    資產用途寫著 Linux 不能拿來當「可以納管」的憑據——那兩欄是人填的自由文字，
    拿它開門風險不對稱。失敗方向維持「漏標」。
    """
    kind, why = classify_os(os_text)
    if kind == "appliance" and is_network_device(os_text):
        # 擋的結論跟 appliance 一樣，只是歸到網路設備這一類（見 KIND_NETWORK）
        return KIND_NETWORK, _KIND_NETWORK_LABEL, f"依據：資產登記的作業系統「{why}」"
    if kind in NOT_ONBOARDABLE_LABEL:
        return kind, NOT_ONBOARDABLE_LABEL[kind], f"依據：資產登記的作業系統「{why}」"
    if kind == "unknown" and extra_text:
        k2, why2 = classify_os(extra_text)
        # 只收**正面認出來**的類別。appliance 的語意是「不在白名單、認不出來」，
        # 套在自由文字上會把「測試機」這種什麼都沒講的用途也判成設備——那是
        # 把「不知道」當成「知道它不是伺服器」，方向相反。
        if k2 in _FROM_FREE_TEXT:
            return k2, NOT_ONBOARDABLE_LABEL[k2], (
                f"依據：作業系統欄沒填，但資產名稱／用途寫著「{why2}」")
    if esxi_names and ((_name_keys(hostname) | _name_keys(ip)) & set(esxi_names)):
        return "esxi", NOT_ONBOARDABLE_LABEL["esxi"], (
            "依據：vCenter（RVTools）匯入把它列為 ESXi 主機——虛擬化平台本身，不建本機帳號")
    return None


# ===== 同一筆資產，一個入口（2026-10-16）=====
#
# 為什麼要有這支：`not_onboardable()` 的證據是**呼叫端自己湊**的，於是三個呼叫點
# 湊出三組不同的證據——
#     collect_dispatch（/adopt）：os + hostname + ip + esxi_names + extra_text
#     api._onboard_block（資產查詢／資產詳細）：os + hostname + ip        ← 少兩項
#     pipeline._onboard_block_for（漏斗）    ：os + hostname + ip        ← 少兩項
# 同一台 EMC UnityXT480（os 欄 `N/A`、用途寫著 NAS）在 /adopt 判成「儲存設備」，
# 在資產查詢卻判成可納管、還掛著「一鍵納管」按鈕。使用者為了分類不一致抱怨兩次。
#
# 根因不是哪一處寫錯，是「湊證據」這件事本來就不該由呼叫端各做一遍。
# 所以把「蒐集證據 ＋ 呼叫判定」收成這一支，呼叫端只要交出資產列就好。
#
# ⚠️ 參數照抄到第四處也只是把洞複製第四次——要加證據就加在這裡，三頁一起變。


def _field(asset, key):
    """從資產列取一欄。dict 與 sqlite3.Row 都吃；沒撈那一欄就當沒填（回 None）。

    為什麼要容忍欄位不存在：三個呼叫點的 SELECT 不一樣（資產查詢是 `SELECT *`，
    漏斗只撈十幾欄）。少一欄就丟 KeyError 的話，這支共用函式會變成「只有某些
    呼叫點敢用」——那就又退回各寫一份的老路。
    """
    if asset is None:
        return None
    try:
        return asset[key]
    except (KeyError, IndexError, TypeError):
        return None


def asset_free_text(asset) -> str | None:
    """資產名稱＋資產用途併成一段自由文字；兩欄都空回 None。

    `not_onboardable(extra_text=...)` 要的就是這個。為什麼是這兩欄：
    作業系統欄填 `N/A` 的設備，身分常常只寫在這兩欄裡（EMC UnityXT480 NAS）。
    """
    parts = [str(_field(asset, k)).strip() for k in ("asset_name", "asset_purpose")
             if _field(asset, k) not in (None, "")]
    return " ".join(p for p in parts if p) or None


def judge_for_asset(conn, asset, ip=None, esxi_names=None):
    """同一筆資產的「能不能納管」判定——**三個呼叫點唯一的入口**。

    回傳跟 `not_onboardable()` 一樣：(類別, 標籤, 依據) 或 None。

    參數：
      conn        資料庫連線；只拿來查 vCenter 的 ESXi 名單。給 None 代表沒得查。
      asset       資產列（dict 或 sqlite3.Row），要有 os／hostname／asset_name／
                  asset_purpose；少撈了哪一欄就當那一欄沒填，不會炸。
      ip          指定要判的 IP；不給就用資產列上的 ip。
      esxi_names  已經查好的 ESXi 名單。**在迴圈裡逐列判時一定要先查好傳進來**——
                  `esxi_host_names()` 每次都全表掃 source_record 再逐列解 JSON，
                  放進迴圈等於 N 次全表掃。
    """
    if esxi_names is None:
        esxi_names = esxi_host_names(conn) if conn is not None else frozenset()
    return not_onboardable(
        _field(asset, "os"), _field(asset, "hostname"),
        ip if ip is not None else _field(asset, "ip"),
        esxi_names, extra_text=asset_free_text(asset))


def block_for_asset(conn, asset, ip=None, esxi_names=None) -> dict | None:
    """`judge_for_asset` 的畫面版：回 {kind,label,reason}，可以納管就回 None。

    api.py 與 pipeline.py 都要把結果塞進 JSON 給前端，轉換寫在這裡，不各寫一遍。
    """
    blk = judge_for_asset(conn, asset, ip=ip, esxi_names=esxi_names)
    return {"kind": blk[0], "label": blk[1], "reason": blk[2]} if blk else None


# ===== SSH banner 自報的身分（2026-09-15）=====
# 使用者看到 10.99.198.21「22 通、但收集帳號進不去」被列在待佈身分，問「這是 VM ESXi 嗎？」
# ——banner 自報 `Avi Cloud Controller；VMware Avi Load Balancer software (Broadcom)`，
# 是 VMware 的負載平衡器（NSX ALB），不是 ESXi，但**一樣不該納管**。
#
# 判斷需要的證據**當下就印在畫面上**（連線失敗訊息帶著 banner），只是程式沒拿來用：
# 資產庫沒登記／os 欄空白的機器，光看 os 判不出來，但 banner 已經自報身分了。
#
# ⚠️ 只認「產品自報」的字樣，不做模糊比對：一般 Linux 的 banner 是 OpenSSH 版本字串，
# 不會出現這些產品名。認不出來就不標——失敗方向是「漏標」（維持原本行為），不是誤標。
_BANNER_PRODUCTS = (
    (r"avi (cloud|load)|nsx\s*alb", "VMware Avi Load Balancer（NSX ALB）"),
    (r"esxi|vsphere", "VMware ESXi"),
    (r"\bvmware\b", "VMware 設備"),
    (r"fortigate|fortios", "FortiGate"),
    (r"big-?ip|f5 networks", "F5 BIG-IP"),
    (r"brocade|fabric os|\bfos\b", "Brocade SAN switch"),
    (r"cisco|nx-?os|ios-xe", "Cisco 網路設備"),
    (r"junos|juniper", "Juniper 網路設備"),
    (r"arubaos|aruba networks", "Aruba 網路設備"),
    (r"idrac|integrated dell remote", "iDRAC 管理卡"),
    (r"\bilo\b|integrated lights-out", "iLO 管理卡"),
    (r"netapp|ontap", "NetApp 儲存"),
    (r"powerstore|unity|isilon|storwize|purestorage", "儲存設備"),
)
_BANNER_RES = tuple((_re.compile(p, _re.I), label) for p, label in _BANNER_PRODUCTS)


def product_from_banner(text) -> str | None:
    """SSH 連線訊息（含 banner）自報的產品名；認不出來回 None。"""
    s = str(text or "")
    if not s:
        return None
    for rx, label in _BANNER_RES:
        if rx.search(s):
            return label
    return None
