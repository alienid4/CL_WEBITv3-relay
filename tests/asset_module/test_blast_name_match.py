"""影響範圍：VM 自己沒系統／負責人時，用正規化主機名（補零）借 CIA 登記那筆的，並標明是推論。

2026-10-23 事故：一台 ESXi 當機，vCenter 的「APP163-97」其實就是 CIA 的「APP163-097」（N-901），
只因為補零對不上就被列進「查不到負責人」。只動呈現層，台數／去重不動。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import blast_radius as br  # noqa: E402
import db  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _vm_on_esxi(conn, vms):
    conn.execute("INSERT INTO ci_node (node_id,node_type,label,source,created_at,updated_at) "
                 "VALUES ('esxi:E1','esxi','E1','t','t','t')")
    for serial, host in vms:
        db.insert_hardware(conn, asset_serial=serial, hostname=host, is_vm=1)
        conn.execute("INSERT INTO ci_node (node_id,node_type,label,asset_serial,source,created_at,updated_at) "
                     "VALUES (?, 'host', ?, ?, 't','t','t')", (f"hw:{serial}", host, serial))
        conn.execute("INSERT INTO ci_edge (src_node_id,dst_node_id,edge_type,confidence,source,created_at) "
                     "VALUES (?, 'esxi:E1', 'runs_on', '證據', 't', 't')", (f"hw:{serial}",))
    conn.commit()


def test_主機名正規化_補零大小寫網域():
    assert br.host_match_key("APP163-97") == br.host_match_key("app163-097.corp.local") == "app163-97"
    assert br.host_match_key("APP163-197") != br.host_match_key("APP163-97")
    assert br.host_match_key("10.99.1.5") is None and br.host_match_key("N/A") is None


def test_補零對到CIA登記_借系統與負責人並標明(tmp_path):
    conn = _conn(tmp_path)
    try:
        db.insert_hardware(conn, asset_serial="HW-0001", hostname="APP163-097", api_id="N-901",
                           asset_name="測試系統甲", user_name="王小明", asset_status="使用中")
        db.insert_hardware(conn, asset_serial="HW-0002", hostname="APP163-197", api_id="N-907",
                           user_name="李小華")
        _vm_on_esxi(conn, [("VC-0001", "APP163-97")])
        s = br.impact(conn, "esxi:E1")["summary"]
        assert s["unknown_owner"] == []
        biz = {b["api_id"]: b for b in s["by_biz_system"]}
        a = biz["N-901"]["assets"][0]
        assert a["asset_serial"] == "VC-0001"
        assert a["name_match"]["method"] == br.NAME_MATCH_ZERO and a["name_match"]["asset_serial"] == "HW-0001"
        assert [o["name"] for o in a["owners"]] == ["王小明"] and "名稱比對（補零）" in a["owners"][0]["role"]
        assert s["name_matched"][0]["matched_asset_serial"] == "HW-0001"
        assert "N-907" not in biz                                   # 163-197 不會被誤配
    finally:
        conn.close()


def test_對到多筆意見不同就不挑_列成有多筆(tmp_path):
    conn = _conn(tmp_path)
    try:
        db.insert_hardware(conn, asset_serial="HW-1", hostname="APP01", api_id="N-1", user_name="甲")
        db.insert_hardware(conn, asset_serial="HW-2", hostname="app001", api_id="N-2", user_name="乙")
        _vm_on_esxi(conn, [("VC-1", "app1")])
        s = br.impact(conn, "esxi:E1")["summary"]
        assert s["by_biz_system"] == []
        assert [u["asset_serial"] for u in s["unknown_owner"]] == ["VC-1"]
        assert {c["asset_serial"] for c in s["unknown_owner"][0]["ambiguous_candidates"]} == {"HW-1", "HW-2"}
        assert s["name_match_ambiguous"][0]["asset_serial"] == "VC-1"
    finally:
        conn.close()


def test_自己有系統就不比對(tmp_path):
    conn = _conn(tmp_path)
    try:
        db.insert_hardware(conn, asset_serial="HW-1", hostname="APP01", api_id="N-1", user_name="甲")
        _vm_on_esxi(conn, [("VC-1", "app1")])
        conn.execute("UPDATE hardware SET api_id = 'N-9' WHERE asset_serial = 'VC-1'")
        conn.commit()
        s = br.impact(conn, "esxi:E1")["summary"]
        assert [b["api_id"] for b in s["by_biz_system"]] == ["N-9"]
        assert s["by_biz_system"][0]["assets"][0]["name_match"] is None
    finally:
        conn.close()


def test_主機名拼錯但IP相同_借登記那筆並標IP相同(tmp_path):
    conn = _conn(tmp_path)
    try:
        db.insert_hardware(conn, asset_serial="HW-9", hostname="APPSRV161-027", ip="10.99.161.27",
                           api_id="N-902", user_name="陳大文")
        _vm_on_esxi(conn, [("DYN-10.99.161.27", "APPSVR161-027")])
        conn.execute("UPDATE hardware SET ip = '10.99.161.27' WHERE asset_serial = 'DYN-10.99.161.27'")
        conn.commit()
        s = br.impact(conn, "esxi:E1")["summary"]
        assert s["unknown_owner"] == []
        a = s["by_biz_system"][0]["assets"][0]
        assert s["by_biz_system"][0]["api_id"] == "N-902"
        assert a["name_match"]["method"] == br.NAME_MATCH_IP and a["name_match"]["asset_serial"] == "HW-9"
        assert "IP 相同" in a["owners"][0]["role"]
    finally:
        conn.close()


def test_VM位置資料天數_快照當時與今天():
    v = br.vm_export_age("2026-08-28 14:12:22", "2026-09-01 10:00:00")
    assert v["exported_at"] == "2026-08-28" and v["days_at"] == 4 and v["stale"] is True
    assert br.vm_export_age(None)["exported_at"] is None
