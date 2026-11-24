"""硬碟通知影子模式（disk_notice_shadow.py，2.069.5）：只記錄「預計通知誰」，**絕不寄信、不送 Teams**。

釘住：跨 90% 判定（含邊界 89.95／90.0、前次已 ≥90 不再記、沒前次資料）、≥95 即時、預估 3 天內滿、去重（同日同機同碟一筆、即時蓋過一般）、
負責人對應（CIA 負責人／保管者／對不到→SP／人名查不到信箱往下一個來源）、關閉不寫表、不寄信（寄信／Teams／SMTP 函式換成會 raise 的替身仍跑完）、
權限（非管理員 403、設定與試算＝admin）、缺表不 500、tick 一天一次。範例 IP 只用 10.99.x。
"""
import gzip
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import disk_notice_shadow as dns  # noqa: E402
import ad_import  # noqa: E402
import relay_import  # noqa: E402

TPE = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 21, 8, 0, tzinfo=TPE)
D0 = "2026-11-21"


def _blob(peak):
    return gzip.compress(json.dumps({"ts": ["00:00:00", "12:00:00"], "avg": [peak - 2, peak - 1], "max": [peak - 1, peak]}).encode())


def _day(n):
    return (date.fromisoformat(D0) - timedelta(days=n)).isoformat()


def put(c, ip, disk, peaks):
    """peaks：{幾天前: 當天最高值}；0＝資料日。"""
    for n, p in peaks.items():
        c.execute("INSERT OR REPLACE INTO relay_disk_util (host, day, disk, interval_s, samples, data, batch_id) VALUES (?,?,?,?,?,?,?)",
                  (ip, _day(n), disk, 3600, 2, _blob(p), "b"))
    c.commit()


def person(c, eid, name, mail):
    c.execute("INSERT INTO ad_person (employee_id, display_name, mail, enabled, dept_name) VALUES (?,?,?,1,'架構部')", (eid, name, mail))


def hw(c, serial, host, ip, owner=None, custodian=None):
    db.insert_hardware(c, asset_serial=serial, hostname=host, ip=ip)
    c.execute("UPDATE hardware SET owner = ?, custodian = ? WHERE asset_serial = ?", (owner, custodian, serial))
    c.commit()


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    relay_import.ensure(c)
    c.execute(ad_import.TABLE_SQL)
    yield c
    c.close()


def on(c):
    dns.save_enabled(c, True)
    c.commit()


def rows(c):
    return [dict(r) for r in c.execute("SELECT * FROM disk_notice_shadow ORDER BY ip, disk")]


# ── 判定 ──
def test_跨90判定_邊界與前次(conn):
    on(conn)
    put(conn, "10.99.0.1", "C:", {0: 90.0, 1: 85.0})        # 剛跨過
    put(conn, "10.99.0.2", "C:", {0: 89.9, 1: 80.0})        # 差一點
    put(conn, "10.99.0.3", "C:", {0: 92.0, 1: 91.0})        # 前一天已經 ≥90：不是今天才跨
    put(conn, "10.99.0.4", "C:", {0: 91.0})                 # 沒有前次資料：照記（前次留空）
    put(conn, "10.99.0.5", "C:", {0: 89.96, 1: 50.0})       # 四捨五入到一位＝90.0（全站同一把尺）
    r = dns.run(conn, NOW)
    assert r["status"] == "ok" and r["day"] == D0
    got = {x["ip"]: x for x in rows(conn)}
    assert set(got) == {"10.99.0.1", "10.99.0.4", "10.99.0.5"}
    assert got["10.99.0.1"]["reason"] == "cross90" and got["10.99.0.1"]["prev_pct"] == 85.0 and not got["10.99.0.1"]["immediate"]
    assert got["10.99.0.4"]["prev_pct"] is None


def test_95以上即時_且預估3天內滿(conn):
    on(conn)
    put(conn, "10.99.0.1", "C:", {0: 95.0, 1: 70.0})                     # ≥95
    put(conn, "10.99.0.2", "D:", {0: 88.0, 1: 85.0, 2: 82.0, 3: 79.0})   # 每天 +3 → 4 天內滿？(100-88)/3=4 → 不算
    put(conn, "10.99.0.3", "D:", {0: 94.0, 1: 91.0, 2: 88.0})            # 每天 +3 → 2 天滿；也早就跨過 90（前一天 ≥90）
    put(conn, "10.99.0.4", "D:", {0: 60.0, 1: 40.0, 2: 20.0})            # 增很快但 <80%：不算
    dns.run(conn, NOW)
    got = {x["ip"]: x for x in rows(conn)}
    assert got["10.99.0.1"]["reason"] == "ge95" and got["10.99.0.1"]["immediate"]
    assert "10.99.0.2" not in got and "10.99.0.4" not in got
    assert got["10.99.0.3"]["reason"] == "full3d" and got["10.99.0.3"]["immediate"] and "預估 2 天滿" in got["10.99.0.3"]["note"]
    assert got["10.99.0.3"]["target"] == dns.SP_NAME


def test_days_to_full_不猜():
    d = date(2026, 11, 21)
    assert dns.days_to_full([(d, 90.0), (d - timedelta(days=1), 88.0)]) is None                     # 少於 3 天
    assert dns.days_to_full([(d, 90.0), (d - timedelta(days=1), 90.0), (d - timedelta(days=2), 90.0)]) is None   # 沒在上升
    assert dns.days_to_full([(d, 94.0), (d - timedelta(days=1), 91.0), (d - timedelta(days=2), 88.0)]) == 2.0


def test_去重_同日同機同碟一筆_重算不重複_即時蓋過一般(conn):
    on(conn)
    put(conn, "10.99.0.1", "C:", {0: 91.0, 1: 80.0})
    dns.run(conn, NOW)
    dns.run(conn, NOW)
    assert len(rows(conn)) == 1 and rows(conn)[0]["reason"] == "cross90"
    put(conn, "10.99.0.1", "C:", {0: 96.0})                 # 同一天稍後更新成 ≥95：升級為即時、仍然一筆
    dns.run(conn, NOW)
    x = rows(conn)
    assert len(x) == 1 and x[0]["reason"] == "ge95" and x[0]["immediate"] == 1 and x[0]["pct"] == 96.0
    put(conn, "10.99.0.1", "C:", {0: 91.0})                 # 掉回去：2.070.1 起以來源日期完整重建，結果跟著資料走（不留舊的 ≥95 假陽性）
    dns.run(conn, NOW)
    assert len(rows(conn)) == 1 and rows(conn)[0]["reason"] == "cross90" and rows(conn)[0]["immediate"] == 0


def test_同日重跑_完整重建_百分比下降後該列消失_負責人改變收件人跟著變(conn):
    """2.070.1（審查 p2#4）：之前同日重跑不更新、也不刪已不符合的列。"""
    on(conn)
    person(conn, "E1", "王大明", "wang@example.test")
    person(conn, "E2", "李小華", "li@example.test")
    hw(conn, "S-1", "a01", "10.99.1.1", owner="王大明")
    hw(conn, "S-2", "a02", "10.99.1.2", owner="王大明")
    put(conn, "10.99.1.1", "C:", {0: 91.0, 1: 70.0})
    put(conn, "10.99.1.2", "C:", {0: 92.0, 1: 70.0})
    dns.run(conn, NOW)
    x = {r["ip"]: r for r in rows(conn)}
    assert set(x) == {"10.99.1.1", "10.99.1.2"} and x["10.99.1.2"]["target"] == "王大明"
    put(conn, "10.99.1.1", "C:", {0: 85.0})                    # 稍後匯入修正版：1.1 降到 85% → 不再符合
    conn.execute("UPDATE hardware SET owner = '李小華' WHERE asset_serial = 'S-2'")   # 1.2 負責人換人
    conn.commit()
    dns.run(conn, NOW)
    y = {r["ip"]: r for r in rows(conn)}
    assert set(y) == {"10.99.1.2"}, "百分比下降的那列要消失"
    assert y["10.99.1.2"]["target"] == "李小華", "負責人改變後收件人要更新"


def test_重建中途出錯_整段退回_不留半份(conn, monkeypatch):
    on(conn)
    put(conn, "10.99.0.1", "C:", {0: 91.0, 1: 70.0})
    dns.run(conn, NOW)
    before = rows(conn)
    put(conn, "10.99.0.1", "C:", {0: 99.0})

    def boom(*a, **k):
        raise RuntimeError("中途壞掉")
    monkeypatch.setattr(dns, "resolve_target", boom)
    with pytest.raises(RuntimeError):
        dns.run(conn, NOW)
    assert rows(conn) == before                                 # 刪除也一起退回，不是「舊列被刪、新列沒寫」


def test_假磁碟與SWAP不算(conn):
    on(conn)
    put(conn, "10.99.0.1", "devfs", {0: 100.0, 1: 100.0})
    put(conn, "10.99.0.1", "Swap space", {0: 99.0, 1: 10.0})
    dns.run(conn, NOW)
    assert rows(conn) == []


# ── 負責人 ──
def test_負責人三種來源與對不到(conn):
    on(conn)
    person(conn, "E1", "王大明", "wang@example.test")
    person(conn, "E2", "李保管", "li@example.test")
    hw(conn, "S-1", "a01", "10.99.1.1", owner="王大明", custodian="李保管")     # 負責人優先
    hw(conn, "S-2", "a02", "10.99.1.2", owner=None, custodian="李保管")          # 沒負責人 → 保管者
    hw(conn, "S-3", "a03", "10.99.1.3", owner="查無此人", custodian=None)        # 名字在 AD 查不到信箱 → 落到 SP
    hw(conn, "S-4", "a04", "10.99.1.4", owner="王大明", custodian="李保管")
    for ip in ("10.99.1.1", "10.99.1.2", "10.99.1.3", "10.99.1.4", "10.99.1.9"):   # .9 資產表根本沒有
        put(conn, ip, "C:", {0: 91.0, 1: 70.0})
    dns.run(conn, NOW)
    got = {x["ip"]: x for x in rows(conn)}
    assert (got["10.99.1.1"]["target"], got["10.99.1.1"]["target_src"]) == ("王大明", dns.SRC_OWNER)
    assert (got["10.99.1.2"]["target"], got["10.99.1.2"]["target_src"]) == ("李保管", dns.SRC_CUSTODIAN)
    assert (got["10.99.1.3"]["target"], got["10.99.1.3"]["target_src"]) == (dns.SP_NAME, dns.SRC_SP) and "查不到信箱" in got["10.99.1.3"]["note"]
    assert (got["10.99.1.9"]["target"], got["10.99.1.9"]["target_src"]) == (dns.SP_NAME, dns.SRC_SP) and "對不到資產" in got["10.99.1.9"]["note"]


def test_負責人查不到信箱_退到保管者(conn):
    on(conn)
    person(conn, "E2", "李保管", "li@example.test")
    hw(conn, "S-1", "a01", "10.99.1.1", owner="離職的人", custodian="李保管")
    put(conn, "10.99.1.1", "C:", {0: 91.0, 1: 70.0})
    dns.run(conn, NOW)
    x = rows(conn)[0]
    assert x["target"] == "李保管" and x["target_src"] == dns.SRC_CUSTODIAN and "CIA負責人「離職的人」查不到信箱" in x["note"]


def test_正式機沒有負責人資料也不報錯_全部對到SP(conn):
    on(conn)                                                # 沒有任何 hardware／ad_person 資料
    hw(conn, "S-9", "z01", "10.99.2.3", owner="某人")        # 有登記人名但 AD 人員表是空的
    conn.execute("DROP TABLE ad_person")                    # 甚至連 AD 表都還沒建
    conn.commit()
    put(conn, "10.99.2.3", "C:", {0: 95.5})
    put(conn, "10.99.2.1", "C:", {0: 93.0, 1: 70.0})
    put(conn, "10.99.2.2", "D:", {0: 97.0})
    r = dns.run(conn, NOW)
    assert r["status"] == "ok" and {x["target"] for x in rows(conn)} == {dns.SP_NAME} and len(rows(conn)) == 3


# ── 關閉／缺表／過舊 ──
def test_關閉不寫表_連表都不建(conn):
    put(conn, "10.99.0.1", "C:", {0: 99.0})
    assert dns.run(conn, NOW) == {"status": "disabled"} and dns.tick(conn, NOW) == {"status": "disabled"}
    assert not dns._has(conn, "disk_notice_shadow")
    assert conn.execute("SELECT 1 FROM app_settings WHERE key = ?", (dns.KEY_LAST_RUN,)).fetchone() is None


def test_缺表與沒資料不報錯(tmp_path):
    p = tmp_path / "e.db"
    db.init_db(p)
    c = db.get_connection(p)
    try:
        v = dns.view(c)                                      # 沒有 disk_notice_shadow 表
        assert v["rows"] == [] and v["summary"]["total"] == 0 and v["enabled"] is False
        dns.save_enabled(c, True)
        assert dns.run(c, NOW)["status"] == "no_data"        # 沒有 relay_disk_util 資料
        relay_import.ensure(c)
        assert dns.run(c, NOW)["status"] == "no_data"        # 有表沒資料
    finally:
        c.close()


def test_太舊的資料不當今天算(conn):
    on(conn)
    put(conn, "10.99.0.1", "C:", {5: 99.0})                  # 最新資料日是 5 天前
    assert dns.run(conn, NOW)["status"] == "stale" and not dns._has(conn, "disk_notice_shadow")


def test_tick_七點前不跑_一天只跑一次(conn):
    on(conn)
    put(conn, "10.99.0.1", "C:", {0: 91.0, 1: 70.0})
    assert dns.tick(conn, NOW.replace(hour=6))["status"] == "early"
    assert dns.tick(conn, NOW)["status"] == "ok"
    assert dns.tick(conn, NOW.replace(hour=9))["status"] == "done_today"
    assert dns.tick(conn, NOW + timedelta(days=1))["status"] in ("ok", "stale")     # 隔天會再算（資料日沒更新時 stale）


# ── 絕不寄信 ──
def test_整條路徑不寄信_寄信與Teams函式換成會raise的替身仍跑完(conn, monkeypatch):
    import smtplib

    import alert_notify
    import daily_check
    import eos_notify
    import opening_notify
    import teams_notify
    import vuln_notify

    def boom(*a, **k):
        raise AssertionError("影子模式不可以寄信或送 Teams")
    for mod, names in ((teams_notify, ["enqueue", "send_now", "set_webhook", "save_settings"]),
                       (smtplib.SMTP, ["__init__", "sendmail", "send_message"]),
                       (smtplib.SMTP_SSL, ["__init__"]),
                       (daily_check, ["_send_mail", "_notify", "_mail_step"]),
                       (alert_notify, [n for n in ("send", "send_mail", "notify") if hasattr(alert_notify, n)]),
                       (opening_notify, [n for n in ("send", "send_mail") if hasattr(opening_notify, n)]),
                       (vuln_notify, [n for n in ("send_mail", "send") if hasattr(vuln_notify, n)]),
                       (eos_notify, [n for n in ("send_mail", "send", "_send") if hasattr(eos_notify, n)])):
        for n in names:
            if hasattr(mod, n):
                monkeypatch.setattr(mod, n, boom)
    on(conn)
    person(conn, "E1", "王大明", "wang@example.test")
    hw(conn, "S-1", "a01", "10.99.1.1", owner="王大明")
    put(conn, "10.99.1.1", "C:", {0: 91.0, 1: 70.0})
    put(conn, "10.99.1.2", "C:", {0: 99.0})
    assert dns.tick(conn, NOW)["status"] == "ok" and len(rows(conn)) == 2
    assert dns.view(conn, today=date(2026, 11, 21))["summary"]["total"] == 2
    assert not conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'teams_notify_delivery'").fetchone() or \
        conn.execute("SELECT COUNT(*) FROM teams_notify_delivery").fetchone()[0] == 0


def test_最底層fail_fast_SMTP_socket_Teams佇列_跑完整流程零新增(conn, monkeypatch):
    """2.070.1（審查 p2#7／#8）：不靠「替換部分公開名稱」，而是在最底層全部攔死——任何送出路徑一碰就 raise。
    包含：smtplib.SMTP／SMTP_SSL 建構、socket connect／create_connection、Teams 佇列實際寫入（_enqueue）、
    eos_notify._send_smtp；跑完 run／tick／API /run／view，Teams 佇列與所有通知／寄送類資料表零新增。"""
    import smtplib
    import socket

    import eos_notify
    import teams_notify

    def boom(*a, **k):
        raise AssertionError("影子模式不可以送出任何東西（最底層攔截）")
    monkeypatch.setattr(smtplib, "SMTP", boom)
    monkeypatch.setattr(smtplib, "SMTP_SSL", boom)
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def _loop(addr):                      # TestClient 在 Windows 用 loopback socketpair，這種放行；其餘一律攔
        return isinstance(addr, tuple) and str(addr[0]) in ("127.0.0.1", "::1", "localhost")

    def guarded_connect(self, addr, *a, **k):
        if not _loop(addr):
            boom()
        return real_connect(self, addr, *a, **k)

    def guarded_connect_ex(self, addr, *a, **k):
        if not _loop(addr):
            boom()
        return real_connect_ex(self, addr, *a, **k)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(teams_notify, "_enqueue", boom)
    monkeypatch.setattr(eos_notify, "_send_smtp", boom)

    teams_notify.ensure(conn)
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    watch = [n for n in names if n != "disk_notice_shadow" and any(k in n for k in ("teams", "deliver", "mail", "notify", "outbox"))]
    assert "teams_notify_delivery" in watch

    def snapshot():
        return {n: conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in watch}
    before = snapshot()

    person(conn, "E1", "王大明", "wang@example.test")
    hw(conn, "S-1", "a01", "10.99.1.1", owner="王大明")
    put(conn, "10.99.1.1", "C:", {0: 91.0, 1: 70.0})
    put(conn, "10.99.1.2", "C:", {0: 99.0})
    cl, api = _client(conn, "admin")
    try:
        assert cl.put("/api/daily-check/disk-notice-shadow/settings", json={"enabled": True}).status_code == 200
        assert cl.post("/api/daily-check/disk-notice-shadow/run").json()["status"] in ("ok", "stale")
        assert cl.get("/api/daily-check/disk-notice-shadow").status_code == 200
    finally:
        api.app.dependency_overrides.clear()
    assert dns.tick(conn, NOW)["status"] in ("ok", "done_today")
    assert dns.run(conn, NOW)["status"] == "ok" and len(rows(conn)) == 2
    assert snapshot() == before, "影子模式跑完，Teams 佇列與通知／寄送類資料表不得有任何新增"


def test_fail_fast_確實會攔到送出(monkeypatch):
    """自我驗證：替身真的會 raise（防止 monkeypatch 名稱沒生效而空轉）。"""
    import smtplib
    import socket
    monkeypatch.setattr(smtplib, "SMTP", lambda *a, **k: (_ for _ in ()).throw(AssertionError("x")))
    with pytest.raises(AssertionError):
        smtplib.SMTP("10.99.0.1")
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("x")))
    with pytest.raises(AssertionError):
        socket.socket().connect(("10.99.0.1", 25))


def test_模組原始碼不含寄信相關匯入():
    src = (ROOT / "APP" / "asset-module" / "backend" / "disk_notice_shadow.py").read_text(encoding="utf-8")
    import ast
    imported = set()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Import):
            imported |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            imported.add(n.module)
    bad = {"smtplib", "teams_notify", "alert_notify", "opening_notify", "vuln_notify", "email", "requests", "urllib.request", "notice_digest"}
    assert not (imported & bad), imported & bad


# ── 畫面資料 ──
def test_view_彙總每人幾顆_對不到幾顆_即時幾顆_項次(conn):
    on(conn)
    person(conn, "E1", "王大明", "wang@example.test")
    hw(conn, "S-1", "a01", "10.99.1.1", owner="王大明")
    hw(conn, "S-2", "a02", "10.99.1.2", owner="王大明")
    put(conn, "10.99.1.1", "C:", {0: 91.0, 1: 70.0})
    put(conn, "10.99.1.2", "C:", {0: 92.0, 1: 70.0})
    put(conn, "10.99.1.3", "C:", {0: 93.0, 1: 70.0})          # 對不到
    put(conn, "10.99.1.4", "C:", {0: 98.0})                   # 即時
    dns.run(conn, NOW)
    v = dns.view(conn, today=date(2026, 11, 21))
    s = v["summary"]
    assert (s["total"], s["owner_disks"], s["no_owner"], s["immediate"]) == (4, 2, 1, 1)
    assert s["per_person"] == [{"target": "王大明", "disks": 2}]
    assert [r["__no"] for r in v["rows"]] == [1, 2, 3, 4] and v["rows"][0]["immediate"] is True and v["rows"][0]["reason_text"].startswith("≥95%")
    assert dns.view(conn, days=1, today=date(2026, 11, 30))["rows"] == []        # 超過天數不列


# ── 權限與 API ──
def _client(conn, role):
    import api
    from fastapi.testclient import TestClient
    path = conn.execute("PRAGMA database_list").fetchone()[2]

    def _get_db():
        c = db.get_connection(Path(path))
        try:
            yield c
        finally:
            c.close()
    api.app.dependency_overrides[api.get_db] = _get_db
    api.app.dependency_overrides[api.require_auth] = lambda: {"username": "t", "role": role}
    return TestClient(api.app), api


def test_權限分級():
    import access_policy as ap
    assert ap.level_for("GET", "/api/daily-check/disk-notice-shadow") == "admin"
    assert ap.level_for("PUT", "/api/daily-check/disk-notice-shadow/settings") == "admin"
    assert ap.level_for("POST", "/api/daily-check/disk-notice-shadow/run") == "admin"


def test_API_非管理員403_管理員可開關與試算(conn):
    put(conn, "10.99.0.1", "C:", {0: 91.0, 1: 70.0})
    cl, api = _client(conn, "user")
    try:
        assert cl.get("/api/daily-check/disk-notice-shadow").status_code == 403
        cl, api = _client(conn, "admin")
        v = cl.get("/api/daily-check/disk-notice-shadow").json()
        assert v["enabled"] is False and v["rows"] == []
        assert cl.post("/api/daily-check/disk-notice-shadow/run").json() == {"status": "disabled"}     # 關閉時試算也不寫
        assert cl.put("/api/daily-check/disk-notice-shadow/settings", json={"enabled": True}).json() == {"enabled": True}
        assert cl.put("/api/daily-check/disk-notice-shadow/settings", json={"enabled": "x"}).status_code == 422
        assert cl.get("/api/daily-check/disk-notice-shadow", params={"days": 999}).json()["days"] == 30
        assert conn.execute("SELECT COUNT(*) FROM daily_check_setting_log WHERE key = ?", (dns.KEY_ENABLED,)).fetchone()[0] == 1   # 開關有稽核
    finally:
        api.app.dependency_overrides.clear()
