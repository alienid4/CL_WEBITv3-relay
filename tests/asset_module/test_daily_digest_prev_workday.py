"""每日異動「比前一個工作日」（2.069.5，D3）。

釘住：
  1. previous_workday：週一→上週五、連假（休市日）跳過、跨月、跨年、休市日設定壞掉只跳週末並講出來
  2. diff_for：比的是前一個工作日那一天的快照；那天沒有快照＝不比（prev_date=None、寫原因），不退回週末快照或更舊的快照
  3. 週末有快照時，週一仍跟週五比（不是跟週日）
  4. 同日重跑冪等
  5. 1.1／1.2／本週異動／摘要信／Teams 同一份數字
"""
import json
import sys
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import daily_digest as dd  # noqa: E402
import db  # noqa: E402


def _db(tmp_path):
    p = tmp_path / "w.db"
    db.init_db(p)
    conn = db.get_connection(p)
    db.insert_hardware(conn, asset_serial="A-1", hostname="h1", ip="10.99.1.1", os="RHEL 9",
                       asset_status="使用中", api_id="N-001", collect_ok=1, collect_checked_at="2026-11-01 01:00:00")
    conn.commit()
    return conn


def _holidays(conn, days):
    conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES ('capacity_holidays', ?)",
                 (json.dumps([{"date": d, "name": "休市"} for d in days]),))
    conn.commit()


def _snap(conn, day, hh=6):
    y, m, d = (int(x) for x in day.split("-"))
    dd.write_snapshot(conn, datetime(y, m, d, hh, 0), "schedule")


def _add_host(conn, n):
    db.insert_hardware(conn, asset_serial=f"B-{n}", hostname=f"hb{n}", ip=f"10.99.2.{n}", os="RHEL 9", asset_status="使用中",
                       collect_ok=1, collect_checked_at="2026-11-01 01:00:00")
    conn.commit()


# ── previous_workday ──
def test_週一對上週五(tmp_path):
    conn = _db(tmp_path)
    pw = dd.previous_workday("2026-11-23", conn)            # 2026-11-23 是週一
    assert pw["date"] == "2026-11-20" and pw["weekday"] == "週五" and pw["label"] == "2026-11-20（週五）"
    assert pw["skipped"] == ["2026-11-22", "2026-11-21"]
    assert pw["snapshot_date"] is None and "沒有快照" in pw["reason"]     # 還沒有任何快照


def test_週二對週一_沒有跳過(tmp_path):
    conn = _db(tmp_path)
    pw = dd.previous_workday("2026-11-24", conn)
    assert pw["date"] == "2026-11-23" and pw["skipped"] == []


def test_連假跳過休市日(tmp_path):
    conn = _db(tmp_path)
    _holidays(conn, ["2026-11-19", "2026-11-20"])          # 週四五休市
    pw = dd.previous_workday("2026-11-23", conn)
    assert pw["date"] == "2026-11-18" and pw["weekday"] == "週三"
    assert set(pw["skipped"]) == {"2026-11-22", "2026-11-21", "2026-11-20", "2026-11-19"}


def test_跨月與跨年(tmp_path):
    conn = _db(tmp_path)
    assert dd.previous_workday("2026-12-01", conn)["date"] == "2026-11-30"      # 週二→週一（跨月）
    assert dd.previous_workday("2027-01-01", conn)["date"] == "2026-12-31"      # 週五→週四（跨年）
    assert dd.previous_workday("2027-01-04", conn)["date"] == "2027-01-01"      # 週一→週五


def test_休市日設定壞掉只跳週末並講出來(tmp_path):
    conn = _db(tmp_path)
    conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES ('capacity_holidays', ?)", ("{not json",))
    conn.commit()
    pw = dd.previous_workday("2026-11-23", conn)
    assert pw["date"] == "2026-11-20" and pw["holiday_note"] and "只跳過週末" in pw["holiday_note"]


# ── diff_for ──
def test_週一跟週五比_不跟週末快照比(tmp_path):
    conn = _db(tmp_path)
    _snap(conn, "2026-11-20")                  # 週五
    _add_host(conn, 1)
    _snap(conn, "2026-11-21")                  # 週六（排程不分週末也會留快照）
    _add_host(conn, 2)
    _snap(conn, "2026-11-22")                  # 週日
    _add_host(conn, 3)
    _snap(conn, "2026-11-23")                  # 週一
    ch = dd.diff_for(conn, "2026-11-23")
    assert ch["prev_date"] == "2026-11-20" and ch["prev_label"] == "2026-11-20（週五）"
    assert ch["counts"]["added"] == 3, "週末新增的 2 台也算進週一的異動（跟週五比），不是只算週日以後的 1 台"
    assert ch["compare"]["compared"] is True and ch["reason"] is None


def test_前一個工作日沒有快照_不用昨天硬湊(tmp_path):
    conn = _db(tmp_path)
    _snap(conn, "2026-11-19")                  # 週四有、週五沒有
    _add_host(conn, 1)
    _snap(conn, "2026-11-22")                  # 週日有（不是工作日，不可拿來比）
    _add_host(conn, 2)
    _snap(conn, "2026-11-23")
    ch = dd.diff_for(conn, "2026-11-23")
    assert ch["prev_date"] is None and ch["prev_label"] is None
    assert "2026-11-20（週五）" in ch["reason"] and "沒有快照" in ch["reason"]
    assert ch["counts"]["added"] == 0 and ch["compare"]["compared"] is False


def test_連假後第一天跟連假前最後一個工作日比(tmp_path):
    conn = _db(tmp_path)
    _holidays(conn, ["2026-11-19", "2026-11-20"])
    _snap(conn, "2026-11-18")
    _add_host(conn, 1)
    _snap(conn, "2026-11-19")                  # 休市日有快照也不算工作日
    _add_host(conn, 2)
    _snap(conn, "2026-11-23")
    ch = dd.diff_for(conn, "2026-11-23")
    assert ch["prev_date"] == "2026-11-18" and ch["counts"]["added"] == 2


def test_只有一份快照寫原因(tmp_path):
    conn = _db(tmp_path)
    _snap(conn, "2026-11-23")
    ch = dd.diff_for(conn, "2026-11-23")
    assert ch["prev_date"] is None and "只有一份快照" in ch["reason"]


def test_同日重跑冪等(tmp_path):
    conn = _db(tmp_path)
    _snap(conn, "2026-11-20")
    _add_host(conn, 1)
    _snap(conn, "2026-11-23")
    a = dd.diff_for(conn, "2026-11-23")
    _snap(conn, "2026-11-23", hh=7)           # 同一天重寫快照
    b = dd.diff_for(conn, "2026-11-23")
    for k in ("prev_date", "prev_label", "counts", "change_total", "reason"):
        assert a[k] == b[k], k
    assert conn.execute("SELECT COUNT(*) FROM daily_snapshot_run WHERE snap_date='2026-11-23'").fetchone()[0] == 1


# ── 同一份數字 ──
def test_一點一_一點二_本週異動_摘要信_同一份數字(tmp_path):
    import v2_today
    import v2_views
    conn = _db(tmp_path)
    _snap(conn, "2026-11-20")
    _add_host(conn, 1)
    _snap(conn, "2026-11-21")
    _add_host(conn, 2)
    _snap(conn, "2026-11-23")
    ch = dd.diff_for(conn, "2026-11-23")
    total = dd.change_total(ch["counts"])
    assert total == 2
    blk = v2_today._changes_today_core(conn, datetime(2026, 11, 23, 9, 0))
    assert blk["n"] == total and blk["prev_date"] == "2026-11-20" and blk["prev_label"] == "2026-11-20（週五）"
    wk = v2_views.week_changes(conn, days=1, today=datetime(2026, 11, 23).date())["rows"][0]
    assert wk["n"] == total and wk["status"] == "對比 2026-11-20（週五）"
    subject, body = dd.build_mail(conn, None, "2026-11-23")
    assert f"變化 {total} 筆" in subject and "與 2026-11-20（週五） 比較" in body


def test_一點一_沒有前一個工作日快照_寫是哪一天(tmp_path):
    import v2_today
    conn = _db(tmp_path)
    _snap(conn, "2026-11-19")
    _snap(conn, "2026-11-23")
    blk = v2_today._changes_today_core(conn, datetime(2026, 11, 23, 9, 0))
    assert blk["n"] is None and "2026-11-20（週五）" in blk["label"]
    import v2_views
    wk = v2_views.week_changes(conn, days=1, today=datetime(2026, 11, 23).date())["rows"][0]
    assert wk["n"] is None and wk["status"].startswith("無資料（前一個工作日 2026-11-20（週五）")


def test_verify_numbers_抓得到拿週末快照冒充前一個工作日(tmp_path, monkeypatch):
    """突變：把 diff_for 改回「最近一份較早的快照」，verify_numbers 的新不變式必須紅。"""
    import verify_numbers as vn
    conn = _db(tmp_path)
    _snap(conn, "2026-11-20")
    _add_host(conn, 1)
    _snap(conn, "2026-11-22")                  # 週日
    _snap(conn, "2026-11-23")
    before = vn.FAILS
    vn.check_daily_digest(conn)
    assert vn.FAILS == before                  # 正確寫法綠
    orig = dd.previous_workday

    def sloppy(day, c):                        # 舊寫法：最近一份較早快照（週日）
        r = orig(day, c)
        older = [d for d in dd.dates(c) if d < str(day)]
        return {**r, "snapshot_date": older[0] if older else None}

    monkeypatch.setattr(dd, "previous_workday", sloppy)
    vn.check_daily_digest(conn)
    assert vn.FAILS > before


# ── 2.070.1 GPT 三輪審查補強 ──
NOTE = "休市日設定讀不到，只跳過週末（比對日期可能不準）"


def test_快照不完整_不比對_寫原因(tmp_path):
    """p1#17：前一工作日的 run 存在但明細少了，不能拿半份快照比（會大量誤報新增／消失）。"""
    conn = _db(tmp_path)
    _snap(conn, "2026-11-20")
    _add_host(conn, 1)
    _snap(conn, "2026-11-23")
    assert dd.diff_for(conn, "2026-11-23")["prev_date"] == "2026-11-20"
    conn.execute("DELETE FROM daily_snapshot WHERE snap_date = '2026-11-20' AND hostname = 'h1'")
    conn.commit()
    ch = dd.diff_for(conn, "2026-11-23")
    assert ch["prev_date"] is None and "快照不完整" in ch["reason"] and "2026-11-20（週五）" in ch["reason"]
    assert ch["counts"]["added"] == 0 and ch["compare"]["compared"] is False
    pw = dd.previous_workday("2026-11-23", conn)
    assert pw["snapshot_date"] is None and pw.get("incomplete") is True


def _break_holidays(conn):
    conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES ('capacity_holidays', ?)", ("{not json",))
    conn.commit()


def test_休市日設定壞掉_同一句話出現在_diff_信件_Teams_首頁區塊(tmp_path):
    """p1#15＋p3#3：不只 helper 回 note，信件內文、Teams、1.1 區塊都要帶同一句。"""
    import teams_notify
    import v2_today
    conn = _db(tmp_path)
    _snap(conn, "2026-11-20")
    _add_host(conn, 1)
    _snap(conn, "2026-11-23")
    _break_holidays(conn)
    ch = dd.diff_for(conn, "2026-11-23")
    assert ch["compare"]["holiday_note"] == NOTE == dd.HOLIDAY_BROKEN_NOTE
    _subject, body = dd.build_mail(conn, None, "2026-11-23")
    assert NOTE in body
    blk = v2_today._changes_today_core(conn, datetime(2026, 11, 23, 9, 0))
    assert blk["compare"]["holiday_note"] == NOTE
    with teams_notify.read_only(conn):
        _t, card, nums = teams_notify.build_summary(conn, dict(teams_notify.DEFAULTS), "daily")
    assert nums["changes_holiday_note"] == NOTE and NOTE in str(card)


def test_休市日設定正常_信件與Teams不帶警語(tmp_path):
    import teams_notify
    conn = _db(tmp_path)
    _snap(conn, "2026-11-20")
    _snap(conn, "2026-11-23")
    _subject, body = dd.build_mail(conn, None, "2026-11-23")
    assert "休市日設定讀不到" not in body
    with teams_notify.read_only(conn):
        _t, card, nums = teams_notify.build_summary(conn, dict(teams_notify.DEFAULTS), "daily")
    assert "changes_holiday_note" not in nums and "休市日設定讀不到" not in str(card)


def test_Teams摘要的變化數字_與信件_1點2_同一份_且同日重跑改資料後跟著變(tmp_path):
    """p1#20＋#18：真的呼叫 teams_notify.build_summary 對照信件內文；同日重跑前先改資料，兩邊都要跟新資料。"""
    import teams_notify
    conn = _db(tmp_path)
    _snap(conn, "2026-11-20")
    _add_host(conn, 1)
    _snap(conn, "2026-11-21")
    _add_host(conn, 2)
    _snap(conn, "2026-11-23")

    def both():
        ch = dd.diff_for(conn, "2026-11-23")
        _s, body = dd.build_mail(conn, None, "2026-11-23")
        with teams_notify.read_only(conn):
            _t, _c, nums = teams_notify.build_summary(conn, dict(teams_notify.DEFAULTS), "daily")
        return dd.change_total(ch["counts"]), body, nums

    total, body, nums = both()
    assert total == 2 and nums["changes_date"] == "2026-11-23" and nums["changes_total"] == total
    assert "與 2026-11-20（週五） 比較" in body
    _add_host(conn, 3)                         # 同一天資料又變了，重寫快照
    _snap(conn, "2026-11-23", hh=7)
    total2, body2, nums2 = both()
    assert total2 == 3 and nums2["changes_total"] == 3
    assert "與 2026-11-20（週五） 比較" in body2
