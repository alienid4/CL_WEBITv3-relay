"""2026-10-29 10.2「作業系統版本」：AIX／Windows 分版本、Linux 一行、其餘歸其他；有納管實測 OS 就以實測為準（使用者：
「納管以後資訊一律以系統查到的為主，CIA 沒查到準確」）。全部假資料。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import collected_os  # noqa: E402
import db  # noqa: E402
import v2_dist  # noqa: E402
import v2_hosts  # noqa: E402


def _lab(os_text, os_class, measured=None):
    return v2_hosts.os_version_of(measured, [{"asset_serial": "X", "os": os_text}], {"os_class": os_class})


def test_版本標籤_Windows分版本且2012與R2分開_AIX認oslevel_Linux一行():
    assert _lab("Windows Server 2019 Standard", "Windows")[0] == "Windows Server 2019"
    assert _lab("Microsoft Windows Server 2022 (64-bit)", "Windows")[0] == "Windows Server 2022"
    assert _lab("Windows Server 2012 R2 Datacenter", "Windows")[0] == "Windows Server 2012 R2"
    assert _lab("Windows Server 2012", "Windows")[0] == "Windows Server 2012"
    assert _lab("Windows", "Windows")[0] == "Windows（版本不明）"
    assert _lab("7200-05-09-2446", "AIX")[0] == "AIX 7.2" and _lab("AIX 7.1.5.0", "AIX")[0] == "AIX 7.1"
    assert _lab("AIX", "AIX")[0] == "AIX（版本不明）"
    assert _lab("CentOS 7.6 1810", "Linux")[0] == "Linux" and _lab("RHEL 9.4", "Linux")[0] == "Linux"
    assert _lab("Cisco IOS 15", "網路設備")[0] == "其他（非 AIX／Windows／Linux）"
    assert _lab("Windows Server 2019", "Windows")[1] == "登記"


def test_有實測就以實測為準_CIA寫Windows但機器講RedHat():
    meas = {"X": {"platform": "linux", "os": "Red Hat Enterprise Linux 9.4", "collected_at": "2026-10-20 10:00:00"}}
    lab, src, text = _lab("Windows Server 2019", "Windows", meas)
    assert (lab, src) == ("Linux", "實測") and "Red Hat" in text
    meas = {"X": {"platform": "aix", "os": "7200-05-09-2446", "collected_at": "2026-10-20 10:00:00"}}
    assert _lab("", "其他", meas)[:2] == ("AIX 7.2", "實測")                       # CIA 空白／判成其他，實測 AIX 7.2
    meas = {"X": {"platform": "windows", "os": "Windows Server 2022", "collected_at": "x"}}
    assert _lab("Windows Server 2016", "Windows", meas)[:2] == ("Windows Server 2022", "實測")   # CIA 版本舊，以實測為準
    assert _lab("Cisco IOS", "網路設備", {"X": {"platform": "linux", "os": "x", "collected_at": "x"}})[0] == "其他（非 AIX／Windows／Linux）"  # 刻意分類不被蓋


def test_10_2版本維度_加總等於全部台數_下鑽列數等於格子(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    conn = db.get_connection(p)
    try:
        for i, (osn, host) in enumerate([("Windows Server 2019 Standard", "w1"), ("Windows Server 2019", "w2"), ("Windows Server 2012 R2", "w3"),
                                         ("7200-05-09-2446", "a1"), ("CentOS 7.6", "l1"), ("RHEL 8.6", "l2"), ("Cisco IOS", "n1")], 1):
            db.insert_hardware(conn, asset_serial=f"V-{i}", hostname=host, ip=f"10.99.4.{i}", os=osn)
        conn.commit()
        s = v2_dist.summary(conn)
        rows = s["dims"]["osver"]
        assert sum(r["all"] for r in rows) == s["total"] == 7                      # 跟其他維度一樣：加總＝全部登記台數
        by = {r["value"]: r["all"] for r in rows}
        assert by["Windows Server 2019"] == 2 and by["Windows Server 2012 R2"] == 1 and by["Linux"] == 2
        for r in rows:
            d = v2_dist.drill(conn, "osver", r["value"], "all")
            assert len(d["rows"]) == r["all"]
            assert all("os_text" in x and "os_src" in x for x in d["rows"])
    finally:
        conn.close()


def test_納管實測蓋過CIA_統計跟著實測(tmp_path, monkeypatch):
    p = tmp_path / "t.db"
    db.init_db(p)
    conn = db.get_connection(p)
    try:
        db.insert_hardware(conn, asset_serial="M-1", hostname="m1", ip="10.99.4.11", os="Windows Server 2019")
        conn.commit()
        monkeypatch.setattr(collected_os, "measured_map", lambda c: {"M-1": {"platform": "linux", "os": "RHEL 9.4", "collected_at": "t"}})
        s = v2_dist.summary(conn)
        by = {r["value"]: r for r in s["dims"]["osver"]}
        assert "Windows Server 2019" not in by and by["Linux"]["all"] == 1 and by["Linux"]["measured"] == 1
    finally:
        conn.close()
