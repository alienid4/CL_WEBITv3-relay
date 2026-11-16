"""2026-10-29 既有機型整理成「廠牌 型號」的前後對照（只預覽、不改資料）。全部假資料。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import model_naming as mn  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _hw(conn, n, model):
    db.insert_hardware(conn, asset_serial=f"C-{n}", hostname=f"h{n}", ip=f"10.99.9.{n}", device_model=model)


def test_整理對照_四種狀態_台數_不改資料(tmp_path):
    conn = _conn(tmp_path)
    try:
        for i, m in enumerate(["DELL R760", "DELL R760", "Dell R750", "HP Enterprise DL380", "HPE Server", "神秘盒子",
                               "Dell  R740\n", None], 1):
            _hw(conn, i, m)
        conn.commit()
        out = mn.cleanup_preview(conn)
        by = {r["before"]: r for r in out["rows"]}
        assert by["DELL R760"]["status"] == "已標準" and by["DELL R760"]["count"] == 2
        assert by["Dell R750"]["status"] == "只差大小寫空白" and by["Dell R750"]["after"] == "DELL R750"
        assert by["HP Enterprise DL380"]["status"] == "可整理" and by["HP Enterprise DL380"]["after"] == "HPE DL380"
        assert by["HPE Server"]["status"] == "要人處理" and by["HPE Server"]["after"] == ""
        assert by["神秘盒子"]["status"] == "要人處理"
        assert by["Dell  R740\n"]["status"] == "只差大小寫空白"
        assert None not in by and "" not in by                                          # 空的不列
        assert out["total_machines"] == 7 and out["total_kinds"] == len(out["rows"])
        assert [r["no"] for r in out["rows"]] == list(range(1, len(out["rows"]) + 1))     # 項次連續
        assert out["rows"][0]["status"] == "要人處理"                                   # 先列要人處理的
        assert out["summary"]["已標準"] == {"kinds": 1, "machines": 2}
        assert conn.execute("SELECT device_model FROM hardware WHERE asset_serial='C-3'").fetchone()[0] == "Dell R750"   # 沒改資料
    finally:
        conn.close()
