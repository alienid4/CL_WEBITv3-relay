"""2.066.30：盤中靜默窗的唯一例外——資料夾名稱 ariahost_ 開頭的批次（Aria ESXi 主機效能）窗內照常匯入；其他批次照舊延後。

不連任何網路；批次在暫存資料夾現做。主機名用 esxi99-*.example.test，資產 IP 用 10.99.x。
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import quiet_window as qw  # noqa: E402
import relay_import as ri  # noqa: E402
from test_opening_peak import _write_files, host  # noqa: E402

DAY1, DAY2 = "2026-11-02", "2026-11-03"
IN_WINDOW = datetime(2026, 11, 3, 10, 0)           # 週二 10:00：盤中
OUT_WINDOW = datetime(2026, 11, 3, 16, 0)          # 週二 16:00：窗外
EDGE_IN = datetime(2026, 11, 3, 8, 30, 0)          # 08:30:00 起在窗內
EDGE_OUT = datetime(2026, 11, 3, 15, 0, 0)         # 15:00:00 解除


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
    db.set_setting(c, ri.INTERVAL_KEY, "1")
    c.commit()
    yield c, drop
    qw.set_clock(None)
    c.close()


def _batch(drop, bid, day=DAY1, name="esxi99-01.example.test"):
    _write_files(drop, bid, [("h.json", host(name, cpu=[10, 20, 30], day=day))], day=day)


def _status(c, bid):
    r = c.execute("SELECT status FROM relay_import_batches WHERE batch_id = ?", (bid,)).fetchone()
    return r[0] if r else None


def _deferred(c):
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='quiet_window_log'").fetchone():
        return 0
    r = c.execute("SELECT SUM(count) FROM quiet_window_log WHERE entry='relay_import_tick' AND kind='deferred'").fetchone()
    return r[0] or 0


def test_窗內_ariahost批次照常匯入_仍記延後(env):
    c, drop = env
    qw.set_clock(lambda: IN_WINDOW)
    _batch(drop, "ariahost_a")
    out = ri.tick(c, IN_WINDOW)
    assert out["status"] == "deferred_quiet_window" and out["exempt_imported"] == 1, out
    assert _status(c, "ariahost_a") == "imported"
    assert _deferred(c) >= 1                                  # 其他批次的延後照記（不因例外而漏記）


def test_窗內_其他批次不放寬_只匯ariahost(env):
    c, drop = env
    qw.set_clock(lambda: IN_WINDOW)
    _batch(drop, "ariahost_a")
    _batch(drop, "other_b", day=DAY2)                         # 同樣是 aria_host 資料，但資料夾名稱不是 ariahost_ 開頭
    out = ri.tick(c, IN_WINDOW)
    assert out["exempt_imported"] == 1
    assert _status(c, "ariahost_a") == "imported" and _status(c, "other_b") is None       # 沒處理、也沒留紀錄＝延後到窗外
    qw.set_clock(lambda: OUT_WINDOW)
    c.execute("DELETE FROM app_settings WHERE key = ?", (ri.LAST_CHECK_KEY,))
    c.commit()
    assert ri.tick(c, OUT_WINDOW)["status"] == "ok"
    assert _status(c, "other_b") == "imported"                # 窗外第一個 tick 補跑


def test_窗內_一次多個ariahost批次都處理_不在第二批被盤中守門截斷(env):
    c, drop = env
    qw.set_clock(lambda: IN_WINDOW)
    _batch(drop, "ariahost_a", DAY1)
    _batch(drop, "ariahost_b", DAY2, name="esxi99-02.example.test")
    out = ri.tick(c, IN_WINDOW)
    assert out["exempt_imported"] == 2 and _status(c, "ariahost_a") == _status(c, "ariahost_b") == "imported"


def test_窗內_沒有任何批次_照舊只記延後(env):
    c, drop = env
    qw.set_clock(lambda: IN_WINDOW)
    out = ri.tick(c, IN_WINDOW)
    assert out == {"status": "deferred_quiet_window"} and _deferred(c) >= 1


def test_窗外_全部照常_跟例外無關(env):
    c, drop = env
    qw.set_clock(lambda: OUT_WINDOW)
    _batch(drop, "ariahost_a")
    _batch(drop, "other_b", day=DAY2)
    out = ri.tick(c, OUT_WINDOW)
    assert out["status"] == "ok" and out["imported"] == 2


@pytest.mark.parametrize("t,inside", [(EDGE_IN, True), (EDGE_OUT, False)])
def test_窗邊界_0830與1500_ariahost都能匯入_其他批次只有窗外(env, t, inside):
    c, drop = env
    qw.set_clock(lambda: t)
    _batch(drop, "ariahost_a")
    _batch(drop, "other_b", day=DAY2)
    ri.tick(c, t)
    assert _status(c, "ariahost_a") == "imported"
    assert (_status(c, "other_b") == "imported") is (not inside)


def test_例外名單只有ariahost():
    assert ri.QUIET_EXEMPT_PREFIXES == ("ariahost_",)
    assert not set(ri.QUIET_EXEMPT_PREFIXES) & set(ri.FAST_PREFIXES)


def test_窗內_資料夾叫ariahost_但manifest來源不是aria_host_不放行(env):
    """獨立審查 11-16 建議 1：例外要綁 manifest 的 source，不能只看資料夾名稱。"""
    import json
    c, drop = env
    qw.set_clock(lambda: IN_WINDOW)
    _batch(drop, "ariahost_fake")
    mf = Path(drop) / "ariahost_fake" / "manifest.json"
    m = json.loads(mf.read_text(encoding="utf-8"))
    m["source"] = "whatsup_alert"
    mf.write_text(json.dumps(m), encoding="utf-8")
    ri.tick(c, IN_WINDOW)
    assert _status(c, "ariahost_fake") is None                     # 沒被匯入（留到窗外）
    assert ri.QUIET_EXEMPT_SOURCE == ri.AriaHostAdapter.name
