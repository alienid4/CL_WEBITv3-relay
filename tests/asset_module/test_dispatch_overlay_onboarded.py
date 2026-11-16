"""收集結果讀取時疊上「之後才納管成功」（2026-10-20）。

使用者批次納管 18 台全部成功，重整後待納管表還寫「待佈身分／請執行納管」——存著的那輪結果
沒人回頭對。修法：讀的時候疊現況，不改寫存著的結果。
"""
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import collect_dispatch as cd  # noqa: E402
import db  # noqa: E402

def _later(minutes=60):
    """測試當下才算（不要在 import 時算：全套跑十幾分鐘，import 時的「之後」會變成「之前」）。"""
    return (datetime.now() + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")


def _setup(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    conn = db.get_connection(p)
    db.insert_hardware(conn, asset_serial="SN-1", ip="10.99.0.1", environment="測試", asset_status="使用中")
    cd.run_dispatch(conn, "10.99.0.1", prober=lambda ip: [22],
                    ssh_prober=lambda ip: (False, "Permission denied (publickey)"))
    r = cd.latest_run(conn)["results"][0]
    assert r["status"] in (cd.STATUS_NEEDS_CREDENTIAL, cd.STATUS_ONBOARD_FAILED)
    return conn


def _audit(conn, ok, trigger="manual", at=None):
    conn.execute("INSERT INTO onboard_audit (target_ip, platform, login_user, trigger, ok, stage, "
                 "message, created_at) VALUES ('10.99.0.1','linux','sysinfra',?,?,'verify','x',?)",
                 (trigger, ok, at or _later()))
    conn.commit()


def test_那輪之後納管成功_重整後顯示已收集_而且不改寫存著的結果():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _setup(tmp)
        before = conn.execute("SELECT status, message FROM collect_dispatch_result").fetchone()
        later = _later()
        _audit(conn, 1, at=later)
        out = cd.latest_run(conn)
        r = out["results"][0]
        assert r["status"] == cd.STATUS_COLLECTED
        assert "之後已納管成功" in r["message"] and later in r["message"]
        assert r["overlay_at"] == later and out["merged"]["overlaid"] == 1
        assert out["collected"] == 1, "頁簽／卡片數字跟著疊上去的狀態走"
        assert sum(out["by_tab"].values()) == out["total"]
        after = conn.execute("SELECT status, message FROM collect_dispatch_result").fetchone()
        assert tuple(after) == tuple(before), "存著的那輪結果不可以被改寫"
        conn.close()


def test_納管失敗不疊():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _setup(tmp)
        _audit(conn, 0)
        r = cd.latest_run(conn)["results"][0]
        assert r["status"] != cd.STATUS_COLLECTED and not r.get("overlay_at")
        conn.close()


def test_成功之後又取消納管不疊():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _setup(tmp)
        _audit(conn, 1)
        _audit(conn, 1, trigger="revoke", at=_later(61))
        r = cd.latest_run(conn)["results"][0]
        assert r["status"] != cd.STATUS_COLLECTED
        conn.close()


def test_納管成功早於那輪收集不疊():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _setup(tmp)
        _audit(conn, 1, at="2000-01-01 00:00:00")
        r = cd.latest_run(conn)["results"][0]
        assert r["status"] != cd.STATUS_COLLECTED
        conn.close()


def test_收集帳號之後確認收得到也算():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _setup(tmp)
        conn.execute("UPDATE hardware SET collect_ok = 1, collect_checked_at = ? WHERE ip = '10.99.0.1'",
                     (_later(),))
        conn.commit()
        r = cd.latest_run(conn)["results"][0]
        assert r["status"] == cd.STATUS_COLLECTED and "收集帳號確認收得到" in r["message"]
        conn.close()


def test_收集狀態寫著收得到_但最後是取消納管_也不疊():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _setup(tmp)
        conn.execute("UPDATE hardware SET collect_ok = 1, collect_checked_at = ? WHERE ip = '10.99.0.1'",
                     (_later(),))
        conn.commit()
        _audit(conn, 1, trigger="revoke", at=_later(61))
        r = cd.latest_run(conn)["results"][0]
        assert r["status"] != cd.STATUS_COLLECTED
        conn.close()
