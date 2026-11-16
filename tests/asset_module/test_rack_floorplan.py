"""4-1 平面圖：機器類型（不明不猜）、系統等級、全部 IP 對網段、U 位重疊、多詞組合搜尋。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import rack_layout as rl  # noqa: E402


@pytest.mark.parametrize("model,oses,sl,esxi,san,want", [
    ("Oracle Exadata X7", ["Oracle Linux 7.9"], None, False, False, "oracle"),
    ("ODA X7-2 HA", [], None, False, False, "oracle"),
    ("Something", [], {"rack_u": 42}, False, False, "oracle"),          # 型號規格 system_level
    ("DELL R750", ["VMWare ESXi 7.0.3"], None, False, False, "esxi"),
    ("DELL R750", [], None, True, False, "esxi"),                       # CI 圖的 ESXi 節點
    ("IBM S922", ["AIX 7.2"], None, False, False, "power"),
    ("IBM S922", [], None, False, False, "power"),                      # 沒寫 OS 也認得 Power 型號
    ("IBM AS400", [], None, False, False, "power"),
    ("HPE DL380 Gen10", ["Windows Server 2019"], None, False, False, "windows"),
    ("Dell R660", ["RedHat 8.10"], None, False, False, "linux"),
    ("IBM San Switch", ["N/A"], None, False, False, "san"),
    ("EMC Storage DS300B", ["N/A"], None, False, False, "san"),         # Brocade DS-300B 是 SAN 交換器
    ("Whatever", [], None, False, True, "san"),                         # san_switch 表有這個 IP
    ("IBM FlashSystem 5100", ["N/A"], None, False, False, "storage"),
    ("EMC UnityXT480F", [], None, False, False, "storage"),
    ("Cisco C9200L-48P-4X", ["17.03.04b"], None, False, False, "network"),
    ("Fortinet FortiGate 101E", [], None, False, False, "network"),
    ("X", ["網路設備"], None, False, False, "network"),
    ("VoiceGateway", [], None, False, False, "unknown"),                # 認不出就是不明
    (None, [], None, False, False, "unknown"),
])
def test_機器類型_認不出就不明(model, oses, sl, esxi, san, want):
    assert rl.classify_machine_type(model, oses, sl, is_esxi=esxi, is_san=san) == want


def test_系統等級_取最高():
    assert rl.sys_class_level("S1-第一類系統軟體（核心）") == "core"
    assert rl.sys_class_level("S1-第一類系統軟體") == "core"
    assert rl.sys_class_level("S2-第二類系統軟體") == "noncore"
    assert rl.sys_class_level(None) == "unclassified"
    lv = {"A": "noncore", "B": "core"}
    assert rl.machine_sys_level(["A", "B"], lv) == "core"
    assert rl.machine_sys_level(["A", "X"], lv) == "noncore"
    assert rl.machine_sys_level([], lv) == "unclassified"


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _seed(conn):
    def hw(serial, host, ip, rack, model, os=None, env=None, api=None):
        db.insert_hardware(conn, asset_serial=serial, hostname=host, ip=ip, physical_location="01_甲機房",
                           rack_no=rack, device_model=model, os=os, environment=env, api_id=api)
    hw("A-1", "aix01", "10.50.1.1", "B10/21-22U", "IBM S922", "AIX 7.2", "正式")
    hw("A-2", "aix02t", "10.50.2.2", "B10/21-22U", "IBM S1022", "AIX 7.2", "測試")
    hw("A-3", "uatp1", "10.50.2.3", "B11/5U", "IBM S922", "AIX 7.2", "測試")
    hw("A-4", "w1", "10.50.1.4", "B11/10U", "DELL R750", "Windows Server 2019", "正式")
    conn.execute("INSERT INTO network_segment (cidr, raw_cidr, net_start, net_end, purpose_desc, environment, "
                 "environment_raw, vlan) VALUES ('10.50.1.0/24','10.50.1.0/24',?,?,'正式 AP 段','正式','PROD','101')",
                 (int.from_bytes(bytes([10, 50, 1, 0]), "big"), int.from_bytes(bytes([10, 50, 1, 255]), "big")))
    conn.execute("INSERT INTO network_segment (cidr, raw_cidr, net_start, net_end, purpose_desc, environment, "
                 "environment_raw, vlan) VALUES ('10.50.2.0/24','10.50.2.0/24',?,?,'UAT 段','測試','UAT','202')",
                 (int.from_bytes(bytes([10, 50, 2, 0]), "big"), int.from_bytes(bytes([10, 50, 2, 255]), "big")))
    conn.execute("INSERT INTO asset_nic (asset_serial, nic_name, ip) VALUES ('A-1','en1','10.50.2.99')")
    conn.commit()


def test_全部IP對網段_重疊_類型(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        ms = {m["name"]: m for m in rl.machines(conn)}
        f = ms["aix01"]
        assert [(x["ip"], x["cidr"]) for x in f["ips"]] == [("10.50.1.1", "10.50.1.0/24"), ("10.50.2.99", "10.50.2.0/24")]
        assert f["machine_type"] == "power" and ms["w1"]["machine_type"] == "windows"
        d = rl.rack_detail(conn, "甲機房", "B10")
        ov = {x["name"]: x["overlap_with"] for x in d["devices"]}
        assert ov["aix01"] and ov["aix02t"]                      # 21-22U 兩台重疊
        tiles = {t["rack"]: t for r in rl.datacenter(conn)["rooms"] for t in r["racks"]}
        assert tiles["B10"]["overlap_count"] == 2 and tiles["B11"]["overlap_count"] == 0
    finally:
        conn.close()


def test_多詞搜尋_同欄位OR_不同欄位AND_找不到不吃掉結果(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        r = rl.search(conn, "S922 S1022")                       # 同欄位（機型）→ OR
        assert r["mode"] == "multi"
        assert {m["name"] for m in r["angles"]["hardware"]["matches"]} == {"aix01", "aix02t", "uatp1"}
        r = rl.search(conn, "uat S922")                         # 環境 AND 機型
        assert {m["name"] for m in r["angles"]["hardware"]["matches"]} == {"aix01", "uatp1"}  # aix01 有一張網卡在 UAT 段
        t = {x["term"]: x for x in r["multi"]["terms"]}
        assert t["uat"]["field"] == "env" and t["S922"]["field"] == "model"
        r = rl.search(conn, "S1022 zzzz9")                      # 找不到的詞列出來，不讓結果變空
        assert r["multi"]["unknown"] == ["zzzz9"]
        assert {m["name"] for m in r["angles"]["hardware"]["matches"]} == {"aix02t"}
        r = rl.search(conn, "IBM S922")                         # 整串找得到 → 單詞行為不變
        assert "mode" not in r and r["counts"]["hardware"] == 2
    finally:
        conn.close()
