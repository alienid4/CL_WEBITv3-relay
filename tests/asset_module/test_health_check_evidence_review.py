"""批 I 獨立審查（2026-10-31）補充：drift 口徑、基線逐台證據、帳號證據的通用 IP。"""
import datetime as dt
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import health_check as hc  # noqa: E402


def test_drift口徑_fixed不算偏移_open與ack算_空基準不算():
    """baseline_drift 的 status：fixed＝符合基線（上線時就寫 fixed）；open／ack＝偏移；基準空值不算。
    SQL 條件要與 health_check 的 ctx["drift"] 同一條（用程式裡的條件字串檢查）。"""
    src = (ROOT / "APP" / "asset-module" / "backend" / "health_check.py").read_text(encoding="utf-8")
    assert "!= 'fixed'" in src or "NOT IN ('fixed'" in src or "status IS NULL OR status != 'fixed'" in src
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE baseline_drift (id INTEGER PRIMARY KEY, asset_serial TEXT, item_key TEXT, baseline TEXT, status TEXT);
        INSERT INTO baseline_drift (asset_serial, item_key, baseline, status) VALUES
          ('A','p1','open','fixed'), ('B','p1','open','open'), ('C','p1','open','ack'), ('D','p1','','open'), ('E','p1',NULL,'open');
    """)
    rows = c.execute("SELECT asset_serial FROM baseline_drift WHERE baseline IS NOT NULL AND baseline != '' "
                     "AND (status IS NULL OR status != 'fixed')").fetchall()
    assert sorted(r[0] for r in rows) == ["B", "C"]


def test_基線逐台證據_全站已回檢但這台全部測不到_不算已驗():
    base = {"drift_last_run": "2026-10-31 06:00:00", "baseline": {"S1": 3, "S2": 3, "S3": 3},
            "golive_at": {"S1": "2026-10-01 00:00:00", "S2": "2026-10-01 00:00:00", "S3": "2026-10-01 00:00:00"},
            "drift_assets": {"S1": [3, 0], "S2": [0, 3]}}                   # S3 完全沒有逐台記錄
    assert hc._drift_state({"asset_serial": "S1"}, base)[0] == hc.PASSED
    st, why = hc._drift_state({"asset_serial": "S2"}, base)
    assert st == hc.NOT_CHECKED and "測不到" in why
    st, why = hc._drift_state({"asset_serial": "S3"}, base)
    assert st == hc.NOT_CHECKED and "沒有這台的比對記錄" in why
    assert hc._drift_state({"asset_serial": "S9"}, base)[0] == hc.NOT_APPLICABLE


def test_帳號證據_通用IP不借別台盤點時間():
    now = dt.datetime(2026, 10, 31, 8, 0, 0)
    ctx = {"acct_last_serial": {}, "acct_last_ip": {"0.0.0.0": "2026-10-30 10:00:00", "10.99.1.1": "2026-10-30 10:00:00"},
           "trace": {}, "now": now, "acct_stale_days": 30}
    assert hc._account_state({"asset_serial": "X", "ip": "0.0.0.0"}, ctx)[0] == hc.NOT_CHECKED        # 不借
    assert hc._account_state({"asset_serial": "X", "ip": "10.99.1.1"}, ctx)[0] == hc.PASSED


def test_帳號證據_SSH失敗時間用解析後比較_格式不同也正確():
    now = dt.datetime(2026, 10, 31, 8, 0, 0)
    ctx = {"acct_last_serial": {"X": "2026-10-30 10:00:00"}, "acct_last_ip": {},
           "trace": {"X": (0, "2026-10-30T12:00:00")},                     # 有 T 的格式，晚於盤點
           "now": now, "acct_stale_days": 30}
    st, why = hc._account_state({"asset_serial": "X", "ip": "10.99.1.1"}, ctx)
    assert st == hc.NOT_CHECKED and "SSH" in why
