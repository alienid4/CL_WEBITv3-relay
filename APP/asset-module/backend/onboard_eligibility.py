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
