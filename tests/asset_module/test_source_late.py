"""SI-3 資料沒進來主動通知（source_late）：逾時排 Teams、同日不重複、恢復通知一次、從沒收過不算逾時、Teams 關閉只記狀態、設定壞掉照預設。
來源名與時間全是假資料。"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import relay_import as ri  # noqa: E402
import source_late as sl  # noqa: E402
import teams_notify as tn  # noqa: E402

T0 = datetime(2026, 11, 22, 12, 0, 0)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    c = db.get_connection()
    ri.ensure(c)
    tn.ensure(c)
    monkeypatch.setattr(tn, "load_settings", lambda _c: {"enabled": True})
    monkeypatch.setattr(tn, "_link", lambda _cfg, p: "https://example.invalid" + p)
    return c


def batch(c, source, at, status="imported", bid=None):
    c.execute("INSERT INTO relay_import_batches (source, batch_id, status, imported_at) VALUES (?,?,?,?)",
              (source, bid or f"{source}-{at}", status, at.strftime(sl.FMT)))
    c.commit()


def queued(c):
    return [dict(r) for r in c.execute("SELECT event_key, kind, title FROM teams_notify_delivery WHERE kind = 'source_late' ORDER BY id")]


def test_never_received_is_not_late(conn):
    r = sl.check(conn, T0, force=True)
    assert r["late"] == [] and queued(conn) == []
    assert {x["state"] for x in sl.status(conn, T0)} == {"never"}


def test_late_pushes_once_per_day_and_recovers(conn):
    batch(conn, "whatsup_lastpoll", T0 - timedelta(hours=3))          # 門檻 2 小時 → 逾時
    batch(conn, "whatsup", T0 - timedelta(hours=5))                   # 門檻 30 小時 → 正常
    r = sl.check(conn, T0, force=True)
    assert r["late"] == ["whatsup_lastpoll"] and len(queued(conn)) == 1
    assert "1 種" in queued(conn)[0]["title"]
    # 同一天再評估：不重複
    sl.check(conn, T0 + timedelta(hours=1), force=True)
    assert len(queued(conn)) == 1
    # 隔天仍逾時：提醒一次
    sl.check(conn, T0 + timedelta(days=1), force=True)
    assert len(queued(conn)) == 2
    # 新資料到：恢復通知一次
    batch(conn, "whatsup_lastpoll", T0 + timedelta(days=1, minutes=5))
    r = sl.check(conn, T0 + timedelta(days=1, minutes=10), force=True)
    assert r["recovered"] == ["whatsup_lastpoll"] and len(queued(conn)) == 3
    sl.check(conn, T0 + timedelta(days=1, minutes=20), force=True)
    assert len(queued(conn)) == 3


def test_failed_batches_do_not_count_as_arrival(conn):
    batch(conn, "whatsup_lastpoll", T0 - timedelta(hours=5))
    batch(conn, "whatsup_lastpoll", T0 - timedelta(minutes=5), status="rejected", bid="bad")
    assert sl.check(conn, T0, force=True)["late"] == ["whatsup_lastpoll"]


def test_threshold_boundary_is_strictly_greater(conn):
    batch(conn, "whatsup_lastpoll", T0 - timedelta(hours=2))
    assert sl.check(conn, T0, force=True)["late"] == []
    assert sl.check(conn, T0 + timedelta(minutes=1), force=True)["late"] == ["whatsup_lastpoll"]


def test_teams_off_records_only(conn, monkeypatch):
    monkeypatch.setattr(tn, "load_settings", lambda _c: {"enabled": False})
    batch(conn, "whatsup_lastpoll", T0 - timedelta(hours=9))
    r = sl.check(conn, T0, force=True)
    assert r["late"] == ["whatsup_lastpoll"] and queued(conn) == []
    assert [x["state"] for x in sl.status(conn, T0) if x["source"] == "whatsup_lastpoll"] == ["late"]


def test_late_then_never_does_not_send_recovery(conn):
    """2.070.1（審查 p2#13／#25）：逾時後來源變成 never（成功歷史被清）不可送「已收到新資料（None）」恢復通知。"""
    batch(conn, "whatsup_lastpoll", T0 - timedelta(hours=3))
    sl.check(conn, T0, force=True)
    assert len(queued(conn)) == 1
    conn.execute("DELETE FROM relay_import_batches")                     # 成功歷史被清掉
    conn.commit()
    r = sl.check(conn, T0 + timedelta(minutes=30), force=True)
    assert r["recovered"] == [] and len(queued(conn)) == 1
    assert [x["state"] for x in sl.status(conn, T0) if x["source"] == "whatsup_lastpoll"] == ["never"]
    # 之後真的再逾時，視為新的逾時（狀態已清掉，不會被舊 late 狀態吃掉）
    batch(conn, "whatsup_lastpoll", T0 + timedelta(hours=1))
    r = sl.check(conn, T0 + timedelta(hours=5), force=True)
    assert r["late"] == ["whatsup_lastpoll"] and len(queued(conn)) == 2


def test_recovery_requires_fresh_state_with_valid_last_at(conn):
    batch(conn, "whatsup_lastpoll", T0 - timedelta(hours=3))
    sl.check(conn, T0, force=True)
    batch(conn, "whatsup_lastpoll", T0 + timedelta(minutes=1))
    r = sl.check(conn, T0 + timedelta(minutes=2), force=True)
    assert r["recovered"] == ["whatsup_lastpoll"] and "已收到新資料" in str(
        conn.execute("SELECT payload_json FROM teams_notify_delivery WHERE title LIKE '資料恢復%'").fetchone()[0])


def test_teams_off_then_on_same_day_still_sends_first_notice(conn, monkeypatch):
    """2.070.1（審查 p2#14／#25）：Teams 關著時不寫「今天通知過」；同一天稍後開啟 Teams 要補發。"""
    monkeypatch.setattr(tn, "load_settings", lambda _c: {"enabled": False})
    batch(conn, "whatsup_lastpoll", T0 - timedelta(hours=9))
    sl.check(conn, T0, force=True)
    assert queued(conn) == []
    monkeypatch.setattr(tn, "load_settings", lambda _c: {"enabled": True})
    r = sl.check(conn, T0 + timedelta(hours=1), force=True)               # 同一天
    assert r["late"] == ["whatsup_lastpoll"] and len(queued(conn)) == 1
    sl.check(conn, T0 + timedelta(hours=2), force=True)                   # 補發後同日不重複
    assert len(queued(conn)) == 1


def test_throttle_and_settings(conn):
    batch(conn, "whatsup_lastpoll", T0 - timedelta(hours=9))
    assert sl.check(conn, T0)["status"] == "ok"
    assert sl.check(conn, T0 + timedelta(minutes=3))["status"] == "throttled"
    assert sl.check(conn, T0 + timedelta(minutes=11))["status"] == "ok"
    db.set_setting(conn, sl.SETTING_KEY, '{"enabled": false}')
    assert sl.check(conn, T0 + timedelta(hours=1), force=True)["status"] == "disabled"
    db.set_setting(conn, sl.SETTING_KEY, "{壞掉")
    cfg = sl.load_settings(conn)
    assert cfg["enabled"] and cfg["hours"] == sl.DEFAULT_HOURS
    db.set_setting(conn, sl.SETTING_KEY, '{"hours": {"whatsup_lastpoll": 12, "nope": 1, "unity": -3}}')
    h = sl.load_settings(conn)["hours"]
    assert h["whatsup_lastpoll"] == 12 and h["unity"] == sl.DEFAULT_HOURS["unity"] and "nope" not in h


def test_scheduler_wrapper_calls_it():
    src = (ROOT / "APP" / "asset-module" / "backend" / "scan_service.py").read_text(encoding="utf-8")
    assert "source_late.check(conn)" in src
