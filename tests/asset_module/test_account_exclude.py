"""排除主機不納入帳號稽核：排除不收、清舊資料、可還原、透明呈現、API。"""
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
PASSWD = "root:x:0:0:root:/root:/bin/bash\nalice:x:1000:1000:Alice:/home/alice:/bin/bash\n"
LASTLOG = "SRC=lastlog\nalice pts/0 x Jul 15, 2026\n"


def _runner(host, cmd):
    if cmd.startswith("cat /etc/passwd"):
        return PASSWD
    if "lastlog" in cmd:
        return LASTLOG
    return ""


def _db(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _seed(conn, ip, serial):
    db.insert_hardware(conn, asset_serial=serial, hostname="h", ip=ip, collect_ok=1)
    conn.execute("INSERT INTO scan_history (ip, scan_ok, open_ports, scan_time) "
                 "VALUES (?,1,'22','2026-07-21 10:00:00')", (ip,))
    conn.commit()


def test_排除的主機不再被收():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _db(tmp)
        try:
            _seed(conn, "203.0.113.20", "A-1")
            _seed(conn, "203.0.113.30", "A-3")   # 要排除的
            account_inventory.set_host_excluded(conn, "A-3", True)
            r = account_inventory.collect_accounts(conn, runner=_runner)
            assert r["candidates"] == 1          # 只剩 A-1
            assert {h["ip"] for h in r["hosts"]} == {"203.0.113.20"}
        finally:
            conn.close()


def test_排除時清掉舊帳號與稽核資料():
    """排除卻留著上次的殘影，稽核數字會虛胖，跟排除語意矛盾。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _db(tmp)
        try:
            _seed(conn, "203.0.113.30", "A-3")
            account_inventory.collect_accounts(conn, runner=_runner)   # 先收到 A-3 的帳號
            assert account_inventory.list_accounts(conn)               # 有資料

            account_inventory.set_host_excluded(conn, "A-3", True)
            assert account_inventory.list_accounts(conn) == []         # 舊資料清光
            assert conn.execute("SELECT COUNT(*) FROM account_finding").fetchone()[0] == 0
        finally:
            conn.close()


def test_可還原納回():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _db(tmp)
        try:
            _seed(conn, "203.0.113.30", "A-3")
            account_inventory.set_host_excluded(conn, "A-3", True)
            assert "A-3" in account_inventory.get_excluded_serials(conn)
            account_inventory.set_host_excluded(conn, "A-3", False)
            assert "A-3" not in account_inventory.get_excluded_serials(conn)
            # 納回後又收得到
            r = account_inventory.collect_accounts(conn, runner=_runner)
            assert r["candidates"] == 1
        finally:
            conn.close()


def test_摘要透明列出排除的主機():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _db(tmp)
        try:
            _seed(conn, "203.0.113.20", "A-1")
            _seed(conn, "203.0.113.30", "A-3")
            account_inventory.set_host_excluded(conn, "A-3", True)
            account_inventory.collect_accounts(conn, runner=_runner)
            s = account_inventory.audit_summary(conn)
            assert s["excluded"] == ["A-3"]      # 明確列出，不是靜默
        finally:
            conn.close()


def test_可收集主機清單帶排除狀態():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _db(tmp)
        try:
            _seed(conn, "203.0.113.20", "A-1")
            _seed(conn, "203.0.113.30", "A-3")
            account_inventory.set_host_excluded(conn, "A-3", True)
            hosts = {h["asset_serial"]: h["excluded"]
                     for h in account_inventory.list_collectable_hosts(conn)}
            assert hosts == {"A-1": False, "A-3": True}
        finally:
            conn.close()


# ---- API ----

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


def test_排除端點要登入():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        try:
            assert client.get("/api/accounts/hosts").status_code == 401
            assert client.put("/api/accounts/exclude",
                              json={"asset_serial": "X", "exclude": True}).status_code == 401
        finally:
            api.app.dependency_overrides.clear()


def test_排除api不存在的資產給404():
    with tempfile.TemporaryDirectory() as tmp:
        client, p = _client(tmp)
        try:
            _login(client, p)
            assert client.put("/api/accounts/exclude",
                              json={"asset_serial": "不存在", "exclude": True}).status_code == 404
        finally:
            api.app.dependency_overrides.clear()


def test_排除api完整流程():
    with tempfile.TemporaryDirectory() as tmp:
        client, p = _client(tmp)
        try:
            _login(client, p)
            c = db.get_connection(p)
            try:
                _seed(c, "203.0.113.30", "A-3")
            finally:
                c.close()
            r = client.put("/api/accounts/exclude",
                           json={"asset_serial": "A-3", "exclude": True}).json()
            assert r["excluded"] == ["A-3"]
            hosts = client.get("/api/accounts/hosts").json()["hosts"]
            assert hosts[0]["excluded"] is True
        finally:
            api.app.dependency_overrides.clear()
