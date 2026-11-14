"""MICS 切片1：CI 圖譜 rebuild()。

最容易寫錯的三件事，逐一測：
1. seed 正確性——VM→ESXi 邊要建起來，且 confidence 是「證據」不是隨便標的。
2. 冪等——同一份底層資料重跑兩次，節點/邊數不變（UPSERT 不是每次疊加）。
3. manual 不被覆蓋——人工補的邊，rebuild 後要原封不動還在，這是覆蓋規則裡
   最容易漏掉的一半（另一半「derive 過期的要軟刪」比較直覺，不容易漏）。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import ci_graph  # noqa: E402
import db  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _seed_vm_and_esxi(conn):
    """一台 VM（已被 RVTools 匯入建成 hardware 資產）跑在一台已登記的 ESXi 上。"""
    vm_id = db.insert_hardware(
        conn, asset_serial="VC-vm-uuid-1", hostname="web01", ip="10.1.1.10", environment="正式",
    )
    db.insert_hardware(
        conn, asset_serial="HW-ESXI-1", hostname="ESXI169-220", ip="10.99.169.220",
        environment="正式",
    )
    payload = json.dumps({
        # 故意用 vCenter 的「小寫＋FQDN」格式，測正規化能不能對回全大寫無網域的登記值。
        # ⚠️ 網域一律用 example.com 這類假值：tests/ 會整包進 relay，寫真實公司網域
        # 會被殘留掃描擋下、整條發布線停掉，公司主機拿不到更新（2026-08-19 踩過）。
        "esxi_host": "esxi169-220.example.com",
        "cluster": "BQ_PROD_B_vSan_Cluster",
        "datastore": "PROD_B_vSan_Datastore",
    }, ensure_ascii=False)
    conn.execute(
        "INSERT INTO source_record (source, source_key, payload, resolved_status, "
        "resolved_hardware_id, resolved_rule, resolved_confidence, collected_at) "
        "VALUES ('vcenter', 'vm-uuid-1', ?, 'matched', ?, 'vm_uuid', 1.0, '2026-08-18 09:12:00')",
        (payload, vm_id),
    )
    conn.commit()
    return vm_id


def test_VM到ESXi邊建立且證據等級正確(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed_vm_and_esxi(conn)
        result = ci_graph.rebuild(conn)
        assert result["edge_count"] > 0

        edge = conn.execute(
            "SELECT * FROM ci_edge WHERE src_node_id='hw:VC-vm-uuid-1' "
            "AND dst_node_id LIKE 'esxi:%' AND edge_type='runs_on'"
        ).fetchone()
        assert edge is not None
        assert edge["confidence"] == "證據"

        # ESXi 名稱正規化：vCenter 給的小寫FQDN要正確比對回 hardware.hostname 登記的
        # 全大寫無網域格式，asset_serial 要回填成功
        esxi_node = conn.execute(
            "SELECT * FROM ci_node WHERE node_type='esxi'"
        ).fetchone()
        assert esxi_node["asset_serial"] == "HW-ESXI-1"

        # cluster/datastore 邊也要建起來
        cluster_edge = conn.execute(
            "SELECT * FROM ci_edge WHERE dst_node_id LIKE 'cluster:%'"
        ).fetchone()
        assert cluster_edge is not None
        ds_edge = conn.execute(
            "SELECT * FROM ci_edge WHERE dst_node_id LIKE 'ds:%'"
        ).fetchone()
        assert ds_edge is not None
    finally:
        conn.close()


def test_ESXi找不到對應資產時照樣建節點並標註未登記(tmp_path):
    conn = _conn(tmp_path)
    try:
        vm_id = db.insert_hardware(
            conn, asset_serial="VC-vm-2", hostname="db01", ip="10.1.1.20", environment="正式",
        )
        payload = json.dumps({"esxi_host": "esxi-unregistered.corp.local"}, ensure_ascii=False)
        conn.execute(
            "INSERT INTO source_record (source, source_key, payload, resolved_status, "
            "resolved_hardware_id, resolved_rule, resolved_confidence, collected_at) "
            "VALUES ('vcenter', 'vm-2', ?, 'matched', ?, 'vm_uuid', 1.0, '2026-08-18 09:12:00')",
            (payload, vm_id),
        )
        conn.commit()

        ci_graph.rebuild(conn)
        esxi_node = conn.execute(
            "SELECT * FROM ci_node WHERE node_id='esxi:ESXI-UNREGISTERED'"
        ).fetchone()
        assert esxi_node is not None
        assert esxi_node["asset_serial"] is None
        attrs = json.loads(esxi_node["attrs"])
        assert attrs["note"] == "未登記於資產庫"
    finally:
        conn.close()


def test_重跑一次結果冪等(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed_vm_and_esxi(conn)
        first = ci_graph.rebuild(conn)
        second = ci_graph.rebuild(conn)
        assert first["node_count"] == second["node_count"]
        assert first["edge_count"] == second["edge_count"]
        assert second["gone_count"] == 0

        # 不會重複插入（UNIQUE 撞到也不會炸，是 UPDATE 不是硬 INSERT）
        dup_check = conn.execute(
            "SELECT COUNT(*) FROM ci_edge WHERE src_node_id='hw:VC-vm-uuid-1' "
            "AND edge_type='runs_on'"
        ).fetchone()[0]
        assert dup_check == 1
    finally:
        conn.close()


def test_人工鎖定的節點與邊rebuild後原封不動(tmp_path):
    conn = _conn(tmp_path)
    try:
        vm_id = _seed_vm_and_esxi(conn)
        ci_graph.rebuild(conn)

        # 人工補一條 hw:VC-vm-uuid-1 → ds:manual-storage 的邊，鎖定
        conn.execute(
            "INSERT INTO ci_node (node_id, node_type, label, source, manual_locked, "
            "created_at, updated_at) VALUES ('ds:manual-storage','datastore','人工補登',"
            "'manual',1,'2026-08-18 10:00:00','2026-08-18 10:00:00')"
        )
        conn.execute(
            "INSERT INTO ci_edge (src_node_id, dst_node_id, edge_type, confidence, source, "
            "manual_locked, created_at) VALUES ('hw:VC-vm-uuid-1','ds:manual-storage',"
            "'stores_on','推論','manual',1,'2026-08-18 10:00:00')"
        )
        conn.commit()

        ci_graph.rebuild(conn)  # 再跑一次，本輪 derive 資料完全沒提到這條人工邊

        node = conn.execute(
            "SELECT * FROM ci_node WHERE node_id='ds:manual-storage'"
        ).fetchone()
        assert node is not None
        assert node["gone_at"] is None  # 沒被軟刪

        edge = conn.execute(
            "SELECT * FROM ci_edge WHERE src_node_id='hw:VC-vm-uuid-1' "
            "AND dst_node_id='ds:manual-storage'"
        ).fetchone()
        assert edge is not None
        assert edge["gone_at"] is None
        assert edge["confidence"] == "推論"  # 沒被 derive 邏輯改掉
    finally:
        conn.close()


def test_舊的derive邊在來源消失後被軟刪(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed_vm_and_esxi(conn)
        ci_graph.rebuild(conn)

        # 來源資料消失（source_record 被刪，模擬這台 VM 從 vCenter 匯出裡消失了）
        conn.execute("DELETE FROM source_record WHERE source='vcenter'")
        conn.commit()

        result = ci_graph.rebuild(conn)
        assert result["gone_count"] > 0

        edge = conn.execute(
            "SELECT * FROM ci_edge WHERE src_node_id='hw:VC-vm-uuid-1' AND edge_type='runs_on'"
        ).fetchone()
        assert edge["gone_at"] is not None  # 軟刪，不是 DELETE 掉那筆
    finally:
        conn.close()


def test_業務系統與機櫃與網段節點也建起來(tmp_path):
    conn = _conn(tmp_path)
    try:
        db.insert_hardware(
            conn, asset_serial="HW-APP-1", hostname="app01", ip="10.1.1.30",
            environment="正式", api_id="APID-001", rack_no="R01", physical_location="板橋機房",
        )
        ci_graph.rebuild(conn)

        bizsys = conn.execute("SELECT * FROM ci_node WHERE node_id='bizsys:APID-001'").fetchone()
        assert bizsys is not None
        rack = conn.execute("SELECT * FROM ci_node WHERE node_id='rack:板橋機房#R01'").fetchone()
        assert rack is not None

        edge1 = conn.execute(
            "SELECT * FROM ci_edge WHERE src_node_id='bizsys:APID-001' AND dst_node_id='hw:HW-APP-1'"
        ).fetchone()
        assert edge1 is not None
        edge2 = conn.execute(
            "SELECT * FROM ci_edge WHERE src_node_id='hw:HW-APP-1' AND dst_node_id='rack:板橋機房#R01'"
        ).fetchone()
        assert edge2 is not None
    finally:
        conn.close()
