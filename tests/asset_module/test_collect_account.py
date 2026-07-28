"""可設定的收集身分（方案 A：用 sysinfra 收）。

驗證：設定影響遠端 SSH 登入帳號；本機收集不受影響；API 只接受白名單身分；
公鑰授權說明帶得出來。真正的 sysinfra 登入要等公鑰授權後在真機驗，這裡守設定管線。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
import manage_state  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_PW = "test-password-123"


def _db(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return p


def test_預設收集身分是唯讀帳號():
    with tempfile.TemporaryDirectory() as tmp:
        conn = db.get_connection(_db(tmp))
        try:
            assert manage_state.get_collect_account(conn) == "webit3scan"
        finally:
            conn.close()


def test_設定影響遠端ssh登入帳號(monkeypatch):
    """收集身分改成 sysinfra 後，遠端 SSH 指令要用 sysinfra@host 登入。"""
    captured = {}

    import subprocess

    class FakeCompleted:
        stdout = ""

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return FakeCompleted()

    monkeypatch.setattr(subprocess, "run", fake_run)

    runner = manage_state._runner_for("203.0.113.9", "/tmp/key", account="sysinfra")
    runner("203.0.113.9", "id")
    # ssh -i /tmp/key ... sysinfra@203.0.113.9 id
    assert "sysinfra@203.0.113.9" in captured["cmd"]


def test_預設帳號時本機走直跑不繞ssh(monkeypatch):
    """用預設唯讀帳號(webit3scan)收本機時走 local runner（bash -lc）。"""
    import subprocess

    captured = {}

    class FakeCompleted:
        stdout = ""

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return FakeCompleted()

    monkeypatch.setattr(manage_state, "local_ips", lambda: {"203.0.113.1"})
    monkeypatch.setattr(subprocess, "run", fake_run)
    runner = manage_state._runner_for("203.0.113.1", "/tmp/key")   # 預設帳號
    runner("203.0.113.1", "id")
    assert captured["cmd"][0] == "bash"        # 本機直跑，不繞 SSH


def test_設了管理身分時本機也走ssh(monkeypatch):
    """設了 sysinfra 後，連本機都要以 sysinfra SSH（否則收集器自己漏一半需 root 的欄位）。"""
    import subprocess

    captured = {}

    class FakeCompleted:
        stdout = ""

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return FakeCompleted()

    monkeypatch.setattr(manage_state, "local_ips", lambda: {"203.0.113.1"})
    monkeypatch.setattr(subprocess, "run", fake_run)
    runner = manage_state._runner_for("203.0.113.1", "/tmp/key", account="sysinfra")
    runner("203.0.113.1", "id")
    assert captured["cmd"][0] == "ssh"                          # 本機也走 SSH
    assert "sysinfra@203.0.113.1" in captured["cmd"]           # 以 sysinfra 身分


# ---- API ----

def _client(tmp):
    p = _db(tmp)

    def _ov():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _ov
    return TestClient(api.app), p


def _login(client, p):
    c = db.get_connection(p)
    try:
        db.create_user(c, "tester", auth.hash_password(_PW))
    finally:
        c.close()
    assert client.post("/api/auth/login",
                       json={"username": "tester", "password": _PW}).status_code == 200


def test_config端點要登入():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        try:
            assert client.get("/api/accounts/collect-config").status_code == 401
            assert client.put("/api/accounts/collect-config",
                              json={"account": "sysinfra"}).status_code == 401
        finally:
            api.app.dependency_overrides.clear()


def test_可切成sysinfra並帶出授權說明():
    with tempfile.TemporaryDirectory() as tmp:
        client, p = _client(tmp)
        try:
            _login(client, p)
            r = client.put("/api/accounts/collect-config", json={"account": "sysinfra"})
            assert r.status_code == 200
            cfg = client.get("/api/accounts/collect-config").json()
            assert cfg["account"] == "sysinfra"
            assert "authorized_keys" in cfg["provision_hint"]
            assert any(o["value"] == "sysinfra" for o in cfg["options"])
        finally:
            api.app.dependency_overrides.clear()


def test_只接受白名單身分():
    """收集身分決定拿多大權限，不能是打字就指定任意帳號的欄位。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, p = _client(tmp)
        try:
            _login(client, p)
            assert client.put("/api/accounts/collect-config",
                              json={"account": "root"}).status_code == 400
            assert client.put("/api/accounts/collect-config",
                              json={"account": "'; DROP TABLE users--"}).status_code == 400
            # 設定沒被污染，維持預設
            assert client.get("/api/accounts/collect-config").json()["account"] == "webit3scan"
        finally:
            api.app.dependency_overrides.clear()
