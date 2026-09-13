"""S16 主機指紋：把掃到的未登記主機從「光禿禿一個 IP」變成「認得出大概是什麼」。

三塊線索，全部**不必登入該機器**就拿得到（登入拿真主機名/序號是 facts_collector 的事）：
- MAC + OUI 廠商：00:0c:29 = VMware，一眼看出是 VM
- 開放埠 + TTL：3389=Windows、TTL 128≈Windows / 64≈Linux
- 綜合成一句 os_guess——**明講是「推測」**，畫面不可寫死成事實

網路動作（讀 ARP 表、ping 取 TTL）抽成可注入函式，測試不打真網路。
"""
from __future__ import annotations

import re
import subprocess

# 常見 OUI → 廠商。刻意是 curated 子集，不是完整 IEEE 資料庫（那有數萬筆、要定期更新）：
# 先涵蓋本網段實際出現的（全是 VMware）＋最常見的虛擬化/網卡廠商，查不到就回 None
# （畫面顯示「未知廠商」，不騙人）。要擴充直接加這張表。
_OUI = {
    # 虛擬化平台（最有價值——一眼看出是 VM）
    "00:0c:29": "VMware", "00:50:56": "VMware", "00:05:69": "VMware", "00:1c:14": "VMware",
    "00:15:5d": "Microsoft Hyper-V", "08:00:27": "VirtualBox",
    "00:16:3e": "Xen", "52:54:00": "QEMU/KVM", "00:1a:4a": "Red Hat Virtio",
    # 常見伺服器/網卡廠商
    "00:25:90": "Super Micro", "ac:1f:6b": "Super Micro", "3c:ec:ef": "Super Micro",
    "00:1b:21": "Intel", "00:1e:67": "Intel", "a4:bf:01": "Intel", "b4:96:91": "Intel",
    "b0:83:fe": "Dell", "00:14:22": "Dell", "18:66:da": "Dell", "f8:bc:12": "Dell",
    "00:0d:3a": "Microsoft Azure",
    "00:1d:09": "Dell", "84:2b:2b": "Dell",
}


def oui_vendor(mac: str | None) -> str | None:
    """MAC 前 3 段查廠商；查不到或格式不對回 None。"""
    if not mac:
        return None
    parts = mac.strip().lower().replace("-", ":").split(":")
    if len(parts) < 3:
        return None
    return _OUI.get(":".join(parts[:3]))


def grab_banner(ip: str, port: int = 22, timeout: float = 2.0) -> str | None:
    """抓服務 banner。不需要任何帳密，一個 TCP 連線就拿得到。

    價值：SSH banner 會**直說**自己是什麼平台，這是確定性線索不是推測——
        SSH-2.0-OpenSSH_for_Windows_7.7   → Windows
        SSH-2.0-OpenSSH_8.7               → Linux
    實測 .110 同時開 22 和 3389，光看埠號要靠「3389 優先」猜（Linux 跑 xrdp 就會猜錯），
    但 banner 直接寫著 for_Windows，一翻兩瞪眼。
    """
    import socket as _socket
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((ip, port))
        data = s.recv(256)
        return data.decode("utf-8", "replace").strip() or None
    except OSError:
        return None
    finally:
        s.close()


def os_from_banner(banner: str | None) -> str | None:
    """從 banner 直接判平台。認不出來回 None，交給後面的推測邏輯。"""
    if not banner:
        return None
    low = banner.lower()
    if "for_windows" in low or "windows" in low:
        return "Windows（SSH banner 自報）"
    if "openssh" in low or "dropbear" in low:
        return "Linux/Unix（SSH banner 自報）"
    if "microsoft" in low or "iis" in low:
        return "Windows（服務 banner 自報）"
    return None


def guess_os(ttl: int | None = None, open_ports=None, vendor: str | None = None,
             banner: str | None = None) -> str | None:
    """綜合 TTL／開放埠猜作業系統。回傳「人看得懂＋帶佐證」的字串，或 None（線索不足）。

    ⚠️ 回傳字串一律帶佐證來源（如「TTL 128」「RDP 3389」），因為這是**推測不是事實**，
    畫面要讓人知道憑什麼這樣猜。TTL 會被路由每跳減 1，所以用區間判斷（Windows 基準 128、
    Linux 基準 64、網路設備 255）。
    """
    # banner 是「它自己說的」，優先於任何推測
    from_banner = os_from_banner(banner)
    if from_banner:
        return from_banner

    ports = set(open_ports or [])
    if 3389 in ports:
        return "Windows（RDP 3389）"
    if ttl is not None:
        if ttl > 128:
            return f"網路設備/其他（TTL {ttl}）"
        if ttl > 64:
            return "Windows（TTL≈128）"
        if ttl > 0:
            return "Linux/Unix（TTL≈64）"
    if 445 in ports:
        return "Windows（SMB 445）"
    if 22 in ports:
        return "Linux/Unix（SSH 22）"
    if 161 in ports:
        return "網路設備（SNMP 161）"
    return None


_ARP_LINE = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})\b.*?lladdr\s+([0-9a-fA-F:]{17})")


def read_arp_table(runner=None) -> dict[str, str]:
    """讀本機 ARP/neighbour 表，回 {ip: mac}。

    價值：有些主機（實測 .110/.113）不回應被探測的 port，TCP-sweep 完全掃不到，
    但只要近期通訊過就會留在 ARP 表裡——這是把它們撈回來的唯一線索（F4）。
    """
    out = (runner or _default_ip_neigh)()
    table: dict[str, str] = {}
    for line in out.splitlines():
        m = _ARP_LINE.search(line)
        if m:
            table[m.group(1)] = m.group(2).lower()
    return table


def probe_ttl(ip: str, runner=None) -> int | None:
    """對單一 IP ping 一次取 TTL。ping 是 setuid，不需要 root；拿不到回 None。"""
    out = (runner or _default_ping)(ip)
    m = re.search(r"ttl=(\d+)", out, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _default_ip_neigh() -> str:
    try:
        return subprocess.run(
            ["ip", "neigh"], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _default_ping(ip: str) -> str:
    try:
        return subprocess.run(
            ["ping", "-c", "1", "-W", "1", ip], capture_output=True, text=True, timeout=3
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


# 指紋 → 該怎麼納管。這是「找到一個活著的 IP」之後最實際的問題：
# 我連不進去、也不知道它是什麼，那我下一步要做什麼？
ONBOARD_LINUX = "linux"
ONBOARD_WINDOWS = "windows"
ONBOARD_NETDEV = "netdev"
ONBOARD_UNKNOWN = "unknown"

ONBOARD_HINT = {
    ONBOARD_LINUX: "在該機器以 root 執行 Linux 納管腳本（webit3-bootstrap.sh）",
    ONBOARD_WINDOWS: "在該機器以系統管理員 PowerShell 執行 Windows 納管腳本"
                     "（需先有 OpenSSH Server）",
    ONBOARD_NETDEV: "網路設備，沒有可佈的作業系統帳號——建議標為不適用，改用 SNMP 納管",
    ONBOARD_UNKNOWN: "線索不足，無法判斷平台。可先確認它是否開放 22/3389，"
                     "或從 MAC 廠商、實體位置追查是什麼設備",
}


def onboard_method(os_guess: str | None = None, open_ports=None,
                   banner: str | None = None) -> dict:
    """依指紋判斷「該用哪一套納管方式」，並誠實給出信心度。

    信心度直接影響使用者要不要照做：banner 自報是 confirmed（它自己說的），
    埠號/TTL 推測是 likely（可能猜錯），什麼都沒有就是 unknown（別亂試）。
    """
    ports = set(open_ports or [])
    text = (os_guess or "")

    if banner and os_from_banner(banner):
        plat = ONBOARD_WINDOWS if "Windows" in os_from_banner(banner) else ONBOARD_LINUX
        return {"method": plat, "confidence": "confirmed",
                "evidence": f"服務 banner：{banner[:80]}", "hint": ONBOARD_HINT[plat]}

    if "網路設備" in text or (161 in ports and 22 not in ports):
        return {"method": ONBOARD_NETDEV, "confidence": "likely",
                "evidence": "只開 SNMP、沒有 SSH", "hint": ONBOARD_HINT[ONBOARD_NETDEV]}

    if "Windows" in text or 3389 in ports:
        return {"method": ONBOARD_WINDOWS, "confidence": "likely",
                "evidence": f"開放埠 {sorted(ports)}／{text}",
                "hint": ONBOARD_HINT[ONBOARD_WINDOWS]}

    if "Linux" in text or 22 in ports:
        return {"method": ONBOARD_LINUX, "confidence": "likely",
                "evidence": f"開放埠 {sorted(ports)}／{text}",
                "hint": ONBOARD_HINT[ONBOARD_LINUX]}

    return {"method": ONBOARD_UNKNOWN, "confidence": "unknown",
            "evidence": f"開放埠 {sorted(ports) or '無'}",
            "hint": ONBOARD_HINT[ONBOARD_UNKNOWN]}
