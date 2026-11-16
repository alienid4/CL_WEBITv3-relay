"""2.043.9（D3）：7.3 軟體清單每種軟體的台數、各版本台數、搜尋下鑽，一律「一台＝system_stats.machine_key」、排除退役。
台數＝點下去的列數。全部假資料、IP 用 10.99.x。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BE = ROOT / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import machine_identity  # noqa: E402
import software_inventory as si  # noqa: E402
import system_stats  # noqa: E402
from lifecycle import RETIRED_STATUS  # noqa: E402
from test_v2_batch1 import _conn  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_mode():
    yield
    machine_identity._STATE.update({"mode": machine_identity.MODE_LEGACY, "pending": frozenset(), "host_fix": {}})


def _pkg(c, ip, serial, name, version, arch="x86_64"):
    c.execute("INSERT INTO host_package (ip, asset_serial, name, version, arch, source, first_seen, last_seen) "
              "VALUES (?, ?, ?, ?, ?, 'ssh_rpm', '2026-10-01 00:00', datetime('now','localtime'))",
              (ip, serial, name, version, arch))


@pytest.fixture
def c(tmp_path):
    conn = _conn(tmp_path)
    db.set_setting(conn, "machine_key_mode", "hostname")
    db.insert_hardware(conn, asset_serial="W1", hostname="web01", ip="10.99.9.1", os="RHEL 9")
    db.insert_hardware(conn, asset_serial="W1b", hostname="web01", ip="10.99.9.2", os="RHEL 9")   # 同一台、第二個 IP
    db.insert_hardware(conn, asset_serial="A1", hostname="alpha01", ip="10.99.9.5", os="RHEL 9")
    db.insert_hardware(conn, asset_serial="B1", hostname="beta01", ip="10.99.9.5", os="RHEL 9")   # 同 IP、不同主機名
    db.insert_hardware(conn, asset_serial="R1", hostname="old01", ip="10.99.9.8", os="RHEL 9",
                       asset_status=sorted(RETIRED_STATUS)[0])
    conn.commit()
    machine_identity.refresh(conn)
    _pkg(conn, "10.99.9.1", "W1", "openssl", "3.0")
    _pkg(conn, "10.99.9.2", "W1b", "openssl", "3.1")      # 同一台兩個 IP、兩個版本
    _pkg(conn, "10.99.9.5", "A1", "openssl", "3.0")
    _pkg(conn, "10.99.9.5", "B1", "openssl", "3.0", "noarch")   # host_package 以 (ip,name,arch,version) 唯一，換 arch 才放得進同 IP 第二台
    _pkg(conn, "10.99.9.8", "R1", "openssl", "1.1")       # 退役：不算
    _pkg(conn, "10.99.9.8", "R1", "telnet", "0.17")       # 只有退役機器裝的軟體：不列
    conn.commit()
    yield conn
    conn.close()


def _mk(host, ip, sn):
    return system_stats.machine_key(host, ip, sn)


def test_軟體清單台數按machine_key_排除退役(c):
    rows = {r["name"]: r for r in si.list_software(c)}
    assert set(rows) == {"openssl"}, "只有退役機器裝的軟體不列"
    o = rows["openssl"]
    assert o["host_count"] == 3, "web01 兩個 IP 算一台、alpha01／beta01 同 IP 是兩台、退役不算"
    assert o["version_count"] == 2 and o["versions"] == ["3.0", "3.1"], "退役那台的 1.1 不列"
    assert o["current_host_count"] == 3 and o["stale_host_count"] == 0
    assert si.software_summary(c)["names"] == 1 == len(si.list_software(c)), "頁首軟體種數＝清單列數"


def test_搜尋下鑽一台一列_台數等於列數_鍵就是machine_key(c):
    hs = si.software_hosts(c, "openssl")
    assert len(hs) == si.list_software(c)[0]["host_count"]
    assert {h["key"] for h in hs} == {_mk("web01", "10.99.9.1", "W1"), _mk("alpha01", "10.99.9.5", "A1"), _mk("beta01", "10.99.9.5", "B1")}
    web = next(h for h in hs if h["hostname"] == "web01")
    assert web["versions"] == ["3.0", "3.1"] and set(web["ips"]) == {"10.99.9.1", "10.99.9.2"}
    assert all(h["hostname"] != "old01" for h in hs)


def test_各版本台數_同一台兩版本各算一台(c):
    v = {x["version"]: x["host_count"] for x in si.software_versions(c, "openssl")}
    assert v == {"3.0": 3, "3.1": 1}
    for ver, n in v.items():
        assert len(si.software_hosts(c, "openssl", ver)) == n, "版本台數＝點那個版本的列數"


def test_分組不能改回IP_原始碼走同一支(c):
    src = (BE / "software_inventory.py").read_text(encoding="utf-8")
    body = src[src.index("def _live_rows"):src.index("def list_software")]
    assert "system_stats.machine_key(" in body and "RETIRED_STATUS" in body
    for fn in ("def list_software", "def software_versions", "def software_hosts"):
        seg = src[src.index(fn):]
        seg = seg[:seg.index("\ndef ", 5)]
        assert "_live_rows(" in seg and "DISTINCT hp.ip" not in seg and "DISTINCT ip" not in seg, fn


def _soft_results(c):
    import verify_numbers as vn
    vn.RESULTS.clear()
    vn.check_card_drilldowns(c)
    return [r for r in vn.RESULTS if r["page"] == "軟體盤點" or r["name"] == "這段檢查自己出錯"]


def test_verify_numbers_軟體台數全部對得上(c):
    rs = _soft_results(c)
    names = {r["name"] for r in rs}
    assert any("各版本台數" in n for n in names) and any("點下去的主機列數" in n for n in names)
    assert all(r["ok"] for r in rs), [r for r in rs if not r["ok"]]


def test_verify_numbers_版本下鑽少一列會紅(c, monkeypatch):
    """突變：點版本下鑽少回一台 → 版本層不變式要紅（證明真的有驗版本層）。"""
    orig = si.software_hosts

    def broken(conn, name, version=None):
        rows = orig(conn, name, version)
        return rows[:-1] if version == "3.0" else rows
    monkeypatch.setattr(si, "software_hosts", broken)
    bad = [r["name"] for r in _soft_results(c) if not r["ok"]]
    assert any("各版本台數" in n for n in bad), bad


def test_verify_numbers_獨立側不共用software_inventory的SQL():
    src = (BE / "verify_numbers.py").read_text(encoding="utf-8")
    seg = src[src.index("V2 7.3（2.043.8／2.043.9，D3）"):src.index("_n = len(software_inventory.list_software(conn))")]
    assert "FROM host_package WHERE gone_at IS NULL" in seg and "LEFT JOIN hardware" not in seg
    assert "_live_rows" not in seg and "_current_by_machine" not in seg
    assert "system_stats.machine_key(" in seg and "RETIRED_STATUS" in seg


def test_同一個request只讀一次表():
    src = (ROOT / "APP" / "asset-module" / "backend" / "routers" / "assets.py").read_text(encoding="utf-8")
    seg = src[src.index('@router.get("/api/software/packages")'):][:900]
    assert "live = software_inventory._live_rows(conn)" in seg and "live=live" in seg
