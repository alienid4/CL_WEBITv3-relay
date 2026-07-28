"""CMDB Gateway client：回應解析 + 連線設定讀取（不打真網路，確定性）。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"))

import cmdb_gateway  # noqa: E402
import db  # noqa: E402


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    c = db.get_connection()
    yield c
    c.close()


def test_extract_items_容錯各種回應結構():
    assert cmdb_gateway.extract_items([{"SN": "1"}]) == [{"SN": "1"}]
    assert cmdb_gateway.extract_items({"items": [{"SN": "2"}]}) == [{"SN": "2"}]
    assert cmdb_gateway.extract_items({"data": {"items": [{"SN": "3"}]}}) == [{"SN": "3"}]
    assert cmdb_gateway.extract_items({"assets": [{"SN": "4"}]}) == [{"SN": "4"}]
    assert cmdb_gateway.extract_items({"其他": 1}) == []


def test_seen_fields_列出出現過的欄位():
    items = [{"SN": "1", "Name": "a"}, {"Name": "b", "Category": "c"}]
    assert cmdb_gateway.seen_fields(items) == ["Category", "Name", "SN"]


def test_沒設定gateway連線要raise(conn):
    with pytest.raises(ConnectionError):
        cmdb_gateway._endpoint(conn)


def test_設定後從連線讀出URL與token(conn):
    db.create_connection_record(
        conn, "CMDB讀", cmdb_gateway.GATEWAY_TYPE, "http://10.93.18.35:3001/", None, None, "tok123"
    )
    base, token = cmdb_gateway._endpoint(conn)
    assert base == "http://10.93.18.35:3001"  # 尾斜線被去掉
    assert token == "tok123"
