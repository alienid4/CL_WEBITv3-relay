"""S16 指紋：OUI 廠商、OS 猜測、ARP 補強。全部不打真網路（注入假 runner）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import fingerprint  # noqa: E402
from net_scan import NetworkSweepSource  # noqa: E402


# ===== F2：OUI → 廠商 =====

def test_vmware_oui認得出來():
    assert fingerprint.oui_vendor("00:0c:29:49:6a:03") == "VMware"
    assert fingerprint.oui_vendor("00-50-56-AB-CD-EF") == "VMware"  # 破折號+大寫也要認


def test_未知oui回None不亂猜():
    assert fingerprint.oui_vendor("de:ad:be:ef:00:01") is None
    assert fingerprint.oui_vendor(None) is None
    assert fingerprint.oui_vendor("亂七八糟") is None


# ===== F3：OS 猜測 =====

def test_3389是Windows的強訊號():
    assert "Windows" in fingerprint.guess_os(open_ports=[3389, 445])


def test_ttl區間判斷():
    assert "Linux" in fingerprint.guess_os(ttl=64)
    assert "Linux" in fingerprint.guess_os(ttl=63)      # 隔一跳
    assert "Windows" in fingerprint.guess_os(ttl=128)
    assert "Windows" in fingerprint.guess_os(ttl=127)
    assert "網路設備" in fingerprint.guess_os(ttl=255)


def test_線索不足回None():
    assert fingerprint.guess_os() is None
    assert fingerprint.guess_os(open_ports=[12345]) is None


def test_猜測一定帶佐證來源():
    # 因為是推測不是事實，畫面要能講出憑什麼——回傳字串必須含 TTL 或埠號
    g = fingerprint.guess_os(ttl=64)
    assert "TTL" in g
    g2 = fingerprint.guess_os(open_ports=[3389])
    assert "3389" in g2


# ===== F4：ARP 表解析 + 把掃不到的主機撈回來 =====

def test_arp表解析():
    sample = (
        "10.99.0.1 dev ens160 lladdr c0:2e:5f:12:a9:30 REACHABLE\n"
        "10.99.0.110 dev ens160 lladdr 00:0c:29:49:6a:03 STALE\n"
        "192.168.1.22 dev ens160 FAILED\n"     # 沒 lladdr 不能收
    )
    t = fingerprint.read_arp_table(runner=lambda: sample)
    assert t == {"10.99.0.1": "c0:2e:5f:12:a9:30", "10.99.0.110": "00:0c:29:49:6a:03"}


def test_arp補強撈回不回應探測埠的主機():
    """.113 不回應任何被探測的 port（probe 回 None），但 ARP 表裡有它 → 必須被撈回來，
    否則這台永遠不會出現在掃描結果，也就永遠無法納管（實測 .110/.113 就是這樣被漏掉）。"""
    arp = {"10.99.0.113": "c0:25:2f:8a:be:09"}
    src = NetworkSweepSource(
        "10.99.0.113/32",
        probe=lambda ip, timeout: None,            # 所有 port 都逾時
        arp_reader=lambda: arp,
        ttl_prober=lambda ip: 64,
    )
    results = src.scan()
    assert len(results) == 1
    r = results[0]
    assert r.ip == "10.99.0.113"
    assert r.mac == "c0:25:2f:8a:be:09"
    assert "ARP" in r.device_model                 # 標明是 ARP 補強來的


def test_掃到的主機帶完整指紋():
    arp = {"10.99.0.110": "00:0c:29:49:6a:03"}
    src = NetworkSweepSource(
        "10.99.0.110/32",
        probe=lambda ip, timeout: [3389],
        arp_reader=lambda: arp,
        ttl_prober=lambda ip: 128,
    )
    r = src.scan()[0]
    assert r.mac_vendor == "VMware"
    assert r.is_vm is True                          # VMware OUI → 判定為 VM
    assert r.open_ports == "3389"
    assert r.ttl == 128
    assert "Windows" in r.os_guess


def test_真的掃不到也沒ARP就是不存在():
    src = NetworkSweepSource(
        "192.168.1.99/32",
        probe=lambda ip, timeout: None,
        arp_reader=lambda: {},                      # ARP 也沒有
        ttl_prober=lambda ip: None,
    )
    assert src.scan() == []


# ===== SSH banner：確定性線索，優先於推測 =====

def test_banner自報平台_優先於埠號推測():
    """實測踩到：.110 同時開 22 和 3389，光看埠號要靠「3389 優先」猜——
    Linux 跑 xrdp 就會猜錯。但 SSH banner 直接寫著 for_Windows，一翻兩瞪眼。"""
    win = "SSH-2.0-OpenSSH_for_Windows_7.7"
    lin = "SSH-2.0-OpenSSH_8.7"
    assert "Windows" in fingerprint.os_from_banner(win)
    assert "Linux" in fingerprint.os_from_banner(lin)

    # banner 要蓋過埠號推測：這台開 22（看起來像 Linux）但 banner 說是 Windows
    g = fingerprint.guess_os(open_ports=[22], banner=win)
    assert "Windows" in g and "banner" in g   # 且要標明憑據是 banner

    # 沒 banner 就退回原本的推測，不能整個壞掉
    assert "Windows" in fingerprint.guess_os(open_ports=[3389])
    assert fingerprint.os_from_banner(None) is None


def test_建議納管方式_信心度要誠實():
    """使用者的真實情境：抓到活著的 IP、進不去、不知道是什麼，下一步做什麼？"""
    # banner 自報 → confirmed（它自己說的）
    r = fingerprint.onboard_method(banner="SSH-2.0-OpenSSH_for_Windows_7.7")
    assert r["method"] == fingerprint.ONBOARD_WINDOWS and r["confidence"] == "confirmed"

    # 只有埠號 → likely（可能猜錯，要講清楚）
    r = fingerprint.onboard_method(os_guess="Windows（RDP 3389）", open_ports=[3389])
    assert r["method"] == fingerprint.ONBOARD_WINDOWS and r["confidence"] == "likely"

    r = fingerprint.onboard_method(os_guess="Linux/Unix（SSH 22）", open_ports=[22])
    assert r["method"] == fingerprint.ONBOARD_LINUX and r["confidence"] == "likely"

    # 網路設備沒有可佈的帳號——不要叫人去跑納管腳本
    r = fingerprint.onboard_method(open_ports=[161])
    assert r["method"] == fingerprint.ONBOARD_NETDEV

    # 什麼線索都沒有 → unknown，不可亂建議（叫人亂試比不建議更糟）
    r = fingerprint.onboard_method(open_ports=[])
    assert r["method"] == fingerprint.ONBOARD_UNKNOWN and r["confidence"] == "unknown"

    # 每一種都要給得出「下一步做什麼」
    for m in (fingerprint.ONBOARD_LINUX, fingerprint.ONBOARD_WINDOWS,
              fingerprint.ONBOARD_NETDEV, fingerprint.ONBOARD_UNKNOWN):
        assert fingerprint.ONBOARD_HINT[m]
