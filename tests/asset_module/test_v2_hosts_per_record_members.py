"""Codex 69／003：對帳橋頁籤（實體設備）「顯示每筆登記」必須展開本頁單位的**全部成員**（同箱第二筆等），
不灌全庫、不改單位總數；搜尋要能用非代表成員找到該單位；管理口／VM 不混進實體。用真的 v2_hosts.listing＋count_bridge 完整假庫。"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(HERE))

import count_bridge as cb  # noqa: E402
import test_count_bridge as tcb  # noqa: E402
import v2_hosts  # noqa: E402


def _members_of_physical(conn):
    items = cb.compute(conn)["items"]
    units = v2_hosts.bridge_units(items, v2_hosts.BRIDGE_TABS["physical"])
    return units, {x["asset_serial"] for u in units for x in u["items"]}


def test_實體每筆展開含同箱全部成員_單位總數不變(tmp_path):
    conn, _ = tcb._conn(tmp_path)
    units, member_serials = _members_of_physical(conn)
    res = v2_hosts.listing(conn, "physical", per_record=True, size=0)
    assert res["total"] == len(units) == len(res["rows"])                       # 單位數不被改成成員數
    rec_serials = {x["serial"] for x in res["records"]}
    assert member_serials <= rec_serials                                         # 所有成員（含 LP2／EX2）都在
    assert {"HW-LP2", "HW-EX2"} <= rec_serials
    # 不灌全庫：管理口、VM、退役不混進實體每筆
    assert not ({"HW-ILO", "HW-VM", "HW-OLD", "DYN-VM1"} & rec_serials)
    assert len(rec_serials) < len(tcb.EXPECT) + 20


def test_分頁只展開本頁單位的成員(tmp_path):
    conn, _ = tcb._conn(tmp_path)
    full = v2_hosts.listing(conn, "physical", per_record=True, size=0)
    seen = set()
    for pg in range(1, full["total"] + 1):
        r = v2_hosts.listing(conn, "physical", per_record=True, page=pg, size=1)
        assert len(r["rows"]) == 1
        row = r["rows"][0]
        keys = {x["key"] for x in r["records"]}
        assert row["key"] in keys
        # 本頁每筆都屬於這個單位（同箱單位 unit_records 筆；單筆單位 ≥1 筆登記）
        assert len(keys) <= max(row["unit_records"], 1) + 1
        seen |= {x["serial"] for x in r["records"]}
    assert {"HW-LP2", "HW-EX2"} <= seen                                          # 逐頁走完也一個不漏


def test_搜尋非代表成員可定位單位(tmp_path):
    conn, _ = tcb._conn(tmp_path)
    units, _m = _members_of_physical(conn)
    two = [u for u in units if len(u["items"]) >= 2]
    assert two, "fixture 應有同箱單位"
    u = two[0]
    rep_serial = u["items"][0]["asset_serial"]
    other = next(x for x in u["items"] if x["asset_serial"] != rep_serial)
    for term in (other["asset_serial"], other["hostname"]):
        r = v2_hosts.listing(conn, "physical", q=term, per_record=True, size=0)
        assert r["total"] >= 1
        assert any(row["uid"] == u["unit"] for row in r["rows"]), term           # 用非代表成員搜得到該單位
        assert other["asset_serial"] in {x["serial"] for x in r["records"]}      # 且該筆有展開


def test_全部頁籤維持原逐台關係(tmp_path):
    conn, _ = tcb._conn(tmp_path)
    r = v2_hosts.listing(conn, "all", per_record=True, size=0)
    assert r["records"] and all(x["key"] in {row["key"] for row in r["rows"]} for x in r["records"])


def test_管理口與VM頁籤也展開自己的成員且不混入實體(tmp_path):
    conn, _ = tcb._conn(tmp_path)
    for tab in ("mgmt", "vm"):
        r = v2_hosts.listing(conn, tab, per_record=True, size=0)
        assert r["total"] == len(r["rows"])
        assert not ({"HW-LP2", "HW-EX2"} & {x["serial"] for x in r["records"]})


def test_實體設備清單帶U位欄_幾U_無法解析_未填(tmp_path):
    conn, _ = tcb._conn(tmp_path)
    rows = {r["name"]: r for r in v2_hosts.listing(conn, "physical", size=0)["rows"]}
    assert rows["exa1"]["u_text"] == "1–10U（共 10U）"
    assert rows["rackpc"]["u_text"] == "30U"
    assert rows["rackpc2"]["u_text"] == "U 位無法解析"        # 機櫃欄填了但解析不出 U
    assert rows["lonely"]["u_text"] == "未填"                 # 沒有機櫃資料
    total = v2_hosts.listing(conn, "physical", size=0)["total"]
    assert total == len(rows)                                 # 只加欄位，單位數不變
