"""S3 done_when 驗證：mock 掃描可產生結構化結果，且單一來源失敗不會讓其他來源結果消失
（契約驗收標準第3條：掃描失敗要明確呈現，不能被誤讀為無異常）。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import scanner  # noqa: E402
from scan_sources import MockSNMPSource, MockVCenterSource  # noqa: E402


def test_successful_scan_writes_results():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        db.init_db(db_path)
        conn = db.get_connection(db_path)
        try:
            summary = scanner.run_scan([MockVCenterSource(), MockSNMPSource()], conn)
            assert summary["total_found"] == 5  # 3 vCenter + 2 SNMP
            assert summary["failed_segments"] == []

            rows = conn.execute("SELECT * FROM scan_history WHERE scan_ok = 1").fetchall()
            assert len(rows) == 5
        finally:
            conn.close()


def test_one_source_failure_does_not_lose_other_results():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        db.init_db(db_path)
        conn = db.get_connection(db_path)
        try:
            summary = scanner.run_scan(
                [MockVCenterSource(fail=True), MockSNMPSource(fail=False)], conn
            )
            assert summary["total_found"] == 2  # 只有 SNMP 的 2 筆成功
            assert len(summary["failed_segments"]) == 1
            assert summary["failed_segments"][0]["source"] == "vCenter(mock)"

            ok_rows = conn.execute("SELECT * FROM scan_history WHERE scan_ok = 1").fetchall()
            failed_rows = conn.execute("SELECT * FROM scan_history WHERE scan_ok = 0").fetchall()
            assert len(ok_rows) == 2
            assert len(failed_rows) == 1  # 失敗的網段本身也要留紀錄，不能悄悄消失
        finally:
            conn.close()


def test_latest_scan_results_filters_failed_rows():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        db.init_db(db_path)
        conn = db.get_connection(db_path)
        try:
            scanner.run_scan([MockVCenterSource(), MockSNMPSource(fail=True)], conn)
            results = scanner.latest_scan_results(conn)
            assert len(results) == 3  # 只有 vCenter 的 3 筆算成功
            assert all(r["scan_ok"] == 1 for r in results)
        finally:
            conn.close()


if __name__ == "__main__":
    test_successful_scan_writes_results()
    test_one_source_failure_does_not_lose_other_results()
    test_latest_scan_results_filters_failed_rows()
    print("S3 test_scanner.py: PASS")
