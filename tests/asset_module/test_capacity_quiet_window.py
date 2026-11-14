"""盤中靜默窗：平日 08:30–15:00（沿用 transfer_policy 既有定義）自動取數一律跳過、窗外補跑；人為主動需二次確認。取數函式全是 mock，不連網。"""
import sqlite3
import sys
from datetime import datetime, time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import capacity_analysis as ca  # noqa: E402
import transfer_policy as tp  # noqa: E402


def mem():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
    c.execute(ca.WU_TABLE_SQL)
    return c


class Fetch:
    def __init__(self):
        self.n = 0

    def __call__(self, hosts, frm, to):
        self.n += 1
        return {}


def day(d, hh, mm=0):
    return datetime(2026, 9, d, hh, mm)       # 9/10 週四、9/12 週六


@pytest.mark.parametrize("t,quiet", [
    (day(10, 8, 29), False), (day(10, 8, 30), True), (day(10, 10, 0), True), (day(10, 14, 59), True), (day(10, 15, 0), False),
    (day(12, 10, 0), False),                               # 週六
])
def test_窗口邊界與週末(t, quiet):
    assert ca.in_quiet_window(t) is quiet


def test_休市日可選_平日休市就不是靜默窗():
    assert ca.in_quiet_window(day(10, 10), {"2026-09-10"}) is False


def test_自動觸發在盤中不得呼叫取數函式_延後_窗外第一個tick補跑():
    c = mem()
    f = Fetch()
    r = ca.wu_refresh(c, f, day(10, 10, 0), hosts=["A"])                       # 模擬 10:00 自動觸發
    assert f.n == 0 and r["ran"] is False and r["deferred"] is True and "盤中靜默" in r["reason"] and "延後" in r["reason"]
    assert c.execute("SELECT value FROM app_settings WHERE key = 'capacity_wu_deferred'").fetchone()
    assert ca.wu_refresh(c, f, day(10, 14, 59), hosts=["A"])["deferred"] is True and f.n == 0
    r = ca.wu_refresh(c, f, day(10, 15, 0), hosts=["A"])                       # 窗外第一個 tick
    assert f.n == 1 and r["ran"] is True and r["in_quiet_window"] is False


def test_週末與窗外自動觸發照常():
    f = Fetch()
    assert ca.wu_refresh(mem(), f, day(12, 10, 0), hosts=["A"])["ran"] is True
    assert ca.wu_refresh(mem(), f, day(10, 8, 29), hosts=["A"])["ran"] is True and f.n == 2


def test_人為主動在窗內要二次確認_確認後才取數_並留稽核標記():
    c = mem()
    f = Fetch()
    r = ca.wu_refresh(c, f, day(10, 10, 0), manual=True, hosts=["A"])
    assert f.n == 0 and r["needs_confirm"] is True and "二次確認" in r["reason"]
    import quiet_window as qw
    r = ca.wu_refresh(c, f, day(10, 10, 0), manual=True, hosts=["A"], ticket=qw.issue_ticket("capacity_wu_refresh", "t"))
    assert f.n == 1 and r["ran"] is True and r["in_quiet_window"] is True
    assert c.execute("SELECT value FROM app_settings WHERE key = 'capacity_wu_inwindow_manual'").fetchone()
    assert ca.wu_refresh(c, f, day(10, 10, 5), manual=True, hosts=["A"], ticket=qw.issue_ticket("capacity_wu_refresh", "t"))["ran"] is False      # 冷卻仍然有效，二次確認不能繞過


def test_靜默窗的定義來自transfer_policy_不是自己寫一份(monkeypatch):
    monkeypatch.setattr(tp, "MARKET_OPEN", time(7, 0))
    f = Fetch()
    r = ca.wu_refresh(mem(), f, day(10, 7, 30), hosts=["A"])
    assert f.n == 0 and r["deferred"] is True
    src = (ROOT / "APP" / "asset-module" / "backend" / "capacity_analysis.py").read_text(encoding="utf-8")
    assert "time(8, 30)" not in src and "MARKET_OPEN" in src


def test_容量分析頁的純讀本機分析不受靜默窗影響():
    c = mem()
    c.execute("CREATE TABLE nmon_series (host TEXT NOT NULL, day TEXT NOT NULL, interval_s INTEGER, samples INTEGER, data BLOB NOT NULL, updated_at TEXT, PRIMARY KEY (host, day))")
    assert ca.overview(c)["hosts"] == []                  # 不呼叫任何取數；overview 沒有 fetcher 參數
