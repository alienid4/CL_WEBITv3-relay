"""夜間速查後端：獨立審查（2026-11-15）抓到的問題逐項釘住。

P1-1 硬碟通知編號含日期:級別、處理紀錄只配「處理時間不早於通知時間」；P2-3 告警配通知邊界比對；P2-4 通知查詢有結束時間上限；
P2-5 同一項目只能一筆未結案；P2-6 結案影響筆數；P2-7 存快照管理員＋欄位型別；P2-8 vCenter 解析同請求一次；
P2-9 快照錯誤訊息遮蔽；P3-10 月報接 KeyError／TypeError；P3-13 停用主機快照 key 含 ret:。
測試資料虛構：10.99.x、example 主機名。
"""
import json
from datetime import timedelta

import pytest

import db
import host_monthly
import host_ops
import night_desk as nd
from test_night_desk import NOW, LIVE, _conn, _deliv, _mk, _t, _wug, env  # noqa: F401  (env 是 fixture)

DAY = timedelta(days=1)


def _disk_at(c, mk, disk, at, batch, tier="critical", pct=95):
    """某一天的硬碟告警（每次通知各自的批次、送出紀錄）。"""
    key = f"disk:{batch}:{tier}:1"
    c.execute("INSERT INTO disk_alert_seen (host, disk, day, tier, pct, batch_id, first_at) VALUES (?,?,?,?,?,?,?)",
              (f"mk:{mk}", disk, at.date().isoformat(), tier, pct, batch, _t(at)))
    c.execute("INSERT INTO disk_alert_history (at, kind, batch_id, threshold, hosts, items, status, delivery_key, summary, tier) "
              "VALUES (?, 'alert', ?, 95, 1, 1, 'queued', ?, 's', ?)", (_t(at), batch, key, tier))
    _deliv(c, key, "sent", _t(at + timedelta(minutes=1)))
    c.commit()


# ------------------------------------------------------------------ P1-1
def test_前一晚已結案的硬碟_今晚同一顆碟再通知仍要在要你處理(tmp_path):
    c = _conn(tmp_path)
    mk = _mk(c)
    y = NOW - DAY - timedelta(hours=1)
    _disk_at(c, mk, "E:", y, "b-y")
    old = nd.notified(c, 12, NOW - DAY + timedelta(minutes=10))["items"][0]
    h = nd.start_handling(c, old["id"], mk, "alice", NOW - DAY + timedelta(minutes=10))
    nd.finish_handling(c, h["id"], "昨晚已清", "alice", NOW - DAY + timedelta(minutes=20))
    c.commit()
    _disk_at(c, mk, "E:", NOW - timedelta(hours=1), "b-t")
    d = nd.notified(c, 12, NOW)
    assert d["summary"]["total"] == 1
    it = d["items"][0]
    assert it["id"] != old["id"] and ":critical" in it["id"] and NOW.date().isoformat() in it["id"]
    assert it["handling"] is None and it["handled"] is False         # 昨晚的結案不能蓋掉今晚


def test_舊格式同編號的處理紀錄_處理時間早於通知就不配(tmp_path):
    c = _conn(tmp_path)
    mk = _mk(c)
    _disk_at(c, mk, "E:", NOW - timedelta(hours=1), "b-t")
    it = nd.notified(c, 12, NOW)["items"][0]
    nd.ensure(c)
    c.execute("INSERT INTO night_handling (item_id, machine_key, kind, title, notified_at, by, at, result, done_by, done_at) "
              "VALUES (?,?,?,?,?,?,?,?,?,?)", (it["id"], mk, "disk", "t", None, "bob", _t(NOW - DAY), "舊的", "bob", _t(NOW - DAY)))
    c.commit()
    assert nd.notified(c, 12, NOW)["items"][0]["handling"] is None
    h = nd.start_handling(c, it["id"], mk, "alice", NOW)
    assert h["existing"] is False
    got = nd.notified(c, 12, NOW)["items"][0]
    assert got["handled"] is True and got["handling"]["by"] == "alice"


def test_月報同一顆碟多次通知_不會全配到第一次的處理紀錄(tmp_path):
    c = _conn(tmp_path)
    mk = _mk(c)
    t1, t2 = NOW - DAY, NOW - timedelta(hours=1)
    _disk_at(c, mk, "E:", t1, "b1")
    _disk_at(c, mk, "E:", t2, "b2")
    items = nd.collect(c, NOW - 3 * DAY, NOW)
    assert len({x["id"] for x in items}) == 2                      # 兩次通知各有自己的編號
    first = min(items, key=lambda x: x["notified_at"])
    h = nd.start_handling(c, first["id"], mk, "alice", t1 + timedelta(minutes=5))
    nd.finish_handling(c, h["id"], "清了", "alice", t1 + timedelta(minutes=9))
    c.commit()
    hm = nd._handling_map(c, items)
    assert list(hm) == [first["id"]]
    m = nd.month_summary(c, "2026-11")
    top = m["hosts"][0]
    assert top["times"] == 2 and top["handled"] is True and top["first_handle_min"] == 4 and top["result"] == "清了"


# ------------------------------------------------------------------ P2-3
def test_邊界比對_IP與事件編號與主機名():
    assert nd._mentions("🔴 x（10.99.1.1）Down", "10.99.1.1")
    assert nd._mentions("10.99.1.1。", "10.99.1.1") and nd._mentions("ip=10.99.1.1.", "10.99.1.1")
    assert not nd._mentions("🔴 x（10.99.1.10）Down", "10.99.1.1")
    assert not nd._mentions("10.99.1.1.5", "10.99.1.1") and not nd._mentions("110.99.1.1", "10.99.1.1")
    assert nd._mentions("APPDB01 down", "APPDB01") and nd._mentions("appdb01.example.test down", "appdb01")
    assert not nd._mentions("APPDB010 down", "APPDB01") and not nd._mentions("XAPPDB01 down", "APPDB01")
    assert not nd._mentions("anything", "")


def test_告警配通知_10_99_1_1不配到10_99_1_10的通知(tmp_path):
    c = _conn(tmp_path)
    mk1 = _mk(c)
    _wug(c, "APPDB01", "10.99.1.10", mk1, NOW - timedelta(hours=2))            # 事件 #1，通知內文 10.99.1.10
    c.execute("INSERT INTO alert_event (wug_device_id, monitor, device_name, ip, machine_key, hostname, tier, started_at, ended_at, last_seen, "
              "notified, created_at, wug_state) VALUES ('z','(設備)','ZZ','10.99.1.1',NULL,'ZZ','red',?,NULL,?,',open,','x','Down')",
              (_t(NOW - timedelta(hours=2)), _t(NOW - timedelta(hours=2))))                 # 事件 #2：IP 是 .1，自己沒通知
    c.commit()
    d = nd.notified(c, 12, NOW)
    assert [x["id"] for x in d["items"]] == ["wug:1"]


def test_告警配通知_事件編號1不配到12(tmp_path):
    c = _conn(tmp_path)
    c.execute("INSERT INTO alert_event (id, wug_device_id, monitor, device_name, ip, hostname, tier, started_at, last_seen, notified, created_at, wug_state) "
              "VALUES (1,'a','(設備)','AAA','10.99.8.1','AAA','red',?,?,',open,','x','Down')", (_t(NOW - timedelta(hours=2)),) * 2)
    c.execute("INSERT INTO alert_notice (at, kind, tier, events, status, delivery_key, title, summary) VALUES (?, 'open', 'red', 1, 'queued', 'k12', 't', ?)",
              (_t(NOW - timedelta(hours=2)), "別台 事件 #12"))
    _deliv(c, "k12", "sent", _t(NOW - timedelta(hours=2)))
    c.commit()
    assert nd.notified(c, 12, NOW)["items"] == []


# ------------------------------------------------------------------ P2-4
def test_通知查詢有結束時間上限_未來的通知不列(tmp_path):
    c = _conn(tmp_path)
    c.execute("INSERT INTO alert_notice (at, kind, tier, events, status, delivery_key, title) VALUES (?, 'heartbeat_down', 'red', 0, 'queued', 'hb1', '中斷')",
              (_t(NOW + timedelta(hours=1)),))
    _deliv(c, "hb1", "sent", _t(NOW + timedelta(hours=1)))
    c.commit()
    assert nd.notified(c, 12, NOW)["items"] == []
    assert len(nd.notified(c, 12, NOW + timedelta(hours=2))["items"]) == 1


def test_配通知只看事件時段前後5分鐘(tmp_path):
    c = _conn(tmp_path)
    mk = _mk(c)
    st = NOW - timedelta(hours=2)
    c.execute("INSERT INTO alert_event (id, wug_device_id, monitor, device_name, ip, machine_key, hostname, tier, started_at, ended_at, last_seen, notified, created_at, wug_state) "
              "VALUES (1,'a','(設備)','APPDB01','10.99.1.10',?,'APPDB01','red',?,?,?,',open,','x','Down')", (mk, _t(st), _t(st + timedelta(minutes=30)), _t(st)))
    c.execute("INSERT INTO alert_notice (at, kind, tier, events, status, delivery_key, title, summary) VALUES (?, 'open', 'red', 1, 'queued', 'kfar', 't', 'APPDB01 Down')",
              (_t(st - timedelta(minutes=6)),))              # 開始前 6 分鐘＝太早
    _deliv(c, "kfar", "sent", _t(st - timedelta(minutes=6)))
    c.commit()
    assert nd.notified(c, 12, NOW)["items"] == []
    c.execute("INSERT INTO alert_notice (at, kind, tier, events, status, delivery_key, title, summary) VALUES (?, 'open', 'red', 1, 'queued', 'knear', 't', 'APPDB01 Down')",
              (_t(st - timedelta(minutes=4)),))
    _deliv(c, "knear", "sent", _t(st - timedelta(minutes=4)))
    c.commit()
    assert len(nd.notified(c, 12, NOW)["items"]) == 1


# ------------------------------------------------------------------ P2-5 / P2-6
def test_同一項目只能一筆未結案_撞到回已存在那一筆(tmp_path):
    c = _conn(tmp_path)
    mk = _mk(c)
    rid = _wug(c, "APPDB01", "10.99.1.10", mk, NOW - timedelta(hours=2))
    iid = f"wug:{rid}"
    nd.ensure(c)
    with pytest.raises(Exception) as ei:                                   # 直接寫第二筆未結案：資料庫擋下
        for _ in range(2):
            c.execute("INSERT INTO night_handling (item_id, machine_key, by, at) VALUES (?,?,?,?)", (iid, mk, "x", _t(NOW - DAY)))
    assert "UNIQUE" in str(ei.value).upper()
    c.rollback()
    # 已有一筆「處理時間早於通知」的未結案：程式查不到它，INSERT 撞索引 → 回那一筆，不是 500
    c.execute("INSERT INTO night_handling (item_id, machine_key, by, at) VALUES (?,?,?,?)", (iid, mk, "bob", _t(NOW - DAY)))
    c.commit()
    got = nd.start_handling(c, iid, mk, "alice", NOW)
    assert got["existing"] is True and got["by"] == "bob"
    assert c.execute("SELECT COUNT(*) FROM night_handling WHERE item_id = ?", (iid,)).fetchone()[0] == 1


def test_結案時影響筆數不是1就409(tmp_path):
    c = _conn(tmp_path)
    mk = _mk(c)
    rid = _wug(c, "APPDB01", "10.99.1.10", mk, NOW - timedelta(hours=2))
    h = nd.start_handling(c, f"wug:{rid}", mk, "alice", NOW)
    c.commit()

    class Racer:                       # 在我們的 UPDATE 之前，別人先結案了
        def __init__(self, conn):
            self.c = conn

        def execute(self, sql, params=()):
            if sql.startswith("UPDATE night_handling"):
                self.c.execute("UPDATE night_handling SET done_at = ?, result = 'other', done_by = 'bob' WHERE id = ?", (_t(NOW), h["id"]))
            return self.c.execute(sql, params)

    with pytest.raises(PermissionError):
        nd.finish_handling(Racer(c), h["id"], "我也要寫", "alice", NOW)
    assert c.execute("SELECT result FROM night_handling WHERE id = ?", (h["id"],)).fetchone()[0] == "other"


# ------------------------------------------------------------------ P2-7
def test_存快照一般帳號403_管理員可_欄位型別與長度(env):
    c = env.conn()
    mk = _mk(c)
    c.close()
    a, adm = env.client("alice"), env.client("admin")
    url = "/api/v2/night/check-snapshot"
    assert a.post(url, json={"key": mk, "result": LIVE}).status_code == 403
    assert env.client("vera").post(url, json={"key": mk, "result": LIVE}).status_code == 403
    assert adm.post(url, json={"key": mk, "result": LIVE}).status_code == 200
    bad = [{k: v for k, v in LIVE.items() if k != "ip"},                                  # 沒帶 ip
           {**LIVE, "ip": ""}, {**LIVE, "ip": {"a": 1}},                                   # ip 不是字串
           {**LIVE, "platform": "x" * 201},                                                # 字串太長
           {**LIVE, "platform": {"nested": 1}},                                            # 巢狀物件
           {**LIVE, "reachable": "yes"}, {**LIVE, "uptime": "5"},
           {**LIVE, "uptime": {"uptime_sec": {"x": 1}}},
           {**LIVE, "disks": [{"mount": ["C:"], "use_pct": 1}]}, {**LIVE, "disks": ["C:"]},
           {**LIVE, "failed_units": [{"a": 1}]}, {**LIVE, "failed_units": ["x" * 201]},
           {**LIVE, "ports": [{"port": {"n": 1}}]}, {**LIVE, "error": 5}]
    for r in bad:
        assert adm.post(url, json={"key": mk, "result": r}).status_code == 400, r
    c = env.conn()
    assert c.execute("SELECT COUNT(*) FROM night_check_snapshot").fetchone()[0] == 1       # 擋下的都沒寫進去
    c.close()


# ------------------------------------------------------------------ P2-8
def test_vCenter解析同一個請求只做一次(tmp_path, monkeypatch):
    c = _conn(tmp_path)
    db.insert_hardware(c, asset_serial="S-10", hostname="esx02", ip="10.99.9.2", os="VMware ESXi 7.0.3", asset_status="使用中")
    c.execute("INSERT INTO source_record (source, payload, collected_at, resolved_hardware_id) VALUES ('vcenter', ?, '2026-11-15 01:30:00', NULL)",
              (json.dumps({"vm_name": "vm01", "esxi_host": "esx01.example.test", "powerstate": "poweredOn"}),))
    c.commit()
    for ip, nm in (("10.99.9.1", "esx01"), ("10.99.9.2", "esx02")):
        _wug(c, nm, ip, _mk(c, ip), NOW - timedelta(hours=2))
    calls = []
    real = nd._vcenter_rows
    monkeypatch.setattr(nd, "_vcenter_rows", lambda conn: calls.append(1) or real(conn))
    d = nd.notified(c, 12, NOW)
    assert d["summary"]["total"] == 2 and len(calls) == 1
    esx1 = next(x for x in d["items"] if x["ip"] == "10.99.9.1")
    assert esx1["impact"]["hosts"] == 1                                    # 快取不影響結果


# ------------------------------------------------------------------ P2-9 / P3-13
def test_快照的錯誤訊息先遮蔽再截300字(tmp_path):
    c = _conn(tmp_path)
    mk = _mk(c)
    err = "connect failed password=Sup3rSecretValue99 " + "x" * 600
    out = nd.save_snapshot(c, mk, {**LIVE, "reachable": False, "error": err}, "admin", NOW)
    stored = json.loads(c.execute("SELECT result_json FROM night_check_snapshot WHERE id = ?", (out["id"],)).fetchone()[0])
    assert "Sup3rSecretValue99" not in stored["error"] and len(stored["error"]) <= 300


def test_停用主機的快照key存完整_讀取兩種都查(tmp_path):
    c = _conn(tmp_path)
    db.insert_hardware(c, asset_serial="S-99", hostname="OLDHOST", ip="10.99.1.99", os="Red Hat Enterprise Linux 9", asset_status="報廢")
    c.commit()
    import oncall
    key = [k for k in oncall._groups(c)[0] if k.startswith("ret:")][0]
    out = nd.save_snapshot(c, key, {**LIVE, "ip": "10.99.1.99"}, "admin", NOW)
    assert c.execute("SELECT machine_key FROM night_check_snapshot WHERE id = ?", (out["id"],)).fetchone()[0] == key
    bare = key[4:]
    assert nd._latest_snapshot(c, bare)["id"] == out["id"] and nd._latest_snapshot(c, key)["id"] == out["id"]
    # 舊資料（只存了不含 ret: 的 key）也讀得到
    c.execute("UPDATE night_check_snapshot SET machine_key = ?", (bare,))
    c.commit()
    assert nd._latest_snapshot(c, key)["id"] == out["id"]
    evs, _cov = nd.events_for_host(c, {"key": key, "ips": ["10.99.1.99"], "retired": True}, _t(NOW - DAY), _t(NOW + DAY))
    assert any(e["source"] == "night_check" for e in evs)
    c.execute("UPDATE night_check_snapshot SET machine_key = ?", (key,))
    c.commit()
    evs, _cov = nd.events_for_host(c, {"key": key, "ips": ["10.99.1.99"], "retired": True}, _t(NOW - DAY), _t(NOW + DAY))
    assert any(e["source"] == "night_check" for e in evs)


# ------------------------------------------------------------------ P3-10
def test_月報夜間段讀不到時照實回_不炸(tmp_path, monkeypatch):
    c = _conn(tmp_path)
    for exc in (KeyError("k"), TypeError("t")):
        def boom(conn, month, _e=exc):
            raise _e
        monkeypatch.setattr(nd, "month_summary", boom)
        r = host_monthly._night(c, "2026-11")
        assert r["count"] is None and "讀不到" in r["reason"] and r["hosts"] == []
