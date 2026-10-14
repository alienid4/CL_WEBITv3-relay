"""CI 圖譜每日重建排程（切片 1 收尾）。

這裡測的都是「排程本身會不會壞掉」，不是重建結果對不對（那在 test_ci_graph.py）。
六問裡的第 4、5 問——中途失敗留下什麼、重複執行會怎樣——就是這支要守住的：
卡死的 running 列會讓之後每一次重建都被擋掉，而且畫面看起來像「一直在跑」。
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import ci_graph  # noqa: E402
import db  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def test_預設排程為每天03時且啟用(tmp_path):
    conn = _conn(tmp_path)
    sched = ci_graph.get_schedule(conn)
    assert sched["enabled"] is True
    # 刻意不是 00:00：圖譜原料由 01:00 的夜間掃描那一串刷新，排太早會拿到舊資料。
    assert sched["time"] == "03:00"


def test_設定時間格式錯誤要擋下來(tmp_path):
    conn = _conn(tmp_path)
    for bad in ("25:00", "3:00", "03:60", "凌晨三點", ""):
        with pytest.raises(ValueError):
            ci_graph.set_schedule(conn, True, bad)
    # 擋下來之後設定不該被改壞
    assert ci_graph.get_schedule(conn)["time"] == "03:00"


def test_關閉排程後永遠不到點(tmp_path):
    conn = _conn(tmp_path)
    ci_graph.set_schedule(conn, False, "03:00")
    assert ci_graph.is_due(conn, datetime(2026, 8, 20, 3, 30)) is False


def test_未到點不觸發到點才觸發(tmp_path):
    conn = _conn(tmp_path)
    assert ci_graph.is_due(conn, datetime(2026, 8, 20, 2, 59)) is False
    assert ci_graph.is_due(conn, datetime(2026, 8, 20, 3, 0)) is True


def test_同一天只跑一次隔天再跑(tmp_path):
    conn = _conn(tmp_path)
    ci_graph.run_rebuild(conn, "schedule", "scheduler")
    # 當天稍晚再問一次：已經跑過了，不該重複。
    # started_at 是實際寫入當下的時間，所以這裡要用「今天」而不是寫死日期，
    # 否則測的是「跨日」而不是「同日」。
    today_late = datetime.now().replace(hour=23, minute=59, second=0, microsecond=0)
    assert ci_graph.is_due(conn, today_late) is False
    # 隔天到點要再跑（用未來日期問，避免綁死在寫測試的當下）
    assert ci_graph.is_due(conn, datetime(2099, 1, 1, 3, 0)) is True


def test_手動重建不會讓當晚排程被跳過(tmp_path):
    """使用者早上臨時按了一次重建，不該害當晚的定期刷新不跑——兩件事目的不同。"""
    conn = _conn(tmp_path)
    ci_graph.run_rebuild(conn, "manual", "admin")
    assert ci_graph.is_due(conn, datetime(2099, 1, 1, 3, 0)) is True


def test_已有重建在跑時再觸發會被擋(tmp_path):
    conn = _conn(tmp_path)
    conn.execute(
        "INSERT INTO ci_graph_runs (trigger, triggered_by, status) "
        "VALUES ('manual', 'admin', 'running')"
    )
    conn.commit()
    with pytest.raises(ci_graph.RebuildInProgress):
        ci_graph.run_rebuild(conn, "schedule", "scheduler")


def test_卡死的running列會被回收且標明原因(tmp_path):
    """程序被砍留下的 running 列若沒人收，之後每次重建都被擋 → 圖譜永遠不再更新。

    收掉時要標 failed 並寫原因，不是靜默刪除：「跑到一半被中斷」跟「沒跑過」
    是兩件不同的事，事後查為什麼沒更新要看得到這一筆。
    """
    conn = _conn(tmp_path)
    conn.execute(
        "INSERT INTO ci_graph_runs (trigger, triggered_by, status) "
        "VALUES ('schedule', 'scheduler', 'running')"
    )
    conn.commit()

    assert ci_graph.reclaim_stale_runs(conn) == 1

    row = conn.execute("SELECT * FROM ci_graph_runs ORDER BY id DESC LIMIT 1").fetchone()
    assert row["status"] == "failed"
    assert row["error"] and "中斷" in row["error"]
    assert row["finished_at"]

    # 回收之後重建要能正常進行，不再被擋
    result = ci_graph.run_rebuild(conn, "schedule", "scheduler")
    assert result["run_id"]


def test_沒有卡死列時回收不動任何東西(tmp_path):
    conn = _conn(tmp_path)
    ci_graph.run_rebuild(conn, "manual", "admin")
    assert ci_graph.reclaim_stale_runs(conn) == 0
    assert conn.execute(
        "SELECT status FROM ci_graph_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()["status"] == "done"


def test_重建失敗要記進run列而不是靜默吞掉(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    monkeypatch.setattr(ci_graph, "rebuild", lambda c: (_ for _ in ()).throw(RuntimeError("壞了")))
    with pytest.raises(RuntimeError):
        ci_graph.run_rebuild(conn, "schedule", "scheduler")
    row = conn.execute("SELECT * FROM ci_graph_runs ORDER BY id DESC LIMIT 1").fetchone()
    assert row["status"] == "failed"
    assert "壞了" in row["error"]
