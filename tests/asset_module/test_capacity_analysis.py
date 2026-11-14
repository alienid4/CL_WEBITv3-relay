"""ACT-04 證券業分時段容量分析（capacity_analysis，2.041.39）。全部假資料（主機名 DEMO-/TEST-，IP 不出現）。

守的重點：
  1. 早盤 90%＋離峰 10% 不可被平均稀釋——早盤格的 P95／最大值要是高的，且輸出裡沒有「全天平均」；
  2. 覆蓋不足的格子不給平均／P95（只留最大值當下限證據），不是 0 也不是空白；
  3. 沒收集的指標標「未收集」，不是 0；
  4. 大量交易日、一般交易日、非交易日分開算；週末與休市日不混進交易日；
  5. 時段可設定、驗證嚴格（重疊／格式錯整批不存），預設只是「範例」；
  6. 限制明列：資料天數不足、未設定大量交易日、備援用途、缺業務延遲證據；
  7. 加總＝下鑽（各格取樣點加總 == 資料庫該台取樣點數）；示範資料只在記憶體、不寫真實 DB。
"""
import gzip
import json
import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import auth  # noqa: E402
import api  # noqa: E402
import capacity_analysis as ca  # noqa: E402
import db  # noqa: E402
import nmon_series  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_STUB = "test-password-123"


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


def mem_conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
    c.execute(nmon_series.SERIES_SQL)
    return c


def put_day(c, host, day, fn, interval=60, metrics=("cpu_busy",), start=0, end=1440, extra=None):
    """fn(minute) → cpu 值。每 interval 秒一個點。"""
    ts, cpu = [], []
    step = interval // 60 if interval >= 60 else 1
    for m in range(start, end, step):
        ts.append(f"{m // 60:02d}:{m % 60:02d}:00")
        cpu.append(fn(m))
    cols = {}
    if "cpu_busy" in metrics:
        cols["cpu_busy"] = cpu
    if "mem_used" in metrics:
        cols["mem_used"] = [50.0] * len(ts)
    if extra:
        cols.update({k: [v] * len(ts) for k, v in extra.items()})
    blob = gzip.compress(json.dumps({"ts": ts, "cols": cols, "top": {}}).encode())
    c.execute("INSERT OR REPLACE INTO nmon_series VALUES (?,?,?,?,?,?)", (host, day, interval, len(ts), blob, None))


def weekdays(start: date, n: int):
    d, out = start, []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def early_heavy(m):      # 早盤 09:30–11:00 = 90，其餘 10
    return 90.0 if 9 * 60 + 30 <= m < 11 * 60 else 10.0


def cell(h, dt, slot, metric="cpu_busy"):
    return next(c for c in h["cells"] if c["day_type"] == dt and c["slot"] == slot and c["metric"] == metric)


# ---------------------------------------------------------------- 核心：不被平均稀釋
def test_早盤90離峰10_早盤格的P95與最大值是90_不被平均稀釋():
    c = mem_conn()
    for d in weekdays(date(2026, 9, 7), 25):
        put_day(c, "TEST-A", d, early_heavy)
    cfg = ca.get_config(c)
    h = ca.analyze_host(c, "TEST-A", cfg, idx=None)
    early = cell(h, "normal", "early")
    assert early["status"] == "ok" and early["p95"] == 90.0 and early["max"] == 90.0 and early["avg"] == 90.0
    off = cell(h, "normal", ca.OTHER_KEY)
    assert off["avg"] == 10.0                        # 離峰是離峰，不會被早盤拉高
    # 沒有任何「全天平均」欄位（全天平均 ≈ 約 20，會把早盤掩蓋）
    assert not any("day_avg" in k or "daily" in k for k in h)
    assert h["peak_cpu"]["slot"] == "early" and h["peak_cpu"]["p95"] == 90.0


def test_尖峰只有一次_P95不被洗掉但最大值抓得到():
    c = mem_conn()
    days = weekdays(date(2026, 9, 7), 25)
    for i, d in enumerate(days):
        put_day(c, "TEST-B", d, lambda m, i=i: 99.0 if (i == 3 and m == 9 * 60 + 40) else 20.0)
    h = ca.analyze_host(c, "TEST-B", ca.get_config(c), idx=None)
    e = cell(h, "normal", "early")
    assert e["max"] == 99.0 and e["max_at"] == f"{days[3]} 09:40:00"
    assert e["p95"] == 20.0 and e["avg"] < 21        # P95 反映常態；尖峰由最大值揭露——兩個都給，不能只給平均


# ---------------------------------------------------------------- 覆蓋率
def test_覆蓋不足的格子不給平均與P95_只留最大值():
    c = mem_conn()
    for d in weekdays(date(2026, 9, 7), 25):
        # 只有 09:30–09:45 有資料（早盤共 90 分鐘 → 覆蓋 ~16.7%）
        put_day(c, "TEST-C", d, lambda m: 70.0, start=9 * 60 + 30, end=9 * 60 + 45)
    e = cell(ca.analyze_host(c, "TEST-C", ca.get_config(c), idx=None), "normal", "early")
    assert e["status"] == "low_coverage" and e["coverage_pct"] < 20
    assert e["avg"] is None and e["p95"] is None and e["max"] == 70.0
    h = ca.analyze_host(c, "TEST-C", ca.get_config(c), idx=None)
    assert any(l["code"] == "low_coverage_cells" for l in h["limits"])
    assert cell(h, "normal", "mid")["status"] == "no_data"      # 完全沒資料的格子：無資料，不是 0


def test_期望樣本數與覆蓋率的算法():
    c = mem_conn()
    put_day(c, "TEST-D", "2026-09-07", lambda m: 50.0)          # 週一、1 分鐘取樣、整天
    h = ca.analyze_host(c, "TEST-D", ca.get_config(c), idx=None)
    e = cell(h, "normal", "early")
    assert e["expected"] == 90 and e["samples"] == 90 and e["coverage_pct"] == 100.0


# ---------------------------------------------------------------- 日型
def test_日型_週末與休市日_大量交易日標註優先():
    heavy, hol = {"2026-09-09"}, {"2026-09-10"}
    assert ca.day_type("2026-09-07", heavy, hol) == "normal"     # 週一
    assert ca.day_type("2026-09-09", heavy, hol) == "heavy"      # 標註日（分析時仍是交易日）
    assert ca.day_type("2026-09-10", heavy, hol) == "off"        # 休市日（週四）
    assert ca.day_type("2026-09-12", heavy, hol) == "off"        # 週六
    assert ca.day_type("2026-09-12", {"2026-09-12"}, hol) == "heavy"   # 人明確指定那天有交易


def test_大量交易日只是事後標註_不另開格子_標註日對照其他日():
    c = mem_conn()
    days = weekdays(date(2026, 9, 7), 25)
    c.execute("INSERT INTO app_settings VALUES (?,?)", (ca.HEAVY_KEY, json.dumps([{"date": days[5], "note": "x"}])))
    for d in days:
        put_day(c, "TEST-E", d, lambda m, d=d: (95.0 if d == days[5] else 40.0) if 9 * 60 + 30 <= m < 11 * 60 else 5.0)
    h = ca.analyze_host(c, "TEST-E", ca.get_config(c), idx=None)
    e = cell(h, "normal", "early")
    assert not [x for x in h["cells"] if x["day_type"] == "heavy"]                     # 沒有 heavy 日型的格子
    assert e["max"] == 95.0 and e["days"] == 25 and e["avg"] < 45                      # 標註日與其他交易日同在『交易日』這一格
    assert h["days_by_type"] == {"normal": 25, "off": 0} and h["annotated_days"] == [days[5]]
    assert e["annotated_cmp"] == {"annotated_days": 1, "annotated_max": 95.0, "other_days": 24, "other_max": 40.0, "by_kind": {"legacy": {"days": 1, "max": 95.0}}}
    assert e["peak_days"][0]["date"] == days[5] and e["peak_days"][0]["annotated"] == "legacy"


def test_沒有任何大量交易日設定_尖峰日排名仍正確_不要求事先定義():
    c = mem_conn()
    days = weekdays(date(2026, 9, 7), 25)
    spike = {days[3]: 99.0, days[10]: 97.0, days[11]: 80.0, days[20]: 80.0, days[4]: 60.0, days[7]: 50.0}
    for d in days:
        put_day(c, "TEST-F", d, lambda m, d=d: spike.get(d, 20.0) if 9 * 60 + 30 <= m < 9 * 60 + 40 else 5.0)
    h = ca.analyze_host(c, "TEST-F", ca.get_config(c), idx=None)
    e = cell(h, "normal", "early")
    assert [r["date"] for r in e["peak_days"]] == [days[3], days[10], days[11], days[20], days[4]]        # 前 5 名；同分(80)依日期
    assert e["period_max"] == 99.0 and e["period_max_day"] == days[3]
    assert e["top5_max_median"] == 80.0                                                                     # 99,97,80,80,60 的中位數
    assert e["annotated_cmp"] is None and h["annotated_days"] == []                                         # 沒標註就沒有對照，也不影響結果
    assert not any(l["code"] in ("heavy_not_configured", "few_heavy_days") for l in h["limits"])           # 不再要求事先定義
    assert any(l["code"] == "extreme_day_unknown" and "無法事先定義" in l["text"] for l in h["limits"])


def test_尖峰日排名_P95不可得的彙整桶排在後面_且標不可得():
    c = mem_conn()
    c.execute(ca.WU_TABLE_SQL)
    ca.save_config(c, {"params": {"min_days": 1}})
    ts = [f"{9 + m // 60:02d}:{m % 60:02d}:00" for m in range(30, 120, 5)]
    n = len(ts)
    for d, mx in (("2026-09-07", 70.0), ("2026-09-08", 90.0)):
        row = {"kind": "bucket", "interval_s": 300, "ts": ts, "cols": {"cpu_busy": [10.0] * n}, "max": {"cpu_busy": [mx] * n}, "cnt": {"cpu_busy": [5] * n}}
        c.execute("INSERT INTO capacity_whatsup_perf VALUES ('B',?,300,?,?,'x')", (d, n, gzip.compress(json.dumps(row).encode())))
    e = cell(ca.analyze_host(c, "B", ca.get_config(c)), "normal", "early")
    assert [r["date"] for r in e["peak_days"]] == ["2026-09-08", "2026-09-07"] and all(r["p95"] is None for r in e["peak_days"])


# ---------------------------------------------------------------- 限制與證據等級
def test_必有限制_缺業務延遲_時區假設_不自動調整():
    c = mem_conn()
    for d in weekdays(date(2026, 6, 1), 65):
        put_day(c, "TEST-G", d, early_heavy)
    h = ca.analyze_host(c, "TEST-G", ca.get_config(c), idx=None)
    codes = {l["code"] for l in h["limits"]}
    assert {"no_business_latency", "tz_assumed", "no_auto_action"} <= codes


def test_資料不到90天_標span_short_不得說足夠():
    c = mem_conn()
    for d in weekdays(date(2026, 9, 7), 25):
        put_day(c, "TEST-H", d, early_heavy)
    h = ca.analyze_host(c, "TEST-H", ca.get_config(c), idx=None)
    assert any(l["code"] == "span_short" for l in h["limits"])
    assert h["evidence"] != "adequate"


def test_沒有歷史_證據不足_不補造():
    c = mem_conn()
    h = ca.analyze_host(c, "NOPE", ca.get_config(c), idx=None)
    assert h["evidence"] == "insufficient" and h["data_days"] == 0
    assert any(l["code"] == "no_history" for l in h["limits"])
    assert all(x["samples"] == 0 and x["avg"] is None for x in h["cells"])


def test_未收集的指標_標未收集_不是0():
    c = mem_conn()
    put_day(c, "TEST-I", "2026-09-07", lambda m: 30.0, metrics=("cpu_busy",))
    h = ca.analyze_host(c, "TEST-I", ca.get_config(c), idx=None)
    m = cell(h, "normal", "early", "mem_used")
    assert m["status"] == "not_collected" and m["avg"] is None and m["max"] is None
    assert any(l["code"] == "metric_not_collected" for l in h["limits"])


def test_備援用途_標limit_不得據以減配():
    c = mem_conn()
    c.execute("CREATE TABLE hardware (asset_serial TEXT, hostname TEXT, ip TEXT, environment TEXT, asset_purpose TEXT)")
    c.execute("INSERT INTO hardware VALUES ('SN1','test-j.example.local','','DR','災難備援')")
    put_day(c, "TEST-J", "2026-09-07", lambda m: 3.0)
    # 名稱相符只是候選：沒有人工對應時不得自動綁定（所以還看不出備援）
    h0 = ca.analyze_host(c, "TEST-J", ca.get_config(c))
    assert h0["context"]["mapped"] is False and h0["context"]["candidates"] == 1 and h0["context"]["standby"] is None
    ca.save_mapping(c, "nmon", "TEST-J", "SN1", "2026-01-01")
    h = ca.analyze_host(c, "TEST-J", ca.get_config(c))
    assert h["context"]["standby"] is True
    assert h["limits"][0]["code"] == "standby_use" and "待命期間負載" in h["limits"][0]["text"]


def test_主機對不到資產_用途不明列限制():
    c = mem_conn()
    c.execute("CREATE TABLE hardware (asset_serial TEXT, hostname TEXT, ip TEXT, environment TEXT, asset_purpose TEXT)")
    put_day(c, "TEST-K", "2026-09-07", lambda m: 3.0)
    h = ca.analyze_host(c, "TEST-K", ca.get_config(c))
    assert h["context"]["mapped"] is False and any(l["code"] == "purpose_unknown" for l in h["limits"])


def test_對應表_生效期間_重疊與不存在資產_都拒絕():
    c = mem_conn()
    c.execute("CREATE TABLE hardware (asset_serial TEXT, hostname TEXT, ip TEXT, environment TEXT, asset_purpose TEXT)")
    c.execute("INSERT INTO hardware VALUES ('SN1','a','','正式','x'),('SN2','b','','DR','備援')")
    ca.save_mapping(c, "nmon", "DEV1", "SN1", "2026-01-01", "2026-06-30")
    with pytest.raises(ValueError, match="重疊"):
        ca.save_mapping(c, "nmon", "DEV1", "SN2", "2026-06-01")
    with pytest.raises(ValueError, match="找不到"):
        ca.save_mapping(c, "nmon", "DEV1", "NOPE", "2026-07-01")
    ca.save_mapping(c, "nmon", "DEV1", "SN2", "2026-07-01")               # 舊的結束後可接新的
    assert ca._host_context(c, "DEV1", "nmon", "2026-03-01")["standby"] is False
    assert ca._host_context(c, "DEV1", "nmon", "2026-08-01")["standby"] is True    # 依資料日期取當時有效的那筆


def test_UTC原始資料_轉台北時間再分時段():
    from datetime import datetime
    c = mem_conn()
    c.execute(ca.WU_TABLE_SQL)
    ts = [f"{h:02d}:00:00" for h in range(24)]
    cpu = [90.0 if h == 1 else 5.0 for h in range(24)]       # UTC 01:00 = 台北 09:00（開盤）
    blob = gzip.compress(json.dumps({"ts": ts, "cols": {"cpu_busy": cpu}, "tz": "UTC", "kind": "raw"}).encode())
    c.execute("INSERT INTO capacity_whatsup_perf VALUES ('U1','2026-09-07',3600,24,?,NULL)", (blob,))
    h = ca.analyze_host(c, "U1", ca.get_config(c))
    assert h["source"] == "whatsup"
    # 若沒轉換，UTC 01:00 會落在『其他時間』而讓它的最大值是 90；轉成台北 09:00 後那桶屬於『開盤』時段，其他時間只剩 5
    assert cell(h, "normal", ca.OTHER_KEY)["max"] == 5.0
    assert h["bucketed_ts"] == 24 and h["total_ts"] == 24


def test_彙整桶_P95不可得_不得平均各桶P95_avg以筆數加權():
    c = mem_conn()
    c.execute(ca.WU_TABLE_SQL)
    ca.save_config(c, {"params": {"min_days": 1}})
    ts = [f"{9 + m // 60:02d}:{m % 60:02d}:00" for m in range(30, 120, 5)]       # 09:30–10:55 每 5 分鐘一桶
    n = len(ts)
    blob = gzip.compress(json.dumps({"ts": ts, "cols": {"cpu_busy": [10.0] * (n - 1) + [70.0]}, "max": {"cpu_busy": [20.0] * (n - 1) + [95.0]},
                                     "cnt": {"cpu_busy": [5] * (n - 1) + [1]}, "exp": {"cpu_busy": [5] * n}}).encode())
    c.execute("INSERT INTO capacity_whatsup_perf VALUES ('B1','2026-09-07',300,?,?,NULL)", (n, blob))
    h = ca.analyze_host(c, "B1", ca.get_config(c))
    e = cell(h, "normal", "early")
    assert e["bucket"] is True and e["p95"] is None and "不可得" in e["p95_note"]
    assert e["max"] == 95.0 and e["samples"] == 5 * (n - 1) + 1
    assert abs(e["avg"] - (10.0 * 5 * (n - 1) + 70.0) / (5 * (n - 1) + 1)) < 0.01      # 筆數加權
    assert any(l["code"] == "p95_unavailable" for l in h["limits"])
    assert h["peak_cpu"]["basis"] == "max"


def test_最大連續缺口與值為0不等於缺值():
    c = mem_conn()
    # 09:30–11:00 之間 10:00–10:30 沒資料；另外有一段真的是 0
    put_day(c, "TEST-G2", "2026-09-07", lambda m: 0.0 if 9 * 60 + 30 <= m < 9 * 60 + 40 else 50.0, start=9 * 60 + 30, end=10 * 60)
    put_day(c, "TEST-G2", "2026-09-08", lambda m: 50.0, start=9 * 60 + 30, end=11 * 60)
    h = ca.analyze_host(c, "TEST-G2", ca.get_config(c))
    e = cell(h, "normal", "early")
    assert e["max_gap_min"] is not None and e["max_gap_min"] >= 1
    assert e["status"] in ("ok", "low_coverage") and e["samples"] == 30 + 90
    assert ca._stats([0.0, 0.0])["avg"] == 0.0                     # 真的 0 是 0


# ---------------------------------------------------------------- 設定
def test_存過設定才標管理員設定():
    c = mem_conn()
    cfg = ca.get_config(c)
    assert cfg["slots_are_example"] is False and cfg["heavy_days"] == [] and cfg["holidays"] == []           # 大量交易日、休市日預設空，不擅定
    out = ca.save_config(c, {"slots": [{"key": "a", "label": "甲", "start": "09:00", "end": "10:00"}]})
    assert out["slots_source"] == "管理員設定" and len(out["slots"]) == 1


@pytest.mark.parametrize("slots,msg", [
    ([{"key": "a", "label": "A", "start": "09:00", "end": "10:00"}, {"key": "b", "label": "B", "start": "09:30", "end": "11:00"}], "重疊"),
    ([{"key": "a", "label": "A", "start": "9am", "end": "10:00"}], "HH:MM"),
    ([{"key": "a", "label": "A", "start": "09:00", "end": "09:00"}], "不能相同"),
    ([{"key": "a", "label": "A", "start": "09:00", "end": "10:00"}, {"key": "a", "label": "B", "start": "11:00", "end": "12:00"}], "重複"),
    ([], "不可為空"),
])
def test_時段驗證_錯的整批不存(slots, msg):
    c = mem_conn()
    with pytest.raises(ValueError, match=msg):
        ca.save_config(c, {"slots": slots})
    assert c.execute("SELECT COUNT(*) FROM app_settings").fetchone()[0] == 0


def test_跨午夜時段():
    c = mem_conn()
    ca.save_config(c, {"slots": [{"key": "n", "label": "夜間", "start": "22:00", "end": "02:00"}]})
    # 週五整天＋週六整天：週六 00:00–02:00 屬於「週五開始的夜間時段」→ 歸一般交易日；週六 22:00 開始的那一段才是非交易日
    for d in ("2026-09-11", "2026-09-12"):
        put_day(c, "TEST-L", d, lambda m: 60.0 if (m >= 22 * 60 or m < 2 * 60) else 5.0)
    h = ca.analyze_host(c, "TEST-L", ca.get_config(c), idx=None)
    n = cell(h, "normal", "n")
    assert n["samples"] == 360 and n["expected"] == 480 and n["days"] == 2          # 歸屬日：9/10（週五凌晨尾段）、9/11（週五晚段＋週六凌晨尾段）
    off = cell(h, "off", "n")
    assert off["samples"] == 120 and off["days"] == 1                               # 只有週六 22:00–24:00
    assert n["max_gap_min"] == 120                                                  # 9/10 那天只有 00:00–02:00 的尾段，前 120 分鐘（22:00–24:00）缺


def test_大量交易日與休市日同一天_拒絕():
    c = mem_conn()
    with pytest.raises(ValueError, match="同一天"):
        ca.save_config(c, {"heavy_days": [{"date": "2026-09-09"}], "holidays": [{"date": "2026-09-09"}]})
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        ca.save_config(c, {"heavy_days": [{"date": "9/9"}]})


def test_參數範圍():
    c = mem_conn()
    with pytest.raises(ValueError):
        ca.save_config(c, {"params": {"min_coverage_pct": 0}})
    assert ca.save_config(c, {"params": {"min_coverage_pct": 50}})["params"]["min_coverage_pct"] == 50


# ---------------------------------------------------------------- 加總＝下鑽、百分位
def test_加總等於下鑽_各格取樣點加總等於資料庫取樣點數():
    c = mem_conn()
    for d in weekdays(date(2026, 9, 5), 12) + ["2026-09-12", "2026-09-13"]:     # 含週末
        put_day(c, "TEST-M", d, early_heavy, interval=300)
    for r in ca.reconcile(c):
        assert r["bucketed"] == r["analysis_total"] == r["db_samples"] > 0


def test_nearest_rank百分位():
    assert ca.percentile(list(range(1, 101)), 95) == 95
    assert ca.percentile([1.0], 95) == 1.0
    assert ca.percentile([1, 2, 3, 4], 50) == 2


def test_取樣間隔沒寫_用中位數推算並標推算():
    c = mem_conn()
    put_day(c, "TEST-N", "2026-09-07", lambda m: 10.0)
    c.execute("UPDATE nmon_series SET interval_s = NULL")
    h = ca.analyze_host(c, "TEST-N", ca.get_config(c), idx=None)
    assert h["interval_s"] == 60 and any(l["code"] == "interval_inferred" for l in h["limits"])


def test_磁碟忙碌取最忙那顆_吞吐為讀寫加總():
    c = mem_conn()
    put_day(c, "TEST-O", "2026-09-07", lambda m: 10.0,
            extra={"disk:hdisk0:busy": 20.0, "disk:hdisk1:busy": 70.0, "disk:hdisk0:rd": 100.0, "disk:hdisk0:wr": 50.0, "disk:hdisk1:rd": 10.0})
    h = ca.analyze_host(c, "TEST-O", ca.get_config(c), idx=None)
    assert cell(h, "normal", "early", "disk_busy_max")["max"] == 70.0
    assert cell(h, "normal", "early", "disk_kbs")["max"] == 160.0


# ---------------------------------------------------------------- 示範資料
def test_示範資料_只在記憶體_不碰真實資料庫_且早盤型可見尖峰():
    d = ca.demo_overview()
    assert d["demo"] is True and all(h["host"].startswith("DEMO-") for h in d["hosts"])
    front = next(h for h in d["hosts"] if h["host"] == "DEMO-FRONT01")
    assert cell(front, "normal", "early")["p95"] > 80 and cell(front, "normal", ca.OTHER_KEY)["avg"] < 15
    sparse = next(h for h in d["hosts"] if h["host"] == "DEMO-SPARSE01")
    assert sparse["span_days"] < 30 and any(l["code"] == "span_short" for l in sparse["limits"])
    assert all(len(h["limits"]) >= 3 for h in d["hosts"])


# ---------------------------------------------------------------- API（含權限）
def _client(tmp, user, role):
    p = Path(tmp) / "t.db"
    db.init_db(p)

    def _ov():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()
    api.app.dependency_overrides[api.get_db] = _ov
    cl = TestClient(api.app)
    c = db.get_connection(p)
    db.create_user(c, user, auth.hash_password(_STUB))
    c.execute("UPDATE users SET role = ? WHERE username = ?", (role, user))
    c.commit()
    c.close()
    assert cl.post("/api/auth/login", json={"username": user, "password": _STUB}).status_code == 200
    return cl, p


def test_API_概覽_設定_權限():
    with tempfile.TemporaryDirectory() as tmp:
        cl, p = _client(tmp, "admin", "admin")
        c = db.get_connection(p)
        nmon_series.ensure(c)
        for d in weekdays(date(2026, 9, 7), 5):
            put_day(c, "TEST-P", d, early_heavy)
        c.commit()
        c.close()
        r = cl.get("/api/v2/capacity/overview")
        assert r.status_code == 200
        j = r.json()
        assert j["demo"] is False and [h["host"] for h in j["hosts"]] == ["TEST-P"] and j["hosts"][0]["no"] == 1
        assert j["config"]["slots_are_example"] is False
        assert cl.get("/api/v2/capacity/overview?from=bad").status_code == 400
        assert cl.get("/api/v2/capacity/demo").json()["demo"] is True
        bad = cl.put("/api/v2/capacity/config", json={"slots": [{"key": "a", "label": "A", "start": "x", "end": "10:00"}]})
        assert bad.status_code == 400
        ok = cl.put("/api/v2/capacity/config", json={"heavy_days": [{"date": "2026-09-09", "note": "測試"}]})
        assert ok.status_code == 200 and ok.json()["heavy_days"][0]["date"] == "2026-09-09"
        c = db.get_connection(p)
        assert c.execute("SELECT COUNT(*) FROM activity_log WHERE action = 'capacity_settings'").fetchone()[0] == 1
        c.close()
        api.app.dependency_overrides.clear()


def test_API_一般帳號不能改設定():
    with tempfile.TemporaryDirectory() as tmp:
        cl, _ = _client(tmp, "alice", "user")
        assert cl.get("/api/v2/capacity/config").status_code == 200
        assert cl.put("/api/v2/capacity/config", json={"params": {"min_days": 5}}).status_code == 403
        api.app.dependency_overrides.clear()


# ---------------------------------------------------------------- verify_numbers 不變式
def test_verify_numbers_容量分析不變式_綠燈且能抓到違規():
    import verify_numbers as vn
    c = mem_conn()
    for d in weekdays(date(2026, 9, 7), 6) + ["2026-09-12"]:
        put_day(c, "TEST-V", d, early_heavy, interval=300)
    vn.FAILS, vn.CHECKS = 0, 0
    vn.check_capacity(c)
    assert vn.FAILS == 0 and vn.CHECKS >= 3
    vn.FAILS, vn.CHECKS = 0, 0


def test_記憶體超標門檻用90_跟NMON月報同一把尺():
    """使用者 11-13 拍板：容量封存／分析的記憶體超標分鐘改用 90（原 80），其他 % 指標仍用 high_pct。"""
    import capacity_analysis as ca
    import nmon_series as ns

    p = {"high_pct": 80}
    assert ca.over_threshold("mem_used", p) == 90.0 == float(ns.THRESHOLDS["mem_used"])
    assert ca.over_threshold("cpu_busy", p) == 80.0
    assert ca.over_threshold("swap_used", {"high_pct": 70}) == 70.0
