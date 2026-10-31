"""收集憑證新增三種類型（whatsup／vrops／storage）——D5 帳密危險區守門。2026-10-30 使用者：
「WhatsUp、vCenter Operations、儲存設備的帳密也都在收集憑證設定」。

守的東西：
1. 新 kind 可存；不在白名單的 kind 拒絕（400）
2. API 回應／列表／稽核／操作紀錄一律不含明文；列表只有 has_secret；DB 裡是密文
3. 寫入（新增／編輯／刪除／測試）非 admin 擋 403，未登入 401
4. 舊整合設定（固定名稱 integration:xxx）不搬也能用；人挑的優先；多筆沒挑不猜
5. 整合服務（WhatsUp／vROps）真的從憑證庫取帳密（不再自己存）
6. 測試按鈕：whatsup／vrops 走各自最小連線（mock）；storage 回「尚未支援測試」；失敗訊息不含密碼
7. SAN／HMC 收集可挑 storage 憑證（類型／適用範圍檢查、留稽核），當場輸入的老路徑不變

未實機驗證：沒有真的 WhatsUp／Aria／SAN／HMC 環境，連線全部是 mock。
"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import credential_store as cs  # noqa: E402
import db  # noqa: E402
import hmc_collector  # noqa: E402
import integration_http as ih  # noqa: E402
import vrops_import as vi  # noqa: E402
import whatsup_service as wu  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

PW = "FAKE-TEST-PW-not-real-7731"
LOGIN_PW = "s3cure-pass!"
URL = "https://wug.example.local:9644"


@pytest.fixture()
def env(monkeypatch):
    from cryptography.fernet import Fernet

    key = Fernet.generate_key()
    monkeypatch.setattr(cs, "_load_or_create_key", lambda path=cs.KEY_PATH_DEFAULT: key)
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "t.db"
        db.init_db(db_path)

        def _dep():
            c = db.get_connection(db_path)
            try:
                yield c
            finally:
                c.close()

        api.app.dependency_overrides[api.get_db] = _dep
        client = TestClient(api.app)
        conn = db.get_connection(db_path)
        db.create_user(conn, "admin", auth.hash_password(LOGIN_PW))
        db.create_user(conn, "oncall", auth.hash_password(LOGIN_PW), role="user")
        conn.close()
        yield client, db_path
        api.app.dependency_overrides.clear()


def _login(client, who):
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"username": who, "password": LOGIN_PW}).status_code == 200


def _db(db_path):
    return db.get_connection(db_path)


def _post(client, name, kind, scope=None):
    return client.post("/api/credentials", json={"name": name, "kind": kind, "username": "svc_x", "password": PW,
                                                   "scope": scope, "note": "t"})


# ---- 1 / 2 ----
def test_新類型可存_未知類型拒絕(env):
    client, _ = env
    _login(client, "admin")
    for kind in ("whatsup", "vrops", "storage", "winrm", "ssh"):
        assert _post(client, f"c-{kind}", kind).status_code == 200, kind
    assert _post(client, "bad", "mongo").status_code == 400
    assert _post(client, "bad2", "").status_code == 400


def test_回應列表稽核都不含明文(env):
    client, db_path = env
    _login(client, "admin")
    r = _post(client, "san-lab", "storage", "10.99.5.")
    assert r.status_code == 200 and PW not in r.text
    lst = client.get("/api/credentials")
    assert PW not in lst.text
    row = next(x for x in lst.json() if x["name"] == "san-lab")
    assert row["has_secret"] is True and "secret_enc" not in row and "password" not in row
    r = client.put("/api/credentials/san-lab", json={"username": "svc_y", "password": PW + "2"})
    assert r.status_code == 200 and PW not in r.text
    conn = _db(db_path)
    enc = conn.execute("SELECT secret_enc FROM collect_credential WHERE name='san-lab'").fetchone()["secret_enc"]
    assert PW not in enc and (PW + "2") not in enc
    assert client.delete("/api/credentials/san-lab").status_code == 200
    log = json.dumps([dict(x) for x in conn.execute("SELECT * FROM activity_log")], ensure_ascii=False)
    audit = json.dumps([dict(x) for x in conn.execute("SELECT * FROM credential_use_audit")], ensure_ascii=False)
    conn.close()
    assert "san-lab" in log and PW not in log and PW not in audit
    for act in ("新增收集憑證", "編輯收集憑證", "刪除收集憑證"):
        assert act in log, act


# ---- 3 ----
def test_非admin與未登入擋下(env):
    client, _ = env
    assert _post(client, "x", "storage").status_code == 401
    _login(client, "admin")
    assert _post(client, "x", "whatsup").status_code == 200
    _login(client, "oncall")
    assert _post(client, "y", "whatsup").status_code == 403
    assert client.put("/api/credentials/x", json={"username": "u"}).status_code == 403
    assert client.delete("/api/credentials/x").status_code == 403
    assert client.post("/api/credentials/x/test", json={}).status_code == 403


# ---- 4 / 5 ----
class _FakeWug:
    def __init__(self, user, pw):
        self.user, self._pw = user, pw

    def acquire_token(self):
        pass

    def devices_overview(self):
        return [1, 2]


class _FakeAria:
    def __init__(self, user, pw):
        self.user, self._pw, self._token = user, pw, None

    def acquire_token(self):
        pass

    def list_resources(self, kind):
        return [1]

    def release_token(self):
        pass


@pytest.fixture()
def svc(env, monkeypatch):
    client, db_path = env
    seen = {}

    def wug_factory(cfg, user, pw):
        seen["wug"] = (user, pw)
        return _FakeWug(user, pw)

    def aria_factory(cfg, user, pw):
        seen["aria"] = (user, pw)
        return _FakeAria(user, pw)

    monkeypatch.setattr(wu, "CLIENT_FACTORY", wug_factory)
    monkeypatch.setattr(vi, "CLIENT_FACTORY", aria_factory)
    conn = _db(db_path)
    wu.ensure_tables(conn)
    vi.ensure_tables(conn)
    conn.execute("UPDATE feature_flags SET enabled=1 WHERE module_key IN ('whatsup_api','vrops_api')")
    conn.commit()
    conn.close()
    return client, db_path, seen


def test_舊整合憑證不搬也能用_人挑的優先_多筆不猜(svc):
    _client, db_path, seen = svc
    conn = _db(db_path)
    wu.save_config(conn, url=URL, ca_bundle="", enabled=False, interval_min=5)
    cs.save(conn, wu.CRED_NAME, "whatsup", "legacy_user", PW)           # 舊版固定名稱，沒有任何「選擇」設定
    cfg = wu.get_config(conn)
    assert cfg["credential"] == wu.CRED_NAME and cfg["username"] == "legacy_user" and cfg["has_password"]
    assert wu.check_connection(conn)["ok"] and seen["wug"] == ("legacy_user", PW)
    cs.save(conn, "wug-new", "whatsup", "new_user", PW + "n")
    assert wu.credential_name(conn) == wu.CRED_NAME                      # 沒挑＝舊名稱優先，不會悄悄換組
    wu.save_config(conn, url=URL, ca_bundle="", enabled=False, interval_min=5, credential="wug-new")
    assert wu.check_connection(conn)["ok"] and seen["wug"] == ("new_user", PW + "n")
    conn.execute("DELETE FROM collect_credential WHERE name = ?", (wu.CRED_NAME,))
    conn.execute("DELETE FROM app_settings WHERE key = ?", (wu.K_CRED,))
    cs.save(conn, "wug-2", "whatsup", "u2", PW)
    conn.commit()
    assert wu.credential_name(conn) is None                              # 同類型兩筆又沒挑：不猜
    assert wu.check_connection(conn)["ok"] is False
    conn.close()


def test_整合服務從憑證庫取值_不再自己收帳密(svc):
    client, db_path, seen = svc
    _login(client, "admin")
    assert _post(client, "aria-api", "vrops").status_code == 200
    assert _post(client, "wug-api", "whatsup").status_code == 200
    # 舊版 client 還在送帳密欄位：明確 400 拒絕（不是靜默 200 然後密碼其實沒更新）
    r = client.put("/api/integrations/vrops", json={"url": URL, "credential": "aria-api", "username": "sneak", "password": "sneak-pw"})
    assert r.status_code == 400 and "sneak" not in r.text and "收集憑證" in r.text   # 不回顯送來的密碼
    r = client.put("/api/integrations/vrops", json={"url": URL, "credential": "aria-api"})
    assert r.status_code == 200 and r.json()["credential"] == "aria-api" and r.json()["username"] == "svc_x"
    assert "sneak" not in r.text and PW not in r.text
    r = client.put("/api/integrations/whatsup", json={"url": URL, "credential": "wug-api"})
    assert r.status_code == 200 and r.json()["has_password"] is True and PW not in r.text
    assert client.post("/api/integrations/vrops/test").json()["ok"] is True and seen["aria"] == ("svc_x", PW)
    assert client.post("/api/integrations/whatsup/test").json()["ok"] is True and seen["wug"] == ("svc_x", PW)
    conn = _db(db_path)
    assert conn.execute("SELECT COUNT(*) AS n FROM collect_credential WHERE name LIKE 'integration:%'").fetchone()["n"] == 0
    conn.close()
    # 挑到不存在／類型不對的憑證 → 400
    assert client.put("/api/integrations/whatsup", json={"url": URL, "credential": "aria-api"}).status_code == 400
    assert client.put("/api/integrations/whatsup", json={"url": URL, "credential": "nope"}).status_code == 400


def test_整合設定非admin仍擋(svc):
    client, _db_path, _ = svc
    _login(client, "oncall")
    assert client.put("/api/integrations/whatsup", json={"url": URL, "credential": "x"}).status_code == 403
    assert client.put("/api/integrations/vrops", json={"url": URL, "credential": "x"}).status_code == 403


# ---- 6 ----
def test_測試按鈕_新類型(svc, monkeypatch):
    client, db_path, seen = svc
    _login(client, "admin")
    _post(client, "wug-api", "whatsup")
    _post(client, "aria-api", "vrops")
    _post(client, "san-lab", "storage")
    conn = _db(db_path)
    wu.save_config(conn, url=URL, ca_bundle="", enabled=False, interval_min=5, credential="wug-api")
    vi.save_config(conn, url=URL, auth_source="", ca_bundle="", enabled=False, interval_hours=24, credential="aria-api")
    conn.close()
    r = client.post("/api/credentials/wug-api/test", json={})
    assert r.status_code == 200 and r.json()["ok"] is True and PW not in r.text
    r = client.post("/api/credentials/aria-api/test", json={})
    assert r.status_code == 200 and r.json()["ok"] is True and PW not in r.text
    r = client.post("/api/credentials/san-lab/test", json={})
    assert r.status_code == 200 and r.json()["ok"] is False and r.json()["supported"] is False and "尚未支援" in r.json()["message"]
    # 失敗：訊息要白話、不含密碼（用戶端把密碼寫進錯誤訊息時由 ih.scrub 去掉）
    def bad_factory(cfg, user, pw):
        c = _FakeWug(user, pw)

        def boom():
            raise ih.TransportError(f"login failed for {pw}")
        c.acquire_token = boom
        return c
    monkeypatch.setattr(wu, "CLIENT_FACTORY", bad_factory)
    import whatsup_service
    whatsup_service.CLIENT_FACTORY = bad_factory
    from routers import onboard as ob
    ob._CRED_TEST_LAST.clear()
    r = client.post("/api/credentials/wug-api/test", json={})
    assert r.status_code == 200 and r.json()["ok"] is False and PW not in r.text


# ---- 7 ----
@pytest.fixture()
def hmc(env, monkeypatch):
    client, db_path = env
    got = {}

    def fake_collect(ip, user, pw, timeout=40):
        got["login"] = (ip, user, pw)
        return {"systems": [], "lpars": []}

    monkeypatch.setattr(hmc_collector, "collect", fake_collect)
    monkeypatch.setattr(hmc_collector, "store", lambda conn, ip, inv, by: {"system_count": 0, "lpar_count": 0})
    return client, db_path, got


def test_HMC收集可挑storage憑證(hmc):
    client, db_path, got = hmc
    _login(client, "admin")
    _post(client, "hmc-lab", "storage", "10.99.8.")
    _post(client, "win-x", "winrm", "10.99.8.")
    r = client.post("/api/hmc/collect", json={"ip": "10.99.8.5", "credential": "hmc-lab"})
    assert r.status_code == 200 and got["login"] == ("10.99.8.5", "svc_x", PW) and PW not in r.text
    assert client.post("/api/hmc/collect", json={"ip": "10.99.9.5", "credential": "hmc-lab"}).status_code == 400   # 範圍外
    assert client.post("/api/hmc/collect", json={"ip": "10.99.8.5", "credential": "win-x"}).status_code == 400     # 類型不對
    assert client.post("/api/hmc/collect", json={"ip": "10.99.8.5", "credential": "nope"}).status_code == 400
    assert client.post("/api/hmc/collect", json={"ip": "10.99.8.5"}).status_code == 400                           # 兩種都沒給
    r = client.post("/api/hmc/collect", json={"ip": "10.99.8.5", "username": "typed", "password": "typed-pw"})   # 當場輸入老路徑
    assert r.status_code == 200 and got["login"] == ("10.99.8.5", "typed", "typed-pw")
    conn = _db(db_path)
    audit = json.dumps([dict(x) for x in conn.execute("SELECT * FROM credential_use_audit")], ensure_ascii=False)
    conn.close()
    assert "hmc-lab" in audit and PW not in audit and "typed-pw" not in audit


# ---- 審查（獨立審查者＋Codex，2026-10-30）後補的防線 ----
def test_儲存憑證沒設範圍就拒用(hmc):
    client, _db_path, got = hmc
    _login(client, "admin")
    _post(client, "hmc-open", "storage", None)                      # 範圍留空＝通用
    r = client.post("/api/hmc/collect", json={"ip": "10.99.8.5", "credential": "hmc-open"})
    assert r.status_code == 400 and "適用範圍" in r.text and "login" not in got


def test_一般帳號不能用已存的儲存憑證(hmc):
    # 存取等級登記表本來就把 /api/hmc/collect 擋在管理員；resolve_storage_login 另外再擋一層（雙保險）
    client, _db_path, got = hmc
    _login(client, "admin")
    _post(client, "hmc-lab", "storage", "10.99.8.")
    _login(client, "oncall")
    r = client.post("/api/hmc/collect", json={"ip": "10.99.8.5", "credential": "hmc-lab"})
    assert r.status_code == 403 and "login" not in got


def test_儲存憑證不能對VMware設備使用(hmc):
    client, db_path, got = hmc
    _login(client, "admin")
    _post(client, "hmc-lab", "storage", "10.99.8.")
    conn = _db(db_path)
    conn.execute("INSERT INTO hardware (asset_serial, hostname, ip, os, device_model) "
                 "VALUES ('T-ESX-1','esx-t1','10.99.8.77','VMware ESXi 7.0','VMware ESXi')")
    conn.commit()
    conn.close()
    r = client.post("/api/hmc/collect", json={"ip": "10.99.8.77", "credential": "hmc-lab"})
    assert r.status_code == 400 and "VMware" in r.text and "login" not in got


def test_收集失敗的錯誤文字不會夾帶密碼(env, monkeypatch):
    client, db_path = env
    _login(client, "admin")
    _post(client, "hmc-lab", "storage", "10.99.8.")

    def boom(ip, user, pw, timeout=40):
        raise RuntimeError(f"auth failed for {user}:{pw}")

    monkeypatch.setattr(hmc_collector, "collect", boom)
    r = client.post("/api/hmc/collect", json={"ip": "10.99.8.5", "credential": "hmc-lab"})
    assert r.status_code == 502 and PW not in r.text and "●●●" in r.text


def test_啟用中不能清掉所選憑證(svc):
    client, _db_path, _ = svc
    _login(client, "admin")
    assert _post(client, "wug-api", "whatsup").status_code == 200
    assert client.put("/api/integrations/whatsup", json={"url": URL, "credential": "wug-api", "enabled": True}).status_code == 200
    r = client.put("/api/integrations/whatsup", json={"url": URL, "credential": "", "enabled": True})
    assert r.status_code == 400 and "清掉" in r.text
