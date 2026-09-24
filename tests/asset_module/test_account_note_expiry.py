"""手動備註（跟 gecos 分開、收集不覆蓋）＋密碼到期明講狀態＋findings 帶 gecos/note。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import account_inventory  # noqa: E402
import account_rules as ar  # noqa: E402
import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_PW = "test-password-123"
GROUP = "wheel:x:10:alice\n"
LASTLOG = "SRC=lastlog\nalice pts/0 x Jul 15, 2026\n"
OS_OUT = ("OSID=rocky\nOSVER=9.7\nUIDMIN=1000\nBIN=lastlog:/usr/bin/lastlog\n"
          "BIN=chage:/usr/bin/chage\nBIN=passwd:/usr/bin/passwd\nBIN=sudo:/usr/bin/sudo\n")


def test_密碼到期狀態明講():
    now_change = "Jul 01, 2026"    # 近期
    old_change = "Jan 01, 2020"    # 很久以前
    assert ar.password_expiry_status(
        {"kind": "human", "pw_max_days": "99999", "pw_last_change": now_change}) == "never"
    assert ar.password_expiry_status(
        {"kind": "human", "pw_max_days": "90", "pw_last_change": old_change}) == "expired"
    # 未過期：門檻大、剛改不久（用今天附近的日期較穩，這裡用大門檻確保 valid）
    assert ar.password_expiry_status(
        {"kind": "human", "pw_max_days": "36500", "pw_last_change": now_change}) == "valid"
    assert ar.password_expiry_status(
        {"kind": "human", "pw_max_days": None, "pw_last_change": None}) == "unknown"
    assert ar.password_expiry_status(
        {"kind": "service", "pw_max_days": "90", "pw_last_change": old_change}) == "na"


def _db(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _seed_and_collect(conn):
    db.insert_hardware(conn, asset_serial="A-1", hostname="h", ip="203.0.113.5", collect_ok=1)
    conn.execute("INSERT INTO scan_history (ip, scan_ok, open_ports, scan_time) "
                 "VALUES ('203.0.113.5',1,'22','2026-07-21 10:00:00')")
    conn.commit()
    passwd = "root:x:0:0:root:/root:/bin/bash\nalice:x:1000:1000:Alice Chen:/home/alice:/bin/bash\n"

    def runner(host, cmd):
        if cmd.startswith("cat /etc/passwd"):
            return passwd
        if cmd.startswith("cat /etc/group"):
            return GROUP
        if "os-release" in cmd:
            return OS_OUT
        if "lastlog" in cmd:
            return LASTLOG
        return ""

    account_inventory.collect_accounts(conn, runner=runner)
    return runner


def test_手動備註設定且收集不覆蓋():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _db(tmp)
        try:
            runner = _seed_and_collect(conn)
            account_inventory.set_account_note(conn, "203.0.113.5", "alice", "廠商帳號，2026-12 到期")
            a = {x["username"]: x for x in account_inventory.list_accounts(conn)}
            assert a["alice"]["note"] == "廠商帳號，2026-12 到期"
            assert a["alice"]["gecos"] == "Alice Chen"   # gecos 是另一回事

            # 再收一次，手動備註不被覆蓋
            account_inventory.collect_accounts(conn, runner=runner)
            a2 = {x["username"]: x for x in account_inventory.list_accounts(conn)}
            assert a2["alice"]["note"] == "廠商帳號，2026-12 到期"
        finally:
            conn.close()


def test_list_accounts帶密碼到期狀態():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _db(tmp)
        try:
            _seed_and_collect(conn)
            a = {x["username"]: x for x in account_inventory.list_accounts(conn)}
            assert "pw_expiry_status" in a["alice"]      # 後端算好，畫面直接顯示
        finally:
            conn.close()


def test_findings帶出gecos與note():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _db(tmp)
        try:
            _seed_and_collect(conn)
            account_inventory.set_account_note(conn, "203.0.113.5", "root", "root 直登要覆核")
            f = account_inventory.latest_findings(conn)
            root_f = [x for x in f if x["username"] == "root"]
            assert root_f and root_f[0]["note"] == "root 直登要覆核"
            assert "gecos" in root_f[0]
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


def test_備註端點():
    with tempfile.TemporaryDirectory() as tmp:
        client, p = _client(tmp)
        try:
            assert client.put("/api/accounts/note",
                              json={"ip": "x", "username": "y", "note": "z"}).status_code == 401
            c = db.get_connection(p)
            try:
                db.create_user(c, "tester", auth.hash_password(_PW))
                _seed_and_collect(c)
            finally:
                c.close()
            client.post("/api/auth/login", json={"username": "tester", "password": _PW})
            r = client.put("/api/accounts/note",
                           json={"ip": "203.0.113.5", "username": "alice", "note": "測試備註"})
            assert r.status_code == 200 and r.json()["note"] == "測試備註"
            # 不存在的帳號 404
            assert client.put("/api/accounts/note",
                              json={"ip": "203.0.113.5", "username": "nobody", "note": "x"}
                              ).status_code == 404
        finally:
            api.app.dependency_overrides.clear()
