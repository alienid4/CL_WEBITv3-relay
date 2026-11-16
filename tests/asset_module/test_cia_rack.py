"""S2 機櫃編號統一（2.061.4）。全部假資料（IP 一律 10.99.x、機房名虛構）。

清冊（從機櫃圖 Excel 載入、版本、核准才生效）／舊寫法自動對照（樓層前綴、括號、跨櫃起訖）／對不到的人選（只增不改、最新生效）／
一格一案、交出每批限一個機房／verify_numbers。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import cia_case  # noqa: E402
import cia_fix  # noqa: E402
import cia_rack  # noqa: E402
import db  # noqa: E402
import rack_excel_import as rx  # noqa: E402
import verify_numbers as vn  # noqa: E402

ROOM = "甲機房"


def _seed(c):
    for s, h, ip, rk in [("S1", "h1", "10.99.1.1", "M11/10-12U"), ("S2", "h2", "10.99.1.2", "7F-M11~M12"),
                         ("S3", "h3", "10.99.1.3", "m-12 櫃"), ("S4", "h4", "10.99.1.4", "Z99"), ("S5", "h5", "10.99.1.5", "(VM)"),
                         ("S6", "h6", "10.99.1.6", "M12")]:
        db.insert_hardware(c, asset_serial=s, hostname=h, ip=ip, physical_location=ROOM, rack_no=rk, inventory_department="部A", group_name="G1")
    db.insert_hardware(c, asset_serial="S9", hostname="h9", ip="10.99.2.9", physical_location="乙機房", rack_no="M11")
    c.commit()
    rx.ensure(c)
    rows = [{"rack": "M11", "key": "x", "status": "OK"}, {"rack": "M12", "key": "y", "status": "OK"}]
    c.execute("INSERT INTO rack_excel_run (filename, room, created_by, created_at, rows_json) VALUES (?,?,?,?,?)",
              ("x.xlsx", ROOM, "t", "2026-11-01 10:00:00", json.dumps(rows)))
    c.commit()


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    _seed(c)
    yield c
    c.close()


def test_解析_跨櫃存起訖_樓層前綴_VM跳過():
    assert cia_rack.parse("7F-M11~M12") == {"kind": "range", "a": "M11", "b": "M12", "u": ""}
    assert cia_rack.parse("M11/10-12U")["u"] == "/10-12U"
    assert cia_rack.parse("(VM)")["kind"] == "skip"


def test_清冊要核准才生效_有版本(conn):
    assert cia_rack.roster(conn, ROOM) is None
    r = cia_rack.load_roster(conn, ROOM, "a", extra=["Z99"])
    assert r["version"] == 1 and r["racks"] == 3
    assert cia_rack.roster(conn, ROOM) is None                     # 草稿
    cia_rack.approve_roster(conn, ROOM, 1, "boss")
    ro = cia_rack.roster(conn, ROOM)
    assert ro["racks"] == ["M11", "M12", "Z99"] and ro["approved_by"] == "boss" and "excel:" in ro["source"]
    with pytest.raises(ValueError):
        cia_rack.approve_roster(conn, ROOM, 1, "boss")
    cia_rack.load_roster(conn, ROOM, "a")                         # 第 2 版草稿：生效的仍是第 1 版
    assert cia_rack.roster(conn, ROOM)["version"] == 1
    assert cia_rack.rooms(conn)[0]["draft"] == 2


def test_自動對照_對不到的人選_最新生效(conn):
    cia_rack.load_roster(conn, ROOM, "a")
    cia_rack.approve_roster(conn, ROOM, 1, "a")
    m = {x["raw"]: x for x in cia_rack.mapping(conn, ROOM)["items"]}
    assert m["M11/10-12U"]["status"] == "ok"
    assert m["7F-M11~M12"]["status"] == "auto" and m["7F-M11~M12"]["new"] == "M11~M12"
    assert m["m-12 櫃"]["status"] == "auto" and m["m-12 櫃"]["new"] == "M12"
    assert m["Z99"]["status"] == "unmatched" and "(VM)" not in m
    with pytest.raises(ValueError):
        cia_rack.add_alias(conn, ROOM, "Z99", "Q1", None, None, "a")       # 不在清冊
    with pytest.raises(ValueError):
        cia_rack.add_alias(conn, ROOM, "Z99", None, None, "", "a")         # 不處理要原因
    cia_rack.add_alias(conn, ROOM, "Z99", "M11", None, None, "a")
    cia_rack.add_alias(conn, ROOM, "Z99", "M12", None, None, "a")          # 改選：最新一筆生效
    m = {x["raw"]: x for x in cia_rack.mapping(conn, ROOM)["items"]}
    assert m["Z99"]["status"] == "manual" and m["Z99"]["new"] == "M12" and "對照表第" in m["Z99"]["why"]
    assert conn.execute("SELECT COUNT(*) FROM cia_rack_alias").fetchone()[0] == 2


def test_一格一案_交出每批限一個機房(conn):
    cia_rack.load_roster(conn, ROOM, "a")
    cia_rack.approve_roster(conn, ROOM, 1, "a")
    cia_rack.scan(conn, "a")
    cs = cia_case.list_cases(conn, "rack")
    assert {c["old_value"] for c in cs} == {"7F-M11~M12", "m-12 櫃"} and all(c["room"] == ROOM for c in cs)
    with pytest.raises(ValueError):
        cia_rack.handoff(conn, "", "a")
    cia_case.act(conn, [c["id"] for c in cs], "confirm", "a")
    r = cia_rack.handoff(conn, ROOM, "a")
    assert r["batch_id"] and r["cases"] == 2
    keys = {(i["asset_serial"], i["old_value"], i["new_value"], i["rule_label"]) for i in cia_fix.list_items(conn, batch_id=r["batch_id"])}
    assert keys == {("S2", "7F-M11~M12", "M11~M12", "機櫃編號統一"), ("S3", "m-12 櫃", "M12", "機櫃編號統一")}
    vn.FAILS = 0
    vn.check_cia_case(conn)
    vn.check_cia_rack(conn)
    assert vn.FAILS == 0
    # 突變：同一批混到別的機房 → 紅
    cid = cs[0]["id"]
    conn.execute("UPDATE cia_case SET room = '乙機房' WHERE id = ?", (cid,))
    conn.commit()
    vn.FAILS = 0
    vn.check_cia_rack(conn)
    assert vn.FAILS > 0
    vn.FAILS = 0



def test_審查P1_確認後改對照再重掃_不會交出沒確認的值(conn):
    cia_rack.load_roster(conn, ROOM, "a")
    cia_rack.approve_roster(conn, ROOM, 1, "a")
    cia_rack.add_alias(conn, ROOM, "Z99", "M11", None, None, "a")          # Z99 → M11
    cia_rack.scan(conn, "a")
    z = next(c for c in cia_case.list_cases(conn, "rack") if c["old_value"] == "Z99")
    cia_case.act(conn, [z["id"]], "confirm", "a")
    assert cia_case.list_cases(conn, "rack", ids=[z["id"]])[0]["decision"]["new"] == "M11"
    cia_rack.add_alias(conn, ROOM, "Z99", "M12", None, None, "a")          # 改成 M12、重掃
    cia_rack.scan(conn, "a")
    z2 = cia_case.list_cases(conn, "rack", ids=[z["id"]])[0]
    assert z2["status"] == "confirmed" and z2["detail"]["new"] == "M12" and z2["decision"]["new"] == "M11"
    r = cia_rack.handoff(conn, ROOM, "a")
    assert r["batch_id"] is None
    assert any(s["id"] == z["id"] and "對照已改" in s["why"] for s in r["skipped"])


def test_審查P3_7_退役不計入對照(conn):
    db.insert_hardware(conn, asset_serial="S20", hostname="old", ip="10.99.1.20", physical_location=ROOM, rack_no="Q77", asset_status="報廢")
    conn.commit()
    cia_rack.load_roster(conn, ROOM, "a")
    cia_rack.approve_roster(conn, ROOM, 1, "a")
    assert "Q77" not in {x["raw"] for x in cia_rack.mapping(conn, ROOM)["items"]}
