"""2.066.30：「立即匯入」要把待處理的批次吃完（不是只做 20 批就說下一輪）；跟排程／快速通道的匯入互斥（不能同時兩個在跑、不能重複匯入同一批）。"""
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import quiet_window as qw  # noqa: E402
import relay_import as ri  # noqa: E402
from test_opening_peak import _write_files, host  # noqa: E402

BASE = datetime(2026, 10, 5)                       # 都是過去的日子；一批一天（aria_host 同一天只留較新的一批）


def _day(i):
    return (BASE + timedelta(days=i)).strftime("%Y-%m-%d")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    c = db.get_connection()
    drop = tmp_path / "drop"
    drop.mkdir()
    monkeypatch.setattr(ri, "STABLE_SECONDS", -5)
    db.set_setting(c, ri.DIR_KEY, str(drop))
    db.set_setting(c, ri.ENABLED_KEY, "1")
    c.commit()
    qw.set_clock(lambda: datetime(2026, 11, 7, 10, 0))          # 週六：窗外，這份檔案只測匯入本身
    yield c, drop
    qw.set_clock(None)
    c.close()


def _batches(drop, n):
    for i in range(n):
        d = _day(i)
        _write_files(drop, f"ariahost_{i:02d}", [("h.json", host(f"esxi99-{i:02d}.example.test", cpu=[10, 20, 30], day=d))], day=d)


def _n_imported(c):
    return c.execute("SELECT COUNT(*) FROM relay_import_batches WHERE status = 'imported'").fetchone()[0]


def test_人工_一次吃完全部待處理_不留下一輪(env, monkeypatch):
    c, drop = env
    monkeypatch.setattr(ri, "MAX_BATCHES_PER_RUN", 3)
    _batches(drop, 8)
    out = ri.run_scan(c, "manual", "admin")
    assert out["imported"] == 8 and out["remaining"] == 0 and "下一輪" not in out["message"] and "沒處理" not in out["message"], out
    assert _n_imported(c) == 8


def test_排程_仍然一輪只做上限批數_其餘下一輪(env, monkeypatch):
    c, drop = env
    monkeypatch.setattr(ri, "MAX_BATCHES_PER_RUN", 3)
    _batches(drop, 8)
    out = ri.run_scan(c, "schedule")
    assert out["imported"] == 3 and out["remaining"] == 5 and "下一輪" in out["message"]


def test_人工_時間用完就停_講清楚還剩幾批_再按一次接著做(env, monkeypatch):
    c, drop = env
    _batches(drop, 4)
    monkeypatch.setattr(ri, "MANUAL_BUDGET_S", -1)                 # 預算一開始就用完：至少處理第一批，不空轉
    out = ri.run_scan(c, "manual", "admin")
    assert out["imported"] == 1 and out["remaining"] == 3 and "再按一次" in out["message"], out
    monkeypatch.setattr(ri, "MANUAL_BUDGET_S", 100)
    out = ri.run_scan(c, "manual", "admin")
    assert out["imported"] == 3 and out["remaining"] == 0 and _n_imported(c) == 4


def test_互斥_別的匯入正在跑_人工等不到就busy_什麼都不匯_排程馬上busy(env, monkeypatch):
    c, drop = env
    _batches(drop, 2)
    monkeypatch.setattr(ri, "MANUAL_LOCK_WAIT_S", 0.3)
    assert ri._run_lock.acquire(blocking=False)
    try:
        t0 = time.monotonic()
        assert ri.run_scan(c, "schedule")["status"] == "busy" and time.monotonic() - t0 < 0.2        # 排程不等
        t0 = time.monotonic()
        out = ri.run_scan(c, "manual", "admin")
        assert out["status"] == "busy" and time.monotonic() - t0 >= 0.25                            # 人工等了一下才放棄
        assert _n_imported(c) == 0
    finally:
        ri._run_lock.release()
    assert ri.run_scan(c, "manual", "admin")["imported"] == 2                                       # 鎖放掉就正常


def test_互斥_兩個匯入同時跑_每一批只匯一次_不會同時兩個在跑(env, monkeypatch):
    c, drop = env
    _batches(drop, 4)
    calls, running, peak = [], [0], [0]
    real = ri.import_batch
    guard = threading.Lock()

    def slow(conn, folder, *a, **k):
        with guard:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.15)
        try:
            calls.append(folder.name)
            return real(conn, folder, *a, **k)
        finally:
            with guard:
                running[0] -= 1

    monkeypatch.setattr(ri, "import_batch", slow)
    monkeypatch.setattr(ri, "MANUAL_LOCK_WAIT_S", 30)
    res = {}

    def run(name, trigger):
        cn = db.get_connection()
        try:
            res[name] = ri.run_scan(cn, trigger, "admin" if trigger == "manual" else None)
        finally:
            cn.close()

    a = threading.Thread(target=run, args=("a", "schedule"))
    b = threading.Thread(target=run, args=("b", "manual"))
    a.start()
    time.sleep(0.05)                                                # 讓排程那個先拿到鎖
    b.start()
    a.join(30)
    b.join(30)
    assert peak[0] == 1, "同一時間有兩個匯入在跑"
    assert sorted(calls) == sorted(set(calls)) and len(calls) == 4, calls            # 每一批只處理一次
    assert _n_imported(c) == 4
    assert res["a"]["status"] == "ok" and res["b"]["status"] == "ok"
    assert res["a"]["imported"] + res["b"]["imported"] == 4
