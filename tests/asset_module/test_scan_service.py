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


# ===== 各網段自訂排程時間（2026-09-12）=====
def _add_conn(conn, name, target, scan_time=None):
    cid = db.create_connection_record(conn, name, "網路掃描", target, None, None, None)
    if scan_time:
        db.set_connection_scan_time(conn, cid, scan_time)
    return cid


def test_網段自訂排程_到點判定(conn):
    cid = _add_conn(conn, "seg-a", "10.99.20.0/24", "03:00")
    row = db.get_connection_by_id(conn, cid)
    assert scan_service._seg_due(row, datetime(2026, 7, 18, 2, 0)) is False   # 還沒到
    assert scan_service._seg_due(row, datetime(2026, 7, 18, 3, 30)) is True   # 到點、今天沒跑
    db.mark_connection_scanned(conn, cid, "2026-07-18 03:31:00")
    row = db.get_connection_by_id(conn, cid)
    assert scan_service._seg_due(row, datetime(2026, 7, 18, 4, 0)) is False   # 今天跑過了
    assert scan_service._seg_due(row, datetime(2026, 7, 19, 3, 30)) is True   # 隔天又到點


def test_沒設時間的網段_seg_due一律否(conn):
    cid = _add_conn(conn, "seg-b", "10.99.21.0/24")
    assert scan_service._seg_due(db.get_connection_by_id(conn, cid), datetime(2026, 7, 18, 3, 30)) is False


def test_沒有自訂時間_全域排程照舊掃全部(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(scan_service, "start_scan", lambda *a, **k: calls.append((a, k)) or True)
    scan_service.set_schedule(conn, True, "daily", "01:00", 6)
    _add_conn(conn, "seg", "10.99.20.0/24")   # 沒自訂時間
    scan_service._run_due_scans(conn, datetime(2026, 7, 18, 1, 30))
    assert calls == [(("schedule",), {})], "沒自訂時間時要跟以往完全一樣：全域掃全部、無 only_ids"


def test_有自訂時間_全域只掃其餘_該段自己到點單獨跑(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(scan_service, "start_scan", lambda *a, **k: calls.append((a, k)) or True)
    scan_service.set_schedule(conn, True, "daily", "01:00", 6)
    rest_id = _add_conn(conn, "rest", "10.99.20.0/24")          # 無自訂
    seg_id = _add_conn(conn, "seg", "10.99.21.0/24", "03:00")   # 自訂 03:00
    scan_service._run_due_scans(conn, datetime(2026, 7, 18, 1, 30))   # 全域到點
    assert calls[-1] == (("schedule",), {"only_ids": {rest_id}}), "全域只掃沒自訂時間的那些"
    _stamp_schedule_run(conn, "2026-07-18 01:31:00")   # 記全域已跑
    calls.clear()
    scan_service._run_due_scans(conn, datetime(2026, 7, 18, 3, 30))   # seg 到點
    assert calls[-1] == (("schedule-seg",), {"only_ids": {seg_id}, "light": True})


def test_scan_targets_子集只回指定網段(conn):
    import run_real_scan
    a = _add_conn(conn, "a", "10.99.20.0/24")
    _add_conn(conn, "b", "10.99.21.0/24")
    assert len(run_real_scan.scan_targets(conn, only_ids={a})) == 1
    assert len(run_real_scan.scan_targets(conn)) == 2
