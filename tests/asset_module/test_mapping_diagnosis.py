"""分佈統計「涵蓋 0 台」時要講得出原因（2026-09-14 公司機）。

對照表 169 筆、資產 4,155 台，分佈統計卻一台都對不上。原因有好幾種、要做的事不同，
只給 0 會讓人只能猜。
"""
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import system_stats as ss  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "t.db"
    db.init_db(path)
    c = sqlite3.connect(str(path))
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def _bs(c, *codes):
    for code in codes:
        c.execute("INSERT INTO business_system (api_id, name) VALUES (?, ?)", (code, "x"))
    c.commit()


def _hw(c, serial, api_id):
    db.insert_hardware(c, asset_serial=serial, api_id=api_id)


def test_對得上就不給原因(conn):
    _bs(conn, "N-001")
    _hw(conn, "HW-1", "N-001")
    out = ss.by_system(conn, limit=None)
    assert out["mapping"] is None


def test_對照表空的(conn):
    _hw(conn, "HW-1", "N-001")
    d = ss.by_system(conn, limit=None)["mapping"]
    assert "對照表是空的" in d["reason"]


def test_資產AP_ID全空白(conn):
    _bs(conn, "N-001")
    _hw(conn, "HW-1", None)
    d = ss.by_system(conn, limit=None)["mapping"]
    assert "全部空白" in d["reason"]


def test_只差空白大小寫(conn):
    _bs(conn, "n-001 ")
    _hw(conn, "HW-1", "N-001")
    d = ss.by_system(conn, limit=None)["mapping"]
    assert "空白或大小寫" in d["reason"]


def test_兩邊代碼完全不一樣_附樣本(conn):
    _bs(conn, "12", "13")
    _hw(conn, "HW-1", "N-001")
    d = ss.by_system(conn, limit=None)["mapping"]
    assert "抓錯欄" in d["reason"]
    assert d["sample_table_codes"] == ["12", "13"]
    assert d["sample_asset_codes"] == ["N-001"]
