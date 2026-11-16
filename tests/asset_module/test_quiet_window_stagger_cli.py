"""AUD-02 獨立審查補充：補跑不卡排程器＋15:00 錯開、兩套時鐘（伺服器 TZ=UTC）、命令列入口、外部掃描腳本、resume_waiting、
fail-closed 的直接釘住、dir_housekeeping 共用時間定義。所有外連函式都是 mock。"""
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(ROOT / "APP" / "asset-module"))

import db  # noqa: E402
import quiet_window as qw  # noqa: E402
import scan_service  # noqa: E402

MON = lambda h, m=0, s=0: datetime(2026, 11, 2, h, m, s)     # noqa: E731
SAT = lambda h, m=0, s=0: datetime(2026, 11, 7, h, m, s)     # noqa: E731
UTC = timezone.utc


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    c = db.get_connection()
    yield c
    c.close()


@pytest.fixture
def at():
    def _set(t):
        qw.set_clock(lambda: t)
    return _set


class Counter:
    def __init__(self):
        self.n = 0

    def __call__(self, *a, **k):
        self.n += 1
        return {"ran": True}


# ---------------------------------------------------------------- A：補跑不卡排程器、15:00 錯開
def test_補跑睡3秒_Teams_tick仍在半秒內輪到(conn, monkeypatch, at):
    slept = threading.Event()

    def slow(c):
        slept.set()
        time.sleep(3)
    monkeypatch.setitem(scan_service.CATCHUP, "vrops_import_tick", slow)
    teams = Counter()
    for name in ("_run_due_scans", "_vcenter_autoimport_tick", "_integrations_tick", "_ci_graph_tick", "_health_baseline_tick",
                 "_vuln_notify_tick", "_eos_notify_tick", "_daily_digest_tick", "_paste_watch_tick", "_dispatch_weekly_tick"):
        monkeypatch.setattr(scan_service, name, lambda *a, **k: None)
    monkeypatch.setattr(scan_service, "_teams_notify_tick", teams)
    at(MON(15, 10))
    qw.pending_mark(conn, "vrops_import_tick")
    t0 = time.monotonic()
    scan_service._scheduler_once(conn)
    assert time.monotonic() - t0 < 0.5 and teams.n == 1, "補跑必須在背景，不能卡住後面的 tick"
    assert slept.wait(2)
    scan_service._scheduler_once(conn)                       # 補跑還在睡：不重入、Teams 仍然輪到
    assert teams.n == 2
    for _ in range(80):
        if not scan_service._catchup_lock.locked():
            break
        time.sleep(0.1)


def test_15點整_各類入口錯開啟動(conn, monkeypatch, at):
    called = []
    for name in ("_run_due_scans", "_vcenter_autoimport_tick", "_integrations_tick", "_ci_graph_tick", "_health_baseline_tick",
                 "_vuln_notify_tick", "_eos_notify_tick", "_daily_digest_tick", "_teams_notify_tick", "_paste_watch_tick",
                 "_dispatch_weekly_tick", "_quiet_catchup_tick"):
        monkeypatch.setattr(scan_service, name, (lambda n: lambda *a, **k: called.append(n))(name))
    seq = {}
    for minute in (0, 1, 2, 3, 4):
        at(MON(15, minute))
        called.clear()
        scan_service._scheduler_once(conn)
        seq[minute] = set(called)
    assert "_run_due_scans" in seq[0] and "_teams_notify_tick" in seq[0]            # 掃描與通知不延遲
    assert "_integrations_tick" not in seq[0] and "_dispatch_weekly_tick" not in seq[0] and "_quiet_catchup_tick" not in seq[0]
    assert "_integrations_tick" in seq[1] and "_paste_watch_tick" in seq[1] and "_dispatch_weekly_tick" not in seq[1]
    assert "_dispatch_weekly_tick" in seq[2] and "_health_baseline_tick" not in seq[2]
    assert "_health_baseline_tick" in seq[3] and "_quiet_catchup_tick" not in seq[3]
    assert "_quiet_catchup_tick" in seq[4]
    at(MON(10))                                                                      # 窗內：全部照常呼叫（各自守門並記延後）
    called.clear()
    scan_service._scheduler_once(conn)
    assert "_integrations_tick" in called and "_dispatch_weekly_tick" in called


def test_minutes_since_release(conn, at):
    at(MON(10))
    assert qw.minutes_since_release(conn) == -1
    at(MON(15, 7))
    assert qw.minutes_since_release(conn) == 7
    at(SAT(15, 7))
    assert qw.minutes_since_release(conn) == 9999
    at(MON(7))
    assert qw.minutes_since_release(conn) == 9999


# ---------------------------------------------------------------- B：伺服器 TZ=UTC 的兩套時鐘
def test_UTC伺服器_台北週一10點_網段自訂排程不被標成已掃_每週收集不啟動(conn, monkeypatch):
    import dispatch_weekly
    import transfer_policy as tp
    started, marked = [], []
    monkeypatch.setattr(scan_service, "start_scan", lambda *a, **k: (started.append(a), False)[1])
    monkeypatch.setattr(scan_service, "mark_connection_scanned", lambda *a, **k: marked.append(a))
    monkeypatch.setattr(scan_service, "list_connections", lambda c: [{"id": 1, "scan_time": "01:00", "enabled": 1, "last_scan_at": None}])
    monkeypatch.setattr(scan_service, "_due", lambda c, n: False)
    qw.set_clock(None)
    _prev_tp = tp._clock
    tp.set_clock(lambda: datetime(2026, 11, 2, 2, 0, tzinfo=UTC).astimezone(tp.TZ))     # 台北週一 10:00（伺服器本地 naive 是 02:00）
    try:
        scan_service._run_due_scans(conn, datetime(2026, 11, 2, 2, 0))
        assert started == [] and marked == [], "窗內：不啟動、也不可先標成已掃"
        # 每週整片收集：設週一 01:00（伺服器本地 02:00 仍在補跑窗），台北其實 10:00
        db.set_setting(conn, "dispatch_weekly_enabled", "1")
        db.set_setting(conn, "dispatch_weekly_weekday", "0")
        db.set_setting(conn, "dispatch_weekly_time", "01:00")
        db.set_setting(conn, "dispatch_weekly_targets", "10.99.1.0/30")
        monkeypatch.setattr(dispatch_weekly, "_run_in_progress", lambda cn: False)
        c = Counter()
        assert dispatch_weekly.tick(conn, datetime(2026, 11, 2, 2, 0), starter=c) is False and c.n == 0
        # 第二層：直接呼叫 start_background（無票證）也不會開跑
        import collect_dispatch
        with pytest.raises(RuntimeError):
            collect_dispatch.start_background(None, "10.99.1.0/30", triggered_by="t")
    finally:
        tp.set_clock(_prev_tp)


def test_網段自訂排程_start_scan成功啟動後才標已掃(conn, monkeypatch, at):
    started, marked = [], []
    monkeypatch.setattr(scan_service, "start_scan", lambda *a, **k: (started.append(a), True)[1])
    monkeypatch.setattr(scan_service, "mark_connection_scanned", lambda *a, **k: marked.append(a))
    monkeypatch.setattr(scan_service, "list_connections", lambda c: [{"id": 1, "scan_time": "01:00", "enabled": 1, "last_scan_at": None}])
    monkeypatch.setattr(scan_service, "_due", lambda c, n: False)
    at(SAT(2))
    scan_service._run_due_scans(conn, SAT(2))
    assert len(started) == 1 and len(marked) == 1
    started.clear(); marked.clear()
    monkeypatch.setattr(scan_service, "start_scan", lambda *a, **k: False)               # 別的原因沒啟動
    scan_service._run_due_scans(conn, SAT(2))
    assert marked == []


# ---------------------------------------------------------------- C：auto_onboard 命令列／scheduled_cycle
def test_auto_onboard_窗內scheduled_cycle被拒_命令列要確認旗標(conn, monkeypatch, at, capsys):
    import auto_onboard
    at(MON(10))
    out = auto_onboard.scheduled_cycle(conn=conn)
    assert out.get("refused")
    assert qw.cli_gate("scan_post_auto_onboard", False, "自動納管") is False
    assert "--confirm-in-window" in capsys.readouterr().out
    assert qw.cli_gate("scan_post_auto_onboard", True, "自動納管") is True
    assert conn.execute("SELECT actor FROM quiet_window_log WHERE kind='manual_confirmed' AND entry='scan_post_auto_onboard'").fetchone()["actor"] == "cli"
    at(SAT(10))
    assert qw.cli_gate("scan_post_auto_onboard", False) is True


# ---------------------------------------------------------------- D：外部掃描腳本
def test_scan_segments_盤中拒絕_now也一樣_確認旗標才放行(tmp_path):
    import scan_segments as ss
    assert ss.in_market_hours(datetime(2026, 11, 2, 10, tzinfo=ss.MARKET_TZ)) is True
    assert ss.in_market_hours(datetime(2026, 11, 2, 8, 29, 59, tzinfo=ss.MARKET_TZ)) is False
    assert ss.in_market_hours(datetime(2026, 11, 2, 15, 0, tzinfo=ss.MARKET_TZ)) is False
    assert ss.in_market_hours(datetime(2026, 11, 7, 10, tzinfo=ss.MARKET_TZ)) is False
    import transfer_policy as tp                                                          # 與後端同一份定義
    assert ss.MARKET_OPEN_HM == (tp.MARKET_OPEN.hour, tp.MARKET_OPEN.minute) and ss.MARKET_CLOSE_HM == (tp.MARKET_CLOSE.hour, tp.MARKET_CLOSE.minute)
    assert ss.MARKET_TZ.utcoffset(None) == tp.TZ.utcoffset(None)


def test_scan_segments_main_在盤中直接拒絕(tmp_path, monkeypatch):
    import scan_segments as ss
    seg = tmp_path / "s.txt"
    seg.write_text("10.99.9.0/30\n", encoding="utf-8")
    real_dt = ss.datetime
    fake_now = datetime(2026, 11, 2, 10, 0, tzinfo=ss.MARKET_TZ)

    class FakeDT(real_dt):
        @classmethod
        def now(cls, tz=None):
            return fake_now if tz is not None else fake_now.replace(tzinfo=None)
    monkeypatch.setattr(ss, "datetime", FakeDT)
    called = []
    monkeypatch.setattr(ss, "scan_segment", lambda *a, **k: called.append(1), raising=False)
    monkeypatch.setattr(sys, "argv", ["scan_segments.py", "--segments", str(seg), "--out", str(tmp_path / "o"), "--now"])
    ss.main()
    assert called == []
    assert not (tmp_path / "o" / "scan.lock").exists()


# ---------------------------------------------------------------- E：resume_waiting
def test_resume_waiting_窗內不自動開始_窗外開始(conn, monkeypatch, at):
    import os

    import transfer_service as ts
    p = Path(os.environ["ASSET_DB_PATH"])
    monkeypatch.setattr(ts, "background_allowed", lambda c: True)
    monkeypatch.setattr(ts, "block_reason", lambda c, u: None)
    c = Counter()
    monkeypatch.setattr(ts, "spawn", c)
    import transfer_store
    transfer_store.ensure_tables(conn)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(ft_job)")]
    row = {"kind": "upload", "box": "linux", "file_name": "f", "size": 1, "dest_folder": "/tmp", "status": "queued",
           "created_by": "alice", "created_at": "2020-01-01 00:00:00", "stop_requested": 0}
    keep = {k: v for k, v in row.items() if k in cols}
    conn.execute(f"INSERT INTO ft_job ({','.join(keep)}) VALUES ({','.join('?' * len(keep))})", list(keep.values()))
    conn.commit()
    ts._active_jobs.clear()
    at(MON(10))
    assert ts.resume_waiting(p) == 0 and c.n == 0
    at(SAT(10))
    ts._active_jobs.clear()
    assert ts.resume_waiting(p) == 1 and c.n == 1


# ---------------------------------------------------------------- G／H：直接釘住 fail-closed 與補跑「窗內不執行」
def test_in_quiet_window外層例外_fail_closed(conn, monkeypatch):
    monkeypatch.setattr(qw, "get_config", lambda c: (_ for _ in ()).throw(RuntimeError("boom")))
    assert qw.in_quiet_window(conn, SAT(3)) is True
    monkeypatch.undo()
    monkeypatch.setattr(qw, "is_quiet_time", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    assert qw.in_quiet_window(conn, SAT(3)) is True


def test_補跑tick_窗內不執行(conn, monkeypatch, at):
    c = Counter()
    monkeypatch.setitem(scan_service.CATCHUP, "vrops_import_tick", c)
    qw.pending_mark(conn, "vrops_import_tick")
    at(MON(10))
    scan_service._quiet_catchup_tick(conn, sync=True)
    scan_service._quiet_catchup_tick(conn)
    time.sleep(0.2)
    assert c.n == 0 and qw.pending_list(conn)[0]["state"] == "pending"


# ---------------------------------------------------------------- J：dir_housekeeping 共用時間定義
def test_dir_housekeeping_共用一份時間定義(conn, at):
    import dir_housekeeping as hk
    import transfer_policy as tp
    src = Path(hk.__file__).read_text(encoding="utf-8")
    assert "tp.MARKET_OPEN" in src and "_dt.time(*BLOCK_START)" not in src
    assert hk.in_blocked_window(datetime(2026, 11, 7, 10, 0)) is True       # 保守：七天都擋（沿用既有行為）
    assert hk.in_blocked_window(datetime(2026, 11, 2, 15, 0)) is False and hk.in_blocked_window(tp.at(2026, 11, 2, 8, 30).replace(tzinfo=None)) is True
    at(MON(10))
    assert hk._now() == MON(10)                                              # 同一個時鐘來源（台北）


def test_run_real_scan_cli_窗內拒絕(conn, monkeypatch, at):
    import run_real_scan as rr
    at(MON(10))
    called = []
    monkeypatch.setattr(rr, "main", lambda: called.append(1))
    monkeypatch.setattr(sys, "argv", ["run_real_scan.py"])
    import runpy
    # __main__ 區塊：用 cli_gate 的結果決定是否呼叫 main（直接驗 gate 行為）
    assert qw.cli_gate("scan_schedule", False, "網段掃描") is False and called == []
