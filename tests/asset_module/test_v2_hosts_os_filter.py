"""W221-010：9.2 對帳頁「OS 欄有值／掃描推測／未知／平台」→ 2.1 主機查詢下鑽。筆數必須＝對帳頁該列數字（加總＝下鑽）。全部假資料。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import manage_state  # noqa: E402
import v2_hosts  # noqa: E402
import verify_numbers as vn  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    rows = [
        ("S-1", "web01", "10.99.1.1", "Red Hat Enterprise Linux 9", "使用中"),
        ("S-2", "web02", "10.99.1.2", "Windows Server 2019", "使用中"),
        ("S-3", "aix01", "10.99.1.3", "AIX 7.2", "使用中"),
        ("S-4", "na01", "10.99.1.4", "N/A", "使用中"),
        ("S-5", "blank01", "10.99.1.5", "", "使用中"),
        ("S-6", "blank02", "10.99.1.6", None, "使用中"),
        ("S-7", "old01", "10.99.1.7", None, "停用"),            # 退役＋OS 空白：只算進「OS 未知」（母體含退役）
        ("S-8", "old02", "10.99.1.8", "Windows Server 2012", "停用"),
        ("S-9", "web01", "10.99.1.9", "Red Hat Enterprise Linux 9", "使用中"),   # 同名第二筆登記
    ]
    for s, h, ip, os_, st in rows:
        db.insert_hardware(c, asset_serial=s, hostname=h, ip=ip, os=os_, asset_status=st)
    c.commit()
    yield c
    c.close()


def test_篩選筆數_等於對帳頁各列數字(conn):
    comp = manage_state.composition(conn)
    f = lambda **kw: v2_hosts.listing(conn, "all", **kw)       # noqa: E731
    assert f(os_source="facts")["filter_total"] == comp["os_from_facts"] == 4
    assert f(os_source="guessed")["filter_total"] == comp["os_guessed"] == 3        # N/A、空白、None 都算推測
    assert f(os_source="unknown")["filter_total"] == comp["data_quality"]["os_unknown"] == 3   # 含退役那筆
    assert comp["os_from_facts"] + comp["os_guessed"] == comp["total"]
    for k, n in comp["by_os_class"].items():
        assert f(os_class=k)["filter_total"] == n, k
    assert sum(f(os_class=k)["filter_total"] for k in comp["by_os_class"]) == comp["total"]


def test_列內容正確_有項次_退役不混進現役篩選(conn):
    r = v2_hosts.listing(conn, "all", os_source="facts", size=0)
    assert {x["serial"] for x in r["filter_rows"]} == {"S-1", "S-2", "S-3", "S-9"}
    assert [x["no"] for x in r["filter_rows"]] == [1, 2, 3, 4]
    assert all(x["key"] and x["machine"] for x in r["filter_rows"])
    unk = v2_hosts.listing(conn, "all", os_source="unknown", size=0)
    assert {x["serial"] for x in unk["filter_rows"]} == {"S-5", "S-6", "S-7"}


def test_分頁排序與全部匯出一致(conn):
    p1 = v2_hosts.listing(conn, "all", os_source="facts", page=1, size=2, sort="serial", desc=True)
    p2 = v2_hosts.listing(conn, "all", os_source="facts", page=2, size=2, sort="serial", desc=True)
    allr = v2_hosts.listing(conn, "all", os_source="facts", size=0, sort="serial", desc=True)
    assert p1["filter_total"] == 4 and len(p1["filter_rows"]) == 2
    assert [x["serial"] for x in p1["filter_rows"] + p2["filter_rows"]] == [x["serial"] for x in allr["filter_rows"]]


def test_不認得的來源_400行為(conn):
    with pytest.raises(ValueError):
        v2_hosts.listing(conn, "all", os_source="bogus")


def test_沒帶篩選_回應不變(conn):
    r = v2_hosts.listing(conn, "all")
    assert "filter_rows" not in r and "filter" not in r


def test_verify_numbers_新不變式全綠(conn):
    vn.RESULTS.clear()
    vn.check_v2_hosts(conn)
    mine = [x for x in vn.RESULTS if "下鑽" in x["name"] and ("OS" in x["name"] or "平台" in x["name"])]
    assert len(mine) == 4 and all(x["ok"] for x in mine), mine


def test_對帳頁不再連舊版資產清單():
    src = (ROOT / "APP" / "asset-module" / "frontend" / "pages" / "v2" / "reconcile.vue").read_text(encoding="utf-8")
    assert "/assets?os_source" not in src and "/assets?os_class" not in src and "filter_field=os" not in src
    assert "/v2/hosts?os_source=facts" in src and "/v2/hosts?os_class=" in src
