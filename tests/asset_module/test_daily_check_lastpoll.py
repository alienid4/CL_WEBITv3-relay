"""S13（2.062.0）：1.5 每日檢查改讀 WhatsUp「上次輪循」群組快照。全部假資料（IP 10.99.x、群組名稱用正本的通用名）。

釘住：parse 拒收壞資料、快速通道匯入（一般排程不碰）、不完整批次不拿、新鮮度兩層、門檻與等號、deviceId 去重、無資料另外數、
資產對不到照算、成員跳動 → 範圍未確認 → 核准恢復、群組對不上停判、w3 切換、verify_numbers 不變式、API 與權限。
"""
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import daily_check as dc  # noqa: E402
import db  # noqa: E402
import quiet_window as qw  # noqa: E402
import relay_import as ri  # noqa: E402
import verify_numbers as vn  # noqa: E402
import wug_lastpoll as lp  # noqa: E402

TPE = timezone(timedelta(hours=8))
Q_CPU, Q_SWAP, Q_DISK, Q_INFRA = "1-xServer(重要系統/交易)", "1-xServer(正式-電券/交易)", "Server", "5-Infra 主機類(Windows、Linux)"
ROOTP = "My Network > "


def tnow():
    return datetime.now(TPE).replace(tzinfo=None, microsecond=0)


def grp(query, gid, members, cpu=(), mem=(), disk=(), ok=True, error="", drop=()):
    """members: [(id, ip)]；cpu/mem/disk: [(id, item, pct, age_s)]。drop：要拿掉的指標（模擬缺指標）。"""
    def rows(lst):
        # used／size 跟百分比一致（size 100）：一批的單位由記憶體／磁碟的已用量判斷（審查 P2），這裡的假資料都是 0～100
        return [{"id": i, "name": f"dev-{i}", "item": it, "poll": "2026-11-12T00:00:00Z", "age": age, "avg": pct, "max": pct, "used": pct, "size": 100.0}
                for i, it, pct, age in lst]
    mets = {k: {"ok": True, "error": "", "rows": rows(v)} for k, v in (("cpu", cpu), ("mem", mem), ("disk", disk)) if k not in drop}
    return {"query": query, "path": ROOTP + query, "id": gid, "ok": ok, "error": error,
            "members": [{"id": i, "name": f"dev-{i}", "ip": ip} for i, ip in members], "metrics": mets}


def counts_of(groups):
    return [{"path": g["path"], "members": len(g["members"]), **{k: len(g["metrics"].get(k, {}).get("rows", [])) for k in ("cpu", "mem", "disk")}}
            for g in groups]


def send(drop, name, groups, when=None, counts="auto", fname="snap.json", raw=None):
    when = when or datetime.now(TPE)
    d = Path(drop) / name
    d.mkdir(parents=True)
    body = json.dumps(raw if raw is not None else {"groups": groups}, ensure_ascii=False).encode()
    (d / fname).write_bytes(body)
    m = {"source": "whatsup_lastpoll", "batch_id": name, "schema_version": 1, "collector_version": "wug_lastpoll_collect 1",
         "collected_at": when.isoformat(timespec="seconds"), "time_zone": "UTC", "range": {"from": "2026-11-12", "to": "2026-11-12"}, "count": 1,
         "files": [{"name": fname, "sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}]}
    if counts == "auto":
        m["counts"] = counts_of(groups or [])
    elif counts is not None:
        m["counts"] = counts
    (d / "manifest.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    return d


M = [("d1", "10.99.1.1"), ("d2", "10.99.1.2"), ("d3", "10.99.1.3")]


def standard(cpu=None, mem=None, disk=None, members=None):
    members = members or M
    g1 = grp(Q_CPU, "11", members,
             cpu=cpu if cpu is not None else [("d1", "", 80.0, 120), ("d2", "", 75.0, 120)],
             mem=mem if mem is not None else [("d1", "Physical Memory", 50.0, 120), ("d2", "Physical Memory", 90.5, 120), ("d2", "Virtual Memory", 99.0, 120)])
    g2 = grp(Q_SWAP, "12", [("d1", "10.99.1.1"), ("d4", "10.99.1.4")], cpu=[("d1", "", 10.0, 120), ("d4", "", 10.0, 5000)],
             mem=[("d1", "m", 10.0, 120)], disk=[("d4", "/", 10.0, 120)])
    g3 = grp(Q_DISK, "13", M, disk=disk if disk is not None else [("d1", "C:", 95.0, 120), ("d1", "D:", 10.0, 120), ("d2", "/", 85.0, 120),
                                                                    ("d2", "Swap space", 99.0, 120), ("d3", "/", 84.9, 120)])
    g4 = grp(Q_INFRA, "14", [("d4", "10.99.1.4"), ("d5", "10.99.1.5")], cpu=[("d5", "", 1.0, 60)])
    return [g1, g2, g3, g4]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    c = db.get_connection()
    drop = tmp_path / "drop"
    drop.mkdir()
    monkeypatch.setattr(ri, "STABLE_SECONDS", -5)
    db.set_setting(c, ri.DIR_KEY, str(drop))
    db.set_setting(c, ri.ENABLED_KEY, "1")
    db.insert_hardware(c, asset_serial="H-1", hostname="app01", ip="10.99.1.1")
    db.insert_hardware(c, asset_serial="H-2", hostname="app02", ip="10.99.1.2")
    # 預設群組已改成 WhatsUp 實名（11-13，正式-電券 已併掉）；這裡的測試沿用原本四群組的分工（SWAP／輪詢看 Q_SWAP 群組）
    dc.save_settings(c, {"dc_group_swap": {"path": Q_SWAP, "id": ""}, "dc_group_poll": [{"path": Q_SWAP, "id": ""}, {"path": Q_INFRA, "id": ""}]})
    c.commit()
    yield c, drop
    qw.set_clock(None)
    c.close()


def imp(c):
    return lp.import_pending(c)


def gen(c, now=None):
    rid = dc.generate(c, "tester", "manual", now or tnow())
    v = dc.run_view(c, rid)
    return {r["code"]: r for r in v["rows"]}, v


# ---------------------------------------------------------------- 匯入
@pytest.mark.parametrize("raw,fname,why", [
    ({"groups": []}, "snap.json", "空"),
    ({"groups": [{"ok": True}]}, "snap.json", "沒有 path"),
    ({"groups": [{"path": "a", "ok": "yes"}]}, "snap.json", "ok 不是布林"),
    ({"groups": [{"path": "a", "ok": True, "members": [], "metrics": {"cpu": {"ok": True, "rows": [{"avg": 1}]}}}]}, "snap.json", "沒有設備 id"),
    ({"groups": [{"path": "a", "ok": True, "members": [], "metrics": {"cpu": {"ok": True, "rows": [{"id": "d1", "avg": "80"}]}}}]}, "snap.json", "數字是字串"),
    ({"groups": [{"path": "a", "ok": True, "members": [], "metrics": {"cpu": {"ok": True, "rows": [{"id": "d1", "avg": -1}]}}}]}, "snap.json", "負數"),
    ({"groups": [{"path": "a", "ok": True}, {"path": "a", "ok": True}]}, "snap.json", "群組重複"),
    ({"groups": [{"path": "a", "ok": True}]}, "other.json", "檔名不對"),
])
def test_parse_拒收壞資料(env, raw, fname, why):
    c, drop = env
    send(drop, "wuglp_bad", None, raw=raw, fname=fname, counts=None)
    assert imp(c)["rejected"] == 1, why
    st = c.execute("SELECT status FROM relay_import_batches WHERE batch_id = 'wuglp_bad'").fetchone()[0]
    assert st == "rejected"


def test_收集時間在未來_拒收(env):
    c, drop = env
    send(drop, "wuglp_future", standard(), when=datetime.now(TPE) + timedelta(hours=1))
    assert imp(c)["rejected"] == 1


def test_快速通道匯入_一般排程不碰_中繼沒啟用就不動(env):
    c, drop = env
    send(drop, "wuglp_1", standard())
    assert ri.pending_batches(c) == []                                   # 一般 15 分鐘排程不看 wuglp_
    db.set_setting(c, ri.ENABLED_KEY, "0")
    assert imp(c)["status"] == "disabled"
    db.set_setting(c, ri.ENABLED_KEY, "1")
    assert imp(c)["imported"] == 1
    r = c.execute("SELECT status, trigger FROM relay_import_batches WHERE batch_id = 'wuglp_1'").fetchone()
    assert tuple(r) == ("imported", "lastpoll")
    b = lp.latest_complete(c)
    assert b["batch_id"] == "wuglp_1" and b["complete"] == 1 and len(b["groups"]) == 4
    assert c.execute("SELECT COUNT(*) FROM wug_lp_member").fetchone()[0] == 3 + 2 + 3 + 2
    # 第一次收到每個群組＝自動核准一筆
    assert c.execute("SELECT COUNT(*) FROM wug_lp_approval WHERE auto = 1").fetchone()[0] == 4
    assert imp(c)["status"] == "idle"                                    # 冪等


def test_C類例外登記_scan_service每分鐘呼叫():
    e = qw.ENTRIES["wug_lastpoll_tick"]
    assert e["cls"] == "C" and qw.TICK_SITES["wug_lastpoll.py:tick"] == "wug_lastpoll_tick"
    assert qw.SCHEDULER_CALLS["_wug_lastpoll_tick"] == "wug_lastpoll_tick"
    src = (ROOT / "APP" / "asset-module" / "backend" / "scan_service.py").read_text(encoding="utf-8")
    assert src.index("_wug_lastpoll_tick(conn)") < src.index("    _daily_check_tick(conn)")


def test_不完整批次照存_每日檢查只拿完整的(env):
    c, drop = env
    g = standard()
    bad_counts = counts_of(g)
    bad_counts[0]["cpu"] = 99                                            # manifest 筆數對不上
    send(drop, "wuglp_1", g, counts=bad_counts)
    imp(c)
    assert lp.latest_any(c)["complete"] == 0 and lp.latest_complete(c) is None
    r, _ = gen(c)
    assert r["W-c"]["verdict"] == "nodata" and "從沒收到完整" in r["W-c"]["reason"] and "筆數對不上" in r["W-c"]["reason"]
    # 群組 ok=false、缺指標也一樣不完整
    g2 = standard()
    g2[1]["ok"], g2[1]["error"] = False, "group not found"
    send(drop, "wuglp_2", g2)
    g3 = standard()
    del g3[2]["metrics"]["disk"]
    send(drop, "wuglp_3", g3, counts=counts_of(g3))
    imp(c)
    probs = {b: json.loads(p) for b, p in c.execute("SELECT batch_id, problems_json FROM wug_lp_batch")}
    assert any("group not found" in x for x in probs["wuglp_2"]) and any("缺 磁碟" in x for x in probs["wuglp_3"])
    assert lp.latest_complete(c) is None
    # 沒有 counts 的 manifest＝不能確認完整
    send(drop, "wuglp_4", standard(), counts=None)
    imp(c)
    assert c.execute("SELECT complete FROM wug_lp_batch WHERE batch_id = 'wuglp_4'").fetchone()[0] == 0


# ---------------------------------------------------------------- 判定
def test_新鮮度兩層_批次太舊無資料_單筆舊值不算正常(env):
    c, drop = env
    send(drop, "wuglp_1", standard(cpu=[("d1", "", 10.0, 120), ("d2", "", 10.0, 3700), ("d3", "", 10.0, 120)]))
    imp(c)
    r, _ = gen(c)
    cpu = r["W-c"]
    assert cpu["verdict"] == "ok" and cpu["kind"] == "auto" and cpu["fresh"] == 0       # d2 舊值 → 不算正常、要人看
    st = cpu["stats"]
    assert st["scope"] == 3 and st["with_data"] == 2 and st["no_data"] == 1 and st["stale"] == 1
    assert [d["state"] for d in cpu["detail"] if d["device_id"] == "d2"] == ["舊值"]
    r, _ = gen(c, tnow() + timedelta(minutes=40))                       # 最新完整一批 > 35 分鐘
    assert r["W-c"]["verdict"] == "nodata" and "太舊" in r["W-c"]["reason"]


def test_門檻與等號_照正本需低於_CPU達75_記憶體達90就異常_不算虛擬記憶體(env):
    c, drop = env
    send(drop, "wuglp_1", standard(cpu=[("d1", "", 80.0, 120), ("d2", "", 75.0, 120), ("d3", "", 74.9, 120)],
                                   mem=[("d1", "m", 89.9, 120), ("d2", "Physical Memory", 90.0, 120), ("d2", "Virtual Memory", 99.0, 120)]))
    imp(c)
    r, _ = gen(c)
    cpu = {d["device_id"]: d for d in r["W-c"]["detail"]}
    assert cpu["d1"]["state"] == "異常" and cpu["d2"]["state"] == "異常" and cpu["d3"]["state"] == "正常"   # 75 ＝ 異常（需低於 75）
    assert r["W-c"]["verdict"] == "bad" and r["W-c"]["stats"]["bad"] == 2 and r["W-c"]["value_text"].startswith("2 台達 75% 以上")
    mem = {d["device_id"]: d for d in r["W-d"]["detail"]}
    assert mem["d2"]["state"] == "異常" and "90%" in mem["d2"]["what"] and "Virtual" not in mem["d2"]["what"]
    assert mem["d1"]["state"] == "正常" and mem["d3"]["state"] == "無資料"


def test_有效年齡_加上批次到產生的時間_舊值與輪詢逾時(env):
    c, drop = env
    when = datetime.now(TPE) - timedelta(minutes=20)
    g = standard(cpu=[("d1", "", 10.0, 2500), ("d2", "", 10.0, 600), ("d3", "", 10.0, 600)])   # d1：41 分＋20 分＝61 分 > 60
    g[3]["metrics"]["cpu"]["rows"][0]["age"] = 2500                                           # d5（輪詢群組）同樣 61 分
    send(drop, "wuglp_1", g, when=when)
    imp(c)
    r, _ = gen(c)
    cpu = {d["device_id"]: d for d in r["W-c"]["detail"]}
    assert cpu["d1"]["state"] == "舊值" and cpu["d2"]["state"] == "正常"
    poll = {d["device_id"]: d for d in r["W-g"]["detail"]}
    assert poll["d5"]["state"] == "異常" and "CPU 61 分鐘前" in poll["d5"]["what"]


def test_磁碟_95以上異常_85到95參考_SWAP與假磁碟不算_單台例外(env):
    c, drop = env
    send(drop, "wuglp_1", standard())
    imp(c)
    r, _ = gen(c)
    dk = {d["device_id"]: d for d in r["W-f"]["detail"]}
    assert dk["d1"]["state"] == "異常" and "C: 95%" in dk["d1"]["what"]       # ≥ 95
    assert dk["d2"]["state"] == "參考" and "Swap" not in dk["d2"]["what"]      # 85 參考；Swap space 99% 不算
    assert dk["d3"]["state"] == "正常"                                           # 84.9
    assert r["W-f"]["verdict"] == "bad" and r["W-f"]["stats"]["ref"] == 1 and r["W-f"]["stats"]["bad_disks"] == 1
    dc.save_settings(c, {"disk_exceptions": [{"ip": "10.99.1.1", "threshold": 97, "reason": "歸檔碟", "expires": "2099-12-31"}]})
    r, _ = gen(c)
    dk = {d["device_id"]: d for d in r["W-f"]["detail"]}
    assert dk["d1"]["state"] == "參考" and "單台例外" in dk["d1"]["what"] and r["W-f"]["verdict"] == "ok"


def test_輪詢_兩個群組deviceId去重_沒有指標不算逾時(env):
    c, drop = env
    send(drop, "wuglp_1", standard())
    imp(c)
    r, _ = gen(c)
    p = r["W-g"]
    det = {d["device_id"]: d for d in p["detail"]}
    assert set(det) == {"d1", "d4", "d5"}                                # d4 在兩個群組只算一次
    assert det["d4"]["state"] == "異常" and "CPU 83 分鐘前" in det["d4"]["what"] and "、" in det["d4"]["src"]
    assert det["d1"]["state"] == "正常" and det["d5"]["state"] == "正常"
    assert p["stats"]["per_metric"] == {"cpu": 1, "mem": 0, "disk": 0} and p["verdict"] == "bad"
    # 子群組重複列出同一台＝成員去重
    g = standard(members=M + [("d1", "10.99.1.1")])
    send(drop, "wuglp_2", g)
    imp(c)
    r, _ = gen(c)
    assert r["W-c"]["stats"]["scope"] == 3


def test_P1_輪詢_成員在但沒有輪詢時間_整列不判正常_列為參考(env):
    c, drop = env
    g = standard()
    g[1]["metrics"]["cpu"]["rows"] = [{**x, "age": 120} for x in g[1]["metrics"]["cpu"]["rows"]]   # 沒有逾時的
    g[3]["metrics"]["cpu"]["rows"] = []                                                            # d5 三份都沒有
    g[3]["members"].append({"id": "d6", "name": "dev-d6", "ip": "10.99.1.6"})
    g[3]["metrics"]["mem"]["rows"] = [{"id": "d6", "name": "dev-d6", "item": "m", "poll": "", "age": None, "avg": 10.0, "max": 10.0}]  # age 空
    send(drop, "wuglp_1", g)
    imp(c)
    r, _ = gen(c)
    p = r["W-g"]
    det = {d["device_id"]: d for d in p["detail"]}
    assert p["verdict"] == "ref" and p["fresh"] == 0 and "沒有輪詢時間" in p["reason"]
    assert det["d5"]["state"] == det["d6"]["state"] == "無資料" and "沒有輪詢時間" in det["d5"]["what"]
    assert p["stats"]["no_data"] == 2 and p["stats"]["bad"] == 0


def test_資產對不到照算_明細標示(env):
    c, drop = env
    send(drop, "wuglp_1", standard())
    imp(c)
    r, _ = gen(c)
    d = {x["device_id"]: x for x in r["W-c"]["detail"]}
    assert d["d1"]["serial"] == "H-1" and d["d1"]["name"] == "app01" and d["d1"]["asset"] == ""
    assert d["d3"]["serial"] is None and d["d3"]["asset"] == "資產對不到"
    assert r["W-c"]["stats"]["asset_unmatched"] == 1


def test_成員跳動_範圍未確認_管理員核准後恢復(env):
    c, drop = env
    send(drop, "wuglp_1", standard(), when=datetime.now(TPE) - timedelta(minutes=5))
    imp(c)
    more = M + [("d6", "10.99.1.6"), ("d7", "10.99.1.7")]                 # +2／3＝66% > 20%
    send(drop, "wuglp_2", standard(members=more))
    imp(c)
    r, _ = gen(c)
    assert r["W-c"]["verdict"] == "nodata" and "範圍未確認" in r["W-c"]["reason"]
    assert r["W-d"]["verdict"] == "nodata"
    out = lp.approve(c, ROOTP + Q_CPU, "admin", tnow())
    assert out["count"] == 5 and out["previous"] == 3
    r, _ = gen(c)
    assert r["W-c"]["verdict"] in ("ok", "bad") and r["W-c"]["stats"]["scope"] == 5
    rows = lp.approvals(c)
    assert rows[0]["by"] == "admin" and rows[0]["auto"] == 0 and any(x["auto"] == 1 for x in rows)


def test_成員以批次為鍵_晚到的舊批與不完整批不寫成員也不自動核准(env):
    c, drop = env
    g = standard()
    bad = counts_of(g)
    bad[0]["cpu"] = 99
    send(drop, "wuglp_a_incomplete", g, counts=bad, when=datetime.now(TPE) - timedelta(minutes=10))
    imp(c)
    assert c.execute("SELECT COUNT(*) FROM wug_lp_member").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM wug_lp_approval").fetchone()[0] == 0           # 不完整批不自動核准
    send(drop, "wuglp_b_new", standard())
    imp(c)
    send(drop, "wuglp_c_old", standard(members=M + [("d9", "10.99.1.9")]), when=datetime.now(TPE) - timedelta(minutes=30))   # 晚到的舊批
    imp(c)
    assert {r[0] for r in c.execute("SELECT DISTINCT batch_id FROM wug_lp_member")} == {"wuglp_b_new"}
    assert c.execute("SELECT count FROM wug_lp_approval WHERE group_path = ?", (ROOTP + Q_CPU,)).fetchone()[0] == 3
    r, _ = gen(c)
    assert r["W-c"]["batch"] == "wuglp_b_new" and r["W-c"]["stats"]["scope"] == 3          # 判定用的成員與數值同一批


def test_核准前列出新增移除的設備(env):
    c, drop = env
    send(drop, "wuglp_1", standard(), when=datetime.now(TPE) - timedelta(minutes=5))
    imp(c)
    send(drop, "wuglp_2", standard(members=[("d1", "10.99.1.1"), ("d2", "10.99.1.2"), ("d6", "10.99.1.6"), ("d7", "10.99.1.7")]))
    imp(c)
    d = lp.member_diff(c, ROOTP + Q_CPU)
    assert d["added"] == 2 and d["removed"] == 1 and d["members"] == 4
    assert {(x["device_id"], x["change"]) for x in d["rows"]} == {("d6", "新增"), ("d7", "新增"), ("d3", "移除")}
    assert [x["ip"] for x in d["rows"] if x["device_id"] == "d6"] == ["10.99.1.6"]
    with pytest.raises(LookupError):
        lp.member_diff(c, "沒有這個群組")


def test_心跳_0530到1800超過60分鐘沒完整批次_紅字_Teams同一天一次_恢復再一次(env):
    c, drop = env
    t0 = datetime(2026, 11, 10, 9, 0, tzinfo=TPE)                       # 週二
    send(drop, "wuglp_1", standard(), when=t0)
    imp(c)
    now = datetime(2026, 11, 10, 10, 30)
    hb = lp.heartbeat_view(c, now)
    assert hb["down"] and "90 分鐘前" in hb["reason"]
    assert "收集中斷" in dc.today_status(c, now)["lp_alert"]
    assert lp.heartbeat_view(c, datetime(2026, 11, 10, 9, 30))["down"] is False
    assert lp.heartbeat_view(c, datetime(2026, 11, 10, 19, 0))["checked"] is False      # 18:00 後不判斷
    assert lp.heartbeat_view(c, datetime(2026, 11, 8, 10, 30))["checked"] is False      # 週日不判斷
    assert lp.heartbeat_tick(c, now) == "down"
    assert lp.heartbeat_tick(c, now + timedelta(minutes=1)) == "same"
    send(drop, "wuglp_2", standard(), when=datetime(2026, 11, 10, 10, 40, tzinfo=TPE))
    imp(c)
    assert lp.heartbeat_tick(c, datetime(2026, 11, 10, 10, 41)) == "up"
    assert lp.heartbeat_tick(c, datetime(2026, 11, 10, 12, 0)) == "down_again"        # 同一天第二次中斷：不再推
    st = lp._hb_state(c)
    assert st["down"] and st["notified_up"] in ("recorded", "queued")


def test_心跳_從沒收到批次不判斷(env):
    c, _ = env
    assert lp.heartbeat_view(c, datetime(2026, 11, 10, 10, 30))["checked"] is False
    assert "lp_alert" not in dc.today_status(c, datetime(2026, 11, 10, 10, 30))


def test_成員0台_範圍未確認(env):
    c, drop = env
    g = standard()
    g[0]["members"] = []
    g[0]["metrics"] = {k: {"ok": True, "error": "", "rows": []} for k in ("cpu", "mem", "disk")}
    send(drop, "wuglp_1", g)
    imp(c)
    r, _ = gen(c)
    assert r["W-c"]["verdict"] == "nodata" and "範圍未確認" in r["W-c"]["reason"]


def test_群組對不上_停判(env):
    c, drop = env
    send(drop, "wuglp_1", standard())
    imp(c)
    dc.save_settings(c, {"dc_group_cpu_mem": {"path": ROOTP + Q_CPU, "id": "999"}})
    r, _ = gen(c)
    assert r["W-c"]["verdict"] == "nodata" and "群組對不上" in r["W-c"]["reason"] and "999" in r["W-c"]["reason"]
    dc.save_settings(c, {"dc_group_cpu_mem": {"path": ROOTP + Q_CPU, "id": "11"}})       # 完整路徑＋正確 ID
    r, _ = gen(c)
    assert r["W-c"]["verdict"] == "bad"
    dc.save_settings(c, {"dc_group_poll": [{"path": "不存在的群組", "id": ""}, {"path": Q_INFRA, "id": ""}]})
    r, _ = gen(c)
    assert r["W-g"]["verdict"] == "nodata" and "群組對不上" in r["W-g"]["reason"]


def test_SWAP_沒有API_退回健康檢查_範圍用群組IP(env):
    c, drop = env
    send(drop, "wuglp_1", standard())
    imp(c)
    r, _ = gen(c)
    s = r["W-e"]
    assert s["verdict"] == "nodata" and "Linux_SWAP 沒有 API" in s["reason"] and "2 個 IP" in s["reason"] and s["kind"] == "ref"


def test_手填清單並行比對(env):
    c, drop = env
    send(drop, "wuglp_1", standard())
    imp(c)
    dc.save_settings(c, {"xserver_ips": ["10.99.1.1", "10.99.1.9"]})
    r, _ = gen(c)
    st = r["W-c"]["stats"]
    assert st["manual_only"] == ["10.99.1.9"] and st["group_only"] == ["10.99.1.2", "10.99.1.3"]
    assert "手填比對：手填有群組沒有 1、群組有手填沒有 2" in r["W-c"]["value_text"]


def test_w3切換_走舊規則(env):
    c, drop = env
    send(drop, "wuglp_1", standard())
    imp(c)
    dc.save_settings(c, {"daily_check_perf_source": "w3"})
    r, v = gen(c)
    assert r["W-c"]["kind"] == "ref" and r["W-c"]["source"] == "WhatsUp 效能中繼" and r["W-c"]["stats"] is None
    assert "whatsup_lastpoll" not in v["run"]["sources"]
    with pytest.raises(ValueError):
        dc.save_settings(c, {"daily_check_perf_source": "xx"})
    with pytest.raises(ValueError):
        dc.save_settings(c, {"disk_list": 96})                            # 列出門檻不能大於異常門檻


def test_從沒收到_無資料(env):
    c, _ = env
    r, _ = gen(c)
    for k in ("W-c", "W-d", "W-f", "W-g"):
        assert r[k]["verdict"] == "nodata" and "從沒收到" in r[k]["reason"], k


def test_verify_numbers_不變式(env):
    c, drop = env
    send(drop, "wuglp_1", standard())
    imp(c)
    gen(c)
    before = vn.FAILS
    vn.check_daily_check(c)
    assert vn.FAILS == before
    rid = c.execute("SELECT id FROM daily_check_row WHERE code = 'W-c' ORDER BY id DESC LIMIT 1").fetchone()[0]
    st = json.loads(c.execute("SELECT stats_json FROM daily_check_row WHERE id = ?", (rid,)).fetchone()[0])
    st["scope"] += 1
    c.execute("UPDATE daily_check_row SET stats_json = ? WHERE id = ?", (json.dumps(st), rid))
    c.commit()
    vn.check_daily_check(c)
    assert vn.FAILS > before
    # 列上文字的數字跟明細對不上也要抓到（stats 改回正確，只改文字）
    st["scope"] -= 1
    c.execute("UPDATE daily_check_row SET stats_json = ?, value_text = '0 台達 75% 以上（有資料 2／範圍 3 台；無資料 1 台）' WHERE id = ?", (json.dumps(st), rid))
    c.commit()
    before = vn.FAILS
    vn.check_daily_check(c)
    assert vn.FAILS == before + 1


def test_值保留30天_成員永久(env):
    c, drop = env
    send(drop, "wuglp_old", standard(), when=datetime.now(TPE) - timedelta(days=31))
    imp(c)
    # 分兩次匯入：同一輪兩個檔的處理順序依檔案時間／檔名，全套平行時 mtime 相同會變成 new 先處理、
    # purge 跑完 old 才進來（下一輪才清）——那是正常行為，這支測的是「保留 30 天」，不是處理順序
    send(drop, "wuglp_new", standard())
    imp(c)
    assert {r[0] for r in c.execute("SELECT DISTINCT batch_id FROM wug_lp_value")} == {"wuglp_new"}
    assert c.execute("SELECT COUNT(DISTINCT day) FROM wug_lp_member").fetchone()[0] == 2


def test_清舊成員_每天留最後一批_依收集時間不依batch_id字串(env):
    c, drop = env
    day0 = datetime.now(TPE) - timedelta(days=40)
    # batch_id 字串順序跟時間相反：zz 是早上那批、aa 是晚上那批
    send(drop, "wuglp_zz_morning", standard(members=M), when=day0.replace(hour=8, minute=0))
    imp(c)
    send(drop, "wuglp_aa_evening", standard(members=M + [("d8", "10.99.1.8")]), when=day0.replace(hour=17, minute=0))
    imp(c)
    send(drop, "wuglp_now", standard())
    imp(c)                                                                # 這批觸發清理（40 天前那天只留一批）
    old_day = day0.date().isoformat()
    kept = {r[0] for r in c.execute("SELECT DISTINCT batch_id FROM wug_lp_member WHERE day = ?", (old_day,))}
    assert kept == {"wuglp_aa_evening"}
    assert c.execute("SELECT COUNT(*) FROM wug_lp_member WHERE day = ? AND group_path = ?", (old_day, ROOTP + Q_CPU)).fetchone()[0] == 4


def test_API_狀態與核准_核准限管理員(env):
    import access_policy as ap
    import api
    from fastapi.testclient import TestClient

    c, drop = env
    send(drop, "wuglp_1", standard())
    imp(c)
    assert ap.level_for("POST", "/api/daily-check/approve-members") == "admin"

    def _get_db():
        cc = db.get_connection()
        try:
            yield cc
        finally:
            cc.close()
    api.app.dependency_overrides[api.get_db] = _get_db
    api.app.dependency_overrides[api.require_auth] = lambda: {"username": "admin", "role": "admin"}
    try:
        cl = TestClient(api.app)
        s = cl.get("/api/daily-check/lastpoll").json()
        assert s["latest_complete"]["batch_id"] == "wuglp_1" and len(s["groups"]) == 4
        g = s["groups"][0]
        assert g["members"] == 3 and g["ips"] == 3 and g["cpu"] == 2 and g["scope_ok"] is True and g["approved_count"] == 3
        df = cl.get("/api/daily-check/lastpoll/diff", params={"path": ROOTP + Q_CPU}).json()
        assert df["members"] == 3 and df["rows"] == []
        r = cl.post("/api/daily-check/approve-members", json={"path": ROOTP + Q_CPU})
        assert r.status_code == 200 and r.json()["count"] == 3
        assert cl.post("/api/daily-check/approve-members", json={"path": "沒有這個"}).status_code == 404
        info = cl.get("/api/daily-check").json()
        assert info["perf_source"] == "lastpoll" and info["thresholds"]["disk_alert"] == 95.0
    finally:
        api.app.dependency_overrides.clear()


# ---------------------------------------------------------------- 收集端 v2（NB 11-13 第一次實跑）
def test_預設群組_WhatsUp實名_名稱寬鬆比對_空白與全形括號(env):
    c, drop = env
    dc.save_settings(c, dict(dc.DEFAULT_GROUPS))                        # 回到預設
    real_cpu, real_infra = "1-xServer (重要系統/交易)", "5-Infra(主機類_Windows/Linux)"
    g = standard()
    g[0]["query"], g[0]["path"] = "1-xServer（重要系統／交易）", ROOTP + real_cpu       # 設定檔用全形括號、沒空白
    g[3]["query"], g[3]["path"] = real_infra, ROOTP + real_infra
    send(drop, "wuglp_1", g)
    imp(c)
    r, _ = gen(c)
    assert r["W-c"]["verdict"] == "bad" and r["W-c"]["stats"]["group"] == ROOTP + real_cpu
    assert r["W-g"]["verdict"] != "nodata" and r["W-g"]["stats"]["groups"] == [ROOTP + real_cpu, ROOTP + real_infra]
    assert lp.norm_name(" 5-Infra （主機類＿Windows／Linux） ") == lp.norm_name(real_infra)
    # 寬鬆比對出不只一個 → 停判、不猜
    b = {"groups": [{"path": "A > X (1)", "query": "", "ok": True}, {"path": "B > X(1)", "query": "", "ok": True}]}
    got, why = lp.match_group(b, {"path": "X（1）"})
    assert got is None and "對到 2 個群組" in why


def test_avgPercent是小數_換成百分比_磁碟用used比size確認(env):
    c, drop = env
    g = standard(cpu=[("d1", "", 0.80, 120), ("d2", "", 0.75, 120), ("d3", "", 0.1003, 120)],
                 mem=[("d1", "m", 0.5, 120), ("d2", "Physical Memory", 0.905, 120)],
                 disk=[("d1", "C:", 0.95, 120), ("d2", "/", 0.1003, 120), ("d3", "/", 0.0, 120)])
    for grp_ in g:                                                      # 同一批同一指標一起判：其他群組也換成小數
        for k in ("cpu", "mem", "disk"):
            for r in grp_["metrics"][k]["rows"]:
                if r["avg"] > 1:
                    r["avg"] = r["max"] = r["avg"] / 100
    for grp_ in g:                                                      # 已用量／容量跟小數一致
        for k in ("mem", "disk"):
            for r in grp_["metrics"][k]["rows"]:
                r["size"], r["used"] = 200.0, round(r["avg"] * 200.0, 4)
    send(drop, "wuglp_1", g)
    imp(c)
    r, _ = gen(c)
    cpu = {d["device_id"]: d for d in r["W-c"]["detail"]}
    assert cpu["d1"]["state"] == "異常" and "CPU 80%" in cpu["d1"]["what"] and "10.03%" in cpu["d3"]["what"]
    dk = {d["device_id"]: d for d in r["W-f"]["detail"]}
    assert dk["d1"]["state"] == "異常" and "C: 95%" in dk["d1"]["what"] and dk["d2"]["state"] == "正常"
    assert lp._vote([{"pct": 0.5, "used": 0.5, "size": 100.0}, {"pct": 0.9, "used": 0.9, "size": 100.0}]) == 1.0   # 小值但已是百分比
    assert lp._vote([{"pct": 0.25, "used": 25.0, "size": 100.0}]) == 100.0
    assert lp._vote([{"pct": 80.0}, {"pct": 0.5}]) is None                                                        # 沒有 used/size 不猜


def _rows(g, k):
    return [r for x in g for r in x["metrics"][k]["rows"]]


def test_P2a_來源已是百分比_整批CPU都不到1_不會被乘100(env):
    c, drop = env
    send(drop, "wuglp_1", standard(cpu=[("d1", "", 0.95, 120), ("d2", "", 0.3, 120), ("d3", "", 0.1, 120)]))
    imp(c)
    r, _ = gen(c)
    cpu = {d["device_id"]: d for d in r["W-c"]["detail"]}
    assert r["W-c"]["verdict"] == "ok" and cpu["d1"]["state"] == "正常" and "CPU 0.95%" in cpu["d1"]["what"]


def test_P2b_來源是小數_混一個超過1_整批照換_超過100的指標不判(env):
    c, drop = env
    g = standard(cpu=[("d1", "", 0.10, 120), ("d2", "", 1.02, 120), ("d3", "", 0.2, 120)])
    for k in ("cpu", "mem", "disk"):
        for r in _rows(g, k):
            if k != "cpu" or r["avg"] > 5:
                r["avg"] = r["max"] = r["avg"] / 100
            if k != "cpu":
                r["size"], r["used"] = 100.0, round(r["avg"] * 100.0, 4)
    send(drop, "wuglp_1", g)
    imp(c)
    b = lp.latest_complete(c)
    assert b is not None                                                  # 單位問題不讓整批不完整，只停判那個指標
    vals = {v["device_id"]: v["pct"] for v in lp.values(c, b["batch_id"], ROOTP + Q_CPU, "cpu")}
    assert vals["d1"] == 10.0 and vals["d2"] == 102.0                    # 整批照記憶體／磁碟判出的「小數」換算
    r, _ = gen(c)
    assert r["W-c"]["verdict"] == "nodata" and "超過 100%" in r["W-c"]["reason"] and "102" in r["W-c"]["reason"]
    assert r["W-d"]["verdict"] in ("ok", "bad") and r["W-f"]["verdict"] in ("ok", "bad")                       # 其他指標照判


def test_P2_單位判不出或不一致_該列無資料不判正常(env):
    c, drop = env
    g = standard()
    for k in ("cpu", "mem", "disk"):
        for r in _rows(g, k):
            r.pop("used"), r.pop("size")
    send(drop, "wuglp_1", g)
    imp(c)
    r, _ = gen(c)
    for code in ("W-c", "W-d", "W-f"):
        assert r[code]["verdict"] == "nodata" and "單位判斷不出" in r[code]["reason"], code
    assert r["W-g"]["verdict"] != "nodata"                                # 輪詢只看時間，不受單位影響
    g2 = standard(disk=[("d1", "C:", 95.0, 120), ("d2", "/", 50.0, 120)])
    for r in _rows(g2, "mem"):                                            # 記憶體判成小數、磁碟判成百分比
        r["used"], r["size"] = r["avg"], 1.0
    send(drop, "wuglp_2", g2)
    imp(c)
    r, _ = gen(c)
    assert r["W-f"]["verdict"] == "nodata" and "不一致" in r["W-f"]["reason"]


def test_報表只讀到部分_照收不算不完整_該列標部分不判新鮮_輪詢不判正常(env):
    c, drop = env
    g = standard(disk=[("d1", "C:", 96.0, 120), ("d2", "/", 50.0, 120)])
    g[2]["metrics"]["disk"].update({"ok": False, "partial": True, "pages": 50, "error": "partial: 50 pages read; page cap 50 reached"})
    g[1]["metrics"]["cpu"]["rows"] = [{**x, "age": 120} for x in g[1]["metrics"]["cpu"]["rows"]]   # 輪詢群組沒有逾時的
    g[1]["metrics"]["mem"].update({"ok": False, "partial": True, "pages": 3, "error": "partial: 3 pages read; group call budget reached"})
    send(drop, "wuglp_1", g)
    assert imp(c)["imported"] == 1
    b = lp.latest_complete(c)
    assert b is not None and b["batch_id"] == "wuglp_1" and b["problems"] == []          # partial 不讓整批不完整
    disk = next(x for x in b["groups"] if x["path"] == ROOTP + Q_DISK)["disk"]
    assert disk["partial"] is True and disk["pages"] == 50 and disk["rows"] == 2
    r, _ = gen(c)
    f = r["W-f"]
    assert f["verdict"] == "bad" and f["fresh"] == 0 and "只讀到部分" in f["reason"] and "50 頁" in f["reason"]   # 讀到的照判
    assert {d["device_id"]: d["state"] for d in f["detail"]}["d3"] == "無資料"                                     # 沒讀到的逐台無資料
    p = r["W-g"]
    assert p["verdict"] == "ref" and p["fresh"] == 0 and "只讀到部分" in p["reason"]                               # 輪詢不判正常
    assert r["W-c"]["reason"] is None or "只讀到部分" not in r["W-c"]["reason"]                                    # 別的指標不受影響
    s = lp.status_view(c, dc.settings(c), tnow())
    assert next(x for x in s["groups"] if x["path"] == ROOTP + Q_DISK)["partial"] == ["disk"]
    # 一頁都沒讀到（讀取失敗）仍是不完整
    g2 = standard()
    g2[2]["metrics"]["disk"].update({"ok": False, "partial": False, "rows": [], "error": "HTTP 500"})
    send(drop, "wuglp_2", g2, counts=counts_of(g2))
    imp(c)
    assert c.execute("SELECT complete FROM wug_lp_batch WHERE batch_id = 'wuglp_2'").fetchone()[0] == 0


def test_P3_判成百分比但記憶體磁碟全部不到1_疑似單位不一致_不猜():
    """複審 P3（11-14）：used/size 單位不一致（例 MB 對 bytes）時投票會判成「已是百分比」，
    小數資料不換算就全部顯示 0.x%＝假正常。記憶體＋磁碟 ≥10 筆且全部 ≤1 → 標單位不明，不判正常。"""
    def grp(vals):
        rows = [{"pct": v, "used": v, "size": 100.0} for v in vals]   # used/size＝v/100 ≈ pct/100 → 投票判「已是百分比」
        return {"metrics": {k: {"rows": [dict(r) for r in rows]} for k in lp.METRICS}}
    w: list[str] = []
    units = lp._scale_batch([grp([0.1, 0.2, 0.3, 0.4, 0.5, 0.6])], w)
    assert all(u is None for u in units.values()) and any("單位不一致" in x for x in w)
    # 筆數不夠（<10）不擋，照投票結果
    w2: list[str] = []
    units2 = lp._scale_batch([{"metrics": {k: {"rows": [{"pct": 0.5, "used": 0.5, "size": 100.0}]} for k in lp.METRICS}}], w2)
    assert all(u == 1.0 for u in units2.values())


def test_v3_CPU報表上限_PARTIAL_不判新鮮_涵蓋率低只提示(env):
    c, drop = env
    g = standard(cpu=[("d1", "", 10.0, 120), ("d2", "", 20.0, 120)])
    g[0]["metrics"]["cpu"].update({"ok": False, "partial": True, "pages": 1,
                                   "error": "partial: WhatsUp report cap (100 devices per query) in My Network > X"})
    g[2]["metrics"]["mem"]["note"] = "low coverage: 1 devices of 3 members (no next page)"
    send(drop, "wuglp_1", g)
    imp(c)
    b = lp.latest_complete(c)
    assert b is not None
    r, _ = gen(c)
    w = r["W-c"]
    assert w["fresh"] == 0 and "只讀到部分" in w["reason"]                                # 報表上限：照判讀到的，但不能當完整
    assert {d["device_id"]: d["state"] for d in w["detail"]}["d3"] == "無資料"
    grp_ = next(x for x in b["groups"] if x["path"] == ROOTP + Q_DISK)
    assert grp_["mem"]["note"].startswith("low coverage") and r["W-f"]["verdict"] in ("ok", "bad")   # 涵蓋率低只提示，不擋


def test_已知例外_磁碟以machine_key加磁碟名對_例外不列異常_對不到的不套(env):
    """2026-11-14：1.5 磁碟列跟 5.2／Teams 用同一份已知例外（capacity_exempt.check）。"""
    import capacity_exempt as ce
    c, drop = env
    send(drop, "wuglp_1", standard())
    imp(c)
    k = ce.disk_key(ce.mk_of_ip(c, "10.99.1.1"), "C:")
    ce.set_exempt(c, {"kind": "disk", "key": k, "label": "app01 C:", "used_pct": 95.0, "source": "whatsup"}, "歸檔碟", 90, "admin")
    c.commit()
    r, _ = gen(c)
    dk = {d["device_id"]: d for d in r["W-f"]["detail"]}
    assert dk["d1"]["state"] == "正常" and "已知例外 C: 95%" in dk["d1"]["what"] and "歸檔碟" in dk["d1"]["what"]   # D: 10% 照判
    assert r["W-f"]["stats"]["exempt_disks"] == 1 and r["W-f"]["stats"]["bad_disks"] == 0 and r["W-f"]["verdict"] == "ok"
    cd = ce.candidates(c)
    assert any(x["key"] == k for x in cd["rows"])
    assert {u["ip"] for u in cd["unmapped"]} >= {"10.99.1.3"}             # 沒有資產的 IP：對不到、不能設例外


def test_輪詢_一台多顆磁碟取最近一次_舊磁碟不讓整台算沒輪詢(env):
    """11-14 .14 實測 W-g 279 台幾乎都是硬碟：WhatsUp 報表列出已不輪循的舊磁碟，原本取最舊那顆＝整台誤報。改取最近一次。"""
    c, drop = env
    send(drop, "wuglp_1", standard(disk=[("d1", "C:", 10.0, 120), ("d1", "Z:", 10.0, 999999), ("d2", "/", 10.0, 120)]))
    imp(c)
    r, _ = gen(c)
    det = {d["device_id"]: d for d in r["W-g"]["detail"]}
    assert det["d1"]["state"] != "異常" or "硬碟" not in det["d1"]["what"]      # Z: 很舊，但 C: 剛輪循過＝這台有在輪詢


# ---------------------------------------------------------------- 收集端 v4：板橋交易所設備狀態（devices.json）
BQ = "10.99.7.1"


def dev(ip=BQ, ok=True, state="Up", age=120, down=0, mons=(), err="", did="701", query=None):
    return {"query": query or ip, "label": "", "ip": ip, "id": did if ok else "", "name": f"bq-{ip}", "ok": ok, "error": err,
            "worst_state": state if ok else "", "best_state": "Up" if ok else "", "monitors_down": down if ok else None,
            "down_monitors": list(mons), "last_change": "2026-11-14T00:00:00Z", "poll": "2026-11-14T00:30:00Z" if age is not None else "",
            "age": age, "collected_at": "2026-11-14T08:31:00+08:00"}


def send_dev(drop, name, devices, when=None):
    """standard() 群組＋devices.json 一起送（manifest 兩個檔）。"""
    d = send(drop, name, standard(), when=when)
    body = json.dumps({"devices": devices}, ensure_ascii=False).encode()
    (d / "devices.json").write_bytes(body)
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    m["files"].append({"name": "devices.json", "sha256": hashlib.sha256(body).hexdigest(), "size": len(body)})
    m["count"] = 2
    (d / "manifest.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    return d


def _alerts(c, fresh=True, events=()):
    import wug_alert
    wug_alert.ensure(c)
    at = tnow() - (timedelta(minutes=2) if fresh else timedelta(hours=3))
    c.execute("INSERT INTO alert_scan (batch_id, snap_at, kind, complete, pages_read, devices_seen, problems, stopped_reason, group_json, received_at) "
              "VALUES (?,?,?,?,?,?,?,?,?,?)", ("wugalert_1", at.strftime(dc.FMT), "scan", 1, 1, 100, 0, None, json.dumps({"totalDevices": 100}),
                                              at.strftime(dc.FMT)))
    for ip, state in events:
        c.execute("INSERT INTO alert_event (wug_device_id, monitor, device_name, ip, wug_state, tier, started_at, last_seen, created_at) "
                  "VALUES (?,?,?,?,?,?,?,?,?)", ("701", "Ping", "bq", ip, state, "red", "2026-11-14 05:00:00", "2026-11-14 06:29:00", "2026-11-14 05:00:00"))
    c.commit()


def _bq(c):
    dc.save_settings(c, {"banqiao": [{"name": "交易所主機", "ip": BQ}]})
    r, _ = gen(c)
    return next(v for k, v in r.items() if k.startswith("B"))


@pytest.mark.parametrize("devices,events,verdict,needle", [
    ([dev()], (), "ok", "WhatsUp 設備 #701 狀態 Up、停止的監控 0 個、最後輪循 08:30:00"),
    ([dev(state="Down", down=1, mons=("Ping",))], ((BQ, "Down"),), "bad", "Down"),
    ([dev(state="Maintenance")], ((BQ, "Maintenance"),), "ref", "維護"),
    ([dev(state="Unknown")], (), "nodata", "不能判正常"),
    ([dev(age=4000)], (), "nodata", "分鐘前"),                                   # 輪循太舊
    ([dev(age=None, err="no last poll time in the ping report")], (), "nodata", "沒有最後輪循時間"),
    ([dev()], ((BQ, "Down"),), "nodata", "矛盾"),                                 # 快照說 Down、狀態 Up
    ([dev(state="Down", down=1)], (), "nodata", "矛盾"),                          # 狀態 Down、快照沒有
    ([dev(state="Up", down=1, mons=("HTTP",))], ((BQ, "Down"),), "bad", "HTTP"),  # 狀態 Up 但有監控停止 → 不可判正常（審查 P3）
    ([dev(ok=False, err="device not found by IP")], (), "nodata", "查不到"),       # 查不到 → 退回舊規則、寫原因
    ([dev(did="701"), dev(did="702", query="702")], (), "nodata", "2 筆"),        # 同一個 IP 兩台
    ([], (), "nodata", "wug_lp_devices.txt"),                                     # 不在清單
])
def test_v4_板橋交易所_上次輪循設備狀態_各種狀態(env, devices, events, verdict, needle):
    c, drop = env
    _alerts(c, events=events)
    send_dev(drop, "wuglp_1", devices)
    assert imp(c)["imported"] == 1
    row = _bq(c)
    assert row["verdict"] == verdict, (row["verdict"], row["value_text"], row["reason"])
    assert needle in ((row["value_text"] or "") + (row["reason"] or "")), (row["value_text"], row["reason"])
    if verdict == "ok":
        assert row["fresh"] == 1 and row["source"] == dc.LP_SRC and row["reason"] is None


def test_v4_板橋_上次輪循批次太舊_退回告警快照規則(env):
    c, drop = env
    _alerts(c, events=((BQ, "Down"),))
    send_dev(drop, "wuglp_1", [dev()], when=datetime.now(TPE) - timedelta(minutes=90))
    imp(c)
    row = _bq(c)
    assert row["verdict"] == "bad" and row["source"] == "WhatsUp 告警快照"         # 快照有明確 Down 仍判異常
    c.execute("UPDATE alert_event SET ended_at = '2026-11-14 07:00:00'")
    c.commit()
    row = _bq(c)
    assert row["verdict"] == "nodata" and "太舊" in row["reason"]


def test_v4_告警快照不可用_Up仍可判但標未交叉比對_不算新鮮(env):
    c, drop = env
    _alerts(c, fresh=False)
    send_dev(drop, "wuglp_1", [dev()])
    imp(c)
    row = _bq(c)
    assert row["verdict"] == "ok" and row["fresh"] == 0 and "沒有交叉比對" in row["reason"]


def test_v4_devices_json_壞資料整批拒收_沒有devices_json照舊(env):
    c, drop = env
    send_dev(drop, "wuglp_bad", [{"query": BQ}])                                  # 沒有 ok
    assert imp(c)["rejected"] == 1
    send(drop, "wuglp_2", standard())
    assert imp(c)["imported"] == 1
    assert lp.device_rows(c, "wuglp_2", BQ) == []


@pytest.mark.parametrize("bad", [{"age": float("inf")}, {"age": 1e400}, {"monitors_down": float("inf")}, {"age": -100000}, {"monitors_down": -1}])
def test_v4_devices_json_非有限或負數_整批拒收_不卡住後面的匯入(env, bad):
    """審查 P2／P3：Infinity 會讓 int() 丟 OverflowError 卡住整個中繼匯入；負的 age 會讓舊資料判正常。"""
    c, drop = env
    send_dev(drop, "wuglp_bad", [{**dev(), **bad}])
    assert imp(c)["rejected"] == 1
    send(drop, "wuglp_2", standard())
    assert imp(c)["imported"] == 1


def test_v4_板橋正常列_明細寫齊可對照的證據(env):
    """使用者 11-14：「只寫 WhatsUp 確認 Up 沒辦法當作證據」→ 明細要有設備 ID、WhatsUp 名稱、狀態、停止的監控、最後狀態變更與最後輪循（台北時間）、查詢時間、批次。"""
    c, drop = env
    _alerts(c)
    send_dev(drop, "wuglp_1", [dev()])
    imp(c)
    row = _bq(c)
    assert row["verdict"] == "ok"
    what = row["detail"][0]["what"]
    for s in ("#701", f"bq-{BQ}", "整體狀態 Up", "最佳 Up", "停止的監控 0 個", "最後狀態變更 2026-11-14 08:00:00",
              "最後輪循 2026-11-14 08:30:00", "查詢時距最後輪循 120 秒", "查詢時間 2026-11-14 08:31:00", "批次 wuglp_1"):
        assert s in what, (s, what)
