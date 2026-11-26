"""S3 同機多筆合併（2.061.1）。全部假資料（IP 一律 10.99.x）。

母體（machine_key 同一台、只算 CIA 正本、排除堆疊／機箱元件）／逐欄建議（平手不建議）／逐案確認（每欄都要選）／
交出成 9.3 修正包（WEBIT3 不刪任何一筆）／CIA 匯入後核對整筆動作（消失或退役＝已套用）。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import cia_case  # noqa: E402
import cia_dup  # noqa: E402
import cia_fix  # noqa: E402
import db  # noqa: E402
import verify_numbers as vn  # noqa: E402


def _seed(c):
    hw = [
        dict(asset_serial="S1", hostname="h1", ip="10.99.1.1", api_id="AP1", asset_name="甲", owner="王", environment="正式",
             inventory_department="部A", group_name="G1", os="RHEL"),
        dict(asset_serial="S2", hostname="h1", ip="10.99.1.1", api_id="AP1", asset_name="甲", owner="", environment="測試",
             inventory_department="部A", group_name="G1"),
        dict(asset_serial="S3", hostname="h1", ip="10.99.1.1", api_id="AP1", asset_name="甲", owner="", environment="正式",
             inventory_department="部A", group_name="G1"),
        dict(asset_serial="DYN-1", hostname="h2", ip="10.99.2.2"),          # 帳外＋一筆 CIA：不成組
        dict(asset_serial="S4", hostname="h2", ip="10.99.2.2"),
        # 堆疊：同一管理 IP、型號像堆疊、序號不同 → 不列
        dict(asset_serial="S5", hostname="sw1", ip="10.99.3.3", device_model="Switch Stack", hardware_no="HW-A"),
        dict(asset_serial="S6", hostname="sw1", ip="10.99.3.3", device_model="Switch Stack", hardware_no="HW-B"),
        dict(asset_serial="S7", hostname="solo", ip="10.99.4.4"),
    ]
    for h in hw:
        db.insert_hardware(c, **h)
    c.commit()


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    _seed(c)
    yield c
    c.close()


def test_母體_只算CIA正本_排除堆疊(conn):
    cs = cia_dup.candidates(conn)
    assert [c["old_value"] for c in cs] == ["S1,S2,S3"]
    d = cs[0]["detail"]
    assert cs[0]["match_basis"]["hostnames"] == ["h1"] and cs[0]["match_basis"]["mode"]
    by = {x["field"]: x for x in d["diffs"]}
    assert set(by) >= {"owner", "environment", "os"} and "api_id" not in by
    assert by["owner"]["suggest"] == "S1" and by["owner"]["why"] == "只有這筆有值"
    assert by["environment"]["suggest_value"] == "正式" and by["environment"]["why"] == "多數筆是這個值"
    assert d["keep"] == "S1" and d["keep_why"] == "欄位最齊"


def test_逐欄平手不建議():
    labels = {"owner": "擁有者", "os": "作業系統"}
    a = cia_dup.analyze_group([{"asset_serial": "A", "owner": "甲", "os": "x"}, {"asset_serial": "B", "owner": "乙", "os": "y"}], labels)
    assert all(x["suggest"] is None for x in a["diffs"]) and a["keep"] == "A" and a["keep_why"].startswith("欄位一樣齊")


def test_確認要每欄都選_交出成修正包_核對整筆動作(conn):
    cia_dup.scan(conn, "t")
    case = cia_case.list_cases(conn, "dup")[0]
    diffs = case["detail"]["diffs"]
    with pytest.raises(ValueError):
        cia_dup.confirm(conn, case["id"], "S1", {}, None, "a")             # 沒選欄
    with pytest.raises(ValueError):
        cia_dup.confirm(conn, case["id"], "S9", {x["field"]: "S1" for x in diffs}, None, "a")
    pick = {x["field"]: (x["suggest"] or "S1") for x in diffs}
    pick["environment"] = "S2"                                              # 人改選：環境取 S2（測試）
    assert cia_dup.confirm(conn, case["id"], "S1", pick, None, "a")["updated"] == 1
    r = cia_dup.handoff(conn, "a")
    assert r["batch_id"] and r["cases"] == 1
    items = cia_fix.list_items(conn, batch_id=r["batch_id"])
    keys = {(i["asset_serial"], i["field"], i["old_value"], i["new_value"]) for i in items}
    assert ("S1", "environment", "正式", "測試") in keys
    assert ("S2", "_dup_merge", "S2", "停用或併入 S1") in keys and ("S3", "_dup_merge", "S3", "停用或併入 S1") in keys
    assert {i["rule_label"] for i in items} == {"同機多筆：欄位取值", "同機多筆：停用或併入"}
    assert all(i["field_label"] for i in items)
    # WEBIT3 沒刪任何一筆
    assert conn.execute("SELECT COUNT(*) FROM hardware WHERE asset_serial IN ('S1','S2','S3')").fetchone()[0] == 3
    # 模擬 CIA 改好再匯入：S1 環境改了、S2 刪掉、S3 變退役
    conn.execute("UPDATE hardware SET environment = '測試' WHERE asset_serial = 'S1'")
    conn.execute("DELETE FROM hardware WHERE asset_serial = 'S2'")
    conn.execute("UPDATE hardware SET asset_status = '報廢' WHERE asset_serial = 'S3'")
    conn.commit()
    res = cia_fix.reconcile_after_import(conn, "cia.xlsx", "x" * 64, "t")
    assert res["applied"] == 3
    cia_case.settle(conn, "t")
    assert cia_case.list_cases(conn, "dup", ids=[case["id"]])[0]["status"] == "closed"


def test_確認後登記變了_交出時擋下要重新比對(conn):
    cia_dup.scan(conn, "t")
    case = cia_case.list_cases(conn, "dup")[0]
    cia_dup.confirm(conn, case["id"], "S1", {x["field"]: "S1" for x in case["detail"]["diffs"]}, None, "a")
    conn.execute("DELETE FROM hardware WHERE asset_serial = 'S3'")
    conn.commit()
    r = cia_dup.handoff(conn, "a")
    assert r["batch_id"] is None and "重新比對" in r["skipped"][0]["why"]


def test_verify_numbers_通過(conn):
    cia_dup.scan(conn, "t")
    vn.FAILS = 0
    vn.check_cia_case(conn)
    vn.check_cia_fix(conn)
    assert vn.FAILS == 0



def test_審查P2_2_交出用確認時的值_來源改了就擋(conn):
    cia_dup.scan(conn, "t")
    case = cia_case.list_cases(conn, "dup")[0]
    pick = {x["field"]: (x["suggest"] or "S1") for x in case["detail"]["diffs"]}
    pick["environment"] = "S2"
    cia_dup.confirm(conn, case["id"], "S1", pick, None, "a")
    dec = cia_case.list_cases(conn, "dup", ids=[case["id"]])[0]["decision"]
    assert dec["values"]["environment"] == "測試" and dec["keep_values"]["environment"] == "正式"
    # 確認之後 S2 的環境被改了：不能把沒人確認過的新值送出去
    conn.execute("UPDATE hardware SET environment = 'DR' WHERE asset_serial = 'S2'")
    conn.commit()
    r = cia_dup.handoff(conn, "a")
    assert r["batch_id"] is None and "不一樣" in r["skipped"][0]["why"]


def test_確認前CIA已變_要先重新比對(conn):
    cia_dup.scan(conn, "t")
    case = cia_case.list_cases(conn, "dup")[0]
    conn.execute("UPDATE hardware SET owner = '李' WHERE asset_serial = 'S2'")
    conn.commit()
    with pytest.raises(ValueError):
        cia_dup.confirm(conn, case["id"], "S1", {x["field"]: "S1" for x in case["detail"]["diffs"]}, None, "a")


def test_分類存在案件上_可篩(conn):
    cia_dup.scan(conn, "t")
    c = cia_case.list_cases(conn, "dup")[0]
    assert c["category"] == c["detail"]["dup_kind"]
    assert len(cia_case.list_cases(conn, "dup", category=c["category"])) == 1
    assert cia_case.counts(conn, "dup", category="nope")["total"] == 0


def test_審查P3_畫面開著時重新比對過_確認擋下(conn):
    cia_dup.scan(conn, "t")
    case = cia_case.list_cases(conn, "dup")[0]
    pick = {x["field"]: "S1" for x in case["detail"]["diffs"]}
    with pytest.raises(ValueError, match="重新整理"):
        cia_dup.confirm(conn, case["id"], "S1", pick, None, "a", seen_last="2000-01-01 00:00:00")
    assert cia_dup.confirm(conn, case["id"], "S1", pick, None, "a", seen_last=case["last_seen"])["updated"] == 1
