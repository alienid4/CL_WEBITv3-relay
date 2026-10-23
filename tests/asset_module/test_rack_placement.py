"""上架助手：連續空 U、用電／重量上限、U 不確定排後面、預留佔 U、建議不重疊。"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import rack_layout as rl  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _hw(conn, serial, rack, model="Dell R750", status="使用中"):
    db.insert_hardware(conn, asset_serial=serial, hostname=serial.lower(), ip=f"10.99.9.{len(serial) + ord(serial[-1]) % 200}",
                       physical_location="01_甲機房", rack_no=rack, device_model=model, api_id="N-920", asset_name="上架系統")
    conn.execute("UPDATE hardware SET asset_status = ? WHERE asset_serial = ?", (status, serial))


def _seed(conn):
    # A1：1-38U 滿（38 台 1U），剩 39-42 → 4U 連續
    for i in range(1, 39):
        _hw(conn, f"A{i:02d}", f"A1/{i}U", "Cisco C9200L-48T-4X")
    # A2：只有 1 台在 20U，另一台沒寫 U（空 U 不確定）
    _hw(conn, "B01", "A2/20U")
    _hw(conn, "B02", "A2")
    # A3：關機未下架佔 10-11U
    _hw(conn, "C01", "A3/10-11U", status="停用")
    conn.commit()


def test_找得下的機櫃_最緊的優先_原因(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        r = rl.placement(conn, u=4, qty=1, w=100, kg=20, rooms=["甲機房"])      # A1 已有 38×125 W，再 100 W 剛好在 5000 W 內
        by = {x["rack"]: x for x in r["racks"]}
        assert by["A1"]["fits"] and (by["A1"]["suggest_u_start"], by["A1"]["leftover"]) == (39, 0)
        assert r["racks"][0]["rack"] == "A1"                                  # 剛好塞滿 → 排第一
        assert by["A2"]["uncertain_u"] and any("沒寫 U 位" in x for x in by["A2"]["reasons"])
        assert [x["rack"] for x in r["racks"]].index("A2") > [x["rack"] for x in r["racks"]].index("A3")
        assert 10 not in range(by["A3"]["suggest_u_start"], by["A3"]["suggest_u_end"] + 1)  # 關機的也佔 U
        big = rl.placement(conn, u=5, qty=1, rooms=["甲機房"])
        a1 = next(x for x in big["racks"] if x["rack"] == "A1")
        assert not a1["fits"] and a1["fit_count"] == 0
    finally:
        conn.close()


def test_用電與重量上限(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES ('rack_budget', ?)",
                     (json.dumps({"default": {"w": 5000, "kg": None}, "racks": {"甲機房/A2": {"w": 3000, "kg": 50}}}),))
        conn.commit()
        r = {x["rack"]: x for x in rl.placement(conn, u=2, qty=1, w=800, kg=30, rooms=["甲機房"])["racks"]}
        a2 = r["A2"]                                                           # 已有 R750 2800 W（B02 未寫 U 也在用）
        assert a2["w_ok"] is False and not a2["fits"] and a2["kg_ok"] is False
        assert r["A3"]["kg_budget"] is None and r["A3"]["kg_ok"] is None        # 未設定上限
    finally:
        conn.close()


def test_預留佔U_重疊警告_取消_建議不重疊(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        res = rl.reserve(conn, "甲機房", "A1", 39, 2, "新防火牆", "2099-12-31", "tester")
        assert res["collisions"] == []
        res2 = rl.reserve(conn, "甲機房", "A1", 40, 1, "重疊測試", "2099-12-31", "tester")
        assert [c["name"] for c in res2["collisions"]] == ["預留：新防火牆"]
        rl.close_reservation(conn, res2["id"], "tester")
        a1 = next(x for x in rl.placement(conn, 2, 1, rooms=["甲機房"])["racks"] if x["rack"] == "A1")
        assert (a1["suggest_u_start"], a1["reserved_u"]) == (41, 2)
        det = rl.rack_detail(conn, "甲機房", "A1")
        assert [x["label"] for x in det["reservations"]] == ["新防火牆"]
        with pytest.raises(ValueError):
            rl.reserve(conn, "甲機房", "A1", 39, 1, "x", "明天", "tester")
        with pytest.raises(ValueError):
            rl.close_reservation(conn, res2["id"], "tester")
        for s in rl.placement(conn, 1, 1)["racks"]:
            if s["suggest_u_start"]:
                assert s["suggest_u_start"] not in s["occupied"]
        assert [a["asset_serial"] for a in rl.find_assets(conn, "b01")] == ["B01"]
    finally:
        conn.close()
