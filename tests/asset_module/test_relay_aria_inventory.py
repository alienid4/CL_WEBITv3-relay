"""中繼匯入：Aria（vCOps）VM 清單（2.058.31，B 路線：55.33 每週匯出 → 9.26 上傳 → .14 匯入）。

釘住：VM 進資產清單（含 ESXi／叢集／vCenter）、只寫 vInfo 不碰明細分頁、空清單／缺檔拒收、每週來源 9 天才過期。
範例 IP 只用 192.0.2.x。
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import relay_import as ri  # noqa: E402
from test_relay_import import env, write_batch, batch_row  # noqa: E402,F401


def res(rid, name, kind, ids=None):
    return {"identifier": rid, "resourceKey": {"name": name, "adapterKindKey": "VMWARE", "resourceKindKey": kind,
            "resourceIdentifiers": [{"identifierType": {"name": k}, "value": v} for k, v in (ids or {}).items()]}}


def files(vms=None):
    vc = {"VMEntityVCID": "vc-1"}
    vms = vms if vms is not None else [res("vm-a", "web01", "VirtualMachine", {"VMEntityObjectID": "vm-101", **vc})]
    return {
        "vms.json": vms,
        "hosts.json": [res("h1", "esxi-a.example.local", "HostSystem", vc)],
        "clusters.json": [res("c1", "CL_PROD", "ClusterComputeResource", vc)],
        "props.json": {"vm-a": {"config|uuid": "42301111-2222-3333-4444-555566667777", "guest|hostName": "web01.corp.example",
                                "guest|ipAddress": "192.0.2.10", "config|guestFullName": "Rocky Linux 9",
                                "summary|runtime|powerState": "Powered On", "summary|parentVcenter": "vc01.corp.example"}},
        "rels.json": {"cluster_hosts": {"c1": ["h1"]}, "host_vms": {"h1": ["vm-a"]}},
    }


def put(drop, name="inv1", content=None):
    return write_batch(drop, name, content or files(), source="aria_inventory",
                       collected=datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"))


def write_on(c):
    db.set_setting(c, ri.AriaInventoryAdapter.WRITE_KEY, "1")


def test_預設只預覽_不寫資產_紀錄裡有比對數字(env):
    c, drop = env
    put(drop)
    ri.run_scan(c)
    row = batch_row(c, "inv1")
    assert row["status"] == "imported"
    assert c.execute("SELECT COUNT(*) FROM source_record WHERE source='vcenter'").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM hardware").fetchone()[0] == 0
    text = " ".join(str(row[k]) for k in row.keys() if row[k] is not None)
    assert "預覽（未寫入）" in text and "新的 1" in text and "vCenter 100.0%" in text


def test_打開寫入_UUID太少整批不寫(env):
    c, drop = env
    write_on(c)
    f = files()
    f["props.json"]["vm-a"].pop("config|uuid")
    put(drop, content=f)
    ri.run_scan(c)
    assert batch_row(c, "inv1")["status"] == "rejected"
    assert c.execute("SELECT COUNT(*) FROM source_record WHERE source='vcenter'").fetchone()[0] == 0


def test_VM清單匯入_帶ESXi叢集vCenter(env):
    c, drop = env
    write_on(c)
    put(drop)
    r = ri.run_scan(c)
    row = batch_row(c, "inv1")
    assert row["status"] == "imported", (r, dict(row))
    sr = c.execute("SELECT payload FROM source_record WHERE source='vcenter' AND payload LIKE '%web01%'").fetchone()
    assert sr is not None
    p = sr["payload"]
    assert "esxi-a.example.local" in p and "CL_PROD" in p and "vc01.corp.example" in p


def test_只寫VM清單_不碰明細分頁(env):
    c, drop = env
    write_on(c)
    put(drop)
    ri.run_scan(c)
    n = c.execute("SELECT COUNT(*) FROM source_record WHERE source LIKE 'vcenter_extra:%'").fetchone()[0]
    assert n == 0


def test_空清單拒收_不當成VM全部不見(env):
    c, drop = env
    put(drop, content=files(vms=[]))
    ri.run_scan(c)
    assert batch_row(c, "inv1")["status"] == "rejected"


def test_缺檔拒收(env):
    c, drop = env
    f = files()
    del f["rels.json"]
    put(drop, content=f)
    ri.run_scan(c)
    assert batch_row(c, "inv1")["status"] == "rejected"


def test_每週來源_9天內不亮過期(env):
    c, drop = env
    put(drop)
    ri.run_scan(c)
    later = datetime.now(timezone(timedelta(hours=8))) + timedelta(days=8)
    v = next(s for s in ri.sources_view(c, now=later) if s["source"] == "aria_inventory")
    assert v["stale_days"] == 9 and v["status"] == "ok"
    v2 = next(s for s in ri.sources_view(c, now=later + timedelta(days=2)) if s["source"] == "aria_inventory")
    assert v2["status"] == "stale"


def test_寫入時vCOps沒帶的欄位沿用既有_vCenter名沿用既有(env):
    c, drop = env
    write_on(c)
    put(drop, "inv1")
    ri.run_scan(c)
    import json as _j
    row = c.execute("SELECT source_key, payload FROM source_record WHERE source='vcenter'").fetchone()
    old = _j.loads(row["payload"])
    old["vi_sdk_server"] = "vc01-rvtools.corp.example"
    c.execute("UPDATE source_record SET payload=? WHERE source_key=?", (_j.dumps(old), row["source_key"]))
    c.commit()
    f = files()
    f["rels.json"] = {"cluster_hosts": {}, "host_vms": {}}          # 這次 vCOps 沒帶關聯 → ESXi／叢集會是空的
    put(drop, "inv2", content=f)
    ri.run_scan(c)
    new = _j.loads(c.execute("SELECT payload FROM source_record WHERE source='vcenter'").fetchone()["payload"])
    assert new["esxi_host"] == "esxi-a.example.local" and new["cluster"] == "CL_PROD"
    assert new["vi_sdk_server"] == "vc01-rvtools.corp.example"


def test_整包抓_屬性分檔_其他vInfo欄位進來源紀錄(env):
    c, drop = env
    write_on(c)
    f = files()
    props = f.pop("props.json")
    props["vm-a"].update({"summary|folder": "N-107/108_交易系統", "config|createDate": "2024-01-02",
                          "net:4000|mac_address": "00:50:56:AA:BB:CC", "config|hardware|numCpu": "8"})
    f["props_001.json"] = {"vm-a": props["vm-a"]}
    f["props_002.json"] = {}
    put(drop, content=f)
    ri.run_scan(c)
    assert batch_row(c, "inv1")["status"] == "imported"
    import json as _j
    raw = _j.loads(c.execute("SELECT payload FROM source_record WHERE source='vcenter'").fetchone()["payload"])["raw"]
    assert raw["Folder"] == "N-107/108_交易系統" and raw["MAC Address"] == "00:50:56:aa:bb:cc" and raw["CPUs"] == "8"


def test_沒有任何屬性檔拒收(env):
    c, drop = env
    f = files()
    f.pop("props.json")
    put(drop, content=f)
    ri.run_scan(c)
    assert batch_row(c, "inv1")["status"] == "rejected"
