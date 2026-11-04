"""Q95 EOS 到期自動通知。守的是「做錯會比沒做更糟」的那幾件（跟 test_vuln_notify 同一組精神）：

1. 同一筆不可以每個門檻各寄一次；跨進更急的一級才再出現
2. 寄失敗不可以算寄過（不更新 last_bucket）
3. 找不到收件人的每一種都進「沒人收」那一籃，不可以靜靜跳過；同名疑慮不亂猜
4. 預覽（乾跑）永遠不寄、不寫紀錄、不動 last_bucket
5. 本期 0 筆也寄一封；沒設代收信箱要講出來
6. 硬體 EOS → hardware.owner（SP）；作業系統 EOS → software.owner（AP）；同一台只算一台
測試一律用假 SMTP 與假 AD 名單，不碰網路。
"""
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import ad_import  # noqa: E402
import db  # noqa: E402
import eos  # noqa: E402
import eos_notify as en  # noqa: E402
import vuln_notify as vn  # noqa: E402

TODAY = date(2026, 11, 1)          # 每月 1 號
CFG = {"smtp_host": "192.0.2.25", "smtp_port": 25, "mail_from": "webit3@example.invalid"}
ENC = {"enabled": True, "fallback_to": "soc@example.invalid"}

# 假的 EOS 對照（不依賴真實資料檔的日期會隨時間過期）
FAKE_OS = [
    {"name": "Rocky Linux 9", "eos_date": "2027-03-01"},                  # 距 11/01 = 120 天 → 180 級
    {"name": "Red Hat Enterprise Linux 8", "eos_date": "2027-09-01"},    # 304 天 → 365 級
    {"name": "Ubuntu 18.04", "eos_date": "2023-05-31"},                   # 已過 EOS → 0 級
    {"name": "AIX 7.2", "eos_date": "2031-01-01"},                        # 很遠 → 不提
]
FAKE_HW = [{"name": "Dell PowerEdge R760", "eos_date": "2027-01-01"}]     # 61 天 → 180 級


@pytest.fixture(autouse=True)
def fake_eos_tables():
    eos._os_table = FAKE_OS
    eos._hw_table = FAKE_HW
    yield
    eos.reload_tables()


@pytest.fixture()
def conn(tmp_path):
    db.init_db(tmp_path / "t.db")
    c = db.get_connection(tmp_path / "t.db")
    c.execute(ad_import.TABLE_SQL)
    vn._ensure(c)
    en._ensure(c)
    vn.save_settings(c, CFG)
    en.save_settings(c, ENC)
    c.commit()
    try:
        yield c
    finally:
        c.close()


def _person(c, eid, name, mail, enabled=1, mgr=None, dept="架構部"):
    c.execute("INSERT INTO ad_person (employee_id, display_name, mail, enabled, manager_mail, dept_name) "
              "VALUES (?,?,?,?,?,?)", (eid, name, mail, enabled, mgr, dept))


def _hw(c, serial, host, ip, os_=None, model=None, owner=None, status="使用中"):
    c.execute("INSERT INTO hardware (asset_serial, hostname, ip, os, device_model, owner, asset_status, "
              "environment) VALUES (?,?,?,?,?,?,?,'正式')", (serial, host, ip, os_, model, owner, status))


def _sw(c, serial, owner):
    c.execute("INSERT INTO software (asset_serial, owner) VALUES (?,?)", (serial, owner))


class Sender:
    def __init__(self, ok=True):
        self.ok, self.mails = ok, []

    def __call__(self, cfg, to, cc, subject, body):
        self.mails.append({"to": to, "cc": cc, "subject": subject, "body": body})
        return (self.ok, "250 OK" if self.ok else "Connection refused")


class Boom:
    """乾跑時被呼叫就是錯。"""
    def __call__(self, *a, **k):
        raise AssertionError("乾跑／預覽不可以寄信")


# ── 門檻 ───────────────────────────────────────────────────────────────
def test_兩級門檻():
    b = (180, 365)
    assert en.bucket_for(-3, b) == 0
    assert en.bucket_for(120, b) == 180
    assert en.bucket_for(180, b) == 180
    assert en.bucket_for(181, b) == 365
    assert en.bucket_for(365, b) == 365
    assert en.bucket_for(366, b) is None
    assert en.bucket_for(None, b) is None


def test_門檻存設定且只能兩級():
    assert en.validate_buckets([365, 180]) == (180, 365)
    for bad in ([180], [30, 60, 90], [100, 100], [0, 5], ["a", 2]):
        with pytest.raises(ValueError):
            en.validate_buckets(bad)
    assert en.buckets_of({}) == (180, 365)                       # 沒填就用退路
    assert en.buckets_of({"buckets": [90, 270]}) == (90, 270)     # 設定改了就照設定
    assert en.buckets_of({"buckets": [1]}) == (180, 365)          # 壞值不讓整個功能停擺


def test_月精度的EOS日期補成月初():
    assert en.parse_eos_date("2027-04") == date(2027, 4, 1)
    assert en.parse_eos_date("2027-04-15") == date(2027, 4, 15)
    assert en.parse_eos_date(None) is None and en.parse_eos_date("亂寫") is None


# ── 收件人：硬體→hardware.owner、OS→software.owner ─────────────────────
def test_硬體寄給擁有者_OS寄給軟體擁有者(conn):
    _person(conn, "E1", "王SP", "sp@example.invalid")
    _person(conn, "E2", "李AP", "ap@example.invalid")
    _hw(conn, "S1", "srv01", "10.99.0.1", os_="Rocky Linux 9.7", model="Dell PowerEdge R760", owner="王SP")
    _sw(conn, "S1", "李AP")
    conn.commit()
    s = Sender()
    r = en.run(conn, TODAY, "monthly", sender=s)
    assert r["sent"] == 2 and r["failed"] == 0
    by_to = {m["to"]: m for m in s.mails}
    assert "[硬體]" in by_to["sp@example.invalid"]["body"] and "[作業系統]" not in by_to["sp@example.invalid"]["body"]
    assert "[作業系統]" in by_to["ap@example.invalid"]["body"] and "[硬體]" not in by_to["ap@example.invalid"]["body"]


def test_同一人又是SP又是AP只收一封(conn):
    _person(conn, "E1", "王大", "one@example.invalid")
    _hw(conn, "S1", "srv01", "10.99.0.1", os_="Rocky Linux 9.7", model="Dell PowerEdge R760", owner="王大")
    _sw(conn, "S1", "王大")
    conn.commit()
    s = Sender()
    en.run(conn, TODAY, "monthly", sender=s)
    assert len(s.mails) == 1 and "[硬體]" in s.mails[0]["body"] and "[作業系統]" in s.mails[0]["body"]


def test_一格填多個人_一個一個查():
    assert en.split_owners("甲、乙,丙／丁;甲") == ["甲", "乙", "丙", "丁"]
    assert en.split_owners("John Smith") == ["John Smith"]
    assert en.split_owners(None) == []


def test_同一台多筆登記只算一台(conn):
    """全站只有一把尺 machine_key：同主機名＋IP 的多筆登記不能各通知一次。"""
    _person(conn, "E1", "王SP", "sp@example.invalid")
    _person(conn, "E2", "李AP", "ap@example.invalid")
    _hw(conn, "S1", "srv01", "10.99.0.1", os_="Rocky Linux 9.7", owner="王SP")
    _hw(conn, "S2", "srv01", "10.99.0.1", os_="Rocky Linux 9.7", owner="王SP")
    _sw(conn, "S1", "李AP")
    _sw(conn, "S2", "李AP")
    conn.commit()
    d = en.collect(conn, TODAY)
    assert d["stats"]["machines"] == 1
    assert len(d["by_owner"]["E2"]["items"]) == 1, "同一台被通知了兩次"


def test_退役與虛擬機不通知(conn):
    _person(conn, "E1", "王SP", "sp@example.invalid")
    _hw(conn, "S1", "old01", "10.99.0.5", os_="Ubuntu 18.04", owner="王SP", status="報廢")
    _hw(conn, "S2", "vm01", "10.99.0.6", model="VMware Virtual Machine", owner="王SP")
    conn.commit()
    d = en.collect(conn, TODAY)
    assert not d["by_owner"] and not d["orphans"]


# ── 找不到收件人：每一種都進沒人收 ─────────────────────────────────────
def test_找不到收件人的每一種都進沒人收(conn):
    _person(conn, "E1", "有信箱", "ok@example.invalid")
    _person(conn, "E2", "離職者", "gone@example.invalid", enabled=0)
    _person(conn, "E3", "沒信箱", None)
    _person(conn, "E4", "菜市仔名", "a@example.invalid")
    _person(conn, "E5", "菜市仔名", "b@example.invalid")
    # 硬體軌：5 台各一種原因（機型都用會中的 Dell）
    for i, owner in enumerate([None, "查無此人", "離職者", "沒信箱", "菜市仔名"]):
        _hw(conn, f"H{i}", f"hw{i}", f"10.99.1.{i}", model="Dell PowerEdge R760", owner=owner)
    # OS 軌：一台軟體表沒登記、一台登記了但沒填擁有者
    _hw(conn, "O1", "os1", "10.99.2.1", os_="Rocky Linux 9.7")
    _hw(conn, "O2", "os2", "10.99.2.2", os_="Rocky Linux 9.7")
    _sw(conn, "O2", "")
    conn.commit()

    d = en.collect(conn, TODAY)
    assert not d["by_owner"]
    whys = sorted(o["_why"] for o in d["orphans"])
    assert whys == sorted([en.NO_OWNER_HW, vn.NOT_IN_AD, vn.DISABLED, vn.NO_MAIL, vn.AMBIGUOUS,
                           en.NO_SOFTWARE, en.NO_OWNER_SW])


def test_同名疑慮不可以亂猜(conn):
    _person(conn, "E4", "菜市仔名", "a@example.invalid")
    _person(conn, "E5", "菜市仔名", "b@example.invalid")
    _hw(conn, "H1", "hw1", "10.99.1.1", model="Dell PowerEdge R760", owner="菜市仔名")
    conn.commit()
    s = Sender()
    en.run(conn, TODAY, "monthly", sender=s)
    assert all(m["to"] not in ("a@example.invalid", "b@example.invalid") for m in s.mails), "寄給了其中一個同名的人"


def test_沒人收的照樣寄給代收信箱並寫明原因(conn):
    _hw(conn, "H1", "hw1", "10.99.1.1", model="Dell PowerEdge R760", owner="查無此人")
    _person(conn, "E9", "路人", "x@example.invalid")      # AD 要有人，否則卡在前一關
    conn.commit()
    s = Sender()
    r = en.run(conn, TODAY, "monthly", sender=s)
    assert r["orphan_items"] == 1
    assert [m["to"] for m in s.mails] == [ENC["fallback_to"]]
    assert vn.NOT_IN_AD in s.mails[0]["body"]


def test_沒設代收信箱時沒人收那批不可以靜靜吞掉(conn):
    en.save_settings(conn, {"enabled": True})
    _person(conn, "E9", "路人", "x@example.invalid")
    _hw(conn, "H1", "hw1", "10.99.1.1", model="Dell PowerEdge R760", owner="查無此人")
    conn.commit()
    r = en.run(conn, TODAY, "monthly", sender=Sender())
    assert r["failed"] >= 1
    assert any("沒有寄出去" in (d.get("detail") or "") for d in r["details"])


# ── 不重複通知／寄失敗不算寄過 ─────────────────────────────────────────
def _basic(conn):
    _person(conn, "E1", "王SP", "sp@example.invalid", mgr="boss@example.invalid")
    _hw(conn, "S1", "srv01", "10.99.0.1", model="Dell PowerEdge R760", owner="王SP")   # 61 天 → 180
    conn.commit()


def test_寄失敗不更新bucket_寄成功才更新(conn):
    _basic(conn)
    bad = Sender(ok=False)
    r = en.run(conn, TODAY, "monthly", sender=bad)
    assert r["failed"] >= 1 and r["sent"] == 0
    assert conn.execute("SELECT COUNT(*) FROM eos_notify_state").fetchone()[0] == 0, \
        "寄失敗卻更新了 bucket——下次就不會再寄"
    row = en.logs(conn)[0]
    assert row["status"] == "failed" and "refused" in row["detail"]
    # 失敗後下一期還會再進同一封
    good = Sender()
    en.run(conn, TODAY, "monthly", sender=good)
    assert len(good.mails) == 1
    st = conn.execute("SELECT last_bucket FROM eos_notify_state").fetchone()[0]
    assert st == 180
    log = en.logs(conn)[0]
    assert log["status"] == "sent" and log["smtp_host"] == CFG["smtp_host"] and log["mail_from"] == CFG["mail_from"]


def test_同一級不重複_跨進更急才再出現(conn):
    _basic(conn)
    s = Sender()
    en.run(conn, TODAY, "monthly", sender=s)
    assert len(s.mails) == 1
    s2 = Sender()
    en.run(conn, TODAY, "monthly", sender=s2)
    # 這期已寄過：只剩 0 筆的心跳信，不是同一筆再寄
    assert all("本期 0 筆" in m["subject"] for m in s2.mails)
    # 過了 EOS 日（已過 EOS＝0 級）才又出現
    s3 = Sender()
    en.run(conn, date(2027, 2, 1), "monthly", sender=s3)
    assert any("[硬體]" in m["body"] and "已過 EOS" in m["body"] for m in s3.mails)


def test_從365跨進180才再出現(conn):
    _person(conn, "E1", "王SP", "sp@example.invalid")
    _hw(conn, "S1", "srv01", "10.99.0.1", os_="Red Hat Enterprise Linux 8.9")   # EOS 2027-09-01
    _sw(conn, "S1", "王SP")
    conn.commit()
    s = Sender()
    en.run(conn, TODAY, "monthly", sender=s)                      # 304 天 → 365 級
    assert "365 天內到期" in s.mails[0]["body"]
    s2 = Sender()
    en.run(conn, date(2027, 3, 1), "monthly", sender=s2)          # 184 天，仍在 365 級
    assert not any("[作業系統]" in m["body"] for m in s2.mails)
    s3 = Sender()
    en.run(conn, date(2027, 4, 1), "monthly", sender=s3)          # 153 天 → 跨進 180
    assert any("180 天內到期" in m["body"] for m in s3.mails)


def test_OS換版後不會被舊的bucket蓋住(conn):
    """state key 含項目名：升級到另一個 OS 後，新的 EOS 是新的一筆。"""
    _person(conn, "E1", "王SP", "sp@example.invalid")
    _hw(conn, "S1", "srv01", "10.99.0.1", os_="Rocky Linux 9.7")
    _sw(conn, "S1", "王SP")
    conn.commit()
    en.run(conn, TODAY, "monthly", sender=Sender())
    keys = [r[0] for r in conn.execute("SELECT state_key FROM eos_notify_state")]
    assert keys and all("Rocky Linux 9.7" in k for k in keys)


# ── 乾跑／預覽不寄 ─────────────────────────────────────────────────────
def test_乾跑不寄不寫紀錄不動bucket(conn):
    _basic(conn)
    r = en.run(conn, TODAY, "monthly", dry_run=True, sender=Boom())
    assert r["ok"] and r["details"] and r["details"][0]["status"] == "預覽"
    assert conn.execute("SELECT COUNT(*) FROM eos_notify_state").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM eos_notify_log").fetchone()[0] == 0


def test_預覽不需要SMTP且列出沒人收的原因(conn, monkeypatch):
    vn.save_settings(conn, {})                                     # SMTP 一個都沒填
    _person(conn, "E1", "王SP", "sp@example.invalid")
    _hw(conn, "S1", "srv01", "10.99.0.1", model="Dell PowerEdge R760", owner="王SP")
    _hw(conn, "S2", "srv02", "10.99.0.2", model="Dell PowerEdge R760", owner="查無此人")
    conn.commit()
    monkeypatch.setattr(en, "_send_smtp", Boom())
    monkeypatch.setattr(vn, "_send_smtp", Boom())
    p = en.preview(conn, TODAY)
    assert p["ok"] and p["groups"][0]["to"] == "sp@example.invalid"
    assert [o["why"] for o in p["orphans"]] == [vn.NOT_IN_AD]
    assert p["orphans"][0]["hostname"] == "srv02"
    assert conn.execute("SELECT COUNT(*) FROM eos_notify_log").fetchone()[0] == 0


def test_預覽不看寄過沒有(conn):
    """畫面要看「現在到底有哪些」，不能因為寄過就從預覽消失。"""
    _basic(conn)
    en.run(conn, TODAY, "monthly", sender=Sender())
    assert en.preview(conn, TODAY)["groups"], "寄過的從預覽消失了"


def test_AD沒匯入時預覽講清楚(conn):
    c2 = conn
    c2.execute("DROP TABLE ad_person")
    c2.commit()
    p = en.preview(c2, TODAY)
    assert p["ok"] is False and "資料匯入" in p["reason"]


# ── 0 筆心跳／設定 ─────────────────────────────────────────────────────
def test_本期0筆也寄一封給代收信箱(conn):
    _person(conn, "E1", "王SP", "sp@example.invalid")
    conn.commit()
    s = Sender()
    r = en.run(conn, TODAY, "monthly", sender=s)
    assert r["zero"] and [m["to"] for m in s.mails] == [ENC["fallback_to"]]
    assert "0 筆" in s.mails[0]["subject"] and "0 筆" in s.mails[0]["body"]


def test_0筆但沒設代收信箱不可以靜靜跳過(conn):
    en.save_settings(conn, {"enabled": True})
    _person(conn, "E1", "王SP", "sp@example.invalid")
    conn.commit()
    r = en.run(conn, TODAY, "monthly", sender=Sender())
    assert r["failed"] >= 1 and any("沒有寄出去" in (d.get("detail") or "") for d in r["details"])


def test_SMTP缺值要指名_AD沒匯入要指路(conn):
    vn.save_settings(conn, {"smtp_host": "h"})
    r = en.run(conn, TODAY, "monthly", sender=Boom())
    assert r["ok"] is False and "SMTP 埠" in r["reason"] and "寄件者" in r["reason"]
    vn.save_settings(conn, CFG)
    conn.execute("DROP TABLE ad_person")
    conn.commit()
    r = en.run(conn, TODAY, "monthly", sender=Boom())
    assert r["ok"] is False and "資料匯入" in r["reason"]


def test_設定壞掉跟沒設定要分得開(conn):
    conn.execute("UPDATE app_settings SET value = ? WHERE key = ?", ("{不是 JSON", en.SETTING_KEY))
    conn.commit()
    with pytest.raises(en.SettingsCorrupt) as e:
        en.load_settings(conn)
    assert "不是沒設定" in str(e.value)
    r = en.run(conn, TODAY, "monthly", sender=Boom())
    assert r["ok"] is False and "壞掉" in r["reason"]
    assert en.is_due(conn, datetime(2026, 11, 1, 8, 0)) == []


def test_原始碼沒有寫死IP或信箱():
    src = (ROOT / "APP" / "asset-module" / "backend" / "eos_notify.py").read_text(encoding="utf-8")
    code = "\n".join(l.split("#", 1)[0] for l in src.splitlines())
    assert not re.search(r"\d{1,3}(\.\d{1,3}){3}", code)
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", code)


def test_副本_一律副本與主管不重複(conn):
    en.save_settings(conn, {**ENC, "always_cc": "win@example.invalid"})
    _person(conn, "E1", "王SP", "sp@example.invalid", mgr="WIN@example.invalid")
    _hw(conn, "S1", "srv01", "10.99.0.1", model="Dell PowerEdge R760", owner="王SP")
    conn.commit()
    s = Sender()
    en.run(conn, TODAY, "monthly", sender=s)
    assert s.mails[0]["cc"] == ["win@example.invalid"], "同一個信箱出現兩次"


# ── 排程 ───────────────────────────────────────────────────────────────
D1_8 = datetime(2026, 11, 1, 8, 0)


def test_只有每月一號():
    assert en.schedules_due(date(2026, 11, 1)) == ["monthly"]
    assert en.schedules_due(date(2026, 11, 2)) == []
    assert en.schedules_due(date(2026, 11, 2 + 4)) == []


def test_沒開就不准自己寄(conn):
    _basic(conn)
    en.save_settings(conn, {"fallback_to": "soc@example.invalid"})     # 沒有 enabled
    assert en.is_due(conn, D1_8) == []


def test_同一期不寄兩次_失敗要補_過下班不補(conn):
    _basic(conn)
    bad = Sender(ok=False)
    assert len(en.tick(conn, D1_8, sender=bad)) == 1
    assert en.tick(conn, D1_8 + timedelta(minutes=30), sender=bad) == [], "冷卻內重試＝洗版"
    good = Sender()
    assert len(en.tick(conn, D1_8 + timedelta(minutes=61), sender=good)) == 1
    assert en.tick(conn, D1_8 + timedelta(hours=3), sender=good) == [], "已成功的一期又寄"
    runs = en.last_runs(conn)
    assert len(runs) == 2 and runs[1]["failed"] >= 1, "失敗那次沒留痕跡"
    assert en.is_due(conn, datetime(2026, 11, 1, 18, 0)) == []


def test_沒到點不跑(conn):
    _basic(conn)
    assert en.tick(conn, datetime(2026, 11, 1, 7, 59), sender=Boom()) == []
    assert en.tick(conn, datetime(2026, 11, 2, 9, 0), sender=Boom()) == []


# ── API ───────────────────────────────────────────────────────────────
def _client(tmp_path):
    import api
    import auth
    from fastapi.testclient import TestClient
    path = tmp_path / "api.db"
    db.init_db(path)

    def _override():
        c = db.get_connection(path)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _override
    client = TestClient(api.app)
    c = db.get_connection(path)
    try:
        db.create_user(c, "admin", auth.hash_password("test-password-123"))
        db.create_user(c, "bob", auth.hash_password("test-password-123"))
    finally:
        c.close()
    return api, client, path


def _login(client, user):
    r = client.post("/api/auth/login", json={"username": user, "password": "test-password-123"})
    assert r.status_code == 200


def test_API要登入且寫入限管理員(tmp_path):
    api, client, _ = _client(tmp_path)
    try:
        for m, u in (("get", "/api/eos-notify/settings"), ("get", "/api/eos-notify/preview"),
                     ("get", "/api/eos-notify/logs"), ("post", "/api/eos-notify/run"),
                     ("put", "/api/eos-notify/settings")):
            assert getattr(client, m)(u).status_code in (401, 422) or m == "put"
        assert client.get("/api/eos-notify/settings").status_code == 401
        _login(client, "bob")
        assert client.get("/api/eos-notify/settings").status_code == 200
        assert client.put("/api/eos-notify/settings", json={"enabled": False}).status_code == 403
        assert client.post("/api/eos-notify/run").status_code == 403
    finally:
        api.app.dependency_overrides.clear()


def test_API門檻驗證與啟用要先填SMTP(tmp_path):
    api, client, path = _client(tmp_path)
    try:
        _login(client, "admin")
        assert client.put("/api/eos-notify/settings", json={"buckets": [30]}).status_code == 400
        r = client.put("/api/eos-notify/settings", json={"enabled": True})
        assert r.status_code == 400 and "SMTP" in r.json()["detail"]
        ok = client.put("/api/eos-notify/settings", json={"buckets": [90, 270], "fallback_to": "a@example.invalid"})
        assert ok.status_code == 200
        got = client.get("/api/eos-notify/settings").json()
        assert got["buckets"] == [90, 270] and got["can_edit"] is True
        p = client.get("/api/eos-notify/preview").json()
        assert p["ok"] is False and "資料匯入" in p["reason"]       # 沒匯 AD：講清楚，不是 500
        assert client.get("/api/eos-notify/logs").json()["items"] == []
    finally:
        api.app.dependency_overrides.clear()


# Q91：這個檔案測的是「誰能做什麼」，不套 conftest 的「測試帳號預設 admin」相容層（見 tests/conftest.py）。
ROLE_AWARE_TESTS = True
