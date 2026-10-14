"""批次納管只能碰伺服器 Linux——白名單，不認得就不碰（A6，2026-09-08 拍板）。

會走到這條規則，是因為**開 22 不等於是伺服器**。查 221 實際資料 4784 筆，
`os` 欄不是伺服器作業系統的有 1051 筆：網路設備、儲存設備、iDRAC 管理卡、
Cisco APIC、F5 BIG-IP、FortiGate、ATEN KVM、IBM i(V7R3/V7R5)……它們都吃 SSH，
banner 也常自報 Linux。在那些東西上 useradd 是會出事的等級。

守三件事：
1. **白名單**：不在可納管清單裡就不跑（黑名單會漏掉沒見過的廠牌，方向是錯的）
2. **CoreOS／RHCOS 明確排除**——它們是 Linux、SSH 進得去、useradd 也會成功，
   所以任何「像 Linux 就納管」的判法都會掃到它們。使用者原話：「不能動他們」
3. 排除的東西**要列出來講原因**，不是靜默消失（列了按不了才是雜訊；不列又會
   讓人以為機器不存在）
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import onboard_eligibility as el  # noqa: E402


# 這些值全部取自 221 資產庫真實出現過的寫法（只保留機型字串，不含任何 IP／人名）
def test_伺服器Linux放行():
    for v in ("Red Hat Enterprise Linux 8 (64-bit)", "RedHat 8.5",
              "Red Hat Enterprise Linux 9 (64-bit)", "Debian GNU/Linux 11",
              "Ubuntu 22.04", "CentOS Linux 7", "SUSE Linux Enterprise Server 15"):
        assert el.classify_os(v)[0] == "linux", v
        assert el.is_onboardable(v), v


def test_CoreOS與RHCOS一定要排除():
    """是 Linux、進得去、建帳號也會成功——所以只有明確排除才擋得住。

    它們是 OpenShift 節點：本機帳號會在節點重佈時消失（收集之後無聲失效），
    而且那批機器歸 OpenShift 管。221 上約 167 台。
    """
    for v in ("CoreOS4.20", "CoreOS 4.18", "CoreOS 4.12", "RHCOS 4", "RHCOS4.12",
              "Flatcar Container Linux", "Fedora CoreOS"):
        kind, why = el.classify_os(v)
        assert kind == "immutable", f"{v} 被判成 {kind}——會被納管"
        assert not el.is_onboardable(v), v
        assert "OpenShift" in why or "不可變" in why, why


def test_Container_Linux不可以被泛用規則放行():
    """`LINUX_OK` 有一條泛用的 `linux`。順序寫錯的話這個名字就會漏過去。"""
    assert el.classify_os("Container Linux 2512")[0] == "immutable"


def test_網通設備不放行():
    for v in ("網路設備", "Idrac 7.10.70.00", "APIC 5.2(8h)",
              "cisco 15.2(4)E7", "BIG-IP 17.1.1.4", "fortigate v7.0.12 build0523",
              "ATEN v1.5.141", "WC.16.10.0009", "12.3R9.4", "V7R3", "V7R5",
              "17.03.04b", "客製化系統"):
        kind, why = el.classify_os(v)
        assert kind == "appliance", f"{v} 被判成 {kind}"
        assert not el.is_onboardable(v), v
        assert v in why, "理由裡要帶原本的 os 字串，人才知道是哪一台的哪個欄位"


def test_儲存設備自成一類(): 
    """2026-10-13 使用者：「是 storage 你怎麼抓不出來，可把 storage 獨立分類出來」。
    以前跟網通／管理卡一起擠在 appliance，看不出哪些是儲存。拆出來之後
    **一樣不納管**——拆的是可見度，不是放寬。"""
    for v in ("儲存設備", "Avamar - 客製OS", "EMC UnityXT480 NAS (iSCSI target)",
              "NetApp ONTAP 9.12", "IBM Storwize V7000", "Pure Storage FlashArray"):
        kind, why = el.classify_os(v)
        assert kind == "storage", f"{v} 被判成 {kind}"
        assert not el.is_onboardable(v), v
        assert v in why


def test_Windows_Storage_Server不是儲存設備():
    """`storage` 規則若排在 WINDOWS_OS 前面，這台 Windows 伺服器就會被當成儲存
    設備而不納管——漏掉一台該納管的機器，而且畫面上還顯示得理直氣壯。"""
    assert el.classify_os("Windows Storage Server 2019")[0] == "windows"


def test_作業系統欄空白時改看資產名稱與用途():
    """使用者指的那台 NAS：os 欄是 `N/A`，但「EMC UnityXT480 NAS」寫在資產用途。"""
    r = el.not_onboardable("N/A", "NAS-055", "10.99.19.207", frozenset(),
                           extra_text="儲存設備 EMC UnityXT480 NAS (iSCSI target)")
    assert r is not None and r[0] == "storage", r
    assert "資產名稱／用途" in r[2], r


def test_自由文字只採信正面認出來的身分():
    """`appliance` 的語意是「認不出來」。若拿它套在自由文字上，「測試機」這種
    什麼都沒講的用途也會被判成設備——把「不知道」當成「知道它不是伺服器」。"""
    assert el.not_onboardable("", "h", "1.1.1.1", frozenset(), extra_text="測試機") is None
    # 用途寫 Linux 也不能拿來開門：那兩欄是人填的自由文字，風險不對稱
    assert el.not_onboardable("", "h", "1.1.1.1", frozenset(), extra_text="Linux 應用主機") is None
    # 但正面認出是 ESXi 就擋
    r = el.not_onboardable("", "h", "1.1.1.1", frozenset(), extra_text="ESXi 虛擬化主機")
    assert r is not None and r[0] == "esxi"


def test_沒填作業系統不是預設放行():
    """空值要落在 unknown（請人確認），**不能**掉進 linux。"""
    for v in (None, "", "  ", "N/A", "n/a", "-", "無", "不明"):
        kind, _ = el.classify_os(v)
        assert kind == "unknown", f"{v!r} 被判成 {kind}"
        assert not el.is_onboardable(v)


def test_Windows與ESXi與AIX各自歸類():
    assert el.classify_os("Windows Server 2019")[0] == "windows"
    assert el.classify_os("Microsoft Windows Server 2022 (64-bit)")[0] == "windows"
    assert el.classify_os("VMware ESXi")[0] == "esxi"
    assert el.classify_os("AIX 7.2")[0] == "aix"
    for v in ("Windows Server 2019", "VMware ESXi", "AIX 7.2"):
        assert not el.is_onboardable(v), v


def test_沒見過的東西一律不放行():
    """白名單的重點：新廠牌、新機型、亂填的值，預設都是**不碰**。"""
    for v in ("SomeVendor OS 9.9", "未來才會買的設備", "???", "0"):
        assert not el.is_onboardable(v), v
