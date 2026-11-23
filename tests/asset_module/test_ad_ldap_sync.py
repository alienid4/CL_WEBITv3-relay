"""AD 人員名單 LDAPS 每日同步（2.068.x）：假 LDAP 連線把整條路走一遍，另外用本機 TLS 伺服器實測憑證驗證與「沒驗過不送 bind」。

示範資料只用 *.example.test 與假員編（0100000x）、假姓名；不連任何真的 DC。
2.068.2 依 GPT 審查＋裁判補：分頁控制項完整性、UAC 未知、有效人數防呆、DB 錯不放行、換台只限 bind 前、
持久化鎖帳節流（帳號正規化、共用額度）、憑證主機名不接受 IP 替代、CA 必填、422/500 不回顯、補跑同一判斷、台北時區、
running 回收、名單與成功紀錄同交易、單一來源、設定稽核、適用範圍。
"""
import datetime as _dt
import json
import logging
import socket
import sqlite3
import ssl
import sys
import tempfile
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import ad_import  # noqa: E402
import ad_ldap_sync as ads  # noqa: E402
import api  # noqa: E402
import auth  # noqa: E402
import credential_store  # noqa: E402
import db  # noqa: E402
import integration_http as ih  # noqa: E402
import quiet_window as qw  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# Q91：這個檔案測「誰能做什麼」，不套 conftest 的「測試帳號預設 admin」相容層。
ROLE_AWARE_TESTS = True

PW = "Ldap-Test-Pw-7788"
CRED = "ad-svc"
BASE = "DC=corp,DC=example,DC=test"
HOSTS = "10.99.0.1 dc01.corp.example.test\n10.99.0.2 dc02.corp.example.test"
MGR_DN = "CN=主管甲,OU=cs7110000,OU=cs7000000,OU=Corp,DC=corp,DC=example,DC=test"
UTC = _dt.timezone.utc


def person(i, *, uac=512, emp=True, dept="資訊處系統部(7110000)", mgr=MGR_DN, name=None):
    a = {"sAMAccountName": [f"0100{i:04d}"], "displayName": [name or f"測試員{i}"],
         "userPrincipalName": [f"u{i}@corp.example.test"], "mail": [f"u{i}@corp.example.test"],
         "department": [dept], "manager": [mgr] if mgr else []}
    if uac is not None:
        a["userAccountControl"] = [uac]
    if emp:
        a["employeeID"] = [f"0100{i:04d}"]
    return a


def boss():
    return person(9999, name="主管甲", mgr="")


class FakeConn:
    """假的「已開、未 bind」ldap3 連線。bind：'ok'／'49:<data>'／'raise'／'rc:<n>'。
    page_mode：{頁: 'fail'｜'nocontrol'｜'strcookie'｜'timeout'}。"""

    def __init__(self, pages, bind="ok", page_mode=None):
        self.pages, self.bind_mode, self.page_mode = pages, bind, page_mode or {}
        self.result = None
        self.response = None
        self.bound = False
        self.unbound = False
        self.searches = []
        self.strategy = self

    def close(self):           # _hard_close 走 strategy.close()
        pass

    def bind(self):
        m = self.bind_mode
        if m == "raise":
            raise TimeoutError(f"socket timeout while binding {PW}")
        if m.startswith("49:"):
            self.result = {"result": 49, "description": "invalidCredentials",
                           "message": f"80090308: LdapErr: DSID-0C09044E, comment: AcceptSecurityContext error, data {m[3:]}, v4563 {PW}"}
            return False
        if m.startswith("rc:"):
            self.result = {"result": int(m[3:]), "description": "x", "message": PW}
            return False
        self.bound = True
        return True

    def search(self, base, flt, scope, attributes=None, paged_size=None, paged_criticality=False, paged_cookie=None):
        assert self.bound, "沒 bind 就查詢"
        self.searches.append({"base": base, "filter": flt, "attrs": attributes, "size": paged_size,
                              "crit": paged_criticality, "cookie": paged_cookie})
        idx = 0 if not paged_cookie else int(paged_cookie.decode())
        mode = self.page_mode.get(idx)
        if mode == "timeout":
            raise TimeoutError(f"receive timeout {PW}")
        if mode == "fail":
            self.result, self.response = {"result": 1, "description": "operationsError"}, []
            return False
        self.response = [{"type": "searchResEntry", "dn": f"CN=x{n}", "attributes": a} for n, a in enumerate(self.pages[idx])]
        self.response.append({"type": "searchResRef", "uri": ["ldaps://ForestDnsZones.corp.example.test/DC=x"]})
        nxt = idx + 1 if idx + 1 < len(self.pages) else None
        cookie = str(nxt).encode() if nxt else b""
        self.result = {"result": 0, "description": "success",
                       "controls": {ads.PAGED_OID: {"value": {"size": 0, "cookie": cookie}}}}
        if mode == "nocontrol":
            self.result = {"result": 0, "description": "success", "controls": {}}
        if mode == "strcookie":
            self.result["controls"][ads.PAGED_OID]["value"]["cookie"] = cookie.decode()
        return True

    def unbind(self):
        self.unbound = True


class World:
    """CONNECT 的替身：behaviour[host] ＝ 'ok'／'down'／'tls'／bind 模式；page_modes[host]＝分頁模式。"""

    def __init__(self, pages, behaviour=None, page_modes=None):
        self.pages = pages
        self.behaviour = behaviour or {}
        self.page_modes = page_modes or {}
        self.calls = []
        self.conns = []

    def __call__(self, host, user, password, ca):
        self.calls.append((host["host"], user, password, host.get("cert_name"), ca))
        b = self.behaviour.get(host["host"], "ok")
        if b in ("down", "tls", "cert_name"):
            raise ads.Unreachable({"down": "connect"}.get(b, b), host["host"])
        c = FakeConn(self.pages, b, self.page_modes.get(host["host"]))
        self.conns.append(c)
        return c

    @property
    def binds(self):
        return sum(1 for c in self.conns if c.bind_mode)


@pytest.fixture
def ca_file(tmp_path):
    p = tmp_path / "corp-root-ca.pem"
    p.write_text("-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n", encoding="ascii")
    return str(p)


def _base_setup(c, ca, cred_user="webit3sc@corp.example.test", scope=None):
    c.execute("UPDATE feature_flags SET enabled=1 WHERE module_key='ad_ldap'")
    c.commit()
    credential_store.save(c, CRED, "ldap", cred_user, PW, scope=scope, key_path=ads.KEY_PATH)
    ads.save_config(c, hosts=HOSTS, base_dn=BASE, ca_bundle=ca, enabled=False, run_at="07:00", credential=CRED, actor="admin")


@pytest.fixture
def env(tmp_path, monkeypatch, ca_file):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    monkeypatch.setattr(ads, "KEY_PATH", str(tmp_path / "cred.key"))
    _base_setup(c, ca_file)
    yield c
    c.close()


def use(monkeypatch, world):
    monkeypatch.setattr(ads, "CONNECT", world)
    return world


def count(c):
    return ad_import.current_total(c)


def batches(c):
    return c.execute("SELECT COUNT(*) FROM ad_batch").fetchone()[0]


# ======================================================================== 對映
def test_對映_停用_缺employeeID_主管DN_UAC缺或壞是未知不是啟用():
    assert ads.map_entry(person(1)) == ["01000001", "01000001", "測試員1", "u1@corp.example.test", "u1@corp.example.test",
                                        "資訊處系統部(7110000)", MGR_DN, "True"]
    assert ads.map_entry(person(2, uac=514))[7] == "False"
    assert ads.map_entry(person(3, uac=66050))[7] == "False"
    no_emp = ads.map_entry(person(4, emp=False))
    assert no_emp[0] == "01000004" and no_emp[1] == ""
    assert ads.map_entry(person(5, uac=None))[7] == ""                     # 缺值＝未知
    assert ads.map_entry(person(6, uac="abc"))[7] == ""                    # 解不開＝未知
    assert ads.map_entry({"displayName": ["沒有帳號"]}) is None
    assert ads.map_entry({"SAMACCOUNTNAME": b"01000006", "useraccountcontrol": "2"})[0:1] == ["01000006"]


def test_同步寫入_與CSV路徑同一份解析_停用與主管對上(env, monkeypatch):
    use(monkeypatch, World([[person(1), person(2, uac=514), person(3, emp=False), boss()]]))
    out = ads.run_sync(env, "manual", "admin")
    assert out["status"] == "ok", out
    rows = {r["employee_id"]: dict(r) for r in env.execute("SELECT * FROM ad_person")}
    assert set(rows) == {"01000001", "01000002", "01000003", "01009999"}
    assert rows["01000002"]["enabled"] == 0 and rows["01000001"]["enabled"] == 1
    assert rows["01000001"]["manager_state"] == ad_import.MGR_OK and rows["01000001"]["manager_id"] == "01009999"
    b = env.execute("SELECT * FROM ad_batch ORDER BY id DESC").fetchone()
    assert b["imported_by"] == "ad-sync:manual" and b["file_name"] == "LDAPS 10.99.0.1"


def test_UAC未知超過2趴_整批拒收(env, monkeypatch):
    ppl = [person(i) for i in range(1, 99)] + [person(200, uac=None), person(201, uac="x"), person(202, uac=None)]
    use(monkeypatch, World([ppl]))
    out = ads.run_sync(env, "manual", "admin")
    assert out["status"] == "refused" and "userAccountControl" in out["message"] and count(env) == 0
    use(monkeypatch, World([[person(i) for i in range(1, 100)] + [person(300, uac=None)]]))     # 1% 可以，未知照實存 NULL
    assert ads.run_sync(env, "manual", "admin")["status"] == "ok"
    assert env.execute("SELECT enabled FROM ad_person WHERE employee_id='01000300'").fetchone()[0] is None


# ======================================================================== 分頁完整性
def test_分頁讀完整份_要求criticality_參照不跟_屬性只要這幾個(env, monkeypatch):
    pages = [[person(i) for i in range(1, 4)], [person(i) for i in range(4, 7)], [person(7)]]
    w = use(monkeypatch, World(pages))
    assert ads.run_sync(env, "manual", "admin")["status"] == "ok"
    assert count(env) == 7
    s = w.conns[0].searches
    assert len(s) == 3 and s[0]["cookie"] is None and s[0]["size"] == 1000 and all(x["crit"] is True for x in s)
    assert s[0]["filter"] == "(&(objectCategory=person)(objectClass=user))" and s[0]["base"] == BASE
    assert "telephoneNumber" not in s[0]["attrs"] and "userPassword" not in s[0]["attrs"]
    assert w.conns[0].unbound


def _seed(env, monkeypatch, n=60):
    use(monkeypatch, World([[person(i) for i in range(1, n + 1)]]))
    assert ads.run_sync(env, "manual", "admin")["status"] == "ok"
    return count(env), batches(env)


@pytest.mark.parametrize("modes", [{0: "nocontrol"}, {1: "nocontrol"}, {1: "strcookie"}, {1: "fail"}])
def test_分頁控制項缺或型別錯或某頁失敗_整批失敗_名單與批次都沒變(env, monkeypatch, modes):
    before = _seed(env, monkeypatch)
    pages = [[person(i) for i in range(1, 30)], [person(i) for i in range(30, 60)], [person(61)]]
    use(monkeypatch, World(pages, page_modes={"10.99.0.1": modes, "10.99.0.2": modes}))
    out = ads.run_sync(env, "manual", "admin")
    assert out["status"] == "failed" and "名單沒有變動" in out["message"]
    assert (count(env), batches(env)) == before


def test_第二頁逾時_換另一台從第一頁重抓(env, monkeypatch):
    pages = [[person(i) for i in range(1, 30)], [person(i) for i in range(30, 61)]]
    w = use(monkeypatch, World(pages, page_modes={"10.99.0.1": {1: "timeout"}}))
    out = ads.run_sync(env, "manual", "admin")
    assert out["status"] == "ok" and count(env) == 60
    assert w.conns[1].searches[0]["cookie"] is None                         # 第二台從第一頁開始


def test_兩台都在第二頁逾時_失敗_名單與批次都沒變_訊息沒有原始例外(env, monkeypatch):
    before = _seed(env, monkeypatch)
    pages = [[person(i) for i in range(1, 30)], [person(i) for i in range(30, 61)]]
    use(monkeypatch, World(pages, page_modes={"10.99.0.1": {1: "timeout"}, "10.99.0.2": {1: "timeout"}}))
    out = ads.run_sync(env, "manual", "admin")
    assert out["status"] == "failed" and (count(env), batches(env)) == before
    blob = json.dumps(ads.list_runs(env), ensure_ascii=False)
    assert PW not in blob and "receive timeout" not in blob and "查詢途中連線中斷或逾時" in blob


def test_整次超過時間上限_中止不寫入(env, monkeypatch):
    before = _seed(env, monkeypatch)
    monkeypatch.setattr(ads, "MAX_RUN_S", -1)
    use(monkeypatch, World([[person(1)]]))
    out = ads.run_sync(env, "manual", "admin")
    assert out["status"] == "failed" and "10 分鐘" in out["message"] and (count(env), batches(env)) == before


# ======================================================================== 換台規則
def test_第一台連不上_換第二台(env, monkeypatch):
    w = use(monkeypatch, World([[person(1)]], {"10.99.0.1": "down"}))
    out = ads.run_sync(env, "manual", "admin")
    assert out["status"] == "ok" and "前面 1 台" in out["message"]
    assert [c[0] for c in w.calls] == ["10.99.0.1", "10.99.0.2"] and w.calls[1][3] == "dc02.corp.example.test"


@pytest.mark.parametrize("mode,code", [("49:52e", "bind_invalid"), ("raise", "bind_uncertain"), ("rc:53", "bind_refused"),
                                       ("49:775", "bind_locked")])
def test_bind送出後的任何失敗_不換下一台(env, monkeypatch, mode, code):
    w = use(monkeypatch, World([[person(1)]], {"10.99.0.1": mode}))
    out = ads.run_sync(env, "manual", "admin")
    assert out["status"] == "failed" and out["code"] == code and len(w.calls) == 1
    blob = json.dumps(ads.list_runs(env), ensure_ascii=False) + out["message"]
    assert PW not in blob and "LdapErr" not in blob and "socket timeout" not in blob


# ======================================================================== 鎖帳節流（持久化、共用額度）
def test_帳號正規化_三種寫法同一人():
    assert {ads.account_key(u) for u in ("CORP\\WebIT3SC", "webit3sc@corp.example.test",
                                         "CN=webit3sc,OU=Svc,DC=corp,DC=example,DC=test", "WEBIT3SC@CORP.EXAMPLE.TEST")} == {"webit3sc"}


def test_被拒三次先停_同步與測試共用額度_跨重開機仍在_改密碼才解除(env, monkeypatch):
    w = use(monkeypatch, World([[person(1)]], {"10.99.0.1": "49:52e"}))
    ads.run_sync(env, "manual", "admin")
    ads.check_connection(env)
    ads.check_connection(env, CRED)                                         # 收集憑證頁的測試入口
    assert len(w.calls) == 3
    row = env.execute("SELECT fails FROM ad_ldap_lockout WHERE account='webit3sc'").fetchone()
    assert row[0] == 3                                                      # 存在 DB：服務重啟也還在
    out = ads.check_connection(env)
    assert out["ok"] is False and "暫停使用" in out["message"] and len(w.calls) == 3
    assert ads.run_sync(env, "schedule", None)["status"] == "failed" and len(w.calls) == 3
    assert ads.freshness(env)["label"] == "失敗" and ads.freshness(env)["blocked"]
    # 帳號換另一種寫法（同一個人）也一樣被擋
    env.execute("UPDATE collect_credential SET username='CORP\\webit3sc' WHERE name=?", (CRED,))
    env.commit()
    assert ads.check_connection(env)["ok"] is False and len(w.calls) == 3
    credential_store.update(env, CRED, "webit3sc@corp.example.test", None, None, "new-pw", key_path=ads.KEY_PATH)
    env.execute("UPDATE collect_credential SET updated_at='2099-01-01 00:00:00' WHERE name=?", (CRED,))
    env.commit()
    w.behaviour = {}
    assert ads.run_sync(env, "manual", "admin")["status"] == "ok"


def test_帳號鎖定_一次就停到改密碼_排程不再自動重試(env, monkeypatch):
    w = use(monkeypatch, World([[person(1)]], {"10.99.0.1": "49:775"}))
    db.set_setting(env, ads.K_ENABLED, "1")
    ads.run_sync(env, "manual", "admin")
    assert ads.tick(env)["status"] == "blocked" and len(w.calls) == 1
    f = ads.freshness(env)
    assert f["label"] == "失敗" and "帳號已鎖定" in f["blocked"]


def test_持久化額度_原子遞增(env):
    ads._quota_hit(env, CRED, "webit3sc@corp.example.test", "bind_invalid")
    ads._quota_hit(env, CRED, "CORP\\webit3sc", "bind_invalid")
    assert env.execute("SELECT fails FROM ad_ldap_lockout").fetchone()[0] == 2


# ======================================================================== 少兩成防呆（有效人數、DB 錯不放行）
def test_少超過兩成_整批不收_名單不動(env, monkeypatch):
    use(monkeypatch, World([[person(i) for i in range(1, 101)]]))
    assert ads.run_sync(env, "manual", "admin")["status"] == "ok"
    use(monkeypatch, World([[person(i) for i in range(1, 71)]]))
    out = ads.run_sync(env, "manual", "admin")
    assert out["status"] == "refused" and "少超過兩成" in out["message"] and count(env) == 100


def test_防呆用去重後的有效人數(env):
    hdr = ads.COLUMNS
    ad_import.import_rows(env, [hdr] + [ads.map_entry(person(i)) for i in range(1, 101)], guard_shrink=True)
    dup = [hdr] + [ads.map_entry(person(i)) for i in range(1, 61)] * 2         # 120 列、只有 60 人
    with pytest.raises(ad_import.ShrinkRefused):
        ad_import.import_rows(env, dup, guard_shrink=True)
    assert count(env) == 100
    out = ad_import.import_rows(env, dup)                                     # 人工上傳：照縮，重複只留一筆
    assert count(env) == 60 and out["duplicates"] == 60


def test_防呆的人數查詢出錯_不可當0人放行(monkeypatch):
    class Locked:
        def execute(self, sql, *a):
            if "COUNT(*) FROM ad_person" in sql:
                raise sqlite3.OperationalError("database is locked")
            raise AssertionError("不該走到這裡")
    with pytest.raises(sqlite3.OperationalError):
        ad_import.shrink_message(Locked(), 10)
    with pytest.raises(sqlite3.OperationalError):
        ad_import.current_total(Locked())


def test_名單與成功紀錄同一交易_紀錄寫不進去名單也回滾(env, monkeypatch):
    before = _seed(env, monkeypatch)
    use(monkeypatch, World([[person(i) for i in range(1, 70)]]))
    real = ads._finish_run

    def boom(conn, run_id, status, *a, **k):
        if status == "ok":
            raise RuntimeError(f"disk full {PW}")
        return real(conn, run_id, status, *a, **k)
    monkeypatch.setattr(ads, "_finish_run", boom)
    out = ads.run_sync(env, "manual", "admin")
    assert out["status"] == "failed" and PW not in out["message"]
    assert (count(env), batches(env)) == before


# ======================================================================== 只收 LDAPS、CA 必填、IP 要憑證名
@pytest.mark.parametrize("bad", ["ldap://10.99.0.1", "10.99.0.1:389 dc01.corp.example.test", "dc01.corp.example.test:3268",
                                 "10.99.0.1 *.corp.example.test", "10.99.0.1 *", "dc_01.corp", "a b c", "10.99.0.1",
                                 "ldaps://", ":636", "ldaps://:636", "10.99.0.1 10.99.0.9", "http://dc01.corp.example.test"])
def test_設定只收LDAPS636_IP要寫憑證名(env, bad, ca_file):
    with pytest.raises(ih.ConfigError):
        ads.save_config(env, hosts=bad, base_dn=BASE, ca_bundle=ca_file, enabled=False, run_at="07:00")


def test_設定_DN解析_CA必填_連接埠不能改_時間不能在盤中(env, ca_file):
    assert ads.parse_hosts("ldaps://dc01.corp.example.test:636\n10.99.0.2 dc02.corp.example.test") == [
        {"host": "dc01.corp.example.test", "cert_name": ""}, {"host": "10.99.0.2", "cert_name": "dc02.corp.example.test"}]
    kw = dict(hosts=HOSTS, base_dn=BASE, ca_bundle=ca_file, enabled=False, run_at="07:00")
    for bad in ({"port": 389}, {"port": "636"}, {"base_dn": "corp.example.test"}, {"base_dn": "DC=corp,,DC=x"},
                {"base_dn": "OU=only"}, {"ca_bundle": ""}, {"ca_bundle": "/no/such/ca.pem"}, {"run_at": "10:00"},
                {"enabled": "yes"}, {"hosts": 5}):
        with pytest.raises(ih.ConfigError):
            ads.save_config(env, **{**kw, **bad})
    ads.save_config(env, **{**kw, "base_dn": "dc=corp,dc=example,dc=test"})
    assert ads.get_config(env)["base_dn"] == "dc=corp,dc=example,dc=test" and ads.get_config(env)["port"] == 636


def test_沒有關閉驗證的選項_TLS一定CERT_REQUIRED_CA必填(ca_file):
    import inspect
    for fn in (ads.save_config, ads.build_tls, ads.default_connect):
        assert not [p for p in inspect.signature(fn).parameters if "verify" in p or "insecure" in p or "skip" in p], fn
    t = ads.build_tls(ca_file, "dc01.corp.example.test")
    assert t.validate == ssl.CERT_REQUIRED and t.valid_names == ["dc01.corp.example.test"] and t.ca_certs_file == ca_file
    with pytest.raises(ih.ConfigError):
        ads.build_tls("", "dc01.corp.example.test")


def test_憑證名稱比對_不接受IP替代_萬用字元只一層():
    cert = {"subject": ((("commonName", "dc01.corp.example.test"),),),
            "subjectAltName": (("DNS", "dc01.corp.example.test"), ("IP Address", "10.99.0.1"))}
    assert ads.cert_name_ok(cert, "dc01.corp.example.test")
    assert not ads.cert_name_ok(cert, "dc99.corp.example.test")
    assert not ads.cert_name_ok(cert, "10.99.0.1")
    wild = {"subjectAltName": (("DNS", "*.corp.example.test"),)}
    assert ads.cert_name_ok(wild, "dc01.corp.example.test") and not ads.cert_name_ok(wild, "a.b.corp.example.test")
    assert not ads.cert_name_ok(None, "dc01.corp.example.test")


def test_帳號寫法_光帳號名與空密碼不送(env, monkeypatch):
    for u in ("CN=webit3sc,OU=Svc,DC=corp,DC=example,DC=test", "webit3sc@corp.example.test", "CORP\\webit3sc"):
        assert ads.check_bind_user(u) == u
    with pytest.raises(ih.ConfigError):
        ads.check_bind_user("webit3sc")
    w = use(monkeypatch, World([[person(1)]]))
    credential_store.update(env, CRED, "webit3sc", None, None, "x", key_path=ads.KEY_PATH)
    assert ads.check_connection(env)["ok"] is False and not w.calls
    env.execute("UPDATE collect_credential SET secret_enc=?, username='webit3sc@corp.example.test' WHERE name=?",
                (credential_store._fernet(ads.KEY_PATH).encrypt(b"").decode(), CRED))
    env.commit()
    assert ads.check_connection(env)["ok"] is False and not w.calls


def test_憑證適用範圍_DC不在範圍內拒用(env, monkeypatch, ca_file):
    env.execute("UPDATE collect_credential SET scope='10.99.5.' WHERE name=?", (CRED,))
    env.commit()
    w = use(monkeypatch, World([[person(1)]]))
    out = ads.check_connection(env)
    assert out["ok"] is False and "適用範圍" in out["message"] and not w.calls
    with pytest.raises(ih.ConfigError):
        ads.save_config(env, hosts=HOSTS, base_dn=BASE, ca_bundle=ca_file, enabled=False, run_at="07:00")
    env.execute("UPDATE collect_credential SET scope='10.99.0.0/24' WHERE name=?", (CRED,))
    env.commit()
    assert ads.check_connection(env)["ok"] is True


# ======================================================================== 實測 TLS（不可 skip：發布前必跑）
def _make_cert(tmp: Path, name: str, ips=("127.0.0.1",), tag="srv"):
    import ipaddress as _ip

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subj = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = _dt.datetime.now(UTC)
    san = [x509.DNSName(name)] + [x509.IPAddress(_ip.ip_address(i)) for i in ips]
    cert = (x509.CertificateBuilder().subject_name(subj).issuer_name(subj).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - _dt.timedelta(days=1))
            .not_valid_after(now + _dt.timedelta(days=2))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    (tmp / f"{tag}.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tmp / f"{tag}.key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                       serialization.NoEncryption()))
    return tmp / f"{tag}.pem", tmp / f"{tag}.key"


@pytest.fixture
def tls_server(tmp_path, monkeypatch):
    """本機 TLS 伺服器（不會 LDAP）：記下每條連線握手後收到幾個位元組——有收到＝客戶端送了 bind。"""
    import ldap3  # noqa: F401 - 不准 skip：缺 ldap3 這裡直接紅
    cert, key = _make_cert(tmp_path, "dc01.corp.example.test")
    other, _k = _make_cert(tmp_path, "dc01.corp.example.test", tag="other")      # 同名、不同 CA
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert), str(key))
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    port = srv.getsockname()[1]
    got: list[int] = []
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                s, _ = srv.accept()
            except OSError:
                return
            try:
                with ctx.wrap_socket(s, server_side=True) as t:
                    t.settimeout(1.5)
                    try:
                        got.append(len(t.recv(4096)))
                    except (OSError, ssl.SSLError):
                        got.append(0)
            except (ssl.SSLError, OSError):
                got.append(-1)                  # 握手沒成功
    th = threading.Thread(target=loop, daemon=True)
    th.start()
    monkeypatch.setattr(ads, "PORT", port)
    monkeypatch.setattr(ads, "CONNECT_TIMEOUT", 5)
    monkeypatch.setattr(ads, "RECEIVE_TIMEOUT", 3)
    yield {"ca": str(cert), "other_ca": str(other), "got": got}
    stop.set()
    srv.close()


def _wait_conns(got, n):
    import time
    for _ in range(50):
        if len(got) >= n:
            return
        time.sleep(0.05)


def test_實測TLS_憑證名對得上IP但名稱不符_伺服器收不到bind(tls_server):
    got = tls_server["got"]
    with pytest.raises(ads.Unreachable) as e:
        ads.default_connect({"host": "127.0.0.1", "cert_name": "dc99.corp.example.test"}, "webit3sc@corp.example.test", PW,
                            tls_server["ca"])
    assert e.value.code == "cert_name" and PW not in str(e.value)
    _wait_conns(got, 1)
    assert got and all(n <= 0 for n in got), got                                   # 沒有任何 bind 位元組


def test_實測TLS_CA不對或沒給_一律不送帳密(tls_server):
    with pytest.raises(ads.Unreachable) as e:
        ads.default_connect({"host": "127.0.0.1", "cert_name": "dc01.corp.example.test"}, "u@corp.example.test", PW,
                            tls_server["other_ca"])
    assert e.value.code in ("tls", "connect")
    with pytest.raises(ih.ConfigError):
        ads.default_connect({"host": "127.0.0.1", "cert_name": "dc01.corp.example.test"}, "u@corp.example.test", PW, "")
    with pytest.raises(ih.ConfigError):
        ads.default_connect({"host": "127.0.0.1", "cert_name": ""}, "u@corp.example.test", PW, tls_server["ca"])
    _wait_conns(tls_server["got"], 1)
    assert all(n <= 0 for n in tls_server["got"])


def test_實測TLS_CA與憑證名都對_才送bind_結果不明就停(tls_server):
    c = ads.default_connect({"host": "127.0.0.1", "cert_name": "dc01.corp.example.test"}, "u@corp.example.test", PW,
                            tls_server["ca"])
    with pytest.raises(ads.BindStop) as e:
        ads._bind(c, "127.0.0.1")
    assert e.value.code in ("bind_uncertain", "bind_refused") and PW not in str(e.value)
    _wait_conns(tls_server["got"], 1)
    assert any(n > 0 for n in tls_server["got"])                                   # 伺服器確實收到 bind


# ======================================================================== 連線測試
def test_連線測試只抽查一頁_不寫名單也不留執行紀錄(env, monkeypatch):
    w = use(monkeypatch, World([[person(1), person(2, uac=514)], [person(3)]]))
    out = ads.check_connection(env)
    assert out["ok"] is True and out["count"] == 2 and "warning" not in out
    assert len(w.conns[0].searches) == 1 and w.conns[0].searches[0]["size"] == ads.TEST_LIMIT
    assert count(env) == 0 and ads.list_runs(env) == []


def test_連線測試讀到0筆要警告(env, monkeypatch):
    use(monkeypatch, World([[]]))
    out = ads.check_connection(env)
    assert out["ok"] is True and "Base DN" in out["warning"]


def test_執行紀錄不含個資(env, monkeypatch):
    use(monkeypatch, World([[person(1, name="王小明"), boss()]]))
    ads.run_sync(env, "manual", "admin")
    blob = json.dumps(ads.list_runs(env), ensure_ascii=False)
    assert "王小明" not in blob and "u1@corp.example.test" not in blob and MGR_DN not in blob


def test_內部錯誤_不外洩密碼_log只有類別名(env, monkeypatch, caplog):
    def bad(*a, **k):
        raise RuntimeError(f"boom {PW}")
    monkeypatch.setattr(ads, "CONNECT", bad)
    with caplog.at_level(logging.DEBUG):
        out = ads.run_sync(env, "manual", "admin")
        t = ads.check_connection(env)
    assert out["status"] == "failed" and t["ok"] is False
    assert PW not in json.dumps([out, t, ads.list_runs(env)], ensure_ascii=False) and PW not in caplog.text


# ======================================================================== 排程、補跑、時區、running 回收
def _taipei(y, m, d, hh, mm=0):
    """回 UTC aware：測主機時區是 UTC 時，仍照台北牆上時間算。"""
    return _dt.datetime(y, m, d, hh, mm, tzinfo=_dt.timezone(_dt.timedelta(hours=8))).astimezone(UTC)


def test_排程_台北時間07點觸發_主機UTC也一樣_失敗隔60分最多3次_午夜換日(env, monkeypatch):
    w = use(monkeypatch, World([[person(1)]], {"10.99.0.1": "down", "10.99.0.2": "down"}))
    db.set_setting(env, ads.K_ENABLED, "1")
    t = [_taipei(2026, 11, 7, 6, 59)]
    monkeypatch.setattr(qw, "_clock", lambda: t[0])
    assert ads.tick(env)["status"] == "not_due"                       # UTC 22:59 前一天＝台北 06:59
    t[0] = _taipei(2026, 11, 7, 7, 0)
    assert ads.tick(env)["status"] == "failed"
    t[0] = _taipei(2026, 11, 7, 7, 30)
    assert ads.tick(env)["status"] == "retry_later"
    t[0] = _taipei(2026, 11, 7, 8, 1)
    assert ads.tick(env)["status"] == "failed"
    t[0] = _taipei(2026, 11, 7, 9, 2)
    assert ads.tick(env)["status"] == "failed"
    t[0] = _taipei(2026, 11, 7, 23, 59)
    assert ads.tick(env)["status"] == "gave_up_today"
    t[0] = _taipei(2026, 11, 8, 0, 1)                                 # 台北午夜換日（UTC 還是 11/7 16:01）
    assert ads.tick(env)["status"] == "not_due"
    t[0] = _taipei(2026, 11, 8, 7, 0)
    w.behaviour = {}
    assert ads.tick(env)["status"] == "ok"
    assert ads.list_runs(env, 1)[0]["started_at"] == "2026-11-08 07:00:00"
    t[0] = _taipei(2026, 11, 8, 9, 0)
    assert ads.tick(env)["status"] == "done_today"


def test_排程_模組或開關關著不跑(env, monkeypatch):
    w = use(monkeypatch, World([[person(1)]]))
    assert ads.tick(env)["status"] == "disabled"
    db.set_setting(env, ads.K_ENABLED, "1")
    env.execute("UPDATE feature_flags SET enabled=0 WHERE module_key='ad_ldap'")
    env.commit()
    assert ads.tick(env)["status"] == "disabled" and not w.calls


def test_補跑走同一個判斷_只有成功才算成功(env, monkeypatch):
    import scan_service
    w = use(monkeypatch, World([[person(1)]]))
    assert ads.catchup(env)["status"] == "not_needed" and not w.calls     # 開關關著：不連線
    db.set_setting(env, ads.K_ENABLED, "1")
    assert scan_service._catchup_result(ads.catchup(env)) == (True, "")
    assert ads.list_runs(env, 1)[0]["status"] == "ok"
    assert ads.catchup(env)["status"] == "not_needed" and len(w.calls) == 1   # 今天已成功：不再跑
    env.execute("DELETE FROM ad_ldap_runs")
    env.commit()
    w.behaviour = {"10.99.0.1": "down", "10.99.0.2": "down"}
    ok, _d = scan_service._catchup_result(ads.catchup(env))
    assert ok is False                                                     # 失敗扣重試
    env.execute("DELETE FROM ad_ldap_runs")
    env.commit()
    ads._run_lock.acquire()
    try:
        assert scan_service._catchup_result(ads.catchup(env)) == (True, "refused")   # busy：回 pending 不扣
    finally:
        ads._run_lock.release()


def test_盤中靜默_bind前與分頁中都會停_記延後不算失敗(env, monkeypatch):
    w = use(monkeypatch, World([[person(i) for i in range(1, 5)], [person(9)]]))
    calls = {"n": 0}
    real_gate = qw.network_gate

    def gate():
        calls["n"] += 1
        if calls["n"] >= 2:                     # 第一次（連線前）放行，第二次（bind 前）擋
            raise qw.QuietWindowBlocked("x")
        real_gate()
    monkeypatch.setattr(qw, "network_gate", gate)
    out = ads.run_sync(env, "schedule", None)
    assert out["status"] == "deferred" and w.conns and not w.conns[0].bound
    calls["n"] = -1                             # 讓連線、bind、第一頁過，第二頁擋
    out = ads.run_sync(env, "schedule", None)
    assert out["status"] == "deferred" and count(env) == 0
    assert all(r["status"] == "deferred" for r in ads.list_runs(env))
    assert ads.freshness(env)["label"] == "沒收到"
    assert "ad_ldap_tick" in [p["entry"] for p in qw.pending_list(env)]


def test_running卡住超過上限_會回收不擋今天排程(env, monkeypatch):
    use(monkeypatch, World([[person(1)]]))
    db.set_setting(env, ads.K_ENABLED, "1")
    env.execute("INSERT INTO ad_ldap_runs (started_at, trigger, status) VALUES ('2026-11-07 07:00:00','schedule','running')")
    env.commit()
    monkeypatch.setattr(qw, "_clock", lambda: _dt.datetime(2026, 11, 7, 7, 20))
    assert ads.tick(env)["status"] == "done_today"                       # 20 分鐘內：當作還在跑
    monkeypatch.setattr(qw, "_clock", lambda: _dt.datetime(2026, 11, 7, 7, 31))
    assert ads.tick(env)["status"] == "retry_later"                      # 回收成失敗＝算一次排程嘗試，照重試間隔
    assert ads.list_runs(env)[0]["status"] == "failed"
    monkeypatch.setattr(qw, "_clock", lambda: _dt.datetime(2026, 11, 7, 8, 1))
    assert ads.tick(env)["status"] == "ok"
    assert [r["status"] for r in ads.list_runs(env)] == ["ok", "failed"]


def test_新鮮度_待設定_沒收到_正常_失敗_過期(env, monkeypatch, ca_file):
    assert ads.freshness(env)["label"] == "沒收到"
    w = use(monkeypatch, World([[person(1)]]))
    t = [_dt.datetime(2026, 11, 7, 10, 0)]
    monkeypatch.setattr(qw, "_clock", lambda: t[0])
    ads.run_sync(env, "manual", "admin")
    assert ads.freshness(env)["label"] == "正常"
    w.behaviour = {"10.99.0.1": "down", "10.99.0.2": "down"}
    ads.run_sync(env, "manual", "admin")
    assert ads.freshness(env)["label"] == "失敗"
    t[0] = _dt.datetime(2026, 11, 9, 10, 1)
    f = ads.freshness(env)
    assert f["label"] == "過期" and f["age_hours"] >= 48
    ads.save_config(env, hosts="", base_dn=BASE, ca_bundle=ca_file, enabled=False, run_at="07:00")
    assert ads.freshness(env)["label"] == "待設定"


def test_啟用前要有DC_BaseDN_憑證_不自動挑憑證(env, ca_file):
    kw = dict(hosts=HOSTS, base_dn=BASE, ca_bundle=ca_file, run_at="07:00")
    with pytest.raises(ih.ConfigError):
        ads.save_config(env, **{**kw, "hosts": ""}, enabled=True)
    with pytest.raises(ih.ConfigError):
        ads.save_config(env, **{**kw, "base_dn": ""}, enabled=True)
    with pytest.raises(ih.ConfigError):
        ads.save_config(env, **kw, enabled=True, credential="")
    with pytest.raises(ih.ConfigError):
        ads.save_config(env, **kw, enabled=False, credential="no-such")
    db.set_setting(env, ads.K_CRED, "")
    assert ads.credential_name(env) is None                              # 只有一筆也不自動挑
    ads.save_config(env, **kw, enabled=True, credential=CRED)
    assert ads.get_config(env)["enabled"] is True


def test_設定變更有稽核_改前改後無密碼(env, ca_file):
    ads.save_config(env, hosts="10.99.0.3 dc03.corp.example.test", base_dn="DC=corp2,DC=example,DC=test", ca_bundle=ca_file,
                    enabled=False, run_at="06:30", actor="alice")
    a = ads.config_audit(env, 1)[0]
    assert a["username"] == "alice" and a["before"]["base_dn"] == BASE and a["after"]["base_dn"] == "DC=corp2,DC=example,DC=test"
    assert a["after"]["hosts"] == [{"host": "10.99.0.3", "cert_name": "dc03.corp.example.test"}]
    act = env.execute("SELECT username, action FROM activity_log ORDER BY id DESC LIMIT 1").fetchone()
    assert act["username"] == "alice" and "dc03.corp.example.test" in act["action"] and "Base DN" in act["action"]
    assert PW not in json.dumps(ads.config_audit(env), ensure_ascii=False)


def test_憑證被整合引用_刪除前看得到(env):
    import credential_refs
    refs = credential_refs.refs_of(env, CRED)
    assert refs["block_count"] == 1 and refs["refs"][0]["link"].endswith("sub=ad")


# ======================================================================== 單一來源
def test_單一來源_模組開著時中繼名單拒收_手動匯入有警告(env):
    import relay_import as ri
    ad = ri.AdUsersAdapter()
    parsed = {"users.json": {"columns": ads.COLUMNS, "rows": [ads.map_entry(person(1))]}}
    with pytest.raises(ri.RelayReject) as e:
        ad.apply(env, type("Ctx", (), {"batch_id": "b1"})(), parsed, "now")
    assert e.value.code == "superseded"
    s = ad_import.summary(env)
    assert s["auto_sync_warning"] and s["batch"] is None
    ad_import.import_rows(env, [ads.COLUMNS, ads.map_entry(person(1))], "x.csv", "admin")
    assert ad_import.AUTO_SYNC_WARNING in ad_import.summary(env)["batch"]["note"]
    env.execute("UPDATE feature_flags SET enabled=0 WHERE module_key='ad_ldap'")
    env.commit()
    assert ad_import.summary(env)["auto_sync_warning"] is None
    assert ad.apply(env, type("Ctx", (), {"batch_id": "b2"})(), parsed, "now")["rows"] == 1


# ======================================================================== 路由
def _client(tmp):
    db_path = Path(tmp) / "test.db"
    db.init_db(db_path)

    def _dep():
        c = db.get_connection(db_path)
        try:
            yield c
        finally:
            c.close()
    api.app.dependency_overrides[api.get_db] = _dep
    return TestClient(api.app, raise_server_exceptions=False), db_path


def _login(client, db_path, username):
    conn = db.get_connection(db_path)
    try:
        if not conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            db.create_user(conn, username, auth.hash_password("test-password-123"))
    finally:
        conn.close()
    assert client.post("/api/auth/login", json={"username": username, "password": "test-password-123"}).status_code == 200


CALLS = [("get", "/api/integrations/ad"), ("put", "/api/integrations/ad"), ("post", "/api/integrations/ad/test"),
         ("post", "/api/integrations/ad/run"), ("get", "/api/integrations/ad/runs")]


def test_路由_模組關閉403_非admin一律403_admin可用_不回密碼(monkeypatch, ca_file):
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        monkeypatch.setattr(ads, "KEY_PATH", str(Path(tmp) / "cred.key"))
        use(monkeypatch, World([[person(1), person(2)]]))
        try:
            assert client.get("/api/integrations/ad").status_code == 401
            _login(client, db_path, "admin")
            for m, path in CALLS:
                assert getattr(client, m)(path, **({"json": {}} if m == "put" else {})).status_code == 403, path
            c = db.get_connection(db_path)
            c.execute("UPDATE feature_flags SET enabled=1 WHERE module_key='ad_ldap'")
            c.commit()
            credential_store.save(c, CRED, "ldap", "webit3sc@corp.example.test", PW, key_path=ads.KEY_PATH)
            c.close()
            client.post("/api/auth/logout")
            _login(client, db_path, "someone")
            for m, path in CALLS:
                assert getattr(client, m)(path, **({"json": {}} if m == "put" else {})).status_code == 403, path
            client.post("/api/auth/logout")
            _login(client, db_path, "admin")
            body = {"hosts": HOSTS, "base_dn": BASE, "ca_bundle": ca_file, "enabled": False, "run_at": "07:00", "credential": CRED}
            r = client.put("/api/integrations/ad", json=body)
            assert r.status_code == 200, r.text
            v = r.json()
            assert v["has_password"] is True and v["port"] == 636 and PW not in r.text
            assert v["config_audit"][0]["username"] == "admin"
            t = client.post("/api/integrations/ad/test")
            assert t.status_code == 200 and t.json()["ok"] is True and PW not in t.text
            run = client.post("/api/integrations/ad/run")
            assert run.status_code == 200 and run.json()["status"] == "ok" and PW not in run.text
            runs = client.get("/api/integrations/ad/runs").json()["runs"]
            assert len(runs) == 1 and runs[0]["__no"] == runs[0]["id"] and runs[0]["rows"] == 2
            g = client.get("/api/integrations/ad").json()
            assert g["freshness"]["label"] == "正常" and g["list"]["total"] == 2 and PW not in json.dumps(g)
        finally:
            api.app.dependency_overrides.clear()


def test_路由_錯誤不回顯輸入_400_422_500都不含密碼(monkeypatch, ca_file, caplog):
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        monkeypatch.setattr(ads, "KEY_PATH", str(Path(tmp) / "cred.key"))
        try:
            c = db.get_connection(db_path)
            c.execute("UPDATE feature_flags SET enabled=1 WHERE module_key='ad_ldap'")
            c.commit()
            credential_store.save(c, CRED, "ldap", "webit3sc@corp.example.test", PW, key_path=ads.KEY_PATH)
            c.close()
            _login(client, db_path, "admin")
            base = {"hosts": HOSTS, "base_dn": BASE, "ca_bundle": ca_file, "enabled": False, "run_at": "07:00", "credential": CRED}
            for bad in ({**base, "password": PW}, {**base, "enabled": PW}, {**base, "port": PW}, {**base, "hosts": {"a": PW}},
                        {**base, "base_dn": PW}, {**base, "run_at": PW}, {**base, "zzz": PW}, [PW], PW):
                r = client.put("/api/integrations/ad", json=bad)
                assert r.status_code == 400 and PW not in r.text, (bad, r.status_code, r.text)
            r = client.put("/api/integrations/ad", content=b"{not json " + PW.encode(), headers={"content-type": "application/json"})
            assert r.status_code in (400, 422) and PW not in r.text
            client.put("/api/integrations/ad", json=base)

            def bad_connect(*a, **k):
                raise RuntimeError(f"kaboom {PW}")
            monkeypatch.setattr(ads, "CONNECT", bad_connect)
            monkeypatch.setattr(ads, "_read_all", lambda *a, **k: (_ for _ in ()).throw(RuntimeError(f"kaboom {PW}")))
            with caplog.at_level(logging.DEBUG):
                for path in ("/api/integrations/ad/run", "/api/integrations/ad/test"):
                    r = client.post(path)
                    assert r.status_code in (200, 500) and PW not in r.text
            runs = client.get("/api/integrations/ad/runs").text
            assert PW not in runs and PW not in caplog.text
        finally:
            api.app.dependency_overrides.clear()


def test_路由_盤中按立即同步與測試要二次確認(monkeypatch, ca_file):
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        monkeypatch.setattr(ads, "KEY_PATH", str(Path(tmp) / "cred.key"))
        w = use(monkeypatch, World([[person(1)]]))
        try:
            c = db.get_connection(db_path)
            _base_setup(c, ca_file)
            c.close()
            _login(client, db_path, "admin")
            qw.set_clock(lambda: _dt.datetime(2026, 11, 9, 10, 0))          # 週一 10:00，盤中
            r = client.post("/api/integrations/ad/run")
            assert r.status_code == 409 and r.json()["detail"]["code"] == "quiet_window_confirm" and not w.calls
            assert client.post("/api/integrations/ad/test").status_code == 409 and not w.calls
            assert client.post(f"/api/credentials/{CRED}/test", json={}).status_code == 409 and not w.calls
            r = client.post("/api/integrations/ad/run?confirm_in_window=true")
            assert r.status_code == 200 and r.json()["status"] == "ok" and w.calls
        finally:
            qw.set_clock(lambda: _dt.datetime(2026, 11, 7, 10, 0))
            api.app.dependency_overrides.clear()


def test_收集憑證頁的測試_同一套守門_模組關閉403_非admin403(monkeypatch, ca_file):
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        monkeypatch.setattr(ads, "KEY_PATH", str(Path(tmp) / "cred.key"))
        w = use(monkeypatch, World([[person(1)]]))
        try:
            c = db.get_connection(db_path)
            _base_setup(c, ca_file)
            c.execute("UPDATE feature_flags SET enabled=0 WHERE module_key='ad_ldap'")
            c.commit()
            c.close()
            _login(client, db_path, "admin")
            assert client.post(f"/api/credentials/{CRED}/test", json={}).status_code == 403 and not w.calls
            c = db.get_connection(db_path)
            c.execute("UPDATE feature_flags SET enabled=1 WHERE module_key='ad_ldap'")
            c.commit()
            c.close()
            r = client.post(f"/api/credentials/{CRED}/test", json={})
            assert r.status_code == 200 and r.json()["ok"] is True and PW not in r.text
            client.post("/api/auth/logout")
            _login(client, db_path, "someone")
            assert client.post(f"/api/credentials/{CRED}/test", json={}).status_code == 403
        finally:
            api.app.dependency_overrides.clear()


# ======================================================================== 真的 ldap3（MOCK_SYNC）：查詢、分頁控制項、轉換
def test_真ldap3_MOCK連線_分頁查詢與轉換(env, monkeypatch):
    import ldap3
    monkeypatch.setattr(ads, "PAGE_SIZE", 1)
    srv = ldap3.Server("mock-dc", get_info=ldap3.NONE)
    mc = ldap3.Connection(srv, user="CN=svc,DC=corp,DC=example,DC=test", password=PW, client_strategy=ldap3.MOCK_SYNC)
    mc.strategy.add_entry("CN=svc,DC=corp,DC=example,DC=test", {"userPassword": PW, "sAMAccountName": "svc"})
    for i, uac in ((1, 512), (2, 514), (3, None)):
        a = {"objectClass": ["top", "person", "user"], "objectCategory": "person", "sAMAccountName": f"0100000{i}",
             "employeeID": f"0100000{i}", "displayName": f"測試員{i}", "manager": MGR_DN, "department": "資訊處系統部(7110000)"}
        if uac is not None:
            a["userAccountControl"] = uac
        mc.strategy.add_entry(f"CN=u{i},OU=Users,DC=corp,DC=example,DC=test", a)
    assert mc.bind()
    rows, seen = ads.fetch_rows(mc, BASE, "mock-dc")
    got = {r[0]: r for r in rows}
    assert seen == 3 and got["01000001"][7] == "True" and got["01000002"][7] == "False" and got["01000003"][7] == ""
    assert got["01000001"][6] == MGR_DN
