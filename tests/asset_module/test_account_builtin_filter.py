"""「拉掉內建帳號」篩選器：藏乾淨的系統帳號，但被稽核點名的一律留著。

最關鍵的一條：UID 0 後門帳號因 UID<UID_MIN 會被歸成 service，
無腦藏 service 正好會把它藏掉——那是稽核工具最糟的失敗。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import account_inventory  # noqa: E402
import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_PW = "test-password-123"

# root(default) + 一串系統帳號(service) + 兩個真人 + 一個 UID0 後門(會被歸 service)
PASSWD = """root:x:0:0:root:/root:/bin/bash
bin:x:1:1:bin:/bin:/sbin/nologin
daemon:x:2:2:daemon:/sbin:/sbin/nologin
sshd:x:74:74:ssh:/var/empty/sshd:/sbin/nologin
guest:x:405:100:guest:/home/guest:/bin/bash
backdoor:x:0:0:evil:/home/backdoor:/bin/bash
alice:x:1000:1000:Alice:/home/alice:/bin/bash
bob:x:1001:1001:Bob:/home/bob:/bin/bash
"""
GROUP = "wheel:x:10:alice\n"
LASTLOG = "SRC=lastlog\nUsername Port From Latest\nalice pts/0 x Jul 15, 2026\nbob **Never logged in**\n"
OS_OUT = "OSID=rocky\nOSVER=9.7\nUIDMIN=1000\nBIN=lastlog:/usr/bin/lastlog\nBIN=chage:/usr/bin/chage\nBIN=passwd:/usr/bin/passwd\nBIN=sudo:/usr/bin/sudo\n"


def _runner(host, cmd):
    if cmd.startswith("cat /etc/passwd"):
        return PASSWD
    if cmd.startswith("cat /etc/group"):
        return GROUP
    if "os-release" in cmd:
        return OS_OUT
    if "lastlog" in cmd:
        return LASTLOG
    return ""


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


def _seed(c, ip="203.0.113.30", serial="A-1"):
    db.insert_hardware(c, asset_serial=serial, hostname="h1", ip=ip, collect_ok=1)
    c.execute("INSERT INTO scan_history (ip, scan_ok, open_ports, scan_time) "
              "VALUES (?,1,'22','2026-07-21 10:00:00')", (ip,))
    c.commit()


def test_is_builtin判定():
    # 不能登入的系統帳號 = 可藏的內建噪音（sshd 現在歸 builtin，仍算可藏）
    assert account_inventory._is_builtin(
        {"kind": "builtin", "uid": 74, "username": "sshd", "can_login": 0})
    assert account_inventory._is_builtin(
        {"kind": "service", "uid": 800, "username": "svc99", "can_login": 0})
    assert not account_inventory._is_builtin(
        {"kind": "human", "uid": 1000, "username": "alice", "can_login": 1})
    # UID 0 非 root：披著 service 外皮的後門，絕不算內建
    assert not account_inventory._is_builtin(
        {"kind": "service", "uid": 0, "username": "backdoor", "can_login": 1})
    # 使用者定案（2026-07-22）：root / guest 是可登入預設帳號，稽核焦點，絕不藏
    assert not account_inventory._is_builtin(
        {"kind": "default", "uid": 0, "username": "root", "can_login": 1})
    assert not account_inventory._is_builtin(
        {"kind": "default", "uid": 500, "username": "guest", "can_login": 1})
    # 有登入 shell 的服務帳號本身就可疑，不藏
    assert not account_inventory._is_builtin(
        {"kind": "service", "uid": 200, "username": "svc", "can_login": 1})


def test_拉掉內建帳號後只剩真人與異常():
    with tempfile.TemporaryDirectory() as tmp:
        client, p = _client(tmp)
        try:
            _login(client, p)
            c = db.get_connection(p)
            try:
                _seed(c)
                account_inventory.collect_accounts(c, runner=_runner)
            finally:
                c.close()

            full = client.get("/api/accounts").json()
            assert {x["username"] for x in full["items"]} >= {
                "root", "bin", "daemon", "sshd", "guest", "backdoor", "alice", "bob"}

            hid = client.get("/api/accounts", params={"hide_builtin": True}).json()
            names = {x["username"] for x in hid["items"]}
            # 不能登入的系統帳號被拉掉
            assert "bin" not in names and "daemon" not in names and "sshd" not in names
            # 真人留著
            assert "alice" in names and "bob" in names
            # ⚠️ 使用者定案：root / guest 是可登入預設帳號，稽核焦點，絕不藏
            assert "root" in names and "guest" in names
            # ⚠️ UID 0 後門絕不能被藏（它有 A2 finding，且 is_builtin=False 雙重保險）
            assert "backdoor" in names
            assert hid["hidden_builtin"] > 0
        finally:
            api.app.dependency_overrides.clear()


def test_有稽核發現的內建帳號不被藏():
    """就算是純系統帳號，只要這次被規則點名，就得留在畫面上讓人處理。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, p = _client(tmp)
        try:
            _login(client, p)
            c = db.get_connection(p)
            try:
                _seed(c)
                account_inventory.collect_accounts(c, runner=_runner)
                # 人為給 sshd 一條 finding（模擬它被某規則點名）
                run = c.execute("SELECT MAX(run_id) AS r FROM account_finding").fetchone()["r"]
                c.execute(
                    "INSERT INTO account_finding (run_id, ip, asset_serial, username, rule_id, "
                    "label, severity, verdict, law, detail, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now','localtime'))",
                    (run, "203.0.113.30", "A-1", "sshd", "A1", "空密碼", "high", "fail",
                     "測試", "人為注入"),
                )
                c.commit()
            finally:
                c.close()

            hid = client.get("/api/accounts", params={"hide_builtin": True}).json()
            names = {x["username"] for x in hid["items"]}
            assert "sshd" in names          # 被點名就不藏
            assert "bin" not in names       # 沒被點名的系統帳號照樣藏
        finally:
            api.app.dependency_overrides.clear()


def test_隱藏數量有回報不靜默():
    with tempfile.TemporaryDirectory() as tmp:
        client, p = _client(tmp)
        try:
            _login(client, p)
            c = db.get_connection(p)
            try:
                _seed(c)
                account_inventory.collect_accounts(c, runner=_runner)
            finally:
                c.close()
            r = client.get("/api/accounts", params={"hide_builtin": True}).json()
            assert r["hidden_builtin"] >= 3     # bin/daemon/sshd 至少三個
            # 不開篩選時不回報隱藏
            r0 = client.get("/api/accounts").json()
            assert r0["hidden_builtin"] == 0
        finally:
            api.app.dependency_overrides.clear()
