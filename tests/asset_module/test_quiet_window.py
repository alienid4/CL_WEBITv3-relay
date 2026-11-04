"""AUD-02 全站盤中靜默窗：平日 08:30–15:00，系統「自己發動」的探測／掃描／收集一律跳過、窗外補跑；人為主動需二次確認。

這份測試的原則（為什麼這樣寫）：
  1. 每個 A 類自動入口都有一個「driver」：把該入口的對外連線函式換成呼叫計數器，再從入口原本的排程進入點觸發。
     - 窗內（週間 10:00）：計數器必須是 0，且有一筆「延後」紀錄；
     - 窗外：計數器必須 >0（證明 driver 真的走得到外連函式，窗內的 0 才不是「根本沒走到」的假綠燈）；
     - 突變：只把這個入口的守門換成「永遠放行」，窗內計數器必須變成 >0（＝拿掉守門，窗內那條測試一定會紅）。
  2. 邊界：08:29:59 窗外、08:30:00 窗內、14:59:59 窗內、15:00:00 窗外；週六日窗外；設定壞掉＝窗內。
  3. 人工路徑：窗內沒 confirm → 409（code=quiet_window_confirm）、有 confirm → 放行並寫稽核；自動路徑沒有 confirm 參數。
  4. 補跑只跑一次；B／C 類在窗內照常。
所有外連函式都是 mock，不連任何網路。
"""
import inspect
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import quiet_window as qw  # noqa: E402
import scan_service  # noqa: E402

MON = lambda h, m=0, s=0: datetime(2026, 11, 2, h, m, s)     # noqa: E731  2026-11-02 是週一
SAT = lambda h, m=0, s=0: datetime(2026, 11, 7, h, m, s)     # noqa: E731
SUN = lambda h, m=0, s=0: datetime(2026, 11, 8, h, m, s)     # noqa: E731


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    c = db.get_connection()
    yield c
    c.close()


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """每次呼叫給一個全新的資料庫連線（同一個測試要在多個時間點各跑一次，避免『上次成功→這次未到間隔』干擾）。"""
    made = []

    def _f():
        monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / f"f{len(made)}.db"))
        db.init_db()
        c = db.get_connection()
        made.append(c)
        return c
    yield _f
    for c in made:
        c.close()


@pytest.fixture
def at():
    """把靜默窗的時鐘設到指定時間（conftest 預設釘在週六；測試後自動還原）。"""
    def _set(t):
        qw.set_clock(lambda: t)
    return _set


# ============================================================ 邊界與設定
@pytest.mark.parametrize("t,quiet", [
    (MON(8, 29, 59), False), (MON(8, 30, 0), True), (MON(10), True), (MON(14, 59, 59), True), (MON(15, 0, 0), False),
    (MON(0, 0, 0), False), (MON(23, 59, 59), False),
    (SAT(10), False), (SUN(10), False),                              # 週末不在窗內
])
def test_窗口邊界_左閉右開_週末不在窗內(conn, t, quiet):
    assert qw.in_quiet_window(conn, t) is quiet


def test_休市日沿用容量分析的設定_平日休市就不在窗內(conn):
    db.set_setting(conn, "capacity_holidays", '[{"date": "2026-11-02", "label": "測試休市"}]')
    assert qw.in_quiet_window(conn, MON(10)) is False
    assert qw.in_quiet_window(conn, datetime(2026, 11, 3, 10)) is True        # 隔天照常


def test_預設值就是_transfer_policy_的開盤時間_沒有第二份時間定義(conn):
    import transfer_policy as tp
    cfg = qw.get_config(conn)
    assert (cfg["start"], cfg["end"]) == (tp.MARKET_OPEN, tp.MARKET_CLOSE)
    src = inspect.getsource(qw)
    assert "time(8, 30)" not in src and "time(15, 0)" not in src


@pytest.mark.parametrize("key,val", [
    ("quiet_window_enabled", "maybe"), ("quiet_window_start", "8點半"), ("quiet_window_end", "25:00"),
    ("quiet_window_start", "16:00"),                                    # 起 >= 迄
])
def test_設定壞掉_視同在窗內_failclosed(conn, key, val):
    db.set_setting(conn, key, val)
    assert qw.get_config(conn)["broken"] is True
    assert qw.in_quiet_window(conn, SAT(3)) is True and qw.in_quiet_window(conn, MON(3)) is True    # 連半夜、週末也當窗內
    assert qw.status(conn, SAT(3))["broken"] is True and "異常" in qw.status(conn, SAT(3))["message"]


def test_讀不出設定_視同在窗內():
    import sqlite3
    bare = sqlite3.connect(":memory:")                      # 連 app_settings 表都沒有
    assert qw.in_quiet_window(bare, SAT(3)) is True
    bare.close()
    assert qw.in_quiet_window(bare, SAT(3)) is True         # 連線已關閉：同樣 fail-closed


def test_管理員關閉_全時段都不是窗內_且狀態標示已關閉(conn):
    qw.save_config(conn, False, "08:30", "15:00", "admin")
    assert qw.in_quiet_window(conn, MON(10)) is False
    s = qw.status(conn, MON(10))
    assert s["enabled"] is False and "已被管理員關閉" in s["message"]
    assert conn.execute("SELECT 1 FROM quiet_window_log WHERE kind='setting_changed' AND actor='admin' AND detail LIKE '%關閉%'").fetchone()
    assert conn.execute("SELECT 1 FROM activity_log WHERE action='quiet_window_DISABLED'").fetchone()      # 關閉有稽核


def test_自訂起訖時間生效_並驗證格式與起小於迄(conn):
    qw.save_config(conn, True, "09:00", "13:30", "admin")
    assert qw.in_quiet_window(conn, MON(8, 59)) is False and qw.in_quiet_window(conn, MON(9)) is True
    assert qw.in_quiet_window(conn, MON(13, 29, 59)) is True and qw.in_quiet_window(conn, MON(13, 30)) is False
    for bad in (("9:00", "13:00"), ("09:00", "09:00"), ("14:00", "09:00"), ("abc", "15:00")):
        with pytest.raises(ValueError):
            qw.save_config(conn, True, bad[0], bad[1], "admin")


def test_狀態_GET_不寫庫(conn):
    before = conn.total_changes
    qw.status(conn, MON(10))
    assert conn.total_changes == before


def test_自動路徑沒有任何繞過參數():
    assert list(inspect.signature(qw.defer_if_quiet).parameters) == ["conn", "entry", "now"]
    for fn in (scan_service._run_due_scans, scan_service.start_scan, scan_service._health_baseline_tick,
               scan_service._post_scan_collect_services, scan_service._quiet_catchup_tick):
        assert not [p for p in inspect.signature(fn).parameters if "confirm" in p or "force" in p or "bypass" in p], fn
    import dispatch_weekly
    import paste_watch
    import vrops_import
    import whatsup_service
    for fn in (dispatch_weekly.tick, paste_watch.tick_in_background, vrops_import.tick, whatsup_service.tick):
        assert not [p for p in inspect.signature(fn).parameters if "confirm" in p or "force" in p or "bypass" in p], fn


def test_守門只接受_A_或_H_類入口_打錯字會炸(conn):
    with pytest.raises(KeyError):
        qw.defer_if_quiet(conn, "no_such_entry", MON(10))
    with pytest.raises(ValueError):
        qw.defer_if_quiet(conn, "ci_graph_tick", MON(10))          # B 類不需要守門


def test_延後紀錄_同入口同一天一列_次數累加(conn):
    for m in range(5):
        assert qw.defer_if_quiet(conn, "scan_schedule", MON(10, m)) is True
    rows = conn.execute("SELECT count, first_at, last_at FROM quiet_window_log WHERE entry='scan_schedule' AND kind='deferred'").fetchall()
    assert len(rows) == 1 and rows[0]["count"] == 5 and rows[0]["first_at"].endswith("10:00:00") and rows[0]["last_at"].endswith("10:04:00")
    assert qw.defer_if_quiet(conn, "scan_schedule", datetime(2026, 11, 3, 10)) is True      # 隔天另一列
    assert conn.execute("SELECT COUNT(*) FROM quiet_window_log").fetchone()[0] == 2


def test_紀錄寫不進去也不放行(conn, monkeypatch):
    monkeypatch.setattr(qw, "_log_deferred", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    assert qw.defer_if_quiet(conn, "scan_schedule", MON(10)) is True


# ============================================================ 每個 A 類入口的 driver
class Counter:
    def __init__(self):
        self.n = 0
        self.ev = threading.Event()

    def __call__(self, *a, **k):
        self.n += 1
        self.ev.set()
        return {"ran": True}


def _wait(c: Counter, expect: bool):
    if expect:
        assert c.ev.wait(3), "預期會呼叫外連函式但沒有（driver 沒走到）"
    else:
        time.sleep(0.15)


def drv_scan_schedule(conn, mp, t):
    c = Counter()
    mp.setattr(scan_service, "_do_scan", c)
    mp.setattr(scan_service, "_running", False)
    scan_service.set_schedule(conn, True, "daily", "01:00", 6)
    scan_service._run_due_scans(conn, t)
    return c


def drv_scan_schedule_start_scan_backstop(conn, mp, t):
    """第二層：直接呼叫 start_scan("schedule")（繞過 _run_due_scans）也不能在盤中開跑。"""
    c = Counter()
    mp.setattr(scan_service, "_do_scan", c)
    mp.setattr(scan_service, "_running", False)
    scan_service.start_scan("schedule")
    return c


def _drv_post(mod, fn_name, call):
    def drv(conn, mp, t):
        c = Counter()
        m = __import__(mod)
        mp.setattr(m, fn_name, c)
        call(conn)
        return c
    return drv


def drv_post_auto_onboard(conn, mp, t):
    import auto_onboard
    c = Counter()
    mp.setattr(auto_onboard, "is_enabled", lambda cn: True)
    mp.setattr(auto_onboard, "scheduled_cycle", c)
    scan_service._post_scan_auto_onboard(conn)
    return c


def drv_vrops(conn, mp, t):
    import vrops_import as vi
    c = Counter()
    mp.setattr(vi, "module_enabled", lambda cn: True)
    mp.setattr(vi, "run_import", c)
    db.set_setting(conn, vi.K_ENABLED, "1")
    vi.tick(conn)
    return c


def drv_whatsup(conn, mp, t):
    import whatsup_service as wu
    c = Counter()
    mp.setattr(wu, "module_enabled", lambda cn: True)
    mp.setattr(wu, "poll", c)
    db.set_setting(conn, wu.K_ENABLED, "1")
    wu.tick(conn)
    return c


def drv_health_baseline(conn, mp, t):
    import health_baseline
    c = Counter()
    mp.setattr(health_baseline, "baseline_due", lambda cn, now: True)
    mp.setattr(health_baseline, "run_weekly_baseline", c)
    scan_service._health_baseline_tick(conn)
    return c


def drv_paste_watch(conn, mp, t):
    import paste_watch
    c = Counter()
    mp.setattr(paste_watch, "check", c)
    paste_watch._ensure(conn)                       # 有等待中的主機＝確實到期（沒有等待中的不算到期、不記延後）
    cols = [r[1] for r in conn.execute("PRAGMA table_info(onboard_paste_pending)")]
    row = {"ip": "10.99.7.1", "state": "waiting", "issued_at": "2026-11-02 08:00:00"}
    keep = {k: v for k, v in row.items() if k in cols}
    conn.execute(f"INSERT OR IGNORE INTO onboard_paste_pending ({','.join(keep)}) VALUES ({','.join('?' * len(keep))})", list(keep.values()))
    conn.commit()
    mp.setattr(paste_watch, "_TICK_RUNNING", threading.Event())
    paste_watch.tick_in_background()
    return c


def drv_dispatch_weekly(conn, mp, t):
    import dispatch_weekly
    c = Counter()
    db.set_setting(conn, "dispatch_weekly_enabled", "1")
    db.set_setting(conn, "dispatch_weekly_weekday", str(t.weekday()))
    due_from = (t - timedelta(minutes=10)).strftime("%H:%M")      # 排程時間＝10 分鐘前（在補跑窗內），所以窗外時『到期』成立
    db.set_setting(conn, "dispatch_weekly_time", due_from)
    db.set_setting(conn, "dispatch_weekly_targets", "10.99.1.0/30")
    mp.setattr(dispatch_weekly, "_run_in_progress", lambda cn: False)
    dispatch_weekly.tick(conn, t, starter=c)
    return c


def drv_capacity(conn, mp, t):
    import capacity_analysis as ca
    c = Counter()
    c2 = lambda hosts, frm, to: (c(), {})[1]     # noqa: E731
    conn.execute(ca.WU_TABLE_SQL)
    ca.wu_refresh(conn, c2, t, hosts=["A"])
    return c


def drv_capacity_archive(conn, mp, t):
    import capacity_archive as cap_ar
    c = Counter()
    mp.setattr(cap_ar, "due_items", lambda cn, now=None: [{"host": "H", "day": "2026-10-30", "stage": "final", "fp": "x", "why": "測試"}])
    mp.setattr(cap_ar, "archive_one", c)
    cap_ar.tick(conn, t)
    return c


def drv_storage_health(conn, mp, t):
    import storage_health as sh
    c = Counter()
    conn.execute("UPDATE feature_flags SET enabled=1 WHERE module_key='storage_api'")
    sh.ensure_tables(conn)
    conn.execute("INSERT INTO storage_devices (name, host, port, credential) VALUES ('d1','10.99.0.5',443,'x')")
    conn.commit()
    mp.setattr(sh, "eligible", lambda cn, dev: None)
    mp.setattr(sh, "check_device", c)
    sh.tick(conn)
    return c


def drv_relay_import(conn, mp, t):
    import os

    import relay_import as ri
    c = Counter()
    d = Path(os.environ["ASSET_DB_PATH"]).parent / "relay_drop"
    (d / "b1").mkdir(parents=True, exist_ok=True)
    (d / "b1" / "manifest.json").write_text("{}", encoding="utf-8")        # 內容不重要：run_scan 已換成計數器，這裡只要『有一批待處理』
    mp.setattr(ri, "STABLE_SECONDS", -5)
    mp.setattr(ri, "run_scan", c)
    ri.ensure(conn)
    db.set_setting(conn, ri.ENABLED_KEY, "1")
    db.set_setting(conn, ri.DIR_KEY, str(d))
    ri.tick(conn, t)
    return c


def drv_transfer(conn, mp, t):
    import os

    import transfer_service as ts
    c = Counter()
    mp.setattr(ts, "spawn", c)
    ts._active_jobs.discard(1)
    ts.start_job(Path(os.environ["ASSET_DB_PATH"]), 1)
    return c


def drv_post_queue(conn, mp, t):
    import post_onboard as po
    c = Counter()
    po.enqueue(None, ["S-QW-1"], by="t", source="onboard", _runner=lambda cn, serial, by, source: (c(), "ok")[1], _sync=True)
    return c


def drv_hk_night(conn, mp, t):
    """目錄清理夜間自動（2.046.0）：時段、預算、選主機、閘門換成固定放行，只量 quiet_window 守門本身。"""
    import dir_housekeeping as hk
    c = Counter()
    hk.set_night(conn, True, "t")
    mp.setattr(hk, "in_night", lambda now=None: True)
    mp.setattr(hk, "in_blocked_window", lambda now=None: False)
    mp.setattr(hk, "seconds_to_block", lambda now=None: 99999)
    mp.setattr(hk, "night_targets", lambda cn: [("S-HK-1", 95.0)])
    mp.setattr(hk, "_gate", lambda cn, s: ({"asset_serial": s}, None))
    mp.setattr(hk, "queue_scan", lambda *a, **k: (c(), {"queued": 1, "ids": []})[1])
    hk.night_tick(conn, t)
    return c


DRIVERS = {
    "hk_night_tick": drv_hk_night,
    "transfer_resumer": drv_transfer,
    "post_onboard_queue": drv_post_queue,
    "scan_schedule": drv_scan_schedule,
    "scan_post_auto_onboard": drv_post_auto_onboard,
    "scan_post_services": _drv_post("service_inventory", "collect_services", lambda cn: scan_service._post_scan_collect_services(cn)),
    "scan_post_accounts": _drv_post("account_inventory", "collect_accounts", lambda cn: scan_service._post_scan_collect_accounts(cn)),
    "scan_post_software": _drv_post("software_inventory", "collect_software", lambda cn: scan_service._post_scan_collect_software(cn)),
    "vrops_import_tick": drv_vrops,
    "whatsup_poll_tick": drv_whatsup,
    "health_baseline_weekly": drv_health_baseline,
    "paste_watch_tick": drv_paste_watch,
    "dispatch_weekly_tick": drv_dispatch_weekly,
    "capacity_wu_refresh": drv_capacity,
    "capacity_archive_tick": drv_capacity_archive,
    "storage_health_tick": drv_storage_health,
    "relay_import_tick": drv_relay_import,
}
A_ENTRIES = sorted(k for k, v in qw.ENTRIES.items() if v["cls"] == "A" and not v["where"].startswith("routers/"))


def test_每個_A_類入口都有_driver_沒有漏測():
    assert set(DRIVERS) == set(A_ENTRIES), (set(A_ENTRIES) ^ set(DRIVERS))


def _has_log(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name='quiet_window_log'").fetchone() is not None


@pytest.mark.parametrize("entry", A_ENTRIES)
def test_窗內自動觸發_不得呼叫任何外連函式_且記延後(conn, monkeypatch, at, entry):
    at(MON(10))
    c = DRIVERS[entry](conn, monkeypatch, MON(10))
    _wait(c, expect=False)
    assert c.n == 0, f"{entry}：盤中自動觸發卻呼叫了外連函式"
    row = conn.execute("SELECT count FROM quiet_window_log WHERE entry=? AND kind='deferred'", (entry,)).fetchone()
    assert row and row["count"] >= 1, f"{entry}：沒有留下『盤中靜默，延後』紀錄"


@pytest.mark.parametrize("entry", A_ENTRIES)
def test_窗外同樣觸發會呼叫外連函式_證明_driver_走得到(conn, monkeypatch, at, entry):
    at(SAT(10))
    c = DRIVERS[entry](conn, monkeypatch, SAT(10))
    _wait(c, expect=True)
    assert c.n >= 1
    assert (not _has_log(conn)) or conn.execute("SELECT 1 FROM quiet_window_log WHERE entry=?", (entry,)).fetchone() is None


@pytest.mark.parametrize("entry", A_ENTRIES)
def test_邊界_0829到0830_1459到1500(fresh, monkeypatch, at, entry):
    """週一 08:29:59 照常、08:30:00 起跳過；14:59:59 仍跳過、15:00:00 起照常。"""
    for t, runs in ((MON(8, 29, 59), True), (MON(8, 30, 0), False), (MON(14, 59, 59), False), (MON(15, 0, 0), True)):
        at(t)
        c = DRIVERS[entry](fresh(), monkeypatch, t)
        _wait(c, expect=runs)
        assert (c.n >= 1) is runs, f"{entry} @ {t.time()}"


@pytest.mark.parametrize("entry", A_ENTRIES)
def test_突變_拿掉這個入口的守門_窗內測試必須變紅(conn, monkeypatch, at, entry):
    """把『只有這個入口』的守門換成永遠放行：外連函式一定會被呼叫＝上面『窗內 n==0』那條會紅。"""
    real = qw.defer_if_quiet
    monkeypatch.setattr(qw, "defer_if_quiet", lambda cn, e, now=None: False if e == entry else real(cn, e, now))
    at(MON(10))
    c = DRIVERS[entry](conn, monkeypatch, MON(10))
    _wait(c, expect=True)
    assert c.n >= 1, f"突變沒被偵測到：拿掉 {entry} 的守門，窗內仍然沒有外連——測試鑑別力不足"


def test_突變_start_scan_第二層也守(conn, monkeypatch, at):
    at(MON(10))
    c = drv_scan_schedule_start_scan_backstop(conn, monkeypatch, MON(10))
    _wait(c, expect=False)
    assert c.n == 0
    real = qw.defer_if_quiet
    monkeypatch.setattr(qw, "defer_if_quiet", lambda cn, e, now=None: False if e == "scan_schedule" else real(cn, e, now))
    c = drv_scan_schedule_start_scan_backstop(conn, monkeypatch, MON(10))
    _wait(c, expect=True)
    assert c.n == 1


def test_管理員關閉靜默窗_自動入口照常(conn, monkeypatch, at):
    qw.save_config(conn, False, "08:30", "15:00", "admin")
    at(MON(10))
    c = DRIVERS["vrops_import_tick"](conn, monkeypatch, MON(10))
    assert c.n == 1


def test_設定壞掉_自動入口全部不動(conn, monkeypatch, at):
    db.set_setting(conn, "quiet_window_end", "壞掉")
    at(SAT(3))
    for entry in ("vrops_import_tick", "whatsup_poll_tick", "scan_post_services"):
        c = DRIVERS[entry](conn, monkeypatch, SAT(3))
        _wait(c, expect=False)
        assert c.n == 0, entry


# ============================================================ 補跑：窗外第一個 tick 補一次，不重複
def test_每日掃描_窗內延後_窗外第一個tick補跑_只一次(conn, monkeypatch, at):
    runs = []
    monkeypatch.setattr(scan_service, "_do_scan", lambda trig, only, light, run=None: runs.append(trig))
    monkeypatch.setattr(scan_service, "_running", False)
    scan_service.set_schedule(conn, True, "daily", "09:00", 6)
    for m in (0, 30):                                                   # 09:00、09:30 兩個 tick 都在窗內
        at(MON(9, m))
        scan_service._run_due_scans(conn, MON(9, m))
    time.sleep(0.1)
    assert runs == []
    at(MON(15, 0, 0))
    scan_service._run_due_scans(conn, MON(15, 0, 0))                    # 窗外第一個 tick
    for _ in range(50):
        if runs:
            break
        time.sleep(0.05)
    assert runs == ["schedule"]
    # 這輪跑完會有 scan_runs 列；用它模擬『今天已經被排程觸發過』，後面的 tick 不得重跑
    conn.execute("INSERT INTO scan_runs (trigger, status, started_at) VALUES ('schedule','ok',?)", ("2026-11-02 15:00:05",))
    conn.commit()
    monkeypatch.setattr(scan_service, "_running", False)
    at(MON(15, 1))
    scan_service._run_due_scans(conn, MON(15, 1))
    time.sleep(0.15)
    assert runs == ["schedule"]


def test_間隔制整合_窗內不跑_窗外第一個tick跑一次_之後遵守間隔(conn, monkeypatch, at):
    import vrops_import as vi
    calls = []

    def fake_run(c, trig, user):
        calls.append(trig)
        c.execute("CREATE TABLE IF NOT EXISTS vrops_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT)")
        c.execute("INSERT INTO vrops_runs (started_at) VALUES (?)", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
        c.commit()
        return {"status": "ok"}
    monkeypatch.setattr(vi, "module_enabled", lambda cn: True)
    monkeypatch.setattr(vi, "run_import", fake_run)
    monkeypatch.setattr(vi, "ensure_tables", lambda cn: cn.execute("CREATE TABLE IF NOT EXISTS vrops_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT)"))
    db.set_setting(conn, vi.K_ENABLED, "1")
    for m in range(0, 60, 15):
        at(MON(10, m))
        assert vi.tick(conn)["status"] == "deferred_quiet_window"
    assert calls == []
    at(MON(15, 0))
    assert vi.tick(conn)["status"] == "ok" and calls == ["schedule"]
    at(MON(15, 1))
    assert vi.tick(conn)["status"] == "not_due" and calls == ["schedule"]


def test_每週收集_補跑窗3小時整個落在盤中_窗外由pending表補跑一次(conn, monkeypatch, at):
    """設週一 09:00（補跑窗到 12:00）：整個補跑窗都在盤中。15:00 之後由補跑 tick 讀 pending 表補跑，且只一次。"""
    import collect_dispatch
    import dispatch_weekly
    c = Counter()
    monkeypatch.setattr(collect_dispatch, "run_dispatch", c)             # 補跑改『同步』跑完（在補跑背景執行緒裡）才算完成
    db.set_setting(conn, "dispatch_weekly_enabled", "1")
    db.set_setting(conn, "dispatch_weekly_weekday", "0")
    db.set_setting(conn, "dispatch_weekly_time", "09:00")
    db.set_setting(conn, "dispatch_weekly_targets", "10.99.1.0/30")
    monkeypatch.setattr(dispatch_weekly, "_run_in_progress", lambda cn: False)
    for t in (MON(9), MON(11, 59), MON(13)):
        at(t)
        assert dispatch_weekly.tick(conn, t, starter=c) is False
    assert c.n == 0 and [p["entry"] for p in qw.pending_list(conn)] == ["dispatch_weekly_tick"]
    at(MON(14))
    scan_service._quiet_catchup_tick(conn, sync=True)                              # 還在窗內
    assert c.n == 0
    at(MON(15, 0))
    monkeypatch.setattr(dispatch_weekly, "datetime", type("D", (datetime,), {"now": staticmethod(lambda: MON(15, 0))}))
    scan_service._quiet_catchup_tick(conn, sync=True)                              # 窗外第一個 tick
    assert c.n == 1 and qw.pending_list(conn) == []
    scan_service._quiet_catchup_tick(conn, sync=True)
    assert c.n == 1                                                     # 不重複


def test_每週收集_週六夜間照常_沒被延後過的過了補跑窗就不補(conn, monkeypatch, at):
    import dispatch_weekly
    c = Counter()
    db.set_setting(conn, "dispatch_weekly_enabled", "1")
    db.set_setting(conn, "dispatch_weekly_weekday", "5")
    db.set_setting(conn, "dispatch_weekly_time", "22:00")
    db.set_setting(conn, "dispatch_weekly_targets", "10.99.1.0/30")
    monkeypatch.setattr(dispatch_weekly, "_run_in_progress", lambda cn: False)
    assert dispatch_weekly.is_due(conn, datetime(2026, 11, 7, 23, 0)) is True                 # 週六夜間：照常（行為不變）
    assert dispatch_weekly.is_due(conn, datetime(2026, 11, 8, 8, 0)) is False                 # 隔天早上不補（沒延後紀錄的話維持原本行為）
    assert dispatch_weekly.is_due(conn, datetime(2026, 11, 7, 23, 0).replace(hour=1)) is False   # 還沒到點


def _post_mocks(monkeypatch):
    calls = {"svc": 0, "acc": 0, "sw": 0, "ob": 0}
    import account_inventory
    import auto_onboard
    import golive
    import service_inventory
    import software_inventory
    monkeypatch.setattr(service_inventory, "collect_services", lambda *a, **k: calls.__setitem__("svc", calls["svc"] + 1))
    monkeypatch.setattr(account_inventory, "collect_accounts", lambda *a, **k: calls.__setitem__("acc", calls["acc"] + 1))
    monkeypatch.setattr(software_inventory, "collect_software", lambda *a, **k: calls.__setitem__("sw", calls["sw"] + 1))
    monkeypatch.setattr(auto_onboard, "is_enabled", lambda c: True)
    monkeypatch.setattr(auto_onboard, "scheduled_cycle", lambda **k: calls.__setitem__("ob", calls["ob"] + 1))
    monkeypatch.setattr(golive, "run_drift_check", lambda c: None)
    return calls


def test_掃描後處理_盤中被延後_窗外補跑每步一次_走pending表(conn, monkeypatch, at):
    calls = _post_mocks(monkeypatch)
    at(MON(9))
    with qw.use(qw.Run("scan_schedule", human=False)):                  # 夜間掃描拖進盤中（非人為 Run）
        scan_service._post_scan_chain(conn)
    assert calls == {"svc": 0, "acc": 0, "sw": 0, "ob": 0}
    assert {p["entry"] for p in qw.pending_list(conn)} == {"scan_post_auto_onboard", "scan_post_services", "scan_post_accounts", "scan_post_software"}
    at(MON(10))
    scan_service._quiet_catchup_tick(conn, sync=True)                              # 還在窗內：不補
    assert calls == {"svc": 0, "acc": 0, "sw": 0, "ob": 0}
    at(MON(15, 0))
    scan_service._quiet_catchup_tick(conn, sync=True)                              # 窗外第一個 tick
    assert calls == {"svc": 1, "acc": 1, "sw": 1, "ob": 1} and qw.pending_list(conn) == []
    scan_service._quiet_catchup_tick(conn, sync=True)                              # 再一個 tick：不重複
    assert calls == {"svc": 1, "acc": 1, "sw": 1, "ob": 1}


def test_硬守門_窗內直接呼叫後處理步驟_沒有Run一律被擋(conn, monkeypatch, at):
    """舊版 auto=False 可繞過；現在沒有任何旗標：窗內沒有人為授權的 Run 就不連線。"""
    calls = _post_mocks(monkeypatch)
    at(MON(10))
    scan_service._post_scan_collect_services(conn)
    scan_service._post_scan_collect_accounts(conn)
    scan_service._post_scan_collect_software(conn)
    scan_service._post_scan_auto_onboard(conn)
    assert calls == {"svc": 0, "acc": 0, "sw": 0, "ob": 0}


def test_人為持票證的Run_窗內後處理照跑(conn, monkeypatch, at):
    calls = _post_mocks(monkeypatch)
    at(MON(10))
    tk = qw.issue_ticket("scan_schedule", "alice")
    run = qw.begin(conn, "scan_schedule", tk)
    assert run is not None and run.human
    with qw.use(run):
        scan_service._post_scan_chain(conn)
    assert calls == {"svc": 1, "acc": 1, "sw": 1, "ob": 1}


# ============================================================ B／C 類在窗內照常
def test_C類通知_B類本機工作_窗內照常(conn, monkeypatch, at):
    """寄信（vuln／eos／digest）、Teams、CI 圖譜、RVTools 檔案匯入：窗內照呼叫，不經任何靜默窗守門。"""
    at(MON(10))
    import ci_graph
    import daily_digest
    import eos_notify
    import teams_notify
    import vcenter_autoimport
    import vuln_notify
    hits = []
    monkeypatch.setattr(vuln_notify, "tick", lambda c, now: hits.append("vuln"))
    monkeypatch.setattr(eos_notify, "tick", lambda c, now: hits.append("eos"))
    monkeypatch.setattr(daily_digest, "tick", lambda c, now: hits.append("digest"))
    monkeypatch.setattr(teams_notify, "tick", lambda c: hits.append("teams"))
    monkeypatch.setattr(vcenter_autoimport, "tick", lambda c: hits.append("rvtools"))
    monkeypatch.setattr(ci_graph, "is_due", lambda c, n: True)
    monkeypatch.setattr(ci_graph, "run_rebuild", lambda *a, **k: hits.append("ci"))
    for f in (scan_service._vuln_notify_tick, scan_service._eos_notify_tick, scan_service._daily_digest_tick,
              scan_service._teams_notify_tick, scan_service._vcenter_autoimport_tick, scan_service._ci_graph_tick):
        f(conn)
    assert sorted(hits) == ["ci", "digest", "eos", "rvtools", "teams", "vuln"]
    assert (not _has_log(conn)) or conn.execute("SELECT COUNT(*) FROM quiet_window_log").fetchone()[0] == 0     # B／C 不產生延後紀錄


# ============================================================ 人工路徑：窗內二次確認＋稽核
def test_人工路徑_窗內沒帶確認_409_帶了放行並稽核(conn, at):
    from fastapi import HTTPException
    at(MON(10))
    with pytest.raises(HTTPException) as ei:
        qw.require_manual_confirm(conn, "scan_schedule", False, "alice", "10.99.0.5")
    assert ei.value.status_code == 409 and ei.value.detail["code"] == "quiet_window_confirm"
    assert "盤中靜默" in ei.value.detail["message"] and "確認要人為執行" in ei.value.detail["message"]
    assert not _has_log(conn) or conn.execute("SELECT 1 FROM quiet_window_log WHERE kind='manual_confirmed'").fetchone() is None
    qw.require_manual_confirm(conn, "scan_schedule", True, "alice", "10.99.0.5")        # 不丟例外＝放行
    r = conn.execute("SELECT actor, entry, first_at, detail FROM quiet_window_log WHERE kind='manual_confirmed'").fetchone()
    assert r["actor"] == "alice" and r["entry"] == "scan_schedule" and "10.99.0.5" in r["detail"] and r["first_at"].startswith("2026-11-02 10:00")
    assert conn.execute("SELECT username, ip, path FROM activity_log WHERE action='quiet_window_manual'").fetchone()["username"] == "alice"


def test_人工路徑_窗外不問不記(conn, at):
    at(SAT(10))
    qw.require_manual_confirm(conn, "scan_schedule", False, "alice")
    assert not _has_log(conn) or conn.execute("SELECT COUNT(*) FROM quiet_window_log").fetchone()[0] == 0


def test_人工路徑_稽核寫不進去就不放行(conn, at, monkeypatch):
    from fastapi import HTTPException
    at(MON(10))
    monkeypatch.setattr(qw, "_log_event", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(HTTPException) as ei:
        qw.require_manual_confirm(conn, "scan_schedule", True, "alice")
    assert ei.value.status_code == 500


MANUAL_ENDPOINTS = [   # (handler 名稱, 入口代號, 放行時要 mock 的 (模組, 屬性))
    ("scan_run_endpoint", "scan_schedule", ("scan_service", "start_scan")),
    ("collect_services_endpoint", "scan_post_services", ("service_inventory", "collect_services")),
    ("software_collect_endpoint", "scan_post_software", ("software_inventory", "collect_software")),
    ("collect_accounts_endpoint", "scan_post_accounts", ("account_inventory", "collect_accounts")),
    ("health_check_baseline_run", "health_baseline_weekly", ("health_baseline", "run_weekly_baseline")),
    ("auto_onboard_run_now", "scan_post_auto_onboard", ("auto_onboard", "scheduled_cycle")),
]


@pytest.mark.parametrize("handler,entry,target", MANUAL_ENDPOINTS)
def test_人工端點_窗內沒確認409_確認後才執行(conn, monkeypatch, at, handler, entry, target):
    import api
    from fastapi import HTTPException
    c = Counter()
    mod = __import__(target[0])
    monkeypatch.setattr(mod, target[1], c)
    if handler == "auto_onboard_run_now":
        import onboard_engine
        monkeypatch.setattr(onboard_engine, "resolve_collector_ip", lambda cn: "10.99.0.1")
    fn = getattr(api, handler)
    session = {"username": "alice"}
    at(MON(10))
    with pytest.raises(HTTPException) as ei:
        fn(request=None, confirm_in_window=False, session=session, conn=conn)
    assert ei.value.status_code == 409 and ei.value.detail["code"] == "quiet_window_confirm" and ei.value.detail["entry"] == entry
    assert c.n == 0                                                        # 409 之前不可已經連線
    fn(request=None, confirm_in_window=True, session=session, conn=conn)
    assert c.n == 1
    assert conn.execute("SELECT actor FROM quiet_window_log WHERE kind='manual_confirmed' AND entry=?", (entry,)).fetchone()["actor"] == "alice"
    at(SAT(10))
    c.n = 0
    fn(request=None, confirm_in_window=False, session=session, conn=conn)  # 窗外：不用確認
    assert c.n == 1


def test_人工端點_vrops_whatsup_collect_dispatch(conn, monkeypatch, at):
    import api
    import collect_dispatch
    import vrops_import
    import whatsup_service
    from fastapi import HTTPException
    c1, c2, c3 = Counter(), Counter(), Counter()
    monkeypatch.setattr(vrops_import, "run_import", c1)
    monkeypatch.setattr(whatsup_service, "poll", c2)
    monkeypatch.setattr(collect_dispatch, "run_dispatch", c3)
    monkeypatch.setattr(collect_dispatch, "registered_targets", lambda cn, t: (t, None))
    at(MON(10))
    for fn, kw in ((api.vrops_run, {"user": "alice"}), (api.whatsup_poll, {"user": "alice"})):
        with pytest.raises(HTTPException) as ei:
            fn(request=None, confirm_in_window=False, conn=conn, **kw)
        assert ei.value.status_code == 409
    body = api.CollectDispatchBody(targets="10.99.1.5", registered_only=False, full=True)
    with pytest.raises(HTTPException) as ei:
        api.collect_dispatch_run(body=body, request=None, confirm_in_window=False, session={"username": "alice"}, conn=conn)
    assert ei.value.status_code == 409 and (c1.n, c2.n, c3.n) == (0, 0, 0)
    api.vrops_run(request=None, confirm_in_window=True, conn=conn, user="alice")
    api.whatsup_poll(request=None, confirm_in_window=True, conn=conn, user="alice")
    api.collect_dispatch_run(body=body, request=None, confirm_in_window=True, session={"username": "alice"}, conn=conn)
    assert (c1.n, c2.n, c3.n) == (1, 1, 1)


# ============================================================ API（HTTP 層）
def _client(tmp_path):
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
    cl = TestClient(api.app)
    c = db.get_connection(p)
    db.create_user(c, "admin", auth.hash_password("test-password-123"), role="admin")
    db.create_user(c, "viewer1", auth.hash_password("test-password-123"), role="viewer")
    c.close()
    return cl, api, p


def test_HTTP_scan_run_窗內409_帶確認放行_狀態與設定API(tmp_path, monkeypatch, at):
    cl, api, p = _client(tmp_path)
    try:
        assert cl.post("/api/auth/login", json={"username": "admin", "password": "test-password-123"}).status_code == 200
        started = Counter()
        monkeypatch.setattr(api.scan_service, "start_scan", lambda *a, **k: (started(), True)[1])
        at(MON(10))
        r = cl.post("/api/scan/run")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "quiet_window_confirm" and started.n == 0
        r = cl.post("/api/scan/run?confirm_in_window=true")
        assert r.status_code == 200 and started.n == 1
        s = cl.get("/api/quiet-window").json()
        assert s["in_quiet_window"] is True and "盤中靜默中" in s["message"] and "15:00" in s["message"]
        assert any(x["kind"] == "manual_confirmed" and x["actor"] == "admin" for x in s["recent"])
        # admin 可改；關閉有稽核
        r = cl.put("/api/quiet-window", json={"enabled": False, "start": "08:30", "end": "15:00"})
        assert r.status_code == 200 and r.json()["enabled"] is False and "已被管理員關閉" in r.json()["message"]
        assert cl.put("/api/quiet-window", json={"enabled": True, "start": "16:00", "end": "08:00"}).status_code == 400
    finally:
        api.app.dependency_overrides.clear()


def test_HTTP_非admin不能改設定(tmp_path):
    cl, api, p = _client(tmp_path)
    try:
        assert cl.post("/api/auth/login", json={"username": "viewer1", "password": "test-password-123"}).status_code == 200
        assert cl.get("/api/quiet-window").status_code == 200                       # 看得到狀態
        assert cl.put("/api/quiet-window", json={"enabled": False, "start": "08:30", "end": "15:00"}).status_code == 403
    finally:
        api.app.dependency_overrides.clear()
