"""網路設備：主機名後面的廠牌圖示的後端判斷（host_kind.net_vendor_view，2026-10-21）。假資料。"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import host_kind  # noqa: E402


def _db(model):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE hardware (ip TEXT, os TEXT, device_model TEXT, asset_name TEXT, mac TEXT, asset_status TEXT)")
    conn.execute("INSERT INTO hardware VALUES ('10.99.0.9', NULL, ?, NULL, NULL, '使用中')", (model,))
    return conn


def test_登記機型認得出廠牌_依據寫出來():
    v = host_kind.net_vendor_view(_db("Cisco Catalyst 9300-48P"), "10.99.0.9")
    assert v["vendor"] == "Cisco" and v["evidence"] and "資產登記" in v["evidence"][0]
    assert host_kind.net_vendor_view(_db("BIG-IP i5800"), "10.99.0.9")["vendor"] == "F5"


def test_認不出廠牌_回不知道不是空白():
    v = host_kind.net_vendor_view(_db("XYZ-48"), "10.99.0.9")
    assert v["vendor"] is None and v["evidence"]
    v = host_kind.net_vendor_view(_db("x"), None)
    assert v["vendor"] is None
