"""VIP 分列登記：台數照主機算、重複分三類、入口查詢（2026-09-14）。

CIA 清冊是「每個服務／VIP 一筆」：一台 VM 可能登記好幾筆（不同系統 × 不同 VIP）。
要守的：
1. 同一台在同一個系統底下只算一台，但筆數也看得到（一筆都不少）
2. 同主機同 IP 多筆，只有「組合完全一樣」才叫重複、才給可少筆數
3. VIP 欄的「無」「N/A」當空白；入口查詢查得到「這個 VIP 後面是哪幾台」
"""
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import system_stats as ss  # noqa: E402
import vip_view as vv  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "t.db"
    db.init_db(path)
    c = sqlite3.connect(str(path))
    c.row_factory = sqlite3.Row
    for api, name in (("N-001", "甲系統"), ("N-002", "乙系統")):
        c.execute("INSERT INTO business_system (api_id, name) VALUES (?, ?)", (api, name))
    c.commit()
    yield c
    c.close()


def _hw(c, serial, **kw):
    db.insert_hardware(c, asset_serial=serial, environment=kw.pop("environment", "正式"), **kw)


def _seed_one_vm_many_rows(c):
    # 一台 VM 登記 3 筆：甲系統兩個 VIP、乙系統一個 VIP
    _hw(c, "HW-01", hostname="EXAMPLE-A", ip="192.0.2.1", api_id="N-001", big_ip_vip="192.0.2.28")
    _hw(c, "HW-02", hostname="example-a", ip="192.0.2.1", api_id="N-001", big_ip_vip="192.0.2.34")
    _hw(c, "HW-03", hostname="example-a", ip="192.0.2.1", api_id="N-002", big_ip_vip="192.0.2.28")
    _hw(c, "HW-04", hostname="example-b", ip="192.0.2.2", api_id="N-001", big_ip_vip="無")
    # 沒主機名：無法判斷是不是同一台，各算一台
    _hw(c, "HW-05", ip="192.0.2.3", api_id="N-001", big_ip_vip="N/A")


# ---------- VIP 正規化 ----------

@pytest.mark.parametrize("raw", [None, "", "  ", "無", "N/A", "na", "-", "None"])
def test_無和NA都當空白(raw):
    assert vv.normalize_vip(raw) == ""
    assert vv.parse_vip(raw)["kind"] is None


def test_VIP欄混的東西要分得出來_不硬塞成入口():
    assert vv.parse_vip("192.0.2.28")["kind"] == vv.KIND_VIP
    p = vv.parse_vip("192.0.2.168-s-cluster")
    assert p["kind"] == vv.KIND_CLUSTER and p["ips"] == ["192.0.2.168"]
    assert vv.parse_vip("N-202")["kind"] == vv.KIND_APID
    assert vv.parse_vip("EXAMPLEHOST01")["kind"] == vv.KIND_HOSTNAME


# ---------- 重複分三類 ----------

def test_一台多系統_不是重複_不給可少():
    rows = [{"api_id": "N-001", "big_ip_vip": "192.0.2.28"},
            {"api_id": "N-002", "big_ip_vip": "192.0.2.28"}]
    c = vv.classify_duplicate_group(rows)
    assert c["kind"] == vv.DUP_MULTI_SYSTEM and c["extra_rows"] == 0


def test_同系統不同VIP_是分列不是重複():
    rows = [{"api_id": "N-001", "big_ip_vip": "192.0.2.28"},
            {"api_id": "N-001", "big_ip_vip": "192.0.2.34"}]
    c = vv.classify_duplicate_group(rows)
    assert c["kind"] == vv.DUP_SPLIT and c["extra_rows"] == 0


def test_組合完全一樣才是疑似真重複_只少掉一樣的那幾筆():
    rows = [{"api_id": "N-001", "big_ip_vip": "192.0.2.28", "asset_purpose": "官網"},
            {"api_id": "N-001", "big_ip_vip": "192.0.2.28", "asset_purpose": "官網"},
            {"api_id": "N-001", "big_ip_vip": "192.0.2.34", "asset_purpose": "官網"}]
    c = vv.classify_duplicate_group(rows)
    assert c["kind"] == vv.DUP_SUSPECT and c["extra_rows"] == 1


def test_VIP寫無跟空白_算同一個組合():
    rows = [{"api_id": "N-001", "big_ip_vip": "無"}, {"api_id": "N-001", "big_ip_vip": None}]
    assert vv.classify_duplicate_group(rows)["kind"] == vv.DUP_SUSPECT


def test_儀表板彙總只算疑似真重複(conn):
    _seed_one_vm_many_rows(conn)
    _hw(conn, "HW-10", hostname="example-c", ip="192.0.2.9", api_id="N-001")
    _hw(conn, "HW-11", hostname="example-c", ip="192.0.2.9", api_id="N-001")
    s = vv.duplicate_summary(conn)
    assert s["groups"] == 2
    assert s["suspect_groups"] == 1 and s["suspect_extra_rows"] == 1
    assert s["multi_system_groups"] == 1


# ---------- 台數照主機算 ----------

def test_同一台在同系統只算一台_筆數另外給(conn):
    _seed_one_vm_many_rows(conn)
    out = ss.by_system(conn, limit=None)
    by = {s["api_id"]: s for s in out["systems"]}
    assert by["N-001"]["total"] == 3, "example-a 兩筆＋example-b＋沒主機名那筆"
    assert by["N-001"]["rows"] == 4
    assert sum(by["N-001"]["by_env"].values()) == by["N-001"]["total"]
    assert by["N-002"]["total"] == 1
    assert out["total_assets"] == 3 and out["total_rows"] == 5
    assert out["mapped"] + out["unmapped"] == out["total_assets"]


def test_下鑽與明細也是一台一列_附全部序號(conn):
    _seed_one_vm_many_rows(conn)
    d = ss.drilldown(conn, "N-001")
    assert d["total"] == 3 and d["rows"] == 4
    assert sum(r["total"] for r in d["role_matrix"]) == d["total"]
    items = ss.assets_in_cell(conn, "N-001")
    assert len(items) == 3
    a = next(i for i in items if (i["hostname"] or "").lower() == "example-a")
    assert a["row_count"] == 2 and a["serials"] == ["HW-01", "HW-02"]


def test_角色涵蓋率照主機算(conn):
    _seed_one_vm_many_rows(conn)
    cov = ss.role_coverage(conn)
    assert cov["total"] == 3 and cov["rows"] == 5


# ---------- 入口查詢 ----------

def test_入口清單_一個VIP後面幾台幾個系統(conn):
    _seed_one_vm_many_rows(conn)
    _hw(conn, "HW-20", hostname="example-d", ip="192.0.2.4", api_id="N-001", big_ip_vip="N-202")
    out = vv.entries(conn)
    e = {x["vip"]: x for x in out["entries"]}
    assert e["192.0.2.28"]["hosts"] == 1 and e["192.0.2.28"]["systems"] == 2
    assert e["192.0.2.28"]["rows"] == 2
    assert out["empty_rows"] == 2, "無、N/A 當空白"
    assert [o["value"] for o in out["others"]] == ["N-202"], "不是 IP 的值另外列，不吞掉"


def test_入口明細_哪幾台掛哪些系統(conn):
    _seed_one_vm_many_rows(conn)
    d = vv.entry_detail(conn, "192.0.2.28")
    assert d["hosts"] == 1 and d["rows"] == 2
    assert d["machines"][0]["row_count"] == 2
    assert {s["api_id"] for s in d["systems"]} == {"N-001", "N-002"}
    with pytest.raises(LookupError):
        vv.entry_detail(conn, "192.0.2.99")
