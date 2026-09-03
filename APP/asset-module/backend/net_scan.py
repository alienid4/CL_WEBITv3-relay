"""採集底座（第一塊）：網段 TCP-sweep 掃描來源。

家裡沒有真實 vCenter/SNMP，但有真實網段——這個 ScanSource 直接探測整個 CIDR
「誰活著」，是資產盤點最核心的真實掃描（CIA 登記 vs 網路上實際存在）。
不需要 SSH 金鑰或 root，服務帳號(sysctl)就能跑；之後可再加一層 SSH 收 OS/硬體 facts。

判活著：對每台 IP 試連幾個常見 port——連得上(open)或被拒(closed 但主機有回應) = 活著；
全部逾時 = 沒回應（當作不存在）。方向同 M2 採集器：有回應才算數。

`_probe_host` 抽出來讓測試可注入，不用真的打網路。
"""
from __future__ import annotations

import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor

import fingerprint
from scan_sources import ScanResult, ScanSource

# 常見服務 port：任一有回應就代表主機活著（涵蓋 Linux/Windows/網通設備/本專案服務）
PROBE_PORTS = (22, 80, 443, 445, 3389, 8000, 3000, 8443, 161)


def _probe_host(ip: str, ports=PROBE_PORTS, timeout: float = 0.6) -> list[int] | None:
    """回傳該 IP 有回應的 open port 清單；主機沒回應(全逾時)回 None（=不存在）。
    被拒(closed)也算主機活著，但這裡只收 open port 當佐證；只要有任一 port 非逾時就視為活著。
    """
    open_ports: list[int] = []
    alive = False
    for port in ports:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            rc = s.connect_ex((ip, port))
            if rc == 0:
                open_ports.append(port)
                alive = True
            elif rc in (111, 10061):  # ECONNREFUSED（Linux/Windows）＝主機在、port 關
                alive = True
            # 其他(逾時 EAGAIN/ETIMEDOUT)不改變 alive
        except OSError:
            pass
        finally:
            s.close()
    return open_ports if alive else None


def _hostname(ip: str) -> str | None:
    try:
        return socket.gethostbyaddr(ip)[0]
    except OSError:
        return None


class NetworkSweepSource(ScanSource):
    """掃一個 CIDR 網段，回傳實際活著的主機。"""

    def __init__(self, cidr: str, name: str | None = None, timeout: float = 0.6, workers: int = 128,
                 probe=_probe_host, arp_reader=None, ttl_prober=None, banner_grabber=None):
        self.cidr = cidr
        self.name = name or f"網段掃描 {cidr}"
        self.timeout = timeout
        self.workers = workers
        self._probe = probe
        # 指紋收集器可注入，測試不打真網路；預設用 fingerprint 模組的真實作
        self._arp_reader = arp_reader or fingerprint.read_arp_table
        self._ttl_prober = ttl_prober or fingerprint.probe_ttl
        # banner 是確定性線索（服務自報平台），優先於 TTL/埠號的推測
        self._banner_grabber = banner_grabber or fingerprint.grab_banner

    def scan(self) -> list[ScanResult]:
        try:
            net = ipaddress.ip_network(self.cidr, strict=False)
        except ValueError as exc:
            raise ConnectionError(f"網段格式錯誤：{self.cidr}（{exc}）") from exc

        hosts = [str(h) for h in net.hosts()] or [str(net.network_address)]
        # ARP 表讀一次共用：既拿 MAC，也用來把「TCP-sweep 掃不到但 ARP 有」的主機撈回來（F4）
        arp = self._arp_reader()

        def work(ip: str):
            open_ports = self._probe(ip, timeout=self.timeout)
            if open_ports is None:
                # port 全逾時，但若 ARP 表裡有它＝主機其實在，只是不回應被探測的 port
                return (ip, [], True) if ip in arp else None
            return (ip, open_ports, False)

        alive: list[tuple[str, list[int], bool]] = []
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            for out in ex.map(work, hosts):
                if out is not None:
                    alive.append(out)

        results: list[ScanResult] = []
        for ip, open_ports, arp_only in alive:
            mac = arp.get(ip)
            vendor = fingerprint.oui_vendor(mac)
            ttl = self._ttl_prober(ip)
            # 有開 22 才抓 SSH banner（沒開就別浪費一次連線逾時）
            banner = self._banner_grabber(ip, 22) if 22 in open_ports else None
            os_guess = fingerprint.guess_os(ttl=ttl, open_ports=open_ports,
                                            vendor=vendor, banner=banner)
            port_str = ",".join(map(str, open_ports)) if open_ports else None
            model = "(網路探測)"
            if arp_only:
                model = "(ARP 補強：不回應探測埠)"
            elif port_str:
                model = f"(網路探測) open:{port_str}"
            results.append(
                ScanResult(
                    hostname=_hostname(ip) or "",
                    ip=ip,
                    device_model=model,
                    is_vm=vendor in ("VMware", "Microsoft Hyper-V", "VirtualBox", "Xen",
                                     "QEMU/KVM", "Red Hat Virtio"),  # OUI 認得出的虛擬化廠商
                    segment=self.cidr,
                    mac=mac,
                    mac_vendor=vendor,
                    open_ports=port_str,
                    ttl=ttl,
                    os_guess=os_guess,
                )
            )
        return results
