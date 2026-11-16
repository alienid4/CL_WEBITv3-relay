"""夜間速查 N2：daily_check.host_history——依主機查最近 7 天 1.5 每日檢查，每天一列不留空白，
分得出 正常／異常（哪一項、值）／沒收到資料／檢查失敗。測試資料虛構：10.99.x。"""
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import daily_check as dc  # noqa: E402
import db  # noqa: E402

TODAY = date(2026, 11, 15)
IP = "10.99.1.10"


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    dc.ensure(c)
    return c


def _run(c, day, rows, status="open"):
    rid = c.execute("INSERT INTO daily_check_run (day, created_at, trigger, status) VALUES (?,?, 'auto', ?)", (day, day + " 08:00:00", status)).lastrowid
    for i, (name, rule, verdict, reason, detail, result) in enumerate(rows, 1):
        c.execute("INSERT INTO daily_check_row (run_id, no, code, name, kind, rule, verdict, reason, detail_json, result) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (rid, i, f"c{i}", name, "auto", rule, verdict, reason, json.dumps(detail, ensure_ascii=False), result))
    c.commit()
    return rid


def test_七天每天一列_沒有檢查的日子寫沒收到資料(tmp_path):
    c = _conn(tmp_path)
    h = dc.host_history(c, IP, "S-1", 7, TODAY)
    assert [x["day"] for x in h] == [f"2026-11-{d:02d}" for d in range(15, 8, -1)]
    assert {x["state"] for x in h} == {"沒收到資料"} and all(x["reason"] for x in h)


def test_異常_寫哪一項跟值_用IP或序號都對得到(tmp_path):
    c = _conn(tmp_path)
    _run(c, "2026-11-15", [("磁碟使用率", "disk", "ref", None, [{"ip": IP, "serial": None, "name": "APPDB01", "what": "E: 91%"}], None)])
    _run(c, "2026-11-14", [("離線設備", "wug_down", "bad", None, [{"ip": None, "serial": "S-1", "name": "APPDB01", "what": "Down"}], None)])
    h = {x["day"]: x for x in dc.host_history(c, IP, "S-1", 7, TODAY)}
    assert h["2026-11-15"]["state"] == "異常" and h["2026-11-15"]["items"] == [{"name": "磁碟使用率", "what": "E: 91%"}]
    assert h["2026-11-14"]["state"] == "異常"


def test_別台的異常不算這台_沒列到這台算正常並說明(tmp_path):
    c = _conn(tmp_path)
    _run(c, "2026-11-15", [("磁碟使用率", "disk", "ref", None, [{"ip": "10.99.1.99", "serial": None, "what": "C: 99%"}], None),
                           ("離線設備", "wug_down", "ok", None, [{"ip": None, "serial": None, "name": "WhatsUp 告警快照", "what": "0 台"}], None)])
    x = dc.host_history(c, IP, "S-1", 1, TODAY)[0]
    assert x["state"] == "正常" and x["covered"] is False and "不在任何一項的明細" in x["reason"]


def test_每台狀態列_無資料或舊值算沒收到資料_正常算正常(tmp_path):
    c = _conn(tmp_path)
    _run(c, "2026-11-15", [("CPU", "perf_cpu", "ok", None, [{"ip": IP, "state": "舊值", "what": "上次輪詢 90 分鐘前"}], None)])
    _run(c, "2026-11-14", [("CPU", "perf_cpu", "ok", None, [{"ip": IP, "state": "正常", "what": "CPU 20%"}], None)])
    h = {x["day"]: x for x in dc.host_history(c, IP, None, 2, TODAY)}
    assert h["2026-11-15"]["state"] == "沒收到資料" and h["2026-11-15"]["items"][0]["what"].startswith("上次輪詢")
    assert h["2026-11-14"]["state"] == "正常" and h["2026-11-14"]["covered"] is True


def test_整個來源讀不到_算檢查失敗_來源沒進來算沒收到資料(tmp_path):
    c = _conn(tmp_path)
    _run(c, "2026-11-15", [("磁碟使用率", "disk", "nodata", "WhatsUp 磁碟中繼讀不到最新一批", [], None)])
    _run(c, "2026-11-14", [("磁碟使用率", "disk", "nodata", "WhatsUp 磁碟中繼今天沒有新批次", [], None)])
    _run(c, "2026-11-13", [("AD 對時", "ad_time", "nodata", "AD 對時資料來源還沒上線", [], None)])     # 不是全機隊規則：不影響這台
    h = {x["day"]: x for x in dc.host_history(c, IP, None, 3, TODAY)}
    assert h["2026-11-15"]["state"] == "檢查失敗"
    assert h["2026-11-14"]["state"] == "沒收到資料"
    assert h["2026-11-13"]["state"] == "正常"


def test_人工判正常的那一列不算異常_取送出的那一次(tmp_path):
    c = _conn(tmp_path)
    _run(c, "2026-11-15", [("磁碟使用率", "disk", "ref", None, [{"ip": IP, "what": "E: 91%"}], "ok")], status="submitted")
    _run(c, "2026-11-15", [("磁碟使用率", "disk", "ref", None, [{"ip": IP, "what": "E: 93%"}], None)])         # 之後又產生但沒送出
    x = dc.host_history(c, IP, None, 1, TODAY)[0]
    assert x["state"] == "正常" and x["submitted"] is True
