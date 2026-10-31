"""機櫃（rack_layout）：解析不猜、VM 沿用 ESXi 機櫃、加總＝下鑽、機櫃反查、全域搜尋。"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import blast_radius as br  # noqa: E402
import ci_graph  # noqa: E402
import db  # noqa: E402
import rack_layout as rl  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


@pytest.mark.parametrize("raw,kind,rack,u1,u2", [
    ("M10/31U", "rack", "M10", 31, 31),
    ("C5/34-35U", "rack", "C5", 34, 35),
    ("1-1櫃(A1)/10U", "rack", "1-1", 10, 10),
    ("1--4櫃(A4)", "rack", "1-4", None, None),
    ("B08", "rack", "B8", None, None),          # B08＝B8
    ("A-9櫃", "rack", "A9", None, None),
    ("M10/27U、28U", "rack", "M10", 27, 27),     # 後面的段只留在原值
    ("M11-M12", "unparsed", None, None, None),  # 跨兩櫃：不猜
    ("218號-4F", "unparsed", None, None, None),
    ("網路櫃", "unparsed", None, None, None),
    ("21F 機房 2-1", "rack", "2-1", None, None),      # 寫了機櫃代碼（帶樓層）→ 認得
    ("21F 機房 A3", "rack", "A3", None, None),
    ("21F 機房", "unparsed", None, None, None),       # 只有樓層：不猜櫃
    ("(VM)", "vm", None, None, None),
    ("N/A", "empty", None, None, None),
    ("", "empty", None, None, None),
])
def test_機櫃解析_認不出就不猜(raw, kind, rack, u1, u2):
    p = rl.parse_rack(raw)
    assert (p["kind"], p["rack"], p["u_start"], p["u_end"]) == (kind, rack, u1, u2)


def test_顯示名稱_NA改用資產名稱():
    assert rl.display_name("N/A", "交易主機") == "交易主機"
    assert rl.display_name("  ", "交易主機") == "交易主機"
    assert rl.display_name("app01", "交易主機") == "app01"


def _seed(conn):
    def hw(serial, host, ip, loc, rack, api=None, name=None, model=None):
        return db.insert_hardware(conn, asset_serial=serial, hostname=host, ip=ip, physical_location=loc,
                                  rack_no=rack, api_id=api, asset_name=name, device_model=model)
    hw("P-1", "phy1", "10.99.3.1", "01_甲機房", "M10/30-31U", "N-960", "櫃測系統", "PowerEdge R750")
    hw("P-2", "N/A", "10.99.3.2", "01_甲機房", "M10/20U", "N-960", "櫃測DB", "Exadata X9M")
    hw("P-3", "oth1", "10.99.3.3", "01_甲機房", "M10/10U", "N-961", "鄰居系統")
    hw("P-4", "phy4", "10.99.3.4", "01_甲機房", "M11-M12", "N-960", "櫃測系統")      # 認不出：無機櫃資料
    hw("E-1", "esxi-k", "10.99.3.50", "01_甲機房", "M12/5-6U", "I-900", "虛擬化平台")
    vm = hw("V-1", "vm1", "10.99.3.60", None, "(VM)", "N-960", "櫃測系統")
    conn.execute(
        "INSERT INTO source_record (source, source_key, payload, resolved_status, resolved_hardware_id, "
        "resolved_rule, resolved_confidence, collected_at) VALUES ('vcenter','vm-1',?, 'matched', ?, "
        "'vm_uuid', 1.0, '2026-10-01 09:00:00')", (json.dumps({"esxi_host": "esxi-k", "cluster": "CL-K"}), vm))
    hw("V-2", "vm2", "10.99.3.61", None, "(VM)", "N-961", "鄰居系統")
    # 收集帳號 7 天內連得上＝實際看得到（在用）；P-2 刻意不設＝只有登記 → 機櫃上算「待確認」
    conn.execute("UPDATE hardware SET collect_ok = 1, collect_checked_at = datetime('now','localtime') "
                 "WHERE asset_serial IN ('P-1','P-3','P-4','E-1','V-1','V-2')")
    conn.commit()
    ci_graph.rebuild(conn)


def test_系統機櫃_VM沿用ESXi機櫃_組加總等於台數(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        m = br.system_map(conn, "N-960")
        r = m["racks"]
        assert r["racked_count"] + r["no_rack_count"] == m["summary"]["hosts"] == 4
        racks = {x["rack"]: x for room in r["rooms"] for x in room["racks"]}
        assert set(racks) == {"M10", "M12"}
        assert racks["M10"]["host_count"] == 2
        vm_row = racks["M12"]["rows"][0]
        assert vm_row["via_esxi"] == "esxi-k" and (vm_row["u_start"], vm_row["u_end"]) == (5, 6)
        assert r["no_rack"][0]["raw"] == "M11-M12"
        names = {h["hostname"] for h in m["hosts"]}
        assert "櫃測DB" in names and "N/A" not in names                 # N/A 改用資產名稱
    finally:
        conn.close()


def test_機房架構圖_磚加總與VM排除(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        d = rl.datacenter(conn)
        room = next(r for r in d["rooms"] if r["room"] == "甲機房")
        tiles = {t["rack"]: t for t in room["racks"]}
        assert tiles["M10"]["device_count"] == 3 and tiles["M10"]["system_count"] == 2   # 使用中＋有機櫃＝在用
        assert tiles["M10"]["power"]["unobserved_count"] == 1                             # P-2 看不到：只標未觀察到
        assert tiles["M10"]["used_u"] == 4                                 # 30-31、20、10（待確認也佔 U）
        assert room["racked"] == sum(t["device_count"] for t in room["racks"]) == 4
        assert room["device_count"] == room["racked"] + len(room["no_rack"]) == 5
        assert sum(r["vm_excluded"] for r in d["rooms"]) == 2
        assert d["total_racked"] == sum(r["racked"] for r in d["rooms"])
        assert d["floor_map_set"] is False and all(t["floor"] is None for t in room["racks"])
    finally:
        conn.close()


def test_機櫃反查與本系統標示(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        det = rl.rack_detail(conn, "甲機房", "M10", highlight_api="N-960")
        assert det["mine_count"] == 2 and det["others_count"] == 1
        assert [s["api_id"] for s in det["systems"]] == ["N-960", "N-961"]
        assert det["devices"][0]["u_end"] == 31                             # 由上往下排
        with pytest.raises(ValueError):
            rl.rack_detail(conn, "甲機房", "Z99")
    finally:
        conn.close()


def test_樓層掛鉤_有設定才顯示(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES ('rack_floor_map', ?)",
                     (json.dumps({"M1": "3F"}),))
        conn.commit()
        d = rl.datacenter(conn)
        tiles = {t["rack"]: t for r in d["rooms"] for t in r["racks"]}
        assert tiles["M10"]["floor"] == "3F"
    finally:
        conn.close()


def test_全域搜尋_三個角度(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        r = rl.search(conn, "exadata")
        hw = r["angles"]["hardware"]
        assert [(x["rack"], x["u_start"]) for x in hw["matches"]] == [("M10", 20)]
        assert hw["racks"] == [{"room": "甲機房", "rack": "M10", "count": 1}]
        assert r["counts"] == {"hardware": 1, "host": 0, "system": 0}
        assert rl.search(conn, "R 750")["counts"]["hardware"] == 1
        assert rl.search(conn, "P-3")["counts"]["hardware"] == 1                 # 資產序號
        h = rl.search(conn, "10.99.3.4")["angles"]["host"]["matches"]
        assert h[0]["rack_raw"] == "M11-M12"
        s = rl.search(conn, "N-960")["angles"]["system"]                      # 系統 → 全部實體設備
        assert [x["api_id"] for x in s["systems"]] == ["N-960"]
        assert s["total"] == 3                                                # P-1、P-2、P-4（VM 不列）
        assert {x["rack"] for x in s["racks"]} == {"M10"}
        assert rl.search(conn, "櫃測")["counts"]["system"] == 3
        assert rl.search(conn, "vm1")["counts"] == {"hardware": 0, "host": 0, "system": 0}
    finally:
        conn.close()


def test_系統的VM所在ESXi的櫃也要能標示(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        d = rl.datacenter(conn)
        tiles = {t["rack"]: t for r in d["rooms"] for t in r["racks"]}
        assert tiles["M12"]["vm_api_ids"] == ["N-960"]          # vm1（N-960）跑在 M12 的 esxi-k 上
        s = rl.search(conn, "N-960")["angles"]["system"]
        assert s["racks_via_vm"] == [{"room": "甲機房", "rack": "M12", "count": 1}]
        assert s["total"] == 3                                    # ESXi 主機本身不算進這個系統
    finally:
        conn.close()


def test_IP比對以整段為單位():
    assert br.ip_match("10.99.1.12", "10.99.1.12")
    assert not br.ip_match("10.99.1.128", "10.99.1.12")
    assert br.ip_match("10.99.1.128", "10.99.1")
    assert br.ip_match("10.99.1.128", "10.99.1.")
    assert not br.ip_match("10.99.11.5", "10.99.1")
    assert not br.ip_match("10.99.1.5", "app")


def test_樓層提示_機櫃欄自己寫的樓層(tmp_path):
    conn = _conn(tmp_path)
    try:
        db.insert_hardware(conn, asset_serial="F-1", hostname="f1", ip="10.99.2.9", physical_location="00_乙機房",
                           rack_no="21F 機房 2-1", api_id="N-962", asset_name="樓層系統")
        conn.commit()
        d = rl.datacenter(conn)
        t = next(t for r in d["rooms"] for t in r["racks"] if t["rack"] == "2-1")
        assert t["floor"] == "21F"
        assert rl.rack_detail(conn, "乙機房", "2-1")["floor"] == "21F"
    finally:
        conn.close()


def test_盤點表_每櫃列數等於設備數(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        one = rl.print_sheets(conn, room="甲機房", rack="M10")
        det = rl.rack_detail(conn, "甲機房", "M10")
        assert len(one["sheets"]) == 1 and len(one["sheets"][0]["rows"]) == len(det["devices"]) == 3
        assert [r["u"] for r in one["sheets"][0]["rows"]] == ["30-31U", "20U", "10U"]   # 由上往下
        assert one["blank_rows"] == 5
        room = rl.print_sheets(conn, room="甲機房")
        assert [x["rack"] for x in room["sheets"]] == ["M10", "M12"]
        for sh in room["sheets"]:
            assert len(sh["rows"]) == sh["device_count"] == len(rl.rack_detail(conn, "甲機房", sh["rack"])["devices"])
        sysp = rl.print_sheets(conn, api_id="N-960")                  # 系統：自己的櫃＋VM 經 ESXi 的櫃
        assert {x["rack"] for x in sysp["sheets"]} == {"M10", "M12"}
        row = next(r for r in one["sheets"][0]["rows"] if r["asset_serial"] == "P-2")
        assert row["name"] == "櫃測DB" and "未觀察到（只有登記）" in row["marks"]
        with pytest.raises(ValueError):
            rl.print_sheets(conn)
    finally:
        conn.close()


def test_標籤_來源與聯絡人退回擁有者(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        conn.execute("UPDATE hardware SET owner='王小明' WHERE asset_serial='P-1'")
        conn.execute("UPDATE hardware SET custodian='李保管' WHERE asset_serial='P-3'")
        conn.commit()
        r = rl.label_items(conn, room="甲機房", rack="M10")
        by = {x["asset_serial"]: x for x in r["items"]}
        assert set(by) == {"P-1", "P-2", "P-3"}
        assert by["P-1"]["contact"] == "王小明" and by["P-1"]["contact_is_owner"] is True
        assert by["P-3"]["contact"] == "李保管"
        assert [p["id"] for p in r["presets"]] == ["a4_3x8"]
        assert {x["asset_serial"] for x in rl.label_items(conn, api_id="N-960")["items"]} == {"P-1", "P-2", "P-4"}
        assert [x["asset_serial"] for x in rl.label_items(conn, q="exadata")["items"]] == ["P-2"]
    finally:
        conn.close()
