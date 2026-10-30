"""環境別分組 + 機房×環境別交叉表（使用者 2026-08-20 要求）。

這支守的核心是一件事：**畫面上格子寫幾台，點進去就要看到幾台。**

交叉表把 UAT/DEV/OA 併進「測試」，所以點格子時必須用 environment_group 篩，
不能用 environment 精確比對——後者會少撈那 10 台，格子寫 705、清單列 695，
看的人只會認定系統在騙他，這張表就白做了。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import manage_state  # noqa: E402


def test_UAT與DEV與OA一律併進測試():
    for raw in ("使用者測試(UAT)", "開發環境(DEV)", "OA", "測試", "UAT", "DEV"):
        assert manage_state.group_environment(raw) == "測試", raw


def test_正式與備援各自成組():
    assert manage_state.group_environment("正式") == "正式"
    assert manage_state.group_environment("備援") == "備援"


def test_空值歸未填而不是丟掉():
    # 「沒填」是待辦事項，不是可以靜默省略的東西——696 台沒填環境別，
    # 統計上必須看得到它們，否則總數對不起來也沒人會發現。
    for raw in (None, "", "   "):
        assert manage_state.group_environment(raw) == "未填"


def test_沒見過的值歸其他而不是消失():
    assert manage_state.group_environment("災難備援演練區") == "備援"   # 含「備援」
    assert manage_state.group_environment("完全沒見過的值") == "其他"


def test_交叉表每一格加總等於各自的總數(tmp_path):
    """行合計、列合計、總計三者要自洽。這正是改成表格的理由——
    膠囊沒有合計，看的人無從確認有沒有東西被漏掉。"""
    import db

    p = tmp_path / "t.db"
    db.init_db(p)
    conn = db.get_connection(p)
    try:
        rows = [
            ("A1", "01_板橋機房", "正式"),
            ("A2", "01_板橋機房", "測試"),
            ("A3", "02_內湖機房", "使用者測試(UAT)"),   # 要被併進「測試」
            ("A4", "02_內湖機房", "正式"),
            ("A5", "", "正式"),                          # 機房未填
            ("A6", "00_敦南總公司", ""),                 # 環境未填
        ]
        for serial, loc, env in rows:
            db.insert_hardware(conn, asset_serial=serial, hostname=serial,
                               physical_location=loc, environment=env)
        conn.commit()

        comp = manage_state.composition(conn)
        matrix = comp["by_location_env"]

        grand = sum(sum(m.values()) for m in matrix.values())
        assert grand == comp["total"], "交叉表總和必須等於有效資產總數，不能有機器掉出去"

        # 各機房的欄合計要等於 by_location（同一份資料兩種算法要一致）
        for loc, per_env in matrix.items():
            assert sum(per_env.values()) == comp["by_location"][loc], loc

        # UAT 那台要落在內湖／測試，不是自成一格
        assert matrix["內湖"]["測試"] == 1
        assert "UAT" not in matrix["內湖"]

        # 未填的兩台都要在表上找得到，不是被吃掉
        assert matrix["未填"]["正式"] == 1
        assert matrix["敦南"]["未填"] == 1
    finally:
        conn.close()
