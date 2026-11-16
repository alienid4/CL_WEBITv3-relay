"""預設時段＝使用者提供（2026-11-01）：09:00–09:30 開盤時段（重點）、09:30–13:30 盤中；其餘為盤前／盤後；盤後批次不編；大量交易日不要求定義。
這支「不」套用舊的六時段 fixture——測的就是真正的預設值。"""
import gzip
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import capacity_analysis as ca  # noqa: E402
import nmon_series  # noqa: E402


def mem_conn():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
    c.execute(nmon_series.SERIES_SQL)
    return c


def put(c, host, day, itv, fn=lambda m: 10.0):
    step = max(1, itv // 60)
    ts = [f"{m // 60:02d}:{m % 60:02d}:00" for m in range(0, 1440, step)] if itv >= 60 else []
    if itv < 60:
        raise ValueError
    row = {"ts": ts, "cols": {"cpu_busy": [fn(m) for m in range(0, 1440, step)]}, "kind": "raw"}
    c.execute("INSERT INTO nmon_series VALUES (?,?,?,?,?,?)", (host, day, itv, len(ts), gzip.compress(json.dumps(row).encode()), "x"))


def weekdays(start, n):
    d, out = start, []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def test_預設時段是使用者提供的_開盤重點_盤中_不編盤後批次():
    cfg = ca.get_config(mem_conn())
    assert [(s["key"], s["start"], s["end"], bool(s.get("priority"))) for s in cfg["slots"]] == [("open", "09:00", "09:30", True), ("mid", "09:30", "13:30", False)]
    assert "使用者提供" in cfg["slots_source"] and "2026-11-01" in cfg["slots_source"] and cfg["slots_are_example"] is False
    assert any("盤後批次" in u and "尚未提供" in u for u in cfg["unprovided"])           # 盤後批次標待使用者提供，不編
    assert not any("batch" in s["key"] for s in cfg["slots"])
    assert cfg["heavy_days"] == [] and cfg["holidays"] == [] and ca.OTHER_LABEL.startswith("盤前／盤後")


def test_開盤時段每分鐘資料_可評估_且標重點():
    c = mem_conn()
    ca.save_config(c, {"params": {"min_days": 1}})
    ca.save_config(c, {"slots": ca.DEFAULT_SLOTS})
    for d in weekdays(date(2026, 9, 7), 3):
        put(c, "H", d, 60, lambda m: 95.0 if 9 * 60 <= m < 9 * 60 + 10 else 10.0)       # 開盤頭 10 分鐘的尖峰
    h = ca.analyze_host(c, "H", ca.get_config(c))
    o = [x for x in h["cells"] if x["slot"] == "open" and x["metric"] == "cpu_busy"][0]
    assert o["priority"] is True and o["status"] == "ok" and o["max"] == 95.0 and o["samples"] == 90
    mid = [x for x in h["cells"] if x["slot"] == "mid" and x["metric"] == "cpu_busy"][0]
    assert mid["samples"] == 3 * 240 and mid["priority"] is False
    assert not any(l["code"] == "open_coarse" for l in h["limits"])


@pytest.mark.parametrize("itv,coarse", [(60, False), (300, False), (600, False), (900, True), (3600, True)])
def test_粒度粗於約10分鐘_開盤格顯示間隔太粗無法評估開盤尖峰(itv, coarse):
    c = mem_conn()
    for d in weekdays(date(2026, 9, 7), 3):
        put(c, "G", d, itv)
    h = ca.analyze_host(c, "G", ca.get_config(c))
    o = [x for x in h["cells"] if x["slot"] == "open" and x["metric"] == "cpu_busy"][0]
    assert (o["status"] == "coarse_interval") is coarse
    lim = [l for l in h["limits"] if l["code"] == "open_coarse"]
    assert bool(lim) is coarse and (not coarse or "無法評估開盤尖峰" in lim[0]["text"])
    assert o["avg"] is None if coarse else True                                       # 太粗時不給被稀釋的平均
