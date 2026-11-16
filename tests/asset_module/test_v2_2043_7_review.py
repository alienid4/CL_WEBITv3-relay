"""2.043.7：GPT 審查 2.043.6 的 6 條（臨時門檻格子＝下鑽、依主機用 machine_key、OS 下鑽也吃機房／環境、
依主機只列有現行套件、無合約資料子清單、儲存頁新頁籤合計跟著機房）。全部假資料、IP 用 10.99.x。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
BE = ROOT / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import machine_identity  # noqa: E402
import software_inventory as si  # noqa: E402
import v2_hosts  # noqa: E402
from test_v2_batch1 import _conn, _seed  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_mode():
    yield
    machine_identity._STATE.update({"mode": machine_identity.MODE_LEGACY, "pending": frozenset(), "host_fix": {}})


def _pkg(c, ip, serial, name, version="1", gone=False):
    c.execute("INSERT INTO host_package (ip, asset_serial, name, version, arch, source, first_seen, last_seen, gone_at) "
              "VALUES (?, ?, ?, ?, 'x86_64', 'ssh_rpm', '2026-10-01 00:00', ?, ?)",
              (ip, serial, name, version, f"2026-10-0{2 if ip.endswith('.2') else 1} 00:00", "2026-10-03" if gone else None))


def test_2_4_依主機用machine_key分組_只列有現行套件(tmp_path):
    c = _conn(tmp_path)
    try:
        db.set_setting(c, "machine_key_mode", "hostname")
        db.insert_hardware(c, asset_serial="S1", hostname="web01", ip="10.99.5.1", os="RHEL 9")
        db.insert_hardware(c, asset_serial="S1b", hostname="web01", ip="10.99.5.2", os="RHEL 9")   # 同一台換了 IP 的第二筆登記
        db.insert_hardware(c, asset_serial="S2", hostname="db01", ip="10.99.6.1", os="RHEL 9")
        c.commit()
        machine_identity.refresh(c)
        _pkg(c, "10.99.5.1", "S1", "openssl", "3.0")
        _pkg(c, "10.99.5.2", "S1b", "openssl", "3.0")      # 同一台兩個 IP 都回報同一個套件 → 只算一次
        _pkg(c, "10.99.5.2", "S1b", "bash", "5.1")
        _pkg(c, "10.99.6.1", "S2", "zsh", "5", gone=True)   # 全部移除 → 不列
        c.commit()
        rows = si.machines_overview(c)
        assert len(rows) == 1, "web01 兩個 IP 是同一台；db01 沒有現行套件不列"
        r = rows[0]
        assert r["hostname"] == "web01" and set(r["ips"]) == {"10.99.5.1", "10.99.5.2"} and r["ip"] == "10.99.5.2"
        assert r["package_count"] == 2
        assert [p["name"] for p in si.machine_packages(c, r["key"])] == ["bash", "openssl"], "下鑽＝列上的套件數"
        assert si.machine_packages(c, "nope") == []
    finally:
        c.close()


def test_2043_8_退役不列_頁首台數同一份(tmp_path):
    """退役＝備查，不計入任何台數（RETIRED_STATUS 正典）；頁首 hosts＝依主機列數。"""
    import system_stats
    from lifecycle import RETIRED_STATUS
    c = _conn(tmp_path)
    try:
        db.insert_hardware(c, asset_serial="L1", hostname="app01", ip="10.99.7.1", os="RHEL 9")
        db.insert_hardware(c, asset_serial="R1", hostname="old01", ip="10.99.7.2", os="RHEL 9",
                           asset_status=sorted(RETIRED_STATUS)[0])
        c.commit()
        _pkg(c, "10.99.7.1", "L1", "openssl")
        _pkg(c, "10.99.7.2", "R1", "openssl")
        _pkg(c, "10.99.7.9", None, "curl")          # 沒有登記的 IP：不是退役，照列
        c.commit()
        rows = si.machines_overview(c)
        keys = {r["key"] for r in rows}
        assert system_stats.machine_key("old01", "10.99.7.2", "R1") not in keys, "退役那台不列"
        assert keys == {system_stats.machine_key("app01", "10.99.7.1", "L1"), system_stats.machine_key(None, "10.99.7.9", None)}
        s = si.software_summary(c)
        assert s["hosts"] == len(rows) == 2, "頁首台數＝依主機列數（同一份計算）"
        assert s["hosts_ip"] == 3, "舊版頁用的 IP 數另外給"
        assert si.machine_packages(c, system_stats.machine_key("old01", "10.99.7.2", "R1")) == []
    finally:
        c.close()


def test_2043_8_分組鍵就是machine_key_同IP不同主機名是兩台(tmp_path):
    """擋住「另寫一套去重」：每列的 key 必須逐字等於 system_stats.machine_key() 的回傳值。
    同一個 IP 先後給兩台不同主機名的資產 → 兩台；若有人改回依 IP 分組（或自己正規化主機名）就會紅。"""
    import system_stats
    c = _conn(tmp_path)
    try:
        db.set_setting(c, "machine_key_mode", "hostname")
        db.insert_hardware(c, asset_serial="X1", hostname="alpha01", ip="10.99.8.1", os="RHEL 9")
        db.insert_hardware(c, asset_serial="X2", hostname="beta01", ip="10.99.8.1", os="RHEL 9")   # 同 IP、不同主機名
        c.commit()
        machine_identity.refresh(c)
        c.execute("INSERT INTO host_package (ip, asset_serial, name, version, arch, source, last_seen) "
                  "VALUES ('10.99.8.1', 'X1', 'vim', '9', 'x86_64', 'ssh_rpm', '2026-10-01 00:00')")
        c.execute("INSERT INTO host_package (ip, asset_serial, name, version, arch, source, last_seen) "
                  "VALUES ('10.99.8.1', 'X2', 'nano', '7', 'x86_64', 'ssh_rpm', '2026-10-01 00:00')")
        c.commit()
        rows = si.machines_overview(c)
        want = {system_stats.machine_key("alpha01", "10.99.8.1", "X1"): ["vim"],
                system_stats.machine_key("beta01", "10.99.8.1", "X2"): ["nano"]}
        assert len(want) == 2, "前提：machine_key 本身把這兩台分開"
        assert {r["key"]: [p["name"] for p in si.machine_packages(c, r["key"])] for r in rows} == want
        assert si.software_summary(c)["hosts"] == 2 and si.software_summary(c)["hosts_ip"] == 1
    finally:
        c.close()


def test_2_依主機只呼叫machine_key_不自寫IP去重():
    src = (BE / "software_inventory.py").read_text(encoding="utf-8")
    # 2.043.9 起分組改從 _live_rows（那裡呼叫 machine_key、排除退役），_current_by_machine 只用它的 mkey
    body = src[src.index("def _current_by_machine"):src.index("def machines_overview")]
    assert 'r["mkey"]' in body and "GROUP BY" not in body and "_live_rows(" in body
    lr = src[src.index("def _live_rows"):src.index("def list_software")]
    assert "system_stats.machine_key(" in lr and "RETIRED_STATUS" in lr
    v = (FE / "pages" / "v2" / "inventory" / "software.vue").read_text(encoding="utf-8")
    assert "/api/software/by-machine" in v and "/api/software/by-host" not in v
    assert "有現行套件的主機 <b>{{ bhRows.length }}</b>" in v, "標題台數＝列數"


def test_3_OS下鑽也套機房環境多筆(tmp_path):
    c = _conn(tmp_path)
    try:
        _seed(c)
        base = v2_hosts.listing(c, "all", os_source="facts", size=0)
        site = next(s for s in base["facets"]["sites"] if "甲機房" in s)
        only = v2_hosts.listing(c, "all", os_source="facts", sites=site, size=0)
        assert only["filter_total"] == len(only["filter_rows"]) < base["filter_total"]
        assert {r["hostname"] for r in only["filter_rows"]} == {"aix01"}
        assert [r["no"] for r in only["filter_rows"]] == list(range(1, len(only["filter_rows"]) + 1))
        multi = v2_hosts.listing(c, "all", os_source="facts", multi=True, size=0)
        assert {r["hostname"] for r in multi["filter_rows"]} == {"aix01"} and multi["filter_total"] == 2, "那台的兩筆登記都列"
    finally:
        c.close()


def test_5_無合約資料子清單不受has_data限制():
    v = (FE / "pages" / "v2" / "risk.vue").read_text(encoding="utf-8")
    seg = v[v.index("tab === 'contract'"):v.index("tab === 'hk'")]
    assert '<div class="chips">' in seg, "chips 不包在 has_data 裡"
    assert '<V2Table v-if="view === \'none\'" :rows="ctNoneRows"' in seg
    assert 'prefix="W"empty' not in v


def test_1_6_儲存頁_臨時門檻格子與頁首合計():
    v = (FE / "pages" / "v2" / "storage.vue").read_text(encoding="utf-8")
    # 2026-11-14：臨時 % 門檻拿掉，格子＝後端 near_full_count（跟容量預警同一把尺 lun_low）
    assert "near_full_count: r.near_full_count," in v
    assert "physTot" in v and "gb(physTot.total)" in v and "data.physical?.total_gb" not in v
    assert "size(exCap)" in v and "excluded_capacity_mib" not in v
