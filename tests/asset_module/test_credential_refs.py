"""9-3 收集憑證統一入口（D5）：被誰引用、最近使用、刪除前擋、遮罩與稽核、無舊明文路徑。2026-11-02。

守的東西：
1. GET /api/credentials 附 refs／ref_count／last_used_at，仍永不含密碼／密文
2. 引用來源：WhatsUp／vROps 設定（人挑的）、儲存設備、納管時指定的主機
3. 仍被引用 → DELETE 回 409 並列出引用者，憑證還在；解除引用後可刪；被拒也留稽核
4. 同名覆寫不能偷換類型（被引用時 409）
5. 無舊明文路徑：整合設定／app_settings／storage_devices 沒有任何存帳密的欄位；整合 API 收到 password 欄一律 400
全部假值，不連外。
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
import credential_refs as cr  # noqa: E402
import credential_store as cs  # noqa: E402
import db  # noqa: E402
import storage_unity as su  # noqa: E402
import vrops_import as vi  # noqa: E402
import whatsup_service as wu  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

PW = "FAKE-TEST-PW-not-real-5521"
LOGIN_PW = "s3cure-pass!"


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
        wu.ensure_tables(conn)
        vi.ensure_tables(conn)
        su.ensure_tables(conn)
        conn.execute("UPDATE feature_flags SET enabled=1 WHERE module_key IN ('whatsup_api','vrops_api','storage_api')")
        conn.commit()
        conn.close()
        assert client.post("/api/auth/login", json={"username": "admin", "password": LOGIN_PW}).status_code == 200
        yield client, db_path
        api.app.dependency_overrides.clear()


def _conn(db_path):
    return db.get_connection(db_path)


def _post(client, name, kind, scope=None):
    r = client.post("/api/credentials", json={"name": name, "kind": kind, "username": "svc_x", "password": PW,
                                               "scope": scope, "note": "t"})
    assert r.status_code == 200, r.text
    return r


def _row(client, name):
    return next(x for x in client.get("/api/credentials").json() if x["name"] == name)


def test_列表附引用與最近使用_仍不含密碼(env):
    client, db_path = env
    _post(client, "wug-a", "whatsup")
    _post(client, "wug-b", "whatsup")
    conn = _conn(db_path)
    db.set_setting(conn, wu.K_CRED, "wug-a")
    db.set_setting(conn, wu.K_URL, "https://wug.example.local:9644")
    cs.audit_use(conn, "wug-a", "wug.example.local", True, "連線測試")
    cs.audit_use(conn, "wug-a", "wug.example.local", False, "輪詢失敗：逾時")
    conn.commit()
    conn.close()
    r = client.get("/api/credentials")
    assert PW not in r.text and "secret_enc" not in r.text
    a, b = _row(client, "wug-a"), _row(client, "wug-b")
    assert a["ref_count"] == 1 and a["refs"][0]["type"] == "whatsup" and a["refs"][0]["link"].startswith("/v2/settings")
    assert a["last_used_at"] and a["last_used_ok"] is False          # 取最新一筆（失敗那筆）
    assert b["ref_count"] == 0 and b["refs"] == [] and b["last_used_at"] is None


def test_引用來源_vrops_儲存設備_主機指定(env):
    client, db_path = env
    _post(client, "ar-1", "vrops")
    _post(client, "st-1", "storage", "10.99.0.")
    _post(client, "ssh-1", "ssh")
    conn = _conn(db_path)
    db.set_setting(conn, vi.K_CRED, "ar-1")
    db.set_setting(conn, vi.K_URL, "https://aria.example.local")
    su.create_device(conn, "admin", name="unity-a", host="10.99.0.5", credential="st-1")
    cs.set_pref(conn, "10.99.3.7", "ssh", "ssh-1", "admin")
    cs.set_pref(conn, "10.99.3.8", "ssh", "ssh-1", "admin")
    conn.commit()
    conn.close()
    assert _row(client, "ar-1")["refs"][0]["type"] == "vrops"
    st = _row(client, "st-1")
    assert st["ref_count"] == 1 and "unity-a" in st["refs"][0]["label"] and st["refs"][0]["type"] == "storage"
    h = _row(client, "ssh-1")
    assert h["ref_count"] == 2 and {r["type"] for r in h["refs"]} == {"host"} and h["refs"][0]["link"].startswith("/v2/hosts")


def test_主機指定很多台_明細截斷但總數正確(env):
    client, db_path = env
    _post(client, "ssh-big", "ssh")
    conn = _conn(db_path)
    for i in range(cr.MAX_HOST_REFS + 5):
        cs.set_pref(conn, f"10.99.4.{i}", "ssh", "ssh-big", "admin")
    conn.commit()
    conn.close()
    row = _row(client, "ssh-big")
    assert row["ref_count"] == cr.MAX_HOST_REFS + 5 and len(row["refs"]) == cr.MAX_HOST_REFS


def test_被引用時刪除被擋_列出引用者_解除後可刪_被拒也留稽核(env):
    client, db_path = env
    _post(client, "st-del", "storage", "10.99.0.")
    conn = _conn(db_path)
    su.create_device(conn, "admin", name="unity-z", host="10.99.0.9", credential="st-del")
    conn.close()
    r = client.delete("/api/credentials/st-del")
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["ref_count"] == 1 and "unity-z" in d["refs"][0]["label"] and "st-del" in d["message"]
    assert PW not in r.text
    assert _row(client, "st-del")                                   # 還在
    conn = _conn(db_path)
    log = json.dumps([dict(x) for x in conn.execute("SELECT * FROM activity_log")], ensure_ascii=False)
    assert "被拒" in log and "st-del" in log and PW not in log
    conn.execute("DELETE FROM storage_devices")                     # 解除引用
    conn.commit()
    conn.close()
    assert client.delete("/api/credentials/st-del").status_code == 200
    assert not any(x["name"] == "st-del" for x in client.get("/api/credentials").json())
    conn = _conn(db_path)
    log = json.dumps([dict(x) for x in conn.execute("SELECT * FROM activity_log")], ensure_ascii=False)
    conn.close()
    assert "刪除收集憑證「st-del」" in log


def test_同名覆寫不能偷換類型(env):
    client, db_path = env
    _post(client, "st-k", "storage", "10.99.0.")
    conn = _conn(db_path)
    su.create_device(conn, "admin", name="unity-k", host="10.99.0.6", credential="st-k")
    conn.close()
    r = client.post("/api/credentials", json={"name": "st-k", "kind": "ssh", "username": "u", "password": PW})
    assert r.status_code == 409 and "unity-k" in r.text and PW not in r.text
    assert _row(client, "st-k")["kind"] == "storage"
    # 同類型覆寫（換密碼）仍可
    assert client.post("/api/credentials", json={"name": "st-k", "kind": "storage", "username": "u2", "password": PW + "x",
                                                 "scope": "10.99.0."}).status_code == 200


def test_沒設網址也沒挑的整合不算引用(env):
    client, _ = env
    _post(client, "wug-only", "whatsup")            # 同類型唯一一筆，但整合根本沒設定
    assert _row(client, "wug-only")["ref_count"] == 0


def test_無舊明文路徑_整合API收到密碼欄一律400且不回顯(env):
    client, db_path = env
    for path, body in (("/api/integrations/whatsup", {"url": "https://x.example.local", "ca_bundle": "", "enabled": False,
                                                              "interval_min": 5, "password": PW}),
                       ("/api/integrations/vrops", {"url": "https://x.example.local", "auth_source": "local", "ca_bundle": "",
                                                           "enabled": False, "interval_hours": 24, "username": "u", "password": PW})):
        r = client.put(path, json=body)
        assert r.status_code == 400, (path, r.status_code)
        assert PW not in r.text
    r = client.post("/api/storage/devices", json={"name": "d", "host": "10.99.0.1", "password": PW})
    assert r.status_code == 400 and PW not in r.text
    conn = _conn(db_path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(storage_devices)")}
    assert not ({"password", "secret", "token", "username", "secret_enc"} & cols)      # 設備表只存憑證名稱
    leak = [dict(x) for x in conn.execute("SELECT key FROM app_settings WHERE lower(key) LIKE '%pass%' OR lower(key) LIKE '%pwd%'")]
    conn.close()
    assert leak == []        # app_settings 沒有任何密碼類鍵（Teams webhook 另有加密，非登入帳密）
