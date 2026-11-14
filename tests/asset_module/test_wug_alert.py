"""WhatsUp 告警事件（wug_alert.py＋relay_import 的 whatsup_alert adapter＋API）。

不連網路：告警快照批次在暫存資料夾現做（範例 IP 只用 192.0.2.x），Teams 一律走假 transport 或只看佇列。
釘住：事件開／更新／結案、不完整掃描不結案、心跳批次、Maintenance 不推、分級（第一類／環境缺漏往高／對不到資產）、
一個事件只推發生／升級／恢復、🟡 每小時摘要、監控中斷與恢復、對不到的 IP 列清單、舊批次不倒退、
一般中繼排程不碰告警批次而告警 tick 盤中照跑、月報加總＝下鑽、保存期清理、API 權限。
"""
import hashlib
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import credential_store  # noqa: E402
import db  # noqa: E402
import quiet_window as qw  # noqa: E402
import relay_import as ri  # noqa: E402
import teams_notify as tn  # noqa: E402
import wug_alert as wa  # noqa: E402

IP_CORE, IP_CORE_TEST, IP_OTHER, IP_NOENV, IP_NONE = "192.0.2.10", "192.0.2.11", "192.0.2.12", "192.0.2.13", "192.0.2.99"
REAL = "https://abc123def.4a.environment.api.powerplatform.com:443/powerautomate/automations/direct/workflows/0000/triggers/manual/paths/invoke?api-version=1&sv=1.0&sig=FAKEFAKE"
T0 = datetime(2026, 11, 2, 10, 0, 0)          # 週一 10:00（開盤時間）


@pytest.fixture(autouse=True)
def _no_confirm_wait(monkeypatch):
    """舊測試的情境都是「一斷就推」；2.061.12 預設要持續滿 10 分才推，這裡把預設改回 0，新測試自己設 confirm_min。"""
    monkeypatch.setitem(wa.DEFAULTS, "confirm_min", 0)
    import alert_attention
    monkeypatch.setattr(alert_attention, "DEFAULT_MIN", 0)          # 2.066.x：需要注意門檻也改回 0（舊情境「一斷就推」）
    monkeypatch.setattr(alert_attention, "DEFAULT_OPEN_MIN", 0)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(credential_store, "KEY_PATH_DEFAULT", str(tmp_path / "k.key"))
    monkeypatch.delenv("WEBIT_TEAMS_ALLOW_LOOPBACK", raising=False)
    db.init_db()
    c = db.get_connection()
    drop = tmp_path / "drop"
    drop.mkdir()
    monkeypatch.setattr(ri, "STABLE_SECONDS", -5)
    clock = {"now": T0}
    qw.set_clock(lambda: clock["now"])
    db.set_setting(c, ri.DIR_KEY, str(drop))
    db.set_setting(c, ri.ENABLED_KEY, "1")
    c.execute("CREATE TABLE IF NOT EXISTS business_system (api_id TEXT PRIMARY KEY, name TEXT, ap_department TEXT, ap_owner TEXT, sys_class TEXT, sys_class_override TEXT)")
    for aid, name, cls in (("A-001", "核心交易", "S1-第一類系統軟體（核心）"), ("B-002", "報表", "S2-第二類系統軟體")):
        c.execute("INSERT OR REPLACE INTO business_system (api_id, name, ap_department, ap_owner, sys_class, sys_class_override) VALUES (?,?,?,?,?,NULL)",
                  (aid, name, "資訊部", "王小明", cls))
    for serial, host, ip, api, envv in (("S-1", "core01", IP_CORE, "A-001", "正式"), ("S-2", "coret01", IP_CORE_TEST, "A-001", "測試"),
                                        ("S-3", "rpt01", IP_OTHER, "B-002", "正式"), ("S-4", "core02", IP_NOENV, "A-001", "")):
        c.execute("INSERT INTO hardware (asset_serial, hostname, ip, api_id, environment) VALUES (?,?,?,?,?)", (serial, host, ip, api, envv))
    c.commit()
    wa.ensure(c)
    wa.save_settings(c, {**wa.DEFAULTS, "enabled": True})
    c.commit()
    yield c, drop, clock, tmp_path / "t.db"
    qw.set_clock(None)
    c.close()


def dev(did, ip, state="Down", monitors=None, ok=True, name=None):
    return {"id": did, "name": name or f"dev{did}", "host_name": f"dev{did}.example.test", "ip": ip, "worst_state": state,
            "monitors_ok": ok, "monitors": monitors if monitors is not None else [{"name": "Ping", "type": "active", "status": "Down at least 5 min", "enabled": "True"}]}


SEQ = {"n": 0}


def snap_batch(drop, at: datetime, devices, kind="scan", complete=True, name=None, extra=None):
    SEQ["n"] += 1
    name = name or f"wugalert_{at.strftime('%Y%m%dT%H%M%S')}_{SEQ['n']:03d}"
    d = Path(drop) / name
    d.mkdir()
    snap = {"kind": kind, "group": {"totalDevices": 2150, "lastStateChangeUtc": at.isoformat()},
            "scan": {"complete": complete, "pages_read": 11 if complete else 2, "devices_seen": 2150 if complete else 400,
                     "stopped_reason": "" if complete else "time box 420 s reached"},
            "devices": devices, **(extra or {})}
    raw = json.dumps(snap).encode()
    (d / "snapshot.json").write_bytes(raw)
    m = {"source": "whatsup_alert", "batch_id": name, "schema_version": 1, "collector_version": "wug_alert_collect 1",
         "collected_at": at.strftime("%Y-%m-%dT%H:%M:%S+08:00"), "time_zone": "UTC", "range": {"from": at.strftime("%Y-%m-%d"), "to": at.strftime("%Y-%m-%d")},
         "count": 1, "files": [{"name": "snapshot.json", "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}]}
    (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    return d


def feed(c, drop, clock, at, devices, **kw):
    """放一批並讓告警 tick 跑一次（時鐘＝快照時間）。"""
    snap_batch(drop, at, devices, **kw)
    clock["now"] = at
    return wa.tick(c, at)


def ev(c, **w):
    q = "SELECT * FROM alert_event" + (" WHERE " + " AND ".join(f"{k} = ?" for k in w) if w else "") + " ORDER BY id"
    return [dict(r) for r in c.execute(q, tuple(w.values()))]


def notices(c, kind=None):
    rows = [dict(r) for r in c.execute("SELECT * FROM alert_notice ORDER BY id")]
    return [r for r in rows if kind is None or r["kind"] == kind]


# ── 事件生命週期 ──
def test_新問題開事件_還在就更新_完整掃描不見了就結案(env):
    c, drop, clock, _ = env
    feed(c, drop, clock, T0, [dev("1", IP_CORE)])
    e = ev(c)
    assert len(e) == 1 and e[0]["monitor"] == "Ping" and e[0]["ended_at"] is None and e[0]["machine_key"] and e[0]["match"] == "matched"
    assert e[0]["started_at"] == "2026-11-02 09:55:00"           # 「Down at least 5 min」往回推
    assert e[0]["system_name"] == "核心交易" and e[0]["owner"] == "王小明" and e[0]["market"] == "開盤"
    feed(c, drop, clock, T0 + timedelta(minutes=3), [dev("1", IP_CORE, monitors=[{"name": "Ping", "status": "Down at least 8 min"}])])
    e = ev(c)
    assert len(e) == 1 and e[0]["last_seen"] == "2026-11-02 10:03:00" and e[0]["duration_min"] == 8 and e[0]["status_text"] == "Down at least 8 min"
    feed(c, drop, clock, T0 + timedelta(minutes=6), [])
    e = ev(c)
    assert e[0]["ended_at"] == "2026-11-02 10:06:00" and e[0]["duration_min"] == 11
    feed(c, drop, clock, T0 + timedelta(minutes=9), [dev("1", IP_CORE)])           # 又壞：新的一筆，不是重開舊的
    assert len(ev(c)) == 2 and ev(c)[1]["ended_at"] is None


def test_不完整掃描與心跳批次絕不結案(env):
    c, drop, clock, _ = env
    feed(c, drop, clock, T0, [dev("1", IP_CORE), dev("2", IP_OTHER)])
    feed(c, drop, clock, T0 + timedelta(minutes=5), [dev("1", IP_CORE)], complete=False)      # 2 沒看到，但這次只掃了一半
    assert all(r["ended_at"] is None for r in ev(c))
    feed(c, drop, clock, T0 + timedelta(minutes=10), [], kind="heartbeat")
    rows = ev(c)
    assert all(r["ended_at"] is None for r in rows) and all(r["last_seen"] == "2026-11-02 10:10:00" for r in rows)
    scans = [dict(r) for r in c.execute("SELECT kind, complete FROM alert_scan ORDER BY id")]
    assert scans == [{"kind": "scan", "complete": 1}, {"kind": "scan", "complete": 0}, {"kind": "heartbeat", "complete": 0}]


def test_監控項目讀不到的設備_沿用既有事件_不另開設備層級_不結案(env):
    """GPT 審查 5：已有具名事件的設備這次讀不到監控項目 → 不開「(設備)」、不多推一則、不結案。"""
    c, drop, clock, _ = env
    feed(c, drop, clock, T0, [dev("1", IP_CORE)])
    feed(c, drop, clock, T0 + timedelta(minutes=2), [dev("1", IP_CORE, ok=False, monitors=[])])
    rows = ev(c)
    assert [(r["monitor"], r["ended_at"]) for r in rows] == [("Ping", None)] and rows[0]["last_seen"] == "2026-11-02 10:02:00"
    assert len(notices(c, "open")) == 1 and notices(c, "recover") == []


def test_第一次讀不到監控項目_開監控明細讀取失敗_最高黃色_之後讀到改名沿用並重新分級(env):
    """GPT 複核舊 5：首次出現就讀不到監控項目＝收集問題，不當設備故障（第一類正式機也不立即推）；讀到後改名沿用、該立即的才推。"""
    c, drop, clock, _ = env
    feed(c, drop, clock, T0, [dev("1", IP_CORE, state="Warning", ok=False, monitors=[])])   # 2.066.x：設備不是 Down 才當收集問題
    first = ev(c)
    assert [r["monitor"] for r in first] == [wa.MONREAD_MONITOR] and first[0]["tier"] == "yellow" and "監控明細讀取失敗" in first[0]["tier_reason"]
    assert notices(c, "open") == []
    feed(c, drop, clock, T0 + timedelta(minutes=2), [dev("1", IP_CORE)])
    rows = ev(c)
    assert len(rows) == 1 and rows[0]["id"] == first[0]["id"] and rows[0]["monitor"] == "Ping" and rows[0]["ended_at"] is None and rows[0]["tier"] == "red"
    assert len(notices(c, "open")) == 1 and notices(c, "recover") == []


def test_監控明細讀取失敗的事件不會因為久而升級成紅色(env):
    c, drop, clock, _ = env
    feed(c, drop, clock, T0, [dev("1", IP_CORE, state="Warning", ok=False, monitors=[])])
    feed(c, drop, clock, T0 + timedelta(minutes=30), [dev("1", IP_CORE, state="Warning", ok=False, monitors=[])])
    assert notices(c, "open") == []                                        # 設備不是 Down：60 分內當收集問題
    feed(c, drop, clock, T0 + timedelta(minutes=70), [dev("1", IP_CORE, state="Warning", ok=False, monitors=[])])
    r = ev(c)[0]
    assert r["tier"] == "yellow" and "升級" not in r["tier_reason"] and notices(c, "escalate") == []
    assert len(notices(c, "open")) == 1                                     # 審查 P2-1：超過 60 分不能再當收集問題


def test_群組狀態沒有totalDevices_完整掃描也當不完整(env):
    """GPT 複核舊 1：沒有總數就無法確認清單完整。"""
    c, drop, clock, _ = env
    feed(c, drop, clock, T0, [dev("1", IP_CORE)])
    feed(c, drop, clock, T0 + timedelta(minutes=2), [], extra={"group": {"lastStateChangeUtc": "x"}})
    assert ev(c)[0]["ended_at"] is None
    r = c.execute("SELECT complete, stopped_reason FROM alert_scan ORDER BY id DESC LIMIT 1").fetchone()
    assert r["complete"] == 0 and "totalDevices" in r["stopped_reason"]


def test_升級前已啟用_沒有啟用時間_tick補上現在_15分鐘後推監控未啟動(env):
    """GPT 複核舊 3：已啟用但從沒存過啟用時間的，不可永遠停在「從未收到」。"""
    c, drop, clock, _ = env
    c.execute("DELETE FROM app_settings WHERE key = ?", (wa.K_ENABLED_AT,)); c.commit()
    assert wa.heartbeat(c, T0 + timedelta(minutes=60))["status"] == "never"            # 沒有啟用時間：單看 heartbeat 判斷不了
    wa.tick(c, T0)
    assert c.execute("SELECT value FROM app_settings WHERE key = ?", (wa.K_ENABLED_AT,)).fetchone()[0] == "2026-11-02 10:00:00"
    wa.tick(c, T0 + timedelta(minutes=16))
    assert len(notices(c, "heartbeat_never")) == 1


def test_擱置以最初那一批為準_中途重現的移出_其餘滿5分鐘才結案(env):
    """GPT 複核新 1：7 筆被擱置，2 筆中途重現後剩 5 筆（不再 >5），也不可立刻結案。"""
    c, drop, clock, _ = env
    devs = [dev(str(i), "192.0.2.%d" % (50 + i)) for i in range(7)]
    feed(c, drop, clock, T0, devs)
    feed(c, drop, clock, T0 + timedelta(minutes=2), [])
    assert all(r["ended_at"] is None for r in ev(c))
    feed(c, drop, clock, T0 + timedelta(minutes=3), devs[:2])            # 0、1 又出現；2～6 還不見
    assert all(r["ended_at"] is None for r in ev(c))
    feed(c, drop, clock, T0 + timedelta(minutes=5), devs[:2])            # 擱置才 3 分鐘
    assert all(r["ended_at"] is None for r in ev(c))
    feed(c, drop, clock, T0 + timedelta(minutes=7), devs[:2])            # 擱置滿 5 分鐘
    closed = {r["wug_device_id"] for r in ev(c) if r["ended_at"]}
    assert closed == {"2", "3", "4", "5", "6"}
    assert all(r["ended_at"] is None for r in ev(c) if r["wug_device_id"] in ("0", "1"))


def test_擱置外零星恢復照常結案(env):
    c, drop, clock, _ = env
    devs = [dev(str(i), "192.0.2.%d" % (50 + i)) for i in range(9)]
    feed(c, drop, clock, T0, devs)
    feed(c, drop, clock, T0 + timedelta(minutes=2), devs[7:])           # 0～6 大量消失 → 擱置
    feed(c, drop, clock, T0 + timedelta(minutes=3), devs[8:])           # 7 零星恢復 → 照常結案
    assert {r["wug_device_id"] for r in ev(c) if r["ended_at"]} == {"7"}


def test_疑似收集異常的黃色通知30分鐘冷卻(env):
    """GPT 複核新 4：消失的那批每輪稍有變動也不可每輪推一則。"""
    c, drop, clock, _ = env
    devs = [dev(str(i), "192.0.2.%d" % (50 + i)) for i in range(8)]
    feed(c, drop, clock, T0, devs)
    feed(c, drop, clock, T0 + timedelta(minutes=2), [])                  # 擱置 → 第 1 則
    feed(c, drop, clock, T0 + timedelta(minutes=3), devs)                # 全部重現 → 解除擱置
    feed(c, drop, clock, T0 + timedelta(minutes=4), devs[:1])            # 又一批（不同）→ 擱置，但在冷卻內
    assert len(notices(c, "suspect")) == 1
    feed(c, drop, clock, T0 + timedelta(minutes=6), devs)                # 重現
    feed(c, drop, clock, T0 + timedelta(minutes=40), [])                 # 冷卻過了 → 第 2 則
    assert len(notices(c, "suspect")) == 2


def test_多個監控項目各自一筆_只有Down開頭的才算(env):
    c, drop, clock, _ = env
    mons = [{"name": "Ping", "status": "Up at least 5 min"}, {"name": "Disk C 90%", "status": "Down at least 20 min"},
            {"name": "192.0.2.41_UdpReceive.exe-1", "status": "Down at least 1 hour"}, {"name": "Old", "status": "Down at least 3 min", "enabled": "False"}]
    feed(c, drop, clock, T0, [dev("1", IP_OTHER, monitors=mons)])
    rows = ev(c)
    assert sorted(r["monitor"] for r in rows) == ["192.0.2.41_UdpReceive.exe-1", "Disk C 90%"]
    assert next(r for r in rows if r["monitor"].startswith("192"))["started_at"] == "2026-11-02 09:00:00"


def test_Maintenance_照記但不推(env):
    c, drop, clock, _ = env
    feed(c, drop, clock, T0, [dev("1", IP_CORE, state="Maintenance", monitors=[])])
    r = ev(c)[0]
    assert r["tier"] == "maint" and r["monitor"] == wa.MAINT_MONITOR
    assert notices(c) == []
    feed(c, drop, clock, T0 + timedelta(minutes=70), [dev("1", IP_CORE, state="Maintenance", monitors=[])])
    assert notices(c) == [] and ev(c)[0]["tier"] == "maint"            # 維護不會因為久就升級


def test_舊批次晚到不倒退(env):
    c, drop, clock, _ = env
    feed(c, drop, clock, T0 + timedelta(minutes=5), [])
    feed(c, drop, clock, T0, [dev("1", IP_CORE)])
    assert ev(c) == [] and c.execute("SELECT kind FROM alert_scan ORDER BY id DESC LIMIT 1").fetchone()[0] == "stale"


# ── 分級 ──
@pytest.mark.parametrize("ip,tier,why", [
    (IP_CORE, "red", "第一類・正式"), (IP_NOENV, "red", "環境資料缺漏"), (IP_CORE_TEST, "yellow", "第一類・測試"),
    (IP_OTHER, "yellow", "第二類・正式"), (IP_NONE, "red", "IP 對不到資產")])
def test_分級_第一類正式立即_資料缺漏往高(env, ip, tier, why):
    c, drop, clock, _ = env
    feed(c, drop, clock, T0, [dev("1", ip)])
    r = ev(c)[0]
    assert r["tier"] == tier and why in r["tier_reason"], r["tier_reason"]


def test_對不到資產可設成摘要但不能更低(env):
    c, drop, clock, _ = env
    wa.save_settings(c, {**wa.DEFAULTS, "enabled": True, "unmatched_tier": "yellow"}); c.commit()
    feed(c, drop, clock, T0, [dev("1", IP_NONE)])
    assert ev(c)[0]["tier"] == "yellow"
    with pytest.raises(ValueError):
        wa.validate({**wa.DEFAULTS, "unmatched_tier": "white"})


def test_對不到的IP列進清單_沒有IP的也列(env):
    c, drop, clock, _ = env
    feed(c, drop, clock, T0, [dev("1", IP_NONE), dev("2", "")])
    um = {r["ident"] for r in c.execute("SELECT ident FROM relay_import_unmatched WHERE source = 'whatsup_alert'")}
    assert IP_NONE in um and "(無 IP) wug:2" in um
    assert all(r["machine_key"] is None for r in ev(c))
    assert wa.status(c)["unmatched"] == 2


def test_開盤與非開盤只改標示(env):
    c, drop, clock, _ = env
    night = datetime(2026, 11, 2, 22, 0, 0)
    feed(c, drop, clock, night, [dev("1", IP_CORE, monitors=[{"name": "Ping", "status": "Down at least 1 min"}])])
    r = ev(c)[0]
    assert r["market"] == "非開盤" and r["tier"] == "red"
    assert len(notices(c, "open")) == 1 and "非開盤" in notices(c, "open")[0]["title"]


# ── 通知 ──
def test_紅色一個事件只推發生與恢復各一次_第一行是結論(env):
    c, drop, clock, _ = env
    feed(c, drop, clock, T0, [dev("1", IP_CORE)])
    o = notices(c, "open")
    assert len(o) == 1 and o[0]["status"] == "recorded"                    # Teams 沒啟用：只記錄
    t = o[0]["title"]
    assert t.startswith("🔴 core01（192.0.2.10）Ping Down，已 5 分｜A-001 核心交易｜王小明｜開盤")
    for k in (3, 6):
        feed(c, drop, clock, T0 + timedelta(minutes=k), [dev("1", IP_CORE)])
    assert len(notices(c, "open")) == 1                                      # 還在：不重推
    feed(c, drop, clock, T0 + timedelta(minutes=9), [])
    feed(c, drop, clock, T0 + timedelta(minutes=12), [])
    rec = notices(c, "recover")
    assert len(rec) == 1 and rec[0]["title"].startswith("✅ 已恢復 core01") and "共 14 分" in rec[0]["title"]


def test_兩次通知之間就恢復的紅色_未滿門檻不立即推任何一則_超過門檻推一則短暫事故(env):
    """GPT 審查 6：補匯積壓批次時已發生又恢復 → 不推矛盾的「發生」＋「恢復」。
    2.066.x：未滿門檻就恢復的不立即推；審查 P2-5：兩次排程之間超過門檻又恢復的，推一則「短暫事故（已恢復）」。"""
    c, drop, clock, _ = env
    _att(c, 10)
    snap_batch(drop, T0, [dev("1", IP_CORE)])
    snap_batch(drop, T0 + timedelta(minutes=1), [])
    clock["now"] = T0 + timedelta(minutes=1)
    wa.tick(c, clock["now"])
    assert notices(c, "open") == [] and notices(c, "recover") == [] and notices(c, "brief") == []
    wa.tick(c, clock["now"] + timedelta(minutes=1))
    assert notices(c) == []
    snap_batch(drop, T0 + timedelta(minutes=3), [dev("2", IP_CORE, monitors=[{"name": "HTTP", "status": "Down at least 15 min"}])])
    snap_batch(drop, T0 + timedelta(minutes=4), [])
    clock["now"] = T0 + timedelta(minutes=4)
    wa.tick(c, clock["now"])
    b = notices(c, "brief")
    assert len(b) == 1 and b[0]["title"].startswith("⚡ 短暫事故 core01") and notices(c, "open") == []
    wa.tick(c, clock["now"] + timedelta(minutes=1))
    assert len(notices(c, "brief")) == 1


def test_已恢復的事件不會再被當成發生或升級推出(env):
    """2.066.x：沒進過需要注意（門檻 120 分）就恢復 → 發生、升級、恢復都不推。"""
    c, drop, clock, _ = env
    wa.save_settings(c, {**wa.DEFAULTS, "enabled": True, "escalate_min": 90}); c.commit()
    _att(c, 120)
    feed(c, drop, clock, T0, [dev("1", IP_OTHER)])
    feed(c, drop, clock, T0 + timedelta(minutes=61), [dev("1", IP_OTHER)])        # 進摘要
    snap_batch(drop, T0 + timedelta(minutes=92), [dev("1", IP_OTHER)])               # 升級成紅（同批補匯）
    snap_batch(drop, T0 + timedelta(minutes=93), [])                                 # 接著恢復
    clock["now"] = T0 + timedelta(minutes=93)
    wa.tick(c, clock["now"])
    assert notices(c, "escalate") == [] and notices(c, "open") == [] and notices(c, "recover") == []


def test_同時多筆紅色合成一則但每台各自一行(env):
    c, drop, clock, _ = env
    feed(c, drop, clock, T0, [dev("1", IP_CORE), dev("2", IP_NOENV), dev("3", IP_NONE)])
    o = notices(c, "open")
    assert len(o) == 1 and o[0]["events"] == 3 and o[0]["title"].startswith("🔴 3 筆告警")
    assert all(x in o[0]["summary"] for x in ("core01", "core02", "dev3"))


def test_黃色走每小時摘要_升級成紅色不另推_斷滿需要注意門檻才推一次(env):
    """2.066.x：升級只改分級，不另推；只有進入需要注意（這裡門檻 120 分）才推一次發生、恢復一次。"""
    c, drop, clock, _ = env
    wa.save_settings(c, {**wa.DEFAULTS, "enabled": True, "escalate_min": 90}); c.commit()
    _att(c, 120)
    feed(c, drop, clock, T0, [dev("1", IP_OTHER, monitors=[{"name": "Ping", "status": "Down at least 1 min"}])])
    assert notices(c) == []                                                    # 黃色不立即推
    feed(c, drop, clock, T0 + timedelta(minutes=30), [dev("1", IP_OTHER)])
    assert notices(c) == []
    feed(c, drop, clock, T0 + timedelta(minutes=61), [dev("1", IP_OTHER)])  # 摘要時間到 → 摘要
    d = notices(c, "digest")
    assert len(d) == 1 and d[0]["tier"] == "yellow" and "rpt01" in d[0]["summary"]
    feed(c, drop, clock, T0 + timedelta(minutes=92), [dev("1", IP_OTHER)])  # 持續 93 分 → 升級
    r = ev(c)[0]
    assert r["tier"] == "red" and "升級" in r["tier_reason"]
    assert notices(c, "escalate") == [] and notices(c, "open") == []
    feed(c, drop, clock, T0 + timedelta(minutes=121), [dev("1", IP_OTHER)])
    assert len(notices(c, "open")) == 1 and notices(c, "escalate") == []
    feed(c, drop, clock, T0 + timedelta(minutes=125), [dev("1", IP_OTHER)])
    assert len(notices(c, "open")) == 1
    feed(c, drop, clock, T0 + timedelta(minutes=128), [])
    assert len(notices(c, "recover")) == 1


def test_還沒進摘要就升級的黃色_直接推一則紅色發生(env):
    c, drop, clock, _ = env
    feed(c, drop, clock, T0, [dev("1", IP_OTHER, monitors=[{"name": "Ping", "status": "Down at least 1 hour"}])])   # 一進來就超過 60 分
    r = ev(c)[0]
    assert r["tier"] == "red" and "升級" in r["tier_reason"]
    assert len(notices(c, "open")) == 1 and notices(c, "digest") == []


def test_黃色在一小時內發生又恢復_摘要寫一行已恢復(env):
    c, drop, clock, _ = env
    _att(c, 30)
    feed(c, drop, clock, T0, [dev("1", IP_OTHER)])
    feed(c, drop, clock, T0 + timedelta(minutes=10), [])
    clock["now"] = T0 + timedelta(minutes=61)
    wa.tick(c, clock["now"])
    d = notices(c, "digest")
    assert len(d) == 1 and "恢復 1 筆" in d[0]["title"] and "已恢復 rpt01" in d[0]["summary"]
    clock["now"] = T0 + timedelta(minutes=125)
    wa.tick(c, clock["now"])
    assert len(notices(c, "digest")) == 1 and notices(c, "recover") == []       # 不重送


def test_Teams啟用時排進既有佇列並送出(env):
    c, drop, clock, _ = env
    tn.set_webhook(c, REAL)
    tn.save_settings(c, {**tn.DEFAULTS, "enabled": True, "live_confirmed": True}); c.commit()
    feed(c, drop, clock, T0, [dev("1", IP_CORE)])
    rows = [dict(r) for r in c.execute("SELECT * FROM teams_notify_delivery WHERE kind LIKE 'wug_%'")]
    assert len(rows) == 1 and rows[0]["kind"] == "wug_open" and rows[0]["severity"] == "critical"
    assert notices(c, "open")[0]["status"] == "queued"
    calls = []
    tn.process_queue(c, tn.load_settings(c), clock["now"], lambda u, b: calls.append(b) or (202, "", ""))
    assert len(calls) == 1 and "core01" in calls[0].decode()
    assert wa.notices(c)[0]["delivery_status"] == "sent"


def test_未啟用時tick什麼都不做(env):
    c, drop, clock, _ = env
    wa.save_settings(c, {**wa.DEFAULTS, "enabled": False}); c.commit()
    snap_batch(drop, T0, [dev("1", IP_CORE)])
    assert wa.tick(c, T0) == {"status": "disabled"}
    assert ev(c) == []


# ── 心跳 ──
def test_心跳_從沒收到不判斷_超過10分推一次中斷_收到再推一次恢復(env):
    c, drop, clock, _ = env
    assert wa.heartbeat(c, T0)["status"] == "never"
    feed(c, drop, clock, T0, [])
    c.execute("UPDATE app_settings SET value = ? WHERE key = ?", ("2026-11-02 10:00:00", wa.K_LAST_RECV)); c.commit()
    assert wa.heartbeat(c, T0 + timedelta(minutes=9))["status"] == "ok"
    assert wa.heartbeat(c, T0 + timedelta(minutes=11))["status"] == "down_sent"
    assert wa.heartbeat(c, T0 + timedelta(minutes=20))["status"] == "down"            # 不重推
    assert len(notices(c, "heartbeat_down")) == 1 and notices(c, "heartbeat_down")[0]["tier"] == "red"
    assert wa.status(c, T0 + timedelta(minutes=20))["heartbeat"] == "down"
    feed(c, drop, clock, T0 + timedelta(minutes=21), [], kind="heartbeat")
    assert len(notices(c, "heartbeat_up")) == 1 and wa.heartbeat(c, T0 + timedelta(minutes=22))["status"] == "ok"


# ── 排程分工：一般中繼匯入不碰告警批次；告警 tick 盤中照跑 ──
def test_一般中繼排程不碰告警批次_告警tick開盤時間照跑(env):
    c, drop, clock, _ = env
    snap_batch(drop, T0, [dev("1", IP_CORE)])
    clock["now"] = T0                                                     # 週一 10:00：盤中靜默窗內
    assert qw.in_quiet_window(c)
    assert ri.pending_batches(c) == []                                    # 一般排程看不到它（不會記延後）
    assert ri.tick(c, T0)["status"] in ("idle", "not_due", "deferred_quiet_window")
    out = wa.tick(c, T0)
    assert out["import"]["imported"] == 1 and len(ev(c)) == 1
    assert c.execute("SELECT trigger FROM relay_import_batches WHERE source='whatsup_alert'").fetchone()[0] == "alert"


def test_人工立即匯入也會處理告警批次(env):
    c, drop, clock, _ = env
    snap_batch(drop, T0, [dev("1", IP_CORE)])
    out = ri.run_scan(c, "manual", "admin")
    assert out["imported"] == 1 and len(ev(c)) == 1


@pytest.mark.parametrize("mut,code", [
    (lambda s: s.update(kind="weird"), "bad_data"),
    (lambda s: s.update(devices=[{"name": "x"}]), "bad_data"),
    (lambda s: s.update(devices=[dict(dev("1", IP_CORE), access_token="abc")]), "secret_field"),
])
def test_壞快照整批拒收(env, mut, code):
    c, drop, clock, _ = env
    d = snap_batch(drop, T0, [])
    snap = json.loads((d / "snapshot.json").read_text())
    mut(snap)
    raw = json.dumps(snap).encode()
    (d / "snapshot.json").write_bytes(raw)
    m = json.loads((d / "manifest.json").read_text())
    m["files"][0].update(sha256=hashlib.sha256(raw).hexdigest(), size=len(raw))
    (d / "manifest.json").write_text(json.dumps(m))
    wa.tick(c, T0)
    b = c.execute("SELECT status, reason_code FROM relay_import_batches WHERE source='whatsup_alert'").fetchone()
    assert b["status"] == "rejected" and b["reason_code"] == code
    assert ev(c) == []


# ── GPT 審查 1：完整掃描的保險 ──
def test_收集端標完整但台數離群組總數太遠或一台都沒看到_當不完整不結案(env):
    c, drop, clock, _ = env
    feed(c, drop, clock, T0, [dev("1", IP_CORE)])
    feed(c, drop, clock, T0 + timedelta(minutes=2), [], extra={"scan": {"complete": True, "pages_read": 1, "devices_seen": 200, "stopped_reason": ""}})
    assert ev(c)[0]["ended_at"] is None
    feed(c, drop, clock, T0 + timedelta(minutes=4), [], extra={"scan": {"complete": True, "pages_read": 1, "devices_seen": 0, "stopped_reason": ""}})
    assert ev(c)[0]["ended_at"] is None
    sc = [dict(r) for r in c.execute("SELECT complete, stopped_reason FROM alert_scan ORDER BY id")]
    assert sc[1]["complete"] == 0 and "差太多" in sc[1]["stopped_reason"] and sc[2]["complete"] == 0 and "一台設備都沒看到" in sc[2]["stopped_reason"]


def test_一次完整掃描要結案超過一半且超過5筆_疑似收集異常先不結案_推一則黃色_5分後同一批才結案(env):
    c, drop, clock, _ = env
    ips = ["192.0.2.%d" % (50 + i) for i in range(7)]
    feed(c, drop, clock, T0, [dev(str(i), ip) for i, ip in enumerate(ips)])
    assert len(ev(c)) == 7
    feed(c, drop, clock, T0 + timedelta(minutes=2), [])
    assert all(r["ended_at"] is None for r in ev(c))
    s1 = notices(c, "suspect")
    assert len(s1) == 1 and s1[0]["tier"] == "yellow" and "疑似收集異常" in s1[0]["title"]
    assert "疑似收集異常" in c.execute("SELECT stopped_reason FROM alert_scan ORDER BY id DESC LIMIT 1").fetchone()[0]
    feed(c, drop, clock, T0 + timedelta(minutes=4), [])                      # 才 2 分鐘：還不當真，也不重推
    assert all(r["ended_at"] is None for r in ev(c)) and len(notices(c, "suspect")) == 1
    feed(c, drop, clock, T0 + timedelta(minutes=8), [])                      # 6 分鐘後仍是同一批 → 真的恢復
    assert all(r["ended_at"] == "2026-11-02 10:08:00" for r in ev(c))


# ── GPT 審查 2：Unknown ──
def test_Down轉Unknown不結案_新的Unknown開狀態未知事件並通知(env):
    c, drop, clock, _ = env
    feed(c, drop, clock, T0, [dev("1", IP_CORE)])
    feed(c, drop, clock, T0 + timedelta(minutes=2), [dev("1", IP_CORE, state="Unknown", monitors=[])])
    rows = {r["monitor"]: r for r in ev(c)}
    assert rows["Ping"]["ended_at"] is None                                  # 狀態未知不是恢復
    assert rows[wa.UNKNOWN_MONITOR]["wug_state"] == "Unknown" and rows[wa.UNKNOWN_MONITOR]["tier"] == "red"
    feed(c, drop, clock, T0 + timedelta(minutes=4), [dev("2", IP_OTHER, state="Unknown", monitors=[])])
    u = [r for r in ev(c) if r["wug_device_id"] == "2"]
    assert len(u) == 1 and u[0]["monitor"] == wa.UNKNOWN_MONITOR and u[0]["tier"] == "yellow"


# ── GPT 審查 3：從沒收到過 ──
def test_啟用後15分鐘還沒收到第一批_推監控未啟動_收到後推恢復(env):
    c, drop, clock, _ = env
    assert wa.heartbeat(c, T0 + timedelta(minutes=14))["status"] == "never"
    assert wa.heartbeat(c, T0 + timedelta(minutes=16))["status"] == "never_sent"
    assert wa.heartbeat(c, T0 + timedelta(minutes=30))["status"] == "never_down"           # 不重推
    n = notices(c, "heartbeat_never")
    assert len(n) == 1 and n[0]["tier"] == "red" and "監控未啟動" in n[0]["title"]
    feed(c, drop, clock, T0 + timedelta(minutes=31), [])
    assert len(notices(c, "heartbeat_up")) == 1


# ── GPT 審查 7、8：月報 SQL 聚合不受筆數上限；沒有 IP 的下鑽用設備 ID ──
def test_月報彙總不受清單上限_超過兩萬筆照算(env):
    c, drop, clock, _ = env
    wa.ensure(c)
    c.executemany("INSERT INTO alert_event (wug_device_id, monitor, ip, tier, started_at, ended_at, last_seen, duration_min, created_at) "
                  "VALUES (?, 'Ping', '192.0.2.200', 'yellow', '2026-11-01 00:00:00', '2026-11-01 00:01:00', '2026-11-01 00:01:00', 1, 'x')",
                  [(str(i),) for i in range(20005)])
    c.commit()
    m = wa.month_summary(c, "2026-11")
    assert m["events"] == 20005 and sum(g["events"] for g in m["machines"]) == 20005 and m["minutes"] == 20005


def test_月報下鑽_沒有IP用設備ID_有IP但沒對到資產只算沒對到的(env):
    c, drop, clock, _ = env
    feed(c, drop, clock, T0, [dev("1", IP_CORE), dev("7", ""), dev("8", "")])
    c.execute("INSERT INTO alert_event (wug_device_id, monitor, ip, tier, started_at, last_seen, created_at) "
              "VALUES ('9', 'Ping', ?, 'red', '2026-11-02 09:00:00', '2026-11-02 09:00:00', 'x')", (IP_CORE,)); c.commit()   # 同 IP 但沒對到資產
    m = wa.month_summary(c, "2026-11")
    for g in m["machines"]:
        d = wa.events(c, month="2026-11", **wa.drill_filter(g))
        assert len(d) == g["events"], (g["key"], len(d))
    nodev = next(g for g in m["machines"] if g["key"] == "wug:7")
    assert nodev["wug_device_id"] == "7" and nodev["ip"] is None and nodev["events"] == 1
    assert len(wa.events(c, month="2026-11")) == 4


# ── GPT 審查 9：布林欄位嚴格 ──
@pytest.mark.parametrize("mut", [
    lambda s: s["scan"].update(complete="false"),
    lambda s: s["scan"].update(complete=1),
    lambda s: s["scan"].pop("complete"),
    lambda s: s["devices"][0].update(monitors_ok="true"),
    lambda s: s["devices"][0].pop("monitors_ok"),
])
def test_布林欄位不是JSON布林就整批拒收(env, mut):
    c, drop, clock, _ = env
    d = snap_batch(drop, T0, [dev("1", IP_CORE)])
    snap = json.loads((d / "snapshot.json").read_text())
    mut(snap)
    raw = json.dumps(snap).encode()
    (d / "snapshot.json").write_bytes(raw)
    m = json.loads((d / "manifest.json").read_text())
    m["files"][0].update(sha256=hashlib.sha256(raw).hexdigest(), size=len(raw))
    (d / "manifest.json").write_text(json.dumps(m))
    wa.tick(c, T0)
    b = c.execute("SELECT status, reason_code, reason FROM relay_import_batches WHERE source='whatsup_alert'").fetchone()
    assert b["status"] == "rejected" and b["reason_code"] == "bad_data" and ("complete" in b["reason"] or "monitors_ok" in b["reason"])
    assert ev(c) == []


def test_狀態文字換算分鐘():
    assert wa.minutes_from_status("Down at least 20 min") == 20
    assert wa.minutes_from_status("Down at least 2 hours") == 120
    assert wa.minutes_from_status("Down at least 1 day") == 1440
    assert wa.minutes_from_status("Down") is None and wa.minutes_from_status(None) is None


# ── 畫面讀同一張表：加總＝下鑽 ──
def test_月報彙總_次數加總等於事件筆數_點下去同一份(env):
    c, drop, clock, _ = env
    feed(c, drop, clock, T0, [dev("1", IP_CORE), dev("2", IP_NONE)])
    feed(c, drop, clock, T0 + timedelta(minutes=10), [dev("2", IP_NONE)])
    feed(c, drop, clock, T0 + timedelta(minutes=20), [dev("1", IP_CORE), dev("2", IP_NONE)])
    m = wa.month_summary(c, "2026-11")
    evs = wa.events(c, month="2026-11")
    assert m["events"] == len(evs) == 3 and sum(g["events"] for g in m["machines"]) == 3
    core = next(g for g in m["machines"] if g["machine_key"])
    drill = wa.events(c, month="2026-11", machine_key=core["machine_key"])
    assert core["events"] == len(drill) == 2 and core["minutes"] == sum(r["duration_min"] for r in drill)
    assert m["unmatched"] == sum(1 for r in evs if not r["machine_key"])
    assert wa.month_summary(c, "2026-10")["events"] == 0


def test_一台機器的目前告警與最近幾次(env):
    c, drop, clock, _ = env
    feed(c, drop, clock, T0, [dev("1", IP_CORE)])
    feed(c, drop, clock, T0 + timedelta(minutes=5), [])
    feed(c, drop, clock, T0 + timedelta(minutes=8), [dev("1", IP_CORE)])
    mk = ev(c)[0]["machine_key"]
    out = wa.for_machine(c, mk, [IP_CORE])
    assert len(out["current"]) == 1 and len(out["recent"]) == 1 and out["total"] == 2
    assert wa.for_machine(c, None, [IP_NONE]) == {"current": [], "recent": [], "total": 0, "known": True}


def test_保存期清理只清已結案且超過期限的(env):
    c, drop, clock, _ = env
    feed(c, drop, clock, T0, [dev("1", IP_CORE), dev("2", IP_OTHER)])
    feed(c, drop, clock, T0 + timedelta(minutes=5), [dev("2", IP_OTHER)])
    later = T0 + timedelta(days=731)
    assert wa.purge(c, later) == 1
    assert [r["wug_device_id"] for r in ev(c)] == ["2"]                 # 開著的不清
    assert wa.purge(c, later) == 0                                       # 一天一次


def test_真假回報(env):
    c, drop, clock, _ = env
    feed(c, drop, clock, T0, [dev("1", IP_CORE)])
    eid = ev(c)[0]["id"]
    assert wa.set_verdict(c, eid, "false", "alice")["verdict"] == "false"
    with pytest.raises(ValueError):
        wa.set_verdict(c, eid, "maybe", "alice")
    with pytest.raises(LookupError):
        wa.set_verdict(c, 9999, "true", "alice")


def test_verify_numbers_告警事件不變式(env):
    import verify_numbers as vn
    c, drop, clock, _ = env
    feed(c, drop, clock, T0, [dev("1", IP_CORE), dev("2", IP_NONE)])
    feed(c, drop, clock, T0 + timedelta(minutes=5), [dev("2", IP_NONE)])
    before = vn.FAILS
    vn.check_wug_alerts(c)
    assert vn.FAILS == before
    c.execute("UPDATE alert_event SET duration_min = -1 WHERE wug_device_id = '1'"); c.commit()
    vn.check_wug_alerts(c)
    assert vn.FAILS > before
    vn.FAILS = before


def test_月報一段_與主機履歷時間軸讀同一張表(env):
    import monthly_report
    import v2_sections
    c, drop, clock, _ = env
    feed(c, drop, clock, T0, [dev("1", IP_CORE)])
    feed(c, drop, clock, T0 + timedelta(minutes=5), [])
    monthly_report.datetime = type("D", (), {"now": staticmethod(lambda: T0)})            # 月報用「現在」的月份
    try:
        sec = next(x for x in monthly_report.collect(c)["sections"] if x["name"] == "告警事件")
    finally:
        import datetime as _dt
        monthly_report.datetime = _dt.datetime
    assert sec["has_data"] and sec["data"]["事件筆數"] == 1 and sec["data"]["每台（依次數，最多 50 台）"][0]["minutes"] == 10
    ch = v2_sections.section(c, IP_CORE, "changes")
    hits = [r for r in ch["rows"] if r["source"] == "WhatsUp 告警"]
    assert len(hits) == 1 and "Ping Down，持續 10 分" in hits[0]["what"] and "已恢復" in hits[0]["what"]


def test_月報一段_沒有事件表就寫無資料():
    import monthly_report
    import sqlite3
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    sec = monthly_report._alerts(c)
    assert sec["has_data"] is False and "不可寫成本月沒有故障" in sec["data"]["說明"]


# ── API ──
@pytest.fixture
def api_env(env):
    import api
    import auth
    from fastapi.testclient import TestClient
    c, drop, clock, p = env

    def _ov():
        cn = db.get_connection(p)
        try:
            yield cn
        finally:
            cn.close()
    api.app.dependency_overrides[api.get_db] = _ov
    for name, role in (("admin", "admin"), ("alice", "user"), ("vera", "viewer")):
        db.create_user(c, name, auth.hash_password("test-password-123"))
        c.execute("UPDATE users SET role = ? WHERE username = ?", (role, name))
    c.commit()

    def client(name):
        cl = TestClient(api.app, raise_server_exceptions=False)
        assert cl.post("/api/auth/login", json={"username": name, "password": "test-password-123"}).status_code == 200
        tok = cl.cookies.get("session_token")
        if tok:
            cl.headers["X-CSRF-Token"] = auth.csrf_token_for(tok)
        return cl
    yield c, drop, clock, client
    api.app.dependency_overrides.clear()


ROLE_AWARE_TESTS = True


def test_API_讀取_設定權限_真假回報權限(api_env):
    c, drop, clock, client = api_env
    feed(c, drop, clock, T0, [dev("1", IP_CORE)])
    admin, alice, vera = client("admin"), client("alice"), client("vera")
    for who in (admin, alice, vera):
        j = who.get("/api/v2/wug-alerts").json()
        assert j["open"] == 1 and j["heartbeat"] in ("ok", "down")
        assert len(who.get("/api/v2/wug-alerts/events", params={"state": "open"}).json()["rows"]) == 1
        assert who.get("/api/v2/wug-alerts/month", params={"ym": "2026-11"}).json()["events"] == 1
        assert len(who.get("/api/v2/wug-alerts/machine", params={"key": IP_CORE}).json()["current"]) == 1
        assert who.get("/api/v2/wug-alerts/notices").status_code == 200
    assert admin.get("/api/v2/wug-alerts").json()["can_edit"] is True and alice.get("/api/v2/wug-alerts").json()["can_edit"] is False
    assert admin.get("/api/v2/wug-alerts/events", params={"month": "bad"}).status_code == 400
    body = {**wa.DEFAULTS, "enabled": True, "heartbeat_min": 15}
    for who in (alice, vera):
        assert who.put("/api/v2/wug-alerts/settings", json=body).status_code == 403
    assert admin.put("/api/v2/wug-alerts/settings", json={**body, "heartbeat_min": 1}).status_code == 400
    r = admin.put("/api/v2/wug-alerts/settings", json=body)
    assert r.status_code == 200 and r.json()["settings"]["heartbeat_min"] == 15
    eid = ev(c)[0]["id"]
    assert vera.put(f"/api/v2/wug-alerts/events/{eid}/verdict", json={"verdict": "true"}).status_code == 403
    r = alice.put(f"/api/v2/wug-alerts/events/{eid}/verdict", json={"verdict": "true"})
    assert r.status_code == 200 and r.json()["verdict"] == "true", r.text
    assert alice.put("/api/v2/wug-alerts/events/9999/verdict", json={"verdict": "true"}).status_code == 404


def test_啟用時的積壓批次_太舊的只記錄不推_之後新的照推(env):
    """2026-11-11 .14 啟用時補匯 11-10 晚上起的積壓批次，每匯一段推一輪（3 分鐘 8 則）。第一次看到就已超過 30 分的只標 quiet。"""
    c, drop, clock, _ = env
    snap_batch(drop, T0, [dev("1", IP_CORE)])
    snap_batch(drop, T0 + timedelta(minutes=5), [dev("2", IP_CORE)])          # 1 恢復、2 發生（都已過時）
    clock["now"] = T0 + timedelta(hours=2)
    wa.tick(c, clock["now"])
    assert notices(c) == []
    assert all(",quiet," in (r["notified"] or "") for r in ev(c))
    feed(c, drop, clock, T0 + timedelta(hours=2, minutes=1), [dev("2", IP_CORE), dev("3", IP_CORE)])
    o = notices(c, "open")
    assert len(o) == 1 and "dev3" in (o[0]["title"] + o[0]["summary"])
    feed(c, drop, clock, T0 + timedelta(hours=2, minutes=8), [dev("3", IP_CORE)])    # 2 恢復：quiet 的不推恢復
    assert notices(c, "recover") == []


def test_同一台多個監控項目_標題只列一次名字(env):
    c, drop, clock, _ = env
    mons = [{"name": f"Svc{i}", "type": "active", "status": "Down at least 5 min", "enabled": "True"} for i in range(4)]
    feed(c, drop, clock, T0, [dev("1", IP_CORE, monitors=mons)])
    o = notices(c, "open")
    assert len(o) == 1 and o[0]["title"].count("dev1") <= 1


def test_同一台多個監控項目合成一行(env):
    c, drop, clock, _ = env
    mons = [{"name": f"Svc{i}", "type": "active", "status": "Down at least 5 min", "enabled": "True"} for i in range(5)]
    feed(c, drop, clock, T0, [dev("1", IP_CORE, monitors=mons), dev("2", IP_CORE)])
    o = notices(c, "open")
    assert len(o) == 1 and "（2 台）" in o[0]["title"]
    body = o[0]["summary"].splitlines()[1:]
    assert len(body) == 2 and any("5 個監控項目異常" in x and "等 5 項" in x for x in body)


def _mute_window(days="", a="", b=""):
    return {"wug_device_id": "1", "monitor": "*", "days": days, "time_from": a, "time_to": b}


@pytest.mark.parametrize("m,at,hit", [
    (_mute_window(), datetime(2026, 11, 2, 10, 0), True),                        # 每天全天
    (_mute_window("6,7"), datetime(2026, 11, 7, 3, 0), True),                    # 週六
    (_mute_window("6,7"), datetime(2026, 11, 6, 3, 0), False),                   # 週五
    (_mute_window("", "02:00", "04:00"), datetime(2026, 11, 2, 3, 0), True),
    (_mute_window("", "02:00", "04:00"), datetime(2026, 11, 2, 4, 1), False),
    (_mute_window("6", "22:00", "06:00"), datetime(2026, 11, 8, 3, 0), True),    # 週六 22～06 跨到週日凌晨
    (_mute_window("6", "22:00", "06:00"), datetime(2026, 11, 7, 3, 0), False),   # 週六凌晨屬於週五晚的時段
    (_mute_window("6", "22:00", "06:00"), datetime(2026, 11, 7, 23, 0), True),
])
def test_不通知時段判斷(m, at, hit):
    assert wa._in_window(m, at) is hit


def test_不通知清單_符合的只記錄不推_其他照推(env):
    c, drop, clock, _ = env
    feed(c, drop, clock, T0, [dev("9", IP_CORE)])                        # 🔴 第一類正式：沒規則時會推
    assert len(notices(c, "open")) == 1
    wa.add_mute(c, ev(c)[0]["id"], True, [], "", "", "週末 reboot", "admin")
    c.commit()
    feed(c, drop, clock, T0 + timedelta(minutes=1), [])                   # 恢復（規則前就推過發生，恢復照推）
    feed(c, drop, clock, T0 + timedelta(minutes=2), [dev("9", IP_CORE), dev("1", IP_CORE)])
    o = notices(c, "open")
    assert len(o) == 2 and "dev1" in o[1]["summary"] and "dev9" not in o[1]["summary"]
    assert any(",muted," in (r["notified"] or "") for r in ev(c) if r["wug_device_id"] == "9" and not r["ended_at"])


def test_不通知規則驗證():
    with pytest.raises(ValueError):
        wa._days(["8"])
    with pytest.raises(ValueError):
        wa._hhmm("25:00")
    assert wa._days(["7", "6", "6"]) == "6,7" and wa._hhmm("2:5") == "02:05"


# ── 只推指定的 WhatsUp 動作政策（2.060.24，使用者 11-11「先主機組通知就好」）──
def _mon(name, policy="__absent__", status="Down at least 5 min"):
    m = {"name": name, "type": "active", "status": status, "enabled": "True"}
    if policy != "__absent__":
        m["policy"] = policy
    return m


def test_動作政策篩選_只推指定政策_沒掛的記錄不推_不知道的照推(env):
    c, drop, clock, _ = env
    wa.save_settings(c, {**wa.load_settings(c), "notify_policies": "主機組, ，"})
    c.commit()
    assert wa.load_settings(c)["notify_policies"] == "主機組"
    feed(c, drop, clock, T0, [
        dev("1", IP_CORE, monitors=[_mon("Ping", "Action Policy(主機組/維運)")]),
        dev("2", IP_CORE_TEST, monitors=[_mon("TAXCPCarrier", "")]),            # WhatsUp 明講沒掛
        dev("3", IP_OTHER, monitors=[_mon("Disk", "網管組")]),                  # 掛別組
        dev("4", IP_NOENV, monitors=[_mon("Svc")]),                              # 舊收集端：不知道
    ])
    pol = {r["wug_device_id"]: r["policy"] for r in ev(c)}
    assert pol == {"1": "Action Policy(主機組/維運)", "2": "", "3": "網管組", "4": None}
    nop = {r["wug_device_id"] for r in ev(c) if ",nopolicy," in (r["notified"] or "")}
    assert nop == {"2", "3"}                                                  # 照樣記錄、畫面看得到
    text = " ".join(n["title"] + (n["summary"] or "") for n in notices(c))
    assert "TAXCPCarrier" not in text and "Disk" not in text
    # 恢復也不推
    feed(c, drop, clock, T0 + timedelta(minutes=3), [])
    feed(c, drop, clock, T0 + timedelta(minutes=6), [])
    text = " ".join(n["title"] + (n["summary"] or "") for n in notices(c))
    assert "TAXCPCarrier" not in text and "Disk" not in text


def test_動作政策沒設定_全部照推(env):
    c, drop, clock, _ = env
    feed(c, drop, clock, T0, [dev("2", IP_CORE, monitors=[_mon("TAXCPCarrier", "")])])
    assert not [r for r in ev(c) if ",nopolicy," in (r["notified"] or "")]
    assert len(notices(c, "open")) == 1


@pytest.mark.parametrize("pol,kws,ok", [(None, ["主機組"], True), ("", ["主機組"], False), ("x主機組y", ["主機組"], True),
                                        ("網管組", ["主機組", "網管"], True), ("", [], True)])
def test_policy_ok(pol, kws, ok):
    assert wa.policy_ok(pol, kws) is ok


def test_動作政策關鍵字太長擋下(env):
    c, *_ = env
    with pytest.raises(ValueError):
        wa.validate({**wa.DEFAULTS, "notify_policies": "x" * 501})


def _att(c, n):
    """2.066.x：需要注意門檻（取代確認時間）。"""
    import alert_attention
    alert_attention.save_threshold(c, n, n); c.commit()


def _confirm(c, n=10):
    wa.save_settings(c, {**wa.load_settings(c), "confirm_min": n}); c.commit()
    if n > 0:
        _att(c, n)


T_NIGHT = datetime(2026, 11, 2, 22, 0, 0)    # 週一 22:00（非開盤）


def test_開盤時段也要斷滿門檻才推(env):
    """2.066.x（使用者 11-13：只有斷滿門檻、非例行的才要注意）：開盤不再一斷就推，跟非開盤同一個規則。"""
    c, drop, clock, _ = env
    _confirm(c, 10)
    feed(c, drop, clock, T0, [dev("1", IP_CORE)])                          # T0＝週一 10:00 開盤；已斷 5 分
    assert notices(c, "open") == []
    feed(c, drop, clock, T0 + timedelta(minutes=6), [dev("1", IP_CORE)])
    assert len(notices(c, "open")) == 1


def test_斷線未滿確認時間不推_滿了才推一則(env):
    """2.061.12：使用者 11-12 Outlook 夜間一堆排程重開機的發生＋恢復。非開盤 🔴 要持續滿 confirm_min 才推「發生」。"""
    c, drop, clock, _ = env
    T0 = T_NIGHT
    _confirm(c, 10)
    mon = [{"name": "Ping", "type": "active", "status": "Down at least 1 min", "enabled": "True"}]
    feed(c, drop, clock, T0, [dev("1", IP_CORE, monitors=mon)])
    assert notices(c, "open") == []
    feed(c, drop, clock, T0 + timedelta(minutes=5), [dev("1", IP_CORE, monitors=mon)])
    assert notices(c, "open") == []
    feed(c, drop, clock, T0 + timedelta(minutes=11), [dev("1", IP_CORE, monitors=mon)])
    assert len(notices(c, "open")) == 1


def test_確認時間內就恢復_不立即推_併進08點彙整(env):
    import notice_digest as nd
    c, drop, clock, _ = env
    T0 = T_NIGHT
    _confirm(c, 10)
    nd.set_time(c, "08:00"); c.commit()
    mons = [{"name": f"Svc{i}", "type": "active", "status": "Down at least 1 min", "enabled": "True"} for i in range(3)]
    feed(c, drop, clock, T0, [dev("1", IP_CORE, monitors=mons)])
    feed(c, drop, clock, T0 + timedelta(minutes=7), [])                     # 7 分就回來（排程重開機）
    assert notices(c, "open") == [] and notices(c, "brief") == [] and notices(c, "recover") == []
    items = nd.pending(c)
    assert len(items) == 1 and "3 個監控項目" in items[0]["line"]           # 一台一行


def test_門檻內恢復但彙整關著_也不立即推(env):
    import notice_digest as nd
    c, drop, clock, _ = env
    T0 = T_NIGHT
    _confirm(c, 10)
    nd.set_time(c, ""); c.commit()
    mon = [{"name": "Ping", "type": "active", "status": "Down at least 1 min", "enabled": "True"}]
    feed(c, drop, clock, T0, [dev("1", IP_CORE, monitors=mon)])
    feed(c, drop, clock, T0 + timedelta(minutes=7), [])                     # 共 8 分 < 10
    assert notices(c) == []


def test_確認分鐘數驗證():
    with pytest.raises(ValueError):
        wa.validate({**wa.DEFAULTS, "confirm_min": 121})
    assert wa.validate({**wa.DEFAULTS, "confirm_min": 0})["confirm_min"] == 0


# ---------------------------------------------------------------- 收集端 v6：只抓有問題設備（-StateFilter）
def _fsnap(devices, sums, complete=True, mode="state filter"):
    return {"kind": "scan", "group": {"totalDevices": 2150, **({"stateSummaries": sums} if sums is not None else {})},
            "scan": {"complete": complete, "pages_read": 2, "devices_seen": len(devices), "stopped_reason": "", "mode": mode},
            "devices": devices}


def test_v6_只抓有問題設備_各狀態分開比_少一台就不完整():
    devs = [dev("1", "10.99.1.1"), dev("2", "10.99.1.2", state="Maintenance"), dev("3", "10.99.1.3", state="Unknown"),
            dev("4", "10.99.1.4", state="Up")]                                       # Up 但有監控停止：不算進三個狀態
    ok = [{"state": "Up", "deviceCount": 2146}, {"state": "Down", "deviceCount": 1}, {"state": "Maintenance", "deviceCount": 1},
          {"state": "Unknown", "deviceCount": 1}]
    s = wa.parse_snapshot(_fsnap(devs, ok))
    assert s["complete"] is True and s["mode"] == "state filter"                     # 看到 4 台遠小於總數，但這個模式不用總數判
    s = wa.parse_snapshot(_fsnap(devs, [{"state": "Down", "deviceCount": 2}]))
    assert s["complete"] is False and "Down" in s["stopped_reason"]                   # 少一台就不完整（不再容許差 2 台）
    s = wa.parse_snapshot(_fsnap([], [{"state": "Down", "deviceCount": 2}]))
    assert s["complete"] is False                                                    # 查詢回空清單、群組說有 2 台 Down → 不完整（審查 P1-a 情境 A）
    s = wa.parse_snapshot(_fsnap(devs, None))
    assert s["complete"] is False and "stateSummaries" in s["stopped_reason"]
    s = wa.parse_snapshot(_fsnap(devs, [{"state": "Down", "deviceCount": "1"}]))
    assert s["complete"] is False
    s = wa.parse_snapshot(_fsnap(devs, ok, mode="all devices"))                      # 全掃模式照舊用總數判：4 台遠小於 2150 → 不完整
    assert s["complete"] is False and "差太多" in s["stopped_reason"]


def test_v6_只抓有問題設備_一個狀態多不能抵另一個狀態少():
    """審查 P1-b：Down 少 3 台、Unknown 多 3 台，總和一樣也要判不完整。"""
    devs = [dev(str(i), f"10.99.2.{i}") for i in range(1, 8)] + [dev(str(100 + i), f"10.99.3.{i}", state="Unknown") for i in range(1, 7)]
    sums = [{"state": "Down", "deviceCount": 10}, {"state": "Unknown", "deviceCount": 3}]
    s = wa.parse_snapshot(_fsnap(devs, sums))
    assert s["complete"] is False and "Down" in s["stopped_reason"]
    sums = [{"state": "Down", "deviceCount": 5}, {"state": "Unknown", "deviceCount": 6}]   # 多出來可以（查詢之間新壞的）
    assert wa.parse_snapshot(_fsnap(devs, sums))["complete"] is True
