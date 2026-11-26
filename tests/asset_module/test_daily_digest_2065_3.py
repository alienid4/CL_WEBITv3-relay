"""每日異動＋摘要信 驗收疑點修正（2.065.3，docs/驗收_完整日報_2026-11-13.md 第 6 節）。

釘住：
  1. 主旨「變化 N 筆、收集中斷 M 台」分開寫；12 類變化全 0 不寫成有變化；沒有前一份快照不寫「變化 0 筆」
  2. 查歷史日期的新鮮度以那一天判斷（不講「今日未收集」）
  3. 排程失敗要留紀錄（activity_log＋失敗狀態），下一輪成功清掉；scan_service 那層不可以再吞掉
  4. 預覽可指定日期；整份日報匯出＝摘要頁＋網段＋12 類各一頁＋信，摘要頁筆數＝各類頁列數
  5. 現役台數＋退役＝快照列數
"""
import sys
from datetime import date, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import daily_digest as dd  # noqa: E402
import db  # noqa: E402


def _db(tmp_path):
    p = tmp_path / "d.db"
    db.init_db(p)
    conn = db.get_connection(p)
    db.insert_hardware(conn, asset_serial="A-1", hostname="h1", ip="10.99.1.1", os="RHEL 9",
                       asset_status="使用中", api_id="N-001", physical_location="機房A",
                       rack_no="M01/10-11U", collect_ok=1, collect_checked_at="2026-10-01 01:00:00")
    db.insert_hardware(conn, asset_serial="A-2", hostname="h2", ip="10.99.1.2", os="Windows",
                       asset_status="使用中")
    conn.commit()
    return conn


def _two_days(conn):
    dd.write_snapshot(conn, datetime(2026, 10, 22, 6, 0), "schedule")
    dd.write_snapshot(conn, datetime(2026, 10, 23, 6, 0), "schedule")


def test_主旨_收集中斷不算變化():
    counts = {k: 0 for k, _ in dd.CATEGORIES}
    counts["collect_gap"] = 257
    subj, body = dd.compose(dict(dd.DEFAULTS), {"date": "2026-11-12", "prev_date": "2026-11-11", "counts": counts}, None)
    assert "變化 0 筆、收集中斷 257 台" in subj, subj
    assert "變化 257" not in subj
    counts["added"], counts["uptime_risk"] = 2, 1
    subj, _ = dd.compose(dict(dd.DEFAULTS), {"date": "2026-11-12", "prev_date": "2026-11-11", "counts": counts}, None)
    assert "變化 3 筆、收集中斷 257 台" in subj, subj
    # 沒有前一份：比不出變化，不可以寫「變化 0 筆」
    subj, _ = dd.compose(dict(dd.DEFAULTS), {"date": "2026-11-12", "prev_date": None, "counts": counts}, None)
    assert "變化" not in subj and "無前一份快照可比" in subj


def test_歷史日期新鮮度以那一天判斷(tmp_path):
    import v2_views as v
    conn = _db(tmp_path)
    _two_days(conn)
    ch = dd.diff_for(conn, "2026-10-23")
    fr = v.changes_freshness(conn, ch, requested="2026-10-23")
    assert fr["historical"] is True
    assert "今日未收集" not in (fr["reason"] or "")
    assert fr["note"] and "2026-10-23" in fr["note"] and "2026-10-23 06:00:00" in fr["note"]
    # 沒指定日期（看最新一份）而最新不是今天：照舊講今日未收集
    fr2 = v.changes_freshness(conn, ch)
    assert fr2["historical"] is False and "今日未收集" in fr2["reason"]


def test_排程失敗要記錄_成功清掉(tmp_path, monkeypatch):
    import scan_service
    conn = _db(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("寫快照炸了")

    monkeypatch.setattr(dd, "tick", boom)
    scan_service._daily_digest_tick(conn)
    rec = dd.tick_error(conn)
    assert rec and "寫快照炸了" in rec["error"] and rec["count"] == 1
    n_log = conn.execute("SELECT COUNT(*) FROM activity_log WHERE method='TICK' AND path LIKE '%每日異動%'").fetchone()[0]
    assert n_log == 1
    scan_service._daily_digest_tick(conn)           # 同一錯誤一小時內：計數加一、不再多寫 activity_log
    assert dd.tick_error(conn)["count"] == 2
    assert conn.execute("SELECT COUNT(*) FROM activity_log WHERE method='TICK'").fetchone()[0] == 1
    assert "排程失敗" in dd.tick_error_label(dd.tick_error(conn))
    # 首頁（1.1 今天的資產異動）看得到
    import v2_today
    blk = v2_today._changes_today(conn, datetime(2026, 10, 23, 9, 0))
    assert "寫快照炸了" in (blk.get("label") or "")
    # 下一輪成功：清掉
    monkeypatch.setattr(dd, "tick", lambda *a, **k: {})
    scan_service._daily_digest_tick(conn)
    assert dd.tick_error(conn) is None


def test_預覽可指定日期(tmp_path):
    conn = _db(tmp_path)
    _two_days(conn)
    s1, _ = dd.build_mail(conn, None, "2026-10-22")
    s2, _ = dd.build_mail(conn, None, "2026-10-23")
    assert "2026-10-22" in s1 and "無前一份快照可比" in s1
    assert "2026-10-23" in s2 and "變化" in s2


def test_整份日報匯出_摘要筆數等於各類頁列數(tmp_path):
    conn = _db(tmp_path)
    dd.write_snapshot(conn, datetime(2026, 10, 22, 6, 0), "schedule")
    db.insert_hardware(conn, asset_serial="A-3", hostname="h3", ip="10.99.1.3", os="=cmd|x",
                       asset_status="使用中", collect_ok=1, collect_checked_at="2026-10-23 01:00:00")
    conn.commit()
    dd.write_snapshot(conn, datetime(2026, 10, 23, 6, 0), "schedule")
    wb, day, n = dd.export_workbook(conn, "2026-10-23")
    assert day == "2026-10-23"
    names = wb.sheetnames
    assert names[0] == "摘要" and "收集中斷網段" in names and names[-1] == "信件內容"
    cat_sheets = names[2:-1]
    assert len(cat_sheets) == len(dd.CATEGORIES) == 12
    out = dd.diff_for(conn, "2026-10-23")
    summary = {r[1]: r[2] for r in wb["摘要"].iter_rows(values_only=True) if r and isinstance(r[0], int)}
    total = 0
    for (k, lab), sh in zip(dd.CATEGORIES, cat_sheets):
        rows = wb[sh].max_row - 1
        assert rows == out["counts"][k] == summary[lab], (k, rows, summary.get(lab))
        total += rows
    assert total == n
    # 公式注入：「=」開頭不可以變成公式
    added = wb[cat_sheets[0]]
    vals = [c.value for row in added.iter_rows(min_row=2) for c in row]
    assert not any(isinstance(x, str) and x.startswith("=") for x in vals)


def test_現役加退役等於快照列數(tmp_path):
    conn = _db(tmp_path)
    dd.write_snapshot(conn, datetime(2026, 10, 22, 6, 0), "schedule")
    conn.execute("UPDATE hardware SET asset_status = '報廢' WHERE asset_serial = 'A-2'")
    conn.commit()
    dd.write_snapshot(conn, datetime(2026, 10, 23, 6, 0), "schedule")
    out = dd.diff_for(conn, "2026-10-23")
    assert out["machines_retired"] >= 1
    assert out["machines"] + out["machines_retired"] == out["machines_rows"] == out["snapshot"]["machines"]
    assert out["machines_note"]


# ── 審查 P2／P3（nightsprint-1113d）──────────────────────────────────────
def _gap_days(conn):
    """兩天快照；h1 上次收集 10-01（早於 7 天）→ 收集中斷至少 1 台。"""
    _two_days(conn)
    out = dd.diff_for(conn, "2026-10-23")
    assert out["counts"]["collect_gap"] >= 1
    return out


def test_P2_1_變化總數各處一致_收集中斷另列(tmp_path):
    import teams_notify as tn
    import v2_today
    import v2_views
    conn = _db(tmp_path)
    out = _gap_days(conn)
    want = dd.change_total(out["counts"])
    assert out["change_total"] == want
    assert want == sum(n for k, n in out["counts"].items() if k != "collect_gap")
    blk = v2_today._changes_today_core(conn, datetime(2026, 10, 23, 12, 0))
    assert blk["n"] == want == sum(r["n"] for r in blk["rows"])
    assert all(r["key"] != "collect_gap" for r in blk["rows"])
    assert blk["collect_gap"]["n"] == out["counts"]["collect_gap"] and "收集中斷" in blk["label"]
    week = v2_views.week_changes(conn, today=date(2026, 10, 23))
    row = [r for r in week["rows"] if r["date"] == "2026-10-23"][0]
    assert row["n"] == want and row["collect_gap"] == out["counts"]["collect_gap"]
    nums = tn.build_summary(conn, dict(tn.DEFAULTS), "daily", now=datetime(2026, 10, 23, 12, 0))[2]
    assert nums["changes_total"] == want


def test_P2_verify_numbers_總數不一致會紅(tmp_path, monkeypatch):
    import verify_numbers as vn
    import v2_views
    conn = _db(tmp_path)
    _gap_days(conn)
    before = vn.FAILS
    vn.check_daily_digest(conn)
    assert vn.FAILS == before                 # 正常：全綠
    real = v2_views.week_changes

    def bad_week(c, *a, **k):
        w = real(c, *a, **k)
        for r in w["rows"]:
            if r.get("n") is not None:
                r["n"] += 1          # 模擬把收集中斷算進總數的舊寫法
        return w

    monkeypatch.setattr(v2_views, "week_changes", bad_week)
    vn.check_daily_digest(conn)
    assert vn.FAILS == before + 1


def test_P3_同一錯誤以類型加第一行判斷(tmp_path):
    conn = _db(tmp_path)
    dd.record_tick_error(conn, RuntimeError("鎖住了\n第 1 次重試"), datetime(2026, 10, 23, 5, 31))
    dd.record_tick_error(conn, RuntimeError("鎖住了\n第 2 次重試"), datetime(2026, 10, 23, 5, 32))
    assert dd.tick_error(conn)["count"] == 2
    assert conn.execute("SELECT COUNT(*) FROM activity_log WHERE method='TICK'").fetchone()[0] == 1
    dd.record_tick_error(conn, ValueError("鎖住了"), datetime(2026, 10, 23, 5, 33))     # 類型不同＝新錯誤
    assert dd.tick_error(conn)["count"] == 1


def test_P3_tick失敗只退回自己的寫入_不動共用連線上別人的(tmp_path, monkeypatch):
    import pytest
    conn = _db(tmp_path)
    conn.execute("INSERT INTO app_settings (key, value) VALUES ('other_tick_pending', 'x')")   # 別的 tick 還沒 commit 的寫入
    assert conn.in_transaction

    def boom(*a, **k):
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('dd_half', 'y')")
        raise RuntimeError("寫到一半炸了")

    monkeypatch.setattr(dd, "snapshot_due", lambda *a, **k: True)
    monkeypatch.setattr(dd, "write_snapshot", boom)
    with pytest.raises(RuntimeError):
        dd.tick(conn, datetime(2026, 10, 23, 5, 31))
    dd.record_tick_error(conn, RuntimeError("寫到一半炸了"), datetime(2026, 10, 23, 5, 31))
    keys = {r[0] for r in conn.execute("SELECT key FROM app_settings")}
    assert "dd_half" not in keys               # 自己的半套寫入退回
    assert "other_tick_pending" in keys        # 別人的寫入沒被 rollback 掉
