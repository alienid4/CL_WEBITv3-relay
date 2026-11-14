"""S4 未結案清單（2.061.3）。全部假資料（IP 一律 10.99.x）。

卡片＝下鑽（新案／逾期／例外到期／全部）／逾期退回重算（看最後一次交出的時間）／例外沒到期不列、到期才列／
事件紀錄帶在每一列（稽核）／涵蓋範圍分母固定＝2.1 全部登記台數。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import cia_case  # noqa: E402
import cia_fix  # noqa: E402
import cia_open  # noqa: E402
import db  # noqa: E402
import verify_numbers as vn  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.insert_hardware(c, asset_serial="S1", hostname="h1", ip="10.99.1.1", physical_location="甲 機房", inventory_department="部A", group_name="G1")
    db.insert_hardware(c, asset_serial="S2", hostname="h2", ip="10.99.1.2", inventory_department="部A", group_name="G1")
    c.commit()
    cia_fix.save_settings(c, {"甲 機房": "01_甲機房"}, [])
    yield c
    c.close()


def _cand(mk, title):
    return {"machine_key": mk, "field": "*", "old_value": mk, "title": title}


def test_沒有任何資料_全部是零_不建表(conn):
    s = cia_open.summary(conn)
    assert s["counts"] == {"all": 0, "new": 0, "overdue": 0, "exc_due": 0}
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='cia_case'").fetchone()


def test_新案_逾期_例外到期(conn):
    pv = cia_fix.preview(conn)
    cia_fix.create_batch(conn, "t", pv["fingerprint"])
    cia_case.sync(conn, "dup", [_cand("h:a", "A"), _cand("h:b", "B"), _cand("h:c", "C")], "t")
    a, b, c = [x["id"] for x in cia_case.list_cases(conn, "dup")]
    cia_case.act(conn, [b], "except", "t", note="不是同一台")
    conn.execute("UPDATE cia_case SET exception_until = '2000-01-01' WHERE id = ?", (b,))          # 例外已到期
    conn.execute("UPDATE cia_case SET status = 'handed', handed_at = '2000-01-01 00:00:00' WHERE id = ?", (c,))   # 交出很久
    conn.execute("INSERT INTO cia_case_event (case_id, at, by, action, from_status, to_status) VALUES (?, '2000-01-01', 't', 'handed', 'confirmed', 'handed')", (c,))
    conn.commit()
    assert cia_case.expire_exceptions(conn, "t") == 1                                          # 讀取前結算：B 回待確認
    s = cia_open.summary(conn)
    rs, _ = cia_open.rows(conn)
    assert s["counts"]["exc_due"] == 1 and cia_open.pick(rs, "exc_due")[0]["name"] == "B"
    assert s["counts"]["overdue"] == 1 and cia_open.pick(rs, "overdue")[0]["name"] == "C"
    assert s["by_source"]["9.3 修正包"] == 1 and s["by_source"]["同機多筆"] == 3          # A、B（到期回待確認）、C
    assert s["counts"]["new"] == s["counts"]["all"]                                       # 都是今天第一次出現
    later = datetime.now() + timedelta(days=30)
    assert cia_open.summary(conn, later)["counts"]["new"] == 0
    # 事件紀錄帶在列上
    fix_row = next(r for r in rs if r["source"] == "9.3 修正包")
    assert "產生" in fix_row["trail"]
    # 逾期天數可設
    cia_open.save_overdue_days(conn, 99999 // 1000)
    with pytest.raises(ValueError):
        cia_open.save_overdue_days(conn, 0)


def test_例外沒到期不列(conn):  # noqa: D103
    cia_case.sync(conn, "dup", [_cand("h:a", "A")], "t")
    cia_case.act(conn, [cia_case.list_cases(conn, "dup")[0]["id"]], "except", "t", note="不處理")
    rs, _ = cia_open.rows(conn)
    assert rs == []


def test_涵蓋範圍分母固定(conn):
    cov = cia_open.coverage(conn)
    assert cov["total"] >= 2
    for x in cov["sources"]:
        if x["covered"] is not None:
            assert x["covered"] + x["uncovered"] == cov["total"]
    assert any(x["source"] == "WhatsUp" and x["covered"] is None for x in cov["sources"])


def test_verify_numbers_通過(conn):
    pv = cia_fix.preview(conn)
    cia_fix.create_batch(conn, "t", pv["fingerprint"])
    cia_case.sync(conn, "dup", [_cand("h:a", "A")], "t")
    vn.FAILS = 0
    vn.check_cia_open(conn)
    assert vn.FAILS == 0
