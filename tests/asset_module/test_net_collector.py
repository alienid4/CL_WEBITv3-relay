"""net_collector：NIC/HBA 解析與降速判定、store upsert。2026-09-14 機房搬遷盤點。"""
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import db as D          # noqa: E402
import net_collector as N  # noqa: E402
import san_collector as S  # noqa: E402
import json  # noqa: E402


def _runner(mapping):
    def run(host, cmd):
        for key, out in mapping.items():
            if key in cmd:
                return out
        return ""
    return run


def test_nic_downgrade_and_vlan():
    """10G 卡跑 1G 要標 downgraded；VLAN 子介面要抓到 id。"""
    mapping = {
        "ip -d -o link": (
            "2: eth0: <UP> mtu 1500 state UP link/ether 00:11:22:33:44:55 brd ff:ff:ff:ff:ff:ff\n"
            "5: eth0.100@eth0: <UP> state UP link/ether 00:11:22:33:44:55 brd ff:ff:ff:ff:ff:ff "
            "vlan protocol 802.1Q id 100 <REORDER_HDR>"
        ),
        "ip -o -4 addr": "2: eth0    inet 10.99.18.16/24 brd 10.99.18.255 scope global eth0",
        "route": "default via 10.99.18.1 dev eth0",
        "grep": "/sys/class/net/eth0/speed:1000",
        "ethtool": "@@IF eth0\n\tSupported link modes:   1000baseT/Full\n\t                        10000baseT/Full\n",
        "fc_host": "",
    }
    res = N.collect_net(_runner(mapping), "h", "linux")
    nics = {n["nic_name"]: n for n in res["nics"]}
    assert nics["eth0"]["speed_cur"] == 1000
    assert nics["eth0"]["speed_max"] == 10000
    assert nics["eth0"]["downgraded"] == 1          # 10G 卡跑 1G
    assert nics["eth0"]["gateway"] == "10.99.18.1"
    assert nics["eth0.100"]["vlan"] == "100"
    assert "lo" not in nics                          # loopback 略過


def test_hba_wwpn_and_downgrade():
    mapping = {
        "fc_host": ("@@FC host0\nport_name=0x10000000c9abcdef\nnode_name=0x20000000c9abcdef\n"
                    "speed=8 Gbit\nsupported_speeds=8 Gbit, 16 Gbit\nport_state=Online"),
    }
    res = N.collect_net(_runner(mapping), "h", "linux")
    hba = res["hbas"][0]
    assert hba["wwpn"] == "10:00:00:00:c9:ab:cd:ef"
    assert hba["speed_cur"] == 8 and hba["speed_max"] == 16
    assert hba["downgraded"] == 1                    # 16G 卡跑 8G


def test_store_upsert_dedup(tmp_path):
    D.init_db(tmp_path / "t.db")
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.row_factory = sqlite3.Row
    res = {"nics": [{"nic_name": "eth0", "mac": "aa", "speed_cur": 1000, "speed_max": 10000,
                     "downgraded": 1, "state": "up"}],
           "hbas": [{"hba_name": "host0", "wwpn": "10:00", "speed_cur": 8, "speed_max": 16,
                     "downgraded": 1, "port_state": "Online"}]}
    N.store_net(conn, "HW-1", res)
    N.store_net(conn, "HW-1", res)                   # 重收
    assert conn.execute("SELECT COUNT(*) FROM asset_nic WHERE asset_serial='HW-1'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM asset_hba WHERE asset_serial='HW-1'").fetchone()[0] == 1
    row = conn.execute("SELECT downgraded FROM asset_nic WHERE nic_name='eth0'").fetchone()
    assert row["downgraded"] == 1


def test_reconcile_hba_san(tmp_path):
    """主機 HBA 的 WWPN → 用已收 SAN 資料反查補 switch/port/zone/target。"""
    D.init_db(tmp_path / "t.db")
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.row_factory = sqlite3.Row
    data = {"switch": "SAN_SW_A", "rows": [
        {"wwpn": "10:00:00:00:c9:ab:cd:ef", "port": "12", "zones": ["Z_HOSTA_STG"]},
        {"wwpn": "50:06:01:60:aa:bb:cc:dd", "port": "3", "zones": ["Z_HOSTA_STG"]},
    ]}
    conn.execute("INSERT INTO san_switch(ip,switch_name,data_json,vendor,collected_at) "
                 "VALUES('1.1.1.1','SAN_SW_A',?,'brocade',datetime('now'))", (json.dumps(data),))
    conn.execute("INSERT INTO asset_hba(asset_serial,hba_name,wwpn,sources) "
                 "VALUES('HW-9','host0','10:00:00:00:c9:ab:cd:ef','{}')")
    conn.commit()
    assert S.reconcile_hba_san(conn)["matched"] == 1
    r = conn.execute("SELECT san_switch,san_port,zone,target_wwpn FROM asset_hba WHERE asset_serial='HW-9'").fetchone()
    assert r["san_switch"] == "SAN_SW_A" and r["san_port"] == "12"
    assert r["zone"] == "Z_HOSTA_STG"
    assert "50060160aabbccdd" in r["target_wwpn"]        # 同 zone 的儲存端＝target
