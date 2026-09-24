"""S12 done_when 驗證：checks 全 PASS，排程腳本可獨立測試觸發。
覆蓋D6每日備份留7天、D7掃描紀錄90天清除，兩支腳本都用注入的固定時間測試，不依賴真實時鐘。
"""
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import backup  # noqa: E402
import cleanup  # noqa: E402
import db  # noqa: E402

FIXED_NOW = datetime(2026, 7, 17, 3, 0, 0)


def _touch_with_mtime(path: Path, days_ago: float) -> None:
    path.write_bytes(b"fake db content")
    ts = FIXED_NOW.timestamp() - days_ago * 86400
    os.utime(path, (ts, ts))


def test_backup_creates_dated_copy_of_db():
    """S14 起 run_backup 回傳 BackupResult（含完整性檢查結果），不再是單純的 Path。

    也不再逐位元組比對來源與備份：改用 VACUUM INTO 產生一致快照後，備份檔是 SQLite
    重新寫出來的（會順便整理碎片），位元組本來就跟原檔不同——這是預期行為，不是錯誤。
    要驗的是「內容對不對」，見 test_backup_health.py 實際開檔讀資料的那幾支。
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "asset.db"
        db.init_db(db_path)
        backup_dir = Path(tmp) / "backups"

        result = backup.run_backup(db_path, backup_dir, FIXED_NOW)

        assert result.ok, result.error
        assert result.integrity_ok, result.integrity_detail
        assert result.path.exists()
        assert result.path.parent == backup_dir
        assert "20260717" in result.path.name


def test_backup_prunes_backups_older_than_retention_but_keeps_recent():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "asset.db"
        db.init_db(db_path)
        backup_dir = Path(tmp) / "backups"
        backup_dir.mkdir()

        old_backup = backup_dir / "asset_20260701_030000.db"  # 16天前，該被刪
        recent_backup = backup_dir / "asset_20260715_030000.db"  # 2天前，該保留
        _touch_with_mtime(old_backup, days_ago=16)
        _touch_with_mtime(recent_backup, days_ago=2)

        backup.run_backup(db_path, backup_dir, FIXED_NOW)  # 這次呼叫本身也會多產生一份今天的備份

        remaining = {f.name for f in backup_dir.glob("asset_*.db")}
        assert old_backup.name not in remaining
        assert recent_backup.name in remaining


def test_prune_old_backups_returns_removed_files_directly():
    with tempfile.TemporaryDirectory() as tmp:
        backup_dir = Path(tmp)
        stale = backup_dir / "asset_20260601_030000.db"
        _touch_with_mtime(stale, days_ago=46)

        removed = backup.prune_old_backups(backup_dir, FIXED_NOW)

        assert removed == [stale]
        assert not stale.exists()


def _insert_scan_row(conn, scan_time, segment="機房A"):
    conn.execute(
        "INSERT INTO scan_history (scan_time, hostname, ip, device_model, is_vm, segment, scan_ok) "
        "VALUES (?, 'host', '10.0.0.1', 'model', 0, ?, 1)",
        (scan_time, segment),
    )
    conn.commit()


def test_cleanup_removes_scan_history_older_than_90_days_but_keeps_recent():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        db.init_db(db_path)
        conn = db.get_connection(db_path)
        try:
            _insert_scan_row(conn, "2026-04-01 03:00:00", segment="太舊該刪")  # 早於90天前
            _insert_scan_row(conn, "2026-06-01 03:00:00", segment="剛好90天內")
            _insert_scan_row(conn, "2026-07-16 03:00:00", segment="最近")

            removed_count = cleanup.run_cleanup(conn, FIXED_NOW)

            assert removed_count == 1
            remaining_segments = {
                r["segment"] for r in conn.execute("SELECT segment FROM scan_history").fetchall()
            }
            assert remaining_segments == {"剛好90天內", "最近"}
        finally:
            conn.close()


def test_cleanup_does_not_touch_comparison_result():
    """D7範圍只碰scan_history，comparison_result（問題清單）走標記已處理生命週期，不該被這支腳本動到。"""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        db.init_db(db_path)
        conn = db.get_connection(db_path)
        try:
            _insert_scan_row(conn, "2026-01-01 03:00:00")
            db.insert_comparison_result(conn, "old-host", "10.0.0.1", "異常新增")

            cleanup.run_cleanup(conn, FIXED_NOW)

            issue_count = conn.execute("SELECT COUNT(*) AS c FROM comparison_result").fetchone()["c"]
            assert issue_count == 1  # 沒被清掉
        finally:
            conn.close()


if __name__ == "__main__":
    test_backup_creates_dated_copy_of_db()
    test_backup_prunes_backups_older_than_retention_but_keeps_recent()
    test_prune_old_backups_returns_removed_files_directly()
    test_cleanup_removes_scan_history_older_than_90_days_but_keeps_recent()
    test_cleanup_does_not_touch_comparison_result()
    print("S12 test_scheduled_jobs.py: PASS")
