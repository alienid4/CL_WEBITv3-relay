"""掃描服務：排程判定 + 設定往返 + 防並發（確定性，不打真網路）。"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import scan_service  # noqa: E402


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    c = db.get_connection()
    yield c
    c.close()


def _stamp_schedule_run(conn, started_at: str):
    conn.execute(
        "INSERT INTO scan_runs (trigger, status, started_at) VALUES ('schedule','ok',?)",
        (started_at,),
    )
    conn.commit()


def test_排程設定往返(conn):
    scan_service.set_schedule(conn, True, "interval", "02:30", 8)
    assert scan_service.get_schedule(conn) == {
        "enabled": True, "mode": "interval", "time": "02:30", "interval_hours": 8,
    }


def test_停用時不觸發(conn):
    scan_service.set_schedule(conn, False, "daily", "01:00", 6)
    assert scan_service._due(conn, datetime(2026, 7, 18, 2, 0)) is False


def test_每日模式_到點且今天沒跑過才觸發(conn):
    scan_service.set_schedule(conn, True, "daily", "01:00", 6)
    assert scan_service._due(conn, datetime(2026, 7, 18, 0, 30)) is False   # 還沒到點
    assert scan_service._due(conn, datetime(2026, 7, 18, 1, 30)) is True    # 到點、沒跑過
    _stamp_schedule_run(conn, "2026-07-18 01:31:00")
    assert scan_service._due(conn, datetime(2026, 7, 18, 2, 0)) is False    # 今天已跑過
    assert scan_service._due(conn, datetime(2026, 7, 19, 1, 30)) is True    # 隔天又到點


def test_間隔模式_距上次超過N小時才觸發(conn):
    scan_service.set_schedule(conn, True, "interval", "01:00", 6)
    assert scan_service._due(conn, datetime(2026, 7, 18, 3, 0)) is True     # 從沒跑過
    _stamp_schedule_run(conn, "2026-07-18 01:00:00")
    assert scan_service._due(conn, datetime(2026, 7, 18, 5, 0)) is False    # 才過 4h
    assert scan_service._due(conn, datetime(2026, 7, 18, 7, 30)) is True    # 過 6.5h


def test_mode非法要擋(conn):
    with pytest.raises(ValueError):
        scan_service.set_schedule(conn, True, "weekly", "01:00", 6)
