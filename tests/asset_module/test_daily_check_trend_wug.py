"""1.5 每日檢查 W-f 硬碟（上次輪循）：逐顆硬碟的前次、較前次、每日成長、到第一級／到滿載天數；已列例外的開關與高水位硬底線。
歷史＝既有 wug_lp_batch／wug_lp_value，每天只取「這個群組有硬碟值的最後一個完整批次」，並排除目前採用的那一批。全部假資料（10.99.x）。"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import daily_check as dc  # noqa: E402
import daily_check_trend as tr  # noqa: E402
from test_daily_check_lastpoll import Q_DISK, ROOTP, TPE, env, gen, imp, send, standard, tnow  # noqa: E402,F401

GP = ROOTP + Q_DISK


def hist(c, days_ago, rows, batch=None, complete=1, hh="23:50:00", day=None):
    """rows: [(device_id, item, pct)]；寫一個歷史批次（當天 hh 收的）。day＝直接指定日期（YYYY-MM-DD）。"""
    day = day or (tnow().date() - timedelta(days=days_ago)).isoformat()
    bid = batch or f"wuglp_h{day}_{hh}"
    c.execute("INSERT INTO wug_lp_batch (batch_id, collected_at, imported_at, collector_version, complete, problems_json, groups_json) VALUES (?,?,?,?,?,?,?)",
              (bid, f"{day} {hh}", f"{day} {hh}", "t", complete, "[]", "[]"))
    for d, item, pct in rows:
        c.execute("INSERT INTO wug_lp_value (batch_id, group_path, device_id, ip, name, metric, item, pct, poll_at, age_s) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (bid, GP, d, None, None, "disk", item, pct, f"{day} {hh}", 100))
    c.commit()


def setup(c, drop, disk, when=None, **cfg):
    dc.save_settings(c, {"disk_list": 80, "disk_alert": 90, **cfg})
    send(drop, "wuglp_now", standard(disk=disk), when=when)
    imp(c)


def disks(r):
    return {(d["device_id"], k["item"]): k for d in r["W-f"]["detail"] for k in d.get("disks", [])}


def test_逐顆硬碟_下降中仍第一級_上升看到第一級與滿載兩個天數(env):
    c, drop = env
    setup(c, drop, [("d1", "C:", 93.0, 120), ("d2", "/", 85.0, 120), ("d3", "/", 50.0, 120)])
    hist(c, 2, [("d1", "C:", 97.0), ("d2", "/", 81.0)])
    hist(c, 1, [("d1", "C:", 95.0), ("d2", "/", 83.0)])
    r, _ = gen(c)
    k = disks(r)
    c1 = k[("d1", "C:")]
    assert c1["state"] == "第一級" and (c1["prev"], c1["delta"], c1["direction"]) == (95.0, -2.0, "下降中") and c1["tier"] == 1 and c1["days_to_first"] == 0
    assert c1["trend_note"].startswith("下降中，仍≥90%（7 天最高 97%）")                  # 規格：昨天 95、今天 93 不能當沒事
    s2 = k[("d2", "/")]
    assert s2["state"] == "需處理" and s2["growth"] == 2.0 and s2["samples"] == 3 and s2["days_to_first"] == 3 and s2["days_to_full"] == 8
    assert ("d3", "/") not in k                                                          # 50% 沒達列出門檻
    assert r["W-f"]["verdict"] == "bad"


def test_只有一個歷史點_不估算_前次較前次照給(env):
    c, drop = env
    setup(c, drop, [("d1", "C:", 85.0, 120)])
    hist(c, 1, [("d1", "C:", 77.0)])
    k = disks(gen(c)[0])[("d1", "C:")]
    assert k["prev"] == 77.0 and k["delta"] == 8.0 and k["growth"] is None and k["days_to_first"] is None and "樣本不足，不估算" in k["trend_note"]


def test_每天只取這個群組有硬碟值的最後完整批次_缺群組_不完整_同時間用batch_id(env):
    c, drop = env
    setup(c, drop, [("d1", "C:", 92.0, 120)])
    hist(c, 1, [("d1", "C:", 60.0)], hh="08:00:00")                                         # 同一天早一點的批次，被晚的蓋掉
    hist(c, 1, [("d1", "C:", 90.0)], hh="23:30:00", batch="wuglp_a")
    hist(c, 1, [("d1", "C:", 10.0)], hh="23:59:00", complete=0)                            # 不完整批次不採用
    hist(c, 1, [], hh="23:55:00")                                                          # 當天最後一個完整批次沒有這個群組的硬碟值 → 跳過它
    r, _ = gen(c)
    assert disks(r)[("d1", "C:")]["prev"] == 90.0
    day = (tnow().date() - timedelta(days=1)).isoformat()
    hist(c, 1, [("d1", "C:", 70.0)], hh="23:30:00", batch="wuglp_b")                       # 跟 wuglp_a 同一個收集時間：batch_id 大的贏，結果穩定
    hist(c, 1, [("d1", "C:", 50.0)], hh="23:30:00", batch="wuglp_0")                       # 較小的 batch_id 雖然最後才寫入，也不能贏
    h = tr.disk_history(c, GP, tnow().date())[("d1", "C:")]
    assert [x["day"] for x in h] == [day] and h[0]["pct"] == 70.0


def test_目前用的是昨日批次_歷史不含這一批自己_前次是真正的前一天(env):
    c, drop = env
    when = datetime(2026, 11, 12, 23, 50, tzinfo=TPE)                                      # 批次收於 11-12 23:50；現在 11-13 00:20，今天的新批還沒進來
    setup(c, drop, [("d1", "C:", 93.0, 120)], when=when)
    c.execute("UPDATE wug_lp_batch SET collected_at = '2026-11-12 23:50:00' WHERE batch_id = 'wuglp_now'")
    hist(c, 0, [("d1", "C:", 90.0)], day="2026-11-11")
    r, _ = gen(c, now=datetime(2026, 11, 13, 0, 20))
    k = disks(r)[("d1", "C:")]
    assert k["prev"] == 90.0 and k["prev_day"] == "2026-11-11" and k["delta"] == 3.0       # 不是 93（自己）、不是 delta 0
    assert tr.disk_history(c, GP, datetime(2026, 11, 12).date(), "wuglp_now")[("d1", "C:")] == [{"day": "2026-11-11", "pct": 90.0, "total": None}]
    assert all(x["day"] != "2026-11-12" for x in tr.disk_history(c, GP, datetime(2026, 11, 13).date())[("d1", "C:")]) is False   # 沒排除批次時，昨日批次自己會被讀到（證明排除參數有作用）


def test_沒有歷史_待累積_不是0_還是依今天的值判第一級(env):
    c, drop = env
    setup(c, drop, [("d1", "C:", 91.0, 120)])
    r, _ = gen(c)
    k = disks(r)[("d1", "C:")]
    assert k["prev"] is None and k["delta"] is None and k["growth"] is None and k["trend_note"].startswith("已在第一級") and "待累積" in k["trend_note"] and k["state"] == "第一級"
    assert r["W-f"]["verdict"] == "bad"


def test_硬碟邊界_89_9需處理_90第一級_80需處理_79_9不列(env):
    c, drop = env
    setup(c, drop, [("d1", "C:", 89.9, 120), ("d1", "D:", 90.0, 120), ("d2", "/", 80.0, 120), ("d2", "/data", 79.9, 120)])
    k = disks(gen(c)[0])
    assert k[("d1", "C:")]["state"] == "需處理" and k[("d1", "D:")]["state"] == "第一級" and k[("d2", "/")]["state"] == "需處理"
    assert ("d2", "/data") not in k


def test_跨主機排序_到第一級天數少的在前_99度下降項不被80度項擠到後面(env):
    c, drop = env
    setup(c, drop, [("d1", "C:", 85.0, 120), ("d2", "/", 88.0, 120), ("d3", "/", 99.0, 120)])
    hist(c, 2, [("d1", "C:", 75.0), ("d2", "/", 87.0), ("d3", "/", 99.5)])
    hist(c, 1, [("d1", "C:", 80.0), ("d2", "/", 87.5), ("d3", "/", 99.4)])
    r, _ = gen(c)
    k = disks(r)
    assert k[("d1", "C:")]["days_to_first"] == 1 and k[("d2", "/")]["days_to_first"] > 1 and k[("d3", "/")]["tier"] == 1
    order = sorted(k.items(), key=lambda kv: tr.sort_key(kv[1]))
    assert [key[0] for key, _v in order] == ["d3", "d1", "d2"]               # 已在第一級的 99% 在最前面，其次到第一級 1 天的


def test_例外開關預設關_單台例外照舊不提醒_打開後達第一級門檻回第一級(env):
    c, drop = env
    exp = (tnow().date() + timedelta(days=30)).isoformat()
    setup(c, drop, [("d1", "C:", 93.0, 120)], disk_exceptions=[{"ip": "10.99.1.1", "threshold": 97, "reason": "測試", "expires": exp}])
    r, _ = gen(c)
    assert r["W-f"]["stats"]["bad"] == 0 and disks(r)[("d1", "C:")]["state"] == "需處理"            # 預設：單台例外門檻 97，93 不提醒
    dc.save_settings(c, {"exempt_force_first_level": True})
    r, _ = gen(c)
    assert r["W-f"]["stats"]["bad"] == 1 and disks(r)[("d1", "C:")]["state"] == "第一級"            # 開了：達 90% 一律第一級
    dc.save_settings(c, {"exempt_force_first_level": True, "disk_alert": 95})
    r, _ = gen(c)
    assert r["W-f"]["stats"]["bad"] == 0                                                         # 門檻是 disk_alert，不是寫死 90


def _exempt(c, base_pct=89.0):
    import capacity_exempt as ce
    ce.ensure(c)
    key = ce.disk_key(ce.mk_of_ip(c, "10.99.1.1"), "C:")
    assert key, "測試前提：10.99.1.1 要唯一對到資產"
    ce.set_exempt(c, {"kind": "disk", "key": key, "label": "app01 C:", "used_pct": base_pct, "source": "whatsup_lastpoll"}, "測試例外", 30, "tester")
    c.commit()


def test_已知例外_預設不提醒_打開開關達第一級門檻回第一級(env):
    c, drop = env
    setup(c, drop, [("d1", "C:", 93.0, 120)])
    _exempt(c, 93.0)
    r, _ = gen(c)
    assert r["W-f"]["stats"]["bad"] == 0 and r["W-f"]["stats"]["exempt_disks"] == 1 and disks(r)[("d1", "C:")]["state"] == "已知例外"
    dc.save_settings(c, {"exempt_force_first_level": True})
    r, _ = gen(c)
    assert r["W-f"]["stats"]["bad"] == 1 and disks(r)[("d1", "C:")]["state"] == "第一級" and "例外不適用於高水位" in str(r["W-f"]["detail"])


def test_例外不能吞掉高水位_95以上或已滿一律失效_不等連續兩次(env):
    c, drop = env
    setup(c, drop, [("d1", "C:", 99.0, 120)])                                                  # 基準 89%，第一次就暴漲到 99%
    _exempt(c, 89.0)
    r, _ = gen(c)
    assert r["W-f"]["stats"]["bad"] == 1 and r["W-f"]["stats"]["exempt_disks"] == 0 and disks(r)[("d1", "C:")]["state"] == "第一級"
    assert "例外不適用於高水位（≥ 95%）" in str(r["W-f"]["detail"])
    c.execute("DELETE FROM wug_lp_value")
    c.execute("DELETE FROM wug_lp_batch")
    c.commit()
    send(drop, "wuglp_b2", standard(disk=[("d1", "C:", 94.9, 120)]))                          # 94.9% 還在硬底線以下：例外照舊有效
    imp(c)
    r, _ = gen(c)
    assert r["W-f"]["stats"]["bad"] == 0 and disks(r)[("d1", "C:")]["state"] == "已知例外"


def test_沒有歷史表_不炸(env):
    c, drop = env
    setup(c, drop, [("d1", "C:", 93.0, 120)])
    assert tr.disk_history(c, GP, tnow().date()) == {}
    c.execute("DROP TABLE wug_lp_value")
    c.commit()
    assert tr.disk_history(c, GP, tnow().date()) == {}
