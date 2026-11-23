"""M2 第一片 服務發現：ss/netstat 解析、曝露判定、猜測來源分級。

全部用注入的假 runner，不打真 SSH——解析邏輯本身就是最容易出錯的地方，
要能離線確定性地測。真機驗證是另一回事（221 上跑）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"))

import service_collector  # noqa: E402

# 真實 ss -tlnp 輸出樣貌（有 root：帶 users:(("name",pid=…))）
SS_ROOT = """State  Recv-Q Send-Q Local Address:Port  Peer Address:Port Process
LISTEN 0      128          0.0.0.0:22         0.0.0.0:*     users:(("sshd",pid=1234,fd=3))
LISTEN 0      511                *:80               *:*     users:(("nginx",pid=999,fd=6))
LISTEN 0      70         127.0.0.1:33060            *:*     users:(("mysqld",pid=800,fd=21))
LISTEN 0      128             [::]:22            [::]:*     users:(("sshd",pid=1234,fd=4))
"""

# 一般帳號（webit3scan 的實況）：沒有 Process 欄
SS_NOROOT = """State  Recv-Q Send-Q Local Address:Port  Peer Address:Port
LISTEN 0      128          0.0.0.0:22         0.0.0.0:*
LISTEN 0      128    YOUR_SERVER_IP:8000         0.0.0.0:*
LISTEN 0      70         127.0.0.1:3306            *:*
"""

NETSTAT = """Active Internet connections (only servers)
Proto Recv-Q Send-Q Local Address           Foreign Address         State       PID/Program name
tcp        0      0 0.0.0.0:22              0.0.0.0:*               LISTEN      1234/sshd
tcp        0      0 127.0.0.1:6379          0.0.0.0:*               LISTEN      777/redis-server
tcp        0      0 192.168.1.5:45012       192.168.1.9:3306        ESTABLISHED 900/python
"""

UNITS = """sshd.service       loaded active running OpenSSH server daemon
nginx.service      loaded active running The nginx HTTP server
webit3-api.service loaded active running webit3 asset API
"""


def _runner(listen_out, units_out=UNITS):
    def run(host, cmd):
        return units_out if "systemctl" in cmd else listen_out
    return run


def test_ss有root時收得到行程名():
    r = service_collector.collect(_runner(SS_ROOT), "10.0.0.1", "linux")
    by_port = {s["port"]: s for s in r["services"]}
    assert by_port[22]["process"] == "sshd"
    assert by_port[80]["process"] == "nginx"
    assert r["process_visible"] is True
    # 行程名是機器講的 → guess_source=process（確定），不是埠號猜的
    assert by_port[80]["guess_source"] == "process"


def test_ipv4與ipv6同一服務不重複記():
    """ss 會把 0.0.0.0:22 與 [::]:22 分兩行印，那是同一個 sshd。

    不去重的話畫面上每台機器的服務數都會虛胖一倍，看的人會以為真的多開了服務。
    """
    r = service_collector.collect(_runner(SS_ROOT), "10.0.0.1", "linux")
    ports_22 = [s for s in r["services"] if s["port"] == 22]
    assert len(ports_22) == 1


def test_無root時只有埠沒有行程名且不用埠號假裝():
    r = service_collector.collect(_runner(SS_NOROOT), "10.0.0.1", "linux")
    assert r["process_visible"] is False
    by_port = {s["port"]: s for s in r["services"]}
    # process 一律留空——不可以拿「3306→MySQL」的猜測填進去假裝收到了
    assert by_port[3306]["process"] is None
    # 但 service_guess 仍給提示，並標明是埠號猜的
    assert by_port[3306]["service_guess"] == "MySQL/MariaDB"
    assert by_port[3306]["guess_source"] == "port"


def test_netstat格式也吃得下且只收listen():
    r = service_collector.collect(_runner(NETSTAT), "10.0.0.1", "linux")
    ports = {s["port"] for s in r["services"]}
    assert ports == {22, 6379}          # ESTABLISHED 那行不算監聽服務
    by_port = {s["port"]: s for s in r["services"]}
    assert by_port[6379]["process"] == "redis-server"


def test_曝露判定分得出對外與只給本機():
    """綁 127.0.0.1 的服務別台主機不可能依賴它——這是依賴分析的前提，不能只看埠。"""
    r = service_collector.collect(_runner(SS_ROOT), "10.0.0.1", "linux")
    by_port = {s["port"]: s for s in r["services"]}
    assert by_port[22]["exposure"] == "all"          # 0.0.0.0
    assert by_port[80]["exposure"] == "all"          # *
    assert by_port[33060]["exposure"] == "localhost"  # 127.0.0.1

    r2 = service_collector.collect(_runner(SS_NOROOT), "10.0.0.1", "linux")
    by_port2 = {s["port"]: s for s in r2["services"]}
    assert by_port2[8000]["exposure"] == "specific"   # 綁單一網卡 IP


def test_基礎服務有標記但不被過濾掉():
    """SSH/NTP 這種管理流量標成 infra 讓畫面可收合，但採集端不替使用者決定要不要看。"""
    r = service_collector.collect(_runner(SS_ROOT), "10.0.0.1", "linux")
    by_port = {s["port"]: s for s in r["services"]}
    assert by_port[22]["is_infra"] == 1
    assert by_port[80]["is_infra"] == 0


def test_systemd單元清單解析():
    r = service_collector.collect(_runner(SS_ROOT), "10.0.0.1", "linux")
    assert "sshd.service" in r["units"]
    assert "webit3-api.service" in r["units"]


def test_收集失敗要明確拋錯不要吞成空清單():
    """連不上卻回空清單，畫面會顯示「這台沒有任何服務」——那是最糟的假資料。"""
    def broken(host, cmd):
        raise OSError("connection refused")

    try:
        service_collector.collect(broken, "10.0.0.1", "linux")
    except ConnectionError as exc:
        assert "監聽清單" in str(exc)
    else:
        raise AssertionError("收集失敗時必須拋出 ConnectionError")


def test_單元清單拿不到不影響主結果():
    """systemctl 是加分項；它壞掉不該讓整台主機的埠清單一起消失。"""
    def half_broken(host, cmd):
        if "systemctl" in cmd:
            raise OSError("no systemd")
        return SS_ROOT

    r = service_collector.collect(half_broken, "10.0.0.1", "linux")
    assert r["units"] == []
    assert len(r["services"]) >= 3


def test_未支援平台明確拒絕():
    try:
        service_collector.collect(_runner(SS_ROOT), "10.0.0.1", "vms")
    except ValueError as exc:
        assert "未支援" in str(exc)
    else:
        raise AssertionError("未知平台必須拋 ValueError")
