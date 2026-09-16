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

_NOT_ONBOARDABLE_LABEL = {
    "esxi": "VMware ESXi",
    "immutable": "OpenShift 節點",
    "appliance": "設備（不在可納管作業系統清單）",
}
_IPV4 = _re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def _name_keys(name) -> set[str]:
    """主機名比對鍵：原字串大寫＋去網域的短名（IP 不去網域，不然 10.x 會變成 10）。"""
    s = str(name or "").strip().upper()
    if not s:
        return set()
    if _IPV4.match(s):
        return {s}
    return {s, s.split(".")[0]}


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
                    esxi_names=frozenset()) -> tuple[str, str, str] | None:
    """回 (類別, 標籤, 依據)；可以試著納管就回 None。"""
    kind, why = classify_os(os_text)
    if kind in _NOT_ONBOARDABLE_LABEL:
        return kind, _NOT_ONBOARDABLE_LABEL[kind], f"依據：資產登記的作業系統「{why}」"
    if esxi_names and ((_name_keys(hostname) | _name_keys(ip)) & set(esxi_names)):
        return "esxi", _NOT_ONBOARDABLE_LABEL["esxi"], (
            "依據：vCenter（RVTools）匯入把它列為 ESXi 主機——虛擬化平台本身，不建本機帳號")
    return None


# ===== SSH banner 自報的身分（2026-09-15）=====
# 使用者看到 10.92.198.21「22 通、但收集帳號進不去」被列在待佈身分，問「這是 VM ESXi 嗎？」
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
