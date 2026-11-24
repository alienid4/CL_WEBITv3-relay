"""管理卡（iDRAC／OOB）併進主機面板：只改顯示，台數不動、不算位置重疊。全部假資料。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import rack_layout as rl  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _hw(conn, serial, host, ip, rack, model=None, os=None, hw_serial=None, name=None):
    db.insert_hardware(conn, asset_serial=serial, hostname=host, ip=ip, physical_location="01_甲機房",
                       rack_no=rack, device_model=model, os=os, hw_serial=hw_serial, asset_name=name)


def test_規則_單位():
    assert rl.is_mgmt_record({"name": "APPOOB12-001-ABC1234"})
    assert rl.is_mgmt_record({"name": "x", "os": "iDRAC9"})
    assert rl.is_mgmt_record({"name": "x", "asset_names": ["硬體遠端管理"]})
    assert not rl.is_mgmt_record({"name": "esx01", "os": "VMware ESXi 8"})
    assert not rl.is_mgmt_record({"name": "silo01", "os": "Linux"})          # 「ilo」要整個字才算


def test_同櫃同U併成管理卡_不算重疊_台數不變(tmp_path):
    conn = _conn(tmp_path)
    try:
        _hw(conn, "H-1", "esx01", "10.99.7.1", "M11/30-31U", "DELL R750", "VMware ESXi 8", hw_serial="ABC1234")
        _hw(conn, "O-1", "APPOOB12-001-ABC1234", "10.99.8.1", "M11/30-31U", "DELL R750")   # 服務標籤對得上
        _hw(conn, "H-2", "esx02", "10.99.7.2", "M11/20-21U", "DELL R750", "VMware ESXi 8", hw_serial="DEF5678")
        _hw(conn, "O-2", "mgmt-esx02", "10.99.8.2", "M11/20-21U", "DELL R750", os="iDRAC9")  # 只有一台同 U → 併
        _hw(conn, "H-3", "db01", "10.99.7.3", "M11/10U", "X")
        _hw(conn, "H-4", "db02", "10.99.7.4", "M11/10U", "X")
        _hw(conn, "O-3", "APPOOB12-003-ZZZ0000", "10.99.8.3", "M11/10U", "X")             # 兩台同 U、標籤對不到 → 不併
        conn.commit()
        d = rl.rack_detail(conn, "甲機房", "M11")
        by = {x["name"]: x for x in d["devices"]}
        assert by["APPOOB12-001-ABC1234"]["mgmt_of"] == by["esx01"]["key"]
        assert by["esx01"]["mgmt"] == [{"key": by["APPOOB12-001-ABC1234"]["key"], "name": "APPOOB12-001-ABC1234",
                                        "ip": "10.99.8.1", "confirmed": True, "kind": "OOB", "rule": "tag"}]
        assert by["mgmt-esx02"]["mgmt_of"] == by["esx02"]["key"] and by["esx02"]["mgmt"][0]["confirmed"] is False
        assert by["APPOOB12-003-ZZZ0000"]["mgmt_of"] is None
        assert by["esx01"]["overlap_with"] == [] and by["esx02"]["overlap_with"] == []
        assert len(d["devices"]) == 7                                                    # 台數不變
        tile = next(t for r in rl.datacenter(conn)["rooms"] for t in r["racks"] if t["rack"] == "M11")
        # 只剩 10U 那三台算重疊；2026-10-26 起台數＝箱子：併進主機的 2 個管理口不算台，找不到本體的照舊算一台
        assert tile["overlap_count"] == 3 and tile["device_count"] == 5 and tile["record_count"] == 7
        assert tile["breakdown"] == {"box": 4, "suspect": 0, "member": 0, "port": 2, "orphan": 1}
        assert by["APPOOB12-003-ZZZ0000"]["orphan_port"] is True
    finally:
        conn.close()
