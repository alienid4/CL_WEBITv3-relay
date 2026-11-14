"""Q9／Q15（2.066.x）：首頁告警預設 Teams 通知範圍＋同分鐘同來源收合；夜間速查、主機履歷改讀首頁那一份（4.1 告警事件）。
全部假資料（192.0.2.x／10.99.x、TEST-*），不連任何外部。"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import host_ops  # noqa: E402
import night_alerts as na  # noqa: E402
import oncall  # noqa: E402
import quiet_window as qw  # noqa: E402
import v2_today  # noqa: E402
import verify_numbers as vn  # noqa: E402
import wug_alert as wa  # noqa: E402

NOW = datetime(2026, 11, 4, 10, 30, 0)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    c = db.get_connection()
    qw.set_clock(lambda: NOW)
    monkeypatch.setattr(host_ops, "_now", lambda: NOW)
    yield c
    qw.set_clock(None)
    c.close()


def _enable(c, policies="", received="2026-11-04 10:29:00"):
    wa.ensure(c)
    wa.save_settings(c, {**wa.DEFAULTS, "enabled": True, "notify_policies": policies})
    if received:
        wa._set(c, wa.K_LAST_RECV, received)
    c.commit()


def _alert(c, did, monitor, started, policy=None, ended=None, tier="red", ip="192.0.2.10", mk=None, name="TEST-AP01"):
    wa.ensure(c)
    cur = c.execute(
        "INSERT INTO alert_event (wug_device_id, monitor, device_name, ip, machine_key, match, tier, started_at, ended_at, last_seen, duration_min, created_at, policy) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (did, monitor, name, ip, mk, "matched" if mk else "no_asset", tier, started, ended, started, 10, started, policy))
    c.commit()
    return cur.lastrowid


# ===== Q9：範圍＝Teams 通知範圍（同一支 policy_ok），看全部 =====
def test_預設只列通知範圍_看全部列全部_today同範圍(conn):
    _enable(conn, policies="主機組")
    a = _alert(conn, "1", "Ping", "2026-11-04 09:00:00", policy="主機組通知")
    _alert(conn, "2", "AppA.exe", "2026-11-04 09:05:00", policy="")          # WhatsUp 明講沒掛政策 → Teams 不推
    c = _alert(conn, "3", "Disk C:", "2026-11-04 09:10:00", policy=None)            # 不知道有沒有掛 → 照推（範圍內）
    d = v2_today.today(conn, NOW)["alerts"]
    assert {r["id"] for r in d["rows"]} == {a, c} and d["n"] == 2
    assert d["scope"]["mode"] == "policy" and d["scope"]["hidden"] == 1 and d["scope"]["n_all"] == 3 and d["scope"]["keywords"] == ["主機組"]
    assert d["today"]["n"] == 2                                                       # 今天發生過也是同一個範圍
    # 跟 Teams 同一支判斷：範圍內的列＝wug_alert.policy_ok 為真的
    assert all(wa.policy_ok(r["policy"], ["主機組"]) for r in d["rows"])
    full = v2_today.today(conn, NOW, scope="all")["alerts"]
    assert full["n"] == 3 and full["scope"]["mode"] == "all" and full["scope"]["hidden"] == 0 and full["today"]["n"] == 3


def test_沒設通知政策_預設就是全部(conn):
    _enable(conn, policies="")
    _alert(conn, "1", "Ping", "2026-11-04 09:00:00", policy="")
    d = v2_today.today(conn, NOW)["alerts"]
    assert d["n"] == 1 and d["scope"]["mode"] == "all" and "Teams 也是全部推" in d["scope"]["label"]


# ===== Q9：同分鐘＋同來源收合，件數＝下鑽 =====
def test_同分鐘同來源收成一列_件數加總等於列數_下鑽是那幾筆(conn):
    _enable(conn)
    ids = [_alert(conn, "7", m, "2026-11-04 08:00:00") for m in ("AppA.exe", "AppB.exe", "AppC.exe", "AppD.exe")]
    other_dev = _alert(conn, "8", "AppA.exe", "2026-11-04 08:00:30", ip="192.0.2.11", name="TEST-AP02")   # 同分鐘、不同設備 → 另一列
    later = _alert(conn, "7", "Ping", "2026-11-04 08:01:00")                        # 同設備、不同分鐘 → 另一列
    d = v2_today.today(conn, NOW)["alerts"]
    g = d["groups"]
    assert len(g) == 3 and sum(x["n"] for x in g) == d["n"] == len(d["rows"]) == 6
    big = next(x for x in g if x["n"] == 4)
    assert sorted(big["ids"]) == sorted(ids) and "等 4 項" in big["monitor"]
    assert {tuple(x["ids"]) for x in g if x["n"] == 1} == {(other_dev,), (later,)}
    assert [x["__no"] for x in g] == [1, 2, 3]


def test_收合時組裡有紅就整組標紅(conn):
    _enable(conn)
    _alert(conn, "7", "A.exe", "2026-11-04 08:00:00", tier="yellow")
    _alert(conn, "7", "B.exe", "2026-11-04 08:00:10", tier="red")
    (g,) = v2_today.today(conn, NOW)["alerts"]["groups"]
    assert g["tier"] == "red" and g["n"] == 2


def test_verify_首頁收合與範圍不變式都綠(conn):
    _enable(conn, policies="主機組")
    for m in ("A.exe", "B.exe"):
        _alert(conn, "7", m, "2026-11-04 08:00:00", policy="主機組")
    _alert(conn, "9", "C.exe", "2026-11-04 08:00:00", policy="其他")
    before = vn.FAILS
    vn.RESULTS.clear()
    vn.check_v2_today(conn)
    assert vn.FAILS == before, [r for r in vn.RESULTS if r["ok"] is False]
    names = [r["name"] for r in vn.RESULTS if r["page"] == "1.1 今日"]
    assert any("同分鐘同來源收合" in n for n in names) and any("通知範圍：範圍內 + 不在範圍" in n for n in names)


# ===== Q15：夜間速查＝首頁同一份 =====
def test_夜間速查_中繼有啟用時跟首頁同一批_不是未取得(conn):
    _enable(conn, policies="主機組")
    a = _alert(conn, "1", "Ping", "2026-11-04 09:00:00", policy="主機組", tier="red")
    b = _alert(conn, "2", "Disk C:", "2026-11-04 09:05:00", policy=None, tier="yellow")
    _alert(conn, "3", "X.exe", "2026-11-04 09:05:00", policy="")
    d = na.build(conn, NOW)
    home = v2_today.today(conn, NOW)["alerts"]
    assert d["status"] == "ok" and d["status_label"] != "未取得" and d["source"]["kind"] == "relay"
    assert {r["id"] for r in d["rows"]} == {r["id"] for r in home["rows"]} == {a, b}
    assert d["counts"] == {"total": 2, "critical": 1, "warning": 1, "unmapped": 2}
    assert [r["__no"] for r in d["rows"]] == [1, 2]
    assert {r["id"] for r in na.build(conn, NOW, scope="all")["rows"]} == {r["id"] for r in v2_today.today(conn, NOW, scope="all")["alerts"]["rows"]}
    vn.RESULTS.clear()
    vn.check_night_alerts(conn, strict=True)
    rs = [r for r in vn.RESULTS if r["page"] == "夜間速查告警"]
    assert rs and all(r["ok"] is True for r in rs)


def test_夜間速查_中繼啟用但還沒收到資料_講原因(conn):
    _enable(conn, received=None)
    d = na.build(conn, NOW)
    assert d["status"] == "never" and "中繼" in d["message"] and "不代表目前沒有告警" in d["message"] and not d["rows"]


def test_夜間速查_中繼資料中斷_標資料中斷不說沒有告警(conn):
    _enable(conn, received="2026-11-04 08:00:00")
    d = na.build(conn, NOW)
    assert d["status"] == "stale" and "目前沒有告警（" not in d["message"]


def test_夜間速查_兩個來源都沒啟用_訊息講兩個(conn):
    d = na.build(conn, NOW)
    assert d["status"] == "not_enabled" and "4.1 告警事件" in d["message"] and "WhatsUp" in d["message"]


# ===== Q15：主機履歷事件＝首頁那張表 =====
def _host(conn):
    db.insert_hardware(conn, asset_serial="SN-Q15", hostname="TEST-AP01", ip="10.99.1.10", physical_location="01_甲機房", rack_no="M10/10U")
    conn.commit()
    groups, of = oncall._groups(conn)
    return of["SN-Q15"]


def test_主機履歷_中繼有啟用時事件讀告警事件表(conn):
    key = _host(conn)
    _enable(conn)
    on = _alert(conn, "1", "Ping", "2026-11-04 09:00:00", mk=key, ip="10.99.1.10")
    off = _alert(conn, "1", "Disk C:", "2026-11-03 09:00:00", ended="2026-11-03 10:00:00", mk=None, ip="10.99.1.10")   # 對不到資產，用 IP 補
    _alert(conn, "5", "Ping", "2026-11-04 09:00:00", ip="10.99.9.9")                                                  # 別台
    d = host_ops.build(conn, key, "7d", NOW)
    wug = [e for e in d["events"] if e["source"] == "whatsup"]
    assert {e["id"] for e in wug} == {f"WUGA-{on}", f"WUGA-{off}"}
    cur = next(e for e in wug if e["id"] == f"WUGA-{on}")
    assert cur["status"] == "ongoing" and cur["id"] in d["ongoing_ids"]
    cov = next(c for c in d["coverage"] if c["source"] == "whatsup")
    assert cov["state"] == "has_data" and "4.1 告警事件" in cov["label"]
    disk = next(c for c in d["counts"] if c["category"] == "disk")
    assert disk["count"] == 1 and disk["event_ids"] == [f"WUGA-{off}"]                # 計次＝下鑽


def test_主機履歷_中繼資料中斷時未恢復的不說持續中(conn):
    key = _host(conn)
    _enable(conn, received="2026-11-04 08:00:00")
    eid = _alert(conn, "1", "Ping", "2026-11-04 07:00:00", mk=key, ip="10.99.1.10")
    d = host_ops.build(conn, key, "7d", NOW)
    e = next(e for e in d["events"] if e["id"] == f"WUGA-{eid}")
    assert e["status"] == "unknown" and not d["ongoing_ids"]


def test_主機履歷_中繼沒啟用_照舊讀輪詢歷史(conn):
    key = _host(conn)
    d = host_ops.build(conn, key, "7d", NOW)
    cov = next(c for c in d["coverage"] if c["source"] == "whatsup")
    assert "4.1 告警事件" not in cov["label"]


# ===== 審查修正（P2／P3）=====
def test_P2_兩台共用IP_別台已對到的告警不算進這台(conn):
    db.insert_hardware(conn, asset_serial="SN-A", hostname="TEST-NODE-A", ip="10.99.1.50", physical_location="01_甲機房", rack_no="M10/10U")
    db.insert_hardware(conn, asset_serial="SN-B", hostname="TEST-NODE-B", ip="10.99.1.50", physical_location="01_甲機房", rack_no="M10/11U")
    conn.commit()
    _g, of = oncall._groups(conn)
    ka, kb = of["SN-A"], of["SN-B"]
    assert ka != kb
    _enable(conn)
    mine = _alert(conn, "1", "Ping", "2026-11-04 09:00:00", mk=ka, ip="10.99.1.50", name="TEST-NODE-A")
    theirs = _alert(conn, "2", "Disk C:", "2026-11-04 09:00:00", mk=kb, ip="10.99.1.50", name="TEST-NODE-B")
    orphan = _alert(conn, "3", "Disk D:", "2026-11-04 09:00:00", mk=None, ip="10.99.1.50", name="TEST-VIP")
    got = wa.for_machine(conn, ka, ["10.99.1.50"])
    assert {r["id"] for r in got["current"]} == {mine, orphan} and got["total"] == 2
    d = host_ops.build(conn, ka, "7d", NOW)
    ids = {e["id"] for e in d["events"] if e["source"] == "whatsup"}
    assert ids == {f"WUGA-{mine}", f"WUGA-{orphan}"} and f"WUGA-{theirs}" not in ids
    assert sum(c["count"] for c in d["counts"] if c["source"] == "whatsup") == len(ids)      # 計次＝下鑽
    # 只給 IP（key 對不到資產）時照舊：這個 IP 的告警全部列出，不猜
    assert {r["id"] for r in wa.for_machine(conn, None, ["10.99.1.50"])["current"]} == {mine, theirs, orphan}


def test_P3_來源全空的告警不合併(conn):
    _enable(conn)
    a = _alert(conn, "", "A.exe", "2026-11-04 08:00:00", ip="", name="")
    b = _alert(conn, "", "B.exe", "2026-11-04 08:00:20", ip="", name="")
    g = v2_today.today(conn, NOW)["alerts"]["groups"]
    assert len(g) == 2 and sorted(x["ids"][0] for x in g) == sorted([a, b]) and all(x["n"] == 1 for x in g)
