"""夜間速查 N1「跟平常比」（night_baseline.py）。

守的口徑（定案 05 v2 第 6 點）：同時段 ±2 小時、7 天至少 4 天有樣本、多數決過半、平手不標紅、維護時段不算、
查詢失敗≠沒有、磁碟差值＝現在−基準中位數、只有自動啟動且平常在跑現在停才紅、沒有基準寫「沒有基準」不猜。
測試資料一律虛構：10.99.x、example 主機名。
"""
import gzip
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import alert_attention as aa  # noqa: E402
import db  # noqa: E402
import host_ops  # noqa: E402
import night_baseline as nb  # noqa: E402
import night_desk  # noqa: E402
import relay_import  # noqa: E402
import wug_alert  # noqa: E402

NOW = datetime(2026, 11, 15, 2, 0, 0)        # 台北凌晨兩點
IP = "10.99.1.10"


def _conn(tmp_path, os_="Microsoft Windows Server 2019"):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.insert_hardware(c, asset_serial="S-1", hostname="APPDB01", ip=IP, os=os_, asset_status="使用中")
    relay_import.ensure(c)
    c.commit()
    return c


def _mk(c):
    return host_ops.machine_info(c, IP)["key"]


def _disk(c, disk, when_local: datetime, val: float, ip=IP):
    """在 relay_disk_util 加一點（序列是 UTC）。同一天同一顆碟合併。"""
    u = when_local - timedelta(hours=8)
    day, ts = u.date().isoformat(), u.strftime("%H:%M:%S")
    r = c.execute("SELECT data FROM relay_disk_util WHERE host=? AND day=? AND disk=?", (ip, day, disk)).fetchone()
    d = json.loads(gzip.decompress(r[0]).decode()) if r else {"ts": [], "avg": [], "max": [], "min": []}
    d["ts"].append(ts)
    for k in ("avg", "max", "min"):
        d[k].append(val)
    c.execute("INSERT OR REPLACE INTO relay_disk_util (host, day, disk, interval_s, samples, data, batch_id, updated_at) VALUES (?,?,?,?,?,?,?,?)",
              (ip, day, disk, 900, len(d["ts"]), gzip.compress(json.dumps(d).encode()), "b1", "x"))
    c.commit()


def _week(c, disk, vals, start_day=1):
    for i, v in enumerate(vals):
        _disk(c, disk, NOW - timedelta(days=start_day + i) + timedelta(minutes=10), v)


def _snap(c, when, result):
    night_desk.ensure(c)
    c.execute("INSERT INTO night_check_snapshot (machine_key, ip, at, by, alive_state, conclusion, result_json) VALUES (?,?,?,?,?,?,?)",
              (_mk(c), IP, when.strftime(nb.FMT), "tester", "alive", "", json.dumps(result)))
    c.commit()


def test_磁碟_七天中位數_現在漲二十以上紅_差值是現在減中位數(tmp_path):
    c = _conn(tmp_path)
    _week(c, "C:\\ Label:Sys", [40, 41, 39, 40, 42, 40, 38])
    _disk(c, "C:\\ Label:Sys", NOW - timedelta(minutes=50), 65)
    d = nb.diff(c, _mk(c), NOW)
    assert d["status"] == "ok"
    red = [x for x in d["changes"] if x["level"] == "red"]
    assert len(red) == 1 and "C:" in red[0]["what"] and "+25" in red[0]["what"] and "平常 40%" in red[0]["what"]


def test_磁碟_漲十到二十黃_沒漲算一樣(tmp_path):
    c = _conn(tmp_path)
    _week(c, "D:", [50] * 7)
    _week(c, "E:", [30] * 7)
    _disk(c, "D:", NOW - timedelta(minutes=30), 62)
    _disk(c, "E:", NOW - timedelta(minutes=30), 31)
    d = nb.diff(c, _mk(c), NOW)
    assert [x["level"] for x in d["changes"]] == ["amber"]
    assert d["same_count"] == 1 and "E: 31%" in d["same_brief"]


def test_即時檢測的磁碟優先於WhatsUp舊值(tmp_path):
    c = _conn(tmp_path)
    _week(c, "C:\\ Label:Sys", [40] * 7)
    _disk(c, "C:\\ Label:Sys", NOW - timedelta(hours=1), 41)
    d = nb.diff(c, _mk(c), NOW, live={"reachable": True, "checked_at": "2026-11-15 01:59:00", "disks": [{"mount": "C:", "use_pct": 70}]})
    assert d["changes"][0]["level"] == "red" and d["changes"][0]["source"] == "即時檢測"


def test_不到四天有樣本_基準不足_不標任何變化(tmp_path):
    c = _conn(tmp_path)
    _week(c, "C:", [40, 40, 40])
    _disk(c, "C:", NOW - timedelta(minutes=20), 90)
    d = nb.diff(c, _mk(c), NOW)
    assert d["status"] == "insufficient" and d["changes"] == []
    assert "基準不足" in d["reason"]


def test_同時段以外的樣本不算(tmp_path):
    c = _conn(tmp_path)
    for i in range(1, 8):
        _disk(c, "C:", NOW - timedelta(days=i) + timedelta(hours=5), 40)      # 早上七點，不在 ±2 小時內
    _disk(c, "C:", NOW - timedelta(minutes=20), 90)
    assert nb.diff(c, _mk(c), NOW)["status"] != "ok"


def test_維護時段的樣本不算_告警維護期間與例行時段都排除(tmp_path):
    c = _conn(tmp_path)
    _week(c, "C:", [40] * 7)
    _disk(c, "C:", NOW - timedelta(minutes=20), 90)
    wug_alert.ensure(c)
    for i in (1, 2, 3, 4):                                                  # 4 天維護 → 只剩 3 天
        s = NOW - timedelta(days=i)
        c.execute("INSERT INTO alert_event (wug_device_id, monitor, ip, machine_key, tier, started_at, ended_at, last_seen, created_at) "
                  "VALUES (?,?,?,?, 'maint', ?,?,?,?)", (f"d{i}", "(設備)", IP, _mk(c), (s - timedelta(hours=3)).strftime(nb.FMT),
                                                          (s + timedelta(hours=3)).strftime(nb.FMT), (s + timedelta(hours=3)).strftime(nb.FMT), "x"))
    c.commit()
    d = nb.diff(c, _mk(c), NOW)
    assert d["status"] == "insufficient" and d["changes"] == []
    c.execute("DELETE FROM alert_event")
    aa.ensure(c)
    c.execute("INSERT INTO routine_window (host_key, start_hhmm, end_hhmm, created_at) VALUES (?, '00:00', '04:00', 'x')", (_mk(c),))
    c.commit()
    assert nb.diff(c, _mk(c), NOW)["changes"] == []


def test_查詢失敗不等於沒有_分開列出(tmp_path):
    c = _conn(tmp_path)
    _week(c, "C:", [40] * 7)
    c.execute("INSERT INTO relay_disk_util (host, day, disk, data) VALUES (?, ?, 'Z:', ?)", (IP, (NOW - timedelta(days=2)).date().isoformat(), b"broken"))
    c.commit()
    d = nb.diff(c, _mk(c), NOW)
    assert d["query_failed"] and "Z:" in d["query_failed"][0]


def test_完全沒有資料_寫沒有基準不猜(tmp_path):
    c = _conn(tmp_path)
    d = nb.diff(c, _mk(c), NOW)
    assert d["status"] == "none" and "沒有基準" in d["reason"] and d["changes"] == []
    assert any("服務" in n for n in d["needs"])
    assert nb.diff(c, "沒有這台", NOW)["status"] == "none"


def test_服務_自動啟動平常在跑現在停_Windows紅_Linux最多黃(tmp_path):
    c = _conn(tmp_path)
    for i in range(1, 6):
        _snap(c, NOW - timedelta(days=i), {"reachable": True, "platform": "windows", "failed_units": [], "ports": []})
    live = {"reachable": True, "platform": "windows", "checked_at": "2026-11-15 01:58:00", "failed_units": ["Spooler"], "ports": []}
    d = nb.diff(c, _mk(c), NOW, live)
    red = [x for x in d["changes"] if x["kind"] == "service"]
    assert red and red[0]["level"] == "red" and "Spooler" in red[0]["what"] and "5/5" in red[0]["what"]
    d2 = nb.diff(c, _mk(c), NOW, {**live, "platform": "linux"})
    assert [x["level"] for x in d2["changes"] if x["kind"] == "service"] == ["amber"]


def test_服務_平手不標紅_本來停現在跑只黃(tmp_path):
    c = _conn(tmp_path)
    for i in range(1, 5):
        _snap(c, NOW - timedelta(days=i), {"reachable": True, "platform": "windows", "ports": [],
                                           "failed_units": ["Spooler"] if i <= 2 else ["OldSvc"]})
    live = {"reachable": True, "platform": "windows", "checked_at": "x", "failed_units": ["Spooler"], "ports": []}
    d = nb.diff(c, _mk(c), NOW, live)
    svc = [x for x in d["changes"] if x["kind"] == "service"]
    assert not [x for x in svc if x["level"] == "red"]                      # Spooler 2/4 天在跑＝平手
    comp = next(x for x in d["components"] if x["name"] == "service")
    assert any("Spooler" in t["what"] for t in comp["tie"])
    assert any("OldSvc" in x["what"] and x["level"] == "amber" for x in svc) is False   # OldSvc 2/4 天停＝也平手


def test_監聽埠_每週基準快照多數決_現在不見了黃(tmp_path):
    c = _conn(tmp_path)
    c.execute("CREATE TABLE IF NOT EXISTS health_snapshot (id INTEGER PRIMARY KEY, ip TEXT, taken_at TEXT, source TEXT, overall TEXT, reachable INTEGER, "
              "ports_json TEXT, disks_json TEXT, mem_pct REAL, load_per REAL, swap_pct REAL)")
    for i in range(1, 5):
        c.execute("INSERT INTO health_snapshot (ip, taken_at, source, reachable, ports_json) VALUES (?,?,?,1,?)",
                  (IP, (NOW - timedelta(days=i)).strftime(nb.FMT), "manual", json.dumps([22, 1433])))
    c.commit()
    d = nb.diff(c, _mk(c), NOW, {"reachable": True, "checked_at": "x", "ports": [{"port": 22}], "failed_units": []})
    assert any("1433" in x["what"] and x["level"] == "amber" for x in d["changes"])
    assert not any(x["level"] == "red" for x in d["changes"] if x["kind"] == "port")


def test_服務盤點_一直在聽的埠最近不見了_標推論黃(tmp_path):
    c = _conn(tmp_path)
    c.execute("INSERT INTO host_service (ip, proto, port, process, source, first_seen, last_seen, gone_at) VALUES (?,?,?,?,?,?,?,?)",
              (IP, "tcp", 3306, "mysqld", "ssh_ss", "2026-11-01 01:00:00", "2026-11-14 20:00:00", "2026-11-14 23:00:00"))
    c.execute("INSERT INTO host_service (ip, proto, port, process, source, first_seen, last_seen) VALUES (?,?,?,?,?,?,?)",
              (IP, "tcp", 22, "sshd", "ssh_ss", "2026-11-01 01:00:00", "2026-11-15 01:00:00"))
    c.commit()
    d = nb.diff(c, _mk(c), NOW)
    ch = [x for x in d["changes"] if "3306" in x["what"]]
    assert ch and ch[0]["level"] == "amber" and "推論" in ch[0]["source"]
