"""1.5 每日檢查：容量成長與估計滿載（2.070.x，規格＝DailyCheck v2.4.6，GPT 審查後版本）。

涵蓋：analyze 純函式（前次、較前次、上升／下降只看 delta、每日成長至少 3 個日期、擴縮容與容量不可比不跨過、無歷史、壞值、同日去重、40 天外、
到第一級與到滿載兩個天數、排序以容量優先）、Pool 整合（storage_pool_daily 當歷史；≥90% 一律第一級且不跟 disk_alert 漂移；
0 筆或個數不符＝沒收到；昨日批次不跟自己比；讀不到使用率＝未收集）、設定預設值與驗證。全部假資料：10.99.x、假序號。
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import daily_check as dc  # noqa: E402
import daily_check_trend as tr  # noqa: E402
from test_daily_check import conn  # noqa: E402,F401
from test_daily_check_storage import MH, NOW, configure, load, srow, v2  # noqa: E402,F401
from test_relay_unity import GB  # noqa: E402

T = date(2026, 11, 20)


def pt(days_ago, pct, total=100):
    return {"day": (T - timedelta(days=days_ago)).isoformat(), "pct": pct, "total": total}


# ---------------------------------------------------------------- analyze
def test_上升_前次_較前次_成長_兩個天數都給():
    t = tr.analyze(84.0, 100, [pt(3, 76.0), pt(2, 80.0), pt(1, 82.0)], T)
    assert (t["prev"], t["prev_day"], t["delta"], t["direction"], t["samples"]) == (82.0, "2026-11-19", 2.0, "上升中", 4)
    assert t["growth"] == 2.67 and t["days_to_first"] == 3 and t["days_to_full"] == 6          # (90-84)/2.67→3、(100-84)/2.67→6
    assert "約 3 天到第一級、6 天滿載" in t["trend_note"] and t["tier"] == 2


def test_第一級還沒到_89度每天0點5_兩天就跨90_不能只顯示22天():
    t = tr.analyze(89.0, 100, [pt(2, 88.0), pt(1, 88.5)], T)
    assert t["growth"] == 0.5 and t["days_to_first"] == 2 and t["days_to_full"] == 22


def test_至少3個日期才估_兩點或一點歷史都不估_前次較前次照給():
    t = tr.analyze(84.0, 100, [pt(1, 76.0)], T)                      # 昨天 76、今天 84：只有兩個日期
    assert t["prev"] == 76.0 and t["delta"] == 8.0 and t["direction"] == "上升中"
    assert t["growth"] is None and t["days_to_first"] is None and t["days_to_full"] is None and t["samples"] == 2
    assert "樣本不足，不估算" in t["trend_note"]
    t = tr.analyze(84.0, 100, [pt(2, 80.0), pt(1, 82.0)], T)          # 剛好 3 個日期 → 估
    assert t["samples"] == 3 and t["growth"] == 2.0 and t["days_to_full"] == 8


def test_沒有歷史_待累積_不是0():
    t = tr.analyze(70.0, 100, [], T)
    assert t["prev"] is None and t["delta"] is None and t["growth"] is None and t["direction"] is None and t["trend_note"] == "待累積"
    assert t["pct"] == 70.0 and t["peak7"] == 70.0 and t["samples"] == 1


def test_查不到使用率_不假裝正常():
    for bad in (None, "x", True, float("nan"), 101, -1):
        t = tr.analyze(bad, 100, [pt(1, 50.0)], T)
        assert t["pct"] is None and t["trend_note"] == "查不到使用率" and t["days_to_full"] is None and t["tier"] == 4, bad


def test_擴縮容_差超過1個百分比_不比不估_剛好1以內照比():
    t = tr.analyze(80.0, 102, [pt(1, 70.0, 100)], T)                  # 差 1.96%
    assert "容量有變，不估算" in t["trend_note"] and t["delta"] is None and t["direction"] is None and t["growth"] is None and t["prev"] == 70.0
    t = tr.analyze(80.0, 101, [pt(1, 70.0, 100)], T)                  # 差 1.0% 不算變 → 照比，但只有 2 個日期不估
    assert t["delta"] == 10.0 and t["growth"] is None and "樣本不足" in t["trend_note"]
    assert tr.analyze(80.0, 98, [pt(1, 70.0, 100)], T)["delta"] is None                   # 縮容 2%


def test_擴縮容後要重新累積3個日期才估():
    h = [pt(6, 40.0, 50), pt(5, 45.0, 50), pt(1, 60.0, 100)]          # 擴容後只有 1 個舊點
    t = tr.analyze(66.0, 100, h, T)
    assert t["delta"] == 6.0 and t["samples"] == 2 and t["growth"] is None and "樣本不足" in t["trend_note"]
    t = tr.analyze(66.0, 100, h[:2] + [pt(2, 58.0, 100), pt(1, 60.0, 100)], T)
    assert t["samples"] == 3 and t["growth"] == 4.0 and t["days_to_first"] == 6 and t["days_to_full"] == 9      # 擴容前的點不混進來


def test_任一端容量不明_不可比_不跨過該點():
    t = tr.analyze(65.0, 100, [pt(1, 60.0, None)], T)
    assert "容量不可比，不估算" in t["trend_note"] and t["delta"] is None and t["growth"] is None
    t = tr.analyze(65.0, None, [pt(1, 60.0, 100)], T)                 # 現在這端沒有容量
    assert "容量不可比" in t["trend_note"]
    h = [pt(3, 50.0, None), pt(2, 55.0, 100), pt(1, 60.0, 100)]       # 更早的一點容量不明：在那裡斷開，不拿來算
    t = tr.analyze(65.0, 100, h, T)
    assert t["samples"] == 3 and t["growth"] == 5.0
    t = tr.analyze(65.0, None, [pt(1, 60.0, None)], T, compare_cap=False)       # 硬碟沒有容量：不判擴縮容
    assert t["delta"] == 5.0


def test_壞值壞日期不採用_前次用更早的有效值():
    h = [pt(5, 50.0), {"day": "2026/11/18", "pct": 99.0}, {"day": "2026-13-45", "pct": 98.0}, {"day": 20261119, "pct": 97.0}, {"day": None, "pct": 96.0},
         pt(3, "abc"), pt(2, None), pt(2, True), pt(1, float("nan")), pt(1, 150.0), pt(1, -5.0)]
    t = tr.analyze(52.0, 100, h, T)
    assert t["prev"] == 50.0 and t["prev_day"] == "2026-11-15" and t["delta"] == 2.0
    assert t["samples"] == 2 and t["growth"] is None                  # 只有一個有效歷史點 → 不估


def test_同一天重複_後者蓋前者_不重複計算():
    h = [pt(1, 60.0), pt(1, 70.0), pt(1, 80.0)]
    c = tr.clean(h, T)
    assert len(c) == 1 and c[0]["pct"] == 80.0
    t = tr.analyze(85.0, 100, h, T)
    assert t["delta"] == 5.0 and t["samples"] == 2 and t["growth"] is None       # 同一天三筆不能湊成 3 個樣本


def test_今天與未來不算前次_超過回看範圍只給較前次不估():
    h = [{"day": T.isoformat(), "pct": 10.0}, {"day": (T + timedelta(days=1)).isoformat(), "pct": 11.0}, pt(41, 20.0)]
    t = tr.analyze(50.0, 100, h, T)
    assert t["prev"] is None and t["trend_note"] == "待累積"
    t = tr.analyze(50.0, 100, [pt(40, 20.0)], T)                       # 40 天還在，但超過成長回看 14 天
    assert t["prev"] == 20.0 and t["delta"] == 30.0 and t["growth"] is None and "樣本不足" in t["trend_note"]


@pytest.mark.parametrize("cur,prev,falling_high,starts", [
    (93.0, 95.0, True, "下降中，仍≥90%（7 天最高 95%）"),             # 規格：昨天 95、今天 93 不能當沒事
    (90.0, 95.0, True, "下降中，仍≥90%（7 天最高 95%）"),             # 90.0 算
    (89.9, 95.0, False, "下降中"),                                      # 89.9 不是第一級
    (85.0, 89.0, False, "下降中"),
])
def test_下降中_是否仍在第一級(cur, prev, falling_high, starts):
    t = tr.analyze(cur, 100, [pt(1, prev)], T)
    assert t["falling_high"] is falling_high and t["trend_note"].startswith(starts) and t["direction"] == "下降中"
    assert ("仍≥90%" in t["trend_note"]) is falling_high


def test_上升下降中只看較前次_長期斜率另欄_不混成一個狀態():
    h = [pt(14, 95.0), pt(1, 80.0)]                                    # 14 天前 95、昨天 80、今天 85：最近是上升
    t = tr.analyze(85.0, 100, h, T)
    assert t["delta"] == 5.0 and t["direction"] == "上升中" and "下降中" not in t["trend_note"]
    assert t["growth"] < 0 and t["samples"] == 3                       # 長期斜率是負的，另外放在每日成長欄
    t = tr.analyze(92.0, 100, [pt(14, 80.0), pt(1, 94.0)], T)          # 長期上升、今天比昨天低 → 下降中（只看 delta）
    assert t["direction"] == "下降中" and t["growth"] > 0 and t["falling_high"] is True


def test_七天最高含現值與7天內歷史_不含第8天():
    h = [pt(8, 99.0), pt(5, 92.0), pt(1, 94.0)]
    assert tr.analyze(91.0, 100, h, T)["peak7"] == 94.0
    assert tr.analyze(96.0, 100, h, T)["peak7"] == 96.0


def test_未增加_已滿_已在第一級():
    t = tr.analyze(70.0, 100, [pt(2, 70.0), pt(1, 70.0)], T)
    assert t["growth"] == 0.0 and t["trend_note"] == "未增加" and t["days_to_first"] is None
    t = tr.analyze(100.0, 100, [pt(1, 95.0)], T)
    assert t["days_to_full"] == 0 and t["days_to_first"] == 0 and t["tier"] == 0 and t["trend_note"].startswith("已滿")
    t = tr.analyze(92.0, 100, [pt(2, 90.0), pt(1, 91.0)], T)
    assert t["tier"] == 1 and t["days_to_first"] == 0 and t["days_to_full"] == 8 and "已在第一級" in t["trend_note"]


def test_排序_容量優先_99度緩降排在80度預估20天之前():
    full = tr.analyze(100.0, 100, [pt(1, 95.0)], T)                                  # 已滿
    first_growing = tr.analyze(92.0, 100, [pt(2, 90.0), pt(1, 91.0)], T)             # 已在第一級、8 天滿載
    first_falling = tr.analyze(99.0, 100, [pt(1, 99.5)], T)                          # 已在第一級、下降中、沒有天數
    soon = tr.analyze(80.0, 100, [pt(2, 78.0), pt(1, 79.0)], T)                      # 10 天到第一級、20 天滿載
    calm = tr.analyze(60.0, 100, [], T)
    order = sorted([calm, soon, first_falling, first_growing, full], key=tr.sort_key)
    assert [x["pct"] for x in order] == [100.0, 92.0, 99.0, 80.0, 60.0]
    a = tr.analyze(80.0, 100, [pt(2, 70.0), pt(1, 75.0)], T)                         # 5 天滿載、2 天到第一級
    assert sorted([soon, a], key=tr.sort_key)[0] is a                                # 到第一級天數少的在前


# ---------------------------------------------------------------- Pool 整合
def put_hist(c, serial, pool_id, day, used, total, batch="old"):
    c.execute("INSERT OR REPLACE INTO storage_pool_daily (serial, pool_id, day, name, size_total, size_used, size_free, batch_id) VALUES (?,?,?,?,?,?,?,?)",
              (serial, pool_id, day, pool_id, total * GB, used * GB, (total - used) * GB, batch))


def pool_doc(used, total=100, serial="FAKESN0007", extra_pools=()):
    doc = v2(serial=serial)
    tot, u = total * GB, round(used * total * GB / 100)
    doc["pools"] = [{"id": "pool_1", "name": "Pool-Gold", "sizeTotal": tot, "sizeUsed": u, "sizeFree": tot - u}, *extra_pools]
    return doc


def pool_lines(row):
    return {d["name"]: d for d in row["detail"] if d.get("kind") == "Pool"}


def test_Pool_93過昨天95_第一級_顯示下降中_兩個天數欄位(conn):
    configure(conn)
    load(conn, pool_doc(93))
    put_hist(conn, "FAKESN0007", "pool_1", "2026-11-10", 95, 100)
    conn.commit()
    r = srow(conn)[0]
    assert r["verdict"] == "bad" and "Pool 1 個 ≥90%" in r["value_text"]
    p = pool_lines(r)["Pool Pool-Gold"]
    assert p["state"] == "第一級" and p["pct"] == 93.0 and p["prev"] == 95.0 and p["delta"] == -2.0 and p["direction"] == "下降中"
    assert p["trend_note"].startswith("下降中，仍≥90%（7 天最高 95%）") and p["days_to_first"] == 0 and p["tier"] == 1 and p["trend"] is True


@pytest.mark.parametrize("used,verdict,level", [(89.9, "ok", "需處理"), (90.0, "bad", "第一級"), (93.0, "bad", "第一級"), (0.0, "ok", "正常")])
def test_Pool等級邊界_89_9_90_93(conn, used, verdict, level):
    configure(conn)
    load(conn, pool_doc(used, total=1000))
    r = srow(conn)[0]
    assert r["verdict"] == verdict and pool_lines(r)["Pool Pool-Gold"]["state"] == level


def test_Pool第一級是全站固定的90_不跟disk_alert漂移(conn):
    configure(conn, disk_alert=95)                                      # 硬碟異常門檻調到 95
    load(conn, pool_doc(92))
    r = srow(conn)[0]
    assert r["verdict"] == "bad" and pool_lines(r)["Pool Pool-Gold"]["state"] == "第一級"        # Pool 92% 仍是第一級


def test_Pool歷史_升_兩個天數_容量不同不估(conn):
    configure(conn)
    load(conn, pool_doc(84))
    put_hist(conn, "FAKESN0007", "pool_1", "2026-11-09", 80, 100)
    put_hist(conn, "FAKESN0007", "pool_1", "2026-11-10", 82, 100)
    conn.commit()
    p = pool_lines(srow(conn)[0])["Pool Pool-Gold"]
    assert p["growth"] == 2.0 and p["days_to_first"] == 3 and p["days_to_full"] == 8 and p["samples"] == 3 and "約 3 天到第一級、8 天滿載" in p["trend_note"]
    conn.execute("UPDATE storage_pool_daily SET size_total = ?, size_used = ? WHERE day = '2026-11-10'", (200 * GB, 164 * GB))   # 前次容量 200（現在 100）
    conn.commit()
    p = pool_lines(srow(conn)[0])["Pool Pool-Gold"]
    assert "容量有變，不估算" in p["trend_note"] and p["days_to_full"] is None and p["delta"] is None


def test_Pool目前用的是昨日批次_歷史不含這一批自己(conn):
    configure(conn)
    load(conn, pool_doc(86), collected="2026-11-10T05:00:00+08:00", batch="2026-11-10")      # 今天（11-11）還沒收到新批，仍採用 11-10 的批次
    put_hist(conn, "FAKESN0007", "pool_1", "2026-11-09", 80, 100, "b9")
    conn.commit()
    p = pool_lines(srow(conn)[0])["Pool Pool-Gold"]
    assert p["prev"] == 80.0 and p["prev_day"] == "2026-11-09" and p["delta"] == 6.0           # 前次是真正的前一天，不是自己（86）
    assert tr.pool_history(conn, "FAKESN0007", date(2026, 11, 10), "2026-11-10").get("pool_1") is not None
    conn.execute("UPDATE storage_pool_daily SET batch_id = 'b8' WHERE day = '2026-11-09'")
    put_hist(conn, "FAKESN0007", "pool_1", "2026-11-08", 70, 100, "cur")
    conn.commit()
    h = tr.pool_history(conn, "FAKESN0007", date(2026, 11, 10), "cur")["pool_1"]               # 指名排除的批次不採用
    assert [x["day"] for x in h] == ["2026-11-09"]


def test_Pool同日重跑只留一筆_今天自己不算前次(conn):
    configure(conn)
    load(conn, pool_doc(60))
    load(conn, pool_doc(61))
    n = conn.execute("SELECT COUNT(*) FROM storage_pool_daily WHERE serial='FAKESN0007' AND pool_id='pool_1' AND day='2026-11-11'").fetchone()[0]
    assert n == 1
    assert pool_lines(srow(conn)[0])["Pool Pool-Gold"]["prev"] is None


def test_Pool讀不到使用率_無資料_未收集_不是正常(conn):
    configure(conn)
    load(conn, pool_doc(50))
    conn.execute("UPDATE storage_pool SET size_total = NULL WHERE serial = 'FAKESN0007'")
    conn.commit()
    r = srow(conn)[0]
    assert r["verdict"] == "nodata" and "讀不到使用率" in r["reason"]
    assert pool_lines(r)["Pool Pool-Gold"]["state"] == "未收集"


def test_Pool清單0筆或個數不符_沒收到_不判正常(conn):
    configure(conn)
    load(conn, pool_doc(50))
    assert srow(conn)[0]["verdict"] == "ok"
    conn.execute("UPDATE storage_array SET pool_count = 3 WHERE serial = 'FAKESN0007'")           # 陣列說 3 個，只收到 1 個
    conn.commit()
    r = srow(conn)[0]
    assert r["verdict"] == "nodata" and "只收到一半的 Pool" in r["reason"]
    conn.execute("DELETE FROM storage_pool WHERE serial = 'FAKESN0007'")
    conn.commit()
    r = srow(conn)[0]
    assert r["verdict"] == "nodata" and "沒收到 Pool" in r["reason"]


def test_Pool沒有歷史_容量現值照判_整列不降級_趨勢欄待累積(conn):
    configure(conn)
    load(conn, pool_doc(93))
    r = srow(conn)[0]
    assert r["verdict"] == "bad" and pool_lines(r)["Pool Pool-Gold"]["trend_note"].startswith("已在第一級") and "待累積" in pool_lines(r)["Pool Pool-Gold"]["trend_note"]
    conn.execute("UPDATE storage_pool SET size_used = ?, size_free = ? WHERE serial = 'FAKESN0007'", (50 * GB, 50 * GB))
    conn.commit()
    r = srow(conn)[0]
    assert r["verdict"] == "ok" and pool_lines(r)["Pool Pool-Gold"]["state"] == "正常" and pool_lines(r)["Pool Pool-Gold"]["trend_note"] == "待累積"


def test_Pool多個_已在第一級的排前_正常列也有證據(conn):
    configure(conn)
    load(conn, pool_doc(40, extra_pools=[{"id": "pool_2", "name": "Pool-B", "sizeTotal": 100 * GB, "sizeUsed": 70 * GB, "sizeFree": 30 * GB}]))
    put_hist(conn, "FAKESN0007", "pool_2", "2026-11-09", 68, 100)
    put_hist(conn, "FAKESN0007", "pool_2", "2026-11-10", 69, 100)
    conn.commit()
    r = srow(conn)[0]
    assert r["verdict"] == "ok" and "Pool 2 個、最高 70%" in r["value_text"]
    assert [d["name"] for d in r["detail"] if d.get("kind") == "Pool"] == ["Pool Pool-B", "Pool Pool-Gold"]     # 有到第一級天數的在前


def test_沒有Pool歷史表_不炸_照常判定(conn):
    configure(conn)
    load(conn, pool_doc(50))
    conn.execute("DROP TABLE storage_pool_daily")
    conn.commit()
    r = srow(conn)[0]
    assert r["verdict"] == "ok" and pool_lines(r)["Pool Pool-Gold"]["trend_note"] == "待累積"


# ---------------------------------------------------------------- 設定
def test_設定預設值_例外不強制_資料存放區第一級90(conn):
    s = dc.settings(conn)
    assert s["exempt_force_first_level"] is False and s["vm_ds_first_pct"] == 90.0
    assert dc.DEFAULTS["exempt_force_first_level"] is False and dc.DEFAULTS["vm_ds_first_pct"] == 90.0


def test_設定驗證_第一級用量範圍(conn):
    for bad in (49.9, 100.1, "abc"):
        with pytest.raises(ValueError):
            dc.save_settings(conn, {"vm_ds_first_pct": bad})
    assert dc.save_settings(conn, {"vm_ds_first_pct": 95})["vm_ds_first_pct"] == 95.0


def test_布林設定只接受JSON_boolean_字串false不會變True(conn):
    for key in ("exempt_force_first_level", "enabled", "signoff"):
        for bad in ("false", "true", 1, 0, None, "0"):
            with pytest.raises(ValueError):
                dc.save_settings(conn, {key: bad})
    assert dc.settings(conn)["exempt_force_first_level"] is False
    assert dc.save_settings(conn, {"exempt_force_first_level": True})["exempt_force_first_level"] is True
    assert dc.save_settings(conn, {"exempt_force_first_level": False})["exempt_force_first_level"] is False
