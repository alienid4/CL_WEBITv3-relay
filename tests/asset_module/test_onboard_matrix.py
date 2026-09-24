"""OS × 環境 的納管狀態交叉表（2026-09-16 使用者：「OS 為列、環境為欄，格子放納管」）。

「還有 5244 台要處理」不告訴人該從哪裡下手。補佈納管按 OS 分批做、能不能動看環境，
所以「Linux × 非正式 還有 N 台」才是排得進行程的單位。

要守的：

1. **數字要跟納管漏斗對得起來**——同一個系統裡兩個地方講不同的數字，人就不信了。
   所以吃的是 pipeline.summarize() 那份（已經照主機算、狀態也判好了），不自己再判一次
2. 各格加總 = 列合計 = 欄合計 = 總計（互斥且窮盡）
3. **納管率的分母不含退役與非納管設備**——算進去比率永遠到不了 100%
4. 分母是 0 回 None，不可以回 0%：「沒有機器」跟「有機器但一台都沒納管」完全不同
5. 環境值收成三類＋未填；OS 未知要列出來，不可以靜默丟掉
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import env_group  # noqa: E402
import onboard_matrix as om  # noqa: E402
import pipeline  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    # Linux×正式 2 台、Windows×非正式 1 台、沒填 OS 也沒填環境 1 台、1 台停用（退役）
    db.insert_hardware(c, asset_serial="A-1", hostname="l1", ip="192.0.2.1",
                       os="Red Hat Enterprise Linux 9.4", environment="正式", asset_status="使用中")
    db.insert_hardware(c, asset_serial="A-2", hostname="l2", ip="192.0.2.2",
                       os="RedHat 8.8", environment="備援", asset_status="使用中")
    db.insert_hardware(c, asset_serial="A-3", hostname="w1", ip="192.0.2.3",
                       os="Microsoft Windows Server 2019", environment="測試", asset_status="使用中")
    db.insert_hardware(c, asset_serial="A-4", hostname="x1", ip="192.0.2.4",
                       os=None, environment=None, asset_status="使用中")
    db.insert_hardware(c, asset_serial="A-5", hostname="r1", ip="192.0.2.5",
                       os="RedHat 7.9", environment="正式", asset_status="報廢")
    c.commit()
    try:
        yield c
    finally:
        c.close()


def test_列是OS_欄是環境三分類加未填(conn):
    m = om.build(conn)
    assert [c["key"] for c in m["cols"]] == list(env_group.CHOICES)
    assert m["cols"][-1]["label"] == "未填環境"
    assert "Linux" in m["rows"] and "Windows" in m["rows"]
    assert "未填" in m["rows"], "沒填 OS 的那台要看得到，不可以靜默丟掉（[B-02] 改叫「未填」）"


def test_備援歸正式_測試歸非正式(conn):
    m = om.build(conn)
    # Linux×正式那一格有 3 台：正式 1、備援 1（備援也算正式）、報廢 1（環境仍是正式）
    assert m["cells"]["Linux"][env_group.PROD]["total"] == 3
    assert m["cells"]["Windows"][env_group.NONPROD]["total"] == 1
    assert m["cells"]["未填"][env_group.UNSET]["total"] == 1


def test_各格加總等於列合計欄合計與總計(conn):
    m = om.build(conn)
    col_keys = [c["key"] for c in m["cols"]]
    for os_type in m["rows"]:
        assert sum(m["cells"][os_type][k]["total"] for k in col_keys) == m["row_totals"][os_type]["total"]
    for k in col_keys:
        assert sum(m["cells"][o][k]["total"] for o in m["rows"]) == m["col_totals"][k]["total"]
    assert sum(m["row_totals"][o]["total"] for o in m["rows"]) == m["grand"]["total"]


def test_總數要跟納管漏斗對得起來(conn):
    m = om.build(conn)
    p = pipeline.summarize(conn)
    assert m["grand"]["total"] == p["total"], "兩個地方講不同的數字，人就不信了"


def test_退役不算進分母(conn):
    m = om.build(conn)
    cell = m["cells"]["Linux"][env_group.PROD]
    # 報廢那台是 RedHat 7.9＋正式，也會落在同一格
    assert cell["total"] == 3 and cell["excluded"] == 1
    assert cell["needs_action"] == 2
    # 分母＝3-1=2，一台都沒納管 → 0%
    assert om.coverage(cell) == 0.0


def test_沒有機器的格子回None不是0(conn):
    empty = om._blank_cell()
    assert om.coverage(empty) is None, "「沒有機器」跟「有機器但一台都沒納管」完全不同"
    assert om.coverage({"total": 2, "excluded": 2, "onboarded": 0, "collected": 0,
                        "needs_action": 0, "by_stage": {}}) is None, "全部是退役＝沒有分母"


def test_已收集的設備算顧得到(conn):
    conn.execute("INSERT INTO san_switch (ip, switch_name, collected_at) "
                 "VALUES ('192.0.2.3','SW1','2026-09-16 10:00:00')")
    conn.commit()
    m = om.build(conn)
    cell = m["cells"]["Windows"][env_group.NONPROD]
    assert cell["collected"] == 1 and cell["needs_action"] == 0
    assert om.coverage(cell) == 100.0


def test_匯出有表頭與內容_跳過空格子(conn):
    rows = om.export_rows(conn)
    assert len(om.EXPORT_HEADERS) == 8
    assert rows and all(len(r) == 8 for r in rows)
    assert all(r[2] > 0 for r in rows), "沒有機器的格子不要佔一列"
    assert {r[0] for r in rows} <= set(om.build(conn)["rows"])
