"""verify_numbers.check_daily_digest_indep 改成「前一個工作日」獨立重算（2.070.1，審查 p1#13／#16／#17）。

起因：App 改成比前一個工作日，驗證器仍用相鄰快照（ds[i+1]），週末有快照時正確程式會被報錯、deploy [2d/7] 中止。
釘住：
  1. 週末有快照時，週一比週五：驗證器綠；把 App 改回相鄰快照（突變）驗證器紅
  2. 前一工作日缺快照：App 不得比（驗證器綠）；App 退回相鄰快照（突變）驗證器紅
  3. 休市日跳過
  4. 快照不完整（紀錄台數≠實際列數）：App 不比；驗證器同樣當「不可比」
  5. 31 天上限常數兩邊各自獨立（驗證器不 import daily_digest.WORKDAY_LOOKBACK）
"""
import json
import sys
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import daily_digest as dd  # noqa: E402
import db  # noqa: E402
import verify_numbers as vn  # noqa: E402


def _db(tmp_path):
    p = tmp_path / "v.db"
    db.init_db(p)
    conn = db.get_connection(p)
    db.insert_hardware(conn, asset_serial="A-1", hostname="h1", ip="10.99.1.1", os="RHEL 9",
                       asset_status="使用中", api_id="N-001", collect_ok=1, collect_checked_at="2026-11-01 01:00:00")
    conn.commit()
    return conn


def _snap(conn, day):
    y, m, d = (int(x) for x in day.split("-"))
    dd.write_snapshot(conn, datetime(y, m, d, 6, 0), "schedule")


def _add(conn, n):
    db.insert_hardware(conn, asset_serial=f"B-{n}", hostname=f"hb{n}", ip=f"10.99.2.{n}", os="RHEL 9", asset_status="使用中",
                       collect_ok=1, collect_checked_at="2026-11-01 01:00:00")
    conn.commit()


def _holidays(conn, days):
    conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES ('capacity_holidays', ?)",
                 (json.dumps([{"date": d, "name": "休市"} for d in days]),))
    conn.commit()


def _fails(fn, conn):
    before = vn.FAILS
    fn(conn)
    return vn.FAILS - before


def _adjacent(monkeypatch):
    """突變：App 退回「相鄰快照」寫法（最近一份較早的快照）。"""
    orig = dd.previous_workday

    def sloppy(day, c):
        r = orig(day, c)
        older = [d for d in dd.dates(c) if d < str(day)]
        return {**r, "snapshot_date": older[0] if older else None, "reason": None}

    monkeypatch.setattr(dd, "previous_workday", sloppy)


def _weekend_db(tmp_path):
    conn = _db(tmp_path)
    _snap(conn, "2026-11-20")      # 週五
    _add(conn, 1)
    _snap(conn, "2026-11-21")      # 週六
    _add(conn, 2)
    _snap(conn, "2026-11-22")      # 週日
    _snap(conn, "2026-11-23")      # 週一
    # 週六搬櫃：週六起 h1 的櫃號都是 R9（週五不是）→ 週五→週一有 1 筆位置改變，週日→週一是 0 筆
    conn.execute("UPDATE daily_snapshot SET rack = 'R9' WHERE hostname = 'h1' AND snap_date >= '2026-11-21'")
    conn.commit()
    return conn


def test_週末有快照_週一比週五_驗證器綠(tmp_path):
    conn = _weekend_db(tmp_path)
    assert dd.diff_for(conn, "2026-11-23")["prev_date"] == "2026-11-20"
    assert _fails(vn.check_daily_digest_indep, conn) == 0
    assert vn._dd_indep_prev_workday(conn, "2026-11-23") == "2026-11-20"


def test_週五到週一的位置改變_App算得到_且不是相鄰快照的0筆(tmp_path):
    conn = _weekend_db(tmp_path)
    assert dd.diff_for(conn, "2026-11-23")["counts"]["location"] == 1
    sun, mon = dd.load_snapshot(conn, "2026-11-22"), dd.load_snapshot(conn, "2026-11-23")
    assert dd.diff_rows(sun, mon, dd.load_settings(conn), "2026-11-23")["items"]["location"] == []   # 相鄰快照會算成 0 筆


def test_突變_驗證器退回相鄰快照_必須紅(tmp_path, monkeypatch):
    """舊驗證器寫法（ds[i+1]）：把獨立的前一工作日改成「最近一份較早快照」，週末有搬櫃的資料上要紅。"""
    conn = _weekend_db(tmp_path)
    ds = dd.dates(conn)
    monkeypatch.setattr(vn, "_dd_indep_prev_workday",
                        lambda c, day: next((x for x in ds if x < day), None))
    assert _fails(vn.check_daily_digest_indep, conn) > 0


def test_突變_App退回相鄰快照_驗證器必須紅(tmp_path, monkeypatch):
    conn = _weekend_db(tmp_path)
    _adjacent(monkeypatch)
    assert _fails(vn.check_daily_digest_indep, conn) > 0
    # 主驗證器（check_daily_digest）也要抓到
    assert _fails(vn.check_daily_digest, conn) > 0


def test_前一工作日缺快照_App不比_驗證器綠(tmp_path):
    conn = _db(tmp_path)
    _snap(conn, "2026-11-19")      # 週四有、週五沒有
    _add(conn, 1)
    _snap(conn, "2026-11-22")      # 週日有
    _snap(conn, "2026-11-23")
    assert dd.diff_for(conn, "2026-11-23")["prev_date"] is None
    assert _fails(vn.check_daily_digest_indep, conn) == 0


def test_前一工作日缺快照_突變退回相鄰_驗證器紅(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    _snap(conn, "2026-11-19")
    _add(conn, 1)
    _snap(conn, "2026-11-22")
    _snap(conn, "2026-11-23")
    _adjacent(monkeypatch)
    assert _fails(vn.check_daily_digest_indep, conn) > 0


def test_休市日跳過(tmp_path):
    conn = _db(tmp_path)
    _holidays(conn, ["2026-11-19", "2026-11-20"])
    _snap(conn, "2026-11-18")
    _add(conn, 1)
    _snap(conn, "2026-11-19")      # 休市日有快照也不算
    _snap(conn, "2026-11-23")
    assert vn._dd_indep_prev_workday(conn, "2026-11-23") == "2026-11-18"
    assert dd.diff_for(conn, "2026-11-23")["prev_date"] == "2026-11-18"
    assert _fails(vn.check_daily_digest_indep, conn) == 0


def test_快照不完整_App不比_驗證器同樣當不可比(tmp_path):
    conn = _weekend_db(tmp_path)
    conn.execute("DELETE FROM daily_snapshot WHERE snap_date = '2026-11-20' AND mkey = (SELECT MIN(mkey) FROM daily_snapshot WHERE snap_date = '2026-11-20')")
    conn.commit()
    ch = dd.diff_for(conn, "2026-11-23")
    assert ch["prev_date"] is None and "快照不完整" in ch["reason"]
    assert vn._dd_indep_snapshot_complete(conn, "2026-11-20") is False
    assert _fails(vn.check_daily_digest_indep, conn) == 0
    assert _fails(vn.check_daily_digest, conn) >= 0     # 不拋例外（其中 bad_run 會如實標出紀錄≠列數）


def test_驗證器上限常數獨立_不引用App的(tmp_path):
    src = (BACKEND / "verify_numbers.py").read_text(encoding="utf-8")
    assert "WORKDAY_LOOKBACK" not in src.split("def _dd_indep_prev_workday")[1].split("def _dd_indep_rack_names")[0]
    assert vn._DD_INDEP_LOOKBACK == 31
