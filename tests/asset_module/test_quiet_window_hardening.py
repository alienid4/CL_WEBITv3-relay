"""AUD-02 審查必修（GPT 5.6）的補強測試：時區、長任務逐步重查、檔案傳送、確認票證、pending 可靠性、稽核同交易、休市日驗證。
所有外連函式都是 mock，不連網路。helpers 與 test_quiet_window.py 相同的做法（注入靜默窗時鐘）。
"""
import datetime as _datetime
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import quiet_window as qw  # noqa: E402
import scan_service  # noqa: E402

MON = lambda h, m=0, s=0: datetime(2026, 11, 2, h, m, s)     # noqa: E731
SAT = lambda h, m=0, s=0: datetime(2026, 11, 7, h, m, s)     # noqa: E731
UTC = _datetime.timezone.utc


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


def _has_log(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name='quiet_window_log'").fetchone() is not None


# ============================================================ 必修 1：時區
@pytest.mark.parametrize("utc_t,quiet", [
    (datetime(2026, 11, 2, 0, 29, 59, tzinfo=UTC), False),      # 台北 08:29:59
    (datetime(2026, 11, 2, 0, 30, 0, tzinfo=UTC), True),         # 台北 08:30:00
    (datetime(2026, 11, 2, 1, 0, 0, tzinfo=UTC), True),          # 台北 09:00（審查指定案例）
    (datetime(2026, 11, 2, 6, 59, 59, tzinfo=UTC), True),        # 台北 14:59:59
    (datetime(2026, 11, 2, 7, 0, 0, tzinfo=UTC), False),         # 台北 15:00
    (datetime(2026, 11, 1, 17, 0, 0, tzinfo=UTC), False),        # 台北週一 01:00
    (datetime(2026, 11, 6, 17, 0, 0, tzinfo=UTC), False),        # UTC 週五 17:00＝台北週六 01:00（週末）
])
def test_aware_時間先轉台北再判斷(conn, utc_t, quiet):
    assert qw.in_quiet_window(conn, utc_t) is quiet


def test_伺服器時區是UTC_排程仍以台北時間守門(conn, monkeypatch):
    """伺服器 TZ=UTC、現實時間 UTC 02:00＝台北 10:00（盤中）。舊版拿 datetime.now()（02:00）判斷而放行。
    現在排程路徑不傳 naive now，守門讀 transfer_policy 的 aware 台北時鐘。"""
    import transfer_policy as tp
    started = []
    monkeypatch.setattr(scan_service, "_do_scan", lambda *a, **k: started.append(1))
    monkeypatch.setattr(scan_service, "_running", False)
    qw.set_clock(None)
    _prev_tp = tp._clock
    tp.set_clock(lambda: datetime(2026, 11, 2, 2, 0, tzinfo=UTC).astimezone(tp.TZ))
    try:
        scan_service.set_schedule(conn, True, "daily", "01:00", 6)
        scan_service._run_due_scans(conn, datetime(2026, 11, 2, 2, 0))        # 伺服器本地(UTC) naive 的 02:00：到期
        time.sleep(0.15)
        assert started == [] and qw.in_quiet_window(conn) is True
        tp.set_clock(lambda: datetime(2026, 11, 2, 7, 0, tzinfo=UTC).astimezone(tp.TZ))        # 台北 15:00
        scan_service._run_due_scans(conn, datetime(2026, 11, 2, 7, 0))
        for _ in range(40):
            if started:
                break
            time.sleep(0.05)
        assert started == [1]
    finally:
        tp.set_clock(_prev_tp)


# ============================================================ 必修 2：長任務逐步重查
def test_掃描_0829啟動_0830之後下一個網段不得掃(conn, at):
    import scanner
    scanned = []

    class Src:
        def __init__(self, name, cidr, after=None):
            self.name, self.cidr, self.after = name, cidr, after

        def scan(self):
            scanned.append(self.name)
            if self.after:
                at(self.after)                       # 第一個網段掃的過程中，時間走到 08:30:05
            return []

    at(MON(8, 29, 59))
    run = qw.begin(conn, "scan_schedule")
    assert run is not None and not run.human
    srcs = [Src("A", "10.99.1.0/24", after=MON(8, 30, 5)), Src("B", "10.99.2.0/24"), Src("C", "10.99.3.0/24")]
    with qw.use(run):
        out = scanner.run_scan(srcs, conn)
    assert scanned == ["A"], "進窗後不得再掃下一個網段"
    assert [f["source"] for f in out["failed_segments"]] == ["B", "C"] and run.paused
    assert [p["entry"] for p in qw.pending_list(conn)] == ["scan_schedule"]


def test_每週健康基準_分批_進窗後不派下一批_也不記本週跑過(conn, monkeypatch, at):
    import health_baseline as hb
    batches = []

    def check_batch(ips, **k):
        batches.append(len(ips))
        at(MON(8, 30, 5))
        return []
    monkeypatch.setattr(hb, "_target_ips", lambda cn: [f"10.99.2.{i}" for i in range(1, 61)])
    import onboard_eligibility
    monkeypatch.setattr(onboard_eligibility, "vmware_targets", lambda cn, ips: set())      # 不做任何名稱解析／連線
    import manage_state
    monkeypatch.setattr(manage_state, "collect_platform_of", lambda cn, ip, *a, **k: "linux")      # 不做任何 SSH 探測
    at(MON(8, 29, 59))
    out = hb.run_weekly_baseline(conn, _check_batch=check_batch)
    assert batches == [8] and out.get("paused") is True
    assert db.get_setting(conn, "health_baseline_last_run", "") == ""
    assert [p["entry"] for p in qw.pending_list(conn)] == ["health_baseline_weekly"]


def test_收集_批與批之間_進窗不派下一批(conn, monkeypatch, at):
    import collect_dispatch as cd
    seen = []
    real = cd._probe_all

    def probe(ips, prober, workers=64):
        seen.append(len(ips))
        if len(seen) == 1:
            at(MON(8, 30, 5))
        return real(ips, prober, workers=workers)
    monkeypatch.setattr(cd, "_probe_all", probe)
    monkeypatch.setattr(cd, "_sleep", lambda s: None)
    at(MON(8, 29, 59))
    cd.run_dispatch(conn, "192.0.2.0/24", prober=lambda ip: None, batch_size=50, gap_seconds=0)
    assert seen == [50], "第一批之後進窗，不得再探測第二批"
    assert "dispatch_weekly_tick" in [p["entry"] for p in qw.pending_list(conn)]


def test_收集_窗內直接呼叫run_dispatch被拒(conn, monkeypatch, at):
    import collect_dispatch as cd
    seen = []
    monkeypatch.setattr(cd, "_probe_all", lambda *a, **k: seen.append(1) or {})
    at(MON(10))
    out = cd.run_dispatch(conn, "192.0.2.0/24", prober=lambda ip: None, batch_size=50, gap_seconds=0)
    assert out.get("refused") and seen == []


def test_納管後收集隊伍_進窗後不再派下一台_剩下的存起來補跑(conn, monkeypatch, at):
    import post_onboard as po
    ran = []

    def runner(cn, serial, by, source):
        ran.append(serial)
        at(MON(8, 30, 5))
        return "ok"
    at(MON(8, 29, 59))
    po.enqueue(None, ["Q1", "Q2", "Q3"], by="t", source="onboard", _runner=runner, _sync=True)
    assert ran == ["Q1"], "08:30:05 之後不得再收下一台"
    assert json.loads(db.get_setting(conn, po.PAUSED_KEY, "[]")) == ["Q2", "Q3"]
    assert [p["entry"] for p in qw.pending_list(conn)] == ["post_onboard_queue"]
    at(MON(15, 0))
    monkeypatch.setattr(po, "enqueue", lambda *a, **k: ran.append(("requeue", a[1])))
    scan_service._quiet_catchup_tick(conn, sync=True)
    assert ran[-1] == ("requeue", ["Q2", "Q3"]) and qw.pending_list(conn) == []


def test_貼指令偵測_逐台重查(conn, monkeypatch, at):
    import paste_watch as pw
    tried = []
    at(MON(8, 29, 59))
    pw._ensure(conn)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(onboard_paste_pending)")]
    for i in (1, 2, 3):
        vals = {"ip": f"10.99.3.{i}", "state": "waiting", "issued_at": "2026-11-02 08:00:00"}
        keep = {k: v for k, v in vals.items() if k in cols}
        conn.execute(f"INSERT INTO onboard_paste_pending ({','.join(keep)}) VALUES ({','.join('?' * len(keep))})", list(keep.values()))
    conn.commit()

    def try_one(cn, r, now, *a):
        tried.append(r["ip"])
        at(MON(8, 30, 5))
        return {"ip": r["ip"]}
    monkeypatch.setattr(pw, "_try_one", try_one)
    with qw.use(qw.Run("paste_watch_tick", human=False)):
        pw.check(conn)
    assert len(tried) == 1


def test_底層外連閘_窗內無人為授權會擋_並標待補跑(conn, at):
    at(MON(10))
    qw.network_gate()                                                # 沒有任務授權脈絡（人為診斷端點的連線測試）：最底層不擋，窗內確認由路由 dependency 負責
    run = qw.Run("vrops_import_tick", human=False, db_path=qw._path_of(conn))
    with qw.use(run):
        with pytest.raises(qw.QuietWindowBlocked):
            qw.network_gate()
    assert run.paused and "vrops_import_tick" in [p["entry"] for p in qw.pending_list(conn)]
    with qw.use(qw.Run("vrops_import_tick", human=True)):
        qw.network_gate()                                            # 人為 Run 放行
    at(SAT(10))
    qw.network_gate()                                                # 窗外放行


# ============================================================ 必修 3：檔案傳送
def test_檔案傳送_自動續傳窗內不開_窗外開(conn, monkeypatch, at):
    import os

    import transfer_service as ts
    c = Counter()
    monkeypatch.setattr(ts, "spawn", c)
    p = Path(os.environ["ASSET_DB_PATH"])
    at(MON(10))
    ts._active_jobs.discard(7)
    assert ts.start_job(p, 7) is False and c.n == 0                  # resumer 沒有票證
    at(SAT(10))
    assert ts.start_job(p, 7) is True and c.n == 1


def test_檔案傳送_人為窗內持票證才可開始_票證一次性(conn, monkeypatch, at):
    import os

    import transfer_service as ts
    c = Counter()
    monkeypatch.setattr(ts, "spawn", c)
    p = Path(os.environ["ASSET_DB_PATH"])
    at(MON(10))
    tk = qw.issue_ticket("transfer_resumer", "alice")
    ts._active_jobs.discard(8)
    assert ts.start_job(p, 8, quiet_ticket=tk) is True and c.n == 1
    ts._active_jobs.discard(8)
    assert ts.start_job(p, 8, quiet_ticket=tk) is False              # 同一張票證不能用第二次
    ts._active_jobs.discard(8)
    assert ts.start_job(p, 8, quiet_ticket=qw.issue_ticket("scan_schedule", "alice")) is False     # 票證綁入口


def test_檔案傳送_指令窗內無票證被拒(conn, monkeypatch, at):
    import transfer_service as ts
    at(MON(10))
    monkeypatch.setattr(ts, "resolve_hosts", lambda cn, ips, box=None: [{"ip": "10.99.4.1", "hostname": "h", "platform": "linux"}])
    with pytest.raises(ts.TransferError) as ei:
        ts.start_command(conn, "alice", "linux", ["10.99.4.1"], "echo hi")
    assert ei.value.status == 409


# ============================================================ 必修 5：票證與硬守門
def test_票證_一次性_綁入口_逾時失效(conn, monkeypatch):
    t = qw.issue_ticket("scan_schedule", "alice")
    assert qw.consume_ticket(t, "scan_schedule") is True
    assert qw.consume_ticket(t, "scan_schedule") is False             # 一次性
    t2 = qw.issue_ticket("scan_schedule", "alice")
    assert qw.consume_ticket(t2, "vrops_import_tick") is False        # 綁入口
    t3 = qw.issue_ticket("scan_schedule", "alice")
    real = qw._time.monotonic
    monkeypatch.setattr(qw._time, "monotonic", lambda: real() + qw.TICKET_TTL + 1)
    assert qw.consume_ticket(t3, "scan_schedule") is False            # 逾時
    assert qw.consume_ticket("偽造", "scan_schedule") is False and qw.consume_ticket(None, "scan_schedule") is False


def test_硬守門_窗內直接呼叫底層函式都被擋(conn, monkeypatch, at):
    import capacity_analysis as ca
    import service_inventory
    n = Counter()
    at(MON(10))
    conn.execute(ca.WU_TABLE_SQL)
    r = ca.wu_refresh(conn, lambda h, f, t: (n(), {})[1], MON(10), manual=True, hosts=["A"])     # manual=True 不是授權
    assert r.get("needs_confirm") and n.n == 0
    r = ca.wu_refresh(conn, lambda h, f, t: (n(), {})[1], MON(10), manual=True, hosts=["A"], ticket="偽造票證")
    assert r.get("needs_confirm") and n.n == 0
    monkeypatch.setattr(scan_service, "_do_scan", n)
    assert scan_service.start_scan() is False and scan_service.start_scan("manual") is False     # 預設 manual 也要票證
    assert scan_service.start_scan("manual", quiet_ticket="偽造") is False
    time.sleep(0.1)
    assert n.n == 0
    assert service_inventory.collect_services(conn, trigger="manual").get("refused")              # trigger="manual" 不是授權
    assert service_inventory.collect_services(conn, trigger="manual", quiet_ticket="偽造").get("refused")
    with pytest.raises(TypeError):
        scan_service._post_scan_collect_services(conn, True)                                      # 舊的 auto 旗標已不存在


def test_人工API簽票證_窗外不簽(conn, at):
    at(MON(10))
    tk = qw.require_manual_confirm(conn, "scan_schedule", True, "alice", "10.99.0.5")
    assert isinstance(tk, str) and qw.consume_ticket(tk, "scan_schedule")
    at(SAT(10))
    assert qw.require_manual_confirm(conn, "scan_schedule", False, "alice") is None


def test_人為持票證啟動掃描_窗內可執行(conn, monkeypatch, at):
    c = Counter()
    monkeypatch.setattr(scan_service, "_do_scan", c)
    monkeypatch.setattr(scan_service, "_running", False)
    at(MON(10))
    tk = qw.require_manual_confirm(conn, "scan_schedule", True, "alice")
    assert scan_service.start_scan("manual", quiet_ticket=tk) is True
    time.sleep(0.2)
    assert c.n == 1


# ============================================================ 必修 7：稽核同一交易
def test_稽核同交易_activity寫失敗_quiet_window_log也回滾_不簽票證(conn, at, monkeypatch):
    from fastapi import HTTPException
    import activity
    at(MON(10))
    monkeypatch.setattr(activity, "log", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("activity down")))
    issued_before = len(qw._TICKETS)
    with pytest.raises(HTTPException) as ei:
        qw.require_manual_confirm(conn, "scan_schedule", True, "alice", "10.99.0.5")
    assert ei.value.status_code == 500
    assert conn.execute("SELECT COUNT(*) FROM quiet_window_log WHERE kind='manual_confirmed'").fetchone()[0] == 0, "不可留下假的 manual_confirmed"
    assert len(qw._TICKETS) == issued_before


# ============================================================ 必修 6：pending 可靠性
def test_pending_收集失敗_有限次重試_遞增間隔_不每分鐘重跑(conn, monkeypatch, at):
    boom = Counter()

    def fail(c):
        boom()
        raise RuntimeError("collect failed")
    monkeypatch.setitem(scan_service.CATCHUP, "vrops_import_tick", fail)
    at(MON(15, 0))
    qw.pending_mark(conn, "vrops_import_tick", "t")
    scan_service._quiet_catchup_tick(conn, sync=True)
    assert boom.n == 1
    row = conn.execute("SELECT state, attempts, next_at FROM quiet_window_pending").fetchone()
    assert row["state"] == "failed" and row["attempts"] == 1 and row["next_at"] > "2026-11-02 15:00:00"
    scan_service._quiet_catchup_tick(conn, sync=True)                      # 同一分鐘：間隔未到，不重跑
    assert boom.n == 1
    for hours in (2, 4):
        at(MON(15, 0) + _datetime.timedelta(hours=hours))
        scan_service._quiet_catchup_tick(conn, sync=True)
    assert boom.n == 3                                          # 第 3 次失敗後停止自動重試
    at(SAT(23, 0))
    scan_service._quiet_catchup_tick(conn, sync=True)
    assert boom.n == 3
    assert qw.pending_list(conn)[0]["state"] == "failed" and qw.pending_list(conn)[0]["attempts"] == 3     # 狀態條看得到


def test_pending_成功才標done_補跑途中又進窗不算失敗(conn, monkeypatch, at):
    okc = Counter()
    monkeypatch.setitem(scan_service.CATCHUP, "whatsup_poll_tick", okc)
    at(MON(15, 0))
    qw.pending_mark(conn, "whatsup_poll_tick")
    scan_service._quiet_catchup_tick(conn, sync=True)
    assert okc.n == 1 and conn.execute("SELECT state FROM quiet_window_pending").fetchone()[0] == "done"

    def paused(c):
        qw.current().paused = True
    monkeypatch.setitem(scan_service.CATCHUP, "whatsup_poll_tick", paused)
    qw.pending_mark(conn, "whatsup_poll_tick")
    scan_service._quiet_catchup_tick(conn, sync=True)
    r = conn.execute("SELECT state, attempts FROM quiet_window_pending").fetchone()
    assert r["state"] == "pending" and r["attempts"] == 0       # 又被擋＝回 pending，不算失敗


class _BrokenConn:
    def execute(self, *a, **k):
        raise RuntimeError("db locked")

    def commit(self):
        pass


def test_pending_mark寫入失敗_不吞_改記憶體後備_補跑仍會處理(conn, monkeypatch, at, caplog):
    import logging
    okc = Counter()
    monkeypatch.setitem(scan_service.CATCHUP, "vrops_import_tick", okc)
    with caplog.at_level(logging.ERROR, logger="quiet_window"):
        qw.pending_mark(_BrokenConn(), "vrops_import_tick", "x")
    assert any("無法寫入補跑待辦" in r.message for r in caplog.records), "寫入失敗必須告警"
    assert qw.pending_is(conn, "vrops_import_tick") is True
    at(MON(15, 0))
    scan_service._quiet_catchup_tick(conn, sync=True)
    assert okc.n == 1


def test_pending_finish寫入失敗_不吞_保持running_過期後回pending(conn, at, caplog):
    import logging
    at(MON(15, 0))
    qw.pending_mark(conn, "vrops_import_tick")
    assert qw.pending_claim(conn) == ["vrops_import_tick"]
    with caplog.at_level(logging.ERROR, logger="quiet_window"):
        qw.pending_finish(_BrokenConn(), "vrops_import_tick", True)
    assert any("無法更新" in r.message for r in caplog.records)
    assert conn.execute("SELECT state FROM quiet_window_pending").fetchone()[0] == "running"       # 沒被誤標 done
    at(MON(15, 0) + _datetime.timedelta(minutes=qw.STALE_RUNNING_MIN + 1))
    assert qw.pending_claim(conn) == ["vrops_import_tick"]                                        # 過期後重新視為待補跑


# ============================================================ 必修 8：休市日嚴格驗證／告警／只在到期才記延後
@pytest.mark.parametrize("raw", ['[{"date": "2026-11-2"}]', '[{"date": "2026-02-30"}]', '[{"date": "20261102"}]',
                                 '{"date": "2026-11-02"}', '[{"label": "x"}]', "not json", '[{"date": 5}]'])
def test_休市日格式不合_broken_視同窗內_且有告警(conn, raw, caplog):
    import logging
    db.set_setting(conn, "capacity_holidays", raw)
    qw._ALERT_AT[0] = 0
    with caplog.at_level(logging.ERROR, logger="quiet_window"):
        cfg = qw.get_config(conn)
    assert cfg["broken"] and "休市日" in cfg["problem"]
    assert qw.in_quiet_window(conn, SAT(3)) is True
    assert any("設定異常" in r.message for r in caplog.records)
    assert "設定異常，自動排程全部暫停" in qw.status(conn, MON(10))["message"]


def test_延後紀錄只在確實到期才累加(conn, monkeypatch, at):
    at(MON(10))
    scan_service.set_schedule(conn, False, "daily", "01:00", 6)          # 停用：不會到期
    scan_service._run_due_scans(conn, MON(10))
    import vrops_import as vi
    monkeypatch.setattr(vi, "module_enabled", lambda cn: False)
    vi.tick(conn)
    import dispatch_weekly
    dispatch_weekly.tick(conn, MON(10))
    import paste_watch
    paste_watch.tick_in_background()                                     # 沒有等待中的主機
    n = conn.execute("SELECT COUNT(*) FROM quiet_window_log WHERE kind='deferred'").fetchone()[0] if _has_log(conn) else 0
    assert n == 0
