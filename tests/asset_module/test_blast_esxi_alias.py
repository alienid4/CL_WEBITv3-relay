"""同一台 ESXi 有兩筆登記（DYN-IP 與 HW-編號）時，影響範圍／關聯圖都要看得到它的 VM 與業務系統。

起因（2026-10-29 夜間速查，真實資料副本）：角色節點 esxi:ESXI018-01 的 asset_serial 只回填其中一筆登記，
查另一筆（DYN-）時 resolve 只找到 hw: 節點，影響範圍是空的、關聯圖只剩自己。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import blast_radius as br  # noqa: E402
import db  # noqa: E402
import oncall  # noqa: E402


def _node(c, nid, ntype, label, serial=None):
    c.execute("INSERT INTO ci_node (node_id,node_type,label,asset_serial,source,created_at,updated_at) "
              "VALUES (?,?,?,?,'derive:hardware','t','t')", (nid, ntype, label, serial))


def _edge(c, src, dst, etype="depends_on"):
    c.execute("INSERT INTO ci_edge (src_node_id,dst_node_id,edge_type,confidence,source,created_at) "
              "VALUES (?,?,?,'證據','derive:hardware','t')", (src, dst, etype))


def _seed(tmp_path, esxi_label="esxi018-01.corp.example", esxi_id="esxi:ESXI018-01", esxi_serial="HW-ESX"):
    p = tmp_path / "e.db"
    db.init_db(p)
    c = db.get_connection(p)
    # 同一台 ESXi 的兩筆登記
    db.insert_hardware(c, asset_serial="DYN-10.99.18.1", hostname="ESXI018-01", ip="10.99.18.1",
                       os="VMware ESXi, 8.0.3", asset_status="使用中")
    db.insert_hardware(c, asset_serial="HW-ESX", hostname="esxi018-01", ip="10.99.18.1",
                       os="VMware ESXi, 8.0.3", asset_status="使用中")
    # 跑在它上面的 VM，掛業務系統 N-1
    db.insert_hardware(c, asset_serial="HW-VM1", hostname="vm01", ip="10.99.18.21", os="RHEL 8",
                       asset_status="使用中", api_id="N-1", availability=3)
    # 一台不相干的主機（不能被誤抓成 ESXi）
    db.insert_hardware(c, asset_serial="HW-X", hostname="plain01", ip="10.99.18.50", os="RHEL 8",
                       asset_status="使用中")
    c.execute("INSERT INTO business_system (api_id, name) VALUES ('N-1','交易')")
    _node(c, "hw:DYN-10.99.18.1", "host", "ESXI018-01", "DYN-10.99.18.1")
    _node(c, "hw:HW-ESX", "host", "esxi018-01", "HW-ESX")
    _node(c, "hw:HW-VM1", "host", "vm01", "HW-VM1")
    _node(c, "hw:HW-X", "host", "plain01", "HW-X")
    _node(c, "bizsys:N-1", "business_service", "交易")
    # 角色節點只回填 HW-ESX
    _node(c, esxi_id, "esxi", esxi_label, esxi_serial)
    _edge(c, "hw:HW-VM1", esxi_id, "runs_on")
    _edge(c, "bizsys:N-1", "hw:HW-VM1")
    c.commit()
    return c


def test_沒回填的那筆登記也解到角色節點(tmp_path):
    c = _seed(tmp_path)
    assert br.resolve(c, "DYN-10.99.18.1")["node_id"] == "esxi:ESXI018-01"
    assert br.resolve(c, "HW-ESX")["node_id"] == "esxi:ESXI018-01"
    # 不相干的主機維持原樣
    assert br.resolve(c, "HW-X")["node_id"] == "hw:HW-X"


def test_hw節點直接問影響範圍_也算進ESXi上的VM與系統(tmp_path):
    c = _seed(tmp_path)
    imp = br.impact(c, "hw:DYN-10.99.18.1")
    ids = {d["node_id"] for d in imp["dependents"]}
    assert "hw:HW-VM1" in ids and "bizsys:N-1" in ids
    assert [s["api_id"] for s in imp["summary"]["by_biz_system"]] == ["N-1"]
    # 起點自己不算受影響對象
    assert "hw:DYN-10.99.18.1" not in ids and "esxi:ESXI018-01" not in ids
    # 跟直接查角色節點同一組結果
    ref = {d["node_id"] for d in br.impact(c, "esxi:ESXI018-01")["dependents"]}
    assert ids == ref


def test_關聯圖不再只剩自己(tmp_path):
    c = _seed(tmp_path)
    g = br.graph_elements(c, "hw:DYN-10.99.18.1")["elements"]
    nids = {n["data"]["id"] for n in g["nodes"]}
    assert {"hw:DYN-10.99.18.1", "esxi:ESXI018-01", "hw:HW-VM1", "bizsys:N-1"} <= nids
    assert len(g["edges"]) >= 2


def test_一般主機的影響範圍不變(tmp_path):
    c = _seed(tmp_path)
    imp = br.impact(c, "hw:HW-X")
    assert imp["dependents"] == []


def test_ESXi名稱是IP時也找得到(tmp_path):
    """RVTools 的 Host 欄有時就是 IP：節點標籤是那串 IP，node_id 被切成 esxi:10。"""
    c = _seed(tmp_path, esxi_label="10.99.18.1", esxi_id="esxi:10", esxi_serial=None)
    ids = {d["node_id"] for d in br.impact(c, "hw:DYN-10.99.18.1")["dependents"]}
    assert "hw:HW-VM1" in ids


def test_值班卡的影響範圍對ESXi有值(tmp_path):
    c = _seed(tmp_path)
    r = oncall.lookup(c, "ESXI018-01")
    assert r["mode"] == "machine"
    imp = r["card"]["impact"]
    assert imp["ok"] and imp["systems"] == 1 and imp["system_list"][0]["api_id"] == "N-1"
    assert imp["host_count"] >= 1


def test_值班卡標出VMware設備_一般主機沒有(tmp_path):
    c = _seed(tmp_path)
    assert oncall.lookup(c, "ESXI018-01")["card"]["what"]["vmware"]
    assert not oncall.lookup(c, "plain01")["card"]["what"]["vmware"]


def test_候選用資產序號再查_一定回到被點的那台(tmp_path):
    """夜間速查：名稱對到多台，點選後用資產序號查（不是再解析一次打的字）。"""
    p = tmp_path / "pick.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.insert_hardware(c, asset_serial="P-1", hostname="web-01", ip="10.99.7.1", os="RHEL 8", asset_status="使用中")
    db.insert_hardware(c, asset_serial="P-2", hostname="web-011", ip="10.99.7.2", os="RHEL 8", asset_status="使用中")
    c.commit()
    r = oncall.lookup(c, "web-0")
    assert r["mode"] == "candidates" and len(r["candidates"]) == 2
    for cand in r["candidates"]:
        got = oncall.lookup(c, cand["asset_serial"])
        assert got["mode"] == "machine" and got["card"]["what"]["serials"] == [cand["asset_serial"]]


def test_D3審查_一般主機短名剛好跟ESXi同名_不可借用它的影響範圍(tmp_path):
    c = _seed(tmp_path)
    db.insert_hardware(c, asset_serial="HW-RH", hostname="esxi018-01.other", ip="10.99.18.77", os="RHEL 9",
                       asset_status="使用中")
    _node(c, "hw:HW-RH", "host", "esxi018-01.other", "HW-RH")
    c.commit()
    assert br.resolve(c, "HW-RH")["node_id"] == "hw:HW-RH"
    assert br.impact(c, "hw:HW-RH")["dependents"] == []
    g = br.graph_elements(c, "hw:HW-RH")["elements"]
    assert {n["data"]["id"] for n in g["nodes"]} == {"hw:HW-RH"}


def test_D3審查_真ESXi兩筆登記都解得到(tmp_path):
    c = _seed(tmp_path)
    for s in ("DYN-10.99.18.1", "HW-ESX"):
        assert br.resolve(c, s)["node_id"] == "esxi:ESXI018-01"
        ids = {d["node_id"] for d in br.impact(c, "hw:" + s)["dependents"]}
        assert "hw:HW-VM1" in ids


def test_D3審查_IP標籤的ESXi節點只靠完整IP比對(tmp_path):
    c = _seed(tmp_path, esxi_label="10.99.18.1", esxi_id="esxi:10", esxi_serial=None)
    # 另一台 ESXi，IP 前綴相同但不是同一個：10.99.18.12（不是 10.99.18.1）
    db.insert_hardware(c, asset_serial="DYN-10.99.18.12", hostname="OTHERESX", ip="10.99.18.12",
                       os="VMware ESXi, 8.0.3", asset_status="使用中")
    _node(c, "hw:DYN-10.99.18.12", "host", "OTHERESX", "DYN-10.99.18.12")
    c.commit()
    assert br.resolve(c, "DYN-10.99.18.12")["node_id"] == "hw:DYN-10.99.18.12"
    assert br.impact(c, "hw:DYN-10.99.18.12")["dependents"] == []
    assert "hw:HW-VM1" in {d["node_id"] for d in br.impact(c, "hw:DYN-10.99.18.1")["dependents"]}
