"""容量分析日摘要封存（ACT-04 第二階段）：暫定→完成→修訂不覆寫、缺資料不補 0、保存 24 個月（日曆月份）、
admin 手動清理且先預覽、被標記引用不可刪、排程不可清理、權限 fail-closed、舊 DB 升級、重複啟動不重複建立、靜默窗。全部假資料、可注入 now。"""
import ast
import gzip
import json
import re
import sqlite3
import sys
import tempfile
import threading
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import capacity_analysis as ca  # noqa: E402
import capacity_archive as ar  # noqa: E402
import capacity_markers as mk  # noqa: E402
import nmon_series  # noqa: E402
import quiet_window as qw  # noqa: E402

D = "2026-09-10"            # 週四
NEXT = datetime(2026, 9, 11, 16, 0)
AFTER_CLOSE = datetime(2026, 9, 10, 16, 0)


def mem():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
    c.execute(nmon_series.SERIES_SQL)
    return c


def put(c, day=D, host="H", cpu=50.0, start=540, end=810, replace=False):
    c.execute(nmon_series.SERIES_SQL)
    row = {"ts": [f"{m // 60:02d}:{m % 60:02d}:00" for m in range(start, end)], "cols": {"cpu_busy": [cpu] * (end - start)}, "kind": "raw"}
    c.execute("INSERT OR REPLACE INTO nmon_series VALUES (?,?,60,?,?,'x')" if replace else "INSERT INTO nmon_series VALUES (?,?,60,?,?,'x')",
              (host, day, end - start, gzip.compress(json.dumps(row).encode())))
    c.commit()


def cpu_cells(body):
    return {x["slot"]: x for x in body["cells"] if x["metric"] == "cpu_busy"}


# ---------------------------------------------------------------- 保存期限：日曆月份
@pytest.mark.parametrize("d,n,want", [
    ("2024-02-29", -24, "2022-02-28"),       # 閏日往回兩年：目標年沒有 2/29，夾到 2/28
    ("2026-03-31", -1, "2026-02-28"),        # 31 日往回一個月：夾到月底
    ("2024-03-31", -1, "2024-02-29"),        # 閏年的 2 月底
    ("2026-01-31", -2, "2025-11-30"),        # 30 天的月份
    ("2026-11-02", -24, "2024-11-02"),       # 一般日
    ("2026-01-15", -13, "2024-12-15"),       # 跨年
])
def test_日曆月份加減_月底夾到該月最後一天(d, n, want):
    assert ar.add_months(date.fromisoformat(d), n).isoformat() == want


@pytest.mark.parametrize("today,day,expired", [
    ("2026-02-28", "2024-02-29", False),      # 閏日的摘要：2026-02-28 還在 24 個月內
    ("2026-03-01", "2024-02-29", True),       # 隔天才過期
    ("2026-02-28", "2024-02-28", False),      # 剛好滿 24 個日曆月那天還保留
    ("2026-03-01", "2024-02-28", True),
    ("2026-03-31", "2024-03-31", False),
    ("2026-04-01", "2024-03-31", True),
    ("2028-02-29", "2026-02-28", False),      # 今天是閏日：截止日夾到 2026-02-28，當天保留
    ("2028-02-29", "2026-02-27", True),
])
def test_24個月過期判斷_閏日與31日案例(today, day, expired):
    assert (day < ar.cutoff_day(date.fromisoformat(today), 24).isoformat()) is expired


def test_保存月數預設24_可設定_壞值視為損壞():
    c = mem()
    assert ar.get_retention(c)["months"] == 24 and not ar.get_retention(c)["broken"]
    ar.ensure(c)
    assert ar.set_retention(c, 36, "admin")["months"] == 36
    for bad in (0, 121, -3, "24", True, 2.5, None):
        with pytest.raises(ValueError):
            ar.set_retention(c, bad, "admin")
    for raw in ("abc", "0", "999", "24.0", " "):
        c.execute("INSERT OR REPLACE INTO app_settings VALUES (?,?)", (ar.RETENTION_KEY, raw))
        r = ar.get_retention(c)
        assert r["months"] == 24 and (r["broken"] is (raw.strip() != ""))      # 空白＝沒設定（預設 24）；其餘非法值＝損壞


# ---------------------------------------------------------------- 日摘要內容：缺資料不補 0、記來源／粒度／身分／演算法
def test_日摘要記來源粒度演算法版本與資產身分_缺資料不補0():
    c = mem()
    put(c, start=540, end=570)                                    # 只有 09:00–09:30 有資料（開盤時段）
    b = ar.summarize(c, "H", D)
    assert b["sources"] == ["nmon"] and b["granularity"]["interval_s"] == 60 and "raw" in b["granularity"]["kinds"]
    assert b["identity"]["mapped"] is False and b["identity"]["asset_serial"] is None and b["identity"]["device"] == "H"      # 未對應就如實寫未對應
    assert [s["key"] for s in b["config"]["slots"]] == ["open", "mid"]                               # 當時的時段設定一併凍結
    cells = cpu_cells(b)
    assert cells["open"]["samples"] == 30 and cells["open"]["status"] == "ok" and cells["open"]["avg"] == 50.0 and cells["open"]["missing_pct"] == 0.0
    assert cells["mid"]["samples"] == 0 and cells["mid"]["status"] == "no_data"
    assert cells["mid"]["avg"] is None and cells["mid"]["p95"] is None and cells["mid"]["max"] is None       # 不是 0
    mem_cells = [x for x in b["cells"] if x["metric"] == "mem_used"]
    assert mem_cells and all(x["status"] == "not_collected" and x["avg"] is None for x in mem_cells)       # 沒收集的指標不是 0 也不是正常
    h = ar.content_hash(b)
    assert h == ar.content_hash(ar.summarize(c, "H", D))                                            # 同樣輸入 → 同樣雜湊


def test_沒有樣本_非交易日_不建空列():
    c = mem()
    assert ar.summarize(c, "H", D) is None                         # 沒有任何資料
    put(c, day="2026-09-12")                                       # 週六
    assert ar.summarize(c, "H", "2026-09-12") is None              # 非交易日不封存
    ar.ensure(c)
    now = datetime(2026, 9, 14, 16)
    qw.set_clock(lambda: now)
    try:
        assert ar.tick(c, now) == 0                              # 前一日的指紋會含相鄰日（UTC 換算），可能被看一眼，但沒樣本就不建列
        assert c.execute("SELECT COUNT(*) FROM capacity_day_archive").fetchone()[0] == 0
        assert ar.due_items(c, now) == []                        # 看過就記住，不會每分鐘重算
    finally:
        qw.set_clock(None)


def test_休市日_平日也不封存():
    c = mem()
    put(c)
    ca.save_config(c, {"holidays": [{"date": D, "note": "颱風假"}]})
    assert ar.summarize(c, "H", D) is None and ar.due_items(c, NEXT) == []


# ---------------------------------------------------------------- 暫定 → 完成 → 修訂，不覆寫
def _run(c, now):
    return ar.tick(c, now)


def test_暫定_完成_修訂_不覆寫(monkeypatch):
    c = mem()
    put(c)
    at = lambda t: qw.set_clock(lambda: t)       # noqa: E731
    try:
        at(datetime(2026, 9, 10, 13, 29))
        assert ar.due_items(c, datetime(2026, 9, 10, 13, 29)) == []                 # 還沒收盤（13:30）不封存
        at(AFTER_CLOSE)
        assert _run(c, AFTER_CLOSE) == 1
        rows = c.execute("SELECT version, stage FROM capacity_day_archive ORDER BY version").fetchall()
        assert rows == [(1, "provisional")]
        assert _run(c, AFTER_CLOSE) == 0 and c.execute("SELECT COUNT(*) FROM capacity_day_archive").fetchone()[0] == 1          # 同一天內容沒變 → 不重複寫
        at(NEXT)
        assert _run(c, NEXT) == 1                                                  # T+1：升級成完成版（內容同，但階段改變 → 新增一版）
        assert c.execute("SELECT version, stage FROM capacity_day_archive ORDER BY version").fetchall() == [(1, "provisional"), (2, "final")]
        assert _run(c, NEXT) == 0
        put(c, cpu=77.0, replace=True)                                             # 完成後來源資料變了（例如補傳 NMON）
        assert _run(c, NEXT) == 1
        assert c.execute("SELECT version, stage FROM capacity_day_archive ORDER BY version").fetchall() == [(1, "provisional"), (2, "final"), (3, "revision")]
        v1 = json.loads(c.execute("SELECT summary_json FROM capacity_day_archive WHERE version=1").fetchone()[0])
        v3 = json.loads(c.execute("SELECT summary_json FROM capacity_day_archive WHERE version=3").fetchone()[0])
        assert cpu_cells(v1)["open"]["avg"] == 50.0 and cpu_cells(v3)["open"]["avg"] == 77.0            # 舊版原樣保留
        assert _run(c, NEXT) == 0
    finally:
        qw.set_clock(None)


def test_封存列不可被修改_也不可被直接刪除():
    c = mem()
    put(c)
    ar.ensure(c)
    ar.archive_one(c, "H", D, "final", "fp", "t", now=NEXT)
    with pytest.raises(sqlite3.DatabaseError):
        c.execute("UPDATE capacity_day_archive SET stage = 'revision'")
    with pytest.raises(sqlite3.DatabaseError):
        c.execute("UPDATE capacity_day_archive SET summary_json = '{}'")
    with pytest.raises(sqlite3.DatabaseError):
        c.execute("DELETE FROM capacity_day_archive")                               # 沒有清理流程的 guard 就刪不掉
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO capacity_day_archive (host, day, version, stage, algo_version, sources_json, granularity_json, identity_json, summary_json, content_hash, created_at, created_by) "
                  "VALUES ('H', ?, 1, 'final','a','[]','{}','{}','{}','x','t','t')", (D,))          # (主機,日,版本) 唯一
    assert c.execute("SELECT COUNT(*) FROM capacity_day_archive").fetchone()[0] == 1


def test_首次發現已是T加1以後_直接寫完成版_且不降級():
    c = mem()
    put(c)
    ar.ensure(c)
    r = ar.archive_one(c, "H", D, "provisional", "fp", "t", now=NEXT)              # 呼叫端誤給 provisional：沒有舊版 → 照給的階段
    assert r["stage"] == "provisional"
    r2 = ar.archive_one(c, "H", D, "final", "fp", "t", now=NEXT)
    assert r2["stage"] == "final"
    put(c, cpu=10.0, replace=True)
    r3 = ar.archive_one(c, "H", D, "provisional", "fp2", "t", now=NEXT)            # 已經有 final：就算要求 provisional 也只能寫 revision（不降級）
    assert r3["stage"] == "revision" and r3["version"] == 3


def test_兩條連線同時寫同一版_只會有一版():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        p = str(Path(tmp) / "t.db")
        c0 = sqlite3.connect(p)
        c0.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
        c0.execute(nmon_series.SERIES_SQL)
        put(c0)
        ar.ensure(c0)
        body = ar.summarize(c0, "H", D)
        out = []

        def go():
            cn = sqlite3.connect(p, timeout=20)
            try:
                out.append(ar.write_version(cn, "H", D, "final", body, "fp", "t", now=NEXT)["result"])
            finally:
                cn.close()
        ts = [threading.Thread(target=go) for _ in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert sorted(out) == ["unchanged"] * 3 + ["written"]
        assert c0.execute("SELECT COUNT(*) FROM capacity_day_archive").fetchone()[0] == 1
        c0.close()


# ---------------------------------------------------------------- 舊 DB 升級／重複啟動
def test_舊資料庫升級_補欄位不遺失資料_第二次不重複建立():
    c = mem()
    c.execute("""CREATE TABLE capacity_day_marker (id INTEGER PRIMARY KEY AUTOINCREMENT, trade_date TEXT NOT NULL, ranges_json TEXT NOT NULL, mtype TEXT NOT NULL,
        reason_cat TEXT NOT NULL, expected_level TEXT NOT NULL DEFAULT '', basis TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', market_note TEXT NOT NULL DEFAULT '',
        market_source TEXT NOT NULL DEFAULT '', scope TEXT NOT NULL DEFAULT '全系統', service_group TEXT NOT NULL DEFAULT '', source_kind TEXT NOT NULL, created_by TEXT,
        created_at TEXT NOT NULL, supersedes_id INTEGER, voided_at TEXT, voided_by TEXT, void_reason TEXT)""")                # 舊版：沒有 archive_ref／origin_source_kind
    c.execute("INSERT INTO capacity_day_marker (trade_date, ranges_json, mtype, reason_cat, source_kind, created_at) VALUES ('2025-01-02','[]','大量交易日','其他','pre_announce','t')")
    c.commit()
    ar.ensure(c)
    cols = {r[1] for r in c.execute("PRAGMA table_info(capacity_day_marker)")}
    assert "archive_ref" in cols
    assert c.execute("SELECT trade_date FROM capacity_day_marker").fetchall() == [("2025-01-02",)]       # 既有標記還在
    put(c)
    ar.archive_one(c, "H", D, "final", "fp", "t", now=NEXT)
    before = (c.execute("SELECT COUNT(*) FROM capacity_day_archive").fetchone()[0], c.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0],
              c.execute("SELECT allow_delete FROM capacity_day_archive_guard").fetchall())
    for _ in range(3):                                                                   # 重複啟動
        ar.ensure(c)
    after = (c.execute("SELECT COUNT(*) FROM capacity_day_archive").fetchone()[0], c.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0],
             c.execute("SELECT allow_delete FROM capacity_day_archive_guard").fetchall())
    assert before == after == (1, before[1], [(0,)])
    assert c.execute("SELECT COUNT(*) FROM capacity_day_archive_guard").fetchone()[0] == 1


# ---------------------------------------------------------------- 靜默窗：排程入口
@pytest.mark.parametrize("t,runs", [
    (datetime(2026, 9, 14, 8, 29, 59), True), (datetime(2026, 9, 14, 8, 30), False), (datetime(2026, 9, 14, 10), False),
    (datetime(2026, 9, 14, 14, 59, 59), False), (datetime(2026, 9, 14, 15, 0), True),
    (datetime(2026, 9, 12, 10), True),                                                   # 週六不在窗內
])
def test_盤中靜默_窗內不封存並記延後_窗外補跑(t, runs):
    c = mem()
    put(c, day="2026-09-11")                                                             # 週五的資料，隔週一才輪到
    qw.set_clock(lambda: t)
    try:
        n = ar.tick(c, t)
        assert (n == 1) is runs
        deferred = c.execute("SELECT count FROM quiet_window_log WHERE entry=? AND kind='deferred'", (ar.ENTRY,)).fetchone() if \
            c.execute("SELECT 1 FROM sqlite_master WHERE name='quiet_window_log'").fetchone() else None
        assert (deferred is None) is runs
        if not runs:
            assert c.execute("SELECT COUNT(*) FROM capacity_day_archive").fetchone()[0] == 0
    finally:
        qw.set_clock(None)


def test_沒有東西要封存時_不擾動延後紀錄():
    c = mem()
    t = datetime(2026, 9, 14, 10)
    qw.set_clock(lambda: t)
    try:
        assert ar.tick(c, t) == 0
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='quiet_window_log'").fetchone()
    finally:
        qw.set_clock(None)


def test_進窗就停_長任務逐項重查(monkeypatch):
    c = mem()
    for h in ("A", "B", "C"):
        put(c, host=h)
    t = datetime(2026, 9, 11, 16)
    calls = []
    real = ar.archive_one

    def fake(*a, **k):
        calls.append(a[1])
        qw.set_clock(lambda: datetime(2026, 9, 14, 9, 0))        # 第一台做完時鐘進入窗內
        return real(*a, **k)
    monkeypatch.setattr(ar, "archive_one", fake)
    qw.set_clock(lambda: t)
    try:
        ar.tick(c)                      # 不指定 now：用即時時鐘，每一項重查
    finally:
        qw.set_clock(None)
    assert calls == ["A"]


def test_入口登記_排程不可清理_tick與scan_service都沒有清理():
    assert qw.ENTRIES[ar.ENTRY]["cls"] == "A" and qw.TICK_SITES["capacity_archive.py:tick"] == ar.ENTRY
    assert qw.SCHEDULER_CALLS["_capacity_archive_tick"] == ar.ENTRY
    src = (BACKEND / "scan_service.py").read_text(encoding="utf-8")
    assert "purge" not in src.lower()
    tree = ast.parse((BACKEND / "capacity_archive.py").read_text(encoding="utf-8"))
    tick_fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "tick")
    called = {getattr(c.func, "attr", getattr(c.func, "id", "")) for c in ast.walk(tick_fn) if isinstance(c, ast.Call)}
    assert not any("purge" in n for n in called) and "DELETE" not in (ast.get_source_segment((BACKEND / "capacity_archive.py").read_text(encoding="utf-8"), tick_fn) or "").upper()
    # 整個後端只有路由（人工）呼叫 purge
    users = [p.name for p in BACKEND.rglob("*.py") if re.search(r"\.purge(_preview)?\(", p.read_text(encoding="utf-8")) and p.name != "capacity_archive.py"]
    assert users == ["capacity.py"], users


def test_排程跑再多次_也不會刪過期資料():
    c = mem()
    put(c, day="2020-01-06")
    ar.ensure(c)
    ar.archive_one(c, "H", "2020-01-06", "final", "fp", "t", now=datetime(2020, 1, 7, 16))
    t = datetime(2026, 9, 12, 10)                      # 週六，窗外，遠超過 24 個月
    qw.set_clock(lambda: t)
    try:
        for _ in range(3):
            ar.tick(c, t)
    finally:
        qw.set_clock(None)
    assert c.execute("SELECT COUNT(*) FROM capacity_day_archive WHERE day='2020-01-06'").fetchone()[0] == 1


# ---------------------------------------------------------------- 清理：預覽、對得上、標記保護
def _old(c, days, host="H"):
    ar.ensure(c)
    for d in days:
        put(c, day=d, host=host)
        ar.archive_one(c, host, d, "final", "fp", "t", now=datetime(2026, 9, 11, 16))


TODAY = datetime(2026, 9, 14, 10)           # 截止日（24 個月）＝2024-09-14


def test_預覽只算不刪_清理要對得上預覽():
    c = mem()
    _old(c, ["2024-09-12", "2024-09-13", "2024-09-16"])         # 前兩天過期，09-16 還在期限內
    pv = ar.purge_preview(c, "admin", TODAY)
    c.commit()
    assert pv["cutoff"] == "2024-09-14" and pv["rows"] == 2 and pv["host_days"] == 2 and pv["protected_rows"] == 0 and pv["blocked"] is None
    assert c.execute("SELECT COUNT(*) FROM capacity_day_archive").fetchone()[0] == 3                 # 預覽沒刪東西
    assert c.execute("SELECT action FROM capacity_day_archive_audit ORDER BY id DESC LIMIT 1").fetchone()[0] == "purge_preview"      # 預覽有稽核
    with pytest.raises(ValueError):
        ar.purge(c, "admin", 2, pv["cutoff"], False, TODAY)                                         # 沒確認
    with pytest.raises(ar.ArchiveConflict):
        ar.purge(c, "admin", 3, pv["cutoff"], True, TODAY)                                          # 筆數對不上預覽
    with pytest.raises(ar.ArchiveConflict):
        ar.purge(c, "admin", 2, "2024-09-15", True, TODAY)                                          # 截止日對不上
    with pytest.raises(ar.ArchiveConflict):
        ar.purge(c, "someone-else", 2, pv["cutoff"], True, TODAY)                                   # 沒有自己的預覽
    with pytest.raises(ar.ArchiveConflict):
        ar.purge(c, "admin", 2, pv["cutoff"], True, datetime(2026, 9, 14, 11, 0))                   # 預覽超過 30 分鐘
    assert c.execute("SELECT COUNT(*) FROM capacity_day_archive").fetchone()[0] == 3
    out = ar.purge(c, "admin", 2, pv["cutoff"], True, TODAY)
    c.commit()
    assert out["deleted_rows"] == 2
    assert [r[0] for r in c.execute("SELECT day FROM capacity_day_archive")] == ["2024-09-16"]
    a = c.execute("SELECT actor, detail_json FROM capacity_day_archive_audit WHERE action='purge'").fetchone()
    assert a[0] == "admin" and json.loads(a[1])["rows"] == 2
    assert c.execute("SELECT allow_delete FROM capacity_day_archive_guard").fetchone()[0] == 0          # guard 用完關回去
    with pytest.raises(sqlite3.DatabaseError):
        c.execute("DELETE FROM capacity_day_archive")


def test_預覽後資料又變了_清理回衝突():
    c = mem()
    _old(c, ["2024-09-12"])
    pv = ar.purge_preview(c, "admin", TODAY)
    c.commit()
    _old(c, ["2024-09-13"])                                                                         # 預覽後又多一筆過期
    with pytest.raises(ar.ArchiveConflict):
        ar.purge(c, "admin", pv["rows"], pv["cutoff"], True, TODAY)
    assert c.execute("SELECT COUNT(*) FROM capacity_day_archive").fetchone()[0] == 2


def _marker(c, td, ref=None, supersedes=None, voided=False):
    mk.ensure(c)
    c.execute("INSERT INTO capacity_day_marker (trade_date, ranges_json, mtype, reason_cat, source_kind, created_at, archive_ref, supersedes_id, voided_at) "
              "VALUES (?,?,?,?,?,?,?,?,?)", (td, "[]", "大量交易日", "其他", "pre_announce", "t", ref, supersedes, "x" if voided else None))
    c.commit()
    return c.execute("SELECT MAX(id) FROM capacity_day_marker").fetchone()[0]


@pytest.mark.parametrize("ref", ["2024-09-12", "2024-09-12|H", "2024-09-12|h", "archive:{id}", '["2024-01-01", "2024-09-12"]', "2024-01-01; 2024-09-12", "2024-09-12,2024-01-01"])
def test_被標記的archive_ref引用_整條版本鏈不可刪(ref):
    c = mem()
    _old(c, ["2024-09-12", "2024-09-11"])
    ar.archive_one(c, "H", "2024-09-12", "final", "fp", "t", now=datetime(2026, 9, 11, 16))        # 同日第二版（內容相同 → unchanged，不新增）
    put(c, day="2024-09-12", cpu=99.0, replace=True)
    ar.archive_one(c, "H", "2024-09-12", "revision", "fp2", "t", now=datetime(2026, 9, 11, 16))    # 第二版（修訂）
    first_id = c.execute("SELECT id FROM capacity_day_archive WHERE day='2024-09-12' AND version=1").fetchone()[0]
    _marker(c, "2030-01-01", ref.replace("{id}", str(first_id)))                                   # 標記的交易日期無關，靠 archive_ref
    pv = ar.purge_preview(c, "admin", TODAY)
    c.commit()
    assert pv["rows"] == 1 and pv["protected_rows"] == 2 and pv["protected_host_days"] == 1          # 2024-09-11 可刪；2024-09-12 兩版都受保護
    ar.purge(c, "admin", 1, pv["cutoff"], True, TODAY)
    c.commit()
    assert sorted(r[0] for r in c.execute("SELECT day FROM capacity_day_archive")) == ["2024-09-12", "2024-09-12"]


def test_被修訂取代的舊標記與已作廢標記_archive_ref一樣保護():
    c = mem()
    _old(c, ["2024-09-12", "2024-09-11", "2024-09-10", "2024-09-09"])
    m1 = _marker(c, "2030-01-02", "2024-09-12")                                    # 之後被修訂取代
    _marker(c, "2030-01-02", None, supersedes=m1)                                  # 修訂版沒有 archive_ref
    _marker(c, "2030-01-03", "2024-09-11", voided=True)                            # 已作廢
    pv = ar.purge_preview(c, "admin", TODAY)
    assert pv["rows"] == 2 and pv["protected_rows"] == 2
    kept = {x["day"]: x["reasons"] for x in pv["protected_sample"]}
    assert set(kept) == {"2024-09-12", "2024-09-11"} and any("被修訂取代" in r for r in kept["2024-09-12"]) and any("已作廢" in r for r in kept["2024-09-11"])


def test_標記的交易日期本身也保護_保守():
    c = mem()
    _old(c, ["2024-09-12", "2024-09-11"])
    _marker(c, "2024-09-12", None)
    pv = ar.purge_preview(c, "admin", TODAY)
    assert pv["rows"] == 1 and pv["protected_rows"] == 1


def test_archive_ref看不懂_清理_failclosed():
    c = mem()
    _old(c, ["2024-09-12"])
    _marker(c, "2030-01-02", "ARCH-2024-09-12")
    pv = ar.purge_preview(c, "admin", TODAY)
    c.commit()
    assert pv["blocked"] and "看不懂" in pv["blocked"]
    with pytest.raises(ar.ArchiveConflict):
        ar.purge(c, "admin", pv["rows"], pv["cutoff"], True, TODAY)
    assert c.execute("SELECT COUNT(*) FROM capacity_day_archive").fetchone()[0] == 1


def test_保存月數設定損壞_清理_failclosed():
    c = mem()
    _old(c, ["2024-09-12"])
    c.execute("INSERT OR REPLACE INTO app_settings VALUES (?,?)", (ar.RETENTION_KEY, "二十四"))
    pv = ar.purge_preview(c, "admin", TODAY)
    c.commit()
    assert pv["retention_broken"] and pv["blocked"]
    with pytest.raises(ar.ArchiveConflict):
        ar.purge(c, "admin", pv["rows"], pv["cutoff"], True, TODAY)
    assert c.execute("SELECT COUNT(*) FROM capacity_day_archive").fetchone()[0] == 1


def test_保存期限改短後_清理依新設定():
    c = mem()
    _old(c, ["2026-03-02", "2026-05-04"])
    ar.set_retention(c, 6, "admin", TODAY)
    c.commit()
    pv = ar.purge_preview(c, "admin", TODAY)
    assert pv["cutoff"] == "2026-03-14" and pv["rows"] == 1 and pv["oldest"] == "2026-03-02"


# ---------------------------------------------------------------- API：權限、登入停用、窗內確認、原子性
def _api(tmp, users):
    import api
    import auth
    import db
    from fastapi.testclient import TestClient
    p = Path(tmp) / "t.db"
    db.init_db(p)

    def _ov():
        cn = db.get_connection(p)
        try:
            yield cn
        finally:
            cn.close()
    api.app.dependency_overrides[api.get_db] = _ov
    cn = db.get_connection(p)
    for name, role in users:
        db.create_user(cn, name, auth.hash_password("test-password-123"))
        cn.execute("UPDATE users SET role = ? WHERE username = ?", (role, name))
    cn.commit()
    cn.close()

    def client(name):
        cl = TestClient(api.app, raise_server_exceptions=False)
        assert cl.post("/api/auth/login", json={"username": name, "password": "test-password-123"}).status_code == 200
        return cl
    return client, p, api


def _seed_file_db(p, days):
    import db
    cn = db.get_connection(p)
    ar.ensure(cn)
    for d in days:
        put(cn, day=d)
        ar.archive_one(cn, "H", d, "final", "fp", "t", now=datetime(2026, 9, 11, 16))
    cn.commit()
    cn.close()


def test_API_viewer與user全部403_admin可用_登入停用也403_唯讀大家可看():
    import db
    qw.set_clock(lambda: datetime(2026, 9, 12, 10))               # 週六：窗外
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            client, p, api = _api(tmp, [("admin", "admin"), ("alice", "user"), ("vera", "viewer")])
            _seed_file_db(p, ["2024-09-11", "2026-09-10"])
            admin, alice, vera = client("admin"), client("alice"), client("vera")
            writes = [("post", "/api/v2/capacity/archive/run", {"day": D}), ("put", "/api/v2/capacity/archive/settings", {"retention_months": 12}),
                      ("post", "/api/v2/capacity/archive/purge/preview", None), ("post", "/api/v2/capacity/archive/purge", {"expected_rows": 1, "cutoff": "2024-09-12", "confirm": True})]
            for who in (alice, vera):
                for method, url, body in writes:
                    r = getattr(who, method)(url, **({"json": body} if body is not None else {}))
                    assert r.status_code == 403, (method, url, r.status_code)
                r = who.get("/api/v2/capacity/archive")
                assert r.status_code == 200 and r.json()["total"] == 2 and r.json()["settings"]["retention_months"] == 24
                rid = r.json()["rows"][0]["id"]
                assert who.get(f"/api/v2/capacity/archive/{rid}").status_code == 200 and "summary" in who.get(f"/api/v2/capacity/archive/{rid}").json()
                assert who.get("/api/v2/capacity/archive/settings").status_code == 200
            assert admin.get("/api/v2/capacity/archive/999999").status_code == 404
            assert admin.put("/api/v2/capacity/archive/settings", json={"retention_months": 0}).status_code == 400
            assert admin.put("/api/v2/capacity/archive/settings", json={"retention_months": 30}).json()["months"] == 30
            assert admin.put("/api/v2/capacity/archive/settings", json={"retention_months": 24}).status_code == 200
            pv = admin.post("/api/v2/capacity/archive/purge/preview")
            assert pv.status_code == 200 and pv.json()["rows"] == 1
            assert admin.post("/api/v2/capacity/archive/purge", json={"expected_rows": 5, "cutoff": pv.json()["cutoff"], "confirm": True}).status_code == 409
            assert admin.post("/api/v2/capacity/archive/purge", json={"expected_rows": 1, "cutoff": pv.json()["cutoff"], "confirm": False}).status_code == 400
            ok = admin.post("/api/v2/capacity/archive/purge", json={"expected_rows": 1, "cutoff": pv.json()["cutoff"], "confirm": True})
            assert ok.status_code == 200 and ok.json()["deleted_rows"] == 1
            cn = db.get_connection(p)
            assert [r[0] for r in cn.execute("SELECT day FROM capacity_day_archive")] == ["2026-09-10"]
            acts = [r[0] for r in cn.execute("SELECT action FROM activity_log WHERE action LIKE 'capacity_archive%'")]
            assert "capacity_archive_purge" in acts and "capacity_archive_purge_preview" in acts and "capacity_archive_retention" in acts      # 操作紀錄也有
            cn.execute("INSERT OR REPLACE INTO app_settings VALUES ('auth_disabled', '1')")             # 登入停用：假 session 是 admin，但寫入／清理 fail-closed
            cn.commit()
            cn.close()
            for method, url, body in writes:
                r = getattr(admin, method)(url, **({"json": body} if body is not None else {}))
                assert r.status_code == 403, ("auth_disabled", method, url, r.status_code)
            assert admin.get("/api/v2/capacity/archive").status_code == 200
            api.app.dependency_overrides.clear()
    finally:
        qw.set_clock(None)


def test_API_人工封存_窗內要二次確認_窗外直接做_稽核寫不進去整筆回滾():
    import db
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        client, p, api = _api(tmp, [("admin", "admin")])
        cn = db.get_connection(p)
        put(cn, day=D)
        cn.close()
        admin = client("admin")
        qw.set_clock(lambda: datetime(2026, 9, 14, 10))              # 週一盤中
        try:
            r = admin.post("/api/v2/capacity/archive/run", json={"day": D})
            assert r.status_code == 409 and r.json()["detail"]["code"] == qw.CONFIRM_CODE
            cn = db.get_connection(p)
            ar.ensure(cn)
            assert cn.execute("SELECT COUNT(*) FROM capacity_day_archive").fetchone()[0] == 0
            cn.close()
            r = admin.post("/api/v2/capacity/archive/run", json={"day": D, "confirm_in_window": True})
            assert r.status_code == 200 and r.json()["results"][0]["result"] == "written" and r.json()["results"][0]["stage"] == "final"
            assert admin.post("/api/v2/capacity/archive/run", json={"day": D, "confirm_in_window": True}).json()["results"][0]["result"] == "unchanged"
            assert admin.post("/api/v2/capacity/archive/run", json={"day": "2026-09-14", "confirm_in_window": True}).status_code == 400          # 今天還沒收盤
            assert admin.post("/api/v2/capacity/archive/run", json={"day": "2026-09-12", "confirm_in_window": True}).status_code == 400          # 週六
            assert admin.post("/api/v2/capacity/archive/run", json={"day": "2026-09-20", "confirm_in_window": True}).status_code == 400          # 未來
            qw.set_clock(lambda: datetime(2026, 9, 14, 16))               # 收盤後、窗外
            cn = db.get_connection(p)
            put(cn, day=D, cpu=88.0, replace=True)
            cn.execute("CREATE TRIGGER no_audit BEFORE INSERT ON activity_log WHEN NEW.action = 'capacity_archive_run' BEGIN SELECT RAISE(ABORT, 'audit down'); END")
            cn.commit()
            cn.close()
            assert admin.post("/api/v2/capacity/archive/run", json={"day": D}).status_code >= 500
            cn = db.get_connection(p)
            assert cn.execute("SELECT COUNT(*) FROM capacity_day_archive").fetchone()[0] == 1               # 稽核失敗 → 修訂版一併回滾
            cn.execute("DROP TRIGGER no_audit")
            cn.commit()
            cn.close()
            r = admin.post("/api/v2/capacity/archive/run", json={"day": D})
            assert r.status_code == 200 and r.json()["results"][0]["stage"] == "revision"
        finally:
            qw.set_clock(None)
            api.app.dependency_overrides.clear()


def test_API_清理_稽核寫不進去_刪除一併回滾():
    import db
    qw.set_clock(lambda: datetime(2026, 9, 12, 10))
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            client, p, api = _api(tmp, [("admin", "admin")])
            _seed_file_db(p, ["2024-09-12", "2024-09-11"])
            admin = client("admin")
            pv = admin.post("/api/v2/capacity/archive/purge/preview").json()
            cn = db.get_connection(p)
            cn.execute("CREATE TRIGGER no_audit BEFORE INSERT ON activity_log WHEN NEW.action = 'capacity_archive_purge' BEGIN SELECT RAISE(ABORT, 'audit down'); END")
            cn.commit()
            cn.close()
            assert admin.post("/api/v2/capacity/archive/purge", json={"expected_rows": pv["rows"], "cutoff": pv["cutoff"], "confirm": True}).status_code >= 500
            cn = db.get_connection(p)
            assert cn.execute("SELECT COUNT(*) FROM capacity_day_archive").fetchone()[0] == 2                # 全部還在
            assert cn.execute("SELECT allow_delete FROM capacity_day_archive_guard").fetchone()[0] == 0      # guard 沒被留在開啟狀態
            cn.close()
            api.app.dependency_overrides.clear()
    finally:
        qw.set_clock(None)


# ---------------------------------------------------------------- GPT 5.6 審查補強
def test_init_db已建封存表_讀取不再建表(tmp_path):
    import db
    import sqlite3
    dbp = tmp_path / "x.db"
    db.init_db(dbp)
    c = db.get_connection(dbp)
    names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'capacity_day_archive%'")}
    assert {"capacity_day_archive", "capacity_day_archive_guard", "capacity_day_archive_audit"} <= names
    before = c.total_changes
    ar.listing(c)
    ar.settings_view(c)
    assert c.total_changes == before          # 讀取沒有任何寫入
    c.close()
