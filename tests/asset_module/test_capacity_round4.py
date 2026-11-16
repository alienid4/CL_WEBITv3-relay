"""ACT-04 容量分析最後一輪（2.041.42）：合法時刻、台北歸屬日選來源、指標存在才算可用、collected 按來源分組、A→B→A 不合併、數值驗證。"""
import gzip
import json
import math
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import capacity_analysis as ca  # noqa: E402
import nmon_series  # noqa: E402
import verify_numbers as vn  # noqa: E402


LEGACY_SLOTS = [  # 這些既有測試是用舊的六個範例時段寫的；真正的預設時段另有 test_capacity_defaults.py 測
    {"key": "pre_open", "label": "開盤前", "start": "08:30", "end": "09:00"},
    {"key": "open", "label": "開盤", "start": "09:00", "end": "09:30"},
    {"key": "early", "label": "早盤", "start": "09:30", "end": "11:00"},
    {"key": "mid", "label": "盤中", "start": "11:00", "end": "13:00"},
    {"key": "close", "label": "收盤", "start": "13:00", "end": "13:35"},
    {"key": "batch", "label": "盤後批次", "start": "18:00", "end": "23:00"},
]


@pytest.fixture(autouse=True)
def _legacy_slots(monkeypatch):
    monkeypatch.setattr(ca, "DEFAULT_SLOTS", [dict(s) for s in LEGACY_SLOTS])


def mem_conn():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
    c.execute(nmon_series.SERIES_SQL)
    c.execute(ca.WU_TABLE_SQL)
    return c


def put_row(c, table, host, day, row, itv=60):
    c.execute(f"INSERT OR REPLACE INTO {table} VALUES (?,?,?,?,?,?)", (host, day, itv, len(row["ts"]), gzip.compress(json.dumps(row).encode()), "x"))


def full_day(fn=lambda m: 10.0, itv=60, start=0, end=1440, **meta):
    step = max(1, itv // 60)
    ts = [f"{m // 60:02d}:{m % 60:02d}:00" for m in range(start, end, step)]
    row = {"ts": ts, "cols": {"cpu_busy": [fn(m) for m in range(start, end, step)]}, "kind": "raw"}
    row.update(meta)
    return row


def cells(h, **kw):
    return [x for x in h["cells"] if all(x.get(k) == v for k, v in kw.items())]


def cfg1(c):
    ca.save_config(c, {"params": {"min_days": 1}})
    return ca.get_config(c)


# ---------------- 1 合法時刻
@pytest.mark.parametrize("bad", ["24:00:00", "99:99:99", "23:60:00", "12:30:61", "9:30:00"])
def test_非法時刻整列拒絕_不遮蔽NMON(bad):
    c = mem_conn()
    cfg1(c)
    put_row(c, "nmon_series", "T", "2026-09-07", full_day(lambda m: 10.0))
    w = full_day(lambda m: 99.0)
    w["ts"][100] = bad                                   # 時間軸完整，只有一個非法時刻
    put_row(c, "capacity_whatsup_perf", "T", "2026-09-07", w)
    h = ca.analyze_host(c, "T", ca.get_config(c))
    assert h["sources"] == ["nmon"] and cells(h, slot="early", metric="cpu_busy")[0]["avg"] == 10.0
    assert any(l["code"] == "bad_time" for l in h["limits"])


def test_非UTC路徑也驗證時刻_且UTC路徑同樣拒絕():
    c = mem_conn()
    r = full_day()
    r["ts"][5] = "24:00:00"
    put_row(c, "nmon_series", "N", "2026-09-07", r)
    r2 = full_day(tz="UTC")
    r2["ts"][5] = "99:99:99"
    put_row(c, "nmon_series", "U", "2026-09-07", r2)
    for h in ("N", "U"):
        a = ca.analyze_host(c, h, ca.get_config(c))
        assert a["total_ts"] == 0 and any(l["code"] == "bad_time" for l in a["limits"])


# ---------------- 2 台北歸屬日選來源
def test_UTC一列跨兩個台北日_不因此捨棄補足台北日的NMON():
    c = mem_conn()
    cfg1(c)
    # WhatsUp UTC 儲存日 09-14 的一整天 ＝ 台北 09-14 08:00 ～ 09-15 07:59（每分鐘）
    put_row(c, "capacity_whatsup_perf", "S", "2026-09-14", full_day(lambda m: 99.0, tz="UTC"))
    put_row(c, "nmon_series", "S", "2026-09-15", full_day(lambda m: 10.0))                  # NMON 有完整的台北 09-15
    h = ca.analyze_host(c, "S", ca.get_config(c))
    w = cells(h, source="whatsup", slot="early", metric="cpu_busy", day_type="normal")[0]
    n = cells(h, source="nmon", slot="early", metric="cpu_busy", day_type="normal")[0]
    assert w["days"] == 1 and n["days"] == 1 and w["avg"] == 99.0 and n["avg"] == 10.0     # 09-14 用 WhatsUp、09-15 用 NMON，沒有混在同一台北日
    assert h["data_days"] == 2


# ---------------- 3 指標存在才算可用 ＋ 4 collected 按來源分組
def test_偏好來源有完整時間軸卻沒有CPU_NMON不被捨棄_該來源CPU格顯示未收集():
    c = mem_conn()
    cfg1(c)
    w = full_day()
    w["cols"] = {"mem_used": [50.0] * 1440}                    # 有時間軸、有記憶體，沒有 CPU
    put_row(c, "capacity_whatsup_perf", "P", "2026-09-07", w)
    put_row(c, "nmon_series", "P", "2026-09-07", full_day(lambda m: 10.0))
    h = ca.analyze_host(c, "P", ca.get_config(c))
    cpu_n = cells(h, source="nmon", slot="early", metric="cpu_busy")[0]
    cpu_w = cells(h, source="whatsup", slot="early", metric="cpu_busy")[0]
    mem_w = cells(h, source="whatsup", slot="early", metric="mem_used")[0]
    assert cpu_n["status"] == "ok" and cpu_n["avg"] == 10.0                          # CPU 由 NMON 補
    assert cpu_w["status"] == "not_collected" and cpu_w["samples"] == 0               # WhatsUp 完全沒收 CPU：未收集，不是無資料
    assert mem_w["status"] == "ok" and mem_w["avg"] == 50.0                            # 記憶體仍用 WhatsUp（逐指標選來源）


def test_偏好來源cols空白_同日NMON不被捨棄():
    c = mem_conn()
    cfg1(c)
    w = full_day()
    w["cols"] = {}
    put_row(c, "capacity_whatsup_perf", "E", "2026-09-07", w)
    put_row(c, "nmon_series", "E", "2026-09-07", full_day(lambda m: 10.0))
    h = ca.analyze_host(c, "E", ca.get_config(c))
    assert h["sources"] == ["nmon"] and cells(h, slot="early", metric="cpu_busy")[0]["avg"] == 10.0


# ---------------- 5 A→B→A 與未對應空窗
def _hw(c):
    c.execute("CREATE TABLE hardware (asset_serial TEXT, hostname TEXT, ip TEXT, environment TEXT, asset_purpose TEXT)")
    c.execute("INSERT INTO hardware VALUES ('A','a','','正式','x'),('B','b','','DR','備援')")


def test_A到B再到A_兩段A不合併():
    c = mem_conn()
    cfg1(c)
    _hw(c)
    ca.save_mapping(c, "nmon", "M", "A", "2026-01-01", "2026-09-07")
    ca.save_mapping(c, "nmon", "M", "B", "2026-09-08", "2026-09-09")
    ca.save_mapping(c, "nmon", "M", "A", "2026-09-10")
    for d in ("2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11"):
        put_row(c, "nmon_series", "M", d, full_day(lambda m, d=d: {"2026-09-08": 70.0, "2026-09-09": 70.0}.get(d, 10.0)))
    h = ca.analyze_host(c, "M", ca.get_config(c))
    segs = h["context"]["segments"]
    assert [(s["asset_serial"], s["from"], s["to"]) for s in segs] == [("A", "2026-09-07", "2026-09-07"), ("B", "2026-09-08", "2026-09-09"), ("A", "2026-09-10", "2026-09-11")]
    assert len({s["segment"] for s in segs}) == 3
    a_cells = [x for x in h["cells"] if x["asset_serial"] == "A" and x["slot"] == "early" and x["metric"] == "cpu_busy"]
    assert len(a_cells) == 2 and sorted(x["days"] for x in a_cells) == [1, 2]                  # 兩段 A 各算各的


def test_未對應期間各自成段_並標明():
    c = mem_conn()
    cfg1(c)
    _hw(c)
    ca.save_mapping(c, "nmon", "G", "A", "2026-09-08", "2026-09-08")
    for d in ("2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10"):
        put_row(c, "nmon_series", "G", d, full_day())
    h = ca.analyze_host(c, "G", ca.get_config(c))
    segs = h["context"]["segments"]
    assert [(s["mapped"], s["from"], s["to"]) for s in segs] == [(False, "2026-09-07", "2026-09-07"), (True, "2026-09-08", "2026-09-08"), (False, "2026-09-09", "2026-09-10")]
    assert segs[0]["segment"] != segs[2]["segment"]
    assert any(l["code"] == "purpose_unknown" for l in h["limits"])


# ---------------- 6 數值驗證與解壓上限
@pytest.mark.parametrize("patch", [
    lambda r: r["cols"]["cpu_busy"].__setitem__(3, float("nan")),
    lambda r: r["cols"]["cpu_busy"].__setitem__(3, "50"),
    lambda r: r["cols"]["cpu_busy"].__setitem__(3, math.inf),
    lambda r: r["cols"]["cpu_busy"].__setitem__(3, True),
    lambda r: r["cnt"]["cpu_busy"].__setitem__(3, -5),
    lambda r: r["exp"]["cpu_busy"].__setitem__(3, "5"),
    lambda r: r["max"]["cpu_busy"].__setitem__(3, float("nan")),
    lambda r: r["max"]["cpu_busy"].__setitem__(3, -1.0),
])
def test_彙整桶數值非有限或負數或字串_整列拒絕(patch):
    c = mem_conn()
    n = 288
    row = {"kind": "bucket", "interval_s": 300, "ts": [f"{m // 60:02d}:{m % 60:02d}:00" for m in range(0, 1440, 5)], "cols": {"cpu_busy": [10.0] * n},
           "max": {"cpu_busy": [20.0] * n}, "cnt": {"cpu_busy": [5] * n}, "exp": {"cpu_busy": [5] * n}}
    patch(row)
    c.execute("INSERT INTO capacity_whatsup_perf VALUES ('V','2026-09-07',300,?,?,'x')", (n, gzip.compress(json.dumps(row).encode())))
    h = ca.analyze_host(c, "V", ca.get_config(c))
    assert h["total_ts"] == 0 and any(l["code"] == "bad_value" for l in h["limits"])


def test_乾淨的彙整桶照常通過數值驗證():
    c = mem_conn()
    n = 288
    row = {"kind": "bucket", "interval_s": 300, "ts": [f"{m // 60:02d}:{m % 60:02d}:00" for m in range(0, 1440, 5)], "cols": {"cpu_busy": [10.0] * n},
           "max": {"cpu_busy": [20.0] * n}, "cnt": {"cpu_busy": [5] * n}, "exp": {"cpu_busy": [5] * n}}
    c.execute("INSERT INTO capacity_whatsup_perf VALUES ('V','2026-09-07',300,?,?,'x')", (n, gzip.compress(json.dumps(row).encode())))
    assert ca.analyze_host(c, "V", ca.get_config(c))["total_ts"] == n


def test_independent_解壓套用與正式分析相同的大小上限(monkeypatch):
    c = mem_conn()
    put_row(c, "nmon_series", "Z", "2026-09-07", full_day())
    a = ca.analyze_host(c, "Z", ca.get_config(c))
    monkeypatch.setattr(ca, "MAX_BLOB_BYTES", 100)
    out = vn.independent_capacity(c, "Z", ca.get_config(c), a)
    assert out[0][3] == "unverified" and "上限" in out[0][0]
    assert ca.analyze_host(c, "Z", ca.get_config(c))["total_ts"] == 0                 # 正式分析同樣拒絕這列


def test_1081_偏好來源某指標稀疏_該指標改用完整的備援來源_其他指標照用偏好來源():
    """Codex 1081：WhatsUp 記憶體整天都有、CPU 只有 1 點；NMON CPU 整天都有。CPU 要用 NMON，記憶體仍用 WhatsUp。"""
    c = mem_conn()
    cfg1(c)
    w = full_day(lambda m: 50.0, itv=300)
    n = len(w["ts"])
    w["cols"] = {"cpu_busy": [91.0] + [None] * (n - 1), "mem_used": [70.0] * n}
    put_row(c, "capacity_whatsup_perf", "T", "2026-09-07", w, itv=300)
    put_row(c, "nmon_series", "T", "2026-09-07", full_day(lambda m: 33.0, itv=300), itv=300)
    _rows, _cnt, choose = ca._prepare_rows(c, "T", "2026-09-07", "2026-09-07", 80)
    assert choose[("2026-09-07", "cpu_busy")] == "nmon"                  # 修前 whatsup（整天時間戳算 100%）
    assert choose[("2026-09-07", "mem_used")] == "whatsup"
    # 對照：WhatsUp CPU 也整天都有 → 照偏好用 WhatsUp
    w["cols"]["cpu_busy"] = [91.0] * n
    put_row(c, "capacity_whatsup_perf", "T", "2026-09-07", w, itv=300)
    _rows, _cnt, choose = ca._prepare_rows(c, "T", "2026-09-07", "2026-09-07", 80)
    assert choose[("2026-09-07", "cpu_busy")] == "whatsup"
