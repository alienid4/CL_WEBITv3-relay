"""1.5 每日檢查（試辦，2.060.x）：GPT 審查 12 點逐點釘住。全部假資料（IP 10.99.x）。
舊資料／不完整／未設定＝無資料；前一日效能永遠是「前一日參考」；不在問題清單≠Up；分母可驗證；例外門檻要原因／到期／≤95%；
批次確認只限新鮮正常列；Excel 有稽核工作表；排程 06:30／07:45／08:15；verify_numbers 卡片＝下鑽。"""
import gzip
import io
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import capacity_analysis as ca  # noqa: E402
import daily_check as dc  # noqa: E402
import db  # noqa: E402
import relay_import  # noqa: E402
import verify_numbers as vn  # noqa: E402
import wug_alert  # noqa: E402

NOW = datetime(2026, 11, 11, 6, 30)
YDAY = "2026-11-10"


def _scan(c, at, complete=1, seen=100, total=100, reason=None, batch="wugalert_1"):
    c.execute("INSERT INTO alert_scan (batch_id, snap_at, kind, complete, pages_read, devices_seen, problems, stopped_reason, group_json, received_at) "
              "VALUES (?,?,?,?,?,?,?,?,?,?)", (batch, at.strftime(dc.FMT), "scan", complete, 1, seen, 1, reason,
                                              json.dumps({"totalDevices": total}), at.strftime(dc.FMT)))


def _event(c, ip, state, dev="d1", mon="Ping"):
    c.execute("INSERT INTO alert_event (wug_device_id, monitor, device_name, ip, wug_state, tier, started_at, last_seen, created_at) "
              "VALUES (?,?,?,?,?,?,?,?,?)", (dev, mon, f"dev-{ip}", ip, state, "red", "2026-11-11 05:00:00", "2026-11-11 06:29:00", "2026-11-11 05:00:00"))


def _perf(c, ip, day, cpu_max, mem_max, last_ts="15:50:00"):
    ts = [f"{day}T00:00:00", f"{day}T{last_ts}"]
    obj = {"ts": ts, "cols": {"cpu_busy": [10, 20], "mem_used": [30, 40]}, "max": {"cpu_busy": [cpu_max, 5], "mem_used": [mem_max, 5]},
           "interval_s": 300, "kind": "bucket", "tz": "UTC", "src": "relay"}
    c.execute("INSERT OR REPLACE INTO capacity_whatsup_perf (host, day, interval_s, samples, data, updated_at) VALUES (?,?,?,?,?,?)",
              (ip, day, 300, 2, gzip.compress(json.dumps(obj).encode()), "x"))


def _disk(c, ip, day, disk, last):
    d = {"ts": [f"{day}T00:00:00", f"{day}T15:00:00"], "avg": [10, last], "max": [10, last], "min": [10, last], "tz": "UTC"}
    c.execute("INSERT INTO relay_disk_util (host, day, disk, interval_s, samples, data, batch_id, updated_at) VALUES (?,?,?,?,?,?,?,?)",
              (ip, day, disk, 300, 2, gzip.compress(json.dumps(d).encode()), "wug_b1", "x"))


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    wug_alert.ensure(c)
    relay_import.ensure(c)
    c.execute(ca.WU_TABLE_SQL)
    db.insert_hardware(c, asset_serial="H-1", hostname="app01", ip="10.99.1.1")
    c.execute("INSERT INTO relay_import_batches (source, batch_id, status, collected_at, imported_at) VALUES ('whatsup','wug_b1','imported',?,?)",
              ("2026-11-10 16:00:00", "2026-11-10 16:05:00"))
    _scan(c, NOW - timedelta(minutes=2))
    _event(c, "10.99.1.9", "Down", dev="d9")
    _event(c, "10.99.1.8", "Maintenance", dev="d8", mon="(maintenance)")
    for ip, cpu, mem in (("10.99.1.1", 80, 50), ("10.99.1.2", 60, 95)):
        _perf(c, ip, YDAY, cpu, mem)
    _disk(c, "10.99.1.1", YDAY, "C:", 90)
    _disk(c, "10.99.1.2", YDAY, "/", 50)
    c.commit()
    dc.save_settings(c, {"xserver_ips": ["10.99.1.1", "10.99.1.2", "10.99.1.3"], "banqiao": [{"name": "甲", "ip": "10.99.1.9"}, {"name": "乙", "ip": "10.99.1.1"}],
                         "vcenters": ["VC-甲", "VC-乙"], "daily_check_perf_source": "w3"}, today=NOW.date())   # 舊規則（W3）的測試；上次輪循在 test_daily_check_lastpoll.py
    yield c
    c.close()


def _rows(c, now=NOW):
    rid = dc.generate(c, "tester", "manual", now)
    v = dc.run_view(c, rid)
    return rid, {r["code"]: r for r in v["rows"]}, v


def test_自動列_判定與來源(conn):
    rid, r, v = _rows(conn)
    assert r["W-a"]["verdict"] == "bad" and "1 台" in r["W-a"]["value_text"] and r["W-a"]["fresh"] == 1
    assert r["W-a"]["detail"][0]["ip"] == "10.99.1.9"
    assert r["W-b"]["verdict"] == "ref" and "1 台" in r["W-b"]["value_text"]            # 審查 P2-4：有設備維護中要人看
    # GPT 2：前一日效能永遠是「前一日參考」，不會自動正常
    for k in ("W-c", "W-d", "W-f", "W-g"):
        assert r[k]["verdict"] == "ref", k
    assert "1 台超過 75%" in r["W-c"]["value_text"] and "2／3 台有資料" in r["W-c"]["value_text"]
    assert r["W-c"]["detail"][0]["name"] == "app01"                        # GPT 5：對得到資產就給名字
    assert "1 台超過 90%" in r["W-d"]["value_text"]
    assert r["W-d"]["detail"][0]["name"] == "（對不到資產）"
    assert "1 顆超過門檻" in r["W-f"]["value_text"]
    # 人工列
    assert r["W-e"]["verdict"] == "nodata" and "沒有 SWAP 項目" in r["W-e"]["reason"]     # 2.060.x：SWAP 自動（沒有 SWAP 項目＝無資料）
    assert r["O-1"]["verdict"] == "manual" and r["O-2"]["kind"] == "auto"
    assert r["VC-1"]["name"].endswith("VC-甲") and r["VC-2"]["kind"] == "manual" and r["VC-1"]["extra"] == "vmware"
    src = v["run"]["sources"]["whatsup_alert"]["block"]
    assert (src["devices_seen"], src["group_total"], src["complete"]) == (100, 100, True)   # GPT 4：分母存進快照


def test_GPT3_板橋_只有明確Down才判_其餘無資料(conn):
    _, r, _ = _rows(conn)
    assert r["B-1"]["verdict"] == "bad"                                   # 10.99.1.9 有開著的 Down
    assert r["B-2"]["verdict"] == "nodata" and "沒有這台明確的 Up" in r["B-2"]["reason"]


def test_GPT4_掃描不完整或分母對不上_整個來源無資料(conn):
    _scan(conn, NOW - timedelta(minutes=1), seen=50, total=100, batch="wugalert_2")
    conn.commit()
    _, r, _ = _rows(conn)
    for k in ("W-a", "W-b", "B-1", "B-2"):
        assert r[k]["verdict"] == "nodata" and "對不上" in r[k]["reason"], k
    _scan(conn, NOW, complete=0, reason="分頁中斷", batch="wugalert_3")
    conn.commit()
    _, r, _ = _rows(conn)
    assert "不完整" in r["W-a"]["reason"]


def test_舊資料一律無資料_不是正常(conn):
    _, r, _ = _rows(conn, NOW + timedelta(minutes=30))                 # 告警快照超過 10 分鐘
    assert r["W-a"]["verdict"] == "nodata" and "太舊" in r["W-a"]["reason"]
    _, r, _ = _rows(conn, NOW + timedelta(hours=40))                   # 效能超過 30 小時
    assert r["W-c"]["verdict"] == "nodata" and "太舊" in r["W-c"]["reason"]


def test_範圍未設定_寫未設定(conn):
    dc.save_settings(conn, {"xserver_ips": [], "banqiao": []}, today=NOW.date())
    _, r, v = _rows(conn)
    assert r["W-c"]["verdict"] == "nodata" and "未設定" in r["W-c"]["reason"]
    assert r["B"]["verdict"] == "nodata" and "未設定" in r["B"]["reason"]
    assert any("未設定" in w for w in v["run"]["warnings"])


def test_GPT7_例外門檻_原因_到期_上限99_過期失效(conn):
    with pytest.raises(ValueError):
        dc.save_settings(conn, {"disk_exceptions": [{"ip": "10.99.1.1", "threshold": 99.5, "reason": "x", "expires": "2026-12-31"}]}, today=NOW.date())
    with pytest.raises(ValueError):
        dc.save_settings(conn, {"disk_exceptions": [{"ip": "10.99.1.1", "threshold": 92, "reason": "", "expires": "2026-12-31"}]}, today=NOW.date())
    with pytest.raises(ValueError):
        dc.save_settings(conn, {"disk_exceptions": [{"ip": "10.99.1.1", "threshold": 92, "reason": "x", "expires": "2026-11-01"}]}, today=NOW.date())
    dc.save_settings(conn, {"disk_exceptions": [{"ip": "10.99.1.1", "threshold": 92, "reason": "歸檔碟", "expires": "2026-11-20"}]}, today=NOW.date())
    _, r, _ = _rows(conn)
    assert "0 顆超過門檻" in r["W-f"]["value_text"]                       # 90% < 例外 92%
    _, r, v = _rows(conn, datetime(2026, 11, 21, 6, 30))                 # 到期後：照一般門檻（資料舊了也是無資料，看警示）
    assert any("到期" in w for w in v["run"]["warnings"])


def test_GPT8_批次確認只限新鮮正常列_事件記範圍(conn):
    conn.execute("UPDATE alert_event SET ended_at = '2026-11-11 06:00:00' WHERE wug_state = 'Maintenance'")   # 沒有維護中 → W-b 正常
    conn.commit()
    rid, r, _ = _rows(conn)
    out = dc.confirm_fresh_ok(conn, rid, "tester")
    assert out["updated"] == 1                                            # 只有 W-b
    ev = [e for e in dc.events(conn, rid) if e["action"] == "confirm_fresh_ok"][0]
    assert ev["detail"]["count"] == 1 and ev["detail"]["rows"] == ["W-b"] and ev["detail"]["data_at"]
    v = dc.run_view(conn, rid)
    assert {x["code"] for x in v["rows"] if x["result"]} == {"W-b"}


def test_確認_送出_更正_都留事件(conn):
    rid, r, _ = _rows(conn)
    with pytest.raises(ValueError):
        dc.confirm(conn, [r["W-a"]["id"]], "bad", "", "tester")           # 異常要說明
    with pytest.raises(ValueError):
        dc.submit(conn, rid, "tester")                                     # 還有沒確認的
    ids = [x["id"] for x in r.values()]
    dc.confirm(conn, ids, "ok", None, "tester")
    dc.confirm(conn, [r["W-a"]["id"]], "bad", "已通報負責單位", "tester")
    dc.confirm(conn, [r["VC-1"]["id"]], "ok", None, "tester", {"esxi_count": "20", "lun_free_gb": "800.5", "has_alert": False})
    with pytest.raises(ValueError, match="整數"):
        dc.confirm(conn, [r["VC-2"]["id"]], "ok", None, "tester", {"esxi_count": "2x"})
    with pytest.raises(ValueError, match="警示說明"):
        dc.confirm(conn, [r["VC-2"]["id"]], "ok", None, "tester", {"has_alert": True})
    dc.confirm(conn, [r["VC-2"]["id"]], "bad", "LUN 快滿", "tester", {"lun_free_gb": "120", "has_alert": True, "alert_note": "datastore 警示"})
    assert dc.run_view(conn, rid)["rows"][[x["code"] for x in dc.run_view(conn, rid)["rows"]].index("VC-1")]["extra_vals"] == {"esxi_count": 20, "lun_free_gb": 800.5, "has_alert": False}
    assert dc.submit(conn, rid, "tester")["submitted"]
    with pytest.raises(ValueError):
        dc.confirm(conn, [r["W-a"]["id"]], "ok", None, "tester")          # 送出後只能更正
    dc.correct(conn, r["W-a"]["id"], "ok", "誤報，已確認", "tester")
    acts = [e["action"] for e in dc.events(conn, rid)]
    assert acts[0] == "generate" and acts.count("submit") == 1 and acts[-1] == "correct"
    corr = dc.events(conn, rid)[-1]["detail"]
    assert corr["from"] == "bad" and corr["from_note"] == "已通報負責單位"


def test_GPT10_Excel_格式同現行_加稽核工作表(conn):
    rid, r, _ = _rows(conn)
    dc.confirm(conn, [x["id"] for x in r.values()], "ok", None, "tester")
    buf, name = dc.export_xlsx(conn, rid)
    wb = openpyxl.load_workbook(io.BytesIO(buf.getvalue()))
    assert wb.sheetnames == ["每日檢查", "稽核"]
    assert [c.value for c in wb["每日檢查"][1]] == dc.EXCEL_COLS
    au = [c.value for c in wb["稽核"][1]]
    assert {"系統判定", "資料時間", "來源批次"} <= set(au)
    assert wb["每日檢查"].max_row == len(r) + 1 and name.startswith("xServer每日檢查紀錄_2026-11-11")


def test_GPT1_排程_0630產生_0745提醒_0815升級_只送一次(conn, monkeypatch):
    dc.save_settings(conn, {"enabled": True, "signoff": True}, today=NOW.date())
    assert dc.tick(conn, datetime(2026, 11, 11, 6, 0)).get("generated") is None
    out = dc.tick(conn, NOW)
    assert out["generated"]
    assert "remind" not in dc.tick(conn, datetime(2026, 11, 11, 7, 30))
    assert dc.tick(conn, datetime(2026, 11, 11, 7, 46))["remind"] in ("recorded", "queued")
    assert dc.tick(conn, datetime(2026, 11, 11, 7, 47))["remind"] == "dup"
    assert dc.tick(conn, datetime(2026, 11, 11, 8, 16))["escalate"] in ("recorded", "queued")
    # 沒開就完全不動
    dc.save_settings(conn, {"enabled": False}, today=NOW.date())
    assert dc.tick(conn, datetime(2026, 11, 12, 6, 30)) == {"status": "disabled"}


def test_今天沒產生快照_通知(conn):
    dc.save_settings(conn, {"enabled": True, "signoff": True}, today=NOW.date())
    assert dc.tick(conn, datetime(2026, 11, 11, 6, 50))["missing"] in ("recorded", "queued")


def test_讀取不建表(tmp_path):
    p = tmp_path / "e.db"
    db.init_db(p)
    c = db.get_connection(p)
    assert dc.latest_run_id(c, "2026-11-11") is None and dc.events(c, 1) == []
    assert not dc._ready(c)


def test_verify_numbers_卡片等於下鑽(conn):
    rid, r, _ = _rows(conn)
    dc.confirm(conn, [r["W-a"]["id"]], "bad", "x", "tester")
    before = vn.FAILS
    vn.check_daily_check(conn)
    assert vn.FAILS == before


def test_API(conn, tmp_path):
    import api
    from fastapi.testclient import TestClient

    path = conn.execute("PRAGMA database_list").fetchone()[2]

    def _get_db():
        c = db.get_connection(Path(path))
        try:
            yield c
        finally:
            c.close()
    api.app.dependency_overrides[api.get_db] = _get_db
    api.app.dependency_overrides[api.require_auth] = lambda: {"username": "t", "role": "admin"}
    try:
        cl = TestClient(api.app)
        r = cl.get("/api/daily-check", params={"day": "2030-01-01"})
        assert r.status_code == 200 and r.json()["run"] is None
        rid = cl.post("/api/daily-check/generate").json()["run_id"]
        v = cl.get("/api/daily-check", params={"run_id": rid}).json()
        assert v["run"]["id"] == rid and v["counts"]["total"] == len(v["rows"])
        assert cl.post("/api/daily-check/confirm", json={"row_ids": [v["rows"][0]["id"]], "result": "bad"}).status_code == 400
        assert cl.get(f"/api/daily-check/{rid}/export.xlsx").status_code == 200
        assert cl.put("/api/daily-check/settings", json={"patch": {"cpu": 200}}).status_code == 400
    finally:
        api.app.dependency_overrides.clear()


def test_AD對時_自動_ok正常_warn異常_no_data無資料_明細逐台(conn, monkeypatch):
    import types
    fake = types.ModuleType("ad_time")
    state = {"v": {"status": "ok", "reason": None, "collected_at": "2026-11-11 06:00:00", "warn_seconds": 1,
                   "dcs": [{"name": "dc01", "ip": "10.99.1.1", "ok": True, "offset_s": 0.2, "note": None, "verdict": "normal"},
                           {"name": "dc02", "ip": "10.99.1.5", "ok": True, "offset_s": -0.1, "note": None, "verdict": "normal"}]}}
    fake.latest = lambda c: state["v"]
    monkeypatch.setitem(sys.modules, "ad_time", fake)
    _, r, _ = _rows(conn)
    assert r["O-2"]["verdict"] == "ok" and r["O-2"]["kind"] == "auto" and len(r["O-2"]["detail"]) == 2
    assert r["O-2"]["detail"][0]["serial"] == "H-1" and "時間差 +0.2 秒・正常" in r["O-2"]["detail"][0]["what"]
    state["v"] = {**state["v"], "status": "warn", "reason": "dc02 差 5 秒",
                  "dcs": [{**state["v"]["dcs"][0]}, {**state["v"]["dcs"][1], "offset_s": 5, "verdict": "warn"}]}
    _, r, _ = _rows(conn)
    assert r["O-2"]["verdict"] == "bad" and "1 台超過 1 秒" in r["O-2"]["value_text"]
    state["v"] = {"status": "no_data", "reason": "有 DC 沒回應", "collected_at": "2026-11-11 06:00:00", "warn_seconds": 1,
                  "dcs": [{"name": "dc03", "ip": "10.99.1.7", "ok": False, "offset_s": None, "note": "UDP 123", "verdict": "no_data"}]}
    _, r, _ = _rows(conn)
    assert r["O-2"]["verdict"] == "nodata" and r["O-2"]["reason"] == "有 DC 沒回應" and "1 台沒回應" in r["O-2"]["value_text"]


def test_AD對時_真的ad_time_沒資料就是無資料_模組不在也是無資料(conn, monkeypatch):
    _, r, _ = _rows(conn)
    assert r["O-2"]["verdict"] == "nodata" and r["O-2"]["reason"]
    monkeypatch.setitem(sys.modules, "ad_time", None)                   # 還沒上線（import 失敗）
    _, r, _ = _rows(conn)
    assert r["O-2"]["verdict"] == "nodata" and "還沒上線" in r["O-2"]["reason"]


def test_升級信_只寄設定的收件人_每次都記紀錄(conn, monkeypatch):
    import teams_notify as tn
    sent = []
    monkeypatch.setattr(tn, "smtp_missing", lambda c: [])
    monkeypatch.setattr(tn, "smtp_settings", lambda c: {"smtp_host": "mail.example.test", "mail_from": "w@example.test"})
    monkeypatch.setattr(dc, "MAIL_TRANSPORT", lambda smtp, url, payload: (sent.append((url, json.loads(payload))) or (200, "", "")))
    dc.save_settings(conn, {"enabled": True, "signoff": True}, today=NOW.date())
    dc.tick(conn, NOW)
    assert dc.tick(conn, datetime(2026, 11, 11, 8, 16))["escalate"] == "recorded"     # 沒設收件人：不寄、不記
    assert sent == [] and dc.mail_log(conn) == []
    dc.save_settings(conn, {"escalate_to": ["boss@example.test", "sp@example.test"]}, today=NOW.date())
    assert dc.tick(conn, datetime(2026, 11, 12, 6, 30)).get("generated")
    assert dc.tick(conn, datetime(2026, 11, 12, 8, 16))["escalate"].endswith("+mail:sent")
    assert sent[0][0] == "mailto:boss@example.test,sp@example.test"
    log = dc.mail_log(conn)
    assert len(log) == 1 and log[0]["ok"] == 1 and log[0]["kind"] == "escalate" and log[0]["recipients"] == "boss@example.test,sp@example.test"
    assert dc.tick(conn, datetime(2026, 11, 12, 8, 20))["escalate"] == "dup" and len(dc.mail_log(conn)) == 1   # 一天一封
    monkeypatch.setattr(tn, "smtp_missing", lambda c: ["SMTP 主機"])                     # SMTP 沒設定：記一筆失敗，不吞
    dc.tick(conn, datetime(2026, 11, 13, 6, 30))
    assert dc.tick(conn, datetime(2026, 11, 13, 8, 16))["escalate"].endswith("+mail:failed")
    assert dc.mail_log(conn)[0]["ok"] == 0 and "SMTP 主機" in dc.mail_log(conn)[0]["error"]


def test_首頁一行_今日狀態(conn):
    assert dc.today_status(conn, NOW)["text"] == "未產生"
    dc.save_settings(conn, {"signoff": True}, today=NOW.date())
    rid, r, _ = _rows(conn)
    st = dc.today_status(conn, NOW)
    assert st["state"] == "open" and st["text"].startswith(f"待確認 {len(r)} 列") and "系統異常" in st["text"]
    dc.confirm(conn, [x["id"] for x in r.values()], "ok", None, "t")
    dc.submit(conn, rid, "t")
    assert dc.today_status(conn, NOW)["text"].startswith("已送出 ")



def test_首頁一行_今天未產生附上一筆(conn):
    """S5（2.060.19）：今天還沒產生時附上一個有紀錄的日子，灰字標「MM-DD 的資料，今日尚未產生」。"""
    assert "prev" not in dc.today_status(conn, NOW)
    dc.save_settings(conn, {"signoff": True}, today=NOW.date())
    _rows(conn)
    st = dc.today_status(conn, datetime(2026, 11, 12, 6, 0))
    assert st["state"] == "none" and st["prev"]["day"] == "2026-11-11"
    assert st["prev"]["note"] == "2026-11-11 的資料，今日尚未產生" and st["prev"]["text"].startswith("待確認 ")


def test_試辦第幾個工作天_與確認進度三卡加總(conn):
    assert dc.trial_day(conn, NOW.date()) == 0
    _rows(conn)
    assert dc.trial_day(conn, NOW.date()) == 1
    assert dc.trial_day(conn, datetime(2026, 11, 17).date()) == 5          # 11/11（三）～11/17（二）：週一到週五共 5 天
    rid, r, _ = _rows(conn)
    dc.confirm(conn, [r["W-a"]["id"]], "bad", "x", "t")
    dc.confirm(conn, [r["W-b"]["id"]], "ok", None, "t")
    c = dc.run_view(conn, rid)["counts"]
    assert (c["result_bad"], c["result_ok"]) == (1, 1) and c["result_ok"] + c["result_bad"] + c["unconfirmed"] == c["total"]



# ---------------------------------------------------------------- 獨立審查（c6c80be0）
def test_P1_週末與假日_排程完全不動(conn):
    dc.save_settings(conn, {"enabled": True, "signoff": True, "holidays": ["2026-11-12", " "]}, today=NOW.date())
    assert dc.settings(conn)["holidays"] == ["2026-11-12"]
    assert dc.tick(conn, datetime(2026, 11, 14, 6, 30)) == {"status": "non_working"}     # 週六
    assert dc.tick(conn, datetime(2026, 11, 12, 6, 30)) == {"status": "non_working"}     # 設定的假日
    assert dc.tick(conn, datetime(2026, 11, 12, 8, 16)) == {"status": "non_working"}
    assert dc.latest_run_id(conn, "2026-11-14") is None and dc.latest_run_id(conn, "2026-11-12") is None
    with pytest.raises(ValueError):
        dc.save_settings(conn, {"holidays": ["11/12"]}, today=NOW.date())


def test_P2_時段_0930後不再通知_已送出的日子重新產生也不通知_晚產生不馬上升級(conn):
    dc.save_settings(conn, {"enabled": True, "signoff": True}, today=NOW.date())
    # 晚到的人工產生（08:20，已過升級時間）：不馬上升級
    rid = dc.generate(conn, "t", "manual", datetime(2026, 11, 11, 8, 20))
    assert "escalate" not in dc.tick(conn, datetime(2026, 11, 11, 8, 21))
    assert dc.tick(conn, datetime(2026, 11, 11, 9, 31))["status"] == "after_stop"
    # 隔天：排程產生、送出後又重新產生 → 不再提醒／升級
    d2 = datetime(2026, 11, 12, 6, 30)
    rid2 = dc.tick(conn, d2)["generated"]
    ids = [x["id"] for x in dc.run_view(conn, rid2)["rows"]]
    dc.confirm(conn, ids, "ok", None, "t")
    dc.submit(conn, rid2, "t")
    dc.generate(conn, "t", "manual", datetime(2026, 11, 12, 7, 0))
    assert dc.tick(conn, datetime(2026, 11, 12, 7, 50))["status"] == "submitted"
    assert dc.tick(conn, datetime(2026, 11, 12, 8, 20))["status"] == "submitted"
    # 提醒只在提醒時段、升級只在升級時段
    d3 = datetime(2026, 11, 13, 6, 30)
    dc.tick(conn, d3)
    assert "remind" not in dc.tick(conn, datetime(2026, 11, 13, 7, 44))
    out = dc.tick(conn, datetime(2026, 11, 13, 8, 0))
    assert "remind" in out and "escalate" not in out
    out = dc.tick(conn, datetime(2026, 11, 13, 9, 0))
    assert "escalate" in out and "remind" not in out
    assert "escalate" not in dc.tick(conn, datetime(2026, 11, 13, 9, 30))


def test_P2_寄信先記sending_失敗下一輪最多再試2次_每次都記(conn, monkeypatch):
    import teams_notify as tn
    calls = []

    def tr(smtp, url, payload):
        calls.append(url)
        seen = conn.execute("SELECT mail_status FROM daily_check_notice WHERE kind = 'escalate'").fetchone()
        assert seen and seen[0] in ("sending", "failed")                      # 寄之前通知列已經在了
        return None, "OSError", "connect"
    monkeypatch.setattr(tn, "smtp_missing", lambda c: [])
    monkeypatch.setattr(tn, "smtp_settings", lambda c: {"smtp_host": "mail.example.test", "mail_from": "w@example.test"})
    monkeypatch.setattr(dc, "MAIL_TRANSPORT", tr)
    dc.save_settings(conn, {"enabled": True, "signoff": True, "escalate_to": ["boss@example.test"]}, today=NOW.date())
    dc.tick(conn, NOW)
    for m in range(16, 22):
        dc.tick(conn, datetime(2026, 11, 11, 8, m))
    assert len(calls) == 3                                                     # 第一次＋再試 2 次
    log = dc.mail_log(conn)
    assert len(log) == 3 and all(x["ok"] == 0 for x in log)
    n = conn.execute("SELECT mail_status, mail_attempts FROM daily_check_notice WHERE kind = 'escalate'").fetchone()
    assert tuple(n) == ("failed", 3)


def test_P2_送出只收當天最新一次_通知失敗不讓送出變錯誤(conn, monkeypatch):
    rid, r, _ = _rows(conn)
    rid2, r2, _ = _rows(conn)
    dc.confirm(conn, [x["id"] for x in r.values()], "ok", None, "t")
    with pytest.raises(ValueError, match="最新"):
        dc.submit(conn, rid, "t")
    dc.confirm(conn, [x["id"] for x in r2.values()], "ok", None, "t")

    def boom(*a, **k):
        raise RuntimeError("通知壞了")
    monkeypatch.setattr(dc, "_notify", boom)
    assert dc.submit(conn, rid2, "t") == {"submitted": True}
    assert dc.run_view(conn, rid2)["run"]["status"] == "submitted"


def test_P2_設定的IP全部沒資料_是無資料不是參考(conn):
    dc.save_settings(conn, {"xserver_ips": ["10.99.9.1", "10.99.9.2"]}, today=NOW.date())
    _, r, _ = _rows(conn)
    for k in ("W-c", "W-d", "W-f", "W-g"):
        assert r[k]["verdict"] == "nodata" and "都沒有" in r[k]["reason"], k


def test_P2_帶時區的收集時間正確換算():
    assert dc._parse("2026-11-10T08:00:00Z") == datetime(2026, 11, 10, 16, 0)
    assert dc._parse("2026-11-10T16:00:00+08:00") == datetime(2026, 11, 10, 16, 0)
    assert dc._parse("2026-11-10 16:00:00") == datetime(2026, 11, 10, 16, 0)
    assert dc._parse("bad") is None


def test_P2_非管理員只看精簡設定_寄送紀錄限管理員():
    import access_policy as ap
    assert ap.level_for("GET", "/api/daily-check/mail-log") == "admin"



def test_P2_設定API_非管理員看不到IP與收件人(conn):
    import api
    from fastapi.testclient import TestClient

    dc.save_settings(conn, {"escalate_to": ["boss@example.test"]}, today=NOW.date())
    path = conn.execute("PRAGMA database_list").fetchone()[2]

    def _get_db():
        c = db.get_connection(Path(path))
        try:
            yield c
        finally:
            c.close()
    api.app.dependency_overrides[api.get_db] = _get_db
    try:
        api.app.dependency_overrides[api.require_auth] = lambda: {"username": "u1", "role": "user"}
        s = TestClient(api.app).get("/api/daily-check/settings").json()["settings"]
        assert s.get("limited") and "xserver_ips" not in s and "escalate_to" not in s and "banqiao" not in s and s["cpu"] == 75.0
        api.app.dependency_overrides[api.require_auth] = lambda: {"username": "admin", "role": "admin"}
        s = TestClient(api.app).get("/api/daily-check/settings").json()["settings"]
        assert s["escalate_to"] == ["boss@example.test"] and "10.99.1.1" in s["xserver_ips"]
    finally:
        api.app.dependency_overrides.clear()


def test_SWAP_有項目才判_超過門檻要人看_不算進硬碟(conn):
    _disk(conn, "10.99.1.1", YDAY, "Swap space", 95)
    _disk(conn, "10.99.1.2", YDAY, "Virtual memory", 10)
    conn.commit()
    rid, r, v = _rows(conn)
    assert r["W-e"]["verdict"] == "ref" and "1 台超過 90%" in r["W-e"]["value_text"] and "2 個 SWAP 項目" in r["W-e"]["value_text"]
    assert r["W-e"]["detail"][0]["name"] == "app01"
    assert "1 顆超過門檻" in r["W-f"]["value_text"]          # SWAP 95% 不算成硬碟滿（只有 C: 90% 那顆）
    dc.save_settings(conn, {"swap": 99}, today=NOW.date())
    rid, r, v = _rows(conn)
    assert "0 台超過 99%" in r["W-e"]["value_text"]


def test_SWAP_WhatsUp沒有就用健康檢查快照_都沒有寫不知道(conn):
    """S8（2.060.20）：WhatsUp 磁碟資料沒有 Swap → 退到納管健康檢查快照（近 14 天）；都沒有＝不知道，不是 0。"""
    import health_baseline as hb
    rid, r, v = _rows(conn)
    assert r["W-e"]["verdict"] == "nodata" and r["W-e"]["reason"].startswith("不知道")
    conn.execute("CREATE TABLE IF NOT EXISTS health_snapshot (id INTEGER PRIMARY KEY AUTOINCREMENT, ip TEXT NOT NULL, taken_at TEXT NOT NULL, "
                 "source TEXT, overall TEXT, reachable INTEGER, ports_json TEXT, disks_json TEXT, mem_pct REAL, load_per REAL)")
    hb.ensure_swap_col(conn)                                                   # 舊庫補欄
    conn.executemany("INSERT INTO health_snapshot (ip, taken_at, source, swap_pct) VALUES (?,?,?,?)",
                     [("10.99.1.1", "2026-11-08 02:00:00", "weekly", 95.5), ("10.99.1.2", "2026-11-08 02:00:00", "weekly", 3.0),
                      ("10.99.1.3", "2026-10-01 02:00:00", "weekly", 99.0)])   # 超過 14 天不採用
    conn.commit()
    rid, r, v = _rows(conn)
    assert r["W-e"]["verdict"] == "ref" and "1 台超過 90%" in r["W-e"]["value_text"] and "2／3 台" in r["W-e"]["value_text"]
    assert "其餘 1 台不知道" in r["W-e"]["reason"] and r["W-e"]["detail"][0]["name"] == "app01"
    assert hb._swap_of({"mem": {"swap_total_kb": 0, "swap_pct": 0.0}}) is None and hb._swap_of({"mem": {"swap_total_kb": 10, "swap_pct": 5.0}}) == 5.0


# ── 2.060.26：WhatsUp 心跳算新鮮、1-xServer 沒手填用第一類正式主機 ──
def test_完整掃描後的心跳也算新鮮(conn):
    c = conn
    c.execute("DELETE FROM alert_scan")
    _scan(c, NOW - timedelta(minutes=30))
    assert not dc.wug_source(c, dc.settings(c), NOW)["ok"]                         # 只有 30 分鐘前的掃描：太舊
    c.execute("INSERT INTO alert_scan (batch_id, snap_at, kind, complete, received_at) VALUES ('hb1', ?, 'heartbeat', 0, ?)",
              ((NOW - timedelta(minutes=1)).strftime(dc.FMT), NOW.strftime(dc.FMT)))
    src = dc.wug_source(c, dc.settings(c), NOW)
    assert src["ok"] and src["block"]["last_seen"] == (NOW - timedelta(minutes=1)).strftime(dc.FMT)
    # 比掃描舊的心跳不算
    c.execute("DELETE FROM alert_scan WHERE kind = 'heartbeat'")
    c.execute("INSERT INTO alert_scan (batch_id, snap_at, kind, complete, received_at) VALUES ('hb0', ?, 'heartbeat', 0, ?)",
              ((NOW - timedelta(minutes=40)).strftime(dc.FMT), NOW.strftime(dc.FMT)))
    assert not dc.wug_source(c, dc.settings(c), NOW)["ok"]


def test_1xServer沒手填_用第一類正式主機(conn):
    c = conn
    c.execute("CREATE TABLE IF NOT EXISTS business_system (api_id TEXT PRIMARY KEY, name TEXT, sys_class TEXT, sys_class_override TEXT)")
    c.execute("INSERT OR REPLACE INTO business_system (api_id, name, sys_class) VALUES ('A-1','核心','S1-第一類系統軟體（核心）')")
    c.execute("INSERT OR REPLACE INTO business_system (api_id, name, sys_class) VALUES ('B-2','報表','S2-第二類系統軟體')")
    for serial, ip, api, envv in (("X1", "10.99.5.1", "A-1", "正式"), ("X2", "10.99.5.2", "A-1", "測試"), ("X3", "10.99.5.3", "B-2", "正式")):
        db.insert_hardware(c, asset_serial=serial, hostname=serial.lower(), ip=ip)
        c.execute("UPDATE hardware SET api_id = ?, environment = ? WHERE asset_serial = ?", (api, envv, serial))
    eff = dc.effective(c, {**dc.settings(c), "xserver_ips": []})
    assert eff["xserver_source"] == "auto" and eff["xserver_ips"] == ["10.99.5.1"]
    assert any("改用第一類" in w for w in dc.setting_warnings(eff, NOW.date()))
    assert dc.effective(c, {**dc.settings(c), "xserver_ips": ["10.99.1.1"]})["xserver_source"] == "manual"


def test_看板模式_預設不催送出_首頁講異常與無資料(conn):
    """2.066.11 使用者 11-14：每日檢查是看板。預設 signoff=False：不發 07:45 提醒、08:15 升級；首頁那一行講異常／無資料，不講「待確認」。"""
    assert dc.settings(conn)["signoff"] is False
    dc.save_settings(conn, {"enabled": True}, today=NOW.date())
    assert dc.tick(conn, datetime(2026, 11, 12, 6, 30)).get("generated")
    for hm in ((7, 50), (8, 20)):
        out = dc.tick(conn, datetime(2026, 11, 12, *hm))
        assert out["status"] == "dashboard" and "remind" not in out and "escalate" not in out
    st = dc.today_status(conn, datetime(2026, 11, 12, 9, 0))
    assert st["state"] == "board" and "待確認" not in st["text"]
    assert ("異常" in st["text"]) or st["text"].startswith("沒有異常")
    assert not any("升級通知收件人" in w for w in dc.setting_warnings(dc.effective(conn, dc.settings(conn)), NOW.date()))


def test_正常列一律有可下鑽的證據(conn):
    """使用者 11-14：「說它是 OK 的都要提供證據，點選的時候可以點細項」。W-a／W-b 0 台時明細列出判定根據的那次掃描；
    任何系統判「正常」的列，明細都不可是空的。"""
    conn.execute("UPDATE alert_event SET ended_at = '2026-11-11 06:00:00'")
    conn.commit()
    _, r, _ = _rows(conn)
    assert r["W-a"]["verdict"] == "ok" and r["W-b"]["verdict"] == "ok"
    for k in ("W-a", "W-b"):
        w = r[k]["detail"][0]["what"]
        assert "最後一次完整掃描" in w and "群組" in w and "這次看到" in w and "0 台" in w and "批次" in w, w
    for code, row in r.items():
        if row["verdict"] == "ok":
            assert row["detail"], f"{code} 判正常但沒有可下鑽的證據"
