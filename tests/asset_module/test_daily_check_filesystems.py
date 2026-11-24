"""1.5 每日檢查：Unity 檔案系統（收集腳本 v3）的容量成長與估計滿載（2.071.x，沿用 DailyCheck v2.4.6 Pool 那把尺 daily_check_trend.analyze）。

涵蓋：v3 檔解析（讀完／沒讀完／舊腳本／筆數對不上／形狀錯、PowerShell 5.1 包裝）、舊版（v1／v2）批次向下相容、
匯入寫入（快照整份換、每日同日去重、留 40 天、沒讀完不寫歷史也不留舊清單、較舊批次不覆蓋）、
1.5 判定（≥90% 一律第一級、89.9 不是、成長與兩個天數、擴縮容、讀不到＝沒收到不是正常、0 個合法、排序、昨日批次不跟自己比）。
全部假資料：10.99.x、假序號。檔案系統欄位依 Unity 公開文件，沒有對真機驗證。
"""
import copy
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import daily_check_trend as tr  # noqa: E402
import storage_relay as sr  # noqa: E402
from test_daily_check import conn  # noqa: E402,F401
from test_daily_check_storage import FRESH, configure, load, srow, v2  # noqa: E402,F401
from test_relay_unity import GB  # noqa: E402

SN = "FAKESN0007"
T = date(2026, 11, 11)          # test_daily_check_storage.NOW 的日期＝目前採用那一批的收集日


def fs_row(i, used, total=100, name=None, **kw):
    """Unity filesystem 一筆（欄位依公開文件）：used／total 單位 GB（百分比＝used／total）。"""
    return {"id": f"fs_{i}", "name": name or f"FS-{i}", "health": {"value": 5}, "sizeTotal": total * GB, "sizeUsed": round(used * GB),
            "sizeAllocated": round(used * GB), "isThinEnabled": True, "pool": {"id": "pool_1"}, **kw}


def v3(rows=(), **over):
    doc = v2(filesystems=list(rows))
    doc.update(over)
    return doc


def not_read(msg="HTTP 500 from the Storage.", **over):
    doc = v2(filesystems=None)
    doc.update(filesystems_read=False, filesystems_error=msg, filesystems_total=None, filesystems=[], **over)
    return doc


def put_hist(c, fs_id, day, used, total=100, batch="old", serial=SN):
    c.execute("INSERT OR REPLACE INTO storage_fs_daily (serial, fs_id, day, name, size_total, size_used, batch_id) VALUES (?,?,?,?,?,?,?)",
              (serial, fs_id, day, fs_id, total * GB, used * GB, batch))


def lines(row):
    return {d["name"]: d for d in row["detail"] if d.get("kind") == "檔案系統"}


# ---------------------------------------------------------------- 解析
def test_v3解析_讀完_欄位原樣帶出_PS包裝也照收():
    a = sr.parse_file(json.loads(json.dumps(v3([fs_row(1, 50), fs_row(2, 91.5, name="share-b")]))))
    f = a["filesystems"]
    assert f["status"] == "read" and f["n"] == 2 and [r["id"] for r in f["rows"]] == ["fs_1", "fs_2"]
    assert f["rows"][1]["used"] == round(91.5 * GB) and f["rows"][1]["total"] == 100 * GB and f["rows"][1]["pool_id"] == "pool_1"
    assert f["rows"][0]["thin"] == 1 and f["rows"][0]["health"] == "5"
    wrapped = v3(filesystems_total=1)
    wrapped["filesystems"] = {"value": [fs_row(1, 10)], "Count": 1}                    # PowerShell 5.1 把陣列包成 value／Count
    assert sr.parse_file(wrapped)["filesystems"]["status"] == "read"
    single = v3(filesystems_total=1)
    single["filesystems"] = fs_row(1, 10)                                              # 單筆被寫成物件
    assert sr.parse_file(single)["filesystems"]["n"] == 1


def test_讀完0個檔案系統是合法的_不是沒收到():
    f = sr.parse_file(v3([]))["filesystems"]
    assert f["status"] == "read" and f["n"] == 0 and f["rows"] == []


def test_舊腳本v1_v2的檔照收_檔案系統標沒收_不是0個():
    assert sr.parse_file(v2(filesystems=None))["filesystems"]["status"] == "not_collected"
    from test_relay_unity import device
    assert sr.parse_file(device())["filesystems"]["status"] == "not_collected"


def test_旗標false_記失敗與原因_清單不採用():
    f = sr.parse_file(not_read("HTTP 403 from the Storage."))["filesystems"]
    assert f["status"] == "failed" and "HTTP 403" in f["msg"] and f["rows"] == []
    f = sr.parse_file(not_read(""))["filesystems"]
    assert f["status"] == "failed" and f["msg"]                                        # 沒寫原因也要有說明


def test_讀完旗標true但筆數或total對不上_不當讀完():
    doc = v3([fs_row(1, 10), fs_row(2, 20)], filesystems_total=3)                       # 陣列說 3 個、檔案只有 2 個
    f = sr.parse_file(doc)["filesystems"]
    assert f["status"] == "failed" and "對不上" in f["msg"] and f["rows"] == []
    doc = v3([fs_row(1, 10)])
    doc["filesystems_total"] = None                                                     # 沒有 total ＝無法確認讀完
    f = sr.parse_file(doc)["filesystems"]
    assert f["status"] == "failed" and "filesystems_total" in f["msg"]


@pytest.mark.parametrize("mut", [
    lambda d: d.update(filesystems_read="yes"),
    lambda d: d.update(filesystems_total=True),
    lambda d: d.update(filesystems_total=-1),
    lambda d: d.update(filesystems_total="2"),
    lambda d: d.update(filesystems=["not an object"], filesystems_total=1),
    lambda d: d.update(filesystems=[fs_row(1, 10), fs_row(1, 20)], filesystems_total=2),
    lambda d: d.update(filesystems=[{**fs_row(1, 10), "id": "../x"}], filesystems_total=1),
    lambda d: d.update(filesystems=[{**fs_row(1, 10), "isThinEnabled": "yes"}], filesystems_total=1),
    lambda d: d.update(filesystems=[{**fs_row(1, 10), "health": "OK"}], filesystems_total=1),
    lambda d: d.update(filesystems=[{**fs_row(1, 10), "sizeTotal": -5}], filesystems_total=1),
    lambda d: d.update(filesystems=[{**fs_row(1, 10), "sizeUsed": "10"}], filesystems_total=1),
    lambda d: d.update(filesystems=None),
])
def test_格式錯_丟ValueError(mut):
    doc = v3([fs_row(1, 10)])
    mut(doc)
    with pytest.raises(ValueError):
        sr.parse_file(doc)


def test_收集腳本填的是Unity文件欄位_不多收不認得的欄位():
    r = sr.parse_file(v3([{**fs_row(1, 10), "secret": "x", "nasServer": {"id": "nas_1"}}]))["filesystems"]["rows"][0]
    assert set(r) == {"id", "name", "pool_id", "health", "thin", "total", "used", "alloc"}


# ---------------------------------------------------------------- 匯入寫入
def test_匯入_快照與每日歷史_同日去重(conn):
    configure(conn)
    load(conn, v3([fs_row(1, 60)]))
    load(conn, v3([fs_row(1, 61)]))                                                      # 同一天再跑一次
    assert conn.execute("SELECT COUNT(*) FROM storage_fs_daily WHERE serial=? AND fs_id='fs_1'", (SN,)).fetchone()[0] == 1
    assert conn.execute("SELECT size_used FROM storage_fs_daily WHERE serial=? AND fs_id='fs_1'", (SN,)).fetchone()[0] == 61 * GB   # 後到的蓋先到的
    assert conn.execute("SELECT COUNT(*) FROM storage_fs WHERE serial=?", (SN,)).fetchone()[0] == 1
    st = conn.execute("SELECT status, n_total FROM storage_fs_status WHERE serial=?", (SN,)).fetchone()
    assert (st["status"], st["n_total"]) == ("read", 1)


def test_匯入_檔案系統被刪掉_下一批快照不殘留(conn):
    configure(conn)
    load(conn, v3([fs_row(1, 60), fs_row(2, 30)]))
    load(conn, v3([fs_row(1, 60)]))
    assert [r[0] for r in conn.execute("SELECT fs_id FROM storage_fs WHERE serial=? ORDER BY fs_id", (SN,))] == ["fs_1"]


def test_匯入_只留40天(conn):
    configure(conn)
    sr.ensure(conn)
    put_hist(conn, "fs_1", "2026-09-30", 10)            # 42 天前：要被清掉
    put_hist(conn, "fs_1", "2026-10-03", 11)            # 39 天前：要留
    conn.commit()
    load(conn, v3([fs_row(1, 60)]))
    days = [r[0] for r in conn.execute("SELECT day FROM storage_fs_daily WHERE serial=? ORDER BY day", (SN,))]
    assert days == ["2026-10-03", "2026-11-11"]


def test_匯入_沒讀完的批次_不留舊清單_不寫歷史_舊歷史保留(conn):
    configure(conn)
    load(conn, v3([fs_row(1, 60)]))
    load(conn, not_read())                                                                # 同一天再收一批，這次沒讀完
    assert conn.execute("SELECT COUNT(*) FROM storage_fs WHERE serial=?", (SN,)).fetchone()[0] == 0          # 不拿舊清單冒充現況
    st = conn.execute("SELECT status, msg FROM storage_fs_status WHERE serial=?", (SN,)).fetchone()
    assert st["status"] == "failed" and "HTTP 500" in st["msg"]
    assert conn.execute("SELECT COUNT(*) FROM storage_fs_daily WHERE serial=?", (SN,)).fetchone()[0] == 1     # 已寫的歷史不動、也沒多寫


def test_匯入_使用量讀不到的檔案系統_列出但不寫歷史(conn):
    configure(conn)
    row = fs_row(1, 10)
    del row["sizeUsed"]
    load(conn, v3([row]))
    assert conn.execute("SELECT size_used FROM storage_fs WHERE serial=?", (SN,)).fetchone()[0] is None
    assert conn.execute("SELECT COUNT(*) FROM storage_fs_daily").fetchone()[0] == 0


def test_匯入_較舊的批次不蓋掉較新的(conn):
    configure(conn)
    load(conn, v3([fs_row(1, 60)]))
    load(conn, v3([fs_row(1, 10)]), collected="2026-11-09T05:00:00+08:00", batch="2026-11-09")        # 比現有資料舊
    assert conn.execute("SELECT size_used FROM storage_fs WHERE serial=?", (SN,)).fetchone()[0] == 60 * GB


def test_匯入_舊版v2批次照常匯入_Pool與健康不受影響(conn):
    configure(conn)
    a = load(conn, v2(filesystems=None))
    assert a["filesystems"]["status"] == "not_collected"
    assert conn.execute("SELECT COUNT(*) FROM storage_pool WHERE serial=?", (SN,)).fetchone()[0] >= 1
    assert conn.execute("SELECT health_status FROM storage_relay_health WHERE serial=?", (SN,)).fetchone()[0] == "read"
    assert conn.execute("SELECT status FROM storage_fs_status WHERE serial=?", (SN,)).fetchone()[0] == "not_collected"


# ---------------------------------------------------------------- 1.5 判定
@pytest.mark.parametrize("used,verdict,level", [(89.9, "ok", "需處理"), (90.0, "bad", "第一級"), (93.0, "bad", "第一級"), (0.0, "ok", "正常")])
def test_檔案系統等級邊界_89_9不是第一級_90是(conn, used, verdict, level):
    configure(conn)
    load(conn, v3([fs_row(1, used * 10, total=1000)]))             # 總量 1000 GB、已用 used*10 GB ＝ used %
    r = srow(conn)[0]
    assert r["verdict"] == verdict and lines(r)["檔案系統 FS-1"]["state"] == level
    if verdict == "bad":
        assert "檔案系統 1 個 ≥90%" in r["value_text"]


def test_檔案系統第一級是全站固定的90_不跟disk_alert漂移(conn):
    configure(conn, disk_alert=95)
    load(conn, v3([fs_row(1, 92)]))
    r = srow(conn)[0]
    assert r["verdict"] == "bad" and lines(r)["檔案系統 FS-1"]["state"] == "第一級"


def test_歷史成長_兩個天數_到第一級與到滿載(conn):
    configure(conn)
    load(conn, v3([fs_row(1, 84)]))
    put_hist(conn, "fs_1", "2026-11-09", 80)
    put_hist(conn, "fs_1", "2026-11-10", 82)
    conn.commit()
    f = lines(srow(conn)[0])["檔案系統 FS-1"]
    assert f["prev"] == 82.0 and f["delta"] == 2.0 and f["growth"] == 2.0 and f["samples"] == 3
    assert f["days_to_first"] == 3 and f["days_to_full"] == 8 and "約 3 天到第一級、8 天滿載" in f["trend_note"] and f["trend"] is True
    assert f["kind"] == "檔案系統" and f["tier"] == 2


def test_樣本不足兩個日期_不估算_前次較前次照給(conn):
    configure(conn)
    load(conn, v3([fs_row(1, 84)]))
    put_hist(conn, "fs_1", "2026-11-10", 80)
    conn.commit()
    f = lines(srow(conn)[0])["檔案系統 FS-1"]
    assert f["prev"] == 80.0 and f["delta"] == 4.0 and f["growth"] is None and f["days_to_full"] is None and "樣本不足，不估算" in f["trend_note"]


def test_擴縮容超過1pct_不估算(conn):
    configure(conn)
    load(conn, v3([fs_row(1, 84)]))
    put_hist(conn, "fs_1", "2026-11-09", 80)
    put_hist(conn, "fs_1", "2026-11-10", 164, total=200)                                  # 前次容量 200（現在 100）
    conn.commit()
    f = lines(srow(conn)[0])["檔案系統 FS-1"]
    assert "容量有變，不估算" in f["trend_note"] and f["days_to_full"] is None and f["delta"] is None


def test_高水位下降中_仍是第一級_整列bad(conn):
    configure(conn)
    load(conn, v3([fs_row(1, 93)]))
    put_hist(conn, "fs_1", "2026-11-10", 95)
    conn.commit()
    r = srow(conn)[0]
    f = lines(r)["檔案系統 FS-1"]
    assert r["verdict"] == "bad" and f["state"] == "第一級" and f["direction"] == "下降中" and f["trend_note"].startswith("下降中，仍≥90%（7 天最高 95%）")


def test_目前用昨日批次_歷史不含這一批自己(conn):
    configure(conn)
    load(conn, v3([fs_row(1, 86)]), collected="2026-11-10T05:00:00+08:00", batch="2026-11-10")     # 今天還沒收到新批
    put_hist(conn, "fs_1", "2026-11-09", 80, batch="b9")
    conn.commit()
    f = lines(srow(conn)[0])["檔案系統 FS-1"]
    assert f["prev"] == 80.0 and f["prev_day"] == "2026-11-09" and f["delta"] == 6.0
    assert tr.fs_history(conn, SN, date(2026, 11, 10), "2026-11-10").get("fs_1") is not None
    assert tr.fs_history(conn, SN, date(2026, 11, 10), "b9").get("fs_1") is None        # 指名排除的批次不採用（這天只有 b9 一筆）


def test_多個檔案系統_已在第一級的排前_正常列也有證據(conn):
    configure(conn)
    load(conn, v3([fs_row(1, 40), fs_row(2, 70), fs_row(3, 95, name="share-hot")]))
    put_hist(conn, "fs_2", "2026-11-09", 68)
    put_hist(conn, "fs_2", "2026-11-10", 69)
    conn.commit()
    r = srow(conn)[0]
    assert [d["name"] for d in r["detail"] if d.get("kind") == "檔案系統"] == ["檔案系統 share-hot", "檔案系統 FS-2", "檔案系統 FS-1"]
    assert r["verdict"] == "bad" and "檔案系統 1 個 ≥90%（最高 95%）" in r["value_text"]


def test_全部正常_判ok_文字寫檔案系統個數與最高值(conn):
    configure(conn)
    load(conn, v3([fs_row(1, 40), fs_row(2, 70)]))
    r = srow(conn)[0]
    assert r["verdict"] == "ok" and "檔案系統 2 個、最高 70%" in r["value_text"]


def test_讀完0個檔案系統_不影響判定_也不是沒收到(conn):
    configure(conn)
    load(conn, v3([]))
    r = srow(conn)[0]
    assert r["verdict"] == "ok" and "檔案系統 0 個" in r["value_text"] and not lines(r)


def test_讀失敗_沒收到_整列無資料_不判正常(conn):
    configure(conn)
    load(conn, not_read("HTTP 403 from the Storage."))
    r = srow(conn)[0]
    assert r["verdict"] == "nodata" and "檔案系統讀取失敗" in r["reason"] and "HTTP 403" in r["reason"]
    l = lines(r)["檔案系統（沒收到）"]
    assert l["state"] == "未收集" and l["pct"] is None and l["trend"] is True and "不判正常" in l["what"]


def test_舊腳本v2沒有檔案系統_只在明細標未收集_不拖累整列(conn):
    """向下相容：公司端還沒換 v3 腳本時，Storage 這一列照舊判（Pool／健康／告警），檔案系統明細寫「未收集」；
    只有 v3 腳本讀失敗／只收到一半才降級（見下面各條）。"""
    configure(conn)
    load(conn, v2(filesystems=None))
    r = srow(conn)[0]
    assert r["verdict"] == "ok" and not (r["reason"] or "")
    assert lines(r)["檔案系統（沒收到）"]["state"] == "未收集"
    # 2.070.1（審查 p2#3／#23／#24）：查不到≠0 個；整列正常時明細不能再說「不判正常」
    assert "檔案系統：未收集" in r["value_text"] and "檔案系統 0 個" not in r["value_text"]
    w = lines(r)["檔案系統（沒收到）"]["what"]
    assert "未收集" in w and "不判正常" not in w


def test_有檔案系統第一級時_就算別處不完整仍是異常(conn):
    configure(conn)
    load(conn, v3([fs_row(1, 97)], alerts_read=False))                                  # 告警沒讀完，但已讀到的檔案系統 97% 就是異常
    r = srow(conn)[0]
    assert r["verdict"] == "bad" and "檔案系統 1 個 ≥90%" in r["value_text"]


def test_使用量讀不到_未收集_不是正常(conn):
    configure(conn)
    row = fs_row(1, 10)
    del row["sizeUsed"]
    load(conn, v3([row]))
    r = srow(conn)[0]
    assert r["verdict"] == "nodata" and "檔案系統「FS-1」讀不到使用率" in r["reason"] and lines(r)["檔案系統 FS-1"]["state"] == "未收集"


def test_收到一半_清單筆數與狀態記的個數不符_判失敗(conn):
    configure(conn)
    load(conn, v3([fs_row(1, 40), fs_row(2, 50)]))
    conn.execute("DELETE FROM storage_fs WHERE fs_id = 'fs_2'")                           # 模擬清單被截掉一筆
    conn.commit()
    r = srow(conn)[0]
    assert r["verdict"] == "nodata" and "只收到一半的檔案系統" in r["reason"]


def test_沒有檔案系統表_不炸_明細標未收集(conn):
    configure(conn)
    load(conn, v3([fs_row(1, 40)]))
    for t in ("storage_fs_daily", "storage_fs", "storage_fs_status"):
        conn.execute(f"DROP TABLE {t}")
    conn.commit()
    r = srow(conn)[0]
    assert r["verdict"] == "ok"                                           # 沒有資料表＝等同舊腳本：不拖累整列，只在明細標未收集
    assert lines(r)["檔案系統（沒收到）"]["state"] == "未收集"
    assert "檔案系統：未收集" in r["value_text"] and "0 個" not in r["value_text"].split("檔案系統")[-1]
    assert tr.filesystems_for(conn, SN, T, 90.0)["status"] == "not_collected"


def test_沒有歷史表_只缺歷史_現值照判(conn):
    configure(conn)
    load(conn, v3([fs_row(1, 93)]))
    conn.execute("DROP TABLE storage_fs_daily")
    conn.commit()
    r = srow(conn)[0]
    assert r["verdict"] == "bad" and lines(r)["檔案系統 FS-1"]["trend_note"].startswith("已在第一級")


def test_Pool與檔案系統互不干擾_各自列(conn):
    configure(conn)
    load(conn, v3([fs_row(1, 40)]))
    r = srow(conn)[0]
    kinds = {d.get("kind") for d in r["detail"] if d.get("trend")}
    assert kinds == {"Pool", "檔案系統"}


def test_明細列帶畫面欄位_跟Pool列一樣(conn):
    configure(conn)
    load(conn, v3([fs_row(1, 60)]))
    f = lines(srow(conn)[0])["檔案系統 FS-1"]
    for k in ("pct", "prev", "prev_day", "delta", "direction", "growth", "samples", "days_to_first", "days_to_full", "tier", "peak7", "trend_note", "state", "what"):
        assert k in f, k
    assert copy.deepcopy(f)["pct"] == 60.0
