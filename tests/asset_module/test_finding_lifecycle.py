"""發現生命週期（處置狀態跨盤點持久）＋匯出報告。"""
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
GROUP = "wheel:x:10:alice\n"
LASTLOG = "SRC=lastlog\nalice pts/0 x Jul 15, 2026\n"
OS_OUT = "OSID=rocky\nOSVER=9.7\nUIDMIN=1000\nBIN=lastlog:/usr/bin/lastlog\nBIN=chage:/usr/bin/chage\n"


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


def _db(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _seed(conn):
    db.insert_hardware(conn, asset_serial="A-1", hostname="h", ip="203.0.113.5", collect_ok=1)
    conn.execute("INSERT INTO scan_history (ip, scan_ok, open_ports, scan_time) "
                 "VALUES ('203.0.113.5',1,'22','2026-07-21 10:00:00')")
    conn.commit()


def _a_finding(conn):
    f = account_inventory.latest_findings(conn)
    assert f, "應有發現"
    return f[0]


def test_處置狀態跨盤點持久():
    """狀態設在 (ip,username,rule_id) 上，重新盤點（findings 換 id）後仍記得。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _db(tmp)
        try:
            _seed(conn)
            account_inventory.collect_accounts(conn, runner=_runner)
            f = _a_finding(conn)
            account_inventory.set_finding_disposition(
                conn, f["ip"], f["username"], f["rule_id"], "ack", decided_by="tester")

            # 再盤一次（新 run、新 finding id）
            account_inventory.collect_accounts(conn, runner=_runner)
            f2 = [x for x in account_inventory.latest_findings(conn)
                  if x["ip"] == f["ip"] and x["username"] == f["username"]
                  and x["rule_id"] == f["rule_id"]][0]
            assert f2["status"] == "ack"              # 狀態還記得
            assert f2["decided_by"] == "tester"
        finally:
            conn.close()


def test_核准例外未到期隱藏過期回來():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _db(tmp)
        try:
            _seed(conn)
            account_inventory.collect_accounts(conn, runner=_runner)
            f = _a_finding(conn)
            n0 = len(account_inventory.latest_findings(conn))

            # 核准例外到未來 → 該條隱藏
            account_inventory.set_finding_disposition(
                conn, f["ip"], f["username"], f["rule_id"], "exception",
                exempt_until="2099-12-31", decided_by="tester")
            assert len(account_inventory.latest_findings(conn)) == n0 - 1

            # 到期 → 自動回來
            account_inventory.set_finding_disposition(
                conn, f["ip"], f["username"], f["rule_id"], "exception",
                exempt_until="2000-01-01", decided_by="tester")
            assert len(account_inventory.latest_findings(conn)) == n0
        finally:
            conn.close()


def test_標為已修復卻仍偵測到要標矛盾():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _db(tmp)
        try:
            _seed(conn)
            account_inventory.collect_accounts(conn, runner=_runner)
            f = _a_finding(conn)
            account_inventory.set_finding_disposition(
                conn, f["ip"], f["username"], f["rule_id"], "fixed", decided_by="tester")
            f2 = [x for x in account_inventory.latest_findings(conn)
                  if x["rule_id"] == f["rule_id"] and x["username"] == f["username"]][0]
            assert f2["status"] == "fixed"
            assert f2["contradiction"] is True         # 標修復但還在＝矛盾
        finally:
            conn.close()


def test_未知狀態拒絕():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _db(tmp)
        try:
            _seed(conn)
            account_inventory.collect_accounts(conn, runner=_runner)
            try:
                account_inventory.set_finding_disposition(conn, "203.0.113.5", "alice", "R2", "亂填")
            except ValueError:
                pass
            else:
                raise AssertionError("未支援狀態應拋 ValueError")
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


def _login_seed(client, p):
    c = db.get_connection(p)
    try:
        db.create_user(c, "tester", auth.hash_password(_PW))
        _seed(c)
        account_inventory.collect_accounts(c, runner=_runner)
    finally:
        c.close()
    client.post("/api/auth/login", json={"username": "tester", "password": _PW})


def test_處置端點記錄覆核人():
    with tempfile.TemporaryDirectory() as tmp:
        client, p = _client(tmp)
        try:
            _login_seed(client, p)
            f = None
            c = db.get_connection(p)
            try:
                f = account_inventory.latest_findings(c)[0]
            finally:
                c.close()
            r = client.put("/api/accounts/findings/disposition", json={
                "ip": f["ip"], "username": f["username"], "rule_id": f["rule_id"],
                "status": "ack"})
            assert r.status_code == 200
            # decided_by 記成登入者
            got = [x for x in client.get("/api/accounts/findings").json()["items"]
                   if x["rule_id"] == f["rule_id"] and x["username"] == f["username"]][0]
            assert got["status"] == "ack" and got["decided_by"] == "tester"
        finally:
            api.app.dependency_overrides.clear()


def test_匯出excel():
    with tempfile.TemporaryDirectory() as tmp:
        client, p = _client(tmp)
        try:
            _login_seed(client, p)
            r = client.get("/api/accounts/findings/export")
            assert r.status_code == 200
            assert "spreadsheetml" in r.headers["content-type"]
            assert r.content[:2] == b"PK"          # xlsx 是 zip
        finally:
            api.app.dependency_overrides.clear()


def test_匯出要登入():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        try:
            assert client.get("/api/accounts/findings/export").status_code == 401
        finally:
            api.app.dependency_overrides.clear()
