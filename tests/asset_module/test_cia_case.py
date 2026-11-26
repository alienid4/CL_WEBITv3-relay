"""CIA 第三批共用案件表（2.061.0）。全部假資料（IP 一律 10.99.x）。

問題指紋（同一問題只有一案）／比對寫入（新案、再次出現、已不存在）／人的動作（確認、撤回＝反向事件、例外要原因與到期日）／
交出成 9.3 修正包批次、修正包結案後案件結案／verify_numbers 不變式（含突變抽查）。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import cia_case  # noqa: E402
import cia_fix  # noqa: E402
import db  # noqa: E402
import verify_numbers as vn  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.insert_hardware(c, asset_serial="S1", hostname="h1", ip="10.99.1.1", inventory_department="部A", group_name="G1")
    db.insert_hardware(c, asset_serial="S2", hostname="h1", ip="10.99.1.1", inventory_department="部A", group_name="G1")
    c.commit()
    yield c
    c.close()


def _cand(mk="h:h1", field="*", old="S1,S2", title="h1"):
    return {"machine_key": mk, "field": field, "old_value": old, "title": title, "match_basis": {"hostname": "h1"}, "detail": {"n": 2}}


def test_指紋_同一問題同一個_欄位或原值不同就不同():
    a = cia_case.fingerprint("dup", "h:h1", "*", "S1,S2")
    assert a == cia_case.fingerprint("dup", "h:h1", "*", "S1,S2")
    assert a != cia_case.fingerprint("dup", "h:h1", "*", "S1,S3")
    assert a != cia_case.fingerprint("rack", "h:h1", "*", "S1,S2")


def test_讀取不建表(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    assert cia_case.list_cases(c, "dup") == [] and cia_case.counts(c, "dup")["total"] == 0
    assert not cia_case._ready(c)


def test_比對_新案_重掃不重複_不見了已不存在_又出現同一案(conn):
    r = cia_case.sync(conn, "dup", [_cand()], "tester")
    assert r["found"] == 1
    r = cia_case.sync(conn, "dup", [_cand()], "tester")
    assert r["found"] == 0 and r["same"] == 1
    cid = cia_case.list_cases(conn, "dup")[0]["id"]
    assert cia_case.sync(conn, "dup", [], "tester")["gone"] == 1
    assert cia_case.list_cases(conn, "dup")[0]["status"] == "resolved"
    assert cia_case.sync(conn, "dup", [_cand()], "tester")["reappear"] == 1
    cs = cia_case.list_cases(conn, "dup")
    assert len(cs) == 1 and cs[0]["id"] == cid and cs[0]["status"] == "open"
    assert cs[0]["match_basis"] == {"hostname": "h1"} and cs[0]["matched_at"]
    assert [e["action"] for e in cia_case.case_events(conn, cid)] == ["found", "gone", "reappear"]


def test_確認_撤回是反向事件_例外要原因與到期日(conn):
    cia_case.sync(conn, "dup", [_cand()], "t")
    cid = cia_case.list_cases(conn, "dup")[0]["id"]
    assert cia_case.act(conn, [cid], "confirm", "a", decision={"keep": "S1"})["updated"] == 1
    assert cia_case.list_cases(conn, "dup")[0]["decision"] == {"keep": "S1"}
    with pytest.raises(ValueError):
        cia_case.act(conn, [cid], "withdraw", "a")            # 沒原因
    assert cia_case.act(conn, [cid], "withdraw", "a", note="選錯")["updated"] == 1
    ev = cia_case.case_events(conn, cid)
    assert [e["action"] for e in ev] == ["found", "confirm", "withdraw"]     # 舊事件還在
    with pytest.raises(ValueError):
        cia_case.act(conn, [cid], "except", "a", note="不是同一台", until="2000-01-01")
    assert cia_case.act(conn, [cid], "except", "a", note="不是同一台")["updated"] == 1
    c = cia_case.list_cases(conn, "dup")[0]
    assert c["status"] == "exception" and c["exception_until"]
    # 不適用的動作跳過、講原因
    r = cia_case.act(conn, [cid], "withdraw", "a", note="x")
    assert r["updated"] == 0 and r["skipped"]
    # 類別不對也跳過
    assert cia_case.act(conn, [cid], "unexcept", "a", note="重審", kind="rack")["updated"] == 0


def _rows(cid):
    return [{"asset_serial": "S2", "hostname": "h1", "unit": "部A", "asset_group": "G1", "field": "_dup_merge",
             "old_value": "S2", "new_value": "併入 S1", "rule_id": "dup_merge", "reason": "同機多筆"}]


def test_交出成修正包_修正包結案後案件結案_被撤回回到已確認(conn):
    cia_case.sync(conn, "dup", [_cand()], "t")
    cid = cia_case.list_cases(conn, "dup")[0]["id"]
    with pytest.raises(ValueError):
        cia_case.hand_to_fix(conn, "dup", [], "x", "a")
    r = cia_case.hand_to_fix(conn, "dup", [(cid, _rows(cid))], "同機多筆", "a")
    assert r["batch_id"] is None and r["skipped"][0]["why"] == "不是已確認"
    cia_case.act(conn, [cid], "confirm", "a", decision={"keep": "S1"})
    r = cia_case.hand_to_fix(conn, "dup", [(cid, _rows(cid))], "同機多筆", "a")
    assert r["batch_id"] and r["items"] == 1 and r["cases"] == 1
    c = cia_case.list_cases(conn, "dup")[0]
    assert c["status"] == "handed" and c["handed_ref"] == f"cia_fix:{r['batch_id']}"
    items = cia_fix.list_items(conn, batch_id=r["batch_id"])
    assert items[0]["rule_id"] == "dup_merge"
    # 修正包被撤回 → 案件回已確認
    cia_fix.withdraw(conn, [items[0]["id"]], "先不送", "a")
    cia_case.settle(conn, "a")
    assert cia_case.list_cases(conn, "dup")[0]["status"] == "confirmed"
    # 重交 → 修正包已套用 → 案件結案
    r2 = cia_case.hand_to_fix(conn, "dup", [(cid, _rows(cid))], "同機多筆", "a")
    it2 = cia_fix.list_items(conn, batch_id=r2["batch_id"])[0]
    conn.execute("UPDATE cia_fix_item SET status = 'applied' WHERE id = ?", (it2["id"],))
    conn.commit()
    assert cia_case.settle(conn, "a") == 1
    assert cia_case.list_cases(conn, "dup")[0]["status"] == "closed"


def test_verify_numbers_不變式通過_改壞會紅(conn):
    cia_case.sync(conn, "dup", [_cand(), _cand(mk="h:h2", old="S5,S6", title="h2")], "t")
    vn.FAILS = 0
    vn.check_cia_case(conn)
    assert vn.FAILS == 0
    conn.execute("UPDATE cia_case SET status = 'confirmed' WHERE id = 1")     # 狀態改了沒寫事件
    conn.commit()
    vn.FAILS = 0
    vn.check_cia_case(conn)
    assert vn.FAILS > 0
    vn.FAILS = 0



def test_審查P2_4_例外到期自動回待確認_有事件(conn):
    cia_case.sync(conn, "dup", [_cand()], "t")
    cid = cia_case.list_cases(conn, "dup")[0]["id"]
    cia_case.act(conn, [cid], "except", "a", note="不處理")
    assert cia_case.expire_exceptions(conn, "t") == 0                     # 還沒到期
    conn.execute("UPDATE cia_case SET exception_until = '2000-01-01' WHERE id = ?", (cid,))
    conn.commit()
    cia_case.settle(conn, "t")
    c = cia_case.list_cases(conn, "dup")[0]
    assert c["status"] == "open" and cia_case.case_events(conn, cid)[-1]["action"] == "expire"


def test_審查P2_4_例外的問題不見了_已不存在(conn):
    cia_case.sync(conn, "dup", [_cand()], "t")
    cid = cia_case.list_cases(conn, "dup")[0]["id"]
    cia_case.act(conn, [cid], "except", "a", note="不處理")
    assert cia_case.sync(conn, "dup", [], "t")["gone"] == 1
    assert cia_case.list_cases(conn, "dup")[0]["status"] == "resolved"


def test_審查P3_6_同機多筆不能用批次確認(conn):
    cia_case.sync(conn, "dup", [_cand()], "t")
    cid = cia_case.list_cases(conn, "dup")[0]["id"]
    r = cia_case.act(conn, [cid], "confirm", "a")
    assert r["updated"] == 0 and "逐案確認" in r["skipped"][0]["why"]


def test_審查P2_3_CIA匯入核對後案件跟著結案(conn):
    cia_case.sync(conn, "dup", [_cand()], "t")
    cid = cia_case.list_cases(conn, "dup")[0]["id"]
    cia_case.act(conn, [cid], "confirm", "a", decision={"keep": "S1"})
    cia_case.hand_to_fix(conn, "dup", [(cid, _rows(cid))], "同機多筆", "a")
    conn.execute("DELETE FROM hardware WHERE asset_serial = 'S2'")
    conn.commit()
    cia_fix.reconcile_after_import(conn, "cia.xlsx", "x" * 64, "t")      # 只呼叫核對，沒有另外叫 settle
    assert cia_case.list_cases(conn, "dup")[0]["status"] == "closed"
