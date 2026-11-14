"""交易日情境標記：修訂鏈保留首次來源、競態（同時修訂／同時作廢）、時區轉換邊界。"""
import sqlite3
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import capacity_markers as mk  # noqa: E402

D = "2026-09-10"
BODY = {"trade_date": D, "mtype": "大量交易日", "reason_cat": "預期大漲", "expected_level": "高", "basis": "預期央行消息"}
TW = timezone(timedelta(hours=8))


def at(day: str, hh: int, mm: int = 0, ss: int = 0) -> datetime:
    y, m, d = map(int, day.split("-"))
    return datetime(y, m, d, hh, mm, ss)


def mem():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
    return c


# ---------------- 1 修訂鏈保留首次來源
def test_事前預告被盤後修訂_分析仍是事前預告():
    c = mem()
    m = mk.create(c, BODY, "u", at(D, 8, 59))                                              # 08:59 → 事前預告
    r = mk.revise(c, m["id"], {"expected_level": "中", "basis": "盤後修正"}, "u", at(D, 13, 31))   # 13:30 後修訂 → 這一版自己是事後補標
    assert r["source_kind"] == "post_fact" and r["origin_source_kind"] == "pre_announce"
    rows = {x["id"]: x for x in mk.listing(c, now=at(D, 14))}
    assert rows[r["id"]]["revision_source_kind"] == "post_fact" and rows[r["id"]]["origin_kind_label"] == "事前預告" and rows[r["id"]]["status"] == "active"
    assert mk.annotated_days(c) == {D: "pre"}                                               # 統計仍為 pre
    r2 = mk.revise(c, r["id"], {"expected_level": "低"}, "u", at("2026-09-11", 9))          # 再修訂一次，仍沿用鏈首
    assert r2["origin_source_kind"] == "pre_announce" and mk.annotated_days(c) == {D: "pre"}


def test_事後補標被修訂_仍是事後補標_盤中標記永遠不納入():
    c = mem()
    m = mk.create(c, BODY, "u", at(D, 15))
    mk.revise(c, m["id"], {"basis": "x"}, "u", at(D, 16))
    assert mk.annotated_days(c) == {D: "post"}
    c2 = mem()
    mk.create(c2, BODY, "u", at(D, 10))                                                   # 盤中
    assert mk.annotated_days(c2) == {}


# ---------------- 2 競態
def _db(tmp):
    p = Path(tmp) / "m.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
    m = mk.create(c, BODY, "u", at("2026-09-09", 17))
    c.commit()
    return p, c, m["id"]


def _race(p, fn, n=2):
    barrier = threading.Barrier(n)
    results = [None] * n

    def work(i):
        conn = sqlite3.connect(p, timeout=15)
        try:
            barrier.wait(5)
            results[i] = ("ok", fn(conn, i))
            conn.commit()
        except mk.MarkerConflict as exc:
            results[i] = ("conflict", str(exc))
            conn.rollback()
        except Exception as exc:  # noqa: BLE001 - 測試要看到任何意外例外
            results[i] = ("error", repr(exc))
            conn.rollback()
        finally:
            conn.close()
    ts = [threading.Thread(target=work, args=(i,)) for i in range(n)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    return results


def test_兩連線同時修訂同一標記_只成功一個_稽核只有一條revise():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        p, c, mid = _db(tmp)
        res = _race(p, lambda conn, i: mk.revise(conn, mid, {"basis": f"版本{i}"}, f"u{i}", at(D, 10))["id"])
        assert sorted(r[0] for r in res) == ["conflict", "ok"], res
        assert c.execute("SELECT COUNT(*) FROM capacity_day_marker WHERE supersedes_id = ?", (mid,)).fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM capacity_day_marker_audit WHERE action = 'revise'").fetchone()[0] == 1
        c.close()


def test_兩個作廢只有第一個生效_稽核只有一條void_作廢後不得再修訂():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        p, c, mid = _db(tmp)
        res = _race(p, lambda conn, i: mk.void(conn, mid, f"原因{i}", f"admin{i}", at(D, 10))["voided_by"])
        assert sorted(r[0] for r in res) == ["conflict", "ok"], res
        assert c.execute("SELECT COUNT(*) FROM capacity_day_marker_audit WHERE action = 'void'").fetchone()[0] == 1
        winner = [r[1] for r in res if r[0] == "ok"][0]
        assert c.execute("SELECT voided_by FROM capacity_day_marker WHERE id = ?", (mid,)).fetchone()[0] == winner      # 第二個沒有覆蓋第一個
        with pytest.raises(mk.MarkerConflict, match="已作廢"):
            mk.revise(c, mid, {"basis": "x"}, "u", at(D, 11))
        c.close()


def test_supersedes唯一約束_資料庫層也擋_直接寫入重複修訂會失敗():
    c = mem()
    m = mk.create(c, BODY, "u", at(D, 8))
    mk.revise(c, m["id"], {"basis": "a"}, "u", at(D, 9))
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO capacity_day_marker (trade_date, ranges_json, mtype, reason_cat, basis, source_kind, created_at, supersedes_id, origin_source_kind) "
                  "VALUES (?, '[]', 'x', 'y', 'z', 'pre_announce', 't', ?, 'pre_announce')", (D, m["id"]))


# ---------------- 3 aware datetime 先轉台北
@pytest.mark.parametrize("now,kind,locked", [
    (datetime(2026, 9, 10, 0, 59, 59, tzinfo=timezone.utc), "pre_announce", False),        # UTC 00:59:59 ＝ 台北 08:59:59
    (datetime(2026, 9, 10, 1, 0, 0, tzinfo=timezone.utc), "intraday", True),               # 台北 09:00:00
    (datetime(2026, 9, 10, 5, 29, 59, tzinfo=timezone.utc), "intraday", True),             # 台北 13:29:59
    (datetime(2026, 9, 10, 5, 30, 0, tzinfo=timezone.utc), "post_fact", True),             # 台北 13:30:00
    (datetime(2026, 9, 9, 16, 30, tzinfo=timezone.utc), "pre_announce", False),            # 台北 9/10 00:30
    (datetime(2026, 9, 10, 8, 59, 59, tzinfo=TW), "pre_announce", False),
    (datetime(2026, 9, 10, 9, 0, 0, tzinfo=TW), "intraday", True),
    (datetime(2026, 9, 10, 13, 30, 0, tzinfo=TW), "post_fact", True),
    (datetime(2026, 9, 10, 8, 59, 59), "pre_announce", False),                             # 沒有 tzinfo ＝ 已是台北時間
    (datetime(2026, 9, 10, 9, 0, 0), "intraday", True),
])
def test_aware時間先轉台北_再判來源與鎖定(now, kind, locked):
    assert mk.source_kind(D, now) == kind
    assert mk.is_locked(D, now) is locked
    c = mem()
    assert mk.create(c, BODY, "u", now)["source_kind"] == kind


def test_來源分類看鏈首_事前預告盤中修訂仍是pre_盤中起家的鏈永遠不納入():
    c = mem()
    m = mk.create(c, BODY, "u", at(D, 8, 59))
    mk.revise(c, m["id"], {"basis": "盤中修訂"}, "u", at(D, 10))                      # 這一版自己是盤中標記
    assert mk.annotated_days(c) == {D: "pre"}
    c2 = mem()
    m2 = mk.create(c2, BODY, "u", at(D, 10))                                            # 盤中起家
    mk.revise(c2, m2["id"], {"basis": "收盤後修訂"}, "u", at(D, 14))                    # 修訂版自己是事後補標，但鏈首是盤中 → 仍不納入
    assert mk.annotated_days(c2) == {}


def test_寫入都在明確鎖定內():
    src = (ROOT / "APP" / "asset-module" / "backend" / "capacity_markers.py").read_text(encoding="utf-8")
    assert 'conn.execute("BEGIN IMMEDIATE")' in src
    assert src.count("with _lock(conn):") >= 5                                         # create／update／revise／void／set_events
    assert "WHERE id=? AND voided_at IS NULL" in src and "WHERE NOT EXISTS (SELECT 1 FROM capacity_day_marker WHERE supersedes_id = ?)" in src
