"""機櫃用電／重量（型號規格表）：每台一顆 PSU、未知規格、關機未下架／待確認不算電、
工程系統整套只算一次、人工改值重新灌種子不會消失、機房電壓。"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import model_specs as msp  # noqa: E402
import rack_layout as rl  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _hw(conn, serial, host, ip, rack, model, status="使用中", seen=True, api="N-940"):
    db.insert_hardware(conn, asset_serial=serial, hostname=host, ip=ip, physical_location="01_甲機房",
                       rack_no=rack, device_model=model, api_id=api, asset_name="電力系統")
    conn.execute("UPDATE hardware SET asset_status = ? WHERE asset_serial = ?", (status, serial))
    if seen:
        conn.execute("UPDATE hardware SET collect_ok = 1, collect_checked_at = datetime('now','localtime') "
                     "WHERE asset_serial = ?", (serial,))


def _seed(conn):
    _hw(conn, "R-1", "r1", "10.99.7.1", "K1/40-41U", "DELL  R750")                  # 別名比對得到
    _hw(conn, "R-2", "r2", "10.99.7.2", "K1/30-31U", "Dell R750", status="停用")     # 關機未下架
    _hw(conn, "R-3", "r3", "10.99.7.3", "K1/20U", "Cisco C9200L-48T-4X", seen=False)  # 看不到：仍是在用，標未觀察到
    _hw(conn, "R-6", "r6", "10.99.7.6", "K1/25U", "Cisco C9200L-48T-4X", status="報廢")   # 報廢＋有機櫃 → 待確認
    _hw(conn, "R-4", "r4", "10.99.7.4", "K1/10U", "IBM AS400")                      # 籠統描述 → 未知規格
    _hw(conn, "R-5", "r5", "10.99.7.5", "K1/5U", "(VM)")                            # 型號寫 VM → 不進機櫃
    for i in range(3):                                                              # 工程系統 3 筆
        _hw(conn, f"X-{i}", f"x{i}", f"10.99.7.{20 + i}", "K2", "Oracle Exadata X7-2")
    conn.commit()


def test_種子比對與每台一顆PSU(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        spec = msp.lookup(msp.index(conn), "dell r-750")
        assert spec["model"] == "Dell R750" and msp.known_w(spec) == 2800
        assert msp.known_w(msp.lookup(msp.index(conn), "IBM AS400")) is None     # 籠統描述不算電
        r760 = next(s for s in msp.all_specs(conn) if s["model"] == "Dell R760")
        assert r760["psu_max_w"] == 2800 and r760["plug_confidence"] == "low"     # 200–240V 規則、插頭待確認
    finally:
        conn.close()


def test_機櫃用電_狀態與未知規格(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        d = rl.datacenter(conn)
        room = next(r for r in d["rooms"] if r["room"] == "甲機房")
        k1t = next(t for t in room["racks"] if t["rack"] == "K1")
        k1 = k1t["power"]
        assert k1["active_count"] == 3 and k1["off_count"] == 1 and k1["pending_count"] == 1
        assert k1t["device_count"] == 3
        assert k1["unobserved_count"] == 1                                       # R-3 看不到，但照算在用
        assert k1["w"] == 2800 + 125 and k1["if_all_on_w"] == 2800 + 125           # 關機的 R750＋報廢的 C9200L
        assert k1["known_count"] + k1["unknown_count"] == k1["active_count"]
        assert k1["unknown_count"] == 1                                           # AS400
        assert k1["amps"] == round(2925 / 220, 2) and k1["btu_h"] == round(2925 * 3.412)
        assert k1["kg"] == round(35.3 * 2 + 5.89 * 2, 1)                         # 關機、待確認也算重量
        assert k1["used_u"] == 7 and k1["free_u"] == 35
        assert k1["largest_free_u"] == 9                                          # 1–9、11–19U 各 9U
        assert [x["asset_serial"] for x in d["data_quality"]["vm_model"]] == ["R-5"]
        assert [x["asset_serial"] for x in d["data_quality"]["generic"]] == ["R-4"]
        k2 = next(t for t in room["racks"] if t["rack"] == "K2")["power"]
        assert k2["system_level"][0]["records"] == 3 and k2["kg"] == pytest.approx(413.2)   # 整套只算一次
        assert k2["unknown_count"] == 3                                            # 整套用電沒資料
        assert room["power"]["w"] == sum(t["power"]["w"] for t in room["racks"])
    finally:
        conn.close()


def _k1(conn):
    return next(t for r in rl.datacenter(conn)["rooms"] for t in r["racks"] if t["rack"] == "K1")


def test_人工標記_單台與批次_有稽核紀錄_加總不變(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        keys = {m["asset_serial"]: m["key"] for m in rl.machines(conn)}
        racked = lambda t: (t["power"]["active_count"] + t["power"]["off_count"] + t["power"]["pending_count"]  # noqa: E731
                            + t["power"]["removed_count"])
        assert racked(_k1(conn)) == 5
        rl.set_state_override(conn, keys["R-6"], "active", "現場還在用", "tester")      # 報廢其實在用
        assert _k1(conn)["power"]["pending_count"] == 0 and _k1(conn)["power"]["w"] == 2800 + 125 * 2
        rl.set_state_bulk(conn, [keys["R-1"], keys["R-3"]], "off", "現場盤點", "tester")
        t = _k1(conn)
        assert t["power"]["off_count"] == 3 and t["device_count"] == 2 and racked(t) == 5
        rl.set_state_bulk(conn, [keys["R-1"]], "removed", None, "tester")
        t = _k1(conn)
        assert t["power"]["removed_count"] == 1 and t["power"]["used_u"] == 5 and racked(t) == 5
        rl.set_state_bulk(conn, [keys["R-1"], keys["R-3"], keys["R-6"]], None, None, "tester")
        assert _k1(conn)["power"]["active_count"] == 3
        audit = rl.state_audit(conn)
        assert len(audit) == 7 and audit[0]["new_state"] is None and audit[-1]["new_state"] == "active"
        with pytest.raises(ValueError):
            rl.set_state_bulk(conn, [keys["R-1"]], "bogus", None, "tester")
        with pytest.raises(ValueError):
            rl.set_state_bulk(conn, [], "off", None, "tester")
    finally:
        conn.close()


def test_人工改值重新灌種子不會消失_機房電壓(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        msp.update(conn, "Dell R750", {"psu_max_w": "1100"}, None, "tester")
        msp.seed(conn)
        s = next(x for x in msp.all_specs(conn) if x["model"] == "Dell R750")
        assert s["psu_max_w"] == 1100 and s["manual_fields"] == ["psu_max_w"]
        conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES ('room_volts', ?)",
                     (json.dumps({"甲機房": 110}),))
        conn.commit()
        k1 = next(t for r in rl.datacenter(conn)["rooms"] for t in r["racks"] if t["rack"] == "K1")["power"]
        assert k1["w"] == 1100 + 125 and k1["volts"] == 110 and k1["amps"] == round(1225 / 110, 2)
        msp.update(conn, "Dell R750", {}, ["psu_max_w"], "tester")
        assert next(x for x in msp.all_specs(conn) if x["model"] == "Dell R750")["psu_max_w"] == 2800
        with pytest.raises(ValueError):
            msp.update(conn, "Dell R750", {"bogus": 1}, None, "tester")
    finally:
        conn.close()


def test_未定位設備_空U只是上限_W221_001(tmp_path):
    """沒有 U 位的在用設備不佔 U、但也不能當成『空』：輸出 unplaced_count／keys，機房合計同步。"""
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        d = rl.datacenter(conn)
        room = next(r for r in d["rooms"] if r["room"] == "甲機房")
        k2 = next(t for t in room["racks"] if t["rack"] == "K2")["power"]
        assert k2["unplaced_count"] == 3 and len(k2["unplaced_keys"]) == 3      # 工程系統 3 筆都沒 U
        k1 = next(t for t in room["racks"] if t["rack"] == "K1")["power"]
        assert k1["unplaced_count"] == 0 and k1["unplaced_keys"] == []
        assert room["power"]["unplaced_count"] == len(room["power"]["unplaced_keys"]) >= 3
        assert room["power"]["unplaced_count"] == sum(t["power"]["unplaced_count"] for t in room["racks"]
                                                      if not t.get("excel_only"))
        assert k2["free_u"] == rl.rack_height(conn) - k2["used_u"]               # 既有欄位不變
    finally:
        conn.close()


def test_機房頁台數等於主機查詢實體設備在機櫃_且orphan獨立列出_W221_003(tmp_path):
    import v2_hosts
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        d = rl.datacenter(conn)
        phys = v2_hosts.listing(conn, "physical")["rows"]
        assert d["total_racked"] == sum(1 for r in phys if r.get("room") and r.get("rack"))
        for r in d["rooms"]:
            assert r["orphan_count"] == len(r["orphan_keys"])
        assert d["total_orphan"] == sum(r["orphan_count"] for r in d["rooms"])
    finally:
        conn.close()
