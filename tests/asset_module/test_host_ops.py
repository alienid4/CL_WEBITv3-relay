"""單機維運記錄本（host_ops.py，ACT-01/05/02）。

守的事：事件關聯（IP／主機名對回資產登記；歧義、換 IP、重用 IP、退役一律不混進別台，列待核）、
計次規則（disk 按分割區、持續告警／重送不加計、解除後再出現才加計、跨來源不加總）、缺資料不冒充正常、
處理紀錄寫入（改前改後、更正／作廢不抹除、稽核同交易、讀取不寫庫）、MD 與畫面同批次且排除機密。
測試資料一律虛構：10.99.x、example 主機名。
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
import host_ops as ho  # noqa: E402
import incident  # noqa: E402
import nmon_series as ns  # noqa: E402
import whatsup_service as wu  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

NOW = datetime(2026, 11, 1, 12, 0, 0)
_PW = "s3cure-pass!"
ROLE_AWARE_TESTS = True


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _hw(conn, serial, host, ip, status=None):
    db.insert_hardware(conn, asset_serial=serial, hostname=host, ip=ip, asset_status=status, physical_location="01_甲機房", rack_no="M10/10U")


def _seed(conn):
    _hw(conn, "S-1", "APPDB01", "10.99.1.10")
    _hw(conn, "S-2", "APPWEB02", "10.99.1.11")
    _hw(conn, "S-3", "SHARED-A", "10.99.1.50")      # 兩台登記同一個 IP（共用／VIP）
    _hw(conn, "S-4", "SHARED-B", "10.99.1.50")
    wu.ensure_tables(conn)
    conn.commit()


def _h(conn, at, dev, name, ip, event, problem, new_state="Down", old_state=None):
    conn.execute("INSERT INTO whatsup_alert_history (at, device_id, name, ip, event, old_state, new_state, problem, group_name) "
                 "VALUES (?,?,?,?,?,?,?,?,'g')", (at, dev, name, ip, event, old_state, new_state, problem))
    conn.commit()


def _mine(conn, key="APPDB01", period="30d"):
    return ho.build(conn, key, period, NOW)


def _ids(d, cat=None):
    return [e["id"] for e in d["events"] if cat is None or e["category"] == cat]


# ------------------------------------------------------------------ 分類
def test_問題文字分類_磁碟分割區_記憶體_截斷_空白():
    items, tr = ho.classify_problem("Disk Free Space - C:、Disk Free Space - /var、Physical Memory、CPU Utilization、Ping")
    assert tr is False
    assert [(c, p) for c, p, _ in items] == [("disk", "C:"), ("disk", "/var"), ("memory", ""), ("cpu", ""), ("other", "Ping")]
    items, tr = ho.classify_problem("Disk Free Space - D:、Disk Queue 等 8 項")
    assert tr is True and ("disk", "D:") in [(c, p) for c, p, _ in items]
    assert ("disk", ho.NO_DATA) in [(c, p) for c, p, _ in items]            # 抓不到分割區＝未取得，不亂湊
    items, tr = ho.classify_problem("")
    assert items == [("other", ho.NO_DATA, "")] and tr is True               # 沒讀到說明不能當恢復
    items, _ = ho.classify_problem("Check http://example.test/x")
    assert items[0][0] == "other"                                            # URL 裡的 p: 不是 P 槽


# ------------------------------------------------------------------ WhatsUp 計次
def test_持續告警與重送不加計_解除後再出現才加計_按分割區(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    _h(c, "2026-10-20 08:00:00", "d1", "appdb01", "10.99.1.10", "new", "Disk Free Space - C:")
    _h(c, "2026-10-20 09:00:00", "d1", "appdb01", "10.99.1.10", "changed", "Disk Free Space - C:、Disk Free Space - D:", "Warning", "Down")
    _h(c, "2026-10-20 09:30:00", "d1", "appdb01", "10.99.1.10", "changed", "Disk Free Space - C:、Disk Free Space - D:", "Down", "Warning")   # 重送／狀態來回
    _h(c, "2026-10-20 10:00:00", "d1", "appdb01", "10.99.1.10", "cleared", "Disk Free Space - C:、Disk Free Space - D:", None, "Down")
    _h(c, "2026-10-25 08:00:00", "d1", "appdb01", "10.99.1.10", "new", "Disk Free Space - C:")      # 解除後再出現＝再次獨立發生
    d = _mine(c)
    cnt = {(r["category"], r["partition"]): r["count"] for r in d["counts"]}
    assert cnt[("disk", "C:")] == 2 and cnt[("disk", "D:")] == 1
    assert d["totals"]["disk"] == 3 and len(d["events"]) == 3
    ev = {e["id"]: e for e in d["events"]}
    ongoing = [e for e in d["events"] if e["status"] == "ongoing" or e["status"] == "unknown"]
    assert len(ongoing) == 1 and ongoing[0]["partition"] == "C:" and ongoing[0]["first_at"] == "2026-10-25 08:00:00"
    first = [e for e in d["events"] if e["first_at"] == "2026-10-20 08:00:00"][0]
    assert first["recovered_at"] == "2026-10-20 10:00:00" and first["status"] == "recovered"
    # 計次＝下鑽明細同一份
    for r in d["counts"]:
        assert r["count"] == len(r["event_ids"]) and all(i in ev for i in r["event_ids"])
    assert d["events"][0]["metric_text"].startswith(ho.NO_DATA)              # WhatsUp 沒有量測值：不編
    assert [e["__no"] for e in d["events"]] == list(range(1, len(d["events"]) + 1))


def test_同一段告警內項目消失再出現_算再次發生_說明被截斷時不推論恢復(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    _h(c, "2026-10-20 08:00:00", "d1", "appdb01", "10.99.1.10", "new", "Disk Free Space - C:、Physical Memory")
    _h(c, "2026-10-20 08:10:00", "d1", "appdb01", "10.99.1.10", "changed", "Physical Memory", "Down", "Down")                   # C: 消失（沒截斷）
    _h(c, "2026-10-20 08:20:00", "d1", "appdb01", "10.99.1.10", "changed", "Disk Free Space - C:、Physical Memory", "Down", "Down")  # C: 又出現
    _h(c, "2026-10-20 08:30:00", "d1", "appdb01", "10.99.1.10", "changed", "Physical Memory 等 9 項", "Down", "Down")              # 被截斷：不能說 C: 恢復
    d = _mine(c)
    cnt = {(r["category"], r["partition"]): r["count"] for r in d["counts"]}
    assert cnt[("disk", "C:")] == 2 and cnt[("memory", "")] == 1
    c_events = sorted([e for e in d["events"] if e["partition"] == "C:"], key=lambda e: e["first_at"])
    assert c_events[0]["partial_recover"] is True and c_events[0]["recovered_at"] == "2026-10-20 08:10:00"
    assert c_events[1]["recovered_at"] is None                                  # 被截斷那筆不推論恢復


def test_沒讀到說明的起點_補上說明後起點算最早那筆(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    _h(c, "2026-10-20 08:00:00", "d1", "appdb01", "10.99.1.10", "new", "")
    _h(c, "2026-10-20 08:05:00", "d1", "appdb01", "10.99.1.10", "changed", "Disk Free Space - C:", "Down", "Down")
    d = _mine(c)
    assert [(e["partition"], e["first_at"]) for e in d["events"]] == [("C:", "2026-10-20 08:00:00")]


# ------------------------------------------------------------------ 關聯：歧義、換 IP、重用、退役
def test_共用IP與IP主機名衝突_列待核不計次(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    _h(c, "2026-10-20 08:00:00", "dx", "vip-x", "10.99.1.50", "new", "Disk Free Space - C:")     # IP 登記在兩台
    _h(c, "2026-10-21 08:00:00", "dy", "APPWEB02", "10.99.1.10", "new", "Disk Free Space - C:")   # IP 是 DB01、名稱是 WEB02
    d1, d2 = _mine(c, "SHARED-A"), _mine(c, "APPDB01")
    assert d1["events"] == [] and any("多台" in p["reason"] for p in d1["pending"])
    assert d2["events"] == [] and any("不同主機" in p["reason"] for p in d2["pending"])
    assert _mine(c, "APPWEB02")["events"] == [] and _mine(c, "APPWEB02")["pending"]


def test_僅主機名或僅IP符合都列待核_不計次(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    _h(c, "2026-10-20 08:00:00", "dz", "appdb01", "10.99.8.8", "new", "Disk Free Space - C:")      # 名字對、IP 不對
    _h(c, "2026-10-21 08:00:00", "dw", "WUG-中文別名", "10.99.1.10", "new", "Disk Free Space - D:")  # IP 對、名字不在登記
    d = _mine(c)
    assert d["events"] == [] and d["counts"] == []
    assert sorted(p["device"] for p in d["pending"]) == ["WUG-中文別名", "appdb01"]
    reasons = " ".join(p["reason"] for p in d["pending"])
    assert "換 IP" in reasons and "無法印證" in reasons


def test_唯一IP被重用_新機不得繼承舊機_退役機歷史(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    _hw(c, "O-1", "OLDA", "10.99.1.20", status="報廢")          # 甲：已報廢，IP .20
    _hw(c, "N-1", "NEWB", "10.99.1.20")                         # 乙：現役，重用 IP .20
    _h(c, "2026-10-20 08:00:00", "da", "olda", "10.99.1.20", "new", "Disk Free Space - C:")   # WhatsUp 只有舊名
    d = _mine(c, "NEWB")
    assert d["events"] == [] and d["counts"] == []                                   # 乙不得計次
    assert d["pending"] and "已退役" in d["pending"][0]["reason"]
    assert [x for x in d["coverage"] if x["source"] == "whatsup"][0]["state"] == "no_machine_data"
    # 舊機自己也不併入（退役不做 IP 關聯）
    key = [k for k in __import__("oncall")._groups(c)[0] if k.startswith("ret:")][0]
    assert ho.build(c, key, "30d", NOW)["events"] == []


def test_沒有解除的舊段不當持續中_只有最後一段且現況表仍亮著才是持續中(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    _h(c, "2026-10-10 08:00:00", "d1", "appdb01", "10.99.1.10", "new", "Disk Free Space - C:")     # 舊段：沒 cleared 就又 new
    _h(c, "2026-10-20 08:00:00", "d1", "appdb01", "10.99.1.10", "new", "Disk Free Space - C:")     # 最後一段
    d = _mine(c, period="90d")
    st = {e["first_at"]: e["status"] for e in d["events"]}
    assert st == {"2026-10-10 08:00:00": "unknown", "2026-10-20 08:00:00": "unknown"}              # 現況表沒它：兩段都不能說持續中
    assert all(e["recovered_label"] and "未知" in e["recovered_label"] for e in d["events"])
    assert d["ongoing_ids"] == [] and d["totals"]["unknown_end"] == 2
    c.execute("INSERT INTO whatsup_alerts (device_id, name, ip, state, problem, since, group_name, first_seen, last_seen) "
              "VALUES ('d1','appdb01','10.99.1.10','Down','Disk Free Space - C:','2026-10-20 07:59:00','g','2026-10-20 08:00:00','2026-11-01 11:55:00')")
    c.commit()
    d2 = _mine(c, period="90d")
    st2 = {e["first_at"]: e["status"] for e in d2["events"]}
    assert st2 == {"2026-10-10 08:00:00": "unknown", "2026-10-20 08:00:00": "ongoing"}
    assert d2["ongoing_ids"] == [e["id"] for e in d2["events"] if e["status"] == "ongoing"]


def test_抓不到分割區的多個磁碟項各算各的_不併成一個(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    _h(c, "2026-10-20 08:00:00", "d1", "appdb01", "10.99.1.10", "new", "Disk Queue Length、Disk Read Latency、Disk Free Space - C:")
    d = _mine(c)
    unk = [e for e in d["events"] if e["partition"] == ho.NO_DATA]
    assert len(unk) == 2 and len({e["id"] for e in unk}) == 2
    assert {r["partition"]: r["count"] for r in d["counts"]}[ho.NO_DATA] == 2 and d["totals"]["disk"] == 3


def test_待核清單套用查詢期間(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    _h(c, "2026-06-01 08:00:00", "dz", "appdb01", "10.99.8.8", "new", "Disk Free Space - C:")
    _h(c, "2026-06-01 09:00:00", "dz", "appdb01", "10.99.8.8", "cleared", "Disk Free Space - C:", None, "Down")
    _h(c, "2026-10-25 08:00:00", "dy", "appdb01", "10.99.8.9", "new", "Disk Free Space - C:")
    assert [p["first_at"] for p in _mine(c, period="30d")["pending"]] == ["2026-10-25 08:00:00"]
    assert len(_mine(c, period="400d")["pending"]) == 2


def test_換IP與重用IP_每段告警各歸各的主機(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    # 同一個 WhatsUp 裝置，早期 IP 屬 DB01，後來 IP 改成 WEB02 的（重用）
    _h(c, "2026-10-01 08:00:00", "d1", "appdb01", "10.99.1.10", "new", "Disk Free Space - C:")
    _h(c, "2026-10-01 09:00:00", "d1", "appdb01", "10.99.1.10", "cleared", "Disk Free Space - C:", None, "Down")
    _h(c, "2026-10-10 08:00:00", "d1", "appweb02", "10.99.1.11", "new", "Disk Free Space - C:")
    assert [e["first_at"] for e in _mine(c, "APPDB01", "90d")["events"]] == ["2026-10-01 08:00:00"]
    assert [e["first_at"] for e in _mine(c, "APPWEB02", "90d")["events"]] == ["2026-10-10 08:00:00"]


def test_已退役主機不併入WhatsUp歷史(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    _hw(c, "S-9", "OLDHOST", "10.99.1.99", status="報廢")
    _h(c, "2026-10-20 08:00:00", "d9", "oldhost", "10.99.1.99", "new", "Disk Free Space - C:")
    key = [k for k in __import__("oncall")._groups(c)[0] if k.startswith("ret:")][0]
    d = ho.build(c, key, "30d", NOW)
    assert d["events"] == [] and "退役" in [x for x in d["coverage"] if x["source"] == "whatsup"][0]["note"]


def test_來源沒資料標無資料_不是正常(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    c.execute("DROP TABLE whatsup_alert_history")
    c.commit()
    d = _mine(c)
    w = [x for x in d["coverage"] if x["source"] == "whatsup"][0]
    n = [x for x in d["coverage"] if x["source"] == "nmon"][0]
    assert d["events"] == [] and w["state"] == "no_data" and n["state"] == "no_data"
    assert "不代表" in w["note"] and "不代表" in n["note"]
    snap = [x for x in d["coverage"] if x["source"] == "host_metric_latest"][0]
    assert snap["kind"] == "snapshot" and "沒有歷史" in snap["note"]            # 只有快照：不回推歷史
    assert not [x for x in d["coverage"] if x["source"] == "oncall_monthly"][0]["rows"]


# ------------------------------------------------------------------ NMON：跨午夜合併、指標與時間、記憶體佐證
def _nmon(conn, host="APPDB01"):
    def rows(spec):
        return {t: {"fs:/var": v, "mem_used": m, "swap_used": s} for t, (v, m, s) in spec.items()}
    d30 = {"23:57:00": (70, 50, 5), "23:58:00": (85, 95, 20), "23:59:00": (88, 96, 30)}
    d31 = {"00:00:00": (91, 94, 25), "00:01:00": (82, 80, 5), "00:02:00": (70, 80, 5), "12:00:00": (84, 50, 5), "12:01:00": (86, 50, 5),
           "12:02:00": (70, 50, 5)}
    ns.store(conn, {"host": host, "interval_s": 60, "days": {"2026-10-30": {"rows": rows(d30), "top": {"23:59:00": [[1, 9.5, "oracle"]]}},
                                                              "2026-10-31": {"rows": rows(d31), "top": {}}}})


def test_NMON超標時段_跨午夜合併成一次_有峰值與時間_記憶體佐證(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    _nmon(c)
    d = _mine(c)
    disk = sorted([e for e in d["events"] if e["category"] == "disk"], key=lambda e: e["first_at"])
    assert [(e["partition"], e["first_at"], e["last_at"]) for e in disk] == [
        ("/var", "2026-10-30 23:58:00", "2026-10-31 00:01:00"), ("/var", "2026-10-31 12:00:00", "2026-10-31 12:01:00")]
    assert disk[0]["metric"]["value"] == 91 and disk[0]["metric"]["at"] == "2026-10-31 00:00:00"
    assert disk[0]["recovered_at"] == "2026-10-31 00:02:00" and disk[0]["match"] == "host"
    mem = [e for e in d["events"] if e["category"] == "memory"]
    assert len(mem) == 1                                                         # 23:58～00:00 一段（跨午夜合併）
    ev = {x["k"]: x["v"] for x in mem[0]["evidence"]}
    assert ev["換頁空間使用率峰值"] == "30%" and "oracle" in ev["尖峰時最吃資源的程式"] and ev["available／OOM"].startswith(ho.NO_DATA)
    cnt = {(r["category"], r["partition"], r["source"]): r["count"] for r in d["counts"]}
    assert cnt[("disk", "/var", "nmon")] == 2 and cnt[("memory", "", "nmon")] == 1


def test_跨來源並列不加總_WhatsUp與NMON各算各的(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    _nmon(c)
    _h(c, "2026-10-31 12:00:00", "d1", "appdb01", "10.99.1.10", "new", "Disk Free Space - /var")
    d = _mine(c)
    keys = {(r["category"], r["partition"], r["source"]): r["count"] for r in d["counts"]}
    assert keys[("disk", "/var", "whatsup")] == 1 and keys[("disk", "/var", "nmon")] == 2
    assert not any(r["source"] == "all" for r in d["counts"])                     # 沒有跨來源合計列


# ------------------------------------------------------------------ 事件應變、期間、未來 30 天
def test_事件應變來源_以資產關聯_結案不等於恢復(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    key = ho.machine_info(c, "APPDB01")["key"]
    ev = incident.create_event(c, "主機", "DB 無法連線", "2026-10-30 10:00", None, "tester", "incident", "formal")
    incident.set_scope(c, ev["id"], {"machines": [key]}, "tester")
    drill = incident.create_event(c, "主機", "演練", "2026-10-30 11:00", None, "tester", "incident", "drill")
    incident.set_scope(c, drill["id"], {"machines": [key]}, "tester")
    d = _mine(c)
    inc = {e["id"]: e for e in d["events"] if e["source"] == "incident"}
    assert f"INC-{ev['id']}" in inc and inc[f"INC-{ev['id']}"]["status"] == "ongoing"
    assert inc[f"INC-{drill['id']}"]["countable"] is False                        # 演練不混進故障次數
    assert [r for r in d["counts"] if r["category"] == "incident"][0]["count"] == 1
    incident.close_event(c, ev["id"], "已處理", "tester")
    e2 = {e["id"]: e for e in _mine(c)["events"]}[f"INC-{ev['id']}"]
    assert e2["status"] == "closed" and e2["recovered_at"] is None            # 結案時間不寫進恢復欄
    assert e2["closed_at"] and "非單機恢復" in e2["closed_label"]
    assert _mine(c, "APPWEB02")["events"] == []                                   # 別台沒被牽連


def test_期間_今天_本週_最近7天_未來30天_Asia_Taipei():
    assert ho.window("today", NOW)["start"] == "2026-11-01 00:00:00"
    assert ho.window("week", NOW)["start"] == "2026-10-26 00:00:00"               # 2026-11-01 是週日，週一是 10-26
    assert ho.window("7d", NOW)["start"] == "2026-10-25 12:00:00"
    n = ho.window("next30", NOW)
    assert n["end"] == "2026-12-01 12:00:00" and n["future"] and "UTC" in n["tz"] and "預期 Asia/Taipei" in n["tz"]
    with pytest.raises(ValueError):
        ho.window("forever", NOW)


def test_未來30天_計畫停機_憑證到期_EOS月精度_缺來源標明(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    key = ho.machine_info(c, "APPDB01")["key"]
    ev = incident.create_event(c, "主機", "DB 維護", "2026-11-10 22:00", None, "t", "planned", "formal")
    incident.set_scope(c, ev["id"], {"machines": [key]}, "t")
    d = _mine(c, period="next30")
    kinds = {u["kind"] for u in d["upcoming"]}
    assert "planned_incident" in kinds and d["events"] == []                      # 未來期間不含故障事件
    assert [x for x in d["upcoming_coverage"] if x["source"] == "maintenance_calendar"][0]["state"] == "n/a"
    md = ho.render_md(d)
    assert "未來 30 天" in md and "不適用／無此來源" in md and "## 事件明細" not in md


# ------------------------------------------------------------------ 處理紀錄（ACT-05）
def _wug_event(c):
    _h(c, "2026-10-20 08:00:00", "d1", "appdb01", "10.99.1.10", "new", "Disk Free Space - C:")
    return _mine(c)["events"][0]["id"]


def test_讀取不寫庫不建表(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    _wug_event(c)
    before = c.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
    d = _mine(c)
    assert d["events"][0]["handling_text"] == {"action": "未填", "root_cause": "未確認", "outcome": "未填"}
    assert c.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == before
    assert not c.execute("SELECT 1 FROM sqlite_master WHERE name LIKE 'host_ops%'").fetchall()
    src = (ROOT / "APP" / "asset-module" / "backend" / "host_ops.py").read_text(encoding="utf-8")
    for fn in ("def build", "def _whatsup", "def _nmon", "def _incidents", "def _upcoming", "def _notes_of", "def render_md"):
        assert "ensure_tables" not in src.split(fn, 1)[1].split("\ndef ", 1)[0], fn


def test_新增_更正_作廢_保留原文與改前改後_原始告警不動(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    eid = _wug_event(c)
    n1 = ho.add_note(c, "APPDB01", eid, "action", "清除 /tmp 舊檔", "alice", NOW)
    assert n1["status"] == "active" and n1["created_by"] == "alice" and n1["created_at"] == "2026-11-01 12:00:00"
    n2 = ho.correct_note(c, "APPDB01", n1["id"], "清除 C:\\Temp 舊檔", "寫錯槽", "bob", NOW)
    old = ho.get_note(c, n1["id"])
    assert old["status"] == "corrected" and old["text"] == "清除 /tmp 舊檔"          # 原文保留
    assert n2["supersedes_id"] == n1["id"] and n2["text"].startswith("清除 C:")
    aud = ho.note_audit(c, n2["id"])
    assert aud[0]["op"] == "correct" and "清除 /tmp 舊檔" in aud[0]["before_json"] and "C:" in aud[0]["after_json"] and aud[0]["by"] == "bob"
    ho.add_note(c, "APPDB01", eid, "root_cause", "C: 槽被備份暫存塞滿", "alice", NOW)
    n4 = ho.add_note(c, "APPDB01", eid, "outcome", "改排程後觀察 3 天", "alice", NOW)
    v = ho.void_note(c, "APPDB01", n4["id"], "還沒做完，先不寫", "alice", NOW)
    assert v["status"] == "voided" and v["void_reason"] and v["text"] == "改排程後觀察 3 天"
    e = _mine(c)["events"][0]
    assert e["handling_text"] == {"action": "清除 C:\\Temp 舊檔", "root_cause": "C: 槽被備份暫存塞滿", "outcome": "未填"}
    assert len(e["notes"]) == 4                                                    # 歷史版本都還在
    assert c.execute("SELECT count(*) FROM whatsup_alert_history").fetchone()[0] == 1   # 原始告警沒動
    with pytest.raises(ValueError):
        ho.correct_note(c, "APPDB01", n1["id"], "再改一次", None, "bob", NOW)         # 舊版不能再更正
    with pytest.raises(ValueError):
        ho.void_note(c, "APPDB01", n4["id"], "重複作廢", "bob", NOW)
    with pytest.raises(ValueError):
        ho.void_note(c, "APPDB01", n2["id"], "  ", "bob", NOW)                       # 作廢要原因


def test_驗證_空白_過長_機密_未知類型_別台的事件_別台的紀錄(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    eid = _wug_event(c)
    for bad, exc in (("", ValueError), ("x" * 2001, ValueError), ("password=abc123", ValueError),
                     ("見 https://prod.example.test/webhook/abc", ValueError), ("-----BEGIN " + "PRIVATE KEY-----\nabc", ValueError)):
        with pytest.raises(exc):
            ho.add_note(c, "APPDB01", eid, "action", bad, "alice", NOW)
    with pytest.raises(ValueError):
        ho.add_note(c, "APPDB01", eid, "random", "ok", "alice", NOW)
    with pytest.raises(LookupError):
        ho.add_note(c, "APPDB01", "WUG-9999-disk:C:", "action", "ok", "alice", NOW)    # 不存在的事件
    with pytest.raises(LookupError):
        ho.add_note(c, "APPWEB02", eid, "action", "ok", "alice", NOW)                   # 別台的事件
    n = ho.add_note(c, "APPDB01", eid, "action", "ok", "alice", NOW)
    with pytest.raises(LookupError):
        ho.void_note(c, "APPWEB02", n["id"], "亂改別台", "alice", NOW)                  # 網址的主機必須是紀錄的主機


def test_稽核同交易_稽核失敗整筆回滾(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    eid = _wug_event(c)

    def boom(conn, info):
        conn.execute("INSERT INTO activity_log (at, method, path, action) VALUES ('t','OPS','x','y')")
        raise RuntimeError("audit down")
    with pytest.raises(RuntimeError):
        ho.add_note(c, "APPDB01", eid, "action", "會被回滾", "alice", NOW, audit_hook=boom)
    assert c.execute("SELECT count(*) FROM host_ops_note").fetchone()[0] == 0
    assert c.execute("SELECT count(*) FROM host_ops_audit").fetchone()[0] == 0
    assert c.execute("SELECT count(*) FROM activity_log WHERE path='x'").fetchone()[0] == 0
    n = ho.add_note(c, "APPDB01", eid, "action", "正常", "alice", NOW)
    with pytest.raises(RuntimeError):
        ho.correct_note(c, "APPDB01", n["id"], "更正會回滾", None, "alice", NOW, audit_hook=boom)
    assert ho.get_note(c, n["id"])["status"] == "active"                                # 舊版沒被標成已更正
    assert c.execute("SELECT count(*) FROM host_ops_note").fetchone()[0] == 1


def test_事件超出期間後處理紀錄不消失_列孤兒(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    eid = _wug_event(c)
    ho.add_note(c, "APPDB01", eid, "action", "做了某事", "alice", NOW)
    d = ho.build(c, "APPDB01", "today", NOW)
    assert [e["id"] for e in d["events"]] == [eid]                                       # 還在持續中的事件，任何期間都看得到
    far = datetime(2026, 12, 25, 12, 0, 0)
    c.execute("INSERT INTO whatsup_alert_history (at, device_id, name, ip, event, problem) VALUES ('2026-10-20 09:00:00','d1','appdb01','10.99.1.10','cleared','Disk Free Space - C:')")
    c.commit()
    d2 = ho.build(c, "APPDB01", "7d", far)
    assert [n["event_id"] for n in d2["orphan_notes"]] == [eid]


# ------------------------------------------------------------------ MD
def test_MD_與畫面同批次_保留事件ID_排除機密(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    _nmon(c)
    eid = _wug_event(c)
    ho.add_note(c, "APPDB01", eid, "action", "清理暫存", "alice", NOW)
    c.execute("INSERT INTO host_ops_note (machine_key, asset_serial, event_id, kind, text, status, created_by, created_at) "
              "VALUES ('k','S-1',?, 'root_cause', '舊資料 password=hunter2 與 https://x.example.test/webhook/zzz', 'active','old','t')", (eid,))
    c.commit()
    d = _mine(c)
    text, d2 = ho.md(c, "APPDB01", "30d", NOW)
    assert d["batch"] == d2["batch"] and f"`{d['batch']}`" in text
    for e in d["events"]:
        assert e["id"] in text                                                          # 事件 ID 都在 MD
    assert "hunter2" not in text and "x.example.test" not in text and ho.MASK in text
    assert "10.99.1.10" in text and "/var" in text and "C:" in text                     # 內部版保留主機／IP／分割區
    assert "Asia/Taipei" in text and "已恢復不等於根因已解決" in text
    # 計次表每列的次數 = 它列出的事件 ID 數（加總＝下鑽）
    for r in d["counts"]:
        assert f"| {r['count']} |" in text and all(i in text for i in r["event_ids"])


def test_MD_跨午夜事件不拆成兩筆_批次隨資料變動(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    b0 = _mine(c)["batch"]
    _h(c, "2026-10-20 08:00:00", "d1", "appdb01", "10.99.1.10", "new", "Disk Free Space - C:")
    assert _mine(c)["batch"] != b0


# ------------------------------------------------------------------ API：權限、匯出、審計
@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.delenv(api.AUTH_DISABLED_ENV, raising=False)
    p = tmp_path / "t.db"
    db.init_db(p)
    c0 = db.get_connection(p)
    _seed(c0)
    c0.close()

    def _ov():
        cc = db.get_connection(p)
        try:
            yield cc
        finally:
            cc.close()

    api.app.dependency_overrides[api.get_db] = _ov
    c = db.get_connection(p)
    for n, role in (("admin", "admin"), ("alice", "user"), ("vera", "viewer")):
        db.create_user(c, n, auth.hash_password(_PW), role=role)
    c.close()

    class E:
        path = p

        def client(self, name):
            t = TestClient(api.app)
            assert t.post("/api/auth/login", json={"username": name, "password": _PW}).status_code == 200
            return t

        def conn(self):
            return db.get_connection(p)

    yield E()
    api.app.dependency_overrides.pop(api.get_db, None)


def test_API_讀取viewer可_寫入user以上_MD匯出viewer不可_稽核落到操作紀錄(env):
    c = env.conn()
    _h(c, "2026-10-20 08:00:00", "d1", "appdb01", "10.99.1.10", "new", "Disk Free Space - C:")
    c.close()
    v, a = env.client("vera"), env.client("alice")
    r = v.get("/api/v2/machine/ops", params={"key": "APPDB01", "period": "90d"})
    assert r.status_code == 200 and r.json()["events"], r.text
    eid = r.json()["events"][0]["id"]
    assert v.get("/api/v2/machine/ops", params={"key": "APPDB01", "period": "nope"}).status_code == 400
    assert v.get("/api/v2/machine/ops", params={"key": "NO-SUCH-HOST"}).status_code == 404
    body = {"key": "APPDB01", "event_id": eid, "kind": "action", "text": "清暫存"}
    assert v.post("/api/v2/machine/ops/notes", json=body).status_code == 403
    assert v.get("/api/v2/machine/ops.md", params={"key": "APPDB01", "period": "90d"}).status_code == 403
    w = a.post("/api/v2/machine/ops/notes", json=body)
    assert w.status_code == 200 and w.json()["created_by"].startswith("alice")
    nid = w.json()["id"]
    assert a.post(f"/api/v2/machine/ops/notes/{nid}/correct", json={"key": "APPDB01", "text": "清暫存與備份檔", "reason": "補充"}).status_code == 200
    assert a.post(f"/api/v2/machine/ops/notes/{nid}/void", json={"key": "APPDB01", "reason": "x"}).status_code == 400     # 舊版已被更正
    md = a.get("/api/v2/machine/ops.md", params={"key": "APPDB01", "period": "90d"})
    assert md.status_code == 200 and "attachment" in md.headers["content-disposition"] and eid in md.text and "清暫存與備份檔" in md.text
    assert "清暫存\n" not in md.text
    assert v.get(f"/api/v2/machine/ops/notes/{nid}/audit", params={"key": "APPDB01"}).status_code == 403     # 稽核含舊版原文：一般帳號以上
    au = a.get(f"/api/v2/machine/ops/notes/{nid}/audit", params={"key": "APPDB01"})
    assert au.status_code == 200 and au.json()["rows"][0]["op"] == "create"
    cc = env.conn()
    acts = [r["action"] for r in cc.execute("SELECT action FROM activity_log WHERE action LIKE 'host_ops_%'")]
    assert sorted(acts) == ["host_ops_correct", "host_ops_create"]
    assert "清暫存" not in "".join(r["path"] for r in cc.execute("SELECT path FROM activity_log"))   # 稽核只寫做了什麼，不寫內容


def test_verify_numbers_有單機維運記錄本不變式且在種子資料上通過(tmp_path):
    import verify_numbers as vn
    c = _conn(tmp_path)
    _seed(c)
    _h(c, "2026-10-20 08:00:00", "d1", "appdb01", "10.99.1.10", "new", "Disk Free Space - C:")
    _h(c, "2026-10-21 08:00:00", "dx", "vip-x", "10.99.1.50", "new", "Disk Free Space - C:")      # 共用 IP：不能算進任何一台
    before, checks = vn.FAILS, vn.CHECKS
    vn.check_host_ops(c)
    assert vn.FAILS == before and vn.CHECKS == checks + 5


# ------------------------------------------------------------------ 審查修正（2.041.41）
def test_NMON跨月持續超標只算一次(tmp_path):
    c = _conn(tmp_path)
    _seed(c)

    def rows(spec):
        return {t: {"fs:/data": v} for t, v in spec.items()}
    ns.store(c, {"host": "APPDB01", "interval_s": 60, "days": {
        "2026-09-30": {"rows": rows({"23:58:00": 85, "23:59:00": 86}), "top": {}},
        "2026-10-01": {"rows": rows({"00:00:00": 87, "00:01:00": 70}), "top": {}}}})
    d = _mine(c, period="90d")
    ev = [e for e in d["events"] if e["category"] == "disk"]
    assert len(ev) == 1 and ev[0]["first_at"] == "2026-09-30 23:58:00" and ev[0]["last_at"] == "2026-10-01 00:00:00"
    assert ev[0]["metric"]["value"] == 87 and d["counts"][0]["count"] == 1
    # 期間起點落在 10 月初：9 月就開始的那段仍要找得到起點（往前多看一個月）
    d3 = ho.build(c, "APPDB01", "7d", datetime(2026, 10, 3, 12, 0, 0))
    assert [e["first_at"] for e in d3["events"]] == ["2026-09-30 23:58:00"] and d3["counts"][0]["count"] == 1
    assert ho.build(c, "APPDB01", "7d", datetime(2026, 10, 20, 12, 0, 0))["events"] == []      # 已恢復且不在期間內


def test_未來30天嚴格限制_61天後的計畫事件不列(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    key = ho.machine_info(c, "APPDB01")["key"]
    for title, when in (("近", "2026-11-20 22:00"), ("邊界外", "2026-12-02 22:00"), ("遠", "2027-01-01 22:00")):
        e = incident.create_event(c, "主機", title, when, None, "t", "planned", "formal")
        incident.set_scope(c, e["id"], {"machines": [key]}, "t")
    d = _mine(c, period="next30")
    assert [u["title"] for u in d["upcoming"] if u["kind"] == "planned_incident"] == ["近"]
    assert [u["title"] for u in _mine(c, period="30d")["upcoming"] if u["kind"] == "planned_incident"] == ["近"]


_BAD = ["password=abc123", "密碼是 Abc12345", "密碼＝Abc12345", "pw=zzz999", "pass: Secret!9", "api key: 12345abcdef",
        "token abcdef123456", "secret abcdef123456789", "見 https://hooks.slack.com/services/T000/B000/XXXX", "https://example.test/x?token=abc",
        "https://example.test/x?a=1&api_key=zzz", "mysql://root:Passw0rd@10.99.0.1/db", "ftp://u:p@host/x",
        "sk-" + "a" * 24, "AKIA" + "A" * 16, "Authorization: Basic dXNlcjpwYXNz", "Authorization: Bearer abcdefghijk", "Bearer abcDEF123456",
        "-----begin " + "private key-----\nabc", "pass​word=abc12345", "pass​word: abc123",
        "https://prod.webhook.office.com/webhookb2/x", "https://x.example.test/workflows/abc/triggers"]


@pytest.mark.parametrize("bad", _BAD)
def test_機密過濾補洞_寫入內容_更正原因_作廢原因都擋(tmp_path, bad):
    c = _conn(tmp_path)
    _seed(c)
    eid = _wug_event(c)
    with pytest.raises(ValueError):
        ho.add_note(c, "APPDB01", eid, "action", bad, "a", NOW)
    n = ho.add_note(c, "APPDB01", eid, "action", "ok", "a", NOW)
    with pytest.raises(ValueError):
        ho.correct_note(c, "APPDB01", n["id"], "新內容", bad, "a", NOW)
    with pytest.raises(ValueError):
        ho.void_note(c, "APPDB01", n["id"], bad, "a", NOW)
    assert ho.redact(bad) != ho._strip_invisible(bad)                                  # MD 輸出同一套規則


def test_原因欄長度上限300_正常文字不誤擋(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    eid = _wug_event(c)
    n = ho.add_note(c, "APPDB01", eid, "action", "清除 /var/log 舊日誌，token 過期已重新申請（未貼內容）", "a", NOW)
    with pytest.raises(ValueError):
        ho.correct_note(c, "APPDB01", n["id"], "新內容", "x" * 301, "a", NOW)
    with pytest.raises(ValueError):
        ho.void_note(c, "APPDB01", n["id"], "y" * 301, "a", NOW)
    assert ho.correct_note(c, "APPDB01", n["id"], "新內容", "x" * 300, "a", NOW)["status"] == "active"


def test_批次號只看資料內容_同資料不同查詢時間相同_內容變了才變(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    eid = _wug_event(c)
    b1 = ho.build(c, "APPDB01", "30d", NOW)["batch"]
    assert ho.build(c, "APPDB01", "30d", datetime(2026, 11, 1, 15, 31, 7))["batch"] == b1
    assert ho.md(c, "APPDB01", "30d", datetime(2026, 11, 1, 18, 0, 0))[1]["batch"] == b1
    n = ho.add_note(c, "APPDB01", eid, "action", "第一版", "a", NOW)
    b2 = ho.build(c, "APPDB01", "30d", NOW)["batch"]
    assert b2 != b1
    ho.correct_note(c, "APPDB01", n["id"], "第二版", None, "a", NOW)
    b3 = ho.build(c, "APPDB01", "30d", NOW)["batch"]
    assert b3 not in (b1, b2)
    _h(c, "2026-10-20 10:00:00", "d1", "appdb01", "10.99.1.10", "cleared", "Disk Free Space - C:", None, "Down")   # 事件恢復
    assert ho.build(c, "APPDB01", "30d", NOW)["batch"] != b3


def test_沒有來源資料的單機_不顯示有資料_零事件寫未取得(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    _h(c, "2026-10-20 08:00:00", "dz", "appweb02", "10.99.1.11", "new", "Disk Free Space - C:")       # 只有別台有紀錄
    _nmon(c, host="OTHERBOX")                                                                              # NMON 也只有別台
    d = _mine(c, "APPDB01")
    cov = {x["source"]: x for x in d["coverage"]}
    assert cov["whatsup"]["state"] == "no_machine_data" and cov["nmon"]["state"] == "no_machine_data"
    assert cov["whatsup"]["covers_from"] is None and d["events"] == [] and d["evidence_any"] is False
    text = ho.render_md(d)
    assert "**此期間未取得**" in text and "此機未取得" in text
    ok = _mine(c, "APPWEB02")
    assert {x["source"]: x for x in ok["coverage"]}["whatsup"]["state"] == "has_data" and ok["evidence_any"] is True


def test_NMON有資料但不在期間內_不算有資料(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    _nmon(c)                                           # 2026-10-30～31
    d = ho.build(c, "APPDB01", "today", datetime(2026, 12, 20, 12, 0, 0))
    n = [x for x in d["coverage"] if x["source"] == "nmon"][0]
    assert n["state"] == "no_machine_data" and "都不在這個期間" in n["note"] and n["covers_from"] == "2026-10-30"


def test_MD_事件應變結案時間標明非單機恢復_且不含可請求外部資源的語法(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    key = ho.machine_info(c, "APPDB01")["key"]
    ev = incident.create_event(c, "主機", "![x](http://evil.example/p.png)<img src=http://evil.example/a>", "2026-10-30 10:00", None, "t", "incident", "formal")
    incident.set_scope(c, ev["id"], {"machines": [key]}, "t")
    incident.close_event(c, ev["id"], "完", "t")
    eid = _wug_event(c)
    ho.ensure_tables(c)
    c.execute("INSERT INTO host_ops_note (machine_key, asset_serial, event_id, kind, text, status, created_by, created_at) "
              "VALUES ('k','S-1',?, 'action', '[點我](http://evil.example/x) <script>alert(1)</script> http://evil.example/t.gif', 'active','o','t')", (eid,))
    c.commit()
    text = ho.md(c, "APPDB01", "30d", NOW)[0]
    assert "結案時間，非單機恢復時間" in text
    for bad in ("](", "<img", "<script", "http://evil", "https://evil"):
        assert bad not in text
    assert text.startswith("# 單機維運記錄：APPDB01")


def test_MD標題主機名也遮蔽與中和(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    _hw(c, "S-7", "<b>x</b>[a](http://e.example/z)", "10.99.1.77")
    key = ho.machine_info(c, "10.99.1.77")["key"]
    text = ho.md(c, key, "30d", NOW)[0]
    assert "<b>" not in text and "](" not in text and "http://e.example" not in text


def test_時間解析_Z與offset換算成伺服器本地_naive照舊():
    from datetime import timezone
    assert ho._p("2026-10-20 08:00:00") == datetime(2026, 10, 20, 8, 0, 0)
    assert ho._p("2026-10-20T08:00") == datetime(2026, 10, 20, 8, 0, 0)
    want = datetime(2026, 10, 20, 0, 0, 0, tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    assert ho._p("2026-10-20T00:00:00Z") == want and ho._p("2026-10-20T08:00:00+08:00") == want
    assert ho._p("垃圾") is None and ho._p(None) is None
    assert "伺服器" in ho.window("30d", NOW)["tz"]


def test_唯讀連線_mode_ro_下build與md不寫庫_含舊庫nmon_file缺raw_path(tmp_path):
    import hashlib
    import sqlite3
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    _seed(c)
    _nmon(c)
    key = ho.machine_info(c, "APPDB01")["key"]
    ev = incident.create_event(c, "主機", "維護", "2026-11-10 22:00", None, "t", "planned", "formal")
    incident.set_scope(c, ev["id"], {"machines": [key]}, "t")
    _h(c, "2026-10-31 12:00:00", "d1", "appdb01", "10.99.1.10", "new", "Disk Free Space - /var")
    c.execute("DROP TABLE IF EXISTS nmon_file")
    c.execute("CREATE TABLE nmon_file (id INTEGER PRIMARY KEY AUTOINCREMENT, host TEXT NOT NULL, filename TEXT, first_ts TEXT, last_ts TEXT, "
              "samples INTEGER NOT NULL, uploaded_by TEXT, uploaded_at TEXT NOT NULL)")      # 舊庫：沒有 raw_path
    c.commit()
    c.close()
    h0 = hashlib.sha1(p.read_bytes()).hexdigest()
    ro = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    for per in ("30d", "next30", "today"):
        d = ho.build(ro, "APPDB01", per, NOW)
        assert d["batch"] in ho.render_md(d)
    assert len(ho.build(ro, "APPDB01", "30d", NOW)["events"]) >= 2          # NMON 與 WhatsUp 都讀得到
    ro.close()
    assert hashlib.sha1(p.read_bytes()).hexdigest() == h0
    src = (ROOT / "APP" / "asset-module" / "backend" / "host_ops.py").read_text(encoding="utf-8")
    for fn in ("def _nmon(", "def _nload", "def _mem_evidence", "def _top_at", "def _data_end"):
        body = src.split(fn, 1)[1].split("\ndef ", 1)[0]
        assert "ns.hosts(" not in body and "ns.summary(" not in body and "ns.top_at(" not in body and "ns._load(" not in body and "ns.ensure(" not in body, fn


def test_原作者與更正者都留下_事件上看得到原作者(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    eid = _wug_event(c)
    n1 = ho.add_note(c, "APPDB01", eid, "action", "甲寫的", "alice", NOW)
    ho.correct_note(c, "APPDB01", n1["id"], "乙改的", None, "bob", NOW)
    notes = _mine(c)["events"][0]["notes"]
    assert [(n["created_by"], n["original_by"], n["status"]) for n in notes] == [("alice", "alice", "corrected"), ("bob", "alice", "active")]
    assert "alice" in ho.note_audit(c, notes[1]["id"])[0]["before_json"]


def test_verify_numbers_獨立重算_造假的計次會變紅(tmp_path, monkeypatch):
    import verify_numbers as vn
    c = _conn(tmp_path)
    _seed(c)
    db.set_setting(c, "verify_strict_host_ops", "1")                       # strict：不一致才計為失敗
    _h(c, "2026-10-20 08:00:00", "d1", "appdb01", "10.99.1.10", "new", "Disk Free Space - C:")
    _h(c, "2026-10-20 09:00:00", "d1", "appdb01", "10.99.1.10", "cleared", "Disk Free Space - C:", None, "Down")
    _h(c, "2026-10-25 08:00:00", "d1", "appdb01", "10.99.1.10", "new", "Disk Free Space - C:")
    before = vn.FAILS
    vn.check_host_ops(c)
    assert vn.FAILS == before
    real = ho.count_events

    def drop_one(events):                         # 計次少算一筆(明細還在)
        rows = real(events)
        if rows:
            rows[0]["count"] -= 1
            rows[0]["event_ids"] = rows[0]["event_ids"][:-1]
        return rows
    monkeypatch.setattr(ho, "count_events", drop_one)
    b = vn.FAILS
    vn.check_host_ops(c)
    assert vn.FAILS > b
    monkeypatch.setattr(ho, "count_events", real)

    def wrong_class(events):                       # 計次列的分類與事件對不上
        rows = real(events)
        for r in rows:
            r["partition"] = "Z:"
        return rows
    monkeypatch.setattr(ho, "count_events", wrong_class)
    b = vn.FAILS
    vn.check_host_ops(c)
    assert vn.FAILS > b


# ------------------------------------------------------------------ 第三輪審查修正（2.041.42）
def _alert_row(c, state, last_seen="2026-11-01 11:55:00"):
    c.execute("INSERT INTO whatsup_alerts (device_id, name, ip, state, problem, since, group_name, first_seen, last_seen) "
              "VALUES ('d1','appdb01','10.99.1.10',?,'Disk Free Space - C:','2026-10-20 07:59:00','g','2026-10-20 08:00:00',?)", (state, last_seen))
    c.commit()


def test_持續中要現況列的狀態仍是告警_正常或已解除的現況列不算(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    _h(c, "2026-10-20 08:00:00", "d1", "appdb01", "10.99.1.10", "new", "Disk Free Space - C:")
    _alert_row(c, "正常")                                       # 現況列還在，但狀態已不是告警
    d = _mine(c)
    assert d["events"][0]["status"] == "unknown" and d["ongoing_ids"] == []
    c.execute("UPDATE whatsup_alerts SET state='Up'")
    c.commit()
    assert _mine(c)["events"][0]["status"] == "unknown"
    for st in ("當機", "Down", "警告"):
        c.execute("UPDATE whatsup_alerts SET state=?", (st,))
        c.commit()
        assert _mine(c)["events"][0]["status"] == "ongoing", st


def test_該機只有一年前的WhatsUp資料_查詢期間內沒有_不得是有資料_摘要寫此期間未取得(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    _h(c, "2025-10-15 08:00:00", "d1", "appdb01", "10.99.1.10", "new", "Disk Free Space - C:")
    _h(c, "2025-10-15 09:00:00", "d1", "appdb01", "10.99.1.10", "cleared", "Disk Free Space - C:", None, "Down")
    _h(c, "2026-10-30 09:00:00", "dz", "appweb02", "10.99.1.11", "new", "Disk Free Space - C:")           # 來源本身仍在更新（別台）
    d = _mine(c)
    w = [x for x in d["coverage"] if x["source"] == "whatsup"][0]
    assert d["events"] == [] and w["state"] == "no_machine_data" and "不在期間內" in w["note"]
    assert d["evidence_any"] is False
    assert "**此期間未取得**" in ho.render_md(d)
    d2 = ho.build(c, "APPDB01", "400d", NOW)
    assert [x for x in d2["coverage"] if x["source"] == "whatsup"][0]["state"] == "has_data"


def test_事件應變只在期間外有紀錄_不算有資料(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    key = ho.machine_info(c, "APPDB01")["key"]
    ev = incident.create_event(c, "主機", "舊事件", "2026-01-10 10:00", None, "t", "incident", "formal")
    incident.set_scope(c, ev["id"], {"machines": [key]}, "t")
    incident.close_event(c, ev["id"], "完", "t")
    c.execute("UPDATE incident_event SET closed_at='2026-01-11 10:00:00'")
    c.commit()
    d = _mine(c)
    assert [x for x in d["coverage"] if x["source"] == "incident"][0]["state"] == "none_recorded"


def test_viewer看不到更正前_作廢原文與原因_user以上看得到(env):
    c = env.conn()
    _h(c, "2026-10-20 08:00:00", "d1", "appdb01", "10.99.1.10", "new", "Disk Free Space - C:")
    c.close()
    a, v = env.client("alice"), env.client("vera")
    eid = a.get("/api/v2/machine/ops", params={"key": "APPDB01", "period": "90d"}).json()["events"][0]["id"]
    n1 = a.post("/api/v2/machine/ops/notes", json={"key": "APPDB01", "event_id": eid, "kind": "action", "text": "舊版內容OLD"}).json()
    a.post(f"/api/v2/machine/ops/notes/{n1['id']}/correct", json={"key": "APPDB01", "text": "新版內容NEW", "reason": "更正原因RSN"})
    n3 = a.post("/api/v2/machine/ops/notes", json={"key": "APPDB01", "event_id": eid, "kind": "outcome", "text": "會被作廢的VOIDTXT"}).json()
    a.post(f"/api/v2/machine/ops/notes/{n3['id']}/void", json={"key": "APPDB01", "reason": "作廢原因VRSN"})
    full = a.get("/api/v2/machine/ops", params={"key": "APPDB01", "period": "90d"})
    assert "舊版內容OLD" in full.text and "VOIDTXT" in full.text and "VRSN" in full.text
    seen = v.get("/api/v2/machine/ops", params={"key": "APPDB01", "period": "90d"})
    assert seen.status_code == 200
    for hidden in ("舊版內容OLD", "VOIDTXT", "VRSN", "RSN", "supersedes", "void_reason", "original_by"):
        assert hidden not in seen.text, hidden
    ev = seen.json()["events"][0]
    assert [n["text"] for n in ev["notes"]] == ["新版內容NEW"] and ev["handling_text"]["action"] == "新版內容NEW"
    assert seen.json()["batch"] == full.json()["batch"]                     # 兩種角色同批次


def test_批次號涵蓋輸出內容_標題_證據_note全欄位都會改變批次_MD帶舊批次會警告(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    eid = _wug_event(c)
    b1 = ho.build(c, "APPDB01", "30d", NOW)["batch"]
    assert ho.build(c, "APPDB01", "30d", datetime(2026, 11, 1, 20, 0, 0))["batch"] == b1
    # 事件內容（證據文字）變了：同一個事件 id、狀態不變，批次號也要變
    c.execute("UPDATE whatsup_alert_history SET problem='Disk Free Space - C: 91%'")
    c.commit()
    b2 = ho.build(c, "APPDB01", "30d", NOW)["batch"]
    assert b2 != b1
    # 主機 IP（輸出內容）變了
    c.execute("UPDATE hardware SET ip='10.99.1.10' WHERE asset_serial='S-1'")
    n = ho.add_note(c, "APPDB01", eid, "action", "x", "a", NOW)
    b3 = ho.build(c, "APPDB01", "30d", NOW)["batch"]
    assert b3 != b2
    c.execute("UPDATE host_ops_note SET created_by='zed' WHERE id=?", (n["id"],))       # note 的欄位（操作者）變了
    c.commit()
    assert ho.build(c, "APPDB01", "30d", NOW)["batch"] != b3
    cur = ho.build(c, "APPDB01", "30d", NOW)["batch"]
    text, d = ho.md(c, "APPDB01", "30d", NOW, screen_batch="oldbatch0000")
    assert "資料已更新，與畫面批次 `oldbatch0000` 不同" in text and d["batch"] == cur
    assert "資料已更新" not in ho.md(c, "APPDB01", "30d", NOW, screen_batch=cur)[0]


def test_Markdown中和_偶數反斜線_協定相對網址_裸網址email包反引號(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    eid = _wug_event(c)
    ho.ensure_tables(c)
    for txt in (r"!\[x\]\(//example.invalid/pixel\)", r"\\[x](//example.invalid/p.png)", "![x](//example.invalid/p.png)",
                "see www.example.invalid/a and mail bob@example.invalid or http://example.invalid/t.gif"):
        c.execute("INSERT INTO host_ops_note (machine_key, asset_serial, event_id, kind, text, status, created_by, created_at) "
                  "VALUES ('k','S-1',?, 'action', ?, 'active','o','t')", (eid, txt))
        c.commit()
        out = ho._c(txt)
        assert "](" not in out and "//example" not in out and "<" not in out, (txt, out)
        assert "\\\\" not in out.replace("\\[", "").replace("\\]", "").replace("\\(", "").replace("\\)", "")
    assert "`bob@example.invalid`" in ho._c("mail bob@example.invalid") and "`www.example.invalid/a`" in ho._c("see www.example.invalid/a")
    text = ho.md(c, "APPDB01", "30d", NOW)[0]
    assert "](" not in text and "//example.invalid" not in text


_OK_TEXT = ["token：已過期", "pass: completed", "密碼是由使用者自行管理", "token 為無效狀態", "password reset done", "secret santa 活動",
            "已更新 token 設定，請 IT 重新發放", "API key 已輪替完成", "pass through 2 次", "改用 bypass 模式"]


@pytest.mark.parametrize("ok", _OK_TEXT)
def test_機密過濾不誤擋一般說明(tmp_path, ok):
    c = _conn(tmp_path)
    _seed(c)
    eid = _wug_event(c)
    assert ho.redact(ok) == ok
    n = ho.add_note(c, "APPDB01", eid, "action", ok, "a", NOW)
    assert n["text"] == ok
    ho.correct_note(c, "APPDB01", n["id"], ok + "。", ok, "a", NOW)          # 原因欄同樣不誤擋


@pytest.mark.parametrize("bad", ["ＰＡＳＳＷＯＲＤ＝abc12345", "token: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abcDEF", "secret " + "a1" * 12, "token=" + "0123456789abcdef" * 2,
                                 "密碼：Ab3dEf9h"])
def test_機密過濾NFKC_JWT_長hex_全形仍擋(tmp_path, bad):
    c = _conn(tmp_path)
    _seed(c)
    eid = _wug_event(c)
    with pytest.raises(ValueError):
        ho.add_note(c, "APPDB01", eid, "action", bad, "a", NOW)
    assert ho.MASK in ho.redact(bad)


def test_時區顯示伺服器實際時區_期間比較先正規化_含Z與offset(tmp_path):
    from datetime import timezone
    lab = ho.window("30d", NOW)["tz"]
    assert "UTC" in lab and "預期 Asia/Taipei" in lab
    base = datetime(2026, 10, 20, 0, 0, 0, tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    z = "2026-10-20T00:00:00Z"
    start, end = "2026-10-19 00:00:00", "2026-10-21 23:59:59"
    assert ho._overlap_t("2026-10-20T00:00:00Z", None, start, end) is True
    assert ho._overlap_t("2026-10-20T00:00:00Z", "2026-10-20T01:00:00+00:00", "2026-10-22 00:00:00", "2026-10-23 00:00:00") is False
    assert ho._overlap_t("2026-10-20T08:00:00+08:00", "2026-10-20T09:00:00+08:00", base.strftime(FMT_T), base.strftime(FMT_T)) is True
    assert ho._nt("2026-10-20T08:00:00") == "2026-10-20 08:00:00" and ho._nt(z) == base.strftime("%Y-%m-%d %H:%M:%S")
    c = _conn(tmp_path)
    _seed(c)
    _h(c, "2026-10-20T08:00:00", "d1", "appdb01", "10.99.1.10", "new", "Disk Free Space - C:")          # 來源格式含 T
    _h(c, "2026-10-20T09:00:00", "d1", "appdb01", "10.99.1.10", "cleared", "Disk Free Space - C:", None, "Down")
    e = _mine(c)["events"][0]
    assert e["first_at"] == "2026-10-20 08:00:00" and e["recovered_at"] == "2026-10-20 09:00:00"


FMT_T = "%Y-%m-%d %H:%M:%S"


def test_資料庫被鎖_回503請重試_不是500(env, monkeypatch):
    import sqlite3
    a = env.client("alice")

    def boom(*args, **kw):
        raise sqlite3.OperationalError("database is locked")
    real_build = ho.build
    monkeypatch.setattr(ho, "build", boom)
    r = a.get("/api/v2/machine/ops", params={"key": "APPDB01"})
    assert r.status_code == 503 and "重試" in r.json()["detail"] and r.headers.get("retry-after")
    monkeypatch.setattr(ho, "build", real_build)
    monkeypatch.setattr(ho, "add_note", boom)
    r = a.post("/api/v2/machine/ops/notes", json={"key": "APPDB01", "event_id": "x", "kind": "action", "text": "t"})
    assert r.status_code == 503


def test_MD端點帶畫面批次_不同時檔頭警告(env):
    c = env.conn()
    _h(c, "2026-10-20 08:00:00", "d1", "appdb01", "10.99.1.10", "new", "Disk Free Space - C:")
    c.close()
    a = env.client("alice")
    d = a.get("/api/v2/machine/ops", params={"key": "APPDB01", "period": "90d"}).json()
    same = a.get("/api/v2/machine/ops.md", params={"key": "APPDB01", "period": "90d", "batch": d["batch"]})
    assert "資料已更新" not in same.text and d["batch"] in same.text
    diff = a.get("/api/v2/machine/ops.md", params={"key": "APPDB01", "period": "90d", "batch": "stale0000000"})
    assert "資料已更新，與畫面批次 `stale0000000` 不同" in diff.text


def test_待核顯示可能是改名或換IP的提示_缺口與MD寫明已知取捨(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    _h(c, "2026-10-25 08:00:00", "dz", "appdb01", "10.99.8.8", "new", "Disk Free Space - C:")
    d = _mine(c)
    assert "改名或換 IP" in d["pending"][0]["hint"]
    assert any("已知取捨" in x for x in d["limits"])
    text = ho.render_md(d)
    assert "## 已知限制" in text and "改名或換 IP" in text
    assert "改名或換 IP" in [x for x in d["coverage"] if x["source"] == "whatsup"][0]["note"]


def _verify_world(c):
    """各種關聯型態：IP＋名稱一致、只有名稱、只有 IP、共用 IP、退役、NMON-only、incident-only。"""
    _hw(c, "O-1", "OLDA", "10.99.1.60", status="報廢")
    _hw(c, "N-1", "NEWB", "10.99.1.60")
    _hw(c, "NM-1", "NMONLY", "10.99.1.70")
    _hw(c, "IN-1", "INCONLY", "10.99.1.80")
    _h(c, "2026-10-20 08:00:00", "d1", "appdb01", "10.99.1.10", "new", "Disk Free Space - C:")                  # IP＋名稱
    _h(c, "2026-10-20 09:00:00", "d1", "appdb01", "10.99.1.10", "cleared", "Disk Free Space - C:", None, "Down")
    _h(c, "2026-10-25 08:00:00", "d1", "appdb01", "10.99.1.10", "new", "Disk Free Space - C:、Physical Memory")
    _h(c, "2026-10-21 08:00:00", "d2", "appweb02", "10.99.8.9", "new", "Disk Free Space - C:")                  # 只有名稱
    _h(c, "2026-10-22 08:00:00", "d3", "別名-web", "10.99.1.11", "new", "Disk Free Space - C:")               # 只有 IP
    _h(c, "2026-10-23 08:00:00", "d4", "vip", "10.99.1.50", "new", "Disk Free Space - C:")                    # 共用 IP
    _h(c, "2026-10-24 08:00:00", "d5", "olda", "10.99.1.60", "new", "Disk Free Space - C:")                   # 重用 IP／退役名
    _nmon(c, host="NMONLY")
    key = ho.machine_info(c, "INCONLY")["key"]
    ev = incident.create_event(c, "主機", "僅事件", "2026-10-28 10:00", None, "t", "incident", "formal")
    incident.set_scope(c, ev["id"], {"machines": [key]}, "t")


def test_verify_numbers_獨立最小實作重算WhatsUp段數_涵蓋各型態_造假會變紅(tmp_path, monkeypatch):
    import verify_numbers as vn
    c = _conn(tmp_path)
    _seed(c)
    db.set_setting(c, "verify_strict_host_ops", "1")                       # strict：不一致才計為失敗
    _verify_world(c)
    before, checks = vn.FAILS, vn.CHECKS
    vn.check_host_ops(c)
    assert vn.FAILS == before and vn.CHECKS > checks + 4
    real_match = ho._match

    def always_ip_name(idx, ip, name):               # 把『重用 IP／名稱不符』誤判成可計次（IP 重用 bug）
        res, cands, why = real_match(idx, ip, name)
        ipk = idx.ip.get((ip or "").strip(), set())
        return ("ip_name", ipk, "") if len(ipk) == 1 else (res, cands, why)
    monkeypatch.setattr(ho, "_match", always_ip_name)
    b = vn.FAILS
    vn.check_host_ops(c)
    assert vn.FAILS > b
    monkeypatch.setattr(ho, "_match", real_match)

    real_ep = ho._episodes

    def merge_all(rows):                              # 把每個裝置的所有段併成一段（少算段數）
        eps = real_ep(rows)
        if len(eps) > 1:
            first = eps[0]
            first["rows"] = [r for e in eps for r in e["rows"]]
            return [first]
        return eps
    monkeypatch.setattr(ho, "_episodes", merge_all)
    b = vn.FAILS
    vn.check_host_ops(c)
    assert vn.FAILS > b


# ------------------------------------------------------------------ 第四輪（2.041.43）
def _fs(c, days, host="APPDB01"):
    ns.store(c, {"host": host, "interval_s": 60, "days": {d: {"rows": {t: {"fs:/gap": v} for t, v in spec.items()}, "top": {}} for d, spec in days.items()}})


def test_NMON資料中斷處不得憑空判定恢復_10_01仍超標_10_02沒資料_10_03才有(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    _fs(c, {"2026-10-01": {"23:58:00": 85, "23:59:00": 86}, "2026-10-03": {"00:00:00": 70, "00:01:00": 70}})
    e = [x for x in _mine(c, period="90d")["events"] if x["category"] == "disk"]
    assert len(e) == 1 and e[0]["recovered_at"] is None and e[0]["status"] == "unknown"
    assert e[0]["recovered_label"] and "未知" in e[0]["recovered_label"]
    assert "無法證明" in " ".join(x["v"] for x in e[0]["evidence"])


def test_NMON同一天內資料中斷超過兩個取樣間隔_也不證明恢復_間隔合理才證明(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    _fs(c, {"2026-10-05": {"10:00:00": 85, "10:01:00": 86, "10:10:00": 70}})                 # 之後 9 分鐘才有樣本
    e = [x for x in _mine(c, period="90d")["events"] if x["category"] == "disk"][0]
    assert e["recovered_at"] is None and e["status"] == "unknown"
    c2 = _conn(tmp_path / "b") if (tmp_path / "b").mkdir() is None else None
    _seed(c2)
    _fs(c2, {"2026-10-05": {"10:00:00": 85, "10:01:00": 86, "10:03:00": 70}})                # 剛好 2 個取樣間隔、且低於門檻 → 恢復
    e2 = [x for x in _mine(c2, period="90d")["events"] if x["category"] == "disk"][0]
    assert e2["recovered_at"] == "2026-10-05 10:03:00" and e2["status"] == "recovered"


def test_NMON緊接樣本仍高於門檻就不是恢復_正常恢復仍正確(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    _fs(c, {"2026-10-05": {"10:00:00": 85, "10:01:00": 70}, "2026-10-06": {"09:00:00": 90}})
    ev = sorted([x for x in _mine(c, period="90d")["events"] if x["category"] == "disk"], key=lambda x: x["first_at"])
    assert ev[0]["recovered_at"] == "2026-10-05 10:01:00" and ev[0]["status"] == "recovered"
    assert ev[1]["recovered_at"] is None and ev[1]["first_at"] == "2026-10-06 09:00:00"       # 資料結束在超標中


def test_新增處理紀錄的冪等鍵_同一請求重送只新增一次_內容不同拒絕_舊庫補欄位(tmp_path):
    c = _conn(tmp_path)
    _seed(c)
    eid = _wug_event(c)
    # 舊庫：表已存在但沒有 client_key 欄位
    c.execute("CREATE TABLE host_ops_note (id INTEGER PRIMARY KEY AUTOINCREMENT, machine_key TEXT NOT NULL, asset_serial TEXT NOT NULL, event_id TEXT NOT NULL, "
              "kind TEXT NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', supersedes_id INTEGER, event_ctx TEXT, created_by TEXT, "
              "created_at TEXT NOT NULL, voided_by TEXT, voided_at TEXT, void_reason TEXT)")
    c.commit()
    a = ho.add_note(c, "APPDB01", eid, "action", "清暫存", "alice", NOW, client_key="req-12345678")
    b = ho.add_note(c, "APPDB01", eid, "action", "清暫存", "alice", NOW, client_key="req-12345678")
    assert a["id"] == b["id"]
    assert c.execute("SELECT count(*) FROM host_ops_note").fetchone()[0] == 1 and c.execute("SELECT count(*) FROM host_ops_audit").fetchone()[0] == 1
    with pytest.raises(ValueError):
        ho.add_note(c, "APPDB01", eid, "action", "別的內容", "alice", NOW, client_key="req-12345678")
    with pytest.raises(ValueError):
        ho.add_note(c, "APPDB01", eid, "action", "x", "alice", NOW, client_key="短")
    n = ho.add_note(c, "APPDB01", eid, "action", "沒帶鍵", "alice", NOW)
    n2 = ho.add_note(c, "APPDB01", eid, "action", "沒帶鍵", "alice", NOW)
    assert n["id"] != n2["id"]                                                    # 沒帶鍵維持原行為


def test_API冪等_同一請求編號連送兩次只留一筆也只記一次操作紀錄(env):
    c = env.conn()
    _h(c, "2026-10-20 08:00:00", "d1", "appdb01", "10.99.1.10", "new", "Disk Free Space - C:")
    c.close()
    a = env.client("alice")
    eid = a.get("/api/v2/machine/ops", params={"key": "APPDB01", "period": "90d"}).json()["events"][0]["id"]
    body = {"key": "APPDB01", "event_id": eid, "kind": "action", "text": "清暫存", "client_key": "web-abcdef123456"}
    r1, r2 = a.post("/api/v2/machine/ops/notes", json=body), a.post("/api/v2/machine/ops/notes", json=body)
    assert r1.status_code == r2.status_code == 200 and r1.json()["id"] == r2.json()["id"]
    cc = env.conn()
    assert cc.execute("SELECT count(*) FROM host_ops_note").fetchone()[0] == 1
    assert cc.execute("SELECT count(*) FROM activity_log WHERE action='host_ops_create'").fetchone()[0] == 1


_OK2 = ["authorization: completed successfully", "Authorization header 已更新", "authorization 流程已完成", "authorization: pending approval"]


@pytest.mark.parametrize("ok", _OK2)
def test_authorization一般句子不整段遮蔽(ok):
    assert ho.redact(ok) == ok
    assert ho._c(ok).replace("\\", "") .startswith(ok.split()[0])


def test_authorization帶方案與值仍遮蔽():
    for bad in ("Authorization: Basic dXNlcjpwYXNz", "Authorization: Bearer abcdefghijk", "authorization=Token abc", "Authorization: abcDEF123456"):
        assert ho.MASK in ho.redact(bad), bad


def test_verify_numbers_沒有whatsup表仍檢查NMON與事件應變(tmp_path, monkeypatch):
    import verify_numbers as vn
    c = _conn(tmp_path)
    _seed(c)
    db.set_setting(c, "verify_strict_host_ops", "1")                       # strict：不一致才計為失敗
    c.execute("DROP TABLE whatsup_alert_history")
    c.commit()
    _fs(c, {"2026-10-05": {"10:00:00": 85, "10:01:00": 70}})
    key = ho.machine_info(c, "INCONLY")["key"] if False else None
    b, ch = vn.FAILS, vn.CHECKS
    vn.check_host_ops(c)
    assert vn.FAILS == b and vn.CHECKS >= ch + 5                                   # 不再只回『不適用』一項
    real = ho.count_events

    def drop_one(events):
        rows = real(events)
        if rows:
            rows[0]["count"] += 1
        return rows
    monkeypatch.setattr(ho, "count_events", drop_one)
    b = vn.FAILS
    vn.check_host_ops(c)
    assert vn.FAILS > b


def test_verify_numbers_預設觀察模式_不一致只出警示不使verify失敗_strict才失敗(tmp_path, monkeypatch):
    import verify_numbers as vn
    c = _conn(tmp_path)
    _seed(c)
    _h(c, "2026-10-20 08:00:00", "d1", "appdb01", "10.99.1.10", "new", "Disk Free Space - C:")
    real = ho.count_events

    def drop_one(events):
        rows = real(events)
        if rows:
            rows[0]["count"] += 1
        return rows
    monkeypatch.setattr(ho, "count_events", drop_one)
    assert db.get_setting(c, "verify_strict_host_ops", None) is None               # 預設不存在
    n0, f0, r0 = vn.CHECKS, vn.FAILS, len(vn.RESULTS)
    vn.check_host_ops(c)
    assert vn.FAILS == f0 and vn.CHECKS > n0                                       # 計入檢查數、不計失敗
    new = vn.RESULTS[r0:]
    warned = [x for x in new if x["ok"] is None]
    assert warned and all("觀察模式" in x["detail"] and "人工核對" in x["detail"] for x in warned)
    assert not [x for x in new if x["ok"] is False]
    db.set_setting(c, "verify_strict_host_ops", "1")
    f1 = vn.FAILS
    vn.check_host_ops(c)
    assert vn.FAILS > f1                                                           # strict 才升級成會中止的 ❌
    db.set_setting(c, "verify_strict_host_ops", "0")
    f2 = vn.FAILS
    vn.check_host_ops(c)
    assert vn.FAILS == f2
