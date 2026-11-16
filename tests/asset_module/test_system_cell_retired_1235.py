"""Codex 1235：系統交叉表「點一格看是哪幾台」不可混入退役機。

格子上的數字（drilldown）早就排掉退役；明細（assets_in_cell）沒排，
點進去會比格子多出報廢機——加總≠下鑽。兩支必須同一個母體。
雙向：退役要被排掉；現役同一台多筆登記仍合成一台、全部序號保留；UAT 照舊併進測試。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import system_stats as ss  # noqa: E402
from lifecycle import RETIRED_STATUS  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "t.db"
    db.init_db(path)
    c = sqlite3.connect(str(path))
    c.row_factory = sqlite3.Row
    c.execute("INSERT INTO business_system (api_id, name, ap_department) VALUES ('N-DEMO','S','D')")
    # 現役同一台登記兩筆（同主機名同 IP）
    db.insert_hardware(c, asset_serial="A1", api_id="N-DEMO", hostname="demo-a", ip="10.99.0.1",
                       physical_location="ROOM-A", environment="正式", asset_status="使用中")
    db.insert_hardware(c, asset_serial="A2", api_id="N-DEMO", hostname="demo-a", ip="10.99.0.1",
                       physical_location="ROOM-A", environment="正式", asset_status="使用中")
    # 退役（報廢）同機房同環境
    retired = sorted(RETIRED_STATUS)[0]
    db.insert_hardware(c, asset_serial="R1", api_id="N-DEMO", hostname="demo-r", ip="10.99.0.9",
                       physical_location="ROOM-A", environment="正式", asset_status=retired)
    # 另一個機房 UAT
    db.insert_hardware(c, asset_serial="B1", api_id="N-DEMO", hostname="demo-b", ip="10.99.0.2",
                       physical_location="ROOM-B", environment="UAT", asset_status="使用中")
    c.commit()
    yield c
    c.close()


def test_退役機不可出現在格子明細(conn):
    items = ss.assets_in_cell(conn, "N-DEMO", env="正式", location="ROOM-A")
    assert [x["hostname"] for x in items] == ["demo-a"]
    assert all("R1" not in x["serials"] for x in ss.assets_in_cell(conn, "N-DEMO"))


def test_現役重複登記仍合成一台且序號全留(conn):
    items = ss.assets_in_cell(conn, "N-DEMO", location="ROOM-A")
    assert len(items) == 1
    assert sorted(items[0]["serials"]) == ["A1", "A2"]
    assert items[0]["row_count"] == 2


def test_UAT仍併進測試(conn):
    items = ss.assets_in_cell(conn, "N-DEMO", env="測試")
    assert [x["hostname"] for x in items] == ["demo-b"]


def test_明細台數等於格子數字(conn):
    dd = ss.drilldown(conn, "N-DEMO")
    assert len(ss.assets_in_cell(conn, "N-DEMO")) == dd["total"] == 2
    for loc in dd["locations"]:
        assert len(ss.assets_in_cell(conn, "N-DEMO", location=loc["location"])) == loc["total"]
    for env in ("正式", "測試"):
        assert len(ss.assets_in_cell(conn, "N-DEMO", env=env)) == ss.drilldown(conn, "N-DEMO", env=env)["total"]
