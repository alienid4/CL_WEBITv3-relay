"""帳號儀表板依 OS 分頁（2026-10-21 使用者：「3-7 帳號儀表板 這要分 WINDOWS 跟 LINUX」）。

釘住：
- 每台只歸一個 OS 桶：收集時回報的 os_family 優先，沒有才退回資產庫 OS，兩邊都不知道＝unknown（照樣計數）
- 各桶台數／帳號數／稽核摘要相加＝全部（加總＝下鑽）
- ?os= 篩選端點；不認得的值擋 400
- OCP 節點（RHCOS 會回報成 rhel）歸 OCP 桶、不算進 Linux；退役的不算（2026-10-21 使用者：「OCP 的 OS 也是 rhel 要小心」）
- Windows 的「曾登入」沒走 SAMR＝查不到，不可以寫成「有」
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


def _acc(name, **kw):
    a = {"username": name, "uid": 1000, "kind": "human", "can_login": True,
         "pw_status": "set", "is_sudoer": False, "sudo_nopasswd": False,
         "never_logged_in": False, "authorized_keys": 0}
    a.update(kw)
    return a


def _seed(conn):
    """Linux 2 台、Windows 1 台、AIX 1 台、OS 未知 1 台、
    舊資料沒 os_family 但資產庫寫 Windows 的 1 台（要退回資產庫判成 Windows）。"""
    db.insert_hardware(conn, asset_serial="L-1", hostname="lnx1", ip="203.0.113.1", os="Red Hat Enterprise Linux 8")
    db.insert_hardware(conn, asset_serial="L-2", hostname="lnx2", ip="203.0.113.2", os="Ubuntu 22.04")
    db.insert_hardware(conn, asset_serial="W-1", hostname="win1", ip="203.0.113.3", os="Windows Server 2019")
    db.insert_hardware(conn, asset_serial="W-2", hostname="win2", ip="203.0.113.6", os="Windows Server 2016")
    ai = account_inventory
    ai.upsert_accounts(conn, "203.0.113.1", "L-1", [
        _acc("root", uid=0, is_sudoer=True), _acc("alice", is_sudoer=True, sudo_nopasswd=True),
        _acc("bob", authorized_keys=2)], "ssh", os_info={"family": "rhel"})
    ai.upsert_accounts(conn, "203.0.113.2", "L-2", [_acc("carol")], "ssh", os_info={"family": "debian"})
    ai.upsert_accounts(conn, "203.0.113.3", "W-1", [
        _acc("Administrator", uid=None, is_sudoer=True, authorized_keys=None, login_source="samr"),
        _acc("svc", uid=None, kind="service", authorized_keys=None, login_source=None),
        _acc("old", uid=None, can_login=False, pw_status="locked", authorized_keys=None, login_source="samr")],
        "winrm", os_info={"family": "windows"})
    ai.upsert_accounts(conn, "203.0.113.4", None, [_acc("aixusr")], "ssh", os_info={"family": "aix"})
    # 沒登記、收集器也認不出來 → OS 未知
    ai.upsert_accounts(conn, "203.0.113.5", None, [_acc("who")], "ssh", os_info={"family": "unknown"})
    # 舊資料（os_family 是 NULL）→ 退回資產庫：Windows
    ai.upsert_accounts(conn, "203.0.113.6", "W-2", [_acc("legacy", uid=None)], "winrm", os_info=None)

    run = conn.execute("INSERT INTO account_collect_runs (trigger, status, started_at) "
                       "VALUES ('test', 'ok', '2026-10-21 10:00:00')").lastrowid
    for ip, sev, verdict in [("203.0.113.1", "high", "fail"), ("203.0.113.1", "medium", "fail"),
                             ("203.0.113.3", "high", "fail"), ("203.0.113.5", "medium", "unknown")]:
        account_inventory._insert_findings(conn, run, ip, None, [{
            "username": "x", "rule_id": "A1", "label": "t", "severity": sev,
            "verdict": verdict, "law": "", "detail": ""}])
    conn.commit()


def _db(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p), p


def test_每台只歸一個OS桶_收集回報優先_退回資產庫_判不出來算未知():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _db(tmp)
        try:
            _seed(conn)
            b = account_inventory.host_os_buckets(conn)
            assert b == {"203.0.113.1": "linux", "203.0.113.2": "linux", "203.0.113.3": "windows",
                         "203.0.113.4": "aix", "203.0.113.5": "unknown", "203.0.113.6": "windows"}
            counts = account_inventory.os_bucket_counts(b)
            assert counts == {"linux": 2, "windows": 2, "aix": 1, "ocp": 0, "unknown": 1}
            hosts = account_inventory.inventoried_hosts(conn)
            assert sum(counts.values()) == len(hosts)        # OS 未知沒有被默默丟掉
            assert {h["ip"]: h["os_bucket"] for h in hosts} == b
        finally:
            conn.close()


def test_各OS桶帳號數與稽核摘要相加等於全部():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _db(tmp)
        try:
            _seed(conn)
            ai = account_inventory
            per = {o: ai.list_accounts(conn, os_bucket=o) for o in ai.OS_BUCKETS}
            assert {o: len(v) for o, v in per.items()} == {"linux": 4, "windows": 4, "aix": 1, "ocp": 0, "unknown": 1}
            assert sum(len(v) for v in per.values()) == len(ai.list_accounts(conn))
            assert all(r["os_bucket"] == "windows" for r in per["windows"])

            full = ai.audit_summary(conn)
            parts = {o: ai.audit_summary(conn, o) for o in ai.OS_BUCKETS}
            assert (parts["linux"]["fail_high"], parts["linux"]["fail_medium"]) == (1, 1)
            assert parts["windows"]["fail_high"] == 1
            assert parts["unknown"]["unknown"] == 1          # 查不到也跟著 OS 走
            for k in ("fail_high", "fail_medium", "fail_low", "unknown", "accounts", "privileged", "humans"):
                assert sum(p[k] for p in parts.values()) == full[k], k
            # Windows 特權＝本機管理員（is_sudoer），沒有 UID 0 混進來
            assert parts["windows"]["privileged"] == 1
            assert parts["linux"]["privileged"] == 2          # root(UID 0) + alice(sudo)
        finally:
            conn.close()


def test_Windows曾登入_沒走SAMR是查不到不是有():
    cell = account_inventory._cell_login
    assert cell({"os_family": "windows", "never_logged_in": 0, "login_source": None}) == "查不到"
    assert cell({"os_family": "windows", "never_logged_in": 0, "login_source": "samr"}) == "有"
    assert cell({"os_family": "windows", "never_logged_in": 1}) == "從未登入"
    assert cell({"os_family": "rhel", "never_logged_in": 0}) == "有"


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
    client = TestClient(api.app)
    c = db.get_connection(p)
    try:
        db.create_user(c, "tester", auth.hash_password(_PW))
        _seed(c)
    finally:
        c.close()
    assert client.post("/api/auth/login", json={"username": "tester", "password": _PW}).status_code == 200
    return client


def test_端點吃os參數_分頁台數永遠是全部分佈_不認得的值擋400():
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(tmp)
        try:
            h = client.get("/api/accounts/inventoried-hosts").json()
            assert h["os_counts"] == {"linux": 2, "windows": 2, "aix": 1, "ocp": 0, "unknown": 1}
            assert h["total"] == 6
            hw = client.get("/api/accounts/inventoried-hosts", params={"os": "windows"}).json()
            assert {x["ip"] for x in hw["items"]} == {"203.0.113.3", "203.0.113.6"}
            assert hw["os_counts"] == h["os_counts"]          # 標籤台數不跟著篩

            fw = client.get("/api/accounts/findings", params={"os": "windows"}).json()
            assert [f["ip"] for f in fw["items"]] == ["203.0.113.3"]
            assert fw["summary"]["fail_high"] == 1 and fw["summary"]["os_bucket"] == "windows"
            fu = client.get("/api/accounts/findings", params={"os": "unknown"}).json()
            assert fu["summary"]["unknown"] == 1
            fa = client.get("/api/accounts/findings").json()
            assert len(fa["items"]) == 4 and all("os_bucket" in f for f in fa["items"])

            aw = client.get("/api/accounts", params={"os": "windows"}).json()
            assert {a["username"] for a in aw["items"]} == {"Administrator", "svc", "old", "legacy"}
            assert client.get("/api/accounts", params={"os": "macos"}).status_code == 400
            assert client.get("/api/accounts/findings", params={"os": "x"}).status_code == 400
            assert client.get("/api/accounts/inventoried-hosts", params={"os": "x"}).status_code == 400
        finally:
            api.app.dependency_overrides.clear()


def test_verify_numbers有OS分頁不變式且在真資料上通過():
    """新增有數字的頁面要在 verify_numbers 補不變式（專案硬規則）。"""
    import verify_numbers as vn

    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _db(tmp)
        try:
            _seed(conn)
            before = vn.FAILS
            vn.check_account_os_split(conn)
            assert vn.FAILS == before
        finally:
            conn.close()


def _seed_drill_extras(conn):
    """卡片＝下鑽要驗的邊角：標準管理帳號（不是 sudoer 也算特權）、核准例外（不列也不算）、
    Windows 主機上的「查不到」發現。"""
    account_inventory.upsert_accounts(conn, "203.0.113.2", "L-2", [
        _acc("carol"), _acc("webit3", kind="mgmt")], "ssh", os_info={"family": "debian"})
    account_inventory.upsert_accounts(conn, "203.0.113.3", "W-1", [
        _acc("Administrator", uid=None, is_sudoer=True, authorized_keys=None, login_source="samr"),
        _acc("svc", uid=None, kind="service", authorized_keys=None, login_source=None),
        _acc("old", uid=None, can_login=False, pw_status="locked", authorized_keys=None, login_source="samr"),
        _acc("wmgmt", uid=None, kind="mgmt", authorized_keys=None, login_source="samr")],
        "winrm", os_info={"family": "windows"})
    run = conn.execute("SELECT MAX(id) FROM account_collect_runs").fetchone()[0]
    for ip, user, rule, sev, verdict in [("203.0.113.3", "svc", "A9", "low", "unknown"),
                                        ("203.0.113.2", "carol", "A2", "high", "fail")]:
        account_inventory._insert_findings(conn, run, ip, None, [{
            "username": user, "rule_id": rule, "label": "t", "severity": sev,
            "verdict": verdict, "law": "", "detail": ""}])
    conn.commit()
    # 核准例外（未到期）：稽核發現清單不列 → 卡片也不能算
    account_inventory.set_finding_disposition(
        conn, "203.0.113.2", "carol", "A2", "exception", note="test",
        exempt_until=(__import__("datetime").datetime.now() + __import__("datetime").timedelta(days=300)).strftime("%Y-%m-%d"),
        decided_by="t")


def test_卡片數字等於下鑽筆數_查不到與特權_每個OS分頁():
    """使用者規則：點進去的清單筆數＝卡片上的數字。
    - 查不到（權限不足）→ 稽核發現 verdict=unknown（含 Windows 主機）
    - 高／中風險 → 稽核發現 verdict=fail＋severity
    - 特權帳號 → /api/accounts?privileged=true（含標準管理帳號）"""
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(tmp)
        try:
            p = Path(tmp) / "t.db"
            c = db.get_connection(p)
            try:
                _seed_drill_extras(c)
            finally:
                c.close()
            for os_ in (None, *account_inventory.OS_BUCKETS):
                q = {"os": os_} if os_ else {}
                f = client.get("/api/accounts/findings", params=q).json()
                s, items = f["summary"], f["items"]
                assert s["unknown"] == sum(1 for x in items if x["verdict"] == "unknown"), os_
                for sev, key in (("high", "fail_high"), ("medium", "fail_medium")):
                    drill = client.get("/api/accounts/findings", params={**q, "severity": sev}).json()["items"]
                    assert s[key] == sum(1 for x in drill if x["verdict"] == "fail"), (os_, sev)
                priv = client.get("/api/accounts", params={**q, "privileged": "true"}).json()["items"]
                assert s["privileged"] == len(priv), os_
            # 邊角真的有被驗到：Windows 有查不到、標準管理帳號算進特權清單、例外不算
            fw = client.get("/api/accounts/findings", params={"os": "windows"}).json()["summary"]
            assert fw["unknown"] == 1
            pw = client.get("/api/accounts", params={"os": "windows", "privileged": "true"}).json()["items"]
            assert {a["username"] for a in pw} == {"Administrator", "wmgmt"}
            pl = client.get("/api/accounts", params={"os": "linux", "privileged": "true"}).json()["items"]
            assert "webit3" in {a["username"] for a in pl}
            fl = client.get("/api/accounts/findings", params={"os": "linux"}).json()["summary"]
            assert fl["fail_high"] == 1                      # carol 的 A2 是核准例外，不算
        finally:
            api.app.dependency_overrides.clear()


def test_OCP節點回報rhel也歸OCP桶_手動指定也算_退役不算_加總不變():
    import os_override
    import pipeline

    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _db(tmp)
        try:
            _seed(conn)
            ai = account_inventory
            # 登記 OS 是 RHCOS、收集器回報 rhel → 要歸 OCP，不可以算成 Linux
            db.insert_hardware(conn, asset_serial="O-1", hostname="ocp-w1", ip="203.0.113.7", os="RHCOS 4.12")
            ai.upsert_accounts(conn, "203.0.113.7", "O-1", [_acc("core")], "ssh", os_info={"family": "rhel"})
            # 登記只寫 RHEL，但有人手動指定成 OpenShift 節點 → 也歸 OCP
            db.insert_hardware(conn, asset_serial="O-2", hostname="ocp-m1", ip="203.0.113.8", os="RHEL 8")
            ai.upsert_accounts(conn, "203.0.113.8", "O-2", [_acc("core")], "ssh", os_info={"family": "rhel"})
            os_override.set_override(conn, "ocp-m1", "203.0.113.8", "O-2", pipeline.OS_OPENSHIFT,
                                     "Linux", "test", "tester")
            # 退役的 OCP 登記，IP 已給一般 RHEL 用 → 不算 OCP
            db.insert_hardware(conn, asset_serial="O-OLD", hostname="ocp-old", ip="203.0.113.1", os="CoreOS 4.10")
            conn.execute("UPDATE hardware SET asset_status = ? WHERE asset_serial = 'O-OLD'",
                         (sorted(__import__("manage_state").RETIRED_STATUS)[0],))
            # 登記寫 CoreOS，但有人手動改成 Linux（人工指定有最終決定權，跟漏斗一樣）→ 不算 OCP
            db.insert_hardware(conn, asset_serial="O-3", hostname="notocp", ip="203.0.113.9", os="CoreOS 4.12")
            ai.upsert_accounts(conn, "203.0.113.9", "O-3", [_acc("u")], "ssh", os_info={"family": "rhel"})
            os_override.set_override(conn, "notocp", "203.0.113.9", "O-3", "Linux",
                                     pipeline.OS_OPENSHIFT, "test", "tester")
            conn.commit()

            b = ai.host_os_buckets(conn)
            assert b["203.0.113.9"] == "linux"
            assert b["203.0.113.7"] == "ocp" and b["203.0.113.8"] == "ocp"
            assert b["203.0.113.1"] == "linux"
            counts = ai.os_bucket_counts(b)
            assert counts["ocp"] == 2 and counts["linux"] == 3
            assert sum(counts.values()) == len(ai.inventoried_hosts(conn))
            per = {o: len(ai.list_accounts(conn, os_bucket=o)) for o in ai.OS_BUCKETS}
            assert per["ocp"] == 2 and sum(per.values()) == len(ai.list_accounts(conn))
        finally:
            conn.close()
