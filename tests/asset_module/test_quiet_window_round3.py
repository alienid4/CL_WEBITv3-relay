"""AUD-02 第三輪（GPT 複查 P1／P2）補強測試：公開時間函式的時區、小批重查（同一網段／同一批內下一個 IP）、傳送授權帶入執行緒、
pending 完成語意、記憶體後備、台北時鐘、post_onboard 逐項授權、票證綁使用者、可見性、命令列、匯出設定。全部 mock，不連網。"""
import json
import sys
import threading
import time
from datetime import datetime, timezone
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


# ============================================================ P1-1：公開時間函式自己轉台北
def test_is_quiet_time_aware_UTC直接傳入也判對():
    import datetime as dt
    s, e = dt.time(8, 30), dt.time(15, 0)
    assert qw.is_quiet_time(datetime(2026, 11, 2, 1, 0, tzinfo=UTC), s, e) is True        # 台北 09:00
    assert qw.is_quiet_time(datetime(2026, 11, 2, 0, 29, 59, tzinfo=UTC), s, e) is False   # 台北 08:29:59
    assert qw.is_quiet_time(datetime(2026, 11, 2, 7, 0, tzinfo=UTC), s, e) is False        # 台北 15:00
    assert qw.is_quiet_time(datetime(2026, 11, 6, 17, 0, tzinfo=UTC), s, e) is False       # 台北週六 01:00


def test_容量分析的in_quiet_window也走同一個helper():
    import capacity_analysis as ca
    assert ca.in_quiet_window(datetime(2026, 11, 2, 1, 0, tzinfo=UTC)) is True
    assert ca.in_quiet_window(datetime(2026, 11, 2, 7, 0, tzinfo=UTC)) is False


def test_全站沒有直接用naive判斷時間的呼叫():
    """掃描 backend：直接呼叫 is_quiet_time／in_quiet_window 的地方必須是 quiet_window 自己、容量分析的委派，或傳入 conn 的版本。"""
    import re
    backend = ROOT / "APP" / "asset-module" / "backend"
    hits = []
    for p in sorted(backend.glob("*.py")) + sorted((backend / "routers").glob("*.py")):
        if p.name in ("quiet_window.py",):
            continue
        for ln, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\bis_quiet_time\(", line) and not line.strip().startswith("#"):
                hits.append(f"{p.name}:{ln}")
    assert hits == ["capacity_analysis.py:%s" % hits[0].split(":")[1]], hits       # 只有容量分析一處委派


# ============================================================ P1-2：小批重查
def test_同一網段內_進窗後下一批IP不得探測(conn, at):
    import net_scan
    probed = []

    def probe(ip, timeout=0.6):
        probed.append(ip)
        if len(probed) == 1:
            at(MON(8, 30, 5))                  # 第一個 IP 探測的過程中，時間走到 08:30:05
        return None
    src = net_scan.NetworkSweepSource("10.99.5.0/24", probe=probe, arp_reader=lambda: {}, ttl_prober=lambda ip: None,
                                      banner_grabber=lambda ip, p: None)
    at(MON(8, 29, 59))
    run = qw.begin(conn, "scan_schedule")
    with qw.use(run):
        with pytest.raises(ConnectionError):
            src.scan()
    assert len(probed) <= net_scan.__dict__.get("CHUNK", 32) and len(probed) == 32, "第一個小批（32）探完就停，不能再探第二批"
    assert run.paused and "scan_schedule" in [p["entry"] for p in qw.pending_list(conn)]


def test_收集_同一批內下一小批IP不得探測(conn, monkeypatch, at):
    import collect_dispatch as cd
    seen = []

    def prober(ip):
        seen.append(ip)
        if len(seen) == 1:
            at(MON(8, 30, 5))
        return None
    at(MON(8, 29, 59))
    run = qw.begin(conn, "dispatch_weekly_tick")
    with qw.use(run):
        with pytest.raises(qw.QuietWindowBlocked):
            cd._probe_all([f"10.99.6.{i}" for i in range(1, 101)], prober, workers=64)
    assert len(seen) == cd.PROBE_CHUNK


def test_收集_逐台迴圈進窗不再對下一台連線(conn, monkeypatch, at):
    import collect_dispatch as cd
    monkeypatch.setattr(cd, "_sleep", lambda s: None)
    done = []
    real = cd._do_ssh

    def ssh(conn_, ip, asset, ssh_prober, key_path):
        done.append(ip)
        at(MON(8, 30, 5))
        return ("ok", "x")
    monkeypatch.setattr(cd, "_do_ssh", ssh)
    at(MON(8, 29, 59))
    out = cd.run_dispatch(conn, "10.99.8.1\n10.99.8.2\n10.99.8.3", prober=lambda ip: [22], ttl_prober=lambda ip: None,
                          ssh_prober=lambda ip: (True, None), batch_size=50, gap_seconds=0)
    assert len(done) == 1, f"進窗後不得再對下一台 SSH：{done}"
    assert out["stopped_after"] is not None


def test_健康基準_每8台一批(conn, monkeypatch, at):
    import health_baseline as hb
    import manage_state
    import onboard_eligibility
    batches = []
    monkeypatch.setattr(hb, "_target_ips", lambda cn: [f"10.99.2.{i}" for i in range(1, 30)])
    monkeypatch.setattr(onboard_eligibility, "vmware_targets", lambda cn, ips: set())
    monkeypatch.setattr(manage_state, "collect_platform_of", lambda cn, ip, *a, **k: "linux")

    def cb(ips, **k):
        batches.append(len(ips))
        at(MON(8, 30, 5))
        return []
    at(MON(8, 29, 59))
    hb.run_weekly_baseline(conn, _check_batch=cb)
    assert batches == [8]


# ============================================================ P1-3：檔案傳送授權帶入執行緒、途中重查
def test_傳送_reverify_指令_人為票證授權帶入背景執行緒(conn, monkeypatch, at):
    import transfer_service as ts
    seen = []
    monkeypatch.setattr(ts, "spawn", lambda fn, *a: fn(*a))                       # 同步執行
    monkeypatch.setattr(ts, "run_reverify", lambda *a, **k: seen.append(("reverify", qw.current().human)))
    monkeypatch.setattr(ts, "_command_worker", lambda *a, **k: seen.append(("command", qw.current().human)))
    monkeypatch.setattr(ts, "_get_job", lambda cn, jid: {"kind": "upload", "size": 1})
    monkeypatch.setattr(ts, "job_detail", lambda cn, jid: {})
    monkeypatch.setattr(ts.st, "audit", lambda *a, **k: None)
    monkeypatch.setattr(ts, "resolve_hosts", lambda cn, ips, box=None: [{"ip": "10.99.4.1", "hostname": "h", "platform": "linux"}])
    monkeypatch.setattr(ts.st, "cmd_log_start", lambda *a, **k: 1)
    at(MON(10))
    ts.start_reverify(conn, "alice", 1, False, quiet_ticket=qw.issue_ticket("transfer_resumer", "alice"))
    ts.start_command(conn, "alice", "linux", ["10.99.4.1"], "echo hi", quiet_ticket=qw.issue_ticket("transfer_resumer", "alice"))
    assert seen == [("reverify", True), ("command", True)]
    at(SAT(10))                                                                    # 窗外啟動：非人為
    seen.clear()
    ts.start_reverify(conn, "alice", 1, False)
    assert seen == [("reverify", False)]


def test_傳送_窗外啟動的長傳送_進窗後不派下一台_並park(conn, monkeypatch, at):
    import os

    import transfer_service as ts
    import transfer_store
    transfer_store.ensure_tables(conn)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(ft_job)")]
    row = {"kind": "upload", "box": "linux", "file_name": "f", "size": 1, "dest_folder": "/tmp", "status": "queued",
           "created_by": "alice", "created_at": "2020-01-01 00:00:00", "concurrency": 1, "stop_requested": 0}
    keep = {k: v for k, v in row.items() if k in cols}
    conn.execute(f"INSERT INTO ft_job ({','.join(keep)}) VALUES ({','.join('?' * len(keep))})", list(keep.values()))
    jid = conn.execute("SELECT MAX(id) FROM ft_job").fetchone()[0]
    hcols = [r[1] for r in conn.execute("PRAGMA table_info(ft_job_host)")]
    for i in (1, 2, 3):
        h = {"job_id": jid, "seq": i, "ip": f"10.99.9.{i}", "status": "queued", "platform": "linux", "room": "r"}
        k2 = {k: v for k, v in h.items() if k in hcols}
        conn.execute(f"INSERT INTO ft_job_host ({','.join(k2)}) VALUES ({','.join('?' * len(k2))})", list(k2.values()))
    conn.commit()
    sent = []

    def host_task(db_path, job_id, hid, runner):
        sent.append(hid)
        c2 = db.get_connection()
        c2.execute("UPDATE ft_job_host SET status='ok' WHERE id=?", (hid,))
        c2.commit()
        c2.close()
        at(MON(8, 30, 5))
    monkeypatch.setattr(ts, "_host_task", host_task)
    monkeypatch.setattr(ts, "block_reason", lambda c, u: None)
    monkeypatch.setattr(ts, "_drop_ineligible", lambda c, w: w)
    monkeypatch.setattr(ts.pol, "waves", lambda p: [p])
    at(MON(8, 29, 59))
    run = qw.Run("transfer_resumer", human=False)
    with qw.use(run):
        ts._run_job(conn, Path(os.environ["ASSET_DB_PATH"]), jid, runner=object())
    assert len(sent) == 1, "進窗後不得再傳下一台"
    assert conn.execute("SELECT status FROM ft_job WHERE id=?", (jid,)).fetchone()[0] == "waiting_window"
    assert conn.execute("SELECT COUNT(*) FROM ft_job_host WHERE job_id=? AND status='queued'", (jid,)).fetchone()[0] == 2


# ============================================================ P1-4：pending 完成語意
def test_補跑函式回傳False或失敗字典_視為失敗重試(conn, monkeypatch, at):
    for ret in (False, {"status": "failed", "message": "x"}, {"error": "boom"}):
        conn.execute("DELETE FROM quiet_window_pending") if conn.execute("SELECT 1 FROM sqlite_master WHERE name='quiet_window_pending'").fetchone() else None
        n = Counter()
        monkeypatch.setitem(scan_service.CATCHUP, "vrops_import_tick", lambda c, r=ret, n=n: (n(), r)[1])
        at(MON(15, 0))
        qw.pending_mark(conn, "vrops_import_tick")
        scan_service._quiet_catchup_tick(conn, sync=True)
        row = conn.execute("SELECT state, attempts FROM quiet_window_pending").fetchone()
        assert row["state"] == "failed" and row["attempts"] == 1, ret


def test_補跑函式回傳refused_不算失敗_回pending(conn, monkeypatch, at):
    monkeypatch.setitem(scan_service.CATCHUP, "vrops_import_tick", lambda c: {"refused": True})
    at(MON(15, 0))
    qw.pending_mark(conn, "vrops_import_tick")
    scan_service._quiet_catchup_tick(conn, sync=True)
    row = conn.execute("SELECT state, attempts FROM quiet_window_pending").fetchone()
    assert row["state"] == "pending" and row["attempts"] == 0


def test_補跑掃描_同步跑完才done_忙碌回pending(conn, monkeypatch, at):
    ran = []
    monkeypatch.setattr(scan_service, "_do_scan", lambda trig, only, light, run=None: (ran.append(trig), True)[1])
    monkeypatch.setattr(scan_service, "_running", False)
    at(MON(15, 0))
    qw.pending_mark(conn, "scan_schedule")
    scan_service._quiet_catchup_tick(conn, sync=True)
    assert ran == ["schedule-catchup"] and qw.pending_list(conn) == []
    monkeypatch.setattr(scan_service, "_running", True)                           # 別的掃描在跑
    qw.pending_mark(conn, "scan_schedule")
    scan_service._quiet_catchup_tick(conn, sync=True)
    row = conn.execute("SELECT state, attempts FROM quiet_window_pending").fetchone()
    assert row["state"] == "pending" and row["attempts"] == 0 and ran == ["schedule-catchup"]


def test_初始延後一律pending_mark(conn, monkeypatch, at):
    import health_baseline
    import vrops_import as vi
    import whatsup_service as wu
    at(MON(10))
    scan_service.set_schedule(conn, True, "daily", "01:00", 6)
    scan_service._run_due_scans(conn, MON(10))
    monkeypatch.setattr(vi, "module_enabled", lambda cn: True)
    monkeypatch.setattr(wu, "module_enabled", lambda cn: True)
    db.set_setting(conn, vi.K_ENABLED, "1")
    db.set_setting(conn, wu.K_ENABLED, "1")
    vi.tick(conn)
    wu.tick(conn)
    monkeypatch.setattr(health_baseline, "baseline_due", lambda cn, now: True)
    scan_service._health_baseline_tick(conn)
    assert {p["entry"] for p in qw.pending_list(conn)} == {"scan_schedule", "vrops_import_tick", "whatsup_poll_tick", "health_baseline_weekly"}


def test_pending_mark遇到舊失敗_重設attempts_不永久卡死(conn, at):
    at(MON(15, 0))
    qw.pending_mark(conn, "vrops_import_tick")
    for _ in range(3):
        qw.pending_claim(conn)
        qw.pending_finish(conn, "vrops_import_tick", False, detail="x")
        conn.execute("UPDATE quiet_window_pending SET next_at=''")
        conn.commit()
    assert conn.execute("SELECT state, attempts FROM quiet_window_pending").fetchone()["attempts"] == 3
    assert qw.pending_claim(conn) == []                                           # 卡在 failed×3
    qw.pending_mark(conn, "vrops_import_tick", "新的合法延後事件")
    row = conn.execute("SELECT state, attempts, next_at FROM quiet_window_pending").fetchone()
    assert (row["state"], row["attempts"], row["next_at"]) == ("pending", 0, "")
    assert qw.pending_claim(conn) == ["vrops_import_tick"]


def test_admin重設端點_登入停用failclosed_稽核(tmp_path, monkeypatch):
    import api
    import auth
    from fastapi.testclient import TestClient
    p = tmp_path / "api.db"
    db.init_db(p)

    def _dep():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()
    api.app.dependency_overrides[api.get_db] = _dep
    try:
        cl = TestClient(api.app)
        c = db.get_connection(p)
        db.create_user(c, "admin", auth.hash_password("test-password-123"), role="admin")
        db.create_user(c, "bob", auth.hash_password("test-password-123"), role="user")
        qw.pending_mark(c, "vrops_import_tick")
        c.execute("UPDATE quiet_window_pending SET state='failed', attempts=3")
        c.commit()
        c.close()
        assert cl.post("/api/auth/login", json={"username": "bob", "password": "test-password-123"}).status_code == 200
        assert cl.post("/api/quiet-window/pending/vrops_import_tick/reset").status_code == 403         # 非 admin
        cl.post("/api/auth/logout")
        assert cl.post("/api/auth/login", json={"username": "admin", "password": "test-password-123"}).status_code == 200
        r = cl.post("/api/quiet-window/pending/vrops_import_tick/reset")
        assert r.status_code == 200 and r.json()["pending"][0]["attempts"] == 0
        c = db.get_connection(p)
        assert c.execute("SELECT 1 FROM activity_log WHERE action='quiet_window_pending_reset'").fetchone()
        assert cl.post("/api/quiet-window/pending/nope/reset").status_code == 404
        db.set_setting(c, "auth_disabled", "1")
        c.close()
        assert cl.post("/api/quiet-window/pending/vrops_import_tick/reset").status_code in (403, 401)  # 登入停用：fail-closed
    finally:
        api.app.dependency_overrides.clear()


# ============================================================ P1-5：記憶體後備
class _Broken:
    def execute(self, *a, **k):
        raise RuntimeError("db locked")

    def commit(self):
        pass


def test_記憶體後備_claim不先移除_finish失敗或暫停時還原(conn, at):
    at(MON(15, 0))
    qw.pending_mark(_Broken(), "vrops_import_tick", "x")
    assert qw.pending_claim(conn) == ["vrops_import_tick"] and "vrops_import_tick" in qw._MEM_PENDING          # claim 後仍在
    assert qw.pending_claim(conn) == []                                                                          # 已被領走，不重複
    qw.pending_finish(_Broken(), "vrops_import_tick", True)                                                      # 寫入失敗：還原成可再領
    assert qw.pending_claim(conn) == ["vrops_import_tick"]
    qw.pending_finish(conn, "vrops_import_tick", True, paused=True)                                              # 暫停：還原
    assert qw._MEM_PENDING["vrops_import_tick"]["claimed"] is False
    assert qw.pending_claim(conn) == ["vrops_import_tick"]
    qw.pending_finish(conn, "vrops_import_tick", True)                                                           # 成功：upsert done、移除記憶體
    assert "vrops_import_tick" not in qw._MEM_PENDING
    assert conn.execute("SELECT state FROM quiet_window_pending WHERE entry='vrops_import_tick'").fetchone()[0] == "done"


# ============================================================ P1-6：台北時鐘
def test_UTC主機_排程器一輪用台北時鐘_due與錯開(conn, monkeypatch):
    import transfer_policy as tp
    nows = []
    for name in ("_vcenter_autoimport_tick", "_integrations_tick", "_quiet_catchup_tick", "_ci_graph_tick", "_health_baseline_tick",
                 "_vuln_notify_tick", "_eos_notify_tick", "_daily_digest_tick", "_teams_notify_tick", "_paste_watch_tick", "_dispatch_weekly_tick"):
        monkeypatch.setattr(scan_service, name, lambda *a, **k: None)
    monkeypatch.setattr(scan_service, "_run_due_scans", lambda c, now: nows.append(now))
    qw.set_clock(None)
    prev = tp._clock
    tp.set_clock(lambda: datetime(2026, 11, 2, 7, 1, tzinfo=UTC).astimezone(tp.TZ))        # UTC 07:01＝台北 15:01
    try:
        scan_service._scheduler_once(conn)
        assert nows == [datetime(2026, 11, 2, 15, 1)], nows                                    # 不是 OS 本地的 07:01
        assert qw.minutes_since_release(conn) == 1
    finally:
        tp.set_clock(prev)
    import dispatch_weekly
    seen = []
    monkeypatch.setattr(dispatch_weekly, "is_due", lambda c, now: seen.append(now) or False)
    qw.set_clock(lambda: MON(15, 1))
    dispatch_weekly.tick(conn)
    assert seen == [MON(15, 1)]


# ============================================================ P1-7：post_onboard 逐項授權、requeue 順序
def test_post_onboard_同一隊伍_人為與自動各自授權_不互相覆蓋(conn, at):
    import post_onboard as po
    ran = []
    at(MON(10))
    tk = qw.issue_ticket("post_onboard_queue", "alice")
    po._queue.clear(); po._queued.clear(); po._item_human.clear()
    # 先排一台自動、再由人為（持票證）排兩台：兩次 enqueue 的授權不能互相覆蓋
    with po._q_lock:
        po._queue.append(("AUTO-1", None, "onboard"))
        po._queued.add("AUTO-1")
        po._item_human["AUTO-1"] = False
        po._q_state.update(running=False)
    po.enqueue(None, ["HUMAN-1", "HUMAN-2"], by="alice", source="manual", quiet_ticket=tk,
               _runner=lambda cn, s, by, src: (ran.append(s), "ok")[1], _sync=True)
    assert ran == ["HUMAN-1", "HUMAN-2"], "窗內：人為的照跑，自動的 AUTO-1 被擋"
    assert json.loads(db.get_setting(conn, po.PAUSED_KEY, "[]")) == ["AUTO-1"]


def test_requeue_paused_enqueue失敗_PAUSED_KEY保留(conn, monkeypatch, at):
    import post_onboard as po
    db.set_setting(conn, po.PAUSED_KEY, json.dumps(["Q9"]))
    monkeypatch.setattr(po, "enqueue", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("queue down")))
    with pytest.raises(RuntimeError):
        po.requeue_paused(conn)
    assert json.loads(db.get_setting(conn, po.PAUSED_KEY, "[]")) == ["Q9"]
    monkeypatch.setattr(po, "enqueue", lambda *a, **k: {})
    assert po.requeue_paused(conn) == 1
    assert json.loads(db.get_setting(conn, po.PAUSED_KEY, "[]")) == []


# ============================================================ P1-8：票證綁使用者
def test_票證綁使用者_跨使用者或轉純字串不可使用():
    t = qw.issue_ticket("scan_schedule", "alice")
    assert qw.consume_ticket(t, "scan_schedule", actor="bob") is False            # 明確以別人身分使用
    t2 = qw.issue_ticket("scan_schedule", "alice")
    assert qw.consume_ticket(str(t2), "scan_schedule") is False                    # 轉純字串（沒有 actor）
    t3 = qw.issue_ticket("scan_schedule", "alice")
    assert qw.consume_ticket(t3, "scan_schedule") is True
    t4 = qw.issue_ticket("scan_schedule", "alice")
    assert qw.consume_ticket(t4, "scan_schedule", actor="alice") is True


# ============================================================ P2-9／10／11／12
def test_延後紀錄寫入失敗_不放行_且有警示(conn, monkeypatch, at, caplog):
    import logging
    at(MON(10))
    monkeypatch.setattr(qw, "_log_deferred", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    qw._HEALTH.clear()
    with caplog.at_level(logging.ERROR, logger="quiet_window"):
        assert qw.defer_if_quiet(conn, "scan_schedule") is True
    assert any("延後紀錄寫入失敗" in r.message for r in caplog.records)
    st = qw.status(conn, MON(10))
    assert "停擺" in st["warning"] and "延後紀錄" in st["warning"]


def test_背景補跑worker頂層例外_不吞_有警示(conn, monkeypatch, at, caplog):
    import logging
    at(MON(15, 30))
    qw.pending_mark(conn, "vrops_import_tick")
    monkeypatch.setattr(scan_service, "_quiet_catchup_run", lambda c: (_ for _ in ()).throw(RuntimeError("worker boom")))
    qw._HEALTH.clear()
    with caplog.at_level(logging.ERROR, logger="quiet_window"):
        scan_service._quiet_catchup_tick(conn)
        for _ in range(50):
            if not scan_service._catchup_lock.locked():
                break
            time.sleep(0.05)
    assert any("背景補跑發生未預期的錯誤" in r.message for r in caplog.records)
    assert "背景補跑" in qw.status(conn, MON(15, 30))["warning"]


def test_pending讀取失敗_有警示(conn, caplog):
    import logging
    qw._HEALTH.clear()
    with caplog.at_level(logging.ERROR, logger="quiet_window"):
        assert qw.pending_list(_Broken()) == []
    assert "pending_read_failed" in qw._HEALTH


def test_補跑重入保護是Lock():
    assert isinstance(scan_service._catchup_lock, type(threading.Lock()))


def test_補跑重入_第二個tick不會再開worker(conn, monkeypatch, at):
    started = threading.Event()
    release = threading.Event()
    runs = []

    def slow(c):
        runs.append(1)
        started.set()
        release.wait(3)
    monkeypatch.setattr(scan_service, "_quiet_catchup_run", slow)
    at(MON(15, 30))
    qw.pending_mark(conn, "vrops_import_tick")
    scan_service._quiet_catchup_tick(conn)
    assert started.wait(2)
    scan_service._quiet_catchup_tick(conn)
    scan_service._quiet_catchup_tick(conn)
    release.set()
    for _ in range(60):
        if not scan_service._catchup_lock.locked():
            break
        time.sleep(0.05)
    assert runs == [1]


def test_run_real_scan_main_直接呼叫也被守門(conn, monkeypatch, at):
    import run_real_scan as rr
    at(MON(10))
    monkeypatch.setattr(rr, "scan_targets", lambda c: (_ for _ in ()).throw(AssertionError("不該連線")))
    assert rr.main() == {"refused": True}


def test_匯出靜默窗設定_格式與scan_segments讀取(conn, tmp_path, at):
    import scan_segments as ss
    db.set_setting(conn, "capacity_holidays", '[{"date": "2026-11-02"}]')
    qw.save_config(conn, True, "09:00", "13:00", "admin")
    cfg = qw.export_config(conn)
    assert cfg["format"] == "webit3-quiet-window" and cfg["version"] == 1 and cfg["tz"] == "Asia/Taipei"
    assert (cfg["start"], cfg["end"], cfg["holidays"], cfg["enabled"], cfg["broken"]) == ("09:00", "13:00", ["2026-11-02"], True, False)
    f = tmp_path / "q.json"
    f.write_text(json.dumps(cfg), encoding="utf-8")
    c = ss.load_quiet_config(str(f))
    tz = ss.MARKET_TZ
    assert ss.in_market_hours(datetime(2026, 11, 3, 10, tzinfo=tz), c) is True
    assert ss.in_market_hours(datetime(2026, 11, 3, 13, 0, tzinfo=tz), c) is False
    assert ss.in_market_hours(datetime(2026, 11, 2, 10, tzinfo=tz), c) is False        # 休市日
    assert ss.in_market_hours(datetime(2026, 11, 7, 10, tzinfo=tz), c) is False
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert ss.in_market_hours(datetime(2026, 11, 7, 3, tzinfo=tz), ss.load_quiet_config(str(bad))) is True        # 壞檔＝視同窗內
    wrong = tmp_path / "w.json"
    wrong.write_text(json.dumps({"format": "x", "version": 1}), encoding="utf-8")
    assert ss.load_quiet_config(str(wrong)).get("broken")
