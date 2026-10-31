"""Q93 WhatsUp Gold 目前告警：用假 transport 測輪詢、差異、歷史、部分失敗、機密、模組關閉。

⚠️ 回應是依官方文件形狀手造的假資料，不是真實 WhatsUp Gold 的回應；真實格式要在公司網路實測。
"""
import json
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import credential_store  # noqa: E402
import db  # noqa: E402
import integration_http as ih  # noqa: E402
import whatsup_constants as C  # noqa: E402
import whatsup_service as wu  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

FAKE_PW = "wug-pw-for-test-4321"
FAKE_SESSION = "wug-access-token-0123456789-abcdefghij-0123456789-abcdefghij"
URL = "https://wug.example.local:9644"


def dev(i, name, ip, state, group=None):
    d = {"id": i, "name": name, "networkAddress": ip, "worstState": state, "bestState": state}
    if group:
        d["groupName"] = group
    return d


def default_devices():
    return [dev(1, "sw-core-01", "10.99.0.1", "Down"), dev(2, "web01", "10.99.1.10", "Up"),
            dev(3, "db01", "10.99.1.20", "Warning"), dev(4, "nas01", "10.99.1.30", "Maintenance"),
            dev(5, "ups-a", "10.99.0.50", "Unresponsive")]


class FakeWug:
    def __init__(self, devices=None, bad_login=False, fail_on=(), page_size=None):
        self.devices = devices if devices is not None else default_devices()
        self.bad_login, self.fail_on, self.page_size = bad_login, tuple(fail_on), page_size
        self.calls = []

    def request(self, method, url, *, headers=None, json_body=None, form=None, params=None, timeout=30.0):
        path = urlsplit(url).path
        self.calls.append({"method": method, "path": path, "params": params, "form": form, "headers": headers or {}})
        R = ih.HttpResult
        if path == C.TOKEN_PATH:
            if self.bad_login:
                return R(400, {"error": "invalid_grant", "error_description": f"bad {form['password']}"}, "")
            return R(200, {"token_type": "bearer", "access_token": FAKE_SESSION, "expires_in": 3600}, "")
        if (headers or {}).get("Authorization") != f"Bearer {FAKE_SESSION}":
            return R(401, {"error": "no token"}, "")
        for frag in self.fail_on:
            if frag in path or (params and frag in json.dumps(params)):
                return R(500, {"message": f"boom {FAKE_SESSION}"}, "")
        if path == C.DEVICES_OVERVIEW_PATH or path == C.GROUP_DEVICES_PATH:
            card = params.get("view") == "card"
            items = [dict(d, groupName=("機房A" if d["id"] in (1, 3) else "機房B")) if card else d for d in self.devices]
            size = self.page_size or len(items)
            start = int(params.get("pageId") or 0)
            chunk = items[start:start + size]
            nxt = str(start + size) if start + size < len(items) else None
            return R(200, {"data": {"devices": chunk}, "paging": {"size": len(chunk), "nextPageId": nxt}}, "")
        if path.startswith("/api/v1/devices/") and path.endswith("/status"):
            did = int(path.split("/")[4])
            return R(200, {"data": {"deviceId": did, "worstState": "Down", "lastStateChange": f"2026-10-29T08:0{did}:00Z",
                                    "downActiveMonitors": [{"name": "Ping"}, {"name": "CPU"}]}}, "")
        return R(404, None, "")

    def close(self):
        pass


def _setup(tmp, **kw):
    db_path = Path(tmp) / "t.db"
    db.init_db(db_path)
    conn = db.get_connection(db_path)
    wu.KEY_PATH = str(Path(tmp) / "cred.key")
    fake = FakeWug(**kw)
    ih.TRANSPORT_FACTORY = lambda ca: fake
    conn.execute("UPDATE feature_flags SET enabled=1 WHERE module_key='whatsup_api'")
    conn.commit()
    credential_store.save(conn, wu.CRED_NAME, "whatsup", "svc_wug", FAKE_PW, key_path=wu.KEY_PATH)   # 帳密在收集憑證庫（舊固定名稱，不設選擇也能解析）
    wu.save_config(conn, url=URL, ca_bundle="", enabled=False, interval_min=5)
    return conn, fake, db_path


def _cleanup():
    ih.TRANSPORT_FACTORY = ih.HttpxTransport
    wu.KEY_PATH = credential_store.KEY_PATH_DEFAULT


def test_只挑告警狀態_Up與Maintenance不算():
    with tempfile.TemporaryDirectory() as tmp:
        conn, fake, _ = _setup(tmp)
        try:
            r = wu.poll(conn, "manual", "admin")
            assert r["status"] == "ok" and r["devices"] == 5 and r["alerts"] == 3 and r["new"] == 3
            cur = wu.current_alerts(conn)
            names = {x["name"]: x for x in cur["rows"]}
            assert set(names) == {"sw-core-01", "db01", "ups-a"}
            a = names["sw-core-01"]
            assert a["ip"] == "10.99.0.1" and a["state"] == "當機" and a["group_name"] == "機房A"
            assert a["problem"] == "Ping、CPU" and a["since"] == "2026-10-29 08:01:00"
            assert [x["__no"] for x in cur["rows"]] == [1, 2, 3] and cur["stale"] is False
        finally:
            _cleanup(); conn.close()


def test_用OAuth密碼授權與Bearer_只讀():
    with tempfile.TemporaryDirectory() as tmp:
        conn, fake, _ = _setup(tmp)
        try:
            wu.poll(conn)
            tok = fake.calls[0]
            assert tok["path"] == C.TOKEN_PATH and tok["form"]["grant_type"] == "password"
            assert all(c["method"] == "GET" for c in fake.calls[1:])
            assert all(c["headers"]["Authorization"] == f"Bearer {FAKE_SESSION}" for c in fake.calls[1:])
        finally:
            _cleanup(); conn.close()


def test_差異_新增_狀態改變_解除_都進歷史():
    with tempfile.TemporaryDirectory() as tmp:
        conn, fake, _ = _setup(tmp)
        try:
            wu.poll(conn)
            ds = default_devices()
            ds[0] = dev(1, "sw-core-01", "10.99.0.1", "Up")            # 解除
            ds[1] = dev(2, "web01", "10.99.1.10", "Critical")          # 新增
            ds[2] = dev(3, "db01", "10.99.1.20", "Down")               # 警告 → 當機
            fake.devices = ds
            r = wu.poll(conn)
            assert r["new"] == 1 and r["cleared"] == 1
            ev = [(h["event"], h["name"]) for h in wu.history(conn)]
            assert ("cleared", "sw-core-01") in ev and ("new", "web01") in ev and ("changed", "db01") in ev
            changed = next(h for h in wu.history(conn) if h["event"] == "changed")
            assert changed["old_state"] == "警告" and changed["new_state"] == "當機"
            assert {x["name"] for x in wu.current_alerts(conn)["rows"]} == {"web01", "db01", "ups-a"}
            n_hist = len(wu.history(conn))
            wu.poll(conn)                                                  # 沒變化 → 不重複寫歷史
            assert len(wu.history(conn)) == n_hist
        finally:
            _cleanup(); conn.close()


def test_翻頁抓完整份():
    with tempfile.TemporaryDirectory() as tmp:
        conn, fake, _ = _setup(tmp, page_size=2)
        try:
            r = wu.poll(conn)
            assert r["devices"] == 5 and r["alerts"] == 3
            pages = [c["params"].get("pageId") for c in fake.calls if c["path"] == C.DEVICES_OVERVIEW_PATH]
            assert pages == [None, "2", "4"]
        finally:
            _cleanup(); conn.close()


def test_抓失敗不清掉舊告警_標過期():
    with tempfile.TemporaryDirectory() as tmp:
        conn, fake, _ = _setup(tmp)
        try:
            wu.poll(conn)
            fake.fail_on = (C.DEVICES_OVERVIEW_PATH,)
            r = wu.poll(conn)
            assert r["status"] == "failed" and FAKE_SESSION not in json.dumps(r, ensure_ascii=False)
            cur = wu.current_alerts(conn)
            assert len(cur["rows"]) == 3                      # 舊的還在，沒被當成解除
            assert cur["stale"] is True and "失敗" in cur["stale_reason"]
            assert not [h for h in wu.history(conn) if h["event"] == "cleared"]
        finally:
            _cleanup(); conn.close()


def test_登入失敗_訊息不含密碼():
    with tempfile.TemporaryDirectory() as tmp:
        conn, fake, _ = _setup(tmp, bad_login=True)
        try:
            r = wu.poll(conn)
            assert r["status"] == "failed" and FAKE_PW not in json.dumps(r, ensure_ascii=False)
            t = wu.check_connection(conn)
            assert t["ok"] is False and FAKE_PW not in t["message"]
            assert FAKE_PW not in json.dumps(wu.runs(conn), ensure_ascii=False)
            assert wu.current_alerts(conn)["stale"] is True
        finally:
            _cleanup(); conn.close()


def test_單台狀態失敗是部分成功_群組失敗也是():
    with tempfile.TemporaryDirectory() as tmp:
        conn, fake, _ = _setup(tmp, fail_on=("/devices/1/status", "card"))
        try:
            r = wu.poll(conn)
            assert r["status"] == "partial" and r["alerts"] == 3
            assert any("sw-core-01" in w for w in r["warnings"]) and any("群組" in w for w in r["warnings"])
            row = next(x for x in wu.current_alerts(conn)["rows"] if x["name"] == "sw-core-01")
            assert row["problem"] == "" and row["group_name"] == ""
        finally:
            _cleanup(); conn.close()


def test_密碼加密存放_稽核不含密碼():
    with tempfile.TemporaryDirectory() as tmp:
        conn, fake, _ = _setup(tmp)
        try:
            assert wu.get_config(conn)["has_password"] and FAKE_PW not in json.dumps(wu.get_config(conn))
            row = conn.execute("SELECT kind, secret_enc FROM collect_credential WHERE name=?", (wu.CRED_NAME,)).fetchone()
            assert row["kind"] == "whatsup" and FAKE_PW not in row["secret_enc"]
            wu.poll(conn)
            audit = json.dumps([dict(r) for r in conn.execute("SELECT * FROM credential_use_audit")])
            assert wu.CRED_NAME in audit and FAKE_PW not in audit
        finally:
            _cleanup(); conn.close()


def test_排程tick():
    with tempfile.TemporaryDirectory() as tmp:
        conn, fake, _ = _setup(tmp)
        try:
            assert wu.tick(conn)["status"] == "disabled"
            wu.save_config(conn, url=URL, ca_bundle="", enabled=True, interval_min=5)
            assert wu.tick(conn)["status"] == "ok"
            assert wu.tick(conn)["status"] == "not_due"
        finally:
            _cleanup(); conn.close()


def test_沒抓過就標過期_空清單不是沒事():
    with tempfile.TemporaryDirectory() as tmp:
        conn, fake, _ = _setup(tmp)
        try:
            cur = wu.current_alerts(conn)
            assert cur["rows"] == [] and cur["stale"] is True and "尚未" in cur["stale_reason"]
        finally:
            _cleanup(); conn.close()


# ===== API =====

def _client(tmp):
    db_path = Path(tmp) / "api.db"
    db.init_db(db_path)

    def _dep():
        c = db.get_connection(db_path)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _dep
    return TestClient(api.app), db_path


def _login(client, db_path, username):
    conn = db.get_connection(db_path)
    try:
        db.create_user(conn, username, auth.hash_password("test-password-123"))
    finally:
        conn.close()
    assert client.post("/api/auth/login", json={"username": username, "password": "test-password-123"}).status_code == 200


def test_模組關閉_所有端點403():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        wu.KEY_PATH = str(Path(tmp) / "cred.key")
        try:
            assert client.get("/api/integrations/whatsup/alerts").status_code == 401
            _login(client, db_path, "admin")
            for m, p in (("get", "/api/integrations/whatsup"), ("put", "/api/integrations/whatsup"), ("post", "/api/integrations/whatsup/test"),
                         ("post", "/api/integrations/whatsup/poll"), ("get", "/api/integrations/whatsup/alerts"),
                         ("get", "/api/integrations/whatsup/history"), ("get", "/api/integrations/whatsup/runs")):
                kw = {"json": {}} if m == "put" else {}
                assert getattr(client, m)(p, **kw).status_code == 403, p
        finally:
            api.app.dependency_overrides.clear(); _cleanup()


def test_API流程_設定_測試_輪詢_看告警():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        fake = FakeWug()
        ih.TRANSPORT_FACTORY = lambda ca: fake
        wu.KEY_PATH = str(Path(tmp) / "cred.key")
        try:
            conn = db.get_connection(db_path)
            conn.execute("UPDATE feature_flags SET enabled=1 WHERE module_key='whatsup_api'"); conn.commit(); conn.close()
            _login(client, db_path, "oncall")
            assert client.get("/api/integrations/whatsup/alerts").status_code == 200       # 一般登入者可看
            assert client.put("/api/integrations/whatsup", json={"url": URL, "username": "u", "password": FAKE_PW}).status_code == 403
            assert client.post("/api/integrations/whatsup/poll").status_code == 403
            client.post("/api/auth/logout")
            _login(client, db_path, "admin")
            _c = db.get_connection(db_path)
            credential_store.save(_c, "wug-api", "whatsup", "svc_wug", FAKE_PW, key_path=wu.KEY_PATH); _c.close()
            r = client.put("/api/integrations/whatsup", json={"url": URL, "credential": "wug-api", "interval_min": 5})
            assert r.status_code == 200 and FAKE_PW not in r.text and r.json()["has_password"] is True
            assert client.put("/api/integrations/whatsup", json={"url": "http://plain"}).status_code == 400
            assert client.post("/api/integrations/whatsup/test").json()["ok"] is True
            p = client.post("/api/integrations/whatsup/poll").json()
            assert p["status"] == "ok" and p["alerts"] == 3
            a = client.get("/api/integrations/whatsup/alerts").json()
            assert len(a["rows"]) == 3 and a["stale"] is False and FAKE_PW not in json.dumps(a)
            assert len(client.get("/api/integrations/whatsup/history").json()["rows"]) == 3
            assert len(client.get("/api/integrations/whatsup/runs").json()["rows"]) == 1
        finally:
            api.app.dependency_overrides.clear(); _cleanup()


# Q91：這個檔案測的是「誰能做什麼」，不套 conftest 的「測試帳號預設 admin」相容層（見 tests/conftest.py）。
ROLE_AWARE_TESTS = True
