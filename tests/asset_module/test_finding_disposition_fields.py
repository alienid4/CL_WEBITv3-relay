"""#7：finding_disposition 新增 fixed_at / verified_at / verify_due / ref_url。

守的規矩：欄位全可空、無預設（尤其 verify_due 不自動 +N 天）；URL 只收 http/https；
舊庫升級安全、遷移可重跑、舊列讀取相容。
"""
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import account_inventory as ai  # noqa: E402
import db  # noqa: E402

NEW_COLS = ("fixed_at", "verified_at", "verify_due", "ref_url")

OLD_SCHEMA = """
CREATE TABLE finding_disposition (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip TEXT NOT NULL, username TEXT NOT NULL, rule_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open', note TEXT, exempt_until TEXT,
    decided_by TEXT, decided_at TEXT, UNIQUE(ip, username, rule_id));
"""


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _row(conn, ip="10.99.0.1"):
    return conn.execute("SELECT * FROM finding_disposition WHERE ip=?", (ip,)).fetchone()


def _set(conn, status="fixed", **kw):
    return ai.set_finding_disposition(conn, "10.99.0.1", "alice", "R1", status, **kw)


def _cols(conn):
    return {r[1] for r in conn.execute("PRAGMA table_info(finding_disposition)")}


# ---- schema / 遷移 ----

def test_新庫有四個新欄位且預設NULL(tmp_path):
    conn = _conn(tmp_path)
    try:
        assert set(NEW_COLS) <= _cols(conn)
        _set(conn, "ack", note="x")
        r = _row(conn)
        assert all(r[c] is None for c in NEW_COLS)       # 沒有任何預設值
    finally:
        conn.close()


def test_舊schema升級_補欄_舊列不動_重跑冪等(tmp_path):
    conn = _conn(tmp_path)                       # 完整 schema，再把這張表換回舊版（沒有新欄位）
    conn.execute("DROP TABLE finding_disposition")
    conn.executescript(OLD_SCHEMA)
    conn.execute("INSERT INTO finding_disposition (ip,username,rule_id,status,note,decided_by,decided_at)"
                 " VALUES ('10.99.0.9','bob','R2','fixed','舊的','admin','2026-10-01 10:00:00')")
    conn.commit()
    assert not set(NEW_COLS) & _cols(conn)
    db._migrate(conn)
    assert set(NEW_COLS) <= _cols(conn)
    old = conn.execute("SELECT * FROM finding_disposition").fetchone()
    assert old["status"] == "fixed" and old["note"] == "舊的" and old["decided_at"] == "2026-10-01 10:00:00"
    assert all(old[c] is None for c in NEW_COLS)         # 不回填、不替舊列發明預設期限
    before = _cols(conn)
    db._migrate(conn)                                     # 重跑不炸、不重複
    db._migrate(conn)
    assert _cols(conn) == before
    assert conn.execute("SELECT COUNT(*) FROM finding_disposition").fetchone()[0] == 1
    conn.close()


def test_完全沒有該表_init_db建出來_migrate無表不炸_latest_findings不崩(tmp_path):
    conn = _conn(tmp_path)
    conn.execute("DROP TABLE finding_disposition")
    conn.commit()
    db._migrate(conn)                               # 沒有該表：略過、不炸
    assert "finding_disposition" not in {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    conn.close()
    p = tmp_path / "t.db"
    db.init_db(p)                                   # 啟動時 schema.sql 會把表建回來（含新欄位）
    db.init_db(p)                                   # 重跑冪等
    conn = db.get_connection(p)
    try:
        assert set(NEW_COLS) <= _cols(conn)
        assert ai.latest_findings(conn) == []
    finally:
        conn.close()


def test_舊庫經init_db升級_資料保留_latest_findings不崩(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    conn = db.get_connection(p)
    conn.execute("INSERT INTO account_collect_runs (status) VALUES ('ok')")
    rid = conn.execute("SELECT MAX(id) FROM account_collect_runs").fetchone()[0]
    cols = {r[1] for r in conn.execute("PRAGMA table_info(account_finding)")}
    vals = {k: v for k, v in {"run_id": rid, "ip": "10.99.0.1", "username": "alice", "rule_id": "R1",
                              "severity": "high", "verdict": "fail"}.items() if k in cols}
    conn.execute(f"INSERT INTO account_finding ({','.join(vals)}) VALUES ({','.join('?' * len(vals))})",
                 tuple(vals.values()))
    conn.execute("DROP TABLE finding_disposition")
    conn.executescript(OLD_SCHEMA)                  # 真正的舊 schema：有表、缺四個新欄位
    conn.execute("INSERT INTO finding_disposition (ip,username,rule_id,status,note) "
                 "VALUES ('10.99.0.1','alice','R1','ack','舊備註')")
    conn.commit()
    assert not set(NEW_COLS) & _cols(conn)
    conn.close()
    db.init_db(p)
    db.init_db(p)
    conn = db.get_connection(p)
    try:
        assert set(NEW_COLS) <= _cols(conn)
        f = ai.latest_findings(conn)
        assert f and f[0]["status"] == "ack" and f[0]["disp_note"] == "舊備註"
        assert all(f[0][c] is None for c in NEW_COLS)
    finally:
        conn.close()


def test_舊列讀取相容_latest_findings帶新欄位為None(tmp_path):
    conn = _conn(tmp_path)
    try:
        conn.execute("INSERT INTO account_collect_runs (status) VALUES ('ok')")
        rid = conn.execute("SELECT MAX(id) FROM account_collect_runs").fetchone()[0]
        cols = {r[1] for r in conn.execute("PRAGMA table_info(account_finding)")}
        vals = {"run_id": rid, "ip": "10.99.0.1", "username": "alice", "rule_id": "R1",
                "severity": "high", "verdict": "fail"}
        vals = {k: v for k, v in vals.items() if k in cols}
        conn.execute(f"INSERT INTO account_finding ({','.join(vals)}) VALUES ({','.join('?' * len(vals))})",
                     tuple(vals.values()))
        # 模擬舊列：只填舊欄位
        conn.execute("INSERT INTO finding_disposition (ip,username,rule_id,status,decided_by)"
                     " VALUES ('10.99.0.1','alice','R1','fixed','admin')")
        conn.commit()
        f = ai.latest_findings(conn)
        assert f and f[0]["status"] == "fixed"
        assert all(f[0][c] is None for c in NEW_COLS)
        _set(conn, "fixed", fixed_at="2026-10-30", ref_url="https://itsm.example.test/T-1")
        f = ai.latest_findings(conn)
        assert f[0]["fixed_at"] == "2026-10-30" and f[0]["ref_url"] == "https://itsm.example.test/T-1"
    finally:
        conn.close()


# ---- 欄位語意 ----

def test_空值與空字串都存NULL_verify_due不自動設定(tmp_path):
    conn = _conn(tmp_path)
    try:
        _set(conn, "fixed", fixed_at="", verified_at="  ", verify_due=None, ref_url="")
        r = _row(conn)
        assert all(r[c] is None for c in NEW_COLS)
    finally:
        conn.close()


def test_四欄位存入_日期與日期時間格式正規化(tmp_path):
    conn = _conn(tmp_path)
    try:
        _set(conn, "fixed", fixed_at="2026-10-30T09:30", verified_at="2026-10-31 08:00:05",
             verify_due="2026-11-30", ref_url="http://itsm.example.test/a?b=1")
        r = _row(conn)
        assert r["fixed_at"] == "2026-10-30 09:30:00"
        assert r["verified_at"] == "2026-10-31 08:00:05"
        assert r["verify_due"] == "2026-11-30"
        assert r["ref_url"] == "http://itsm.example.test/a?b=1"
    finally:
        conn.close()


def test_帶時區的時間換成本機時間(tmp_path):
    conn = _conn(tmp_path)
    try:
        _set(conn, "fixed", fixed_at="2026-10-30T01:00:00Z")
        assert _row(conn)["fixed_at"] == "2026-10-30 09:00:00"          # 固定 UTC+8
        _set(conn, "fixed", fixed_at="2026-10-30T09:00:00+08:00")
        assert _row(conn)["fixed_at"] == "2026-10-30 09:00:00"
        _set(conn, "fixed", fixed_at="2026-10-29T20:00:00-05:00")
        assert _row(conn)["fixed_at"] == "2026-10-30 09:00:00"
    finally:
        conn.close()


@pytest.mark.parametrize("field,bad", [
    ("fixed_at", "昨天"), ("fixed_at", "2026-13-40"), ("verified_at", "10/30"),
    ("verify_due", "next week"),
])
def test_時間格式錯誤被拒(tmp_path, field, bad):
    conn = _conn(tmp_path)
    try:
        with pytest.raises(ValueError, match="格式錯誤"):
            _set(conn, "fixed", **{field: bad})
        assert _row(conn) is None                         # 沒寫入半套
    finally:
        conn.close()


def test_修復時間不可晚於現在太久(tmp_path):
    conn = _conn(tmp_path)
    try:
        far = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")
        with pytest.raises(ValueError, match="不可晚於現在"):
            _set(conn, "fixed", fixed_at=far)
        with pytest.raises(ValueError, match="不可晚於現在"):
            _set(conn, "fixed", verified_at=far)
        # 一天內的時鐘誤差可接受
        ok = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")
        _set(conn, "fixed", fixed_at=ok)
    finally:
        conn.close()


def test_複驗不可早於修復_期限不可早於修復日(tmp_path):
    conn = _conn(tmp_path)
    try:
        with pytest.raises(ValueError, match="不可早於修復時間"):
            _set(conn, "fixed", fixed_at="2026-10-30 10:00", verified_at="2026-10-30 09:00")
        with pytest.raises(ValueError, match="不可早於修復時間"):
            _set(conn, "fixed", fixed_at="2026-10-30", verify_due="2026-10-01")
        # 只填其中一個不互相檢查；複驗期限可以是遠期（不發明上限）
        _set(conn, "fixed", verify_due="2030-01-01")
    finally:
        conn.close()


def test_非已修復狀態清掉修復與複驗時間_重開不殘留(tmp_path):
    conn = _conn(tmp_path)
    try:
        _set(conn, "fixed", fixed_at="2026-10-30", verified_at="2026-10-31", verify_due="2026-12-01",
             ref_url="https://itsm.example.test/T-2")
        r = _row(conn)
        assert r["fixed_at"] and r["verified_at"] and r["verify_due"]
        _set(conn, "open", note="復發", fixed_at="2026-10-30")   # 重開：就算帶了也清掉（文件化行為）
        r = _row(conn)
        assert r["status"] == "open"
        assert r["fixed_at"] is None and r["verified_at"] is None and r["verify_due"] is None
        assert r["ref_url"] == "https://itsm.example.test/T-2"   # 沒帶連結＝保留
        _set(conn, "open", ref_url=None)
        assert _row(conn)["ref_url"] is None                     # 明確帶 None＝清除
        _set(conn, "ack", note="n", ref_url="https://itsm.example.test/T-3")
        assert _row(conn)["ref_url"] == "https://itsm.example.test/T-3"  # 非 fixed 也可附連結
    finally:
        conn.close()


@pytest.mark.parametrize("bad", [
    "javascript:alert(1)", "JavaScript:alert(1)", "data:text/html,<b>x</b>", "ftp://x.example.test/a",
    "file:///etc/passwd", "//evil.example.test/x", "example.test/path", "https://", "http:///x",
    "https://u:p@example.test/x", "https://example.test/a b", "https://example.test/a\nb", "mailto:a@b.c",
])
def test_無效或危險URL被拒(tmp_path, bad):
    conn = _conn(tmp_path)
    try:
        with pytest.raises(ValueError):
            _set(conn, "fixed", ref_url=bad)
        assert _row(conn) is None
    finally:
        conn.close()


def test_URL長度上限500(tmp_path):
    conn = _conn(tmp_path)
    try:
        base = "https://itsm.example.test/"
        ok = base + "a" * (500 - len(base))
        _set(conn, "ack", ref_url=ok)
        assert _row(conn)["ref_url"] == ok
        with pytest.raises(ValueError, match="過長"):
            _set(conn, "ack", ref_url=ok + "a")
    finally:
        conn.close()


def test_既有例外規則不受影響(tmp_path):
    conn = _conn(tmp_path)
    try:
        with pytest.raises(ValueError, match="理由"):
            _set(conn, "exception", exempt_until=(datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d"))
        _set(conn, "exception", note="n",
             exempt_until=(datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d"),
             fixed_at="2026-10-30")                           # 例外狀態帶 fixed_at 會被清掉
        assert _row(conn)["fixed_at"] is None
    finally:
        conn.close()


# ---- API ----

def test_API接受新欄位_無效URL回400_讀回新欄位(tmp_path):
    import api
    import test_finding_lifecycle as L

    client, p = L._client(str(tmp_path))
    try:
        c = db.get_connection(p)
        try:
            db.create_user(c, "admin", L.auth.hash_password(L._PW))   # 寫入處置需 admin 等級
            L._seed(c)
            L.account_inventory.collect_accounts(c, runner=L._runner)
        finally:
            c.close()
        assert client.post("/api/auth/login", json={"username": "admin", "password": L._PW}).status_code == 200
        client.headers[L.auth.CSRF_HEADER_NAME] = L.auth.csrf_token_for(
            client.cookies.get(L.auth.SESSION_COOKIE_NAME))
        c = db.get_connection(p)
        try:
            f = ai.latest_findings(c)[0]
        finally:
            c.close()
        key = {"ip": f["ip"], "username": f["username"], "rule_id": f["rule_id"]}
        bad = client.put("/api/accounts/findings/disposition",
                         json={**key, "status": "fixed", "ref_url": "javascript:alert(1)"})
        assert bad.status_code == 400
        badt = client.put("/api/accounts/findings/disposition",
                          json={**key, "status": "fixed", "fixed_at": "亂填"})
        assert badt.status_code == 400
        ok = client.put("/api/accounts/findings/disposition", json={
            **key, "status": "fixed", "fixed_at": "2026-10-30 09:00", "verified_at": "2026-10-31",
            "verify_due": "2026-12-01", "ref_url": "https://itsm.example.test/T-9"})
        assert ok.status_code == 200
        got = [x for x in client.get("/api/accounts/findings").json()["items"]
               if x["rule_id"] == f["rule_id"] and x["username"] == f["username"]][0]
        assert got["fixed_at"] == "2026-10-30 09:00:00" and got["verified_at"] == "2026-10-31"
        assert got["verify_due"] == "2026-12-01" and got["ref_url"] == "https://itsm.example.test/T-9"
        # 舊呼叫端（不帶新欄位）照常可用
        old = client.put("/api/accounts/findings/disposition", json={**key, "status": "ack"})
        assert old.status_code == 200
    finally:
        api.app.dependency_overrides.clear()


def test_尚未遷移的舊庫_latest_findings不掛(tmp_path):
    """部署流程是『先對正式庫跑數字核對、再重啟服務才遷移』，所以新碼必須能讀還沒有新欄位的舊庫
    （2026-10-31 221 部署數字核對 OperationalError: no such column: fd.fixed_at 實例）。"""
    import sqlite3
    import account_inventory as ai
    db_path = tmp_path / "old.db"
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE account_finding (id INTEGER PRIMARY KEY, ip TEXT, username TEXT, rule_id TEXT,
            severity TEXT, asset_serial TEXT, audit_id INTEGER, status TEXT);
        CREATE TABLE hardware (asset_serial TEXT, hostname TEXT);
        CREATE TABLE host_account (ip TEXT, username TEXT, gecos TEXT, note TEXT);
        CREATE TABLE finding_disposition (ip TEXT, username TEXT, rule_id TEXT, status TEXT, note TEXT,
            exempt_until TEXT, decided_by TEXT, decided_at TEXT, PRIMARY KEY (ip, username, rule_id));
    """)
    cols = ai._disposition_new_cols(c)
    assert cols == {"fixed_at": "NULL", "verified_at": "NULL", "verify_due": "NULL", "ref_url": "NULL"}
    c.close()


def test_沒有finding_disposition表_也不掛():
    import sqlite3
    import account_inventory as ai
    c = sqlite3.connect(":memory:")
    cols = ai._disposition_new_cols(c)                    # PRAGMA 對不存在的表回空，不會例外
    assert all(v == "NULL" for v in cols.values())
    class Boom:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("boom")
    assert all(v == "NULL" for v in ai._disposition_new_cols(Boom()).values())
