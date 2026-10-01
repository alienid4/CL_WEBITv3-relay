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


def test_網通與儲存設備不放行():
    for v in ("網路設備", "儲存設備", "Idrac 7.10.70.00", "APIC 5.2(8h)",
              "cisco 15.2(4)E7", "BIG-IP 17.1.1.4", "fortigate v7.0.12 build0523",
              "ATEN v1.5.141", "WC.16.10.0009", "12.3R9.4", "V7R3", "V7R5",
              "17.03.04b", "Avamar - 客製OS", "客製化系統"):
        kind, why = el.classify_os(v)
        assert kind == "appliance", f"{v} 被判成 {kind}"
        assert not el.is_onboardable(v), v
        assert v in why, "理由裡要帶原本的 os 字串，人才知道是哪一台的哪個欄位"


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
