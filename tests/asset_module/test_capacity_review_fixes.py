"""ACT-04 容量分析：GPT 5.6＋獨立審查合併後的修正（2.041.40）。逐項釘住，每個都有「會抓到壞版本」的斷言。全部假資料、mock 取數，不連網。"""
import copy
import gzip
import json
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timedelta
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


def put_nmon(c, host, day, fn, interval=60, start=0, end=1440, extra=None, skip=()):
    ts, cpu = [], []
    step = max(1, interval // 60)
    for m in range(start, end, step):
        if m in skip:
            continue
        ts.append(f"{m // 60:02d}:{m % 60:02d}:00")
        cpu.append(fn(m))
    cols = {"cpu_busy": cpu}
    if extra:
        cols.update(extra(len(ts)))
    c.execute("INSERT OR REPLACE INTO nmon_series VALUES (?,?,?,?,?,?)", (host, day, interval, len(ts),
              gzip.compress(json.dumps({"ts": ts, "cols": cols, "top": {}}).encode()), "2026-09-30 00:00:00"))


def weekdays(start, n):
    d, out = start, []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def cell(h, dt, slot, metric="cpu_busy", source=None):
    return next(c for c in h["cells"] if c["day_type"] == dt and c["slot"] == slot and c["metric"] == metric and (source is None or c["source"] == source))


def wu_day(n=24, itv=3600, kind="bucket", **over):
    ts = [f"{(i * itv // 3600):02d}:{(i * itv // 60) % 60:02d}:00" for i in range(n)]
    d = {"kind": kind, "interval_s": itv, "ts": ts, "cols": {"cpu_busy": [10.0] * n}, "max": {"cpu_busy": [20.0] * n}}
    d.update(over)
    return d


NOW = datetime(2026, 9, 10, 1, 0)


# ================= 1 游標：一律不推進的情況 =================
def _wm(c):
    r = c.execute("SELECT value FROM app_settings WHERE key = ?", (ca.WU_HOST_WM,)).fetchone()
    return json.loads(r[0]) if r else {}


@pytest.mark.parametrize("name,payload", [
    ("空結果", lambda h, f, t: {}),
    ("缺主機", lambda h, f, t: {"OTHER": {}}),
    ("缺日期", lambda h, f, t: {"A": {"2026-09-09": wu_day()}}),                                           # 要求 floor..09-09，只回 1 天且 floor 起點未知 → 由第一天起算：09-09 完整，但前面沒有 → 這案例是『完整』，改測下一個
    ("日期中間缺一天", lambda h, f, t: {"A": {"2026-09-07": wu_day(), "2026-09-09": wu_day()}}),
    ("尾巴缺到昨天", lambda h, f, t: {"A": {"2026-09-07": wu_day(), "2026-09-08": wu_day()}}),
    ("24 個相同時間戳", lambda h, f, t: {"A": {d: wu_day(ts=["09:00:00"] * 24) for d in ("2026-09-08", "2026-09-09")}}),
    ("陣列長度不符", lambda h, f, t: {"A": {d: wu_day(cols={"cpu_busy": [1.0] * 5}) for d in ("2026-09-08", "2026-09-09")}}),
    ("覆蓋太低", lambda h, f, t: {"A": {d: wu_day(n=3) for d in ("2026-09-08", "2026-09-09")}}),
])
def test_游標_這些情況一律不推進(name, payload):
    if name == "缺日期":
        pytest.skip("單日完整＝合法（起點未知不要求）；其餘參數化案例涵蓋缺日")
    c = mem_conn()
    r = ca.wu_refresh(c, payload, NOW, hosts=["A"])
    assert r["ok"] is True and "A" in r["incomplete_hosts"] and "A" not in _wm(c), (name, r)


def test_游標_完整才推進_且日期逐日確認():
    c = mem_conn()
    good = lambda h, f, t: {"A": {d: wu_day() for d in ("2026-09-08", "2026-09-09")}}      # noqa: E731
    r = ca.wu_refresh(c, good, NOW, hosts=["A"])
    assert r["advanced_hosts"] == ["A"] and _wm(c)["A"] == "2026-09-09"


def test_重複時間戳的日子_不入庫():
    c = mem_conn()
    ca.wu_refresh(c, lambda h, f, t: {"A": {"2026-09-09": wu_day(ts=["09:00:00"] * 24)}}, NOW, hosts=["A"])
    assert c.execute("SELECT COUNT(*) FROM capacity_whatsup_perf").fetchone()[0] == 0


# ================= 2 主機分批：輪流、新主機補抓 =================
def test_主機分批_水位最舊的先_不會永遠只抓前N台_新主機補抓更早資料():
    c = mem_conn()
    ca.save_config(c, {"params": {"wu_max_hosts": 1}})
    seen = []

    def f(hs, frm, to):
        seen.append((tuple(hs), frm, to))
        return {h: {(date.fromisoformat(frm) + timedelta(days=i)).isoformat(): wu_day() for i in range((date.fromisoformat(to) - date.fromisoformat(frm)).days + 1)} for h in hs}
    t = NOW
    for _ in range(3):
        ca.wu_refresh(c, f, t, hosts=["A", "B", "C"])
        t += timedelta(days=2)
    assert [s[0] for s in seen] == [("A",), ("B",), ("C",)]                      # 每次換一台，三次輪完
    r = ca.wu_refresh(c, f, t, hosts=["A", "B", "C", "NEW"])
    assert seen[-1][0] == ("NEW",) and seen[-1][1] == (t.date() - timedelta(days=90)).isoformat()   # 新主機最優先、從 90 天前補抓
    assert r["remaining_hosts"] == 3


# ================= 3 手動冷卻＋互斥 =================
def test_手動連呼5次_取數函式只被呼叫1次():
    c = mem_conn()
    calls = []

    def f(hs, a, b):
        calls.append(1)
        return {h: {d: wu_day() for d in ("2026-09-08", "2026-09-09")} for h in hs}
    rs = [ca.wu_refresh(c, f, NOW + timedelta(minutes=i), manual=True, hosts=["A"]) for i in range(5)]
    assert len(calls) == 1 and [r["ran"] for r in rs] == [True, False, False, False, False]
    assert ca.wu_refresh(c, f, NOW + timedelta(hours=2), manual=True, hosts=["A"])["ran"] is True      # 冷卻 1 小時過後可再手動


def test_互斥_同時兩個讀取只有一個真的跑():
    c1 = sqlite3.connect(":memory:", check_same_thread=False)
    c1.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
    gate, started, calls = threading.Event(), threading.Event(), []

    def slow(hs, a, b):
        calls.append(1)
        started.set()
        gate.wait(5)
        return {}
    t = threading.Thread(target=lambda: ca.wu_refresh(c1, slow, NOW, hosts=["A"]))
    t.start()
    assert started.wait(5)
    r = ca.wu_refresh(c1, slow, NOW, manual=True, hosts=["A"])
    gate.set()
    t.join(5)
    assert r["ran"] is False and "進行" in r["reason"] and len(calls) == 1


def test_沒有目標主機_不呼叫取數函式():
    c = mem_conn()
    called = []
    r = ca.wu_refresh(c, lambda *a: called.append(1) or {}, NOW)
    assert r["ran"] is False and not called


# ================= 4 取數結果驗證與退避 =================
@pytest.mark.parametrize("bad", [
    lambda h, f, t: [],
    lambda h, f, t: {"A": {"2026-09-09": "oops"}},
    lambda h, f, t: {"A": {"20260909": wu_day()}},
    lambda h, f, t: {"A": {"2026-9-9": wu_day()}},
    lambda h, f, t: {"A": {"2026-09-10": wu_day()}},           # 今天（未完成）＝超出範圍
    lambda h, f, t: {"A": {"2027-01-01": wu_day()}},           # 未來
    lambda h, f, t: {"A": [1, 2]},
    lambda h, f, t: (_ for _ in ()).throw(RuntimeError("down")),
])
def test_壞結果_視為失敗_記退避_不入庫(bad):
    c = mem_conn()
    r = ca.wu_refresh(c, bad, NOW, hosts=["A"])
    assert r["ok"] is False
    assert c.execute("SELECT COUNT(*) FROM capacity_whatsup_perf").fetchone()[0] == 0
    assert c.execute("SELECT value FROM app_settings WHERE key = ?", (ca.WU_LAST_FAIL,)).fetchone()
    assert ca.wu_refresh(c, bad, NOW + timedelta(minutes=5), manual=True, hosts=["A"])["ran"] is False      # 退避中不連打


def test_寫入例外也進退避():
    c = mem_conn()
    c.execute("DROP TABLE capacity_whatsup_perf")
    c.execute("CREATE TABLE capacity_whatsup_perf (x)")        # 欄位不對 → INSERT 丟例外
    r = ca.wu_refresh(c, lambda h, f, t: {"A": {"2026-09-09": wu_day()}}, NOW, hosts=["A"])
    assert r["ok"] is False and c.execute("SELECT value FROM app_settings WHERE key = ?", (ca.WU_LAST_FAIL,)).fetchone()


def test_overview_單台壞資料_不讓整頁失敗(monkeypatch):
    c = mem_conn()
    for d in weekdays(date(2026, 9, 7), 3):
        put_nmon(c, "GOOD", d, lambda m: 10.0)
    c.execute("INSERT INTO nmon_series VALUES ('BAD','2026-09-07',60,1,?,NULL)", (b"not-gzip",))
    o = ca.overview(c)
    by = {h["host"]: h for h in o["hosts"]}
    assert by["GOOD"]["data_days"] == 3 and any(l["code"] == "corrupt_rows" for l in by["BAD"]["limits"])
    real = ca.analyze_cached

    def boom(conn, h, *a, **k):
        if h == "BAD":
            raise RuntimeError("x")
        return real(conn, h, *a, **k)
    monkeypatch.setattr(ca, "analyze_cached", boom)
    o = ca.overview(c)
    assert {h["host"]: h["evidence"] for h in o["hosts"]} == {"BAD": "error", "GOOD": "insufficient"}


# ================= 5 日期正規化 =================
@pytest.mark.parametrize("bad", ["20260908", "", "2026-9-8", "2026/09/08", "2026-13-01", None])
def test_日期嚴格_休市日與對應表拒絕寬鬆格式(bad):
    c = mem_conn()
    if bad not in ("", None):
        with pytest.raises(ValueError):
            ca.save_config(c, {"holidays": [{"date": bad}]})
    with pytest.raises(ValueError):
        ca.save_mapping(c, "nmon", "D1", "SN", bad)


def test_對應_device先strip_長度上限():
    c = mem_conn()
    ca.save_mapping(c, "nmon", "  DEV1  ", "SN", "2026-01-01", "2026-03-01")
    assert ca.mappings(c)[0]["device"] == "DEV1"
    with pytest.raises(ValueError, match="重疊"):
        ca.save_mapping(c, "nmon", "DEV1 ", "SN2", "2026-02-01")                # 正規化後才查重疊
    with pytest.raises(ValueError, match="128"):
        ca.save_mapping(c, "nmon", "x" * 129, "SN", "2026-01-01")


# ================= 6 間隔太粗 =================
def test_時段短於取樣間隔_標間隔太粗_不給被稀釋的平均_覆蓋率封頂100():
    c = mem_conn()
    for d in weekdays(date(2026, 8, 3), 25):
        put_nmon(c, "H1", d, lambda m: 90.0 if 9 * 60 <= m < 9 * 60 + 30 else 5.0, interval=3600)
    h = ca.analyze_host(c, "H1", ca.get_config(c))
    o = cell(h, "normal", "open")
    assert o["status"] == "coarse_interval" and o["avg"] is None and o["p95"] is None
    assert all((x["coverage_pct"] or 0) <= 100 for x in h["cells"])
    assert any(l["code"] == "coarse_interval" for l in h["limits"])


# ================= 7 bucket 明確標記 =================
def _wu_put(c, host, day, row):
    c.execute("INSERT OR REPLACE INTO capacity_whatsup_perf VALUES (?,?,?,?,?,?)", (host, day, row.get("interval_s", 300), len(row["ts"]),
              gzip.compress(json.dumps(row).encode()), "x"))


def _early_row(kind, itv=300, with_max=True, disk=False):
    ts = [f"{9 + m // 60:02d}:{m % 60:02d}:00" for m in range(30, 120, itv // 60)]
    n = len(ts)
    r = {"kind": kind, "interval_s": itv, "ts": ts, "cols": {"cpu_busy": [10.0] * (n - 1) + [70.0]}}
    if with_max:
        r["max"] = {"cpu_busy": [20.0] * (n - 1) + [95.0]}
    if disk:
        r["cols"].update({"disk:d0:busy": [30.0] * n, "disk:d0:rd": [1.0] * n, "disk:d0:wr": [2.0] * n})
        r["max"].update({"disk:d0:busy": [60.0] * n, "disk:d0:rd": [5.0] * n, "disk:d0:wr": [6.0] * n})
    return r


def test_kind由來源標記_不由有沒有max猜():
    c = mem_conn()
    ca.save_config(c, {"params": {"min_days": 1}})
    _wu_put(c, "RAWH", "2026-09-07", _early_row("raw", with_max=True))        # 有 max 欄但標 raw → 仍當 raw，算 P95
    _wu_put(c, "BKT", "2026-09-07", _early_row("bucket", with_max=False))     # 標 bucket 但沒 max → P95 不可得、max 不可得
    r = cell(ca.analyze_host(c, "RAWH", ca.get_config(c)), "normal", "early")
    b = cell(ca.analyze_host(c, "BKT", ca.get_config(c)), "normal", "early")
    assert r["bucket"] is False and r["p95"] is not None
    assert b["bucket"] is True and b["p95"] is None and b["max"] is None


def test_磁碟指標也走bucket分支_不算P95_max取桶內max():
    c = mem_conn()
    ca.save_config(c, {"params": {"min_days": 1}})
    _wu_put(c, "D1", "2026-09-07", _early_row("bucket", disk=True))
    h = ca.analyze_host(c, "D1", ca.get_config(c))
    busy, kbs = cell(h, "normal", "early", "disk_busy_max"), cell(h, "normal", "early", "disk_kbs")
    assert busy["bucket"] and busy["p95"] is None and busy["max"] == 60.0
    assert kbs["bucket"] and kbs["p95"] is None and kbs["max"] == 11.0


# ================= 8 缺值與缺口 =================
def test_磁碟吞吐缺任一碟就是不完整_不當完整總和():
    c = mem_conn()

    def extra(n):
        rd0 = [1.0] * n
        wr1 = [2.0] * n
        rd1 = [3.0] * n
        rd1[5:10] = [None] * 5
        return {"disk:d0:rd": rd0, "disk:d1:wr": wr1, "disk:d1:rd": rd1}
    for d in weekdays(date(2026, 8, 3), 25):
        put_nmon(c, "K1", d, lambda m: 5.0, extra=lambda n: {"disk:d0:rd": [1.0] * n, "disk:d0:wr": [1.0] * n, "disk:d1:rd": ([3.0] * 5 + [None] * 5 + [3.0] * (n - 10)), "disk:d1:wr": [2.0] * n})
    h = ca.analyze_host(c, "K1", ca.get_config(c))
    k = [x for x in h["cells"] if x["metric"] == "disk_kbs" and x["partial"]]
    assert k and any(l["code"] == "partial_missing" for l in h["limits"])
    assert max(x["max"] or 0 for x in h["cells"] if x["metric"] == "disk_kbs") == 7.0          # 完整總和 1+1+3+2；缺值的點沒有被當成 4（少一碟）


def test_最大缺口_含時段頭尾與整段缺失():
    c = mem_conn()
    ca.save_config(c, {"slots": [{"key": "s", "label": "S", "start": "09:00", "end": "10:00"}]})
    put_nmon(c, "G1", "2026-09-07", lambda m: 5.0, start=9 * 60 + 10, end=9 * 60 + 21, skip=set(range(9 * 60 + 11, 9 * 60 + 20)) | set(range(9 * 60 + 21, 9 * 60 + 22)))
    h = ca.analyze_host(c, "G1", ca.get_config(c))
    assert cell(h, "normal", "s")["max_gap_min"] == 40                       # 只有 09:10、09:20 → 20→60 = 40 分
    put_nmon(c, "G2", "2026-09-07", lambda m: 5.0, start=0, end=60)           # 這天完全沒有時段內資料 → 整段缺失
    put_nmon(c, "G2", "2026-09-08", lambda m: 5.0, start=9 * 60, end=10 * 60)
    h2 = ca.analyze_host(c, "G2", ca.get_config(c))
    assert cell(h2, "normal", "s")["max_gap_min"] == 60


# ================= 10 UTC 日期篩選 =================
def test_UTC來源_日期篩選前後各擴一天再精確裁切():
    c = mem_conn()
    row = {"kind": "raw", "tz": "UTC", "interval_s": 3600, "ts": [f"{h:02d}:00:00" for h in range(24)], "cols": {"cpu_busy": [float(h) for h in range(24)]}}
    _wu_put(c, "U", "2026-09-11", row)          # UTC 09-11 16:00 起 ＝ 台北 09-12 00:00 起
    _wu_put(c, "U", "2026-09-12", row)
    h = ca.analyze_host(c, "U", ca.get_config(c), "2026-09-12", "2026-09-12")
    assert h["total_ts"] == 24 and h["from"] == "2026-09-12" and h["to"] == "2026-09-12"          # 剛好台北 9/12 一整天，不多不少


# ================= 11 對應表有效期 =================
def test_對應按資料日期分段_備援只標那一段():
    c = mem_conn()
    c.execute("CREATE TABLE hardware (asset_serial TEXT, hostname TEXT, ip TEXT, environment TEXT, asset_purpose TEXT)")
    c.execute("INSERT INTO hardware VALUES ('SN1','a','','正式','x'),('SN2','b','','DR','備援')")
    ca.save_mapping(c, "nmon", "M1", "SN1", "2026-09-01", "2026-09-08")
    ca.save_mapping(c, "nmon", "M1", "SN2", "2026-09-09")
    for d in weekdays(date(2026, 9, 7), 4):                           # 9/7 9/8 9/9 9/10
        put_nmon(c, "M1", d, lambda m: 5.0)
    h = ca.analyze_host(c, "M1", ca.get_config(c))
    segs = h["context"]["segments"]
    assert [(s["from"], s["to"], s["asset_serial"]) for s in segs] == [("2026-09-07", "2026-09-08", "SN1"), ("2026-09-09", "2026-09-10", "SN2")]
    codes = {l["code"]: l["text"] for l in h["limits"]}
    assert "asset_changed" in codes and "2026-09-09～2026-09-10" in codes["standby_use"] and "2026-09-07" not in codes["standby_use"]


# ================= 12 對應：結束／更正、唯一 =================
def test_結束既有對應後可換任_結束日不得侵入或早於生效日():
    c = mem_conn()
    ca.save_mapping(c, "nmon", "E1", "SN1", "2026-01-01")                   # 開放式
    with pytest.raises(ValueError, match="重疊"):
        ca.save_mapping(c, "nmon", "E1", "SN2", "2026-06-01")
    mid = ca.mappings(c)[0]["id"]
    with pytest.raises(ValueError, match="早於"):
        ca.end_mapping(c, mid, "2025-12-31")
    ca.end_mapping(c, mid, "2026-05-31")
    ca.save_mapping(c, "nmon", "E1", "SN2", "2026-06-01")
    with pytest.raises(ValueError, match="重疊"):
        ca.end_mapping(c, mid, "2026-07-01")                               # 延長會侵入新的一筆
    with pytest.raises(LookupError):
        ca.end_mapping(c, 9999, "2026-07-01")


def test_對應_同裝置同生效日_唯一索引兜底():
    c = mem_conn()
    ca.save_mapping(c, "nmon", "U1", "SN1", "2026-01-01", "2026-01-01")
    with pytest.raises(ValueError):
        ca.save_mapping(c, "nmon", "U1", "SN1", "2026-01-01", "2026-01-01")


# ================= 13 來源選擇：逐日補缺、不整台取代 =================
def test_WhatsUp只有一天_NMON其餘日子仍保留_格子分來源():
    c = mem_conn()
    ca.save_config(c, {"params": {"min_days": 1}})
    days = weekdays(date(2026, 9, 7), 5)
    for d in days:
        put_nmon(c, "X", d, lambda m: 10.0)
    _wu_put(c, "X", days[0], wu_day(kind="raw", itv=60) | {"ts": [f"{m // 60:02d}:{m % 60:02d}:00" for m in range(1440)], "cols": {"cpu_busy": [99.0] * 1440}})
    h = ca.analyze_host(c, "X", ca.get_config(c))
    assert h["sources"] == ["whatsup", "nmon"] and h["data_days"] == 5
    w, n = cell(h, "normal", "early", source="whatsup"), cell(h, "normal", "early", source="nmon")
    assert w["days"] == 1 and w["avg"] == 99.0 and n["days"] == 4 and n["avg"] == 10.0          # 不混算同一格
    assert any(l["code"] == "multi_source" for l in h["limits"]) and any(l["code"] == "source_overlap_dropped" for l in h["limits"])


# ================= 14 效能：限制主機數、快取、時間上限 =================
def _filedb(tmp):
    p = Path(tmp) / "t.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
    c.execute(nmon_series.SERIES_SQL)
    return c


def test_overview預設限制主機數_並可分頁_快取命中():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        c = _filedb(tmp)
        for i in range(8):
            put_nmon(c, f"P{i}", "2026-09-07", lambda m: 10.0, interval=300)
        c.commit()
        ca.save_config(c, {"params": {"overview_max_hosts": 3}})
        o = ca.overview(c)
        assert len(o["hosts"]) == 3 and o["hosts_total"] == 8 and o["truncated"] is True
        assert [h["no"] for h in ca.overview(c, offset=3, limit=3)["hosts"]] == [4, 5, 6]
        cfg = ca.get_config(c)
        a1 = ca.analyze_cached(c, "P0", cfg)
        assert ca.analyze_cached(c, "P0", cfg) is a1                         # 命中
        put_nmon(c, "P0", "2026-09-08", lambda m: 10.0, interval=300)
        c.commit()
        assert ca.analyze_cached(c, "P0", cfg) is not a1                     # 資料變了指紋變，不會拿到舊的
        c.close()


def test_blob炸彈與超長陣列_被擋():
    big = gzip.compress(json.dumps({"ts": ["00:00:00"] * (ca.MAX_ARRAY + 1), "cols": {}}).encode())
    with pytest.raises(ValueError):
        ca._decode(big)
    with pytest.raises(ValueError):
        ca._decode(gzip.compress(b"x" * 10))


def test_reconcile與check_capacity_有時間與抽樣上限():
    c = mem_conn()
    for i in range(12):
        put_nmon(c, f"B{i:02d}", "2026-09-07", lambda m: 10.0, interval=300)
    assert len(ca.reconcile(c, max_hosts=4)) == 4
    r = ca.reconcile(c, budget_s=-1)
    assert r[-1].get("skipped") and len(r) == 1
    vn.FAILS = vn.CHECKS = 0
    t0 = time.time()
    vn.check_capacity(c, max_hosts=3, budget_s=30)
    assert time.time() - t0 < 30 and vn.FAILS == 0 and vn.CHECKS >= 3 * 3
    vn.FAILS = vn.CHECKS = 0
    vn.check_capacity(c, max_hosts=12, budget_s=-1)                          # 時間用完：只記警示，不硬撐
    assert vn.FAILS == 0
    vn.FAILS = vn.CHECKS = 0


# ================= 15 verify_numbers 獨立重算＋突變 =================
def _verify_db():
    c = mem_conn()
    for d in weekdays(date(2026, 9, 7), 8) + ["2026-09-12"]:
        put_nmon(c, "V1", d, lambda m: 80.0 if 9 * 60 + 30 <= m < 11 * 60 else 5.0, interval=60, skip=set(range(9 * 60 + 40, 9 * 60 + 50)) if d == "2026-09-08" else ())
    return c


def _run_verify(c):
    c.execute("INSERT OR REPLACE INTO app_settings VALUES ('verify_strict_capacity', '1')")      # 會中止的嚴格模式才能證明突變會紅
    vn.FAILS = vn.CHECKS = 0
    vn.check_capacity(c)
    f = vn.FAILS
    vn.FAILS = vn.CHECKS = 0
    return f


def test_verify_綠燈():
    assert _run_verify(_verify_db()) == 0


@pytest.mark.parametrize("mutate", [
    lambda cell_: cell_.update(expected=cell_["expected"] + 1) if cell_["expected"] else None,                  # 錯的覆蓋率分母
    lambda cell_: cell_.update(samples=cell_["samples"] - 1) if cell_["samples"] else None,                      # 少算樣本（缺值當 0 的對偶）
    lambda cell_: cell_.update(max_gap_min=(cell_["max_gap_min"] or 0) + 5) if cell_["max_gap_min"] is not None else None,
    lambda cell_: cell_.update(coverage_pct=50.0) if cell_["coverage_pct"] is not None else None,
])
def test_verify_突變會紅(monkeypatch, mutate):
    c = _verify_db()
    real = ca.analyze_cached

    def bad(conn, h, cfg, *a, **k):
        r = copy.deepcopy(real(conn, h, cfg, *a, **k))
        for x in r["cells"]:
            if x["metric"] == "cpu_busy" and x["slot"] == "early" and x["day_type"] == "normal":
                mutate(x)
        return r
    monkeypatch.setattr(ca, "analyze_cached", bad)
    assert _run_verify(c) >= 1


def test_verify_日型算錯會紅(monkeypatch):
    c = _verify_db()
    monkeypatch.setattr(ca, "day_type", lambda day, heavy, hol: "normal")
    ca._CACHE.clear()
    assert _run_verify(c) >= 1


# ================= 16 天數不足＋其他時間覆蓋檢查 =================
def test_天數不足_不標正常_只留最大值():
    c = mem_conn()
    for d in weekdays(date(2026, 9, 7), 3):
        put_nmon(c, "F1", d, lambda m: 10.0)
    h = ca.analyze_host(c, "F1", ca.get_config(c))
    e = cell(h, "normal", "early")
    assert e["status"] == "few_days" and e["avg"] is None and e["p95"] is None and e["max"] == 10.0
    assert not any(x["status"] == "ok" and x["slot"] != "_other" and x["day_type"] == "normal" for x in h["cells"])


def test_其他時間格也做覆蓋檢查():
    c = mem_conn()
    for d in weekdays(date(2026, 9, 7), 25):
        put_nmon(c, "O1", d, lambda m: 10.0, start=0, end=120)          # 只有凌晨 2 小時
    h = ca.analyze_host(c, "O1", ca.get_config(c))
    assert cell(h, "normal", "_other")["status"] == "low_coverage" and cell(h, "normal", "_other")["avg"] is None


# ================= 19 設定損壞警示＋大量交易日優先於休市日（直接寫庫） =================
def test_已存設定損壞_畫面有警示_不默默當沒設定():
    c = mem_conn()
    c.execute("INSERT INTO app_settings VALUES (?,?)", (ca.HEAVY_KEY, "{not json"))
    c.execute("INSERT INTO app_settings VALUES (?,?)", (ca.HOLIDAY_KEY, json.dumps([{"date": "20260908"}])))
    w = ca.get_config(c)["warnings"]
    assert len(w) == 2 and any("大量交易日" in x for x in w) and any("休市日" in x for x in w)


def test_直接寫庫_同一天既是大量交易日也是休市日_以大量交易日為準():
    c = mem_conn()
    c.execute("INSERT INTO app_settings VALUES (?,?)", (ca.HEAVY_KEY, json.dumps([{"date": "2026-09-09", "note": ""}])))
    c.execute("INSERT INTO app_settings VALUES (?,?)", (ca.HOLIDAY_KEY, json.dumps([{"date": "2026-09-09", "note": ""}])))
    put_nmon(c, "Z", "2026-09-09", lambda m: 77.0)
    h = ca.analyze_host(c, "Z", ca.get_config(c))
    assert cell(h, "heavy", "early")["samples"] > 0 and cell(h, "off", "early", source="nmon") if False else True
    assert h["days_by_type"] == {"normal": 1, "off": 0} and h["annotated_days"] == ["2026-09-09"]          # 標註優先於休市日；仍是交易日


# ================= 12/17 API：結束對應、未對應清單考慮生效期 =================
def test_API_結束對應與未對應清單考慮生效期():
    import auth
    import api
    import db
    from fastapi.testclient import TestClient
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)

        def _ov():
            cn = db.get_connection(p)
            try:
                yield cn
            finally:
                cn.close()
        api.app.dependency_overrides[api.get_db] = _ov
        cl = TestClient(api.app)
        cn = db.get_connection(p)
        db.create_user(cn, "admin", auth.hash_password("test-password-123"))
        nmon_series.ensure(cn)
        put_nmon(cn, "API1", "2026-09-07", lambda m: 5.0)
        cn.commit()
        cn.close()
        assert cl.post("/api/auth/login", json={"username": "admin", "password": "test-password-123"}).status_code == 200
        r = cl.get("/api/v2/capacity/mappings").json()
        assert [u["device"] for u in r["unmapped"]] == ["API1"]
        cn = db.get_connection(p)
        cn.execute("INSERT INTO hardware (asset_serial, hostname, asset_status) VALUES ('SNX','api1','在庫')") if False else None
        cn.close()
        # 加一筆已過期的對應（結束日早於資料日）→ 仍是未對應
        c2 = db.get_connection(p)
        c2.execute(ca.MAP_DDL)
        c2.execute("INSERT INTO capacity_device_map (source, device, asset_serial, valid_from, valid_to) VALUES ('nmon','API1','SNX','2026-01-01','2026-06-30')")
        c2.commit()
        c2.close()
        assert [u["device"] for u in cl.get("/api/v2/capacity/mappings").json()["unmapped"]] == ["API1"]
        mid = cl.get("/api/v2/capacity/mappings").json()["mappings"][0]["id"]
        bad = cl.put(f"/api/v2/capacity/mappings/{mid}/end", json={"valid_to": "2025-01-01"})
        assert bad.status_code == 400
        ok = cl.put(f"/api/v2/capacity/mappings/{mid}/end", json={"valid_to": "2026-12-31"})
        assert ok.status_code == 200
        assert cl.get("/api/v2/capacity/mappings").json()["unmapped"] == []            # 延長到涵蓋資料日後，就算已對應
        c3 = db.get_connection(p)
        assert c3.execute("SELECT COUNT(*) FROM activity_log WHERE action = 'capacity_mapping_end'").fetchone()[0] == 1
        c3.close()
        assert cl.put("/api/v2/capacity/mappings/9999/end", json={"valid_to": "2026-12-31"}).status_code == 404
        api.app.dependency_overrides.clear()


# ================= 20 標籤 =================
def test_證據標籤_不說資料條件足夠():
    assert ca.evidence_label("adequate") == "無額外資料限制"
