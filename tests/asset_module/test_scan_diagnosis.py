"""掃描診斷（GET /api/scan/diagnosis，唯讀）：為什麼網段掃描掃不出東西（2026-11-22）。

每條「可能原因」各一組測試，另外釘住：正常情況不誤報、端點要登入、探測埠與逾時取自程式常數、診斷不寫資料庫。
例子 IP 一律 10.99.x。改回舊寫法（門檻取等號、streak 不歸零、排程不看緩衝、寫死埠…）都會有測試紅。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
import net_scan  # noqa: E402
import scan_diagnosis as sd  # noqa: E402
import scan_scope  # noqa: E402
import scan_service  # noqa: E402

NOW = datetime(2026, 11, 22, 10, 0, 0)         # 週日 10:00
A, B, C = "10.99.1.0/24", "10.99.2.0/24", "10.99.3.0/24"
_PW = "pw-Test-12345"


def _ts(days_ago=0, hm="01:00"):
    d = (NOW - timedelta(days=days_ago)).date()
    return f"{d.isoformat()} {hm}:00"


@pytest.fixture
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    yield c
    c.close()


def _seg(conn, cidr, enabled=1, scan_time=None, name=None):
    conn.execute("INSERT INTO connections (name, connection_type, target, enabled, scan_time) VALUES (?,?,?,?,?)",
                 (name or f"網段 {cidr}", "網路掃描", cidr, enabled, scan_time))
    conn.commit()


def _batch(conn, when, per_seg: dict, failed=()):
    """per_seg：{cidr: 成功筆數}；failed：這批失敗的網段（coverage ok=0＋一筆 scan_ok=0 列）。"""
    scan_scope._ensure(conn)
    for cidr, n in per_seg.items():
        conn.execute("INSERT INTO scan_coverage (scan_time, cidr, source, ok, created_at) VALUES (?,?,?,?,?)",
                     (when, cidr, "s", 0 if cidr in failed else 1, when))
        if cidr in failed:
            conn.execute("INSERT INTO scan_history (scan_time, ip, segment, scan_ok) VALUES (?,?,?,0)", (when, None, cidr))
            continue
        for i in range(n):
            conn.execute("INSERT INTO scan_history (scan_time, hostname, ip, segment, scan_ok) VALUES (?,?,?,?,1)",
                         (when, f"h{i}", f"{cidr.split('/')[0][:-1]}{i + 1}", cidr))
    conn.commit()


def _run(conn, when, trigger="schedule", status="ok", error=None):
    conn.execute("INSERT INTO scan_runs (trigger, status, found_count, error, started_at, finished_at) VALUES (?,?,?,?,?,?)",
                 (trigger, status, 0, error, when, when))
    conn.commit()


def _healthy(conn):
    """正常基準：一個網段、今天 01:00 掃到 5 台、排程今天 01:00 跑過。"""
    _seg(conn, A)
    _batch(conn, _ts(0), {A: 5})
    _run(conn, _ts(0))


def _codes(d):
    return [c["code"] for c in d["causes"]]


def test_healthy_no_false_alarm(conn):
    _healthy(conn)
    d = sd.diagnose(conn, NOW)
    assert d["status"] == "正常" and d["causes"] == []
    assert d["latest_batch"]["found"] == 5 and d["summary"]["segments"] == 1
    s = d["segments"][0]
    assert s["status"] == "正常" and s["last_count"] == 5 and s["covered_text"] == "成功涵蓋"


def test_no_segments_configured(conn):
    d = sd.diagnose(conn, NOW)
    assert "no_segments" in _codes(d) and d["status"] == "待設定" and d["summary"]["segments"] == 0
    assert "fallback" in [c for c in d["causes"] if c["code"] == "no_segments"][0]["evidence"]


def test_non_cidr_connection_is_not_a_segment(conn):
    conn.execute("INSERT INTO connections (name, connection_type, target, enabled) VALUES ('cmdb','CMDB','http://10.99.1.1:3001',1)")
    conn.commit()
    assert "no_segments" in _codes(sd.diagnose(conn, NOW))


def test_all_segments_disabled(conn):
    _seg(conn, A, enabled=0)
    _seg(conn, B, enabled=0)
    d = sd.diagnose(conn, NOW)
    assert "all_disabled" in _codes(d) and "no_segments" not in _codes(d)
    assert "2 個網段" in [c for c in d["causes"] if c["code"] == "all_disabled"][0]["evidence"]
    assert d["summary"]["disabled"] == 2 and {s["status"] for s in d["segments"]} == {"待設定"}


def test_one_enabled_segment_is_not_all_disabled(conn):
    _seg(conn, A, enabled=0)
    _seg(conn, B, enabled=1)
    assert "all_disabled" not in _codes(sd.diagnose(conn, NOW))


def test_scan_switch_off(conn):
    _healthy(conn)
    db.set_setting(conn, "scan_enabled", "0")
    d = sd.diagnose(conn, NOW)
    assert "scan_off" in _codes(d) and d["status"] == "待設定" and d["schedule"]["enabled"] is False


def test_never_scanned_says_not_found(conn):
    _seg(conn, A)
    d = sd.diagnose(conn, NOW)
    assert "never_scanned" in _codes(d) and d["status"] == "沒收到"
    assert d["latest_batch"]["time"] is None and d["segments"][0]["status"] == "沒收到"


def test_last_batch_zero_across_all_segments(conn):
    _seg(conn, A)
    _seg(conn, B)
    _batch(conn, _ts(0), {A: 0, B: 0})
    _run(conn, _ts(0))
    d = sd.diagnose(conn, NOW)
    assert "last_batch_zero" in _codes(d) and d["status"] == "失敗"
    c = [x for x in d["causes"] if x["code"] == "last_batch_zero"][0]
    assert "涵蓋 2 個網段" in c["evidence"]
    assert d["latest_batch"]["found"] == 0 and d["summary"]["zero_now"] == 2


def test_one_segment_zero_but_batch_has_hosts_is_not_batch_zero(conn):
    _seg(conn, A)
    _seg(conn, B)
    _batch(conn, _ts(0), {A: 5, B: 0})
    _run(conn, _ts(0))
    assert "last_batch_zero" not in _codes(sd.diagnose(conn, NOW))


def test_segment_zero_streak_needs_consecutive_batches(conn):
    _seg(conn, A)
    _seg(conn, B)
    for days in (0, 1, 2):                      # B 連續 3 批 0 台；A 每批都有
        _batch(conn, _ts(days), {A: 5, B: 0})
    _run(conn, _ts(0))
    d = sd.diagnose(conn, NOW)
    hit = [c for c in d["causes"] if c["code"] == "segment_zero_streak"]
    assert [c["segment"] for c in hit] == [B] and hit[0]["numbers"]["streak"] == 3


def test_segment_zero_streak_two_batches_not_reported_and_nonzero_resets(conn):
    _seg(conn, A)
    _seg(conn, B)
    _batch(conn, _ts(0), {A: 5, B: 0})
    _batch(conn, _ts(1), {A: 5, B: 0})
    _batch(conn, _ts(2), {A: 5, B: 3})          # 中間有掃到：前面只有 2 批 0 台
    _batch(conn, _ts(3), {A: 5, B: 0})
    _run(conn, _ts(0))
    d = sd.diagnose(conn, NOW)
    assert "segment_zero_streak" not in _codes(d)
    assert [s for s in d["segments"] if s["target"] == B][0]["zero_streak"] == 2


def test_failed_batch_breaks_zero_streak(conn):
    _seg(conn, A)
    _seg(conn, B)
    _batch(conn, _ts(0), {A: 5, B: 0})
    _batch(conn, _ts(1), {A: 5, B: 0}, failed=(B,))   # 失敗不是「掃到 0 台」
    _batch(conn, _ts(2), {A: 5, B: 0})
    _run(conn, _ts(0))
    assert "segment_zero_streak" not in _codes(sd.diagnose(conn, NOW))


def test_fail_ratio_high_at_half(conn):
    _seg(conn, A)
    _seg(conn, B)
    _batch(conn, _ts(0), {A: 5, B: 0}, failed=(B,))
    _run(conn, _ts(0))
    d = sd.diagnose(conn, NOW)
    c = [x for x in d["causes"] if x["code"] == "fail_ratio_high"][0]
    assert c["numbers"] == {"failed": 1, "covered": 2} and "50%" in c["evidence"]
    assert "segment_failed" not in _codes(d) and d["status"] == "失敗"


def test_fail_ratio_counts_scan_history_failures_when_coverage_says_ok(conn):
    """2.070.1（審查 p2#5）：涵蓋紀錄誤標成功、但掃描列 scan_ok=0 → 失敗率要算進去，並另報「資料來源不一致」。"""
    _seg(conn, A)
    _seg(conn, B)
    _batch(conn, _ts(0), {A: 5, B: 5})
    conn.execute("INSERT INTO scan_history (scan_time, ip, segment, scan_ok) VALUES (?,?,?,0)", (_ts(0), None, B))
    conn.commit()
    _run(conn, _ts(0))
    d = sd.diagnose(conn, NOW)
    c = [x for x in d["causes"] if x["code"] == "fail_ratio_high"][0]
    assert c["numbers"] == {"failed": 1, "covered": 2}
    mm = [x for x in d["causes"] if x["code"] == "source_mismatch"]
    assert mm and B in mm[0]["evidence"] and d["latest_batch"]["segments_failed"] == 1


def test_consistent_sources_no_mismatch_cause(conn):
    _seg(conn, A)
    _seg(conn, B)
    _batch(conn, _ts(0), {A: 5, B: 0}, failed=(B,))
    _run(conn, _ts(0))
    assert "source_mismatch" not in _codes(sd.diagnose(conn, NOW))


class _Locked:
    """連線替身：碰到指定資料表就丟 OperationalError（模擬鎖住／損壞），其餘照常。"""
    def __init__(self, c, table, msg):
        self._c, self._t, self._m = c, table, msg

    def execute(self, sql, *a, **k):
        import sqlite3
        if self._t in sql:
            raise sqlite3.OperationalError(self._m)
        return self._c.execute(sql, *a, **k)

    def __getattr__(self, name):
        return getattr(self._c, name)


def test_locked_database_is_diagnosis_unavailable_not_silent_missing_table(conn):
    """2.070.1（審查 p2#6）：database is locked 不是缺表；不能安靜當沒資料而繼續推測。"""
    _healthy(conn)
    d = sd.diagnose(_Locked(conn, "scan_history", "database is locked"), NOW)
    assert d.get("diagnosis_unavailable") is True and _codes(d) == ["diagnosis_unavailable"]
    assert "locked" in d["causes"][0]["evidence"] and d["status"] == "失敗"
    assert d["segments"] == [] and d["probe"]["ports"]            # 形狀完整，畫面不會缺鍵


def test_missing_table_message_still_tolerated_for_optional_tables(conn):
    _healthy(conn)
    d = sd.diagnose(_Locked(conn, "scan_coverage", "no such table: scan_coverage"), NOW)
    assert not d.get("diagnosis_unavailable") and "status" in d


def test_core_table_missing_returns_unavailable_not_500(tmp_path):
    """2.070.1（審查 p2#16）：connections／scan_runs 這類核心表缺失 → 結構化 diagnosis_unavailable。"""
    for table in ("connections", "scan_runs"):
        p = tmp_path / f"{table}.db"
        db.init_db(p)
        c = db.get_connection(p)
        c.execute(f"DROP TABLE {table}")
        c.commit()
        d = sd.diagnose(c, NOW)
        assert d.get("diagnosis_unavailable") is True, table
        assert d["causes"][0]["code"] == "diagnosis_unavailable" and "no such table" in d["causes"][0]["evidence"]
        c.close()


def test_low_fail_ratio_lists_segment_only(conn):
    for cidr in (A, B, C):
        _seg(conn, cidr)
    _batch(conn, _ts(0), {A: 5, B: 5, C: 0}, failed=(C,))
    _run(conn, _ts(0))
    d = sd.diagnose(conn, NOW)
    assert "fail_ratio_high" not in _codes(d)
    assert [c["segment"] for c in d["causes"] if c["code"] == "segment_failed"] == [C]
    assert d["status"] == "只收到一半"


def test_stale_scan_over_two_days(conn):
    _seg(conn, A)
    _batch(conn, _ts(3), {A: 5})
    _run(conn, _ts(3))
    d = sd.diagnose(conn, NOW)
    assert "stale" in _codes(d)
    assert [c for c in d["causes"] if c["code"] == "stale"][0]["numbers"]["hours"] > 48


def test_not_stale_within_two_days(conn):
    _seg(conn, A)
    _batch(conn, _ts(1, "13:00"), {A: 5})       # 21 小時前
    _run(conn, _ts(0))
    assert "stale" not in _codes(sd.diagnose(conn, NOW))


def test_schedule_overdue_when_no_scheduled_run_today(conn):
    _seg(conn, A)
    _batch(conn, _ts(1), {A: 5})
    _run(conn, _ts(1))                          # 昨天 01:00 跑過，今天 01:00 沒跑，現在 10:00
    d = sd.diagnose(conn, NOW)
    assert "schedule_overdue" in _codes(d)
    assert "查不到" not in [c for c in d["causes"] if c["code"] == "schedule_overdue"][0]["evidence"]


def test_schedule_overdue_ignores_manual_runs(conn):
    _seg(conn, A)
    _batch(conn, _ts(0), {A: 5})
    _run(conn, _ts(0, "09:00"), trigger="manual")   # 手動不算排程觸發
    assert "schedule_overdue" in _codes(sd.diagnose(conn, NOW))


def test_schedule_within_grace_not_overdue(conn):
    _seg(conn, A)
    _batch(conn, _ts(1), {A: 5})
    _run(conn, _ts(1))
    assert "schedule_overdue" not in _codes(sd.diagnose(conn, datetime(2026, 11, 22, 1, 10)))   # 才過 10 分鐘
    assert "schedule_overdue" in _codes(sd.diagnose(conn, datetime(2026, 11, 22, 1, 40)))       # 過 40 分鐘


def test_schedule_not_overdue_when_all_segments_have_own_time(conn):
    _seg(conn, A, scan_time="03:00")
    _batch(conn, _ts(1), {A: 5})
    d = sd.diagnose(conn, NOW)
    assert "schedule_overdue" not in _codes(d) and d["schedule"]["all_segments_custom"] is True


def test_weekly_mode_expected_time(conn):
    _healthy(conn)
    scan_service.set_schedule(conn, True, "weekly", "01:00", 6, weekday=6)      # 週日 01:00
    assert "schedule_overdue" not in _codes(sd.diagnose(conn, NOW))             # 今天（週日）01:00 跑過
    conn.execute("DELETE FROM scan_runs")
    _run(conn, _ts(8))
    assert "schedule_overdue" in _codes(sd.diagnose(conn, NOW))


def test_quiet_window_deferral_reported(conn):
    import quiet_window as qw

    _healthy(conn)
    qw.pending_mark(conn, "scan_schedule", "排程掃描到期時在盤中靜默，延後")
    d = sd.diagnose(conn, NOW)
    c = [x for x in d["causes"] if x["code"] == "quiet_deferred"][0]
    assert "盤中靜默" in c["evidence"] and d["quiet_window"]["pending"]["entry"] == "scan_schedule"


def test_no_quiet_deferral_when_no_pending(conn):
    _healthy(conn)
    assert "quiet_deferred" not in _codes(sd.diagnose(conn, NOW))


def test_latest_run_failed_shows_error(conn):
    _healthy(conn)
    _run(conn, _ts(0, "02:00"), trigger="manual", status="failed", error="掃描 3 個網段全部無回應，掃描器可能無法連線")
    d = sd.diagnose(conn, NOW)
    c = [x for x in d["causes"] if x["code"] == "run_failed"][0]
    assert "全部無回應" in c["evidence"]


def test_segment_delta_and_previous_count(conn):
    _seg(conn, A)
    _batch(conn, _ts(1), {A: 8})
    _batch(conn, _ts(0), {A: 3})
    _run(conn, _ts(0))
    s = sd.diagnose(conn, NOW)["segments"][0]
    assert (s["last_count"], s["prev_count"], s["delta"]) == (3, 8, -5)
    assert s["status"] == "只收到一半"          # 8 → 3，少於一半


def test_segment_matches_normalized_and_raw_cidr(conn):
    _seg(conn, "10.99.1.5/24")                   # 使用者填非標準寫法；掃描列用原字串
    _batch(conn, _ts(0), {"10.99.1.5/24": 4})
    _run(conn, _ts(0))
    assert sd.diagnose(conn, NOW)["segments"][0]["last_count"] == 4


def test_probe_settings_come_from_code_constants(conn, monkeypatch):
    p = sd.diagnose(conn, NOW)["probe"]
    assert p["ports"] == list(net_scan.PROBE_PORTS) and p["timeout_seconds"] == 0.6
    monkeypatch.setattr(net_scan, "PROBE_PORTS", (22,))
    assert sd.diagnose(conn, NOW)["probe"]["ports"] == [22]


def test_unknowns_are_stated_not_guessed(conn):
    d = sd.diagnose(conn, NOW)
    assert d["unknowns"] and all("查不到" in u for u in d["unknowns"])


def test_diagnosis_is_read_only(conn):
    _healthy(conn)
    before = conn.total_changes
    sd.diagnose(conn, NOW)
    assert conn.total_changes == before


def test_missing_optional_tables_do_not_crash(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    c.execute("DROP TABLE IF EXISTS scan_coverage")
    c.commit()
    d = sd.diagnose(c, NOW)                      # 舊庫沒涵蓋表：照樣回得出來
    assert "status" in d
    c.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv(api.AUTH_DISABLED_ENV, raising=False)
    p = tmp_path / "t.db"
    db.init_db(p)

    def _override():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _override
    c = db.get_connection(p)
    db.create_user(c, "vera", auth.hash_password(_PW), role="viewer")
    c.close()
    yield p
    api.app.dependency_overrides.clear()


def test_endpoint_requires_login(env):
    assert TestClient(api.app).get("/api/scan/diagnosis").status_code == 401


def test_endpoint_returns_diagnosis_for_viewer(env):
    cl = TestClient(api.app)
    assert cl.post("/api/auth/login", json={"username": "vera", "password": _PW}).status_code == 200
    r = cl.get("/api/scan/diagnosis")
    assert r.status_code == 200
    j = r.json()
    assert {"status", "headline", "causes", "segments", "schedule", "latest_batch", "probe", "unknowns", "summary"} <= set(j)
    assert j["probe"]["ports"] == list(net_scan.PROBE_PORTS)
