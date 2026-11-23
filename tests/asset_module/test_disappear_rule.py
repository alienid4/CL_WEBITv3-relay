"""「異常消失」新判定規則（2026-11-20，D3 口徑區）：連續 N（預設 5）次完整掃描都沒掃到才算。

每條規則一組測試；改回舊寫法（本次 vs 上次一比就記、失敗網段用文字欄反推、提早覆寫基準、逐筆 commit…）都會有測試紅。
"""
import json
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import comparison_engine  # noqa: E402
import db  # noqa: E402
import disappear_rule  # noqa: E402
import scan_scope  # noqa: E402

_PW = "pw-Test-12345"


def _t(day, hm="01:00"):
    return f"2026-08-{day:02d}T{hm}"


def _scan(conn, day, hosts, seg="A", failed_segs=(), hm="01:00", cov=None):
    """hosts：[(hostname, ip)]；failed_segs：該批有掃描失敗紀錄（scan_ok=0）的網段；cov：{cidr: ok} 寫 scan_coverage。"""
    for hn, ip in hosts:
        conn.execute("INSERT INTO scan_history (scan_time, hostname, ip, segment, scan_ok) VALUES (?,?,?,?,1)",
                     (_t(day, hm), hn, ip, seg))
    for fs in failed_segs:
        conn.execute("INSERT INTO scan_history (scan_time, hostname, ip, segment, scan_ok) VALUES (?,?,?,?,0)",
                     (_t(day, hm), None, None, fs))
    if cov is not None:
        scan_scope._ensure(conn)
        for cidr, ok in cov.items():
            conn.execute("INSERT INTO scan_coverage (scan_time, cidr, source, ok, created_at) VALUES (?,?,?,?,?)",
                         (_t(day, hm), cidr, "src-" + cidr, 1 if ok else 0, "x"))
    conn.commit()


def _run(conn, day, hm="01:00"):
    return comparison_engine.run_comparison(conn, _t(day, hm), _t(day - 1) if day > 1 else None)


def _gone(conn, open_only=True):
    q = "SELECT * FROM comparison_result WHERE issue_type = '異常消失'" + (" AND is_read = 0" if open_only else "")
    return conn.execute(q).fetchall()


def _db(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _TD():
    return tempfile.TemporaryDirectory(ignore_cleanup_errors=True)


def _apply(conn):
    db.set_setting(conn, "disappear_autoclose_apply", "1")


def _note(conn, rid):
    return conn.execute("SELECT * FROM comparison_note WHERE result_id = ?", (rid,)).fetchone()


BASE = [(f"h{i}", f"10.99.0.{i}") for i in range(1, 11)]       # 10 台
X = ("gone", "10.99.0.99")


# ============================================================ 規則 1～2：次數、歸零、日曆日

def test_rule2_one_to_four_missing_not_reported_fifth_reported_with_note():
    with _TD() as tmp:
        conn = _db(tmp)
        _scan(conn, 1, BASE + [X]); _run(conn, 1)
        for d in range(2, 6):
            _scan(conn, d, BASE); _run(conn, d)
            assert _gone(conn) == [], f"連續 {d - 1} 次未見不應出現"
        _scan(conn, 6, BASE); _run(conn, 6)
        rows = _gone(conn)
        assert len(rows) == 1 and rows[0]["hostname"] == "gone"
        n = _note(conn, rows[0]["id"])
        assert "最近 5 次完整掃描未見" in n["note"] and "首次未見：2026-08-02" in n["note"]
        assert n["miss_count"] == 5 and "天" not in n["note"].split("（")[0]


def test_rule2_seen_in_between_resets_count():
    with _TD() as tmp:
        conn = _db(tmp)
        _scan(conn, 1, BASE + [X]); _run(conn, 1)
        for d in (2, 3, 4):
            _scan(conn, d, BASE); _run(conn, d)
        _scan(conn, 5, BASE + [X]); _run(conn, 5)
        for d in (6, 7, 8, 9):
            _scan(conn, d, BASE); _run(conn, d)
        assert _gone(conn, open_only=False) == []
        _scan(conn, 10, BASE); _run(conn, 10)
        assert len(_gone(conn)) == 1


def test_same_calendar_day_reruns_count_once():
    """同一天手動補跑 5 次不能一天就累積成『連續 5 次』。"""
    with _TD() as tmp:
        conn = _db(tmp)
        _scan(conn, 1, BASE + [X]); _run(conn, 1)
        for i, hm in enumerate(("01:00", "09:00", "11:00", "13:00", "15:00", "17:00")):
            _scan(conn, 2, BASE, hm=hm); _run(conn, 2, hm)
        assert _gone(conn, open_only=False) == []
        days, _ = disappear_rule.load_days(conn, _t(2, "17:00"), 60, disappear_rule.get_config(conn))
        assert len(days) == 2


def test_same_day_rerun_that_sees_host_resets():
    with _TD() as tmp:
        conn = _db(tmp)
        _scan(conn, 1, BASE + [X]); _run(conn, 1)
        for d in (2, 3, 4, 5):
            _scan(conn, d, BASE); _run(conn, d)
        _scan(conn, 6, BASE); _scan(conn, 6, BASE + [X], hm="12:00"); _run(conn, 6, "12:00")   # 當天補跑掃到了
        assert _gone(conn, open_only=False) == []


def test_threshold_is_configurable_and_invalid_values_are_reported_not_silent():
    with _TD() as tmp:
        conn = _db(tmp)
        db.set_setting(conn, "missing_confirm_scans", "3")
        _scan(conn, 1, BASE + [X]); _run(conn, 1)
        for d in (2, 3):
            _scan(conn, d, BASE); _run(conn, d)
        assert _gone(conn) == []
        _scan(conn, 4, BASE); _run(conn, 4)
        assert len(_gone(conn)) == 1
        for bad in ("0", "1", "abc", "-5", "9999"):
            db.set_setting(conn, "missing_confirm_scans", bad)
            cfg = disappear_rule.get_config(conn)
            assert cfg["confirm"] == 5 and cfg["errors"], bad          # 回預設，且有可見錯誤
        db.set_setting(conn, "missing_confirm_scans", "5")
        db.set_setting(conn, "scan_drop_threshold_pct", "120")
        assert disappear_rule.get_config(conn)["drop_pct"] == 20
        db.set_setting(conn, "scan_drop_threshold_pct", "0")
        assert disappear_rule.get_config(conn)["drop_pct"] == 20
        _run(conn, 4)
        n = conn.execute("SELECT COUNT(*) c FROM activity_log WHERE action LIKE 'disappear_rule_error%'").fetchone()["c"]
        assert n >= 1                                                  # 非法設定寫進活動紀錄


# ============================================================ 規則 3：不完整批次

def test_global_drop_not_counted():
    with _TD() as tmp:
        conn = _db(tmp)
        _scan(conn, 1, BASE + [X]); _run(conn, 1)
        _scan(conn, 2, BASE); _run(conn, 2)
        _scan(conn, 3, BASE[:5]); _run(conn, 3)               # 11→5 台，驟降：不算
        for d in (4, 5, 6):
            _scan(conn, d, BASE); _run(conn, d)
        assert _gone(conn) == []
        assert conn.execute("SELECT incomplete FROM scan_batch_quality WHERE scan_time = ?", (_t(3),)).fetchone()["incomplete"] == 1
        _scan(conn, 7, BASE); _run(conn, 7)
        assert len(_gone(conn)) == 1


def test_failed_segment_via_coverage_keyed_by_cidr_not_display_name():
    """失敗網段以 scan_coverage.cidr＋ok 為準；失敗列的文字欄是顯示名稱（≠CIDR）也不能影響判定。"""
    with _TD() as tmp:
        conn = _db(tmp)
        c = "10.99.1.0/24"
        _scan(conn, 1, BASE + [X], seg=c, cov={c: True}); _run(conn, 1)
        for d in (2, 3, 4):
            _scan(conn, d, BASE, seg=c, cov={c: True}); _run(conn, d)
        _scan(conn, 5, BASE, seg=c, failed_segs=("網段-顯示名稱",), cov={c: False}); _run(conn, 5)   # 失敗列名稱對不上 CIDR
        _scan(conn, 6, BASE, seg=c, cov={c: True}); _run(conn, 6)
        assert _gone(conn) == [], "網段失敗那次不計入：只有 4 次"
        row = conn.execute("SELECT incomplete, bad_segments FROM scan_batch_quality WHERE scan_time = ?", (_t(5),)).fetchone()
        assert row["incomplete"] == 1 and c in json.loads(row["bad_segments"])


def test_scanner_writes_failure_rows_with_canonical_cidr(monkeypatch):
    """scanner.run_scan 的失敗列要寫正規化 CIDR（跟成功列同鑰匙），不是來源顯示名稱。"""
    import scanner
    from scan_sources import ScanResult, ScanSource

    class Ok(ScanSource):
        name = "網段 A"
        cidr = "10.99.1.0/24"

        def scan(self):
            return [ScanResult("h1", "10.99.1.1", "m", False, self.cidr)]

    class Boom(ScanSource):
        name = "網段 B（顯示名稱）"
        cidr = "10.99.2.5/24"          # 故意不是網路位址：要被正規化成 10.99.2.0/24

        def scan(self):
            raise RuntimeError("boom")

    with _TD() as tmp:
        conn = _db(tmp)
        monkeypatch.setattr(scanner, "_now_local", lambda: "2026-08-01 01:00:00")
        scanner.run_scan([Ok(), Boom()], conn)
        fail = conn.execute("SELECT segment FROM scan_history WHERE scan_ok = 0").fetchone()["segment"]
        assert fail == "10.99.2.0/24"
        cov = {r["cidr"]: r["ok"] for r in conn.execute("SELECT cidr, ok FROM scan_coverage")}
        assert cov.get("10.99.2.5/24") == 0 and cov.get("10.99.1.0/24") == 1


def test_segment_not_in_coverage_is_out_of_scope_not_counted_and_not_shrink():
    """單網段／部分範圍掃描：沒涵蓋的網段不累加未見次數，也不被當成『縮減』。"""
    with _TD() as tmp:
        conn = _db(tmp)
        a, b = "10.99.1.0/24", "10.99.2.0/24"
        segA = [(f"a{i}", f"10.99.1.{i}") for i in range(1, 9)]
        segB = [(f"b{i}", f"10.99.2.{i}") for i in range(1, 9)]
        yb = ("yb", "10.99.2.99")
        _scan(conn, 1, segA, seg=a, cov={a: True}); _scan(conn, 1, segB + [yb], seg=b, cov={b: True}, hm="01:30"); _run(conn, 1, "01:30")
        for d in range(2, 12):                               # 之後每天只掃 A 網段（B 沒納入）
            _scan(conn, d, segA, seg=a, cov={a: True}); _run(conn, d)
        assert _gone(conn, open_only=False) == []
        q = conn.execute("SELECT incomplete FROM scan_batch_quality WHERE scan_time = ?", (_t(11),)).fetchone()
        assert q["incomplete"] == 0                          # B 不在範圍內，不是不完整


def test_segment_drop_only_discounts_that_segment():
    with _TD() as tmp:
        conn = _db(tmp)
        segA = [(f"a{i}", f"10.99.1.{i}") for i in range(1, 9)]
        segB = [(f"b{i}", f"10.99.2.{i}") for i in range(1, 9)]
        ya, yb = ("ya", "10.99.1.99"), ("yb", "10.99.2.99")
        _scan(conn, 1, segA + [ya], "A"); _scan(conn, 1, segB + [yb], "B"); _run(conn, 1)
        for d in (2, 3, 4, 5, 6):
            _scan(conn, d, segA[:2], "A"); _scan(conn, d, segB, "B"); _run(conn, d)
        assert {r["hostname"] for r in _gone(conn)} == {"yb"}


def test_shrink_acceptance_day_by_day_baseline_not_overwritten_early():
    """持續縮減：前四批不完整、第 5 批才接受新常態；問題恰於其後第 5 個完整未見才出現；採新基準要留事件。"""
    with _TD() as tmp:
        conn = _db(tmp)
        segA = [(f"a{i}", f"10.99.1.{i}") for i in range(1, 11)]
        _scan(conn, 1, segA, "A"); _run(conn, 1)
        keep = segA[:5]
        for d in range(2, 12):
            _scan(conn, d, keep, "A"); _run(conn, d)
            q = conn.execute("SELECT incomplete, new_baseline FROM scan_batch_quality WHERE scan_time = ?", (_t(d),)).fetchone()
            if d <= 5:
                assert q["incomplete"] == 1, f"第 {d} 批應不完整（基準不可被提早覆寫）"
                assert _gone(conn, open_only=False) == []
            elif d == 6:
                assert q["incomplete"] == 0, "第 5 批縮減（第 6 天）才接受新常態"
                assert any("已採新基準" in e and "10 台降為 5 台" in e for e in json.loads(q["new_baseline"]))
            if d < 10:
                assert _gone(conn, open_only=False) == [], f"第 {d} 天還不該有消失"
        assert len(_gone(conn)) == 5                        # 第 10 天＝第 6 天起第 5 個完整未見；五台一起
        lq = disappear_rule.latest_quality(conn)
        assert lq["baseline_events"] and "已採新基準" in lq["baseline_events"][0]


def test_many_small_segments_shrinking_together_trips_global_rule_and_holds_baseline():
    with _TD() as tmp:
        conn = _db(tmp)
        segs = {f"S{n}": [(f"s{n}h{i}", f"10.99.{n}.{i}") for i in range(1, 5)] for n in range(1, 7)}
        for n, hs in segs.items():
            _scan(conn, 1, hs, n)
        _run(conn, 1)
        for d in range(2, 7):
            for n, hs in segs.items():
                _scan(conn, d, hs[:2], n)
            _run(conn, d)
            g = conn.execute("SELECT global_incomplete FROM scan_batch_quality WHERE scan_time = ?", (_t(d),)).fetchone()["global_incomplete"]
            assert g == (1 if d <= 5 else 0), f"第 {d} 天 global_incomplete={g}"
        assert _gone(conn, open_only=False) == []


def test_count_is_distinct_hosts_not_rows():
    with _TD() as tmp:
        conn = _db(tmp)
        _scan(conn, 1, BASE); _run(conn, 1)
        # 第 2 批：只有 7 台不重複主機，但其中 3 台重複列（共 10 列）→ 列數沒降、distinct 降 30%
        _scan(conn, 2, BASE[:7] + BASE[:3]); _run(conn, 2)
        assert conn.execute("SELECT incomplete FROM scan_batch_quality WHERE scan_time = ?", (_t(2),)).fetchone()["incomplete"] == 1


def test_null_segment_rows_never_accumulate_misses():
    with _TD() as tmp:
        conn = _db(tmp)
        nul = [("n1", "10.99.9.1")]
        for hn, ip in nul + BASE:
            conn.execute("INSERT INTO scan_history (scan_time, hostname, ip, segment, scan_ok) VALUES (?,?,?,?,1)",
                         (_t(1), hn, ip, None if hn == "n1" else "A"))
        conn.commit(); _run(conn, 1)
        for d in range(2, 10):
            _scan(conn, d, BASE); _run(conn, d)
        assert _gone(conn, open_only=False) == []


def test_zero_host_batch_and_all_failed_batch_are_recorded_as_incomplete():
    with _TD() as tmp:
        conn = _db(tmp)
        c = "10.99.1.0/24"
        _scan(conn, 1, BASE + [X], seg=c, cov={c: True}); _run(conn, 1)
        _scan(conn, 2, [], seg=c, cov={c: True})                 # 成功但 0 台：scan_history 沒有任何列
        _run(conn, 2)
        q = conn.execute("SELECT * FROM scan_batch_quality WHERE scan_time = ?", (_t(2),)).fetchone()
        assert q is not None and q["global_incomplete"] == 1 and "0 台" in q["reason"]
        assert disappear_rule.latest_quality(conn)["mode"] == "all"
        _scan(conn, 3, [], seg=c, cov={c: False})                # 全部網段失敗
        _run(conn, 3)
        q3 = conn.execute("SELECT * FROM scan_batch_quality WHERE scan_time = ?", (_t(3),)).fetchone()
        assert q3["global_incomplete"] == 1
        assert _gone(conn, open_only=False) == []


def test_recompare_picks_zero_host_batch_via_coverage():
    import scan_service

    with _TD() as tmp:
        conn = _db(tmp)
        c = "10.99.1.0/24"
        _scan(conn, 1, BASE, seg=c, cov={c: True})
        _scan(conn, 2, [], seg=c, cov={c: True})
        scan_service._recompare(conn)
        assert disappear_rule.latest_quality(conn)["scan_time"] == _t(2)


def test_single_batch_still_recomputes_old_records_and_records_quality():
    with _TD() as tmp:
        conn = _db(tmp)
        _apply(conn)
        _scan(conn, 1, BASE + [X])
        rid = db.insert_comparison_result(conn, "gone", X[1], "異常消失")
        comparison_engine.run_comparison(conn, _t(1), None)          # 只有一個批次
        assert conn.execute("SELECT is_read FROM comparison_result WHERE id = ?", (rid,)).fetchone()["is_read"] == 1
        assert conn.execute("SELECT COUNT(*) c FROM scan_batch_quality").fetchone()["c"] == 1


# ============================================================ 規則 4：vCOps 交叉確認

def _vc(conn, ip, power, at, host=None):
    conn.execute("INSERT INTO source_record (source, source_key, payload, collected_at) VALUES ('vcenter', ?, ?, ?)",
                 (ip + (host or ""), json.dumps({"ip": ip, "hostname": host or ("vm-" + ip[-2:]), "powerstate": power}), at))
    conn.commit()


def test_vcops_powered_on_before_ref_not_reported_poweredoff_reported():
    with _TD() as tmp:
        conn = _db(tmp)
        _vc(conn, "10.99.0.99", "poweredOn", "2026-08-06 00:30:00")
        _vc(conn, "10.99.0.98", "poweredOff", "2026-08-06 00:30:00")
        _scan(conn, 1, BASE + [("vm-99", "10.99.0.99"), ("vm-98", "10.99.0.98")]); _run(conn, 1)
        for d in range(2, 7):
            _scan(conn, d, BASE); _run(conn, d)
        assert {r["hostname"] for r in _gone(conn, open_only=False)} == {"vm-98"}


def test_vcops_future_record_is_not_evidence():
    with _TD() as tmp:
        conn = _db(tmp)
        _vc(conn, X[1], "poweredOn", "2026-08-06 06:00:00", host="gone")        # 晚於參考掃描 08-06 01:00
        _scan(conn, 1, BASE + [X]); _run(conn, 1)
        for d in range(2, 7):
            _scan(conn, d, BASE); _run(conn, d)
        assert len(_gone(conn)) == 1


def test_vcops_stale_record_is_not_evidence():
    with _TD() as tmp:
        conn = _db(tmp)
        _vc(conn, X[1], "poweredOn", "2026-07-01 06:00:00", host="gone")
        _scan(conn, 1, BASE + [X]); _run(conn, 1)
        for d in range(2, 7):
            _scan(conn, d, BASE); _run(conn, d)
        assert len(_gone(conn)) == 1


def test_vcops_same_hostname_different_ip_is_not_evidence():
    """兩邊都有 IP 卻對不上＝不同台，不可退回只比主機名。"""
    with _TD() as tmp:
        conn = _db(tmp)
        _vc(conn, "10.99.2.20", "poweredOn", "2026-08-06 00:30:00", host="gone")   # 同名但另一個 IP
        _scan(conn, 1, BASE + [X]); _run(conn, 1)
        for d in range(2, 7):
            _scan(conn, d, BASE); _run(conn, d)
        assert len(_gone(conn)) == 1


def test_vcops_name_match_when_scan_has_no_ip():
    idx = {"ip": {}, "name": {"gone": [(None, "why")]}}
    assert disappear_rule.alive_reason(idx, "10.99.0.99", "gone") == "why"
    idx2 = {"ip": {}, "name": {"gone": [("10.99.2.20", "why")]}}
    assert disappear_rule.alive_reason(idx2, "10.99.0.99", "gone") is None
    assert disappear_rule.alive_reason(idx2, "10.99.2.20", "gone") == "why"


# ============================================================ 規則 5：舊紀錄重算（預覽／套用／還原）

def _old_records(conn):
    _scan(conn, 1, BASE + [X, ("late", "10.99.0.77")]); _scan(conn, 2, BASE)
    a = db.insert_comparison_result(conn, "gone", X[1], "異常消失")
    b = db.insert_comparison_result(conn, "late", "10.99.0.77", "異常消失")
    _scan(conn, 3, BASE + [("late", "10.99.0.77")])
    return a, b


def test_preview_mode_default_changes_nothing_then_apply_closes_with_audit():
    with _TD() as tmp:
        conn = _db(tmp)
        a, b = _old_records(conn)
        summary = comparison_engine.run_comparison(conn, _t(3), _t(2))
        assert summary["自動結案"] == 0                                    # 預覽：沒有真的結案
        for rid in (a, b):
            assert conn.execute("SELECT is_read FROM comparison_result WHERE id = ?", (rid,)).fetchone()["is_read"] == 0
        pv = disappear_rule.preview_list(conn)
        assert pv["count"] == 2 and not pv["apply_mode"]
        assert conn.execute("SELECT COUNT(*) c FROM comparison_auto_close_log WHERE applied = 0").fetchone()["c"] == 2
        h = disappear_rule.preview_list(conn)["preview_hash"]
        assert disappear_rule.apply_preview(conn, "admin", h) == 2
        for rid in (a, b):
            assert conn.execute("SELECT is_read FROM comparison_result WHERE id = ?", (rid,)).fetchone()["is_read"] == 1
            assert conn.execute("SELECT COUNT(*) c FROM comparison_result WHERE id = ?", (rid,)).fetchone()["c"] == 1   # 關閉不是刪除
        lg = conn.execute("SELECT * FROM comparison_auto_close_log WHERE result_id = ? AND applied = 1", (a,)).fetchone()
        assert lg["rule_version"] == disappear_rule.RULE_VERSION and json.loads(lg["config_json"])["confirm"] == 5
        assert lg["applied_by"] == "admin"
        assert db.get_setting(conn, "disappear_autoclose_apply") == "1"
        assert conn.execute("SELECT COUNT(*) c FROM activity_log WHERE action LIKE 'disappear_autoclose_apply%'").fetchone()["c"] == 1
        with pytest.raises(disappear_rule.StalePreview):
            disappear_rule.apply_preview(conn, "admin", h)                   # 用舊雜湊重複套用：清單已變
        assert disappear_rule.apply_preview(conn, "admin", disappear_rule.preview_list(conn)["preview_hash"]) == 0   # 重複套用不會再關


def test_apply_mode_closes_directly_and_note_text():
    with _TD() as tmp:
        conn = _db(tmp)
        _apply(conn)
        a, b = _old_records(conn)
        summary = comparison_engine.run_comparison(conn, _t(3), _t(2))
        assert summary["自動結案"] == 2
        n = _note(conn, a)
        assert "系統自動關閉" in n["note"] and "未達 5 次門檻" in n["note"] and n["miss_count"] is None


def test_auto_close_snapshots_note_and_restore_restores_every_field():
    with _TD() as tmp:
        conn = _db(tmp)
        _apply(conn)
        _scan(conn, 1, BASE + [X]); _scan(conn, 2, BASE)
        rid = db.insert_comparison_result(conn, "gone", X[1], "異常消失")
        conn.execute("INSERT INTO comparison_note (result_id, note, miss_count, first_missing, last_seen, updated_at) "
                     "VALUES (?, '最近 6 次完整掃描未見（首次未見：2026-08-01）', 6, '2026-08-01T01:00', '2026-07-30T01:00', '2026-08-05 01:00:00')", (rid,))
        conn.commit()
        _scan(conn, 3, BASE + [X]); comparison_engine.run_comparison(conn, _t(3), _t(2))
        n = _note(conn, rid)
        assert n["miss_count"] is None and n["first_missing"] is None and "系統自動關閉" in n["note"]
        lg = conn.execute("SELECT * FROM comparison_auto_close_log WHERE result_id = ?", (rid,)).fetchone()
        assert lg["prev_note"].startswith("最近 6 次") and lg["prev_miss_count"] == 6 and lg["prev_first_missing"] == "2026-08-01T01:00"
        assert disappear_rule.restore_auto_closed(conn, rid, "admin", "誤關") == "ok"
        n2 = _note(conn, rid)
        assert n2["note"].startswith("最近 6 次") and n2["miss_count"] == 6 and n2["first_missing"] == "2026-08-01T01:00"
        assert conn.execute("SELECT is_read FROM comparison_result WHERE id = ?", (rid,)).fetchone()["is_read"] == 0
        lg2 = conn.execute("SELECT * FROM comparison_auto_close_log WHERE id = ?", (lg["id"],)).fetchone()
        assert lg2["restored_by"] == "admin" and lg2["restore_reason"] == "誤關"
        assert disappear_rule.restore_auto_closed(conn, rid) == "already_restored"
        assert disappear_rule.restore_auto_closed(conn, 99999) == "not_found"
        other = db.insert_comparison_result(conn, "x", "10.0.0.1", "異常消失")
        assert disappear_rule.restore_auto_closed(conn, other) == "not_auto_closed"


def test_restored_record_is_not_auto_closed_again():
    with _TD() as tmp:
        conn = _db(tmp)
        _apply(conn)
        a, _b = _old_records(conn)
        comparison_engine.run_comparison(conn, _t(3), _t(2))
        assert disappear_rule.restore_auto_closed(conn, a, "admin", "人工覆核") == "ok"
        _scan(conn, 4, BASE + [("late", "10.99.0.77")])
        comparison_engine.run_comparison(conn, _t(4), _t(3))
        assert conn.execute("SELECT is_read FROM comparison_result WHERE id = ?", (a,)).fetchone()["is_read"] == 0


def test_open_record_that_meets_rule_stays_open_and_not_duplicated():
    with _TD() as tmp:
        conn = _db(tmp)
        _apply(conn)
        _scan(conn, 1, BASE + [X]); _run(conn, 1)
        for d in range(2, 8):
            _scan(conn, d, BASE); _run(conn, d)
        rows = _gone(conn)
        assert len(rows) == 1
        assert "最近 6 次完整掃描未見" in _note(conn, rows[0]["id"])["note"]


def test_old_record_not_truncated_by_short_window():
    """舊紀錄的主機最後出現在很久以前、中間大多是不完整批次：不能因視窗太短而永遠無法重算。"""
    with _TD() as tmp:
        conn = _db(tmp)
        _apply(conn)
        c = "10.99.1.0/24"
        _scan(conn, 1, BASE + [X], seg=c, cov={c: True})
        rid = db.insert_comparison_result(conn, "gone", X[1], "異常消失")
        for d in range(2, 22):                                         # 20 天網段失敗（不計入）
            _scan(conn, d, [], seg=c, cov={c: False})
        for d in (22, 23, 24):                                         # 最後 3 天完整、沒掃到 → 只有 3 次
            _scan(conn, d, BASE, seg=c, cov={c: True})
        comparison_engine.run_comparison(conn, _t(24), _t(23))
        assert conn.execute("SELECT is_read FROM comparison_result WHERE id = ?", (rid,)).fetchone()["is_read"] == 1


def test_dedup_by_hostname_when_old_record_has_no_ip():
    with _TD() as tmp:
        conn = _db(tmp)
        rid = db.insert_comparison_result(conn, "gone", None, "異常消失")
        _scan(conn, 1, BASE + [X]); _run(conn, 1)
        for d in range(2, 7):
            _scan(conn, d, BASE); _run(conn, d)
        assert [r["id"] for r in _gone(conn)] == [rid]


def test_handled_episode_not_reopened_but_new_episode_is():
    with _TD() as tmp:
        conn = _db(tmp)
        _scan(conn, 1, BASE + [X]); _run(conn, 1)
        for d in range(2, 7):
            _scan(conn, d, BASE); _run(conn, d)
        (r,) = _gone(conn)
        db.mark_comparison_read(conn, r["id"])                       # 人工處理
        for d in range(7, 12):
            _scan(conn, d, BASE); _run(conn, d)
        assert len(_gone(conn, open_only=False)) == 1                # 同一段未見期間：不重開
        _scan(conn, 12, BASE + [X]); _run(conn, 12)                  # 回來
        for d in range(13, 18):
            _scan(conn, d, BASE); _run(conn, d)
        assert len(_gone(conn, open_only=False)) == 2                # 再消失＝新事件


# ============================================================ 交易 / 效能 / 清理

def test_whole_run_is_one_transaction_rolled_back_on_error(monkeypatch):
    with _TD() as tmp:
        conn = _db(tmp)
        ys = [(f"y{i}", f"10.99.0.{50 + i}") for i in range(2)]
        _scan(conn, 1, BASE + ys); _run(conn, 1)
        for d in range(2, 6):
            _scan(conn, d, BASE); _run(conn, d)
        _scan(conn, 6, BASE)
        calls = {"n": 0}
        real = disappear_rule._set_note

        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] == 2:
                raise sqlite3_error()
            return real(*a, **k)

        def sqlite3_error():
            import sqlite3
            return sqlite3.OperationalError("disk full")

        monkeypatch.setattr(disappear_rule, "_set_note", flaky)
        out = disappear_rule.detect_disappearances(conn, _t(6))
        assert out["錯誤"] and "disk full" in out["錯誤"]
        assert _gone(conn, open_only=False) == []                    # 第一筆已 insert 也一併回復
        assert conn.execute("SELECT COUNT(*) c FROM comparison_note").fetchone()["c"] == 0
        q = conn.execute("SELECT scope, incomplete FROM scan_batch_quality WHERE scan_time = ?", (_t(6),)).fetchone()
        assert q["scope"] == "failed" and q["incomplete"] == 1             # 失敗狀態用獨立小交易留下，沒被 rollback 帶走
        lq = disappear_rule.latest_quality(conn)
        assert lq["judgement_pending"] is True and lq["mode"] == "pending"
        assert conn.execute("SELECT COUNT(*) c FROM activity_log WHERE action LIKE 'disappear_rule_error%'").fetchone()["c"] == 1


def test_no_per_host_select_on_comparison_result():
    with _TD() as tmp:
        conn = _db(tmp)
        hosts = [(f"m{i}", f"10.99.5.{i}") for i in range(1, 61)]
        db.set_setting(conn, "scan_drop_segment_min_hosts", "1000")      # 本測試只看查詢次數：關掉驟降判定讓 60 台一起消失
        db.set_setting(conn, "scan_drop_threshold_pct", "99")
        _scan(conn, 1, BASE + hosts); _run(conn, 1)
        for d in range(2, 7):
            _scan(conn, d, BASE); _run(conn, d)
        assert len(_gone(conn)) == 60
        stmts: list = []
        conn.set_trace_callback(stmts.append)
        _scan(conn, 7, BASE); _run(conn, 7)
        conn.set_trace_callback(None)
        n = sum(1 for s in stmts if "FROM comparison_result" in s and "issue_type = '異常消失'" in s and s.lstrip().upper().startswith("SELECT"))
        assert n <= 8, f"逐台 SELECT 了 {n} 次"


def test_clean_data_and_reset_lists_include_new_tables_before_comparison_result():
    import _app_shared
    import clean_data

    for lst in (list(clean_data.BUSINESS_TABLES), list(_app_shared._RESET_TABLES)):
        for t in ("comparison_note", "comparison_auto_close_log", "scan_batch_quality"):
            assert t in lst and lst.index(t) < lst.index("comparison_result"), t


# ============================================================ API：權限 / 還原 / 預覽 / 欄位

@pytest.fixture()
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
    for name, role in (("admin", "admin"), ("alice", "user"), ("vera", "viewer")):
        db.create_user(c, name, auth.hash_password(_PW), role=role)
    c.close()

    class E:
        path = p

        def client(self, name):
            cl = TestClient(api.app)
            assert cl.post("/api/auth/login", json={"username": name, "password": _PW}).status_code == 200
            return cl

        def conn(self):
            return db.get_connection(p)

    yield E()
    api.app.dependency_overrides.clear()


def _seed_preview(conn):
    a, b = _old_records(conn)
    comparison_engine.run_comparison(conn, _t(3), _t(2))
    return a, b


def test_api_apply_and_restore_admin_only_and_flags(env):
    conn = env.conn()
    a, b = _seed_preview(conn)
    conn.close()
    admin, user, viewer = env.client("admin"), env.client("alice"), env.client("vera")
    for cl in (user, viewer):
        assert cl.get("/api/issues/auto-close/preview").status_code == 403
        assert cl.post("/api/issues/auto-close/apply", json={}).status_code == 403
        assert cl.post(f"/api/issues/{a}/restore-auto-close", json={"reason": "x"}).status_code == 403
    pv = admin.get("/api/issues/auto-close/preview").json()
    assert pv["count"] == 2
    assert admin.post("/api/issues/auto-close/apply", json={"preview_hash": "0" * 64}).status_code == 409   # 雜湊對不上
    assert admin.post("/api/issues/auto-close/apply", json={}).status_code == 422                          # 沒帶雜湊
    assert admin.post("/api/issues/auto-close/apply", json={"preview_hash": pv["preview_hash"]}).json() == {"applied": 2}
    rows = {r["id"]: r for r in admin.get("/api/issues").json()}
    assert rows[a]["auto_closed"] == 1 and rows[a]["is_read"] == 1 and "未達 5 次" in rows[a]["close_reason"]
    assert "系統自動關閉" in rows[a]["note"]
    assert admin.post(f"/api/issues/{a}/restore-auto-close", json={"reason": " "}).status_code == 400
    assert admin.post(f"/api/issues/{a}/restore-auto-close", json={"reason": "誤關"}).json()["restored"] is True
    assert admin.post(f"/api/issues/{a}/restore-auto-close", json={"reason": "again"}).status_code == 409
    assert admin.post("/api/issues/424242/restore-auto-close", json={"reason": "x"}).status_code == 404
    conn = env.conn()
    human = db.insert_comparison_result(conn, "h", "10.0.0.5", "異常消失")
    db.mark_comparison_read(conn, human)
    conn.close()
    assert admin.post(f"/api/issues/{human}/restore-auto-close", json={"reason": "x"}).status_code == 409   # 人工處理的不能用這支還原
    lg = env.conn().execute("SELECT restored_by, restore_reason FROM comparison_auto_close_log WHERE result_id = ?", (a,)).fetchone()
    assert lg["restored_by"] == "admin" and lg["restore_reason"] == "誤關"


def test_api_scan_quality_returns_effective_threshold_and_full_segment_list(env):
    conn = env.conn()
    db.set_setting(conn, "missing_confirm_scans", "7")
    segs = {f"S{n}": [(f"s{n}h{i}", f"10.99.{n}.{i}") for i in range(1, 9)] for n in range(1, 4)}
    for n, hs in segs.items():
        _scan(conn, 1, hs, n)
    for n, hs in segs.items():
        _scan(conn, 2, hs if n == "S3" else [], n, failed_segs=(n,) if n != "S3" else ())
    _run(conn, 2)
    conn.close()
    q = env.client("vera").get("/api/issues/scan-quality").json()
    assert q["confirm"] == 7 and q["mode"] in ("segments", "all") and set(q["bad_segments"]) >= {"S1", "S2"}
    assert q["rule_version"] == disappear_rule.RULE_VERSION


def test_api_list_issues_exposes_miss_count_for_frontend(env):
    conn = env.conn()
    _scan(conn, 1, BASE + [X]); _run(conn, 1)
    for d in range(2, 7):
        _scan(conn, d, BASE); _run(conn, d)
    conn.close()
    rows = env.client("vera").get("/api/issues").json()
    gone = [r for r in rows if r["issue_type"] == "異常消失"]
    assert gone and gone[0]["miss_count"] == 5 and gone[0]["auto_closed"] == 0


# ============================================================ 第二輪複查補強（#2 #3 #4 #5 #6 #9 #10）

def test_coverage_is_sole_scope_unregistered_success_rows_only_prove_presence():
    with _TD() as tmp:
        conn = _db(tmp)
        a, b = "10.99.1.0/24", "10.99.2.0/24"
        segA = [(f"a{i}", f"10.99.1.{i}") for i in range(1, 7)]
        segB = [(f"b{i}", f"10.99.2.{i}") for i in range(1, 7)]
        yb = ("yb", "10.99.2.99")
        _scan(conn, 1, segA, seg=a, cov={a: True}); _scan(conn, 1, segB + [yb], seg=b); _run(conn, 1)
        for d in range(2, 10):                               # coverage 只登記 A；B 的成功列沒登記在 coverage
            _scan(conn, d, segA, seg=a, cov={a: True}); _scan(conn, d, segB, seg=b); _run(conn, d)
        assert _gone(conn, open_only=False) == [], "沒登記在 coverage 的網段只能證明有看到，不能證明沒看到"


def test_total_is_distinct_union_across_segments_not_sum():
    with _TD() as tmp:
        conn = _db(tmp)
        c1, c2 = "10.99.1.0/24", "10.99.2.0/24"
        same = [(f"s{i}", f"10.99.9.{i}") for i in range(1, 6)]             # 同一批身分同時出現在兩個網段
        for d in (1, 2):
            _scan(conn, d, same, seg=c1, cov={c1: True, c2: True}); _scan(conn, d, same, seg=c2)
            _run(conn, d)
        q = conn.execute("SELECT total_count, incomplete FROM scan_batch_quality WHERE scan_time = ?", (_t(2),)).fetchone()
        assert q["total_count"] == 5 and q["incomplete"] == 0               # 聯集 distinct＝5，不是 10


def _verified_seed_setup(conn, incomplete):
    old = "2026-05-01T01:00"
    for i in range(1, 11):
        conn.execute("INSERT INTO scan_history (scan_time, hostname, ip, segment, scan_ok) VALUES (?,?,?,?,1)",
                     (old, f"h{i}", f"10.99.0.{i}", "A"))
    conn.execute("INSERT INTO scan_batch_quality (scan_time, ok_count, incomplete, global_incomplete) VALUES (?,10,?,0)",
                 (old, incomplete))
    for i in range(1, 6):                                                   # 視窗內第一批只有 5 台
        conn.execute("INSERT INTO scan_history (scan_time, hostname, ip, segment, scan_ok) VALUES (?,?,?,?,1)",
                     ("2026-08-20T01:00", f"h{i}", f"10.99.0.{i}", "A"))
    conn.commit()
    disappear_rule.detect_disappearances(conn, "2026-08-20T01:00")
    return conn.execute("SELECT incomplete FROM scan_batch_quality WHERE scan_time = ?", ("2026-08-20T01:00",)).fetchone()["incomplete"]


def test_window_seed_only_from_verified_complete_batch():
    with _TD() as tmp:
        assert _verified_seed_setup(_db(tmp), incomplete=1) == 0           # 不完整的舊批不當種子：寧可少一次證據
    with _TD() as tmp:
        assert _verified_seed_setup(_db(tmp), incomplete=0) == 1           # 已驗證完整的舊批才當種子（10→5 縮減）


def test_empty_batch_from_run_scan_gets_quality_row_using_returned_scan_time(monkeypatch):
    import scan_service
    import scanner
    from scan_sources import ScanSource

    class Empty(ScanSource):
        name = "空來源（沒有 CIDR）"

        def scan(self):
            return []

    with _TD() as tmp:
        conn = _db(tmp)
        monkeypatch.setattr(scanner, "_now_local", lambda: "2026-08-01 01:00:00")
        summary = scanner.run_scan([Empty()], conn)
        assert conn.execute("SELECT COUNT(*) c FROM scan_history").fetchone()["c"] == 0      # 沒有任何列
        scan_service._recompare(conn, summary["scan_time"])
        q = conn.execute("SELECT * FROM scan_batch_quality WHERE scan_time = ?", ("2026-08-01 01:00:00",)).fetchone()
        assert q is not None and q["incomplete"] == 1 and q["global_incomplete"] == 1


def test_candidate_last_seen_80_days_ago_without_existing_event_is_still_reported():
    with _TD() as tmp:
        conn = _db(tmp)
        conn.execute("INSERT INTO scan_history (scan_time, hostname, ip, segment, scan_ok) VALUES ('2026-05-30T01:00','gone','10.99.0.99','A',1)")
        for d in range(1, 21):
            for hn, ip in BASE:
                conn.execute("INSERT INTO scan_history (scan_time, hostname, ip, segment, scan_ok) VALUES (?,?,?,'A',1)", (_t(d), hn, ip))
        conn.commit()
        disappear_rule.detect_disappearances(conn, _t(20))
        rows = _gone(conn)
        assert [r["hostname"] for r in rows] == ["gone"]
        assert _note(conn, rows[0]["id"])["last_seen"].startswith("2026-05-30")


def test_judgement_failure_is_persisted_and_scan_quality_reports_pending(monkeypatch):
    with _TD() as tmp:
        conn = _db(tmp)
        _scan(conn, 1, BASE); _run(conn, 1)
        assert disappear_rule.latest_quality(conn)["judgement_pending"] is False
        _scan(conn, 2, BASE)                                               # 新一批掃描進來，但還沒判定
        lq = disappear_rule.latest_quality(conn)
        assert lq["judgement_pending"] is True and lq["mode"] == "pending" and lq["scan_time"] == _t(1)


def test_apply_bound_to_list_content_not_just_count():
    with _TD() as tmp:
        conn = _db(tmp)
        a, b = _old_records(conn)
        comparison_engine.run_comparison(conn, _t(3), _t(2))
        h = disappear_rule.preview_list(conn)["preview_hash"]
        # 預覽被換掉：筆數相同（2 筆），但 result_id／原因不同
        c = db.insert_comparison_result(conn, "other1", "10.99.8.1", "異常消失")
        d = db.insert_comparison_result(conn, "other2", "10.99.8.2", "異常消失")
        conn.execute("DELETE FROM comparison_auto_close_log WHERE applied = 0")
        for rid in (c, d):
            conn.execute("INSERT INTO comparison_auto_close_log (result_id, closed_at, reason, applied) VALUES (?, 'x', '別的原因', 0)", (rid,))
        conn.commit()
        assert disappear_rule.preview_list(conn)["count"] == 2
        with pytest.raises(disappear_rule.StalePreview):
            disappear_rule.apply_preview(conn, "admin", h)
        for rid in (a, b, c, d):
            assert conn.execute("SELECT is_read FROM comparison_result WHERE id = ?", (rid,)).fetchone()["is_read"] == 0
