"""S1 done_when 驗證：schema 可建立空 DB 並跑基本 CRUD。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402


def test_init_and_crud():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        db.init_db(db_path)
        conn = db.get_connection(db_path)
        try:
            db.insert_hardware(
                conn,
                asset_serial="HW-TEST-0001",
                hostname="test-host-01",
                ip="10.0.0.1",
                device_model="Test Model",
                environment="測試",
                asset_status="使用中",
            )
            row = db.get_hardware_by_serial(conn, "HW-TEST-0001")
            assert row is not None
            assert row["hostname"] == "test-host-01"
            assert row["environment"] == "測試"

            rows = db.list_hardware(conn, environment="測試")
            assert len(rows) == 1

            result_id = db.insert_comparison_result(conn, "test-host-01", "10.0.0.1", "漏登記")
            db.mark_comparison_read(conn, result_id)
            marked = conn.execute(
                "SELECT is_read FROM comparison_result WHERE id = ?", (result_id,)
            ).fetchone()
            assert marked["is_read"] == 1
        finally:
            conn.close()


if __name__ == "__main__":
    test_init_and_crud()
    print("S1 test_db.py: PASS")


def test_既有DB也要長出指紋欄位():
    """S16：221 的正式庫早就有 scan_history 了，只改 schema.sql 不會動到它
    （CREATE TABLE IF NOT EXISTS 對既有表什麼都不做——時區那次就是這樣漏掉的）。

    這支測試刻意造一顆「舊形狀」的 DB，再跑 init_db，確認遷移真的補了欄位。
    """
    import sqlite3

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "old.db"
        # 手動造出加欄位之前的舊表
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE scan_history ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, scan_time TEXT, hostname TEXT, "
            "ip TEXT, device_model TEXT, is_vm INTEGER, segment TEXT, scan_ok INTEGER)"
        )
        conn.execute(
            "INSERT INTO scan_history (ip, hostname) VALUES ('YOUR_CLIENT_IP','')"
        )
        conn.commit()
        conn.close()

        db.init_db(db_path)

        conn = db.get_connection(db_path)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(scan_history)")}
            for col in ("mac", "mac_vendor", "open_ports", "ttl", "os_guess"):
                assert col in cols, f"既有 DB 沒補上 {col} 欄位，S16 的線索無處可存"
            # 舊資料不能被弄丟
            assert conn.execute("SELECT COUNT(*) FROM scan_history").fetchone()[0] == 1
        finally:
            conn.close()


def test_匯入紀錄帶來源與檔名_可依來源篩選查詢():
    """2026-08-19 使用者原話「要有紀錄表讓我查」：一次要匯好幾份RVTools檔案，
    要看得出哪些已經匯過（檔名+時間），不能只有數字統計看不出來源跟檔名。"""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        db.init_db(db_path)
        conn = db.get_connection(db_path)
        try:
            db.create_import_log(
                conn, imported_by="devuser", hardware_count=10, personnel_count=0,
                software_count=0, error_count=0, source="rvtools", file_name="機房A.xlsx",
            )
            db.create_import_log(
                conn, imported_by="devuser", hardware_count=5, personnel_count=0,
                software_count=0, error_count=1, source="rvtools", file_name="機房B.xlsx",
            )
            db.create_import_log(
                conn, imported_by="devuser", hardware_count=100, personnel_count=20,
                software_count=5, error_count=0, source="cia_excel", file_name="全公司盤點.xlsx",
            )

            rv_only = db.list_import_log(conn, source="rvtools")
            assert len(rv_only) == 2
            assert {r["file_name"] for r in rv_only} == {"機房A.xlsx", "機房B.xlsx"}
            assert rv_only[0]["file_name"] == "機房B.xlsx"  # 新到舊

            everything = db.list_import_log(conn)
            assert len(everything) == 3
        finally:
            conn.close()
