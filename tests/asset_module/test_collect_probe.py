"""逐主機收集健檢：判定分類、不外流原始輸出、hints、去識別化匯出、殘留掃描閘門。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import collect_probe as cp  # noqa: E402
import db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_PW = "test-password-123"


def test_判定分類():
    assert cp._classify(0, "data\n", "") == "ok"
    assert cp._classify(0, "", "") == "empty"
    assert cp._classify(1, "", "bash: lastlog: command not found") == "command_missing"
    assert cp._classify(1, "", "sudo: a password is required") == "permission"
    assert cp._classify(255, "", "ssh: connect ... Connection timed out") == "unreachable"


def test_非ascii旗標抓語系():
    assert cp._has_non_ascii("二  7月 21 06:32") is True     # 中文 lastlog
    assert cp._has_non_ascii("Mon Jul 21 06:32") is False


def test_probe不外流原始輸出(monkeypatch):
    """passwd/shadow/authorized_keys 的內容絕不出現在回傳裡——只有行數/判定/stderr。"""
    SECRET_PASSWD = "root:x:0:0:root:/root:/bin/bash\nsupersecretuser:x:1000:1000::/h:/bin/sh\n"

    def fake_runner(ip, account, key_path, local_ips, timeout=12):
        def run(cmd):
            if "id -un" in cmd:
                return 0, "sysinfra\n", ""
            if "sudo -n true" in cmd:
                return 0, "__rc=0\n", ""
            if "os-release" in cmd:
                return 0, "OSID=rocky\nOSVER=9.7\nUIDMIN=1000\nBIN=lastlog:/usr/bin/lastlog\n", ""
            if cmd.startswith("cat /etc/passwd"):
                return 0, SECRET_PASSWD, ""
            return 0, "x\n", ""
        return run

    monkeypatch.setattr(cp, "_runner", fake_runner)
    r = cp.probe("203.0.113.5", "sysinfra", "/tmp/key", local_ips=set())
    import json
    blob = json.dumps(r, ensure_ascii=False)
    assert "supersecretuser" not in blob          # 原始帳號名不外流
    assert "/root" not in blob                     # 家目錄路徑不外流
    passwd_cmd = [c for c in r["commands"] if c["name"] == "passwd"][0]
    assert passwd_cmd["stdout_lines"] == 2         # 只有行數
    assert passwd_cmd["verdict"] == "ok"


def test_probe連不上給可行動提示(monkeypatch):
    def fake_runner(ip, account, key_path, local_ips, timeout=12):
        def run(cmd):
            return 255, "", "Permission denied (publickey)"
        return run

    monkeypatch.setattr(cp, "_runner", fake_runner)
    r = cp.probe("203.0.113.5", "sysinfra", "/tmp/key")
    assert r["reachable"] is False
    assert any("authorized_keys" in h for h in r["hints"])


def test_hints認得無sudo與無lastlog(monkeypatch):
    def fake_runner(ip, account, key_path, local_ips, timeout=12):
        def run(cmd):
            if "id -un" in cmd:
                return 0, "webit3scan\n", ""
            if "sudo -n true" in cmd:
                return 0, "__rc=1\n", ""          # sudo 不可用
            if "os-release" in cmd:
                return 0, "OSID=debian\nOSVER=13\nUIDMIN=1000\n", ""
            if "lastlog" in cmd:                    # Debian 13：lastlog 不存在
                return 1, "", "bash: lastlog: command not found"
            if "passwd -S" in cmd or "shadow" in cmd:
                return 1, "", "sudo: a password is required"
            return 0, "x\n", ""
        return run

    monkeypatch.setattr(cp, "_runner", fake_runner)
    r = cp.probe("203.0.113.5", "webit3scan", "/tmp/key")
    joined = " ".join(r["hints"])
    assert "sudo -n 不可用" in joined
    assert "lastlog" in joined


# ---- API：去識別化＋殘留掃描 ----

def _client(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)

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


def test_健檢端點要登入():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        try:
            assert client.get("/api/accounts/diagnose",
                              params={"asset_serial": "X"}).status_code == 401
        finally:
            api.app.dependency_overrides.clear()


def test_健檢端點去識別化真實IP(monkeypatch):
    """stderr 裡的真實 IP 要被遮成假名才能匯出。"""
    def fake_runner(ip, account, key_path, local_ips, timeout=12):
        def run(cmd):
            if "id -un" in cmd:
                return 0, "sysinfra\n", ""
            # stderr 帶一個真實內網 IP
            return 1, "", "ssh: connect to host 192.168.1.99 port 22: refused"
        return run

    with tempfile.TemporaryDirectory() as tmp:
        client, p = _client(tmp)
        try:
            _login(client, p)
            c = db.get_connection(p)
            try:
                db.insert_hardware(c, asset_serial="A-1", hostname="h1", ip="203.0.113.9")
            finally:
                c.close()
            monkeypatch.setattr(cp, "_runner", fake_runner)
            r = client.get("/api/accounts/diagnose",
                           params={"asset_serial": "A-1"}).json()
            import json
            blob = json.dumps(r, ensure_ascii=False)
            assert "192.168.1.99" not in blob        # 真 IP 被遮
            assert r["_desensitized"] is True
        finally:
            api.app.dependency_overrides.clear()
