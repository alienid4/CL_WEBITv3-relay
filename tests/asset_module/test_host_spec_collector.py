"""host_spec_collector 解析測試（合成資料）。Linux 格式標準；AIX 依文件。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"))

import host_spec_collector as H  # noqa: E402

LSCPU = """\
Architecture:        x86_64
CPU(s):              16
Socket(s):           2
Model name:          Intel(R) Xeon(R) Gold 6130 CPU @ 2.10GHz
"""
MEMINFO = "MemTotal:       32865432 kB\nMemFree:  100 kB\n"
LSBLK = "sda 480103981056 disk\nsdb 960197124096 disk\nsr0 1073741312 rom\n"

PRTCONF = """\
System Model: IBM,8286-42A
Machine Serial Number: 21ABCDE
Processor Type: PowerPC_POWER8
Number Of Processors: 8
Processor Clock Speed: 3891 MHz
Memory Size: 65536 MB
"""


def test_linux_parse():
    assert H.parse_lscpu(LSCPU)["cpu_model"].startswith("Intel(R) Xeon")
    assert H.parse_lscpu(LSCPU)["cpu_count"] == "16"
    assert H.parse_meminfo(MEMINFO) == round(32865432 / 1024)
    # 只加 disk、不含 rom
    assert H.parse_lsblk_bytes(LSBLK) == 480103981056 + 960197124096


def test_aix_parse():
    p = H.parse_prtconf(PRTCONF)
    assert p["model"] == "IBM,8286-42A"
    assert p["serial"] == "21ABCDE"
    assert p["cpu_count"] == "8"
    assert p["mem"] == "65536"
    d = H.parse_aix_disks("hdisk0\nhdisk1\n", "rootvg\ndatavg\n")
    assert d["disk_count"] == 2 and d["vgs"] == ["rootvg", "datavg"]


def test_collect_uses_runner_linux():
    def fake_runner(host, cmd):
        if "lscpu" in cmd: return LSCPU
        if "meminfo" in cmd: return MEMINFO
        if "lsblk" in cmd: return LSBLK
        return ""
    res = H.collect(fake_runner, "10.99.0.1", "linux")
    assert res["spec"]["cores"] == "16"
    assert res["spec"]["mem_mb"] == round(32865432 / 1024)
    assert res["spec"]["storage"].endswith("GB")
