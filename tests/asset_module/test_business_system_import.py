"""業務系統對照表匯入：同一 api_id 多組名字時，**不可靜默取最後一列**，
要標「多來源·待確認」並存所有候選（2026-09-09 使用者定案：全部放進去、不用挑、但不能偷偷猜）。

也釘住：吃 CSV（dynassets 那份是 .csv，欄名 APID／項目名稱／AP／部門）。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import business_system as bs  # noqa: E402
import db  # noqa: E402


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _csv(tmp, text: str) -> Path:
    p = Path(tmp) / "map.csv"
    p.write_text(text, encoding="utf-8-sig")
    return p


def test_csv_同碼多名要標多來源_不靜默取最後一列():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            # 欄名用 dynassets 那份的：APID／項目名稱／AP／部門
            path = _csv(tmp, "\n".join([
                "APID,項目名稱,AP,部門",
                "N-001,單一系統,王五,資訊部",        # 乾淨：只有一個名字
                "N-002,名字甲,張三,交易部",          # 衝突：同碼兩個不同名字
                "N-002,名字乙,張三,交易部",
            ]))
            r = bs.import_file(path, conn)

            assert r["imported"] == 2           # N-001、N-002 各一筆
            assert r["multi_source"] == 1       # N-002 是多來源

            clean = bs.lookup(conn, "N-001")
            assert clean["found"] and clean["needs_review"] == 0
            assert clean["name"] == "單一系統"

            conflict = bs.lookup(conn, "N-002")
            assert conflict["found"]
            assert conflict["needs_review"] == 1
            # 兩個候選都留著，不是只留最後一列
            assert "名字甲" in conflict["name_candidates"]
            assert "名字乙" in conflict["name_candidates"]
        finally:
            conn.close()


def test_重匯釐清後_多來源標記要清掉():
    """第一次有衝突→needs_review=1；後來來源只剩一個名字→重匯要把標記清掉。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            bs.import_file(_csv(tmp, "APID,項目名稱\nN-009,舊甲\nN-009,舊乙\n"), conn)
            assert bs.lookup(conn, "N-009")["needs_review"] == 1
            # 重匯：這次只剩一個名字
            bs.import_file(_csv(tmp, "APID,項目名稱\nN-009,定案名\n"), conn)
            after = bs.lookup(conn, "N-009")
            assert after["needs_review"] == 0
            assert after["name"] == "定案名"
        finally:
            conn.close()
