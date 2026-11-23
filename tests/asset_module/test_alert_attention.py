"""2.066.x「需要注意」與例行時段（使用者 11-13）。全部假資料（192.0.2.x、devN），不連外部。
涵蓋：滿 N 分→需要注意；N 分內恢復→不是；例行時段內壓下、時段結束還沒恢復→升為需要注意；建議例行要確認才生效；
紀錄不滿 14 天不建議；跨午夜同一個事件；心跳壞了顯示監控盲區；通知只在進入需要注意推一次、恢復推一次。"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import alert_attention as aa  # noqa: E402
import db  # noqa: E402
import quiet_window as qw  # noqa: E402
import v2_today  # noqa: E402
import verify_numbers as vn  # noqa: E402
import wug_alert as wa  # noqa: E402
from test_wug_alert import IP_CORE, IP_OTHER, api_env, dev, env, ev, feed, notices  # noqa: E402,F401 - env／api_env 是 fixture

NOW = datetime(2026, 11, 4, 10, 30, 0)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    c = db.get_connection()
    qw.set_clock(lambda: NOW)
    wa.ensure(c)
    wa.save_settings(c, {**wa.DEFAULTS, "enabled": True})
    wa._set(c, wa.K_LAST_RECV, "2026-11-04 10:29:00")
    c.commit()
    yield c
    qw.set_clock(None)
    c.close()


def _alert(c, did, started, ended=None, ip="192.0.2.10", monitor="Ping", tier="red", mk=None, state="Down", market=None, notified=None):
    cur = c.execute("INSERT INTO alert_event (wug_device_id, monitor, device_name, ip, machine_key, match, tier, started_at, ended_at, last_seen, "
                    "duration_min, created_at, wug_state, market, notified) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (did, monitor, f"dev{did}", ip, mk, "matched" if mk else "no_asset", tier, started, ended, ended or started, 0, started,
                     state, market, notified or ","))
    c.commit()
    return cur.lastrowid


def _ts(d: datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M:%S")


def _att(c, now=NOW):
    return v2_today.today(c, now)["alerts"]["attention"]


def _bucket(a, k):
    return next(b for b in a["buckets"] if b["key"] == k)


# ── 判斷 ──
def test_斷滿N分還沒恢復_列需要注意_未滿的不列(conn):
    long_ = _alert(conn, "1", _ts(NOW - timedelta(minutes=12)))
    young = _alert(conn, "2", _ts(NOW - timedelta(minutes=4)), ip="192.0.2.11")
    a = _att(conn)
    assert a["min"] == 10 and [r["id"] for r in a["rows"]] == [long_] and a["n"] == 1
    assert "已斷 12 分" in a["rows"][0]["judge_reason"]
    y = _bucket(a, "young")
    assert [r["id"] for r in y["rows"]] == [young] and "未滿門檻 10 分" in y["rows"][0]["judge_reason"]


def test_N分內自動恢復_不列需要注意_進摺疊區(conn):
    eid = _alert(conn, "1", _ts(NOW - timedelta(minutes=30)), ended=_ts(NOW - timedelta(minutes=25)))
    a = _att(conn)
    assert a["n"] == 0
    b = _bucket(a, "auto_recovered")
    assert [r["id"] for r in b["rows"]] == [eid] and b["n"] == 1 and "5 分內自動恢復" in b["rows"][0]["judge_reason"]


def test_門檻可設定(conn):
    _alert(conn, "1", _ts(NOW - timedelta(minutes=12)))
    aa.save_threshold(conn, 15)
    assert _att(conn)["n"] == 0
    with pytest.raises(ValueError):
        aa.save_threshold(conn, 241)


def test_例行時段內壓下_時段結束還沒恢復就升為需要注意(conn):
    eid = _alert(conn, "1", "2026-11-04 10:00:00")
    aa.add_window(conn, "ip:192.0.2.10", "09:55", "10:40", None, "", "admin", NOW)
    a = _att(conn)
    assert a["n"] == 0
    r = _bucket(a, "routine_now")["rows"][0]
    assert r["id"] == eid and "例行時段 09:55–10:40" in r["judge_reason"]
    later = NOW.replace(hour=10, minute=41)
    a2 = _att(conn, later)
    assert [x["id"] for x in a2["rows"]] == [eid]


def test_例行時段跨午夜與到期日(conn):
    assert aa.in_span("23:30", "00:30", datetime(2026, 11, 4, 0, 10)) and not aa.in_span("23:30", "00:30", datetime(2026, 11, 4, 1, 0))
    _alert(conn, "1", "2026-11-04 10:00:00")
    aa.add_window(conn, "ip:192.0.2.10", "09:55", "10:40", "2026-11-03", "", "admin", NOW)      # 已過期
    assert _att(conn)["n"] == 1


def test_例行時段新增修改刪除都留紀錄(conn):
    w = aa.add_window(conn, "mk1", "02:00", "02:30", None, "排程重開", "alice", NOW)
    aa.update_window(conn, w["id"], "02:00", "02:45", "2026-12-31", "排程重開", "bob", NOW)
    aa.delete_window(conn, w["id"], "carol", NOW)
    log = aa.window_log(conn, "mk1")
    assert [x["action"] for x in log] == ["delete", "update", "add"] and [x["who"] for x in log] == ["carol", "bob", "alice"]
    assert '"02:30"' in log[1]["before_json"] and '"02:45"' in log[1]["after_json"]
    assert aa.windows(conn, "mk1") == []
    with pytest.raises(ValueError):
        aa.add_window(conn, "mk1", "25:00", "02:00", None, "", "x", NOW)


# ── 建議例行 ──
def _daily(conn, days, hhmm="02:00", dur=6, ip="192.0.2.20", start_days_ago=None):
    for d in range(days):
        st = (NOW - timedelta(days=d + 1)).replace(hour=int(hhmm[:2]), minute=int(hhmm[3:]), second=0)
        _alert(conn, "9", _ts(st), ended=_ts(st + timedelta(minutes=dur)), ip=ip)
    if start_days_ago:
        st = NOW - timedelta(days=start_days_ago)
        _alert(conn, "9", _ts(st), ended=_ts(st + timedelta(minutes=1)), ip=ip, monitor="Disk")


def test_建議例行_要管理員確認才生效_拒絕不再建議(conn):
    _daily(conn, 12, start_days_ago=20)
    s = aa.suggestions(conn, NOW)
    assert len(s) == 1 and s[0]["host_key"] == "ip:192.0.2.20" and s[0]["days"] >= 10 and s[0]["start_hhmm"] <= "02:00" <= s[0]["end_hhmm"]
    # 還沒確認：明天 02:05 斷線照樣是需要注意（建議不會自己生效）
    tomorrow = NOW.replace(day=5, hour=2, minute=20)
    eid = _alert(conn, "9", "2026-11-05 02:05:00", ip="192.0.2.20")
    assert eid in [r["id"] for r in _att(conn, tomorrow)["rows"]]
    aa.decide(conn, s[0]["host_key"], s[0]["start_hhmm"], s[0]["end_hhmm"], "confirm", "admin", NOW)
    assert eid not in [r["id"] for r in _att(conn, tomorrow)["rows"]]
    assert aa.windows(conn, "ip:192.0.2.20")[0]["source"] == "suggested" and aa.suggestions(conn, NOW) == []


def test_建議例行_拒絕後不再建議(conn):
    _daily(conn, 12, start_days_ago=20)
    s = aa.suggestions(conn, NOW)[0]
    aa.decide(conn, s["host_key"], s["start_hhmm"], s["end_hhmm"], "reject", "admin", NOW)
    assert aa.suggestions(conn, NOW) == [] and aa.windows(conn) == []


def test_紀錄不滿14天不建議_天數不夠也不建議(conn):
    _daily(conn, 12)                                   # 最早一筆 12 天前
    assert aa.suggestions(conn, NOW) == []
    _daily(conn, 8, ip="192.0.2.21", start_days_ago=20)   # 滿 14 天但只有 8 天
    assert aa.suggestions(conn, NOW) == []


# ── 監控盲區 ──
def test_心跳壞了_顯示監控盲區不說沒事(conn):
    a = _att(conn)
    assert a["blind"] is None
    wa._set(conn, wa.K_LAST_RECV, "2026-11-04 09:00:00")
    conn.commit()
    a = _att(conn)
    assert a["blind"] and "監控盲區" in a["blind"] and "不代表沒事" in a["blind"]


# ── 加總＝下鑽（verify_numbers）──
def test_加總等於下鑽_verify全綠(conn):
    _alert(conn, "1", _ts(NOW - timedelta(minutes=30)))
    _alert(conn, "2", _ts(NOW - timedelta(minutes=3)), ip="192.0.2.11")
    _alert(conn, "3", _ts(NOW - timedelta(minutes=60)), ended=_ts(NOW - timedelta(minutes=58)), ip="192.0.2.12")
    a = _att(conn)
    home = v2_today.today(conn, NOW)["alerts"]
    open_b = sum(b["n"] for b in a["buckets"] if b["key"] in ("young", "collect", "routine_now"))
    assert a["n"] + open_b == home["n"] == 2
    assert sum(g["n"] for g in a["groups"]) == a["n"]
    before = vn.FAILS
    vn.RESULTS.clear()
    vn.check_alert_attention(conn)
    rs = [r for r in vn.RESULTS if r["page"] == "1.1 需要注意"]
    assert rs and vn.FAILS == before, [r for r in rs if r["ok"] is False]


# ── 事件身分與通知（走真的快照流程）──
def _min(c, n=10):
    aa.save_threshold(c, n, n)
    c.commit()


def _d(did, ip, mins=1):
    return dev(did, ip, monitors=[{"name": "Ping", "status": f"Down at least {mins} min"}])


def test_跨午夜同一個事件_重複快照不另開(env):
    c, drop, clock, _ = env
    t = datetime(2026, 11, 2, 23, 58, 0)
    feed(c, drop, clock, t, [_d("1", IP_CORE)])
    feed(c, drop, clock, t, [_d("1", IP_CORE)], name="wugalert_dup_same_time")     # 重複訊息
    feed(c, drop, clock, t + timedelta(minutes=7), [_d("1", IP_CORE)])           # 過了午夜
    rows = ev(c)
    assert len(rows) == 1 and rows[0]["ended_at"] is None and rows[0]["started_at"] < "2026-11-03"
    qw.set_clock(lambda: t + timedelta(minutes=7))
    a = v2_today.today(c, t + timedelta(minutes=7))["alerts"]
    assert [r["id"] for r in a["rows"]] == [rows[0]["id"]] and a["rows"][0]["since_note"] == "昨日起"


def test_通知_進入需要注意推一次_恢復推一次_其他不推(env):
    c, drop, clock, _ = env
    _min(c, 10)
    t0 = datetime(2026, 11, 2, 21, 0, 0)
    feed(c, drop, clock, t0, [_d("1", IP_CORE), _d("2", IP_OTHER)])
    assert notices(c, "open") == []                                        # 未滿 10 分不推（不分紅黃）
    feed(c, drop, clock, t0 + timedelta(minutes=4), [_d("1", IP_CORE)])     # 2 號 5 分內恢復
    feed(c, drop, clock, t0 + timedelta(minutes=11), [_d("1", IP_CORE)])
    o = notices(c, "open")
    assert len(o) == 1 and o[0]["events"] == 1 and "core01" in o[0]["title"]
    feed(c, drop, clock, t0 + timedelta(minutes=15), [_d("1", IP_CORE)])
    assert len(notices(c, "open")) == 1
    feed(c, drop, clock, t0 + timedelta(minutes=20), [])
    feed(c, drop, clock, t0 + timedelta(minutes=25), [])
    assert len(notices(c, "recover")) == 1
    assert [n["kind"] for n in notices(c) if n["kind"] in ("brief", "escalate", "digest")] == []   # 短暫恢復的 2 號沒有任何立即通知


def test_通知_例行時段內不推_時段結束還沒恢復才推(env):
    c, drop, clock, _ = env
    _min(c, 10)
    t0 = datetime(2026, 11, 2, 2, 0, 0)
    feed(c, drop, clock, t0, [_d("1", IP_CORE)])
    key = aa.host_key(ev(c)[0])
    aa.add_window(c, key, "01:55", "02:30", None, "", "admin", t0); c.commit()
    feed(c, drop, clock, t0 + timedelta(minutes=20), [_d("1", IP_CORE)])
    assert notices(c, "open") == []
    feed(c, drop, clock, t0 + timedelta(minutes=31), [_d("1", IP_CORE)])
    assert len(notices(c, "open")) == 1


# ── 反覆斷線 ──
def test_反覆斷線_不列需要注意_一台一行_同一次多個監控只算一次(conn):
    for h in (8, 9, 10):
        st = NOW.replace(hour=h, minute=0)
        for m in ("Ping", "HTTP"):
            _alert(conn, "1", _ts(st), ended=_ts(st + timedelta(minutes=2)), monitor=m)
    a = _att(conn)
    assert a["n"] == 0 and len(a["flapping"]) == 1
    f = a["flapping"][0]
    assert f["n"] == 3 and f["events"] == 6 and "今天斷了 3 次" in f["reason"]


def test_反覆斷線寫進每日彙整一行_次數變了換掉同一行(conn):
    import notice_digest as nd
    nd.set_time(conn, "08:00")
    for h in (8, 9, 10):
        st = NOW.replace(hour=h, minute=0)
        _alert(conn, "1", _ts(st), ended=_ts(st + timedelta(minutes=2)))
    rows = conn.execute("SELECT * FROM alert_event").fetchall()
    assert aa.flap_digest(conn, NOW, rows) == 1
    assert aa.flap_digest(conn, NOW, rows) == 0
    _alert(conn, "1", _ts(NOW.replace(minute=20)), ended=_ts(NOW.replace(minute=22)))
    aa.flap_digest(conn, NOW, conn.execute("SELECT * FROM alert_event").fetchall())
    lines = [i["line"] for i in nd.pending(conn) if i["source"] == "wug_flap"]
    assert len(lines) == 1 and "今天斷了 4 次" in lines[0]


# ── API 權限 ──
def test_API_例行時段_管理員才能寫_一般與唯讀只能看(api_env):
    c, drop, clock, client = api_env
    admin, alice, vera = client("admin"), client("alice"), client("vera")
    body = {"key": "ip:192.0.2.77", "start": "02:00", "end": "02:30"}
    for who in (alice, vera):
        assert who.post("/api/v2/routine", json=body).status_code == 403
        assert who.get("/api/v2/routine", params={"key": "ip:192.0.2.77"}).status_code == 200
    r = admin.post("/api/v2/routine", json=body)
    assert r.status_code == 200, r.text
    wid = r.json()["id"]
    assert admin.put(f"/api/v2/routine/{wid}", json={**body, "end": "02:40"}).status_code == 200
    g = admin.get("/api/v2/routine", params={"key": "ip:192.0.2.77"}).json()
    assert g["can_edit"] is True and g["windows"][0]["end_hhmm"] == "02:40" and [x["action"] for x in g["log"]] == ["update", "add"]
    assert admin.post("/api/v2/routine", json={**body, "start": "9:00"}).status_code == 400
    assert alice.delete(f"/api/v2/routine/{wid}").status_code == 403
    assert admin.delete(f"/api/v2/routine/{wid}").status_code == 200
    assert alice.put("/api/v2/routine-settings", json={"attention_min": 20}).status_code == 403
    assert admin.put("/api/v2/routine-settings", json={"attention_min": 20}).json()["attention_min"] == 20
    assert admin.get("/api/v2/routine/suggestions").status_code == 200
    t = admin.get("/api/v2/today").json()["alerts"]
    assert t["attention"]["min"] == 20 and "buckets" in t["attention"]
    r = admin.put("/api/v2/routine-settings", json={"attention_min": 10, "attention_open_min": 0}).json()
    assert r == {"attention_min": 10, "attention_open_min": 0, "longterm_hours": 24}
    assert admin.put("/api/v2/routine-settings", json={"attention_min": 10, "longterm_hours": 48}).json()["longterm_hours"] == 48
    assert admin.get("/api/v2/routine", params={"key": "ip:192.0.2.77"}).json()["attention_open_min"] == 0


# ── 審查修正（P2-1～P2-6、P3、開盤門檻）──
def test_P2_1_讀不到監控明細但設備是Down_照門檻列需要注意(conn):
    eid = _alert(conn, "1", _ts(NOW - timedelta(minutes=12)), monitor=aa.MONREAD_MONITOR, state="Down")
    assert [r["id"] for r in _att(conn)["rows"]] == [eid]


def test_P2_1_讀不到監控明細且不是Down_60分內收集問題_超過列需要注意(conn):
    a1 = _alert(conn, "1", _ts(NOW - timedelta(minutes=30)), monitor=aa.MONREAD_MONITOR, state="Warning")
    a2 = _alert(conn, "2", _ts(NOW - timedelta(minutes=61)), monitor=aa.MONREAD_MONITOR, state="Warning", ip="192.0.2.11")
    a = _att(conn)
    assert [r["id"] for r in _bucket(a, "collect")["rows"]] == [a1] and [r["id"] for r in a["rows"]] == [a2]


def test_P2_2_時段開始前就斷的_不被例行時段壓下(conn):
    before = _alert(conn, "1", "2026-11-04 10:00:00")                   # 10:00 就斷
    inside = _alert(conn, "2", "2026-11-04 10:16:00", ip="192.0.2.11")    # 時段內才斷
    for ip in ("192.0.2.10", "192.0.2.11"):
        aa.add_window(conn, f"ip:{ip}", "10:15", "10:45", None, "", "admin", NOW)
    a = _att(conn)
    assert [r["id"] for r in a["rows"]] == [before]
    assert [r["id"] for r in _bucket(a, "routine_now")["rows"]] == [inside]


def test_P2_2_推過的事件進了例行時段也不會從需要注意消失(conn):
    eid = _alert(conn, "1", "2026-11-04 10:20:00", notified=",seen,open,")
    aa.add_window(conn, "ip:192.0.2.10", "10:15", "10:45", None, "", "admin", NOW)
    a = _att(conn)
    assert [r["id"] for r in a["rows"]] == [eid] and "已推過通知" in a["rows"][0]["judge_reason"]


def test_P2_2_跨午夜時段的凌晨部分_算前一天開始的那段(conn):
    w = {"start_hhmm": "23:30", "end_hhmm": "00:30"}
    assert aa.occ_start(w, datetime(2026, 11, 4, 0, 10)) == datetime(2026, 11, 3, 23, 30)
    assert aa.occ_start(w, datetime(2026, 11, 4, 23, 40)) == datetime(2026, 11, 4, 23, 30)
    assert aa.occ_start(w, datetime(2026, 11, 4, 1, 0)) is None


def test_P2_3_開始等於結束不接受(conn):
    with pytest.raises(ValueError):
        aa.add_window(conn, "mk1", "02:00", "02:00", None, "", "x", NOW)
    assert not aa.in_span("02:00", "02:00", NOW)


def test_開盤門檻另設_預設5分_0等於一斷就算(conn):
    o = _alert(conn, "1", _ts(NOW - timedelta(minutes=6)), market="開盤")         # 使用者 11-14：開盤 5 分
    n = _alert(conn, "2", _ts(NOW - timedelta(minutes=6)), market="非開盤", ip="192.0.2.11")
    a = _att(conn)
    assert [r["id"] for r in a["rows"]] == [o] and "開盤門檻 5 分" in a["rows"][0]["judge_reason"] and a["open_min"] == 5
    assert n in [r["id"] for r in _bucket(a, "young")["rows"]]
    aa.save_threshold(conn, 0, 10)
    assert [r["id"] for r in _att(conn)["rows"]] == [n]


def test_P3_已恢復的分類看有沒有推過(conn):
    pushed = _alert(conn, "1", _ts(NOW - timedelta(minutes=8)), ended=_ts(NOW - timedelta(minutes=5)), notified=",open,")
    long_ = _alert(conn, "2", _ts(NOW - timedelta(minutes=40)), ended=_ts(NOW - timedelta(minutes=20)), ip="192.0.2.11")
    a = _att(conn)
    rec = {r["id"]: r["judge_reason"] for r in _bucket(a, "recovered")["rows"]}
    assert set(rec) == {pushed, long_} and "已通知" in rec[pushed] and "沒在例行時段" in rec[long_]


def test_P3_反覆斷線彙整_主機鍵有萬用字元也只換掉自己那行(conn):
    import notice_digest as nd
    nd.set_time(conn, "08:00")
    for mk in ("a_1", "ab1"):
        for h in (8, 9, 10):
            st = NOW.replace(hour=h, minute=0)
            _alert(conn, "1", _ts(st), ended=_ts(st + timedelta(minutes=2)), mk=mk)
    aa.flap_digest(conn, NOW, conn.execute("SELECT * FROM alert_event").fetchall())
    _alert(conn, "1", _ts(NOW.replace(minute=20)), ended=_ts(NOW.replace(minute=22)), mk="a_1")
    aa.flap_digest(conn, NOW, conn.execute("SELECT * FROM alert_event").fetchall())
    lines = sorted(i["line"] for i in nd.pending(conn) if i["source"] == "wug_flap")
    assert len(lines) == 2 and any("〔a_1〕" in x and "4 次" in x for x in lines) and any("〔ab1〕" in x and "3 次" in x for x in lines)


def test_P2_6_verify_反向檢查會抓到漏列(conn, monkeypatch):
    _alert(conn, "1", _ts(NOW - timedelta(minutes=30)))
    vn.RESULTS.clear()
    vn.check_alert_attention(conn)
    assert all(r["ok"] for r in vn.RESULTS if r["page"] == "1.1 需要注意")
    orig = aa.judge_one
    monkeypatch.setattr(aa, "judge_one", lambda r, now, th, wmap: ("young", "x") if r["ended_at"] is None else orig(r, now, th, wmap))
    vn.RESULTS.clear()
    vn.check_alert_attention(conn)
    bad = [r["name"] for r in vn.RESULTS if r["page"] == "1.1 需要注意" and r["ok"] is False]
    assert any("反向" in x for x in bad)


def test_P2_4_升級前就開著的事件_第一次跑不推發生_首頁照列_之後新的照推(env):
    c, drop, clock, _ = env
    _min(c, 10)
    t0 = datetime(2026, 11, 2, 21, 0, 0)
    wa.ensure(c)
    c.execute("INSERT INTO alert_event (wug_device_id, monitor, device_name, ip, match, tier, started_at, last_seen, duration_min, created_at, notified) "
              "VALUES ('9', 'Ping', 'dev9', '192.0.2.99', 'no_asset', 'yellow', '2026-11-02 19:00:00', '2026-11-02 20:59:00', 0, '2026-11-02 19:00:00', ',seen,digest,')")
    c.commit()
    clock["now"] = t0
    wa.notify(c, t0)
    assert notices(c, "open") == []
    qw.set_clock(lambda: t0)
    assert len(v2_today.today(c, t0)["alerts"]["attention"]["rows"]) == 1
    feed(c, drop, clock, t0 + timedelta(minutes=1), [_d("1", IP_CORE, mins=15), _d("9", "192.0.2.99", mins=120)])
    o = notices(c, "open")
    assert len(o) == 1 and "core01" in o[0]["summary"] and "dev9" not in o[0]["summary"]


def _pre(c, did, ip, started, created, ended=None, monitor="Ping", state="Down"):
    c.execute("INSERT INTO alert_event (wug_device_id, monitor, device_name, ip, match, tier, started_at, ended_at, last_seen, duration_min, created_at, "
              "notified, wug_state) VALUES (?, ?, ?, ?, 'no_asset', 'yellow', ?, ?, ?, 0, ?, ',seen,', ?)",
              (did, monitor, f"dev{did}", ip, started, ended, ended or started, created, state))
    c.commit()


def test_P3_cutover時還沒滿門檻的舊事件_之後照常推發生與恢復(env):
    c, drop, clock, _ = env
    _min(c, 10)
    t0 = datetime(2026, 11, 2, 21, 0, 0)
    wa.ensure(c)
    _pre(c, "8", "192.0.2.98", "2026-11-02 20:55:00", "2026-11-02 20:55:00")       # cutover 時才斷 5 分
    _pre(c, "9", "192.0.2.99", "2026-11-02 19:00:00", "2026-11-02 19:00:00")       # cutover 時已斷 120 分
    clock["now"] = t0
    wa.notify(c, t0)                                                                  # 記下 cutover
    assert notices(c, "open") == []
    t1 = t0 + timedelta(minutes=6)
    clock["now"] = t1
    wa.notify(c, t1)
    o = notices(c, "open")
    assert len(o) == 1 and "dev8" in o[0]["summary"] and "dev9" not in o[0]["summary"]
    c.execute("UPDATE alert_event SET ended_at = ? WHERE wug_device_id IN ('8', '9')", ("2026-11-02 21:08:00",))
    c.commit()
    t2 = t0 + timedelta(minutes=9)
    clock["now"] = t2
    wa.notify(c, t2)
    rec = notices(c, "recover")
    assert len(rec) == 1 and "dev8" in rec[0]["summary"] and "dev9" not in rec[0]["summary"]
    assert notices(c, "brief") == []                                                  # 9 號：cutover 前就滿門檻，恢復也不補推


def test_P3_收集問題恢復了不推短暫事故_設備Down的照推(env):
    c, drop, clock, _ = env
    _min(c, 10)
    t0 = datetime(2026, 11, 2, 21, 0, 0)
    clock["now"] = t0
    wa.notify(c, t0)                                                                  # 先記 cutover，之後建立的都是新事件
    t1 = t0 + timedelta(minutes=30)
    _pre(c, "7", "192.0.2.97", "2026-11-02 21:01:00", "2026-11-02 21:01:00", ended="2026-11-02 21:20:00",
         monitor=wa.MONREAD_MONITOR, state="Warning")
    _pre(c, "6", "192.0.2.96", "2026-11-02 21:01:00", "2026-11-02 21:01:00", ended="2026-11-02 21:20:00",
         monitor=wa.MONREAD_MONITOR, state="Down")
    clock["now"] = t1
    wa.notify(c, t1)
    b = notices(c, "brief")
    assert len(b) == 1 and "dev6" in b[0]["summary"] and "dev7" not in b[0]["summary"]


# ── 每日摘要（Teams／Email 08:00）跟首頁同一份 ──
def _summary(conn, now=NOW):
    import teams_notify as tn
    with tn.read_only(conn):                                   # 摘要預覽／verify 都在唯讀連線下算：不可以寫庫
        title, card, nums = tn.build_summary(conn, dict(tn.DEFAULTS), "daily", now)
    import json
    return json.dumps(card, ensure_ascii=False), nums


def test_每日摘要_中繼模式_數字等於首頁_同範圍(conn):
    wa.save_settings(conn, {**wa.DEFAULTS, "enabled": True, "notify_policies": "主機組"}); conn.commit()
    conn.execute("UPDATE app_settings SET value = '2026-11-04 10:29:00' WHERE key = ?", (wa.K_LAST_RECV,)); conn.commit()
    _alert(conn, "1", _ts(NOW - timedelta(minutes=30)))
    _alert(conn, "2", _ts(NOW - timedelta(minutes=3)), ip="192.0.2.11")
    _alert(conn, "3", _ts(NOW - timedelta(minutes=60)), ended=_ts(NOW - timedelta(minutes=50)), ip="192.0.2.12")
    conn.execute("UPDATE alert_event SET policy = '其他組' WHERE wug_device_id = '2'"); conn.commit()   # 不在通知範圍
    text, nums = _summary(conn)
    home = v2_today.today(conn, NOW, record=False)["alerts"]
    assert nums["alerts_attention"] == home["attention"]["n"] == 1 and nums["alerts_now"] == home["n"] == 1
    assert "需要注意 1｜仍在告警 1" in text and "目前告警中" not in text
    assert nums["alerts_new"] == 2 and nums["alerts_cleared"] == 1        # 今天開始 1、3（2 不在範圍）；今天恢復 3


def test_每日摘要_監控盲區不給數字(conn):
    _alert(conn, "1", _ts(NOW - timedelta(minutes=30)))
    conn.execute("UPDATE app_settings SET value = '2026-11-04 08:00:00' WHERE key = ?", (wa.K_LAST_RECV,)); conn.commit()
    text, nums = _summary(conn)
    assert nums.get("alerts_blind") and "監控盲區，數字不可信" in text and "alerts_now" not in nums
    conn.execute("DELETE FROM app_settings WHERE key = ?", (wa.K_LAST_RECV,)); conn.commit()
    text, nums = _summary(conn)
    assert nums.get("alerts_blind") and "還沒收到任何" in text


def test_每日摘要_中繼沒啟用_照舊讀輪詢快取(conn):
    wa.save_settings(conn, {**wa.DEFAULTS, "enabled": False}); conn.commit()
    text, nums = _summary(conn)
    assert "需要注意" not in text and "alerts_attention" not in nums


def test_每日摘要_恢復含昨日起的口徑寫清楚(conn):
    _alert(conn, "1", "2026-11-03 23:00:00", ended="2026-11-04 01:00:00")      # 昨天開始、今天恢復
    text, nums = _summary(conn)
    assert nums["alerts_new"] == 0 and nums["alerts_cleared"] == 1
    assert "今日新增／今日恢復（含昨日起）" in text and "監控中斷前" not in text
    import teams_notify as tn
    import json
    with tn.read_only(conn):
        card = tn.build_summary(conn, dict(tn.DEFAULTS), "weekly", NOW)[1]
    assert "本週（近 7 天）恢復（含更早開始的）" in json.dumps(card, ensure_ascii=False)


def test_每日摘要_監控盲區時新增恢復標監控中斷前(conn):
    _alert(conn, "1", _ts(NOW - timedelta(minutes=200)), ended=_ts(NOW - timedelta(minutes=190)))
    conn.execute("UPDATE app_settings SET value = '2026-11-04 08:00:00' WHERE key = ?", (wa.K_LAST_RECV,)); conn.commit()
    text, nums = _summary(conn)
    assert "（監控中斷前）" in text and nums.get("alerts_blind")


def test_每日摘要_告警段讀取失敗_寫原因_其他段照樣有(conn, monkeypatch):
    import sqlite3
    import teams_notify as tn

    def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(tn, "_relay_alert_facts", boom)
    text, nums = _summary(conn)
    assert "讀取失敗：OperationalError: database is locked" in text and nums["alerts_error"]
    assert "新開事件" in text                                          # 後面的段落照樣組出來


# ── 長期未恢復（使用者 11-14）──
def test_長期未恢復_25小時移出需要注意_23小時還在需要注意(conn):
    old = _alert(conn, "1", _ts(NOW - timedelta(hours=25)))
    young = _alert(conn, "2", _ts(NOW - timedelta(hours=23)), ip="192.0.2.11")
    pushed_old = _alert(conn, "3", _ts(NOW - timedelta(hours=80)), ip="192.0.2.12", notified=",seen,open,")
    a = _att(conn)
    assert [r["id"] for r in a["rows"]] == [young] and a["n"] == 1 and a["long_h"] == 24
    lt = _bucket(a, "longterm")
    assert sorted(r["id"] for r in lt["rows"]) == sorted([old, pushed_old]) and lt["n"] == 2
    assert "超過 24 小時" in lt["rows"][0]["judge_reason"]
    home = v2_today.today(conn, NOW, record=False)["alerts"]
    open_sum = a["n"] + sum(b["n"] for b in a["buckets"] if b["key"] in ("longterm", "young", "collect", "routine_now"))
    assert open_sum == home["n"] == 3                                   # 加總＝下鑽：各類合起來＝還開著的全部
    vn.RESULTS.clear()
    before = vn.FAILS
    vn.check_alert_attention(conn)
    rs = [r for r in vn.RESULTS if r["page"] == "1.1 需要注意"]
    assert vn.FAILS == before and any("長期未恢復 =" in r["name"] for r in rs), [r for r in rs if r["ok"] is False]
    aa.save_threshold(conn, 10, 5, 100)
    assert _att(conn)["n"] == 3 and _bucket(_att(conn), "longterm")["n"] == 0
    with pytest.raises(ValueError):
        aa.save_threshold(conn, 10, 5, 0)


def test_長期未恢復_不再推發生_推過的恢復照推_沒推過的恢復不推(env):
    c, drop, clock, _ = env
    _min(c, 10)
    t0 = datetime(2026, 11, 2, 21, 0, 0)
    clock["now"] = t0
    wa.notify(c, t0)                                                     # 記 cutover
    _pre(c, "5", "192.0.2.95", "2026-11-01 20:00:00", "2026-11-02 21:00:30", monitor="Ping")     # 新建立就已斷 25 小時
    c.execute("UPDATE alert_event SET notified = ',seen,' WHERE wug_device_id = '5'")
    _pre(c, "4", "192.0.2.94", "2026-11-01 20:00:00", "2026-11-01 20:00:00", monitor="Ping")
    c.execute("UPDATE alert_event SET notified = ',seen,open,' WHERE wug_device_id = '4'"); c.commit()   # 早就推過發生
    t1 = t0 + timedelta(minutes=1)
    clock["now"] = t1
    wa.notify(c, t1)
    assert notices(c, "open") == []                                      # 長期未恢復不推發生
    c.execute("UPDATE alert_event SET ended_at = ? WHERE wug_device_id IN ('4', '5')", ("2026-11-02 21:02:00",)); c.commit()
    t2 = t0 + timedelta(minutes=3)
    clock["now"] = t2
    wa.notify(c, t2)
    rec = notices(c, "recover")
    assert len(rec) == 1 and "dev4" in rec[0]["summary"] and "dev5" not in rec[0]["summary"]
    assert notices(c, "brief") == []                                     # 沒推過的長期事件恢復不補推短暫事故


def test_長期未恢復_每日彙整一行台數(conn):
    import notice_digest as nd
    nd.set_time(conn, "08:00")
    for i in range(7):
        _alert(conn, str(i), _ts(NOW - timedelta(hours=30)), ip=f"192.0.2.{30 + i}")
    _alert(conn, "9", _ts(NOW - timedelta(hours=2)), ip="192.0.2.50")
    rows = conn.execute("SELECT * FROM alert_event").fetchall()
    assert aa.longterm_digest(conn, NOW, rows, 24) == 1 and aa.longterm_digest(conn, NOW, rows, 24) == 0
    lines = [i["line"] for i in nd.pending(conn) if i["source"] == "wug_longterm"]
    assert len(lines) == 1 and "長期未恢復（超過 24 小時）7 台" in lines[0] and "等 7 台" in lines[0]
