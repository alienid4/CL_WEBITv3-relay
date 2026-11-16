"""1.1 今日（R9）＋1.4 追蹤處理（R10），2.044.0。全部假資料（IP 用 192.0.2.x）。

驗收（需求表 R9／R10）：
  · 跨 00:00 後昨天的項目不列；0 件顯示 0，來源沒資料顯示未收集／未啟用（件數是空的，不是 0）
  · 長期待辦只留入口＋今日變化數
  · 追蹤處理每一類的摘要數＝下鑽列數；類別跟原頁同一份計算
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import incident  # noqa: E402
import quiet_window as qw  # noqa: E402
import v2_today  # noqa: E402
import wug_alert as wa  # noqa: E402

NOW = datetime(2026, 11, 4, 10, 30, 0)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    c = db.get_connection()
    qw.set_clock(lambda: NOW)
    yield c
    qw.set_clock(None)
    c.close()


def _alert(c, did, started, ended=None, tier="red", ip="192.0.2.10"):
    wa.ensure(c)
    c.execute("INSERT INTO alert_event (wug_device_id, monitor, device_name, ip, match, tier, started_at, ended_at, last_seen, created_at) "
              "VALUES (?, 'Ping', ?, ?, 'no_asset', ?, ?, ?, ?, ?)", (did, f"dev{did}", ip, tier, started, ended, started, started))
    c.commit()


def test_告警未啟用_不是0件(conn):
    t = v2_today.today(conn, NOW)
    assert t["alerts"]["status"] == "disabled" and t["alerts"]["n"] is None and t["alerts"]["label"] == "告警未啟用"
    assert next(s for s in t["sources"] if s["key"] == "alerts")["state"] == "未啟用"


def test_啟用但沒收到資料_未收集(conn):
    wa.ensure(conn)
    wa.save_settings(conn, {**wa.DEFAULTS, "enabled": True})
    conn.commit()
    t = v2_today.today(conn, NOW)
    assert t["alerts"]["status"] == "uncollected" and t["alerts"]["n"] is None


def test_告警兩段_現在仍在告警與今天發生過_數字等於明細(conn):
    """2.044.3（GPT A2＋B1，R9 驗收口徑改）：現在仍在告警＝不管哪天開始都列、昨天開始的標「昨日起」；
    今天發生過＝今天 00:00 後開始（含已恢復），列開始／恢復；兩段件數＝各自列數。"""
    wa.ensure(conn)
    wa.save_settings(conn, {**wa.DEFAULTS, "enabled": True})
    wa._set(conn, wa.K_LAST_RECV, "2026-11-04 10:29:00")
    conn.commit()
    _alert(conn, "1", "2026-11-03 23:50:00")                                  # 昨天開始、還開著 → 現在仍在告警（昨日起）
    _alert(conn, "2", "2026-11-04 08:00:00", ended="2026-11-04 08:10:00")     # 今天發生、已恢復 → 只在今天發生過
    _alert(conn, "4", "2026-11-04 09:30:00")                                  # 今天發生、還開著 → 兩段都有
    _alert(conn, "5", "2026-11-01 07:00:00")                                  # 更早開始、還開著 → 標日期
    _alert(conn, "3", "2026-11-04 09:00:00", tier="maint")                    # 維護中都不列
    a = v2_today.today(conn, NOW)["alerts"]
    assert a["status"] == "ok" and a["n"] == len(a["rows"]) == 3
    note = {r["wug_device_id"]: r["since_note"] for r in a["rows"]}
    assert note == {"1": "昨日起", "4": "", "5": "11-01 起"}
    d = a["today"]
    assert d["n"] == len(d["rows"]) == 2 and a["started_today"] == 2
    assert {r["wug_device_id"]: (r["ended_at"], r["open"]) for r in d["rows"]} == {"2": ("2026-11-04 08:10:00", False), "4": (None, True)}
    assert d["still_open"] == 1 and d["recovered"] == 1


def test_心跳逾時_資料中斷(conn):
    wa.ensure(conn)
    wa.save_settings(conn, {**wa.DEFAULTS, "enabled": True})
    wa._set(conn, wa.K_LAST_RECV, "2026-11-04 09:00:00")
    conn.commit()
    a = v2_today.today(conn, NOW)["alerts"]
    assert a["status"] == "stale" and "資料中斷" in a["label"]


def test_今天的事件_昨天的不列_0件是0(conn):
    t = v2_today.today(conn, NOW)
    assert t["incidents"]["n"] == 0 and t["incidents"]["status"] == "ok"
    incident.create_event(conn, "電力", "昨天的事件", "2026-11-03 10:00", None, "tester", mode="formal")
    conn.execute("UPDATE incident_event SET created_at = '2026-11-03 10:00:00'")
    incident.create_event(conn, "網路", "今天的事件", "2026-11-04 09:10", None, "tester", mode="drill")
    conn.execute("UPDATE incident_event SET created_at = '2026-11-04 09:10:00' WHERE title = '今天的事件'")
    conn.commit()
    t = v2_today.today(conn, NOW)
    assert [r["title"] for r in t["incidents"]["rows"]] == ["今天的事件"]
    assert t["incidents"]["n"] == 1


def test_資產異動_今天沒快照是未收集(conn):
    c = v2_today.today(conn, NOW)["changes"]
    assert c["status"] == "uncollected" and c["n"] is None and "未收集" in c["label"]


def test_開盤尖峰_今天檔案還沒到不放數字(conn):
    o = v2_today.today(conn, NOW)["opening"]       # 2.045.0：改讀跳板機收盤後送來的 Aria 檔
    assert o["status"] == "pending" and o["label"] == "今日資料尚未匯入（收盤後由跳板機送來）" and o["as_of"] is None and "top" not in o


def test_追蹤處理_摘要等於下鑽_長期待辦一行(conn):
    incident.create_event(conn, "電力", "還沒結案的舊事件", "2026-10-01 10:00", None, "tester", mode="formal")
    t = v2_today.track(conn, NOW)
    cats = {c["key"]: c for c in t["categories"]}
    assert cats["incident_open"]["n"] == 1 and cats["incident_open"]["high"] == 1
    for c in t["categories"]:
        assert c["n"] == sum(1 for r in t["rows"] if r["cat"] == c["key"])
    assert [r["__no"] for r in t["rows"]] == list(range(1, len(t["rows"]) + 1))
    assert cats["contract"]["status"] == "nodata"            # 沒匯保固：照列、標無資料，不消失
    home = v2_today.today(conn, NOW)["track"]
    assert home["total"] == t["total"] == 1


def test_A3_GET不寫入任何表(conn):
    incident.create_event(conn, "電力", "A", "2026-10-01 10:00", None, "t", mode="formal")
    v2_today.track(conn, NOW)
    v2_today.today(conn, NOW)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "track_snapshot" not in names and "track_daily" not in names
    assert v2_today.track(conn, NOW)["changes"]["new"] is None       # 沒快照：照實寫無資料


def test_A3_排程快照_每小時一份加盤後一份(conn):
    assert v2_today.snapshot_due(conn, datetime(2026, 11, 4, 9, 2)) == "hourly"
    v2_today.tick(conn, datetime(2026, 11, 4, 9, 2))
    assert v2_today.snapshot_due(conn, datetime(2026, 11, 4, 9, 40)) is None          # 同一小時不再寫
    assert v2_today.snapshot_due(conn, datetime(2026, 11, 4, 10, 1)) == "hourly"
    assert v2_today.snapshot_due(conn, datetime(2026, 11, 4, 15, 6)) == "close"      # 盤後一份
    v2_today.tick(conn, datetime(2026, 11, 4, 15, 6))
    assert v2_today.snapshot_due(conn, datetime(2026, 11, 4, 15, 30)) is None
    assert [r[0] for r in conn.execute("SELECT kind FROM track_snapshot ORDER BY id")] == ["hourly", "close"]


def test_A3_B2_今日新增與解除_讀快照_解除列得出是哪幾件(conn):
    a = incident.create_event(conn, "電力", "A", "2026-10-01 10:00", None, "t", mode="formal")
    v2_today.write_snapshot(conn, datetime(2026, 11, 3, 16, 0))                 # 昨天：A
    c = incident.create_event(conn, "主機", "C", "2026-11-04 01:00", None, "t", mode="formal")
    v2_today.write_snapshot(conn, datetime(2026, 11, 4, 2, 0))                  # 今天較早：A、C
    conn.execute("UPDATE incident_event SET status = 'closed' WHERE id IN (?, ?)", (a["id"], c["id"]))
    incident.create_event(conn, "網路", "B", "2026-11-04 08:00", None, "t", mode="formal")
    conn.commit()
    v2_today.write_snapshot(conn, datetime(2026, 11, 4, 10, 0))                 # 最新：B
    t = v2_today.track(conn, NOW)
    ch = t["changes"]
    assert ch["prev_day"] == "2026-11-03" and ch["new"] == 1 and ch["resolved"] == 2
    assert {x["target"]: x["was_in"] for x in ch["resolved_items"]} == {"A": "昨日", "C": "今日較早"}
    assert len(ch["resolved_items"]) == ch["resolved"]
    assert [r["target"] for r in t["rows"] if r["is_new"]] == ["B"]
    home = v2_today.today(conn, NOW)["track"]["changes"]
    assert home["new"] == 1 and home["resolved"] == 2


def test_首頁舊網址轉址_與追蹤處理頁():
    idx = (FE / "pages" / "v2" / "index.vue").read_text(encoding="utf-8")
    assert "route.query.tab === 'todo'" in idx and "navigateTo('/v2/track'" in idx
    assert "navigateTo('/v2/changes?tab=week'" in idx
    assert "/api/v2/today" in idx and "/api/v2/home'" not in idx       # 首頁不再讀舊的待處理事項
    tr = (FE / "pages" / "v2" / "track.vue").read_text(encoding="utf-8")
    assert "/api/v2/track" in tr and "layout: 'v2'" in tr
    ch = (FE / "pages" / "v2" / "changes.vue").read_text(encoding="utf-8")
    assert "k: 'week'" in ch and "/api/v2/home/week" in ch
