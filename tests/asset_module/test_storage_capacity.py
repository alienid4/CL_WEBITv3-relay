"""儲存容量：同一卷宗只算一次、宣告不可信的卷宗排除、總量＝下鑽、依系統、快照、每日快照。"""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import storage_capacity as sc  # noqa: E402

TIB = 1024 * 1024


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _extra(conn, sheet, key, rec, at="2026-10-01 09:00:00"):
    conn.execute("INSERT INTO source_record (source, source_key, payload, resolved_status, collected_at) "
                 "VALUES (?,?,?,'extra',?)", (f"vcenter_extra:{sheet}", key, json.dumps(rec, ensure_ascii=False), at))


def _ds(name, url, vc, cap, used, prov, vms, typ="VMFS", hosts=2):
    return {"Name": name, "URL": url, "VI SDK Server": vc, "Type": typ, "Capacity MiB": cap, "In Use MiB": used,
            "Provisioned MiB": prov, "Free MiB": cap - used, "# VMs": vms, "# Hosts": hosts, "Object ID": f"datastore-{name}"}


def _seed(conn):
    # 同一個 NFS 在兩個 vCenter 各一列 → 算一次
    _extra(conn, "vDatastore", "nfs#0", _ds("NAS1", "ds:///vmfs/volumes/aaa/", "10.99.0.1", 10 * TIB, 4 * TIB, 5 * TIB, 3, "NFS41"))
    _extra(conn, "vDatastore", "nfs#1", _ds("NAS1", "ds:///vmfs/volumes/aaa/", "10.99.0.2", 10 * TIB, 4 * TIB, 5 * TIB, 2, "NFS41"))
    _extra(conn, "vDatastore", "lun#2", _ds("LUN1", "ds:///vmfs/volumes/bbb/", "10.99.0.1", 2 * TIB, 1.9 * TIB, 3 * TIB, 5))
    # 宣告 500 TiB、完全沒在用 → 排除
    _extra(conn, "vDatastore", "ma#3", _ds("ma-ds-1234", "ds:///vmfs/volumes/ccc/", "10.99.0.1", 500 * TIB, 0, 0, 0))
    # 系統對應：vm1 → N-970，vm2 對不到
    db.insert_hardware(conn, asset_serial="S-1", hostname="vm1", ip="10.99.6.1", api_id="N-970", asset_name="儲存系統")
    conn.execute("INSERT INTO source_record (source, source_key, payload, resolved_status, collected_at) "
                 "VALUES ('vcenter','u-1',?, 'ambiguous', '2026-10-01 09:00:00')",
                 (json.dumps({"vm_uuid": "u-1", "hostname": "vm1", "ip": "10.99.6.1", "vm_name": "vm1"}),))
    _extra(conn, "vDisk", "u-1#0", {"VM": "vm1", "VM UUID": "u-1", "Capacity MiB": 100})
    _extra(conn, "vDisk", "u-1#1", {"VM": "vm1", "VM UUID": "u-1", "Capacity MiB": 50})
    _extra(conn, "vDisk", "u-2#2", {"VM": "vm2", "VM UUID": "u-2", "Capacity MiB": 30})
    _extra(conn, "vSnapshot", "u-1#3", {"VM": "vm1", "VM UUID": "u-1", "Name": "before patch",
                                       "Date / time": "2026-09-01 10:00:00", "Size MiB (total)": 2048})
    conn.execute("INSERT INTO host_spec (asset_serial, ip, storage_json, collected_at) VALUES (?,?,?,?)",
                 ("S-1", "10.99.6.1", json.dumps({"by": "vg", "items": [{"name": "rhel", "size_gb": 100, "free_gb": 20}],
                                                   "total_gb": 100}), "2026-10-01"))
    conn.commit()


def test_同卷宗只算一次_宣告不可信的排除_總量等於下鑽(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        d = sc.datastores(conn)
        assert d["raw_rows"] == 4 and d["dup_rows"] == 1
        assert d["volume_count"] == 2 and d["excluded_count"] == 1
        assert d["excluded"][0]["name"] == "ma-ds-1234"
        assert d["totals"]["capacity_mib"] == 12 * TIB                     # 10 + 2，不是 22 也不是 522
        assert d["totals"]["capacity_mib"] == sum(v["capacity_mib"] for v in d["volumes"])
        nas = next(v for v in d["volumes"] if v["name"] == "NAS1")
        assert nas["vcenters"] == ["10.99.0.1", "10.99.0.2"] and nas["vms"] == 5 and nas["rows"] == 2
        assert d["volumes"][0]["name"] == "LUN1"                           # 快滿的排前面
        lun = d["volumes"][0]
        assert lun["free_pct"] == 5.0 and lun["over_ratio"] == 1.5
        assert d["threshold_pct"] == 15
    finally:
        conn.close()


def test_沒有名稱前綴就不排除_不猜(tmp_path):
    conn = _conn(tmp_path)
    try:
        _extra(conn, "vDatastore", "x#0", _ds("EMPTY1", "ds:///vmfs/volumes/ddd/", "10.99.0.1", 50 * TIB, 0, 0, 0))
        conn.commit()
        assert sc.datastores(conn)["excluded_count"] == 0
    finally:
        conn.close()


def test_依系統_快照_實體主機(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        bs = sc.by_system(conn)
        rows = {r["api_id"]: r for r in bs["rows"]}
        assert rows["N-970"]["capacity_mib"] == 150 and rows["N-970"]["vms"] == ["vm1"]
        assert rows[None]["name"] == "未對應系統" and rows[None]["capacity_mib"] == 30
        assert sum(r["capacity_mib"] for r in bs["rows"]) == bs["total_mib"] == 180
        sn = sc.snapshots(conn, now=datetime(2026, 10, 1, 12))
        assert sn["rows"][0]["age_days"] == 30 and sn["rows"][0]["api_ids"] == ["N-970"]
        ph = sc.physical(conn)
        assert ph["rows"][0]["free_pct"] == 20.0 and ph["total_gb"] == 100
    finally:
        conn.close()


def test_每日快照_同一天只存一次_趨勢天數(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        assert sc.record_snapshot(conn) == 2
        assert sc.record_snapshot(conn) == 0
        t = sc.trend(conn)
        assert t["days"] == 1 and t["points"][0]["date"] == "2026-10-01"
        o = sc.overview(conn)
        assert o["trend"]["days"] == 1 and o["datastores"]["volume_count"] == 2
    finally:
        conn.close()



def _seed_rooms(conn):
    """ESXi：e1、e2 在甲機房；e3 在乙機房；e9 沒登記。卷宗：
    L-A 只掛 e1、e2（甲）；L-X 掛 e1、e3（跨機房）；L-U 只掛 e9（不明）；L-P 掛 e2 與 10.99.5.9（IP 對到甲）。"""
    for serial, host, ip, loc in (("E-1", "esxi-e1", "10.99.5.1", "01_甲機房"), ("E-2", "esxi-e2", "10.99.5.2", "01_甲機房"),
                                  ("E-3", "esxi-e3", "10.99.5.3", "02_乙機房"), ("E-4", "esxi-e4", "10.99.5.9", "01_甲機房")):
        db.insert_hardware(conn, asset_serial=serial, hostname=host, ip=ip, physical_location=loc)
    vols = [("L-A", "aaa", ["esxi-e1.example.local", "esxi-e2.example.local"], 10, 9.5),
            ("L-X", "xxx", ["esxi-e1.example.local", "esxi-e3.example.local"], 4, 1),
            ("L-U", "uuu", ["esxi-e9.example.local"], 2, 1),
            ("L-P", "ppp", ["esxi-e2.example.local", "10.99.5.9"], 1, 0.5)]
    for name, uid, hosts, cap, used in vols:
        rec = _ds(name, f"ds:///vmfs/volumes/{uid}/", "10.99.0.1", cap * TIB, used * TIB, cap * TIB, 1)
        rec["Hosts"] = ", ".join(hosts)
        _extra(conn, "vDatastore", f"{name}#0", rec)
    _extra(conn, "vDisk", "u-9#0", {"VM": "vm9", "VM UUID": "u-9", "Capacity MiB": 70, "Path": "[L-X] vm9/vm9.vmdk"})
    _extra(conn, "vDisk", "u-9#1", {"VM": "vm9", "VM UUID": "u-9", "Capacity MiB": 30, "Path": "[L-A] vm9/vm9_1.vmdk"})
    _extra(conn, "vSnapshot", "u-9#2", {"VM": "vm9", "VM UUID": "u-9", "Name": "s1", "Date / time": "2026-10-01 00:00:00",
                                       "Filename": "[L-A] vm9/vm9-Snapshot1.vmsn", "Size MiB (total)": 10})
    conn.commit()


def test_分機房_同一間_跨機房_不明_加總等於總量(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed_rooms(conn)
        o = sc.overview(conn)
        vols = {v["name"]: v for v in o["datastores"]["volumes"]}
        assert vols["L-A"]["room"] == "甲機房"
        assert vols["L-X"]["room"] == sc.ROOM_CROSS and vols["L-X"]["rooms"] == ["乙機房", "甲機房"]
        assert vols["L-U"]["room"] == sc.ROOM_UNKNOWN and vols["L-U"]["hosts_unresolved"] == ["esxi-e9.example.local"]
        assert vols["L-P"]["room"] == "甲機房"                                   # IP 也對得到
        rooms = {r["room"]: r for r in o["rooms"]}
        assert rooms["甲機房"]["volume_count"] == 2 and rooms["甲機房"]["capacity_mib"] == 11 * TIB
        assert rooms["甲機房"]["near_full_count"] == 1                           # L-A 剩 5%
        assert rooms["甲機房"]["snapshot_count"] == 1
        assert sum(r["capacity_mib"] for r in o["rooms"]) == o["datastores"]["totals"]["capacity_mib"]
        assert sum(r["volume_count"] for r in o["rooms"]) == o["datastores"]["volume_count"]
        bs = o["by_system"]
        assert bs["total_by_room"] == {sc.ROOM_CROSS: 70, "甲機房": 30}           # 同一台 VM 的磁碟各算各的機房
        assert sum(bs["total_by_room"].values()) == bs["total_mib"]
        assert [r["room"] for r in o["rooms"]][-2:] == [sc.ROOM_CROSS, sc.ROOM_UNKNOWN]
        summ = {r["room"]: r for r in sc.room_summary(conn)}
        assert summ["甲機房"]["capacity_mib"] == 11 * TIB
    finally:
        conn.close()
