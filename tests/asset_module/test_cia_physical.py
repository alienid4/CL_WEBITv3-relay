"""S1 實體機補登（2.061.2）。全部假資料（IP 一律 10.99.x）。

母體（未登錄 CIA、非虛擬機、非 VMware 設備）／三態（實體、非實體、無法判定）／證據帶日期、相差太久降為單一來源／
機櫃圖 Excel 與彙整表位置並列、衝突不預填／APID 回測沒過門檻不預填／寫進共用案件表。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import cia_case  # noqa: E402
import cia_physical as cp  # noqa: E402
import db  # noqa: E402


def _seed(c):
    db.insert_hardware(c, asset_serial="S1", hostname="reg01", ip="10.99.1.1", api_id="N-1", device_model="PowerEdge R740", hw_serial="HW1")
    db.insert_hardware(c, asset_serial="AUTO-10.99.5.1", hostname="db01", ip="10.99.5.1", device_model="PowerEdge R740", hw_serial="HW5")
    db.insert_hardware(c, asset_serial="AUTO-10.99.5.2", hostname="odd01", ip="10.99.5.2", device_model="", hw_serial="")
    db.insert_hardware(c, asset_serial="AUTO-10.99.5.3", hostname="ctr01", ip="10.99.5.3", device_model="Docker host", os="Linux")
    db.insert_hardware(c, asset_serial="AUTO-10.99.5.4", hostname="esx01", ip="10.99.5.4", os="VMware ESXi 7.0", device_model="PowerEdge R640")
    c.commit()


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    _seed(c)
    yield c
    c.close()


def test_規則檔沒有真實IP():
    txt = cp.RULES_PATH.read_text(encoding="utf-8")
    assert "10.93." not in txt and "10.92." not in txt


def test_三態與排除_VMware不當候選(conn):
    p = cp.plan(conn)
    keys = {s: {x["hostname"] for x in p[s]} for s in ("physical", "unknown", "virtual")}
    assert "db01" in keys["physical"] and "odd01" in keys["unknown"] and "ctr01" in keys["virtual"]
    assert all("esx01" not in v for v in keys.values()) and "reg01" not in str(keys)
    assert p["excluded"]["vmware"] >= 1
    o = cp.overview(conn)
    assert o["physical"] == len(p["physical"]) and o["unknown"] == len(p["unknown"])


def test_證據帶日期_相差太久降為單一來源():
    ev, lv = cp._evidence({"kinds": ["no_cia"]}, "2026-01-01", {"room": "甲", "rack": "A1", "date": "2026-01-10"}, None, 30)
    assert lv == "互相佐證" and [e["source"] for e in ev] == ["彙整表", "機櫃圖 Excel"]
    _ev, lv = cp._evidence({"kinds": ["no_cia"]}, "2026-01-01", {"room": "甲", "rack": "A1", "date": "2026-05-01"}, None, 30)
    assert lv.startswith("單一來源")
    assert cp._evidence(None, None, None, None, 30)[1] == "沒有證據"


def test_機櫃圖Excel有這台就是實體_位置衝突不預填(conn):
    import rack_excel_import as rx
    rx.ensure(conn)
    key = next(x["machine_key"] for x in cp.plan(conn)["unknown"] if x["hostname"] == "odd01")
    rows = [{"key": key, "status": "NOPOS", "rack": "A1", "to_rack": "A1", "u_start": 10, "u_end": 11, "label": "odd01"}]
    conn.execute("INSERT INTO rack_excel_run (filename, room, created_by, created_at, rows_json) VALUES (?,?,?,?,?)",
                 ("x.xlsx", "甲機房", "t", "2026-11-01 10:00:00", json.dumps(rows)))
    conn.commit()
    p = cp.plan(conn)
    odd = next(x for x in p["physical"] if x["hostname"] == "odd01")
    assert odd["state_why"] == "機櫃圖 Excel 有這台" and odd["fill"]["room"] == "甲機房" and odd["fill"]["rack"] == "A1"
    assert odd["rack_loc"].startswith("甲機房 A1") and not odd["conflict"]
    assert odd["evidence"][0]["date"] == "2026-11-01"


def test_APID回測沒過門檻不預填(conn):
    bt = cp._apid_backtest(conn, {}, {}, cp.load_rules())
    assert bt["samples"] == 0 and not bt["ok"]


def test_寫進案件表_一台一案_無法判定不進(conn):
    r = cp.scan(conn, "t")
    assert r["found"] == len(cp.plan(conn)["physical"]) and r["unknown"] >= 1
    cs = cia_case.list_cases(conn, "physical")
    assert {c["title"] for c in cs} == {x["hostname"] for x in cp.plan(conn)["physical"]}
    assert cp.scan(conn, "t")["found"] == 0


def test_verify_numbers_通過(conn):
    import verify_numbers as vn
    cp.scan(conn, "t")
    vn.FAILS = 0
    vn.check_cia_physical(conn)
    vn.check_cia_case(conn)
    assert vn.FAILS == 0



def test_審查P3_8_櫃名完全比對_A1不等於A10():
    assert cp._same_place("A1", "A1") and cp._same_place("A1", "A-1")
    assert not cp._same_place("A1", "A10")
    assert cp._same_place("A1", "")


def test_網路與儲存設備直接判實體():
    rules = cp.load_rules()
    info = {"rows": [{"device_model": "", "hw_serial": "", "os": ""}], "hostname": "sw01", "os": "網路設備"}
    assert cp.classify(info, rules, False)[0] == "physical"
    info["os"] = "儲存設備"
    assert cp.classify(info, rules, False)[0] == "physical"
    info["os"] = "Windows Server 2022"
    assert cp.classify(info, rules, False)[0] == "unknown"
