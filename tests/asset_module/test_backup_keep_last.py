"""備份只留最新 5 份（使用者 2026-10-31）：所有 *.db 一起算，其餘清掉；不碰非 .db 檔。"""
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"))
import backup  # noqa: E402


def test_只留最新5份_跨檔名類型一起算(tmp_path):
    names = ["asset_%d.db" % i for i in range(4)] + ["pre_deploy_%d.db" % i for i in range(3)] + ["pre_restore_x.db"]
    for i, n in enumerate(names):
        f = tmp_path / n
        f.write_bytes(b"x")
        os.utime(f, (1_000_000 + i * 100, 1_000_000 + i * 100))      # 越後面越新
    (tmp_path / "notes.txt").write_text("keep me")
    removed = backup.prune_old_backups(tmp_path, datetime.now())
    left = sorted(p.name for p in tmp_path.glob("*.db"))
    assert len(left) == backup.KEEP_LAST == 5
    assert sorted(r.name for r in removed) == sorted(names[:3])            # 最舊的 3 份被刪
    assert (tmp_path / "notes.txt").exists()                                # 非 .db 不動


def test_不足5份不刪(tmp_path):
    for i in range(3):
        (tmp_path / f"asset_{i}.db").write_bytes(b"x")
    assert backup.prune_old_backups(tmp_path, datetime.now()) == []


def test_最新一份排程備份永遠保留_即使被連續部署快照擠出前5(tmp_path):
    """GPT 審查：連續部署快照不可把最後一個每日備份擠掉。"""
    daily = tmp_path / f"{backup.BACKUP_FILENAME_PREFIX}20260101_020000.db"
    daily.write_bytes(b"x")
    os.utime(daily, (1_000_000, 1_000_000))                       # 最舊
    for i in range(6):
        f = tmp_path / f"pre_deploy_{i}.db"
        f.write_bytes(b"x")
        os.utime(f, (2_000_000 + i, 2_000_000 + i))
    removed = backup.prune_old_backups(tmp_path, datetime.now())
    assert daily.exists(), "最後一份排程備份被擠掉了"
    assert [r.name for r in removed] == ["pre_deploy_0.db"]       # 只刪最舊的快照；共留 6 份（5 份＋受保護的排程備份）
