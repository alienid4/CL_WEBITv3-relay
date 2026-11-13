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
        assert rooms["甲機房"]["near_full_count"] == 2           # L-A、L-P 都剩 512 GB（< 600 GB，1.5 每日檢核同一把尺）
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


def test_檔案系統剩餘查不到_不當0也不告警_codex429(tmp_path):
    """Windows 磁碟剩餘沒收到（free_gb=None）原本被當 0 → 0% → 列容量預警。"""
    conn = _conn(tmp_path)
    try:
        for serial, ip, items in (
            ("W-UNK", "10.99.7.1", [{"name": "C:", "size_gb": 100, "free_gb": None}]),
            ("W-ZERO", "10.99.7.2", [{"name": "C:", "size_gb": 100, "free_gb": 0}]),
            ("W-OK", "10.99.7.3", [{"name": "C:", "size_gb": 100, "free_gb": 20}]),
            ("W-MIX", "10.99.7.4", [{"name": "C:", "size_gb": 100, "free_gb": 2}, {"name": "D:", "size_gb": 100, "free_gb": None}]),
        ):
            db.insert_hardware(conn, asset_serial=serial, hostname=serial.lower(), ip=ip)
            conn.execute("INSERT INTO host_spec (asset_serial, ip, storage_json, collected_at) VALUES (?,?,?,?)",
                         (serial, ip, json.dumps({"by": "mount", "items": items, "total_gb": None}), "2026-11-01"))
        conn.commit()
        ph = sc.physical(conn)
        rows = {r["asset_serial"]: r for r in ph["rows"]}
        assert rows["W-UNK"]["free_gb"] is None and rows["W-UNK"]["free_pct"] is None
        assert rows["W-UNK"]["groups"][0]["free_gb"] is None and rows["W-UNK"]["free_unknown"] == 1
        assert rows["W-ZERO"]["free_pct"] == 0          # 真的 0 照樣是 0
        assert rows["W-OK"]["free_pct"] == 20
        # 部分未知：不判這台（free_pct=None、不告警）；查得到那部分另給 free_pct_known 參考
        assert rows["W-MIX"]["free_pct"] is None and rows["W-MIX"]["free_unknown"] == 1
        assert rows["W-MIX"]["free_pct_known"] == 2 and rows["W-MIX"]["free_gb"] == 2
        ds = sc.datastores(conn)
        al = sc.alerts(ds, ph)
        assert {p["asset_serial"] for p in al["physical"]} == {"W-ZERO", "W-OK"}     # 2.044.0 R17：剩 20%＝用 80.0% 列「處理」
        assert {p["asset_serial"]: p["level_label"] for p in al["physical"]} == {"W-ZERO": "高", "W-OK": "處理"}
        assert al["physical_uncollected"] == 2                 # 查不到剩餘的另計未收集，不列進預警
        # 加總：只加查得到的；未知台數往上帶
        assert ph["free_gb"] == 22 and ph["free_unknown_hosts"] == 2
        assert ph["by_kind"]["mount"]["free_gb"] == 22 and ph["by_kind"]["mount"]["free_unknown_hosts"] == 2
        rooms = sc.rooms(ds, None, ph)
        assert sum(r["phys_free_gb"] or 0 for r in rooms) == 22
        assert sum(r["phys_free_unknown"] for r in rooms) == 2
    finally:
        conn.close()


def test_全部剩餘都查不到_加總是未知不是0_codex429(tmp_path):
    conn = _conn(tmp_path)
    try:
        db.insert_hardware(conn, asset_serial="W-UNK", hostname="w-unk", ip="10.99.7.1")
        conn.execute("INSERT INTO host_spec (asset_serial, ip, storage_json, collected_at) VALUES (?,?,?,?)",
                     ("W-UNK", "10.99.7.1", json.dumps({"by": "mount", "items": [{"name": "C:", "size_gb": 100, "free_gb": None}]}),
                      "2026-11-01"))
        conn.commit()
        ph = sc.physical(conn)
        assert ph["free_gb"] is None and ph["by_kind"]["mount"]["free_gb"] is None and ph["free_unknown_hosts"] == 1
        rooms = sc.rooms(sc.datastores(conn), None, ph)
        assert all(r["phys_free_gb"] is None for r in rooms if r["phys_count"])
        assert sc.alerts(sc.datastores(conn), ph)["count"] == 0
    finally:
        conn.close()


# ===== 2026-11-14：Datastore「處理」＝1.5 每日檢核 VMware 段同一把尺；機房前綴推導；資料時間 =====

GIB = 1024


def _vol(conn, name, free_gb, hosts, cap_gb=10000, at="2026-10-01 09:00:00"):
    rec = _ds(name, f"ds:///vmfs/volumes/{name}/", "10.99.0.1", cap_gb * GIB, (cap_gb - free_gb) * GIB, cap_gb * GIB, 1)
    rec["Hosts"] = ", ".join(hosts)
    _extra(conn, "vDatastore", f"{name}#0", rec, at=at)


def test_處理_599GB列_600不列_601不列(tmp_path):
    conn = _conn(tmp_path)
    try:
        for name, free in (("V-599", 599), ("V-600", 600), ("V-601", 601)):
            _vol(conn, name, free, ["esxi-zz"])
        conn.commit()
        ds = sc.datastores(conn)
        al = sc.alerts(ds, {"rows": []})
        assert {v["name"] for v in al["volumes"]} == {"V-599"}
        assert al["volumes"][0]["level_label"] == "處理"
        assert ds["lun_rule"] == {"threshold": 600.0, "unit": "GB", "op": "<", "threshold_gb": 600.0}
        # 跟 1.5 每日檢核同一支判斷（不是另一份複製）
        import daily_check
        assert all(v["lun_low"] == daily_check.lun_low(v["free_gb"], ds["lun_rule"]) for v in ds["volumes"])
        # 1.5 設定改了，5.2 跟著變（同一份設定）
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)",
                     (daily_check.SETTING, json.dumps({"vm_lun_threshold": 600, "vm_lun_op": "<="})))
        conn.commit()
        assert {v["name"] for v in sc.alerts(sc.datastores(conn), {"rows": []})["volumes"]} == {"V-599", "V-600"}
    finally:
        conn.close()


def _seed_prefix(conn):
    for serial, host, ip, loc in (("E-1", "esxi-a1", "10.99.8.1", "01_甲機房"), ("E-2", "esxi-b1", "10.99.8.2", "02_乙機房")):
        db.insert_hardware(conn, asset_serial=serial, hostname=host, ip=ip, physical_location=loc)
    _vol(conn, "AA_F_one-01", 5000, ["esxi-a1"])          # AA：兩個已知都在甲 → 可推導
    _vol(conn, "AA_F_two-02", 5000, ["esxi-a1"])
    _vol(conn, "AA_F_new-03", 100, ["esxi-x9"])           # ESXi 對不到 → 推導甲機房（而且是處理）
    _vol(conn, "BB_F_one-01", 5000, ["esxi-a1"])          # BB：甲、乙各一 → 衝突
    _vol(conn, "BB_F_two-02", 5000, ["esxi-b1"])
    _vol(conn, "BB_F_new-03", 5000, ["esxi-x9"])
    _vol(conn, "CC_F_one-01", 5000, ["esxi-b1"])          # CC：只有 1 個樣本
    _vol(conn, "CC_F_new-02", 5000, ["esxi-x9"])
    _vol(conn, "NOPREFIX", 5000, ["esxi-x9"])             # 沒有「_」
    conn.commit()


def test_機房前綴推導_兩個同意才推_衝突與單一樣本維持不明(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed_prefix(conn)
        o = sc.overview(conn)
        v = {x["name"]: x for x in o["datastores"]["volumes"]}
        assert v["AA_F_new-03"]["room"] == "甲機房" and v["AA_F_new-03"]["room_derived"] is True
        assert "AA" in v["AA_F_new-03"]["room_reason"] and "推導" in v["AA_F_new-03"]["room_reason"]
        assert v["AA_F_one-01"]["room_derived"] is False                     # 對得到的不標推導
        for n in ("BB_F_new-03", "CC_F_new-02", "NOPREFIX"):
            assert v[n]["room"] == sc.ROOM_UNKNOWN and v[n]["room_derived"] is False and v[n]["room_reason"]
        assert "不同機房" in v["BB_F_new-03"]["room_reason"] and "只有 1 個" in v["CC_F_new-02"]["room_reason"]
        assert o["datastores"]["derived_count"] == 1
        # 加總＝下鑽：每個機房桶的卷宗／容量／處理數＝逐卷宗重數
        rooms = {r["room"]: r for r in o["rooms"]}
        assert sum(r["volume_count"] for r in o["rooms"]) == o["datastores"]["volume_count"]
        assert sum(r["capacity_mib"] for r in o["rooms"]) == o["datastores"]["totals"]["capacity_mib"]
        for name, r in rooms.items():
            mine = [x for x in o["datastores"]["volumes"] if x["room"] == name]
            assert sorted(r["volume_ids"]) == sorted(x["id"] for x in mine)
            assert sorted(r["near_full_ids"]) == sorted(x["id"] for x in mine if x["lun_low"])
        assert rooms["甲機房"]["near_full_count"] == 1                        # 推導過去的 AA_F_new-03（剩 100 GB）
        assert {x["name"] for x in o["alerts"]["volumes"]} == {"AA_F_new-03"}
        assert sum(r["near_full_count"] for r in o["rooms"]) == len(o["alerts"]["volumes"])
    finally:
        conn.close()


def test_資料時間_取最新匯入_超過2天標過期(tmp_path):
    conn = _conn(tmp_path)
    try:
        now = datetime.now().replace(microsecond=0)
        fresh = now.isoformat(sep=" ")
        old = (now - sc.timedelta(days=3)).isoformat(sep=" ")
        _vol(conn, "T-NEW", 5000, ["esxi-x9"], at=fresh)
        _vol(conn, "T-OLD", 5000, ["esxi-x9"], at=old)
        conn.commit()
        ds = sc.datastores(conn)
        v = {x["name"]: x for x in ds["volumes"]}
        assert v["T-NEW"]["collected_at"] == fresh and v["T-NEW"]["stale"] is False
        assert v["T-OLD"]["stale"] is True
        assert ds["as_of"] == fresh and ds["stale_days"] == 2
        assert sc.freshness(conn, ds)["stale"] is False
        assert sc.is_stale("2026-11-10 00:00:00", 2, datetime(2026, 11, 12, 0, 0)) is False   # 剛好 2 天不算
        assert sc.is_stale("2026-11-10 00:00:00", 2, datetime(2026, 11, 12, 0, 1)) is True
        assert sc.is_stale(None, 2) is False
    finally:
        conn.close()


# ===== 2026-11-14：VMware Disk（資料存放區）全部列＋到 90% 預估 =====

def _pts(start_pct, per_day, n, last="2026-11-13"):
    end = datetime.fromisoformat(last)
    out = []
    for i in range(n):
        d = end - sc.timedelta(days=n - 1 - i)
        pct = start_pct + per_day * i
        out.append({"date": d.strftime("%Y-%m-%d"), "cap_gb": 1000.0, "used_gb": pct * 10, "used_pct": round(pct, 2)})
    return out


NOW = datetime(2026, 11, 14, 8, 0)
AT = "2026-11-13 20:00:00"


def test_預估_線性趨勢算天數與日期():
    f = sc.forecast(_pts(50, 0.5, 10), AT, NOW)            # 目前 54.5%，每天 +0.5 → (90-54.5)/0.5 = 71 天
    assert f["slope_pct_day"] == 0.5 and f["gb_per_day"] == 5.0 and f["data_days"] == 10
    assert f["days_to_90"] == 71 and f["eta_date"] == "2027-01-23" and f["status"] == "71 天"


def test_預估_資料不足_不會到_已超過_資料過期():
    f = sc.forecast(_pts(50, 1, 6), AT, NOW)
    assert f["status"] == "資料不足（6 天）" and f["days_to_90"] is None and f["gb_per_day"] is None
    assert sc.forecast([], AT, NOW)["status"] == "資料不足（0 天）"
    assert sc.forecast(_pts(60, 0, 10), AT, NOW)["status"] == "不會到"
    assert sc.forecast(_pts(60, -0.2, 10), AT, NOW)["status"] == "不會到"
    assert sc.forecast(_pts(85, 1, 10), AT, NOW)["status"] == "已超過"          # 最後一點 94%
    old = sc.forecast(_pts(50, 0.5, 10, last="2026-11-01"), "2026-11-01 20:00:00", NOW)
    assert old["status"] == "資料過期，不預估" and old["days_to_90"] is None
    # 只用最後一點往前 30 天：40 天前的舊點不影響斜率
    pts = [{"date": "2026-09-01", "cap_gb": 1000.0, "used_gb": 0.0, "used_pct": 0.0}] + _pts(50, 0.5, 10)
    assert sc.forecast(pts, AT, NOW)["slope_pct_day"] == 0.5


def test_VMware_Disk_全部列_標記數字等於下鑽(tmp_path):
    conn = _conn(tmp_path)
    try:
        now = datetime.now().replace(microsecond=0)
        at = now.isoformat(sep=" ")
        for name, free in (("VD-LOW", 500), ("VD-SOON", 5000), ("VD-FLAT", 5000)):
            _vol(conn, name, free, ["esxi-x9"], at=at)
        conn.commit()
        sc.ensure_table(conn)
        ids = {v["name"]: v["id"] for v in sc.datastores(conn)["volumes"]}
        for i in range(10):                                   # 10 天：VD-SOON 每天 +1%（41→50%），VD-FLAT 不動
            d = (now - sc.timedelta(days=9 - i)).strftime("%Y-%m-%d")
            for name, pct in (("VD-SOON", 41 + i), ("VD-FLAT", 50)):
                conn.execute("INSERT INTO storage_daily (snap_date, volume_id, name, capacity_mib, in_use_mib, free_mib) "
                             "VALUES (?,?,?,?,?,?)", (d, ids[name], name, 10000 * GIB, pct * 100 * GIB, (100 - pct) * 100 * GIB))
        conn.commit()
        o = sc.overview(conn)
        vd = o["vmdisk"]
        rows = {r["name"]: r for r in vd["rows"]}
        assert len(vd["rows"]) == o["datastores"]["volume_count"] == 3
        assert rows["VD-LOW"]["low"] and not rows["VD-SOON"]["low"]
        assert rows["VD-SOON"]["soon"] and rows["VD-SOON"]["days_to_90"] == 40 and rows["VD-SOON"]["gb_per_day"] == 100.0
        assert rows["VD-FLAT"]["forecast_status"] == "不會到" and not rows["VD-FLAT"]["soon"]
        assert rows["VD-LOW"]["forecast_status"] == "已超過"                 # 剩 500 / 10000 GB＝已用 95%
        assert vd["low_count"] == sum(1 for r in vd["rows"] if r["low"]) == 1
        assert vd["soon_count"] == sum(1 for r in vd["rows"] if r["soon"]) == 1
        assert {r["id"] for r in vd["rows"] if r["low"]} == {v["id"] for v in o["alerts"]["volumes"]}
        assert len(rows["VD-SOON"]["points"]) == rows["VD-SOON"]["data_days"] == 10
    finally:
        conn.close()
