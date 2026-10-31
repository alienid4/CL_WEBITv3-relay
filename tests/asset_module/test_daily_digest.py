"""每日變化報告＋早上摘要信（v1.500.0）。

釘住：
  * 差異的每一類（含網段層級的收集中斷）
  * 對稱：N 日的「新增」＝反向比對的「消失」；改名（同序號）配成一對，不算新增＋消失
  * 快照保留天數
  * 信的內容（不連真的 SMTP：注入 sender），失敗要記、只有寄成功才算寄過
"""
import sys
from datetime import date, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import daily_digest as dd  # noqa: E402
import db  # noqa: E402

CFG = dict(dd.DEFAULTS)


def _row(key, **kw):
    r = {f: None for f in dd.SNAP_FIELDS}
    r.update({"mkey": key, "retired": 0, "hostname": key, "ips": "10.99.1.1", "serials": f"S-{key}",
              "room": "機房A", "rack": "M01", "u": "10-11", "os": "RHEL 9", "api_ids": "N-001",
              "exist_level": "onboarded", "exist_score": 100, "collect_status": "fresh",
              "last_collect": "2026-10-22 01:00:00", "sw_count": 100, "svc_count": 20, "acct_count": 30,
              "segment": "10.99.1.0/24", "uptime_days": 10})
    r.update(kw)
    return r


def _snap(*rows):
    return {r["mkey"]: r for r in rows}


def test_每一類都分得出來():
    prev = _snap(_row("a"), _row("b"), _row("c"), _row("d"), _row("e"), _row("f"), _row("g"),
                 _row("h"), _row("gone"), _row("ret"), _row("up", uptime_days=300))
    cur = _snap(
        _row("a", room="機房B"),                              # 位置
        _row("b", ips="10.99.1.2"),                          # IP
        _row("c", api_ids="N-002"),                          # 系統歸屬
        _row("d", os="RHEL 8"),                              # OS
        _row("e", exist_level="collect_stale", exist_score=70),   # 存在可信度下降
        _row("f", sw_count=10),                              # 軟體數大幅變化 100→10
        _row("g", sw_count=105),                             # 小變化不算
        _row("h", hostname="H"),                             # 只有大小寫：不算主機名改變
        _row("new"),                                         # 新增
        _row("ret", retired=1),                              # 退役 → 消失（退役）
        _row("up", uptime_days=400),                         # 開機過久（本次新增）
    )
    out = dd.diff_rows(prev, cur, CFG, "2026-10-23")
    it = out["items"]
    keys = {c: [x["key"] for x in it[c]] for c in it}
    assert keys["added"] == ["new"]
    assert sorted(keys["missing"]) == ["gone", "ret"]
    assert {x["key"]: x["detail"] for x in it["missing"]} == {"gone": "不見", "ret": "退役"}
    assert keys["location"] == ["a"]
    assert keys["ip"] == ["b"]
    assert keys["system"] == ["c"]
    assert keys["os"] == ["d"]
    assert keys["exist_down"] == ["e"]
    assert keys["count_change"] == ["f"]
    assert keys["hostname"] == []
    assert keys["uptime_risk"] == ["up"]
    assert dd.counts_of(it)["added"] == 1


def test_改主機名配成一對_不算新增加消失():
    prev = _snap(_row("h:old", hostname="old", serials="S-1"))
    cur = _snap(_row("h:new", hostname="new", serials="S-1"))
    it = dd.diff_rows(prev, cur, CFG, "2026-10-23")["items"]
    assert it["added"] == [] and it["missing"] == []
    assert [(x["before"], x["after"]) for x in it["hostname"]] == [("old", "new")]


def test_對稱_新增等於反向消失():
    a = _snap(_row("x"), _row("y"), _row("h:o", serials="S-9"), _row("r"))
    b = _snap(_row("y"), _row("z"), _row("h:n", serials="S-9"), _row("r", retired=1))
    fwd = dd.diff_rows(a, b, CFG, "2026-10-23")["items"]
    rev = dd.diff_rows(b, a, CFG, "2026-10-22")["items"]
    assert sorted(x["key"] for x in fwd["added"]) == sorted(x["key"] for x in rev["missing"])
    assert sorted(x["key"] for x in fwd["missing"]) == sorted(x["key"] for x in rev["added"])


def test_收集中斷_依網段分組_整段中斷():
    cfg = {**CFG, "gap_days": 7}
    cur = _snap(
        _row("a1", segment="10.99.1.0/24", last_collect="2026-10-01 00:00:00"),
        _row("a2", segment="10.99.1.0/24", last_collect="2026-10-02 00:00:00"),
        _row("b1", segment="10.99.2.0/24", last_collect="2026-10-01 00:00:00"),
        _row("b2", segment="10.99.2.0/24", last_collect="2026-10-22 00:00:00"),
        _row("c1", segment="10.99.3.0/24", collect_status=None, last_collect=None),   # 沒納管過：不算
        _row("d1", segment="10.99.4.0/24", collect_status="stale", last_collect=None),  # 時間不明：算
    )
    out = dd.diff_rows(None, cur, cfg, "2026-10-23")
    gap = sorted(x["key"] for x in out["items"]["collect_gap"])
    assert gap == ["a1", "a2", "b1", "d1"]
    segs = {s["segment"]: s for s in out["segments"]}
    assert segs["10.99.1.0/24"]["whole"] is True and segs["10.99.1.0/24"]["gap"] == 2
    assert segs["10.99.2.0/24"]["whole"] is False and segs["10.99.2.0/24"]["collected"] == 2
    assert "10.99.3.0/24" not in segs
    # 加總＝下鑽：各網段 gap 加總＝收集中斷筆數
    assert sum(s["gap"] for s in out["segments"]) == len(out["items"]["collect_gap"])
    # 沒有前一份時，差異類一律空
    assert out["items"]["added"] == [] and out["items"]["missing"] == []


def test_開機天數解析():
    assert dd.parse_uptime_days("up 1 year, 2 weeks, 3 days, 4 hours") == 365 + 14 + 3
    assert dd.parse_uptime_days("10:30AM up 120 days, 3:45, 2 users") == 120
    assert dd.parse_uptime_days("8640000.5 100.0") == 100
    assert dd.parse_uptime_days("up 5 hours, 3 minutes") == 0
    assert dd.parse_uptime_days("") is None
    assert dd.parse_uptime_days("看不懂") is None


def _db(tmp_path):
    p = tmp_path / "d.db"
    db.init_db(p)
    conn = db.get_connection(p)
    db.insert_hardware(conn, asset_serial="A-1", hostname="h1", ip="10.99.1.1", os="RHEL 9",
                       asset_status="使用中", api_id="N-001", physical_location="機房A",
                       rack_no="M01/10-11U", collect_ok=1, collect_checked_at="2026-10-22 01:00:00")
    db.insert_hardware(conn, asset_serial="A-2", hostname="h2", ip="10.99.1.2", os="Windows",
                       asset_status="使用中")
    conn.execute("INSERT INTO scan_history (hostname, ip, scan_ok, scan_time) "
                 "VALUES ('h2','10.99.1.2',1,'2026-10-22 01:00:00')")
    conn.commit()
    return conn


def test_快照寫入_同一天覆蓋_保留天數(tmp_path):
    conn = _db(tmp_path)
    r = dd.write_snapshot(conn, datetime(2026, 10, 22, 6, 0), "schedule")
    assert r["machines"] >= 2
    dd.write_snapshot(conn, datetime(2026, 10, 22, 7, 0), "manual")        # 同一天重寫
    assert dd.dates(conn) == ["2026-10-22"]
    n = conn.execute("SELECT COUNT(*) FROM daily_snapshot WHERE snap_date='2026-10-22'").fetchone()[0]
    assert n == r["machines"]
    row = conn.execute("SELECT room, rack, u, api_ids FROM daily_snapshot WHERE serials LIKE '%A-1%'").fetchone()
    assert tuple(row) == ("機房A", "M1", "10-11", "N-001")
    # 保留 3 天：寫到 10-26 時 10-22 要被清掉（cut = 10-23）
    for d in (23, 24, 25, 26):
        dd.write_snapshot(conn, datetime(2026, 10, d, 6, 0), "schedule", {**dd.DEFAULTS, "retain_days": 3})
    assert dd.dates(conn) == ["2026-10-26", "2026-10-25", "2026-10-24", "2026-10-23"]
    assert dd.purge(conn, date(2026, 10, 26), 1) == 1         # 少於 2 天當 2 天：留 10-24 起
    assert dd.dates(conn)[-1] == "2026-10-24"


def test_diff_for_與信的內容(tmp_path):
    conn = _db(tmp_path)
    dd.write_snapshot(conn, datetime(2026, 10, 22, 6, 0))
    db.insert_hardware(conn, asset_serial="A-3", hostname="h3", ip="10.99.1.3", os="RHEL 9",
                       asset_status="使用中", collect_ok=1, collect_checked_at="2026-10-23 01:00:00")
    dd.write_snapshot(conn, datetime(2026, 10, 23, 6, 0))
    out = dd.diff_for(conn)
    assert out["date"] == "2026-10-23" and out["prev_date"] == "2026-10-22"
    assert [x["hostname"] for x in out["items"]["added"]] == ["h3"]
    assert out["counts"] == dd.counts_of(out["items"])
    cfg = {**dd.DEFAULTS, "base_url": "http://webit.example"}
    subject, body = dd.compose(cfg, out, dd.diff_for(conn, "2026-10-22", cfg))
    assert "變化" in subject and "2026-10-23" in subject
    assert "新增：1（0）" in body
    assert "http://webit.example/changes?date=2026-10-23&tab=added" in body
    assert "收集中斷" in body


def _smtp(conn):
    import vuln_notify
    vuln_notify.save_settings(conn, {"smtp_host": "relay.example", "smtp_port": 25,
                                     "mail_from": "webit@example"})


def test_寄信_注入sender_失敗要記_成功才算寄過(tmp_path):
    conn = _db(tmp_path)
    now = datetime(2026, 10, 23, 8, 5)
    # 沒設定：不寄，記一筆 failed 並指名缺什麼
    r = dd.send(conn, "test", now, sender=lambda *a: (True, "x"))
    assert r["ok"] is False and "收件人" in r["reason"]
    _smtp(conn)
    dd.save_settings(conn, {"recipients": "a@example, b@example", "base_url": "http://w",
                            "mail_enabled": True, "mail_time": "08:00"})
    sent = []

    def fail(cfg, to, cc, subject, body):
        sent.append(to)
        return False, "421 service not available"

    assert dd.mail_due(conn, now, dd.load_settings(conn)) is True
    out = dd.tick(conn, now, sender=fail)
    assert out["mail"]["ok"] is False
    log = dd.mail_log(conn)
    assert log[0]["status"] == "failed" and "421" in log[0]["detail"] and log[0]["kind"] == "scheduled"
    assert sent == ["a@example, b@example"]
    # 失敗後 60 分內不重試、之後補寄
    assert dd.mail_due(conn, datetime(2026, 10, 23, 8, 30), dd.load_settings(conn)) is False
    assert dd.mail_due(conn, datetime(2026, 10, 23, 9, 10), dd.load_settings(conn)) is True
    ok = dd.tick(conn, datetime(2026, 10, 23, 9, 10), sender=lambda *a: (True, "250 OK"))
    assert ok["mail"]["ok"] is True
    assert dd.mail_due(conn, datetime(2026, 10, 23, 10, 30), dd.load_settings(conn)) is False
    # 測試信不佔排程那一封
    dd.send(conn, "test", datetime(2026, 10, 24, 7, 0), sender=lambda *a: (True, "250 OK"))
    assert dd.mail_due(conn, datetime(2026, 10, 24, 8, 1), dd.load_settings(conn)) is True


def test_寄信預設關著(tmp_path):
    conn = _db(tmp_path)
    assert dd.mail_due(conn, datetime(2026, 10, 23, 9, 0), dd.load_settings(conn)) is False


def test_快照到點才寫_手動的不算排程(tmp_path):
    conn = _db(tmp_path)
    cfg = dd.load_settings(conn)
    assert dd.snapshot_due(conn, datetime(2026, 10, 23, 5, 0), cfg) is False
    dd.write_snapshot(conn, datetime(2026, 10, 23, 4, 0), "manual")
    assert dd.snapshot_due(conn, datetime(2026, 10, 23, 5, 31), cfg) is True
    dd.tick(conn, datetime(2026, 10, 23, 5, 31))
    assert dd.snapshot_due(conn, datetime(2026, 10, 23, 5, 40), cfg) is False


def test_verify_numbers_每日變化不變式(tmp_path):
    import verify_numbers as vn

    conn = _db(tmp_path)
    dd.write_snapshot(conn, datetime(2026, 10, 22, 6, 0))
    db.insert_hardware(conn, asset_serial="A-3", hostname="h3", ip="10.99.1.3", os="RHEL 9",
                       asset_status="使用中", collect_ok=1, collect_checked_at="2026-10-23 01:00:00")
    conn.execute("UPDATE hardware SET asset_status = '報廢' WHERE asset_serial = 'A-2'")
    conn.execute("UPDATE hardware SET hostname = 'h1new' WHERE asset_serial = 'A-1'")
    conn.commit()
    dd.write_snapshot(conn, datetime(2026, 10, 23, 6, 0))
    out = dd.diff_for(conn)
    assert [x["detail"] for x in out["items"]["missing"]] == ["退役"]
    assert [x["after"] for x in out["items"]["hostname"]] == ["h1new"]
    before_fail, before_checks = vn.FAILS, vn.CHECKS
    vn.check_daily_digest(conn)
    assert vn.CHECKS - before_checks == 4
    assert vn.FAILS == before_fail
