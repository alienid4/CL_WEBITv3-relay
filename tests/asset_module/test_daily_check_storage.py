"""1.5 每日檢查 Storage 段：設定 storage_devices 後每台一列 S-n；管理位址對到 Dell Unity 中繼匯入（unity_collect.ps1 v2，
有健康與告警）＝系統判定，對不到＝人工；沒設定＝照舊一列人工「S」。

涵蓋：v2 檔案的健康／告警解析（含 PowerShell 5.1 陣列包裝）、舊版 v1 檔照收（標沒收）、格式錯整批拒收、匯入只留最新一批、
每條判定路徑（正常／健康不健康／有效告警／太舊／健康未知／告警沒讀完／state 不確定／舊版腳本／沒收集＝人工）、
判正常的列明細一定有證據。全部假資料：*.example.test、192.0.2.x、10.99.x、假序號。
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import daily_check as dc  # noqa: E402
import db  # noqa: E402
import relay_import as ri  # noqa: E402
import storage_relay as sr  # noqa: E402
from test_daily_check import _rows, conn  # noqa: E402,F401  (conn＝每日檢查的共用 fixture)
from test_relay_unity import device  # noqa: E402

NOW = datetime(2026, 11, 11, 6, 30)
FRESH = "2026-11-11T05:00:00+08:00"
OLD = "2026-11-09T05:00:00+08:00"
MH = "10.99.7.10"


def alert(aid, state="Inactive", sev=4, msg="Power supply is operating normally.", ts="2026-11-10T03:00:00.000Z", ack=False):
    return {"id": aid, "state": state, "severity": sev, "timestamp": ts, "message": msg, "isAcknowledged": ack,
            "component": {"id": "psu_0", "resource": "powerSupply"}}


def v2(health=5, alerts=None, health_read=True, alerts_read=True, mh=MH, serial="FAKESN0007", **extra):
    """腳本 v2 的一台：v1 欄位＋health_read／health_error／health＋alerts_read／alerts_error／alerts。"""
    doc = device(mh=mh, serial=serial, collector_version="unity_collect v2")
    doc.update(health_read=health_read, health_error="" if health_read else "HTTP 500 from the Storage.",
               health=[{"id": "0", "name": "UNITY-A", "health": {"value": health, "descriptionIds": ["ALRT_X"],
                                                                 "descriptions": ["The system is operating normally."]}}] if health_read else [],
               alerts_read=alerts_read, alerts_error="" if alerts_read else "More than 40 pages for alert",
               alerts=alerts if alerts is not None else [alert("alert_1"), alert("alert_2")])
    doc.update(extra)
    return doc


def load(c, doc, collected=FRESH, batch="2026-11-11"):
    """跟 UnityAdapter 同一條路：parse_file → apply_arrays（同一個交易）。"""
    ri.ensure(c)
    a = sr.parse_file(doc)
    sr.apply_arrays(c, [a], collected, batch, "2026-11-11 06:00:00")
    c.commit()
    return a


def configure(c, devices=None, **kw):
    dc.save_settings(c, {"storage_devices": devices if devices is not None else [{"name": "主儲存", "ip": MH}], **kw}, today=NOW.date())


def srow(c, now=NOW):
    cfg = dc.effective(c, dc.settings(c))
    it = next(i for i in dc.load_items()["items"] if i["code"] == "S")
    return dc.storage_rows(c, cfg, it, now)


# ---------------------------------------------------------------- 解析
def test_v2解析_健康與告警_PS包裝也照收():
    doc = v2(alerts={"value": [alert("alert_1"), alert("alert_9", state="Active_Auto", sev=3, msg="Disk faulted")], "Count": 2})
    a = sr.parse_file(json.loads(json.dumps(doc)))
    assert a["health"]["status"] == "read" and a["health"]["cls"] == "ok" and a["health"]["text"] == "5（OK）"
    assert "operating normally" in a["health"]["desc"]
    assert a["alerts"]["status"] == "ok" and a["alerts"]["n"] == {"total": 2, "effective": 1, "resolved": 1, "uncertain": 0}
    assert a["collector_version"] == "unity_collect v2"


def test_v1檔照收_健康告警標沒收():
    a = sr.parse_file(device())
    assert a["health"]["status"] == "not_collected" and a["alerts"]["status"] == "not_collected"


def test_健康值_只翻譯明列的值():
    assert sr.classify_health(5)[0] == "ok"
    for v in (7, 10, 15, 20, 25, 30, "Degraded"):
        assert sr.classify_health(v)[0] == "bad", v
    for v in (0, 3, 99, None, True, "something"):
        assert sr.classify_health(v)[0] == "unknown", v


@pytest.mark.parametrize("mut", [
    lambda d: d.update(health_read="yes"),
    lambda d: d.update(alerts_read=1),
    lambda d: d.update(health=[{"health": {"value": 5}}, {"health": {"value": 5}}]),
    lambda d: d.update(health=[{"health": "OK"}]),
    lambda d: d.update(health=[{"health": {"value": [5]}}]),
    lambda d: d.update(alerts=["not an object"]),
    lambda d: d.update(alerts=[alert("a1", ack="no")]),
    lambda d: d.update(alerts=[alert("a1"), alert("a1")]),
    lambda d: d.update(alerts=[{**alert("a1"), "id": "../x"}]),
    lambda d: d.update(alerts=[{**alert("a1"), "message": 123}]),
    lambda d: d.update(alerts_read=True, alerts=None),
])
def test_格式錯_丟ValueError(mut):
    doc = v2()
    mut(doc)
    with pytest.raises(ValueError):
        sr.parse_file(doc)


def test_格式錯_中繼匯入整批拒收(tmp_path, monkeypatch):
    from test_relay_unity import write_batch
    import quiet_window as qw
    from test_relay_import import SAT

    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    c = db.get_connection()
    drop = tmp_path / "drop"
    drop.mkdir()
    monkeypatch.setattr(ri, "STABLE_SECONDS", -5)
    qw.set_clock(lambda: SAT(10))
    try:
        db.set_setting(c, ri.DIR_KEY, str(drop))
        db.set_setting(c, ri.ENABLED_KEY, "1")
        bad = v2(mh="unity-a.example.test")
        bad["alerts"] = [alert("a1", ack="maybe")]
        write_batch(drop, [bad])
        ri.run_scan(c, "manual", "admin")
        r = c.execute("SELECT status, reason_code FROM relay_import_batches WHERE source='unity'").fetchone()
        assert (r["status"], r["reason_code"]) == ("rejected", "bad_data")
        assert c.execute("SELECT COUNT(*) FROM storage_array").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM storage_relay_health").fetchone()[0] == 0
        # 好的 v2 批次：健康與告警跟著進來
        write_batch(drop, [v2(mh="unity-a.example.test")], folder="2026-11-07", collected="2026-11-07T21:05:00+08:00")
        ri.run_scan(c, "manual", "admin")
        h = c.execute("SELECT * FROM storage_relay_health").fetchone()
        assert h["health_status"] == "read" and h["health_class"] == "ok" and h["alert_status"] == "ok" and h["n_total"] == 2
    finally:
        qw.set_clock(None)
        c.close()


# ---------------------------------------------------------------- 匯入：只留最新一批、只存有效＋不確定的明細
def test_匯入只留最新一批_已解除只計數(conn):
    load(conn, v2(alerts=[alert("a1"), alert("a2", state="Active_Manual"), alert("a3", state=2)]))
    rows = conn.execute("SELECT alert_id, state_class FROM storage_relay_alert ORDER BY alert_id").fetchall()
    assert [(r[0], r[1]) for r in rows] == [("a2", "effective"), ("a3", "uncertain")]
    load(conn, v2(alerts=[alert("a1")]), collected="2026-11-11T05:30:00+08:00", batch="2026-11-11b")
    assert conn.execute("SELECT COUNT(*) FROM storage_relay_alert").fetchone()[0] == 0
    h = conn.execute("SELECT batch_id, n_total, n_effective FROM storage_relay_health").fetchall()
    assert [tuple(x) for x in h] == [("2026-11-11b", 1, 0)]


# ---------------------------------------------------------------- 每日檢查判定
def test_沒設定_照舊一列人工S(conn):
    _, r, _ = _rows(conn)
    assert r["S"]["verdict"] == "manual" and r["S"]["kind"] == "manual" and not any(k.startswith("S-") for k in r)


def test_正常_新鮮_健康OK_有效告警0_明細有證據(conn):
    load(conn, v2())
    configure(conn, [{"name": "主儲存", "ip": MH}, {"name": "備援 NAS", "ip": "nas-b.example.test"}])
    _, r, _ = _rows(conn)
    assert "S" not in r
    s1, s2 = r["S-1"], r["S-2"]
    assert s1["kind"] == "auto" and s1["rule"] == "storage_unity" and s1["verdict"] == "ok" and s1["fresh"] == 1
    assert s1["name"] == f"Storage：主儲存（{MH}）" and "健康 5（OK）" in s1["value_text"] and "共讀到 2 筆" in s1["value_text"]
    assert s1["batch"] == "2026-11-11" and s1["data_at"] == "2026-11-11 05:00:00" and s1["source"] == dc.STORAGE_SRC
    w = s1["detail"][0]["what"]
    for must in ("UNITY-A", "Unity 480", "FAKESN0007", "5（OK）", "operating normally", "有效 0", "已解除 2", "已讀到最後一頁",
                 "2026-11-11 05:00:00", "unity_collect v2", "批次 2026-11-11"):
        assert must in w, (must, w)
    # 沒收集的那台＝人工
    assert s2["kind"] == "manual" and s2["verdict"] == "manual" and "人工" in s2["reason"]


def test_健康不健康_異常(conn):
    load(conn, v2(health=20))
    configure(conn)
    (s,) = srow(conn)
    assert s["verdict"] == "bad" and "20（MAJOR）" in s["value_text"]


def test_有效告警_異常_明細逐筆列等級訊息時間(conn):
    load(conn, v2(alerts=[alert("a1"), alert("alert_77", state="Active_Auto", sev=3, msg="Disk DPE drive 0 faulted", ts="2026-11-11T04:10:00.000Z")]))
    configure(conn)
    (s,) = srow(conn)
    assert s["verdict"] == "bad" and "有效告警 1 筆" in s["value_text"]
    al = [d for d in s["detail"] if d["name"].startswith("告警")]
    assert len(al) == 1 and al[0]["name"] == "告警 3（ERROR）" and "Disk DPE drive 0 faulted" in al[0]["what"] and "alert_77" in al[0]["what"]
    assert al[0]["since"] == "2026-11-11T04:10:00.000Z"


def test_資料太舊_無資料(conn):
    load(conn, v2(), collected=OLD)
    configure(conn)
    (s,) = srow(conn)
    assert s["verdict"] == "nodata" and "太舊" in s["reason"] and "30 小時" in s["reason"] and s["detail"]
    configure(conn, storage_fresh_h=72)
    assert srow(conn)[0]["verdict"] == "ok"


def test_太舊即使有有效告警也是無資料(conn):
    load(conn, v2(alerts=[alert("a9", state="Active_Auto")]), collected=OLD)
    configure(conn)
    assert srow(conn)[0]["verdict"] == "nodata"


@pytest.mark.parametrize("health, why", [(0, "不在認得的清單"), (99, "不在認得的清單")])
def test_健康未知_無資料(conn, health, why):
    load(conn, v2(health=health))
    configure(conn)
    (s,) = srow(conn)
    assert s["verdict"] == "nodata" and why in s["reason"]


def test_健康讀不到_無資料(conn):
    load(conn, v2(health_read=False))
    configure(conn)
    (s,) = srow(conn)
    assert s["verdict"] == "nodata" and "讀取失敗" in s["reason"] and "HTTP 500" in s["reason"]


def test_告警沒讀完_無資料_但讀到的有效告警照判異常(conn):
    load(conn, v2(alerts_read=False, alerts=[]))
    configure(conn)
    (s,) = srow(conn)
    assert s["verdict"] == "nodata" and "告警讀取失敗" in s["reason"] and "40 pages" in s["reason"]
    load(conn, v2(alerts_read=False, alerts=[alert("a5", state="Active_Auto")]), collected="2026-11-11T05:10:00+08:00", batch="b2")
    (s,) = srow(conn)
    assert s["verdict"] == "bad" and "至少" in s["value_text"] and "只讀到一部分" in s["reason"]


def test_告警state不確定_無資料(conn):
    load(conn, v2(alerts=[alert("a1"), alert("a2", state=1), alert("a3", state="Updating")]))
    configure(conn)
    (s,) = srow(conn)
    assert s["verdict"] == "nodata" and "2 筆告警的狀態不確定" in s["reason"]
    assert sum(1 for d in s["detail"] if "狀態不確定" in d["what"] and d["name"].startswith("告警")) == 2


def test_舊版腳本沒有健康告警_無資料(conn):
    load(conn, device(mh=MH, serial="FAKESN0007"))
    configure(conn)
    (s,) = srow(conn)
    assert s["verdict"] == "nodata" and "舊版" in s["reason"]


def test_同位址有舊陣列_用最新收集的那台(conn):
    load(conn, v2(serial="OLDSN", health=25), collected="2026-11-10T05:00:00+08:00", batch="b0")
    load(conn, v2(serial="NEWSN"))
    configure(conn)
    (s,) = srow(conn)
    assert s["verdict"] == "ok" and "NEWSN" in s["detail"][0]["what"] and "OLDSN" in s["detail"][0]["what"]


def test_管理位址比對_大小寫與IP正規化(conn):
    load(conn, v2(mh="Unity-B.example.test"))
    configure(conn, [{"name": "", "ip": "unity-b.EXAMPLE.test"}])
    (s,) = srow(conn)
    assert s["kind"] == "auto" and s["name"] == "Storage：unity-b.example.test（unity-b.example.test）"


def test_設定驗證(conn):
    for bad in ([{"name": "x", "ip": ""}], [{"name": "x", "ip": "http://a/b"}], [{"name": "a", "ip": MH}, {"name": "b", "ip": MH}], ["10.99.1.1"]):
        with pytest.raises(ValueError):
            configure(conn, bad)
    with pytest.raises(ValueError):
        configure(conn, storage_fresh_h=0)
    configure(conn, [{"name": " 甲 ", "ip": " 10.99.7.11 "}])
    assert dc.settings(conn)["storage_devices"] == [{"name": "甲", "ip": "10.99.7.11"}]


def test_Storage列_判正常一律有可下鑽的證據(conn):
    """守門：系統判正常的 Storage 列，明細不可空，而且第一筆要寫健康值、告警數、收集時間、批次。"""
    load(conn, v2())
    load(conn, v2(mh="10.99.7.12", serial="FAKESN0008", alerts=[]))
    configure(conn, [{"name": "甲", "ip": MH}, {"name": "乙", "ip": "10.99.7.12"}])
    _, r, _ = _rows(conn)
    oks = [row for code, row in r.items() if code.startswith("S-") and row["verdict"] == "ok"]
    assert len(oks) == 2
    for row in oks:
        assert row["detail"], row["code"]
        w = row["detail"][0]["what"]
        assert "健康" in w and "告警共讀到" in w and "收集" in w and "批次" in w, w
    assert "共讀到 0 筆" in r["S-2"]["value_text"]


def test_讀取不建表(tmp_path):
    import sqlite3
    c = sqlite3.connect(tmp_path / "x.db")
    c.row_factory = sqlite3.Row
    try:
        assert sr.health_for_host(c, MH) is None
        assert c.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0
        sr.ensure(c)          # 舊庫只有 Unity 陣列表、還沒有健康表：照樣讀，健康＝None（每日檢查判無資料「要更新到 v2」）
        c.execute("DROP TABLE storage_relay_health")
        c.execute("INSERT INTO storage_array (serial, name, model, management_host, collected_at, batch_id) VALUES ('S1','A','M',?,?,'b')", (MH, FRESH))
        got = sr.health_for_host(c, MH)
        assert got["array"]["serial"] == "S1" and got["health"] is None and got["alerts"] == []
        (row,) = dc.storage_rows(c, {**dc.DEFAULTS, "storage_devices": [{"name": "甲", "ip": MH}]}, {"code": "S", "section": "Storage"}, NOW)
        assert row["verdict"] == "nodata" and "v2" in row["reason"] and row["detail"]
    finally:
        c.close()


def test_非管理員看不到Storage清單(conn):
    import api
    from fastapi.testclient import TestClient

    configure(conn)
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
        assert "storage_devices" not in s and s["storage_fresh_h"] == 30
        api.app.dependency_overrides[api.require_auth] = lambda: {"username": "admin", "role": "admin"}
        s = TestClient(api.app).get("/api/daily-check/settings").json()["settings"]
        assert s["storage_devices"] == [{"name": "主儲存", "ip": MH}]
    finally:
        api.app.dependency_overrides.clear()


def test_NOW常數跟共用fixture一致():
    import test_daily_check as tdc
    assert tdc.NOW == NOW and NOW - timedelta(hours=1, minutes=30) == datetime(2026, 11, 11, 5, 0)
