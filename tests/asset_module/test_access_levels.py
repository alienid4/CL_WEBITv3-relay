"""Q91：三級權限（管理員 admin／一般 user／只能看 viewer）＋匯出紀錄。

守的是「誰能做什麼」，所以 ROLE_AWARE_TESTS：不套 conftest 的「測試帳號預設 admin」相容層，帳號等級全部明確指定。
（CSRF 的測試在 test_csrf_q91.py；這裡的 TestClient 會自動補 X-CSRF-Token，專心測等級。）

重點不變式：
  1. 每支非 GET 端點，viewer 一律被擋（唯讀查詢／個人偏好除外）；user 碰不到 ADMIN 登記的端點。
  2. 「一般」帳號做得到日常工作（標註、處置），做不到收集／納管／匯入／設定／檔案傳送。
  3. 登入停用時，既有功能照舊全開（假 session 是 admin），但檔案傳送仍 fail-closed。
  4. 匯出／下載每一次都留 append-only 紀錄；viewer 不能匯出；大量或含帳號欄位要 admin。
  5. 管理員改等級：立即生效、寫稽核、最後一個管理員與 admin 帳號不能降級。
"""
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import access_control  # noqa: E402
import access_policy as ap  # noqa: E402
import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
from fastapi import Depends  # noqa: E402
from fastapi.responses import Response  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ROLE_AWARE_TESTS = True

_PW = "s3cure-pass!"          # 測試用假密碼（不是真實憑證）
PATH_STUBS = {
    "{issue_id}": "1", "{asset_serial}": "STUB", "{connection_id}": "1", "{module_key}": "topology",
    "{system_id}": "STUB", "{dep_id}": "1", "{snapshot_id}": "1", "{source}": "dynassets", "{api_id}": "N-107",
    "{archive_id}": "1", "{file_id}": "1", "{job_id}": "1", "{log_id}": "1", "{item_id}": "1", "{rid}": "1",
    "{tid}": "1", "{package_id}": "STUB", "{kind}": "account", "{eid}": "1", "{audit_id}": "1", "{doc_id}": "1", "{drift_id}": "1",
    "{decision_id}": "1", "{move_id}": "1", "{run_id}": "1", "{row_id}": "1", "{seg_id}": "1",
    "{alias_id}": "1", "{name}": "STUB", "{key}": "STUB", "{canonical}": "STUB", "{old_canonical}": "STUB",
    "{token}": "STUB", "{username}": "STUB", "{tag}": "STUB",
}


def _url(path):
    for k, v in PATH_STUBS.items():
        path = path.replace(k, v)
    return path


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.delenv(api.AUTH_DISABLED_ENV, raising=False)
    db_path = tmp_path / "t.db"
    db.init_db(db_path)

    def _override_get_db():
        conn = db.get_connection(db_path)
        try:
            yield conn
        finally:
            conn.close()

    api.app.dependency_overrides[api.get_db] = _override_get_db
    conn = db.get_connection(db_path)
    for name, role in (("admin", "admin"), ("alice", "user"), ("vera", "viewer")):
        db.create_user(conn, name, auth.hash_password(_PW), role=role)
    conn.close()

    class E:
        path = db_path

        def client(self, name):
            c = TestClient(api.app)
            r = c.post("/api/auth/login", json={"username": name, "password": _PW})
            assert r.status_code == 200, r.text
            return c

        def conn(self):
            return db.get_connection(db_path)

    yield E()
    api.app.dependency_overrides.clear()


# ------------------------------------------------------------------ 等級判定本身
def test_session_level_規則():
    row_like = lambda **kw: kw                                    # noqa: E731
    assert auth.session_level(row_like(username="x", role="viewer")) == "viewer"
    assert auth.session_level(row_like(username="x", role="user")) == "user"
    assert auth.session_level(row_like(username="x", role="admin")) == "admin"
    # 沒帶 role 的假 session（測試 stub）：只有帳號名 admin 才是 admin，其餘當「一般」（fail-closed）
    assert auth.session_level(row_like(username="admin")) == "admin"
    assert auth.session_level(row_like(username="tester")) == "user"
    assert auth.session_level(row_like(username="x", role="superuser")) == "viewer"   # 不認得的字串給最低
    assert auth.session_level(None) == "viewer"


def test_create_user_預設等級():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, "
                       "password_hash TEXT, role TEXT DEFAULT 'user', created_at TEXT);")
    db.create_user(conn, "admin", "h")
    db.create_user(conn, "bob", "h")
    db.create_user(conn, "cat", "h", role="viewer")
    got = {r["username"]: r["role"] for r in conn.execute("SELECT username, role FROM users")}
    assert got == {"admin": "admin", "bob": "user", "cat": "viewer"}
    with pytest.raises(ValueError):
        db.create_user(conn, "dan", "h", role="root")


def test_既有資料庫遷移_補role欄_admin帳號是admin_其餘是一般_只做一次(tmp_path):
    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    c.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
                            password_hash TEXT NOT NULL, created_at TEXT);
        INSERT INTO users (username, password_hash) VALUES ('admin','h'), ('bob','h');
    """)
    c.commit()
    c.close()
    db.init_db(p)
    c = db.get_connection(p)
    assert {r["username"]: r["role"] for r in c.execute("SELECT username, role FROM users")} == {
        "admin": "admin", "bob": "user"}
    c.execute("UPDATE users SET role='viewer' WHERE username='bob'")
    c.commit()
    c.close()
    db.init_db(p)           # 再啟動一次：管理員調過的等級不能被蓋回去
    c = db.get_connection(p)
    assert c.execute("SELECT role FROM users WHERE username='bob'").fetchone()[0] == "viewer"
    c.close()


# ------------------------------------------------------------------ 矩陣：整張路由表
def _routes():
    out = []
    for r in api.app.routes:
        p = getattr(r, "path", "")
        if p.startswith("/api/"):
            for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
                out.append((m, p))
    return out


def _guard_refused(resp) -> bool:
    return resp.status_code == 403 and "您的帳號權限是" in (resp.text or "")


def test_只能看_碰不到任何需要一般以上的端點(env):
    vera = env.client("vera")
    leaks = []
    for m, p in _routes():
        need = ap.level_for(m, p)
        if need in (None, "viewer"):
            continue
        resp = vera.request(m, _url(p), json={}) if m != "GET" else vera.get(_url(p))
        if not _guard_refused(resp):
            leaks.append(f"{m} {p} (需要 {need}) -> {resp.status_code}")
    assert not leaks, "只能看的帳號沒被擋：\n" + "\n".join(leaks)


def test_一般_碰不到任何admin端點(env):
    alice = env.client("alice")
    leaks = []
    for m, p in _routes():
        if ap.level_for(m, p) != "admin":
            continue
        resp = alice.request(m, _url(p), json={}) if m != "GET" else alice.get(_url(p))
        if not _guard_refused(resp):
            leaks.append(f"{m} {p} -> {resp.status_code}")
    assert not leaks, "一般帳號沒被擋：\n" + "\n".join(leaks)


def test_只能看_可以看_也可以用唯讀查詢(env):
    vera = env.client("vera")
    assert vera.get("/api/auth/me").json()["level"] == "viewer"
    assert vera.get("/api/dashboard/stats").status_code == 200
    r = vera.post("/api/assets/batch-lookup", json={"terms": ["x"]})
    assert r.status_code == 200
    assert vera.put("/api/nav/favorites", json={"items": []}).status_code == 200      # 自己的偏好


def test_一般_日常工作做得到(env):
    alice = env.client("alice")
    assert alice.get("/api/auth/me").json()["level"] == "user"
    # 過了等級閘門就不會是「您的帳號權限是…」的 403（後面 422／404／200 都是端點自己的事）
    for m, p in (("PUT", "/api/accounts/note"), ("PUT", "/api/accounts/findings/disposition"),
                 ("PUT", "/api/reports/scan-gaps/note"), ("POST", "/api/incidents"), ("PUT", "/api/accounts/kind")):
        resp = alice.request(m, p, json={})
        assert not _guard_refused(resp), (m, p, resp.status_code, resp.text[:120])
    # 但：收集觸發、收集身分、匯入、設定、功能開關、備份還原
    for m, p in (("POST", "/api/accounts/collect"), ("PUT", "/api/accounts/collect-config"),
                 ("PUT", "/api/accounts/exclude"), ("POST", "/api/assets/manual"),
                 ("POST", "/api/assets/scan/discover"), ("POST", "/api/assets/scan/import"),
                 ("POST", "/api/collect/agent-package"), ("POST", "/api/import/excel"),
                 ("POST", "/api/backup/restore"), ("PUT", "/api/feature-flags/golive"),
                 ("PUT", "/api/nav-layout"), ("POST", "/api/admin/reset-inventory"),
                 ("POST", "/api/onboard"), ("POST", "/api/transfer/jobs")):
        assert _guard_refused(alice.request(m, p, json={})), (m, p)


def test_admin_全部通過等級閘門(env):
    adm = env.client("admin")
    assert adm.get("/api/auth/me").json()["level"] == "admin"
    for m, p in (("PUT", "/api/accounts/collect-config"), ("PUT", "/api/accounts/exclude"),
                 ("POST", "/api/assets/manual"), ("POST", "/api/collect/agent-package")):
        assert not _guard_refused(adm.request(m, p, json={})), (m, p)
    assert adm.get("/api/access/users").status_code == 200


def test_已知缺口_七支端點現在要admin(env):
    """獨立審查指出這七支原本只有 require_auth：一般登入者就能觸發收集、改收集身分、建資產。"""
    gaps = [("POST", "/api/accounts/collect"), ("PUT", "/api/accounts/collect-config"),
            ("PUT", "/api/accounts/exclude"), ("POST", "/api/assets/manual"),
            ("POST", "/api/assets/scan/discover"), ("POST", "/api/assets/scan/import"),
            ("POST", "/api/collect/agent-package")]
    for g in gaps:
        assert ap.level_for(*g) == "admin", g
    alice = env.client("alice")
    for m, p in gaps:
        assert _guard_refused(alice.request(m, p, json={})), (m, p)


def test_agent_package_檢查IPv4與VMware(env):
    adm = env.client("admin")
    for bad in ("not-an-ip", "10.99.1.999", "fe80::1", "10.099.1.1", "10.99.1.1; rm -rf /"):
        r = adm.post("/api/collect/agent-package", json={"ip": bad})
        assert r.status_code == 400 and "IPv4" in r.json()["detail"], (bad, r.status_code, r.text)
    # VMware 設備：資產已登記為 ESXi → 拒絕，而且不能順手發金鑰或建資產
    c = env.conn()
    db.insert_hardware(c, asset_serial="ESX-1", ip="10.99.7.7", hostname="esx-lab-01", os="VMware ESXi 8.0",
                       device_model="VMware ESXi")
    c.close()
    r = adm.post("/api/collect/agent-package", json={"ip": "10.99.7.7"})
    assert r.status_code == 400 and "VMware" in r.json()["detail"]
    c = env.conn()
    assert c.execute("SELECT COUNT(*) FROM host_api_key").fetchone()[0] == 0      # 沒發金鑰
    assert c.execute("SELECT COUNT(*) FROM hardware").fetchone()[0] == 1          # 沒順手建資產
    c.close()


# ------------------------------------------------------------------ 登入停用
def test_登入停用_既有功能照舊全開_檔案傳送也放行_使用者1030決定(env, monkeypatch):
    monkeypatch.setenv(api.AUTH_DISABLED_ENV, "1")
    anon = TestClient(api.app)
    assert anon.get("/api/dashboard/stats").status_code == 200
    for m, p in (("PUT", "/api/accounts/collect-config"), ("POST", "/api/assets/manual"),
                 ("PUT", "/api/feature-flags/golive")):
        assert not _guard_refused(anon.request(m, p, json={})), (m, p)
    me = anon.get("/api/auth/me").json()
    assert me["level"] == "admin"
    # 檔案傳送：2026-10-30 使用者決定登入停用也放行（原本 fail-closed），此時是假 admin
    c = env.conn()
    c.execute("UPDATE feature_flags SET enabled=1 WHERE module_key='file_transfer'")
    c.commit()
    c.close()
    r = anon.get("/api/transfer/status")
    assert r.status_code == 200 and not _guard_refused(r)


# ------------------------------------------------------------------ 管理等級
def test_管理員改等級_立即生效_並寫稽核(env):
    adm, alice = env.client("admin"), env.client("alice")
    assert _guard_refused(alice.post("/api/accounts/collect", json={}))
    r = adm.put("/api/access/users/alice/level", json={"level": "admin"})
    assert r.status_code == 200 and r.json()["changed"] is True
    assert not _guard_refused(alice.post("/api/accounts/collect", json={}))        # 既有 session，立即生效
    adm.put("/api/access/users/alice/level", json={"level": "viewer"})
    assert _guard_refused(alice.put("/api/accounts/note", json={}))
    audit = adm.get("/api/access/level-audit").json()["items"]
    assert [(a["target"], a["old_level"], a["new_level"], a["actor"]) for a in audit][:2] == [
        ("alice", "admin", "viewer", "admin"), ("alice", "user", "admin", "admin")]
    users = {u["username"]: u for u in adm.get("/api/access/users").json()["items"]}
    assert users["alice"]["level"] == "viewer" and users["admin"]["locked"] is True


def test_改等級_限制(env):
    adm = env.client("admin")
    assert adm.put("/api/access/users/admin/level", json={"level": "user"}).status_code == 400
    assert adm.put("/api/access/users/alice/level", json={"level": "root"}).status_code == 400
    assert adm.put("/api/access/users/nobody/level", json={"level": "user"}).status_code == 400
    # 相同等級：不算異動、不寫稽核
    assert adm.put("/api/access/users/alice/level", json={"level": "user"}).json()["changed"] is False
    assert adm.get("/api/access/level-audit").json()["items"] == []
    # 至少留一個管理員：把 alice 升管理員，再降 alice 沒問題；但只剩一個管理員時不能降
    c = env.conn()
    db.create_user(c, "boss", auth.hash_password(_PW), role="admin")
    c.execute("UPDATE users SET role='user' WHERE username='admin'")     # 模擬：只剩 boss 一個管理員
    c.commit()
    c.close()
    boss = env.client("boss")
    r = boss.put("/api/access/users/boss/level", json={"level": "user"})
    assert r.status_code == 400 and "最後一個管理員" in r.json()["detail"]


def test_一般與只能看_不能開管理介面(env):
    for who in ("alice", "vera"):
        c = env.client(who)
        assert _guard_refused(c.get("/api/access/users")), who
        assert _guard_refused(c.get("/api/access/export-log")), who
        assert _guard_refused(c.get("/api/access/level-audit")), who
        assert _guard_refused(c.put("/api/access/users/alice/level", json={"level": "admin"})), who


# ------------------------------------------------------------------ append-only
@pytest.mark.parametrize("table", ["export_log", "access_level_audit"])
def test_紀錄表只增不改不刪(env, table):
    c = env.conn()
    if table == "export_log":
        access_control.log_export(c, username="u", level="user", ip=None, method="GET", path="/x", name="n",
                                  filename=None, rows=1, summary=None, status=200)
    else:
        c.execute("INSERT INTO access_level_audit (actor, target, new_level) VALUES ('a','b','user')")
        c.commit()
    with pytest.raises(sqlite3.DatabaseError):
        c.execute(f"UPDATE {table} SET id = id")
    with pytest.raises(sqlite3.DatabaseError):
        c.execute(f"DELETE FROM {table}")
    c.close()


# ------------------------------------------------------------------ 匯出紀錄
def _log(env):
    c = env.conn()
    rows = [dict(r) for r in c.execute("SELECT * FROM export_log ORDER BY id")]
    c.close()
    return rows


def test_畫面表格匯出_留紀錄_列數與名稱(env):
    alice = env.client("alice")
    r = alice.post("/api/export/table-xlsx", json={"title": "主機清單", "headers": ["主機", "IP"],
                                                   "rows": [["a", "10.99.1.1"], ["b", "10.99.1.2"]]})
    assert r.status_code == 200
    log = _log(env)
    assert len(log) == 1
    assert (log[0]["username"], log[0]["level"], log[0]["export_name"], log[0]["row_count"], log[0]["status"]) == (
        "alice", "user", "主機清單", 2, 200)
    assert log[0]["filename"].startswith("主機清單_") and log[0]["filename"].endswith(".xlsx")
    assert log[0]["path"] == "/api/export/table-xlsx" and log[0]["method"] == "POST"


def test_只能看_不能匯出_被拒絕也留一筆(env):
    vera = env.client("vera")
    r = vera.post("/api/export/table-xlsx", json={"title": "x", "headers": ["a"], "rows": [["1"]]})
    assert r.status_code == 403
    log = _log(env)
    assert len(log) == 1 and log[0]["status"] == 403 and log[0]["username"] == "vera"
    assert "被拒絕" in log[0]["export_name"]


def test_大量匯出與含帳號欄位要admin(env):
    alice, adm = env.client("alice"), env.client("admin")
    big = {"title": "大表", "headers": ["a"], "rows": [[i] for i in range(access_control.BULK_ROWS + 1)]}
    assert alice.post("/api/export/table-xlsx", json=big).status_code == 403
    acct = {"title": "盤點", "headers": ["主機", "帳號", "UID"], "rows": [["h", "root", 0]]}
    r = alice.post("/api/export/table-xlsx", json=acct)
    assert r.status_code == 403 and "帳號" in r.json()["detail"]
    ok = {"title": "小表", "headers": ["主機"], "rows": [["h"]]}
    assert alice.post("/api/export/table-xlsx", json=ok).status_code == 200
    assert adm.post("/api/export/table-xlsx", json=big).status_code == 200
    assert adm.post("/api/export/table-xlsx", json=acct).status_code == 200


def test_GET下載端點_依登記等級_且留紀錄(env):
    alice, vera, adm = env.client("alice"), env.client("vera"), env.client("admin")
    # user 級下載
    r = alice.get("/api/segments/export-template")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert vera.get("/api/segments/export-template").status_code == 403
    # admin 級下載（帳號資料）：一般帳號不行
    assert alice.get("/api/accounts/export").status_code == 403
    assert adm.get("/api/accounts/export").status_code in (200, 404, 400)   # 通過等級閘門；內容看資料
    log = _log(env)
    ok = [x for x in log if x["status"] == 200 and x["path"] == "/api/segments/export-template"]
    assert ok and ok[0]["username"] == "alice" and ok[0]["level"] == "user" and ok[0]["filename"]
    denied = [x for x in log if x["status"] == 403]
    assert {(x["username"], x["path"]) for x in denied} >= {("vera", "/api/segments/export-template"),
                                                            ("alice", "/api/accounts/export")}


def test_log_viewer_admin_only且看得到紀錄(env):
    alice, adm = env.client("alice"), env.client("admin")
    alice.post("/api/export/table-xlsx", json={"title": "t", "headers": ["h"], "rows": [["1"]]})
    items = adm.get("/api/access/export-log").json()["items"]
    assert items and items[0]["username"] == "alice"
    assert _guard_refused(alice.get("/api/access/export-log"))


def test_沒登記的下載端點_只能看的帳號最後防線仍擋住_一般則放行並留紀錄(env):
    async def _file():
        return Response(b"x", media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="z.csv"'})

    api.app.add_api_route("/api/_q91_unregistered_dl", _file, methods=["GET"], dependencies=[Depends(api.require_auth)])
    try:
        assert TestClient(api.app).get("/api/_q91_unregistered_dl").status_code == 401
        vera, alice = env.client("vera"), env.client("alice")
        r = vera.get("/api/_q91_unregistered_dl")
        assert r.status_code == 403 and "只能看" in r.json()["detail"]
        assert alice.get("/api/_q91_unregistered_dl").status_code == 200
        log = [x for x in _log(env) if x["path"] == "/api/_q91_unregistered_dl"]
        assert [(x["username"], x["status"]) for x in log] == [("vera", 403), ("alice", 200)]
    finally:
        api.app.router.routes[:] = [r for r in api.app.router.routes
                                    if getattr(r, "path", "") != "/api/_q91_unregistered_dl"]


# ------------------------------------------------------------------ 登記簿本身
def test_登記簿_沒有重複_沒有過期_沒有漏登(env):
    routes = set(_routes())
    nonget = {x for x in routes if x[0] != "GET"}
    reg = ap.all_registered_writes()
    exempt = ap.PUBLIC_ROUTES | ap.AGENT_ROUTES
    exports = set(ap.EXPORT_LEVEL)
    assert not (nonget - reg - exempt - exports), f"沒登記的非 GET 端點：{sorted(nonget - reg - exempt - exports)}"
    assert not (reg - routes), f"登記了但不存在的端點：{sorted(reg - routes)}"
    assert not (exports - routes), f"EXPORT_LEVEL 登記了但不存在：{sorted(exports - routes)}"
    assert not (exempt - routes)
    assert not (ap.ADMIN_WRITE & ap.USER_WRITE) and not (ap.ADMIN_WRITE & ap.VIEWER_WRITE) \
        and not (ap.USER_WRITE & ap.VIEWER_WRITE), "同一支端點登記在兩個等級"
    assert not (reg & exports), "同一支端點同時登記成寫入與匯出"


# ------------------------------------------------------------------ 2.014.3 獨立審查補強
def test_H1_helper回檔案的端點也登記了_一般帳號拿不到(env):
    alice = env.client("alice")
    for path in ("/api/account-audit/export", "/api/account-audit/template", "/api/rack-diagram/export.xlsx",
                 "/api/rack-diagram/imports/1/cia-fix.xlsx", "/api/rack-audits/1/report.xlsx"):
        r = alice.get(path)
        assert _guard_refused(r), (path, r.status_code)


def test_H1_執行期掃描_沒有任何GET路由能讓只能看的帳號拿到檔案(env):
    """用只能看的帳號呼叫所有 GET（路徑參數用替代值）。若有未登記的下載端點真的回了檔案，
    下載中介層會擋下並寫一筆「被拒絕：只能看的帳號不能下載」——那一筆就是漏登記的證據。"""
    vera = env.client("vera")
    for m, p in _routes():
        if m == "GET" and ap.level_for(m, p) == "viewer":
            vera.get(_url(p))
    c = env.conn()
    rows = [r["path"] for r in c.execute("SELECT path FROM export_log WHERE export_name LIKE '%只能看的帳號不能下載%'")]
    c.close()
    assert not rows, f"這些端點回了檔案卻沒登記 EXPORT_LEVEL：{rows}"


def test_M2_假cookie的被拒匯出不寫紀錄(env):
    cl = TestClient(api.app)
    cl.cookies.set("session_token", "bogus")
    for _ in range(5):
        cl.get("/api/accounts/export")
    c = env.conn()
    assert c.execute("SELECT COUNT(*) FROM export_log").fetchone()[0] == 0
    c.close()
    env.client("vera").get("/api/accounts/export")        # 真的使用者被拒絕：照樣留一筆
    c = env.conn()
    assert c.execute("SELECT COUNT(*) FROM export_log").fetchone()[0] == 1
    c.close()


def test_M5_cmdb_pull要admin():
    assert ap.level_for("GET", "/api/cmdb/pull") == "admin"


def test_M4_帳號清冊JSON_只能看的帳號不能讀_一般可以(env):
    vera, alice = env.client("vera"), env.client("alice")
    for p in ("/api/accounts", "/api/accounts/findings", "/api/accounts/hosts", "/api/accounts/sudo-rules",
              "/api/accounts/by-username", "/api/accounts/inventoried-hosts"):
        assert _guard_refused(vera.get(p)), p
        assert not _guard_refused(alice.get(p)), p
    assert vera.get("/api/assets").status_code == 200           # 資產清單 viewer 仍可讀


def test_M4_大量JSON讀取寫bulk_json紀錄_小量不寫(env, monkeypatch):
    monkeypatch.setitem(api._BULK_JSON_THRESHOLD, "/api/assets", 2)
    c = env.conn()
    for i in range(5):
        db.insert_hardware(c, asset_serial=f"S{i}", ip=f"10.99.9.{i + 1}", hostname=f"h{i}")
    c.close()
    alice = env.client("alice")
    r = alice.get("/api/assets")
    assert r.status_code == 200 and len(r.json()) > 2
    c = env.conn()
    rows = [dict(x) for x in c.execute("SELECT * FROM export_log WHERE export_name='bulk-json'")]
    c.close()
    assert len(rows) == 1 and rows[0]["username"] == "alice" and rows[0]["row_count"] >= 5
    monkeypatch.setitem(api._BULK_JSON_THRESHOLD, "/api/assets", 5000)
    alice.get("/api/assets")
    c = env.conn()
    assert c.execute("SELECT COUNT(*) FROM export_log WHERE export_name='bulk-json'").fetchone()[0] == 1
    c.close()


def test_L8_紀錄寫入失敗_下載不放行(env, monkeypatch):
    alice = env.client("alice")
    assert alice.get("/api/segments/export-template").status_code == 200

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(access_control, "log_export", boom)
    r = alice.get("/api/segments/export-template")
    assert r.status_code == 500 and "下載紀錄寫入失敗" in r.json()["detail"]
    assert "content-disposition" not in r.headers


def test_未登記的非GET_執行期當admin(env):
    assert ap.level_for("POST", "/api/_brand_new_endpoint") == "admin"
    assert ap.level_for("GET", "/api/_brand_new_read") == "viewer"
    assert ap.level_for("PUT", "/api/accounts/note") == "user"
    assert ap.level_for("POST", "/api/auth/login") is None


def test_事件_viewer不能建_user要明選性質才建得起來(env):
    """Codex 21 號 #5：建事件 API 沿用權限；性質（正式／演練）必選，沒給＝400。"""
    body = {"type": "主機", "title": "權限測試", "category": "incident"}
    assert _guard_refused(env.client("vera").post("/api/incidents", json={**body, "mode": "formal"}))
    alice = env.client("alice")
    assert alice.post("/api/incidents", json=body).status_code == 400                       # 缺 mode
    assert alice.post("/api/incidents", json={**body, "mode": "亂填"}).status_code == 400
    r = alice.post("/api/incidents", json={**body, "mode": "drill"})
    assert r.status_code == 200 and r.json()["mode"] == "drill" and r.json()["mode_label"] == "演練"
