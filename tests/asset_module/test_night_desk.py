"""夜間速查改版後端（night_desk.py＋routers/night.py；介面約定 06 第 1～5 節）。

守的事：
  · 今晚通知清單只列 Teams **真的送出**的（排進佇列沒送出的不列）；同一台合併一列、times_tonight 累計；未恢復在前。
  · 緊急檢查彙整只讀：活著／剛重開／連不上／不知道（不猜）；ESXi 本身不算，算上面開機中的 VM，>3 台＝影響大；
    聯絡人分得出「沒有資料來源」與「有登記沒填」。
  · 寫入（快照、我在處理、處理結果）一般帳號以上、登入停用 403、只能結案一次、不可刪；寫進主機履歷與 10.1 月報。
測試資料虛構：10.99.x、example 主機名。
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
import disk_alert  # noqa: E402
import host_monthly  # noqa: E402
import host_ops  # noqa: E402
import night_desk as nd  # noqa: E402
import teams_notify as tn  # noqa: E402
import wug_alert  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

NOW = datetime(2026, 11, 15, 2, 0, 0)
F = nd.FMT
_PW = "s3cure-pass!"


def _t(d: datetime) -> str:
    return d.strftime(F)


def _seed(c):
    db.insert_hardware(c, asset_serial="S-1", hostname="APPDB01", ip="10.99.1.10", os="Microsoft Windows Server 2019",
                       asset_status="使用中", api_id="A01", custodian="王小明", environment="正式")
    db.insert_hardware(c, asset_serial="S-2", hostname="APPWEB02", ip="10.99.1.11", os="Red Hat Enterprise Linux 9",
                       asset_status="使用中")
    db.insert_hardware(c, asset_serial="S-9", hostname="esx01", ip="10.99.9.1", os="VMware ESXi 7.0.3", asset_status="使用中")
    c.execute("INSERT INTO business_system (api_id, name, ap_owner) VALUES ('A01', '交割系統', '')")
    c.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES ('teams_alert_contact', '')")
    wug_alert.ensure(c)
    tn.ensure(c)
    for s in disk_alert.SQL:
        c.execute(s)
    c.commit()


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    _seed(c)
    return c


def _mk(c, ip="10.99.1.10"):
    return host_ops.machine_info(c, ip)["key"]


def _deliv(c, key, status="sent", sent_at=None):
    c.execute("INSERT INTO teams_notify_delivery (event_key, kind, payload_json, status, created_at, sent_at) VALUES (?,?,?,?,?,?)",
              (key, "x", "{}", status, _t(NOW - timedelta(hours=1)), sent_at))


def _wug(c, eid_name, ip, mk, started, ended=None, notified=",open,", status="sent", monitor="(設備)", tier="red"):
    rid = c.execute("INSERT INTO alert_event (wug_device_id, monitor, device_name, ip, machine_key, hostname, tier, started_at, ended_at, last_seen, "
                    "notified, created_at, wug_state) VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'Down')",
                    (eid_name, monitor, eid_name, ip, mk, eid_name, tier, _t(started), _t(ended) if ended else None, _t(started), notified, "x")).lastrowid
    key = f"wug:open:{rid}"
    c.execute("INSERT INTO alert_notice (at, kind, tier, events, status, delivery_key, title, summary) VALUES (?, 'open', 'red', 1, 'queued', ?, ?, ?)",
              (_t(started + timedelta(minutes=1)), key, "t", f"🔴 {eid_name}（{ip}）設備 Down｜事件 #{rid}"))
    _deliv(c, key, status, _t(started + timedelta(minutes=2)) if status == "sent" else None)
    c.commit()
    return rid


def _disk(c, mk, disk, pct, at, status="sent"):
    key = f"disk:b-{disk}:critical:1"
    c.execute("INSERT INTO disk_alert_seen (host, disk, day, tier, pct, batch_id, first_at) VALUES (?,?,?,?,?,?,?)",
              (f"mk:{mk}", disk, at.date().isoformat(), "critical", pct, f"b-{disk}", _t(at)))
    c.execute("INSERT INTO disk_alert_history (at, kind, batch_id, threshold, hosts, items, status, delivery_key, summary, tier) "
              "VALUES (?, 'alert', ?, 95, 1, 1, 'queued', ?, 's', 'critical')", (_t(at), f"b-{disk}", key))
    _deliv(c, key, status, _t(at + timedelta(minutes=1)) if status == "sent" else None)
    c.commit()


# ------------------------------------------------------------------ 第 1 節：今晚通知清單
def test_只列Teams真的送出的_同一台合併_未恢復在前(tmp_path):
    c = _conn(tmp_path)
    mk1, mk2 = _mk(c), _mk(c, "10.99.1.11")
    _wug(c, "APPDB01", "10.99.1.10", mk1, NOW - timedelta(hours=2))
    _disk(c, mk1, "E:", 95, NOW - timedelta(hours=1))
    _wug(c, "APPWEB02", "10.99.1.11", mk2, NOW - timedelta(hours=3), ended=NOW - timedelta(hours=2, minutes=30))
    _wug(c, "GHOST01", "10.99.1.77", None, NOW - timedelta(hours=1), status="pending")          # 排進佇列沒送出：不列
    _wug(c, "OLD01", "10.99.1.78", None, NOW - timedelta(hours=20))                               # 時窗外
    d = nd.notified(c, 12, NOW)
    assert d["summary"]["total"] == 2 and d["summary"]["open"] == 1
    first, second = d["items"]
    assert first["machine_key"] == mk1 and first["open"] is True and first["times_tonight"] == 2
    assert {p["kind"] for p in first["parts"]} == {"wug", "disk"}
    assert first["system"]["api_id"] == "A01" and first["system"]["name"] == "交割系統"
    assert second["open"] is False and second["recovered_at"]
    assert [x["no"] for x in d["items"]] == [1, 2]
    assert d["older"]["count"] == 1                        # OLD01 還沒恢復、在時窗外
    assert all(x["name"] != "GHOST01" for x in d["items"])


def test_沒有通知時_回空清單不是錯誤(tmp_path):
    c = _conn(tmp_path)
    d = nd.notified(c, 12, NOW)
    assert d["items"] == [] and d["summary"] == {"total": 0, "open": 0, "tier1": 0, "big_impact": 0}


# ------------------------------------------------------------------ 第 2 節：緊急檢查彙整
def _snap(c, mk, at, uptime_sec=None, reachable=True):
    nd.ensure(c)
    res = {"reachable": reachable, "uptime": {"uptime_sec": uptime_sec} if uptime_sec is not None else None}
    c.execute("INSERT INTO night_check_snapshot (machine_key, ip, at, by, result_json) VALUES (?,?,?,?,?)", (mk, "10.99.1.10", _t(at), "t", json.dumps(res)))
    c.commit()


def test_活著_剛重開_連不上_不知道(tmp_path):
    c = _conn(tmp_path)
    mk = _mk(c)
    assert nd.alive_of(c, mk, ["10.99.1.10"], NOW)["state"] == "unknown"
    _snap(c, mk, NOW - timedelta(minutes=5), uptime_sec=45 * 86400)
    a = nd.alive_of(c, mk, ["10.99.1.10"], NOW)
    assert a["state"] == "alive" and a["uptime_days"] == 45 and "沒有重開過" in a["text"] and a["source"] == "即時檢測"
    _snap(c, mk, NOW - timedelta(minutes=2), uptime_sec=600)
    assert nd.alive_of(c, mk, ["10.99.1.10"], NOW)["state"] == "rebooted"
    _snap(c, mk, NOW - timedelta(minutes=1), reachable=False)
    assert nd.alive_of(c, mk, ["10.99.1.10"], NOW)["state"] == "unknown"            # 連不上但監控沒斷：不判
    _wug(c, "APPDB01", "10.99.1.10", mk, NOW - timedelta(minutes=20))
    assert nd.alive_of(c, mk, ["10.99.1.10"], NOW)["state"] == "unreachable"
    assert nd.alive_of(c, mk, ["10.99.1.10"], NOW + timedelta(hours=1))["state"] == "unknown"   # 快照超過 30 分鐘不採用


def test_ESXi本身不算_算上面開機中的VM_超過三台是影響大(tmp_path):
    c = _conn(tmp_path)
    for i in range(5):
        hid = db.insert_hardware(c, asset_serial=f"V-{i}", hostname=f"vm0{i}", ip=f"10.99.2.{i + 1}", asset_status="使用中", api_id="A01")
        c.execute("INSERT INTO source_record (source, payload, collected_at, resolved_hardware_id) VALUES ('vcenter', ?, '2026-11-15 01:30:00', ?)",
                  (json.dumps({"vm_name": f"vm0{i}", "hostname": f"vm0{i}", "esxi_host": "esx01.example.test",
                               "powerstate": "poweredOn" if i < 4 else "poweredOff"}), hid))
    c.commit()
    w = nd._World(c)
    k = _mk(c, "10.99.9.1")
    assert w.platform(k) == "esxi"
    imp = nd.impact(c, w, k)
    assert imp["hosts"] == 4 and imp["big"] is True and imp["vm_data_at"] == "2026-11-15 01:30:00"
    assert [v["power"] for v in imp["vms"]].count("on") == 4 and imp["systems"][0]["api_id"] == "A01"
    c.execute("UPDATE source_record SET payload = replace(payload, 'poweredOn', 'poweredOff') WHERE resolved_hardware_id IN "
              "(SELECT id FROM hardware WHERE asset_serial = 'V-0')")
    c.commit()
    assert nd.impact(c, nd._World(c), k)["big"] is False                    # 3 台＝不是「超過 3 台」
    assert nd.impact(c, nd._World(c), _mk(c))["hosts"] == 1                  # 一般主機＝自己


def test_聯絡人_沒有資料來源跟有登記沒填分開(tmp_path):
    c = _conn(tmp_path)
    w = nd._World(c)
    cs = nd.contacts_of(c, _mk(c), w)
    ap = next(x for x in cs if x["role"] == "AP 負責人")
    assert ap["state"] == "blank"                                           # 有業務系統、AP 負責人沒填
    assert next(x for x in cs if x["role"] == "SP 負責人")["name"] == "王小明"
    assert next(x for x in cs if x["role"] == "主管")["state"] == "no_source"
    cs2 = nd.contacts_of(c, _mk(c, "10.99.1.11"), w)
    assert next(x for x in cs2 if x["role"] == "AP 負責人")["state"] == "no_source"     # 沒掛 APID
    assert next(x for x in cs2 if x["role"] == "SP 負責人")["state"] == "blank"


def test_彙整端點_一頁的欄位都在_找不到的主機不炸(tmp_path):
    c = _conn(tmp_path)
    d = nd.host(c, "10.99.1.10", NOW)
    for k in ("alive", "baseline", "impact", "contacts", "history", "events_tonight"):
        assert k in d
    assert len(d["history"]) == 7 and d["baseline"]["status"] in ("none", "insufficient", "ok")
    x = nd.host(c, "10.99.250.250", NOW)
    assert x["found"] is False and x["alive"]["state"] == "unknown"


# ------------------------------------------------------------------ 第 3～5 節：API、寫入、主機履歷、月報
@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.delenv(api.AUTH_DISABLED_ENV, raising=False)
    monkeypatch.setattr(nd, "_now", lambda: NOW)
    p = tmp_path / "t.db"
    db.init_db(p)
    c0 = db.get_connection(p)
    _seed(c0)
    for n, role in (("admin", "admin"), ("alice", "user"), ("vera", "viewer")):
        db.create_user(c0, n, auth.hash_password(_PW), role=role)
    c0.close()

    def _ov():
        cc = db.get_connection(p)
        try:
            yield cc
        finally:
            cc.close()

    api.app.dependency_overrides[api.get_db] = _ov

    class E:
        def client(self, name):
            t = TestClient(api.app)
            assert t.post("/api/auth/login", json={"username": name, "password": _PW}).status_code == 200
            return t

        def conn(self):
            return db.get_connection(p)

    yield E()
    api.app.dependency_overrides.pop(api.get_db, None)


LIVE = {"ip": "10.99.1.10", "platform": "windows", "reachable": True, "checked_at": "2026-11-15 01:59:00",
        "uptime": {"uptime_sec": 45 * 86400, "days": 45.0}, "disks": [{"mount": "C:", "use_pct": 41}], "ports": [{"port": 3389}],
        "failed_units": [], "top_cpu": [{"cmd": "x" * 5000}]}


def test_API_讀登入即可_寫入一般帳號以上_結案只能一次(env):
    c = env.conn()
    mk = _mk(c)
    _wug(c, "APPDB01", "10.99.1.10", mk, NOW - timedelta(hours=2))
    c.close()
    v, a = env.client("vera"), env.client("alice")
    r = v.get("/api/v2/night/notified", params={"hours": 12})
    assert r.status_code == 200 and r.json()["summary"]["total"] == 1
    iid = r.json()["items"][0]["id"]
    assert v.get("/api/v2/night/host", params={"key": mk}).status_code == 200
    assert v.get("/api/v2/night/host").status_code == 400
    assert v.post("/api/v2/night/handle", json={"item_id": iid, "key": mk}).status_code == 403
    assert v.post("/api/v2/night/check-snapshot", json={"key": mk, "result": LIVE}).status_code == 403
    assert a.post("/api/v2/night/check-snapshot", json={"key": mk, "result": LIVE}).status_code == 403   # 存快照＝管理員（跟即時檢測同級）
    adm = env.client("admin")
    s = adm.post("/api/v2/night/check-snapshot", json={"key": mk, "result": LIVE})
    assert s.status_code == 200 and s.json()["id"] and "baseline" in s.json()
    assert adm.post("/api/v2/night/check-snapshot", json={"key": mk, "result": {**LIVE, "ip": "10.99.1.11"}}).status_code == 400
    assert a.post("/api/v2/night/handle", json={"item_id": "wug:999999", "key": mk}).status_code == 404     # 不在清單上的不能標
    h = a.post("/api/v2/night/handle", json={"item_id": iid, "key": mk})
    assert h.status_code == 200 and h.json()["by"] == "alice"
    again = a.post("/api/v2/night/handle", json={"item_id": iid, "key": mk})
    assert again.json()["id"] == h.json()["id"] and again.json()["existing"] is True                    # 連點不會多一筆
    hid = h.json()["id"]
    assert a.post(f"/api/v2/night/handle/{hid}/result", json={"result": "  "}).status_code == 400
    ok = a.post(f"/api/v2/night/handle/{hid}/result", json={"result": "已清 E 碟備份，降到 70%"})
    assert ok.status_code == 200 and ok.json()["done_by"] == "alice"
    assert a.post(f"/api/v2/night/handle/{hid}/result", json={"result": "改一下"}).status_code == 409
    n = v.get("/api/v2/night/notified").json()["items"][0]["handling"]
    assert n["by"] == "alice" and n["result"].startswith("已清")
    c = env.conn()
    stored = json.loads(c.execute("SELECT result_json FROM night_check_snapshot").fetchone()[0])
    assert "top_cpu" not in stored                                         # 只留比對要用的欄位
    assert c.execute("SELECT COUNT(*) FROM activity_log WHERE action LIKE 'night_%'").fetchone()[0] == 3
    c.close()


def test_登入停用時寫入一律403(env, monkeypatch):
    c = env.conn()
    mk = _mk(c)
    c.close()
    monkeypatch.setenv(api.AUTH_DISABLED_ENV, "1")
    t = TestClient(api.app)
    assert t.post("/api/v2/night/check-snapshot", json={"key": mk, "result": LIVE}).status_code == 403
    assert t.post("/api/v2/night/handle", json={"item_id": "x", "key": mk}).status_code == 403
    assert t.post("/api/v2/night/handle/1/result", json={"result": "x"}).status_code == 403


def test_主機履歷看得到硬碟告警_緊急檢查快照_處理紀錄(tmp_path):
    c = _conn(tmp_path)
    mk = _mk(c)
    _wug(c, "APPDB01", "10.99.1.10", mk, NOW - timedelta(hours=2))
    _disk(c, mk, "E:", 95, NOW - timedelta(hours=1))
    nd.save_snapshot(c, mk, LIVE, "alice", NOW)
    h = nd.start_handling(c, "wug:1", mk, "alice", NOW + timedelta(minutes=7))
    nd.finish_handling(c, h["id"], "重開服務後恢復", "alice", NOW + timedelta(minutes=30))
    c.commit()
    d = host_ops.build(c, mk, "7d", NOW + timedelta(hours=1))
    srcs = {e["source"] for e in d["events"]}
    assert {"disk_alert", "night_check", "night_handling"} <= srcs
    hd = next(e for e in d["events"] if e["source"] == "night_handling")
    assert hd["status"] == "closed" and "重開服務後恢復" in hd["metric_text"]
    assert any(ev["k"] == "通知到接手" and ev["v"] == "125 分鐘" for ev in hd["evidence"])
    counts = {(r["source"], r["category"]) for r in d["counts"]}
    assert ("disk_alert", "disk") in counts and not any(s.startswith("night") for s, _ in counts)   # 紀錄不計次


def test_月報夜間段_哪幾台幾次_通知到處理幾分_沒人處理幾台(tmp_path):
    c = _conn(tmp_path)
    mk1, mk2 = _mk(c), _mk(c, "10.99.1.11")
    _wug(c, "APPDB01", "10.99.1.10", mk1, NOW - timedelta(hours=2))
    _disk(c, mk1, "E:", 95, NOW - timedelta(hours=1))
    _wug(c, "APPWEB02", "10.99.1.11", mk2, NOW - timedelta(hours=3))
    h = nd.start_handling(c, "wug:1", mk1, "alice", NOW - timedelta(hours=2) + timedelta(minutes=12))
    nd.finish_handling(c, h["id"], "清了暫存檔", "alice", NOW)
    c.commit()
    m = nd.month_summary(c, "2026-11")
    assert m["count"] == 3 and m["unhandled"] == 1
    top = m["hosts"][0]
    assert top["machine_key"] == mk1 and top["times"] == 2 and top["first_handle_min"] == 10 and top["result"] == "清了暫存檔"
    assert nd.month_summary(c, "2026-10")["count"] == 0
    rep = host_monthly.report(c, "2026-11")
    assert rep["night"]["count"] == 3


def test_資產庫找不到的主機_今晚事件用IP對告警與硬碟通知(tmp_path):
    """2.066.28：.14 實機發現——通知過的交換器不在資產庫，緊急檢查頁「今晚事件」卻是空的。"""
    c = _conn(tmp_path)
    ip = "10.99.250.250"                                              # 資產庫沒有這台
    _wug(c, "SW-GHOST", ip, None, NOW - timedelta(hours=2), ended=NOW - timedelta(hours=1))
    x = nd.host(c, ip, NOW)
    assert x["found"] is False
    assert len(x["events_tonight"]) == 1 and "WhatsUp" in x["events_tonight"][0]["what"]
    assert x["events_tonight"][0]["recovered_at"]
    # 時窗外的不列
    c2 = _conn(tmp_path / "b") if (tmp_path / "b").mkdir() is None else None
    _wug(c2, "SW-OLD", "10.99.250.251", None, NOW - timedelta(hours=30), ended=NOW - timedelta(hours=29))
    assert nd.host(c2, "10.99.250.251", NOW)["events_tonight"] == []
    # 不是 IP 的 key（主機名）查不到就是空，不猜
    assert nd.host(c, "NOSUCHHOST", NOW)["events_tonight"] == []
