"""主機自我檢查 批次 2：健康基準快照＋方向性 diff＋週六排程判定。

盯的重點：diff 方向性（消失 port＝紅、新增＝提示）、快照修剪、週六 due 判定。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import datetime as dt  # noqa: E402

import db  # noqa: E402
import health_baseline as hb  # noqa: E402


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _result(ip, ports, disks=None, overall="green"):
    return {"ip": ip, "overall": overall, "reachable": True,
            "ports": [{"port": p} for p in ports],
            "disks": disks or [{"mount": "/", "use_pct": 50}],
            "mem": {"used_pct": 30}, "load": {"per_cpu": 0.5}}


def test_存快照與取最近():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            hb.save_snapshot(conn, _result("10.99.0.1", [22, 80]))
            b = hb.latest_baseline(conn, "10.99.0.1")
            assert b is not None
            import json
            assert json.loads(b["ports_json"]) == [22, 80]
        finally:
            conn.close()


def test_快照修剪到上限():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            for i in range(hb.SNAP_KEEP + 4):
                hb.save_snapshot(conn, _result("10.99.0.1", [22, 1000 + i]))
            n = conn.execute("SELECT COUNT(*) FROM health_snapshot WHERE ip=?",
                             ("10.99.0.1",)).fetchone()[0]
            assert n == hb.SNAP_KEEP
        finally:
            conn.close()


def test_diff方向性_消失是紅新增是提示():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            hb.save_snapshot(conn, _result("10.99.0.1", [22, 80, 3306],
                                           disks=[{"mount": "/", "use_pct": 60}]))
            base = hb.latest_baseline(conn, "10.99.0.1")
            # 現在少了 3306（DB 掉了）、多了 9999、磁碟從 60→85
            cur = _result("10.99.0.1", [22, 80, 9999], disks=[{"mount": "/", "use_pct": 85}])
            d = hb.diff(base, cur)
            assert d["missing_ports"] == [3306] and d["level"] == "red"   # 消失＝紅
            assert d["new_ports"] == [9999]                               # 新增＝提示，不升紅
            assert d["disk_jumps"] and d["disk_jumps"][0]["delta"] == 25
        finally:
            conn.close()


def test_diff沒基準回None():
    assert hb.diff(None, _result("10.99.0.1", [22])) is None


def test_週六才跑且同週不重跑():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            sat = dt.datetime(2026, 9, 26, 4, 0, 0)      # 週六 04:00
            fri = dt.datetime(2026, 9, 25, 4, 0, 0)      # 週五
            assert hb.baseline_due(conn, fri) is False
            assert hb.baseline_due(conn, sat) is True
            db.set_setting(conn, hb._LAST_RUN_KEY, "2026-09-26")
            assert hb.baseline_due(conn, sat) is False   # 本週跑過了
            nxt = dt.datetime(2026, 10, 3, 4, 0, 0)      # 下一個週六
            assert hb.baseline_due(conn, nxt) is True
        finally:
            conn.close()


def test_週基準對已納管主機收一輪():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            db.insert_hardware(conn, asset_serial="A-1", hostname="h1", ip="10.99.0.1", collect_ok=1)
            db.insert_hardware(conn, asset_serial="A-2", hostname="h2", ip="10.99.0.2", collect_ok=1)
            conn.commit()

            def fake_batch(ips, platform_of=None, key_path=None):
                return [_result(ip, [22, 80]) for ip in ips]
            r = hb.run_weekly_baseline(conn, _check_batch=fake_batch)
            assert r["targets"] == 2 and r["saved"] == 2
            assert hb.latest_baseline(conn, "10.99.0.1") is not None
        finally:
            conn.close()
