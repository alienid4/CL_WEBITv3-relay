"""2.1 重複登記「釋放序號」與還原（2.041.97；舊版缺口 230 §3 階段二，危險區 D4＋D3）。

守的是：快照完整（含每一張以序號為鍵的表）、還原後逐列相同、至少保留一筆、成員已變動拒絕、
CIA 來源提示、孤兒掃描、非管理員 403、登入停用 403、同一請求內任何一步失敗全回滾、釋放後台數不變。
全部用合成假庫（IP 10.99.x）；不連任何正式機。
"""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import db  # noqa: E402
import host_release  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

KEY = None


def _seed(c):
    """H-A（匯入）與 ADOPT-（掃描收編）是同一台（同主機名同 IP）；H-OTHER 是另一台，用來驗台數與誤刪。"""
    db.insert_hardware(c, asset_serial="A-000123", hostname="sec01", ip="10.99.167.12", asset_status="使用中", api_id="AP-1",
                       device_model="IBM S1024", os="AIX 7.2")
    db.insert_hardware(c, asset_serial="ADOPT-10.99.167.12", hostname="sec01", ip="10.99.167.12", asset_status="使用中",
                       api_id="AP-1", os="AIX 7.2")
    db.insert_hardware(c, asset_serial="H-OTHER", hostname="web01", ip="10.99.2.1", asset_status="使用中", api_id="AP-2")
    c.commit()
    c.execute("INSERT INTO software (asset_serial, db_software) VALUES ('ADOPT-10.99.167.12', 'db2')")
    c.execute("INSERT INTO software (asset_serial, db_software) VALUES ('A-000123', 'oracle')")
    c.execute("INSERT INTO personnel (asset_serial, person_name) VALUES ('ADOPT-10.99.167.12', '王小明')")
    c.commit()


def _extra_rows(c, serial):
    """在 schema 裡「所有」有 asset_serial 欄位的非保留表各塞一列（用 PRAGMA 找，不是寫死表名），回傳塞了哪些表。"""
    cat = host_release._catalog(c)
    done = []
    for t, col in cat["serial"]:
        if t in ("software", "personnel"):
            continue
        info = c.execute(f'PRAGMA table_info("{t}")').fetchall()
        cols, vals = [], []
        for r in info:
            name, typ, notnull, dflt, pk = r[1], (r[2] or "").upper(), r[3], r[4], r[5]
            if name == col:
                cols.append(name); vals.append(serial)
            elif pk and typ == "INTEGER":
                continue                                    # 自動編號
            elif notnull and dflt is None:
                cols.append(name)
                vals.append(0 if any(x in typ for x in ("INT", "REAL", "NUM")) else f"x-{t}-{name}")
        try:
            c.execute(f'INSERT INTO "{t}" ({",".join(chr(34) + x + chr(34) for x in cols)}) VALUES ({",".join("?" * len(cols))})', vals)
            done.append(t)
        except sqlite3.Error:
            continue
    c.commit()
    return done


def _dump(c, serial):
    """這個序號在 hardware＋所有納入的表的全部列（排序後的 dict list），用來比「刪前＝還原後」。"""
    cat = host_release._catalog(c)
    out = {}
    hw = c.execute("SELECT * FROM hardware WHERE asset_serial = ?", (serial,)).fetchall()
    out["hardware"] = sorted([dict(r) for r in hw], key=lambda d: json.dumps(d, sort_keys=True, default=str))
    for t, col in cat["serial"]:
        rows = c.execute(f'SELECT * FROM "{t}" WHERE "{col}" = ?', (serial,)).fetchall()
        if rows:
            out[f"{t}.{col}"] = sorted([dict(r) for r in rows], key=lambda d: json.dumps(d, sort_keys=True, default=str))
    return out


@pytest.fixture()
def env():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        c = db.get_connection(p)
        _seed(c)
        c.close()
        role = {"v": "admin"}

        def _get_db():
            x = db.get_connection(p)
            try:
                yield x
            finally:
                x.close()

        api.app.dependency_overrides[api.get_db] = _get_db
        api.app.dependency_overrides[api.require_auth] = lambda: {"username": "boss", "role": role["v"]}
        try:
            yield TestClient(api.app, raise_server_exceptions=False), p, role
        finally:
            api.app.dependency_overrides.pop(api.get_db, None)
            api.app.dependency_overrides.pop(api.require_auth, None)


def _key(p):
    import vip_view
    c = db.get_connection(p)
    try:
        gs = vip_view.duplicate_candidate_groups(c)
        assert len(gs) == 1
        return gs[0][0]
    finally:
        c.close()


def _prev(cl, key, serials=("ADOPT-10.99.167.12",)):
    return cl.post("/api/v2/hosts/duplicates/release-preview", json={"key": key, "release_serials": list(serials)})


def _do(cl, key, serials=("ADOPT-10.99.167.12",), confirm="釋放", fp=None):
    if fp is None:
        fp = _prev(cl, key, serials).json()["fingerprint"]
    return cl.post("/api/v2/hosts/duplicates/release", json={"key": key, "release_serials": list(serials), "confirm": confirm, "fingerprint": fp})


# ------------------------------------------------------------------ 預覽
def test_預覽列出各表筆數與CIA來源(env):
    cl, p, _ = env
    k = _key(p)
    r = _prev(cl, k, ("ADOPT-10.99.167.12", ))
    assert r.status_code == 200, r.text
    d = r.json()
    it = d["release"][0]
    assert it["serial"] == "ADOPT-10.99.167.12" and it["from_cia"] is False
    assert it["counts"]["software"] == 1 and it["counts"]["personnel"] == 1 and it["counts"]["hardware"] == 1
    assert d["keep"] == ["A-000123"] and d["count_before"] == 2 and d["count_after"] == 1
    # 釋放 CIA 來源序號 → 警告要提到下次匯入會再出現
    r2 = _prev(cl, k, ("A-000123",))
    assert r2.json()["release"][0]["from_cia"] is True
    assert any("CIA" in w and "再出現" in w for w in r2.json()["warnings"])
    # 預覽不改任何資料
    c = db.get_connection(p)
    assert c.execute("SELECT COUNT(*) FROM hardware").fetchone()[0] == 3
    assert c.execute("SELECT COUNT(*) FROM software").fetchone()[0] == 2
    c.close()


def test_預覽數字等於實際刪除筆數(env):
    cl, p, _ = env
    k = _key(p)
    c = db.get_connection(p)
    tables = _extra_rows(c, "ADOPT-10.99.167.12")
    c.close()
    assert len(tables) >= 5, f"假資料應涵蓋多張表：{tables}"
    pv = _prev(cl, k).json()
    cnt = pv["release"][0]["counts"]
    r = _do(cl, k, fp=pv["fingerprint"])
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == pv["totals"]["rows"] == sum(v for kk, v in cnt.items() if not kk.endswith("（接回用）"))
    c = db.get_connection(p)
    for t in tables:
        assert c.execute(f'SELECT COUNT(*) FROM "{t}" WHERE asset_serial = ?', ("ADOPT-10.99.167.12",)).fetchone()[0] == 0


# ------------------------------------------------------------------ 快照完整＋還原逐列相同
def test_快照含每一張表_還原後逐列相同(env):
    cl, p, _ = env
    k = _key(p)
    s = "ADOPT-10.99.167.12"
    c = db.get_connection(p)
    tables = _extra_rows(c, s)
    before = _dump(c, s)
    kept_other = _dump(c, "A-000123")
    c.close()
    assert "software.asset_serial" in before and "personnel.asset_serial" in before
    r = _do(cl, k)
    assert r.status_code == 200, r.text
    c = db.get_connection(p)
    assert _dump(c, s) == {"hardware": []}, "釋放後該序號在所有表都應是 0 筆"
    snap = json.loads(c.execute("SELECT snapshot FROM merge_audit WHERE released_serial = ?", (s,)).fetchone()[0])
    assert snap["format"] == 3
    snap_keys = {f"{v['table']}.{v['column']}" for v in snap["tables"].values()} | {"hardware"}
    assert set(before) == snap_keys, "快照範圍要等於刪除範圍（每張表都在）"
    assert _dump(c, "A-000123") == kept_other                       # 保留的那筆完全沒被動
    aid = c.execute("SELECT id FROM merge_audit WHERE released_serial = ?", (s,)).fetchone()[0]
    c.close()
    r2 = cl.post("/api/v2/hosts/duplicates/restore", json={"audit_id": aid})
    assert r2.status_code == 200, r2.text
    c = db.get_connection(p)
    assert _dump(c, s) == before, "還原後各表必須與刪前逐列相同"
    row = c.execute("SELECT restored_at, restored_by FROM merge_audit WHERE id = ?", (aid,)).fetchone()
    assert row[0] and row[1] == "boss"
    assert c.execute("SELECT COUNT(*) FROM activity_log WHERE action = 'host_release_restore'").fetchone()[0] == 1
    c.close()
    # 同一筆不能還原第二次
    assert cl.post("/api/v2/hosts/duplicates/restore", json={"audit_id": aid}).status_code == 409


def test_突變_快照少存一張表_測試要紅(env, monkeypatch):
    """把 software 從目錄裡拿掉（模擬「快照／刪除少一張表」）：還原後 software 對不回去，逐列比對必須抓到。"""
    cl, p, _ = env
    k = _key(p)
    s = "ADOPT-10.99.167.12"
    c = db.get_connection(p)
    before = _dump(c, s)
    c.close()
    real = host_release._catalog

    def broken(conn):
        cat = real(conn)
        cat["serial"] = [(t, col) for t, col in cat["serial"] if t != "software"]
        return cat
    monkeypatch.setattr(host_release, "_catalog", broken)
    r = _do(cl, k)
    # 少了 software 的目錄：hardware 刪不掉（software 的外鍵還指著它）→ 交易內整筆回滾，什麼都不能少
    assert r.status_code >= 400
    monkeypatch.setattr(host_release, "_catalog", real)
    c = db.get_connection(p)
    assert _dump(c, s) == before
    assert c.execute("SELECT COUNT(*) FROM merge_audit").fetchone()[0] == 0
    c.close()


# ------------------------------------------------------------------ 規則
def test_至少保留一筆_全部釋放被拒(env):
    cl, p, _ = env
    k = _key(p)
    r = cl.post("/api/v2/hosts/duplicates/release-preview", json={"key": k, "release_serials": ["A-000123", "ADOPT-10.99.167.12"]})
    assert r.status_code == 400 and "至少要保留" in r.json()["detail"]
    r = cl.post("/api/v2/hosts/duplicates/release", json={"key": k, "release_serials": ["A-000123", "ADOPT-10.99.167.12"],
                                                           "confirm": "釋放", "fingerprint": "x"})
    assert r.status_code == 400
    c = db.get_connection(p)
    assert c.execute("SELECT COUNT(*) FROM hardware").fetchone()[0] == 3
    c.close()


def test_不在組內的序號被拒_不能借這支刪別台(env):
    cl, p, _ = env
    k = _key(p)
    r = _prev(cl, k, ("H-OTHER",))
    assert r.status_code == 409 and "H-OTHER" in r.json()["detail"]
    c = db.get_connection(p)
    assert c.execute("SELECT 1 FROM hardware WHERE asset_serial='H-OTHER'").fetchone()
    c.close()


def test_成員已變動拒絕_409(env):
    cl, p, _ = env
    k = _key(p)
    pv = _prev(cl, k).json()
    # 預覽之後有人把另一筆改成別台（主機名不同）→ 這組不再是重複
    c = db.get_connection(p)
    c.execute("UPDATE hardware SET hostname='other99', ip='10.99.9.9' WHERE asset_serial='A-000123'")
    c.commit()
    c.close()
    r = _do(cl, k, fp=pv["fingerprint"])
    assert r.status_code == 409
    c = db.get_connection(p)
    assert c.execute("SELECT 1 FROM hardware WHERE asset_serial='ADOPT-10.99.167.12'").fetchone()
    c.close()


def test_預覽後從屬資料變動_指紋不符拒絕(env):
    cl, p, _ = env
    k = _key(p)
    pv = _prev(cl, k).json()
    c = db.get_connection(p)
    c.execute("INSERT INTO software (asset_serial, db_software) VALUES ('ADOPT-10.99.167.12', 'new-after-preview')")
    c.commit()
    c.close()
    r = _do(cl, k, fp=pv["fingerprint"])
    assert r.status_code == 409 and "預覽之後" in r.json()["detail"]


def test_確認文字與指紋必填(env):
    cl, p, _ = env
    k = _key(p)
    fp = _prev(cl, k).json()["fingerprint"]
    assert _do(cl, k, confirm="", fp=fp).status_code == 400
    assert _do(cl, k, confirm="不要", fp=fp).status_code == 400
    r = cl.post("/api/v2/hosts/duplicates/release", json={"key": k, "release_serials": ["ADOPT-10.99.167.12"], "confirm": "釋放"})
    assert r.status_code == 400
    assert _do(cl, k, confirm="ADOPT-10.99.167.12", fp=fp).status_code == 200          # 輸入序號也可以


def test_釋放後台數不變_且保留的序號在(env):
    cl, p, _ = env
    k = _key(p)
    c = db.get_connection(p)
    before = host_release._machine_count(c)
    c.close()
    assert _do(cl, k).status_code == 200
    c = db.get_connection(p)
    assert host_release._machine_count(c) == before == 2
    assert c.execute("SELECT 1 FROM hardware WHERE asset_serial='A-000123'").fetchone()
    c.close()
    # 釋放後該組不再是重複；dedup 與清單仍通
    assert cl.get("/api/v2/hosts/duplicates").json()["groups"] == []
    assert cl.get("/api/assets/duplicates").status_code == 200


# ------------------------------------------------------------------ 權限
@pytest.mark.parametrize("role", ["user", "viewer"])
def test_非管理員403(env, role):
    cl, p, r = env
    k = _key(p)
    r["v"] = role
    for url, body in (("/api/v2/hosts/duplicates/release-preview", {"key": k, "release_serials": ["ADOPT-10.99.167.12"]}),
                      ("/api/v2/hosts/duplicates/release", {"key": k, "release_serials": ["ADOPT-10.99.167.12"], "confirm": "釋放", "fingerprint": "x"}),
                      ("/api/v2/hosts/duplicates/restore", {"audit_id": 1}),
                      ("/api/assets/duplicates/release", {"hostname": "sec01", "ip": "10.99.167.12", "release_serials": ["ADOPT-10.99.167.12"]})):
        assert cl.post(url, json=body).status_code == 403, url
    c = db.get_connection(p)
    assert c.execute("SELECT COUNT(*) FROM hardware").fetchone()[0] == 3
    c.close()


def test_登入停用一律403(env):
    cl, p, _ = env
    k = _key(p)
    c = db.get_connection(p)
    c.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES ('auth_disabled', '1')")
    c.commit()
    from _app_shared import auth_disabled
    assert auth_disabled(c)
    c.close()
    for url, body in (("/api/v2/hosts/duplicates/release-preview", {"key": k, "release_serials": ["ADOPT-10.99.167.12"]}),
                      ("/api/v2/hosts/duplicates/release", {"key": k, "release_serials": ["ADOPT-10.99.167.12"], "confirm": "釋放", "fingerprint": "x"}),
                      ("/api/v2/hosts/duplicates/restore", {"audit_id": 1})):
        r = cl.post(url, json=body)
        assert r.status_code == 403 and "登入已停用" in r.json()["detail"], url
    c = db.get_connection(p)
    assert c.execute("SELECT COUNT(*) FROM hardware").fetchone()[0] == 3
    c.close()


def test_釋放紀錄清單_管理員限定(env):
    cl, p, r = env
    k = _key(p)
    assert _do(cl, k).status_code == 200
    lst = cl.get("/api/v2/hosts/duplicates/releases").json()
    assert lst["restorable"] == 1 and lst["items"][0]["state"] == "可還原" and lst["items"][0]["actor"] == "boss"
    r["v"] = "user"
    assert cl.get("/api/v2/hosts/duplicates/releases").status_code == 403


# ------------------------------------------------------------------ 原子性
def test_第二個序號刪到一半失敗_全部回滾(env, monkeypatch):
    """三筆同組、釋放兩筆；刪第二個序號的某張表時炸掉 → 第一個序號也要原封不動、沒有留下稽核列。"""
    cl, p, _ = env
    c = db.get_connection(p)
    db.insert_hardware(c, asset_serial="ADOPT-X", hostname="sec01", ip="10.99.167.12", asset_status="使用中", api_id="AP-1")
    c.execute("INSERT INTO software (asset_serial, db_software) VALUES ('ADOPT-X', 'x')")
    c.commit()
    c.close()
    k = _key(p)
    c = db.get_connection(p)
    before = {s: _dump(c, s) for s in ("A-000123", "ADOPT-10.99.167.12", "ADOPT-X")}
    c.close()
    real = host_release._delete_rows
    calls = {"n": 0}

    def flaky(conn, table, col, val):
        if val == "ADOPT-X" and table == "software":
            raise RuntimeError("boom")
        calls["n"] += 1
        return real(conn, table, col, val)
    monkeypatch.setattr(host_release, "_delete_rows", flaky)
    serials = ["ADOPT-10.99.167.12", "ADOPT-X"]
    pv = cl.post("/api/v2/hosts/duplicates/release-preview", json={"key": k, "release_serials": serials}).json()
    r = cl.post("/api/v2/hosts/duplicates/release", json={"key": k, "release_serials": serials, "confirm": "釋放", "fingerprint": pv["fingerprint"]})
    assert r.status_code == 500
    assert calls["n"] >= 2, "第一個序號確實已經刪過東西，才算測到回滾"
    c = db.get_connection(p)
    assert {s: _dump(c, s) for s in before} == before
    assert c.execute("SELECT COUNT(*) FROM merge_audit").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM activity_log WHERE action = 'host_release'").fetchone()[0] == 0
    c.close()


def test_突變_目錄漏掉一張沒有外鍵的表_獨立孤兒掃描要抓到並回滾(env, monkeypatch):
    """host_package 沒有 FK：目錄漏掉它，刪除不會報錯，只有獨立的孤兒掃描能發現殘留。"""
    cl, p, _ = env
    k = _key(p)
    c = db.get_connection(p)
    tables = _extra_rows(c, "ADOPT-10.99.167.12")
    assert "host_package" in tables
    before = _dump(c, "ADOPT-10.99.167.12")
    c.close()
    real = host_release._catalog

    def broken(conn):
        cat = real(conn)
        cat["serial"] = [(t, col) for t, col in cat["serial"] if t != "host_package"]
        return cat
    monkeypatch.setattr(host_release, "_catalog", broken)
    r = _do(cl, k)
    assert r.status_code == 500 and "host_package" in r.json()["detail"]
    monkeypatch.setattr(host_release, "_catalog", real)
    c = db.get_connection(p)
    assert _dump(c, "ADOPT-10.99.167.12") == before
    assert c.execute("SELECT COUNT(*) FROM merge_audit").fetchone()[0] == 0
    c.close()


def test_稽核寫入失敗_資料不刪(env, monkeypatch):
    cl, p, _ = env
    k = _key(p)
    import activity
    monkeypatch.setattr(activity, "log", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("audit down")))
    c = db.get_connection(p)
    before = _dump(c, "ADOPT-10.99.167.12")
    c.close()
    assert _do(cl, k).status_code == 500
    c = db.get_connection(p)
    assert _dump(c, "ADOPT-10.99.167.12") == before
    assert c.execute("SELECT COUNT(*) FROM merge_audit").fetchone()[0] == 0
    c.close()


def test_孤兒掃描_有殘留就整筆回滾(env, monkeypatch):
    """刪除動作漏刪某張納入的表（模擬 DELETE 沒命中）→ 交易內掃描發現殘留 → 全部回滾。"""
    cl, p, _ = env
    k = _key(p)
    real = host_release._delete_rows

    def skip_personnel(conn, table, col, val):
        if table == "personnel":
            return 0
        return real(conn, table, col, val)
    monkeypatch.setattr(host_release, "_delete_rows", skip_personnel)
    r = _do(cl, k)
    assert r.status_code >= 400
    c = db.get_connection(p)
    assert c.execute("SELECT 1 FROM hardware WHERE asset_serial='ADOPT-10.99.167.12'").fetchone()
    assert c.execute("SELECT COUNT(*) FROM personnel WHERE asset_serial='ADOPT-10.99.167.12'").fetchone()[0] == 1
    c.close()


def test_歷史稽核表保留不動_且預覽講出保留幾筆(env):
    cl, p, _ = env
    k = _key(p)
    c = db.get_connection(p)
    cat = host_release._catalog(c)
    assert "hardware_merged" in cat["kept"] or "lifecycle_migration_log" in cat["kept"]
    c.execute("INSERT INTO lifecycle_migration_log (run_at, asset_serial, field, old_value, new_value, reason) "
              "VALUES ('2026-01-01 00:00:00', 'ADOPT-10.99.167.12', 'f', 'a', 'b', 'r')")
    c.commit()
    c.close()
    pv = _prev(cl, k).json()
    assert pv["release"][0]["kept_refs"].get("lifecycle_migration_log") == 1
    assert _do(cl, k, fp=pv["fingerprint"]).status_code == 200
    c = db.get_connection(p)
    assert c.execute("SELECT COUNT(*) FROM lifecycle_migration_log WHERE asset_serial='ADOPT-10.99.167.12'").fetchone()[0] == 1
    c.close()


# ------------------------------------------------------------------ 還原的拒絕條件
def test_還原_序號已被占用就拒絕_且整筆不動(env):
    cl, p, _ = env
    k = _key(p)
    assert _do(cl, k).status_code == 200
    c = db.get_connection(p)
    aid = c.execute("SELECT id FROM merge_audit").fetchone()[0]
    db.insert_hardware(c, asset_serial="ADOPT-10.99.167.12", hostname="newcomer", ip="10.99.8.8", asset_status="使用中")
    c.commit()
    c.close()
    r = cl.post("/api/v2/hosts/duplicates/restore", json={"audit_id": aid})
    assert r.status_code == 409 and "已被現有資產使用" in r.json()["detail"]
    lst = cl.get("/api/v2/hosts/duplicates/releases").json()["items"][0]
    assert lst["state"] == "序號已占用" and not lst["restorable"]
    c = db.get_connection(p)
    assert c.execute("SELECT COUNT(*) FROM software WHERE asset_serial='ADOPT-10.99.167.12'").fetchone()[0] == 0   # 沒有還原一半
    c.close()


def test_還原_與還原期間新資料衝突_整筆不動(env):
    """釋放後有人在從屬表建了同主鍵的列 → 還原不覆蓋、整筆回滾（hardware 也不能先寫進去）。"""
    cl, p, _ = env
    k = _key(p)
    assert _do(cl, k).status_code == 200
    c = db.get_connection(p)
    aid, snap = c.execute("SELECT id, snapshot FROM merge_audit").fetchone()
    soft_id = json.loads(snap)["tables"]["software.asset_serial"]["rows"][0]["id"]
    c.execute("INSERT INTO software (id, asset_serial, db_software) VALUES (?, 'A-000123', 'squatter')", (soft_id,))
    c.commit()
    c.close()
    r = cl.post("/api/v2/hosts/duplicates/restore", json={"audit_id": aid})
    assert r.status_code == 409 and "衝突" in r.json()["detail"]
    c = db.get_connection(p)
    assert c.execute("SELECT 1 FROM hardware WHERE asset_serial='ADOPT-10.99.167.12'").fetchone() is None
    assert c.execute("SELECT db_software FROM software WHERE id=?", (soft_id,)).fetchone()[0] == "squatter"
    assert c.execute("SELECT restored_at FROM merge_audit WHERE id=?", (aid,)).fetchone()[0] is None
    c.close()


def test_舊版紀錄_不完整快照_不能還原(env):
    cl, p, _ = env
    c = db.get_connection(p)
    from _app_shared import _ensure_merge_audit
    _ensure_merge_audit(c)
    c.execute("INSERT INTO merge_audit (merged_at, actor, hostname, ip, released_serial, kept_serials, snapshot) "
              "VALUES ('2026-01-01 00:00:00', 'x', 'h', '10.99.0.1', 'OLD-1', 'K', '{}')")
    c.commit()
    aid = c.execute("SELECT id FROM merge_audit").fetchone()[0]
    c.close()
    r = cl.post("/api/v2/hosts/duplicates/restore", json={"audit_id": aid})
    assert r.status_code == 409 and "舊版" in r.json()["detail"]
    it = cl.get("/api/v2/hosts/duplicates/releases").json()["items"][0]
    assert it["state"] == "無法還原" and not it["restorable"]
    assert cl.post("/api/v2/hosts/duplicates/restore", json={"audit_id": 99999}).status_code == 404


def test_source_record外鍵_釋放斷開_還原接回(env):
    cl, p, _ = env
    k = _key(p)
    c = db.get_connection(p)
    hid = c.execute("SELECT id FROM hardware WHERE asset_serial='ADOPT-10.99.167.12'").fetchone()[0]
    c.execute("INSERT INTO source_record (source, source_key, payload, resolved_status, resolved_hardware_id) "
              "VALUES ('excel', 'r1', '{}', 'matched', ?)", (hid,))
    c.commit()
    c.close()
    pv = _prev(cl, k).json()
    assert any("source_record" in t for t in pv["release"][0]["counts"])
    assert _do(cl, k, fp=pv["fingerprint"]).status_code == 200
    c = db.get_connection(p)
    assert c.execute("SELECT resolved_hardware_id FROM source_record WHERE source_key='r1'").fetchone()[0] is None
    aid = c.execute("SELECT id FROM merge_audit").fetchone()[0]
    c.close()
    assert cl.post("/api/v2/hosts/duplicates/restore", json={"audit_id": aid}).status_code == 200
    c = db.get_connection(p)
    assert c.execute("SELECT resolved_hardware_id FROM source_record WHERE source_key='r1'").fetchone()[0] == hid
    c.close()


def test_舊版釋放端點回410_不能繞過預覽與確認(env):
    """/api/assets/duplicates/release 不再刪任何資料（審查 #1）：管理員呼叫也是 410，資料與稽核都不動。"""
    cl, p, _ = env
    r = cl.post("/api/assets/duplicates/release", json={"hostname": "sec01", "ip": "10.99.167.12", "release_serials": ["ADOPT-10.99.167.12"]})
    assert r.status_code == 410 and "重複登記" in r.json()["detail"]
    c = db.get_connection(p)
    assert c.execute("SELECT COUNT(*) FROM hardware").fetchone()[0] == 3
    assert c.execute("SELECT COUNT(*) FROM software").fetchone()[0] == 2
    assert c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'merge_audit'").fetchone()[0] == 0 or c.execute("SELECT COUNT(*) FROM merge_audit").fetchone()[0] == 0
    c.close()
    # 引擎本身也沒有繞過參數
    import inspect
    assert "legacy" not in inspect.signature(host_release.release).parameters


def test_verify_numbers_重複登記釋放不變式_嚴格模式全綠(env):
    """verify_numbers 新增的檢查：有重複組、有釋放紀錄、有還原紀錄時，嚴格模式也要全綠（預設在正式機是觀察模式）。"""
    import verify_numbers as vn
    cl, p, _ = env
    k = _key(p)
    c = db.get_connection(p)
    f0 = vn.FAILS
    vn.check_duplicate_release(c, strict=True)
    assert vn.FAILS == f0 and any("預覽" in r["name"] and r["ok"] for r in vn.RESULTS[-8:]), vn.RESULTS[-8:]
    c.close()
    assert _do(cl, k).status_code == 200
    c = db.get_connection(p)
    aid = c.execute("SELECT id FROM merge_audit").fetchone()[0]
    vn.check_duplicate_release(c, strict=True)
    assert vn.FAILS == f0
    c.close()
    assert cl.post("/api/v2/hosts/duplicates/restore", json={"audit_id": aid}).status_code == 200
    c = db.get_connection(p)
    vn.check_duplicate_release(c, strict=True)
    assert vn.FAILS == f0
    # 突變：孤兒資料（指向不存在序號的從屬列）嚴格模式必須紅
    c.execute("PRAGMA foreign_keys = OFF")
    c.execute("INSERT INTO host_package (asset_serial, name, ip, source) VALUES ('GHOST-1', 'x', '10.99.0.9', 't')")
    c.commit()
    vn.check_duplicate_release(c, strict=True)
    assert vn.FAILS == f0 + 1
    vn.FAILS = f0
    c.close()
