"""共用 IP／VIP 待核清單：批次判定（兩段式、單一交易、digest、不覆蓋已判定、上限、整批復原、稽核、不動資產與台數）。
測試資料一律虛構：192.0.2.x、example.test。"""
import hashlib
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
import ip_relation as ipr  # noqa: E402
import system_stats  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ROLE_AWARE_TESTS = True
_PW = "s3cure-pass!"          # 測試用假密碼


def _seed(c):
    def hw(serial, host, ip, **kw):
        db.insert_hardware(c, asset_serial=serial, hostname=host, ip=ip, **kw)

    hw("D-3", "db-scan", "192.0.2.50", asset_purpose="RAC SCAN")
    hw("S-1", "app-a", "192.0.2.77")
    hw("S-2", "app-b", "192.0.2.77")
    hw("L-1", "lb-1", "192.0.2.90", device_model="F5 BIG-IP i2600")
    hw("V-2", "ora-1", "192.0.2.41", big_ip_vip="192.0.2.210")
    hw("V-3", "ora-2", "192.0.2.42", big_ip_vip="192.0.2.210")
    hw("T-1", "web-9", "192.0.2.61")
    hw("T-2", "web-8", "192.0.2.61")
    c.commit()


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    _seed(c)
    return c, p


def _pending(c):
    return [x["item_key"] for x in ipr.listing(c)["pending"]]


def _snapshot(c):
    rows = [tuple(r) for r in c.execute("SELECT * FROM hardware ORDER BY asset_serial")]
    keys = sorted({system_stats.machine_key(r["hostname"], r["ip"], r["asset_serial"])
                   for r in c.execute("SELECT asset_serial, hostname, ip FROM hardware")})
    return rows, keys


def _n(c, table):
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone():
        return 0                # 失敗的交易連建表一起 rollback
    return c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_預覽純讀_不建表不寫庫_唯讀連線也能跑(tmp_path):
    c, p = _conn(tmp_path)
    keys = _pending(c)
    c.close()
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    ro = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    ro.execute("PRAGMA query_only=ON")
    st: list[str] = []
    ro.set_trace_callback(st.append)
    pv = ipr.batch_preview(ro, keys, "not_service")
    assert pv["will_apply"] == len(keys) and pv["skipped"] == 0 and pv["digest"] and pv["sample"]
    assert ipr.list_batches(ro) == []
    ro.close()
    assert not [x for x in st if not x.lstrip().upper().startswith(("SELECT", "PRAGMA", "WITH", "BEGIN", "COMMIT"))]
    assert hashlib.sha256(p.read_bytes()).hexdigest() == h


def test_讀取函式不呼叫ensure_table():
    src = (ROOT / "APP" / "asset-module" / "backend" / "ip_relation.py").read_text(encoding="utf-8")
    for fn in ("def batch_preview", "def _batch_plan", "def list_batches", "def listing", "def candidates"):
        body = src.split(fn, 1)[1].split("\ndef ", 1)[0]
        assert "ensure_table" not in body, fn


def test_套用_批次前後資產與台數完全不變_且寫了稽核(tmp_path):
    c, _ = _conn(tmp_path)
    before = _snapshot(c)
    keys = _pending(c)
    pv = ipr.batch_preview(c, keys, "shared_service", "資料庫 VIP", "叢集 A")
    res = ipr.batch_apply(c, keys, "shared_service", "資料庫 VIP", "叢集 A", False, pv["digest"], "共用 IP 全部", "alice")
    assert res["applied"] == len(keys) and res["batch_id"].startswith("IPB-")
    assert _snapshot(c) == before
    assert _pending(c) == [] and ipr.listing(c)["counts"]["confirmed"] == len(keys)
    b = c.execute("SELECT * FROM ip_relation_batch WHERE batch_id = ?", (res["batch_id"],)).fetchone()
    assert b["created_by"] == "alice" and b["item_count"] == len(keys) and b["filter_text"] == "共用 IP 全部"
    assert b["verdict"] == "shared_service" and b["created_at"]
    assert _n(c, "ip_relation_review") == len(keys)
    assert {r["batch_id"] for r in c.execute("SELECT batch_id FROM ip_relation_review")} == {res["batch_id"]}
    assert ipr.list_batches(c)[0]["batch_id"] == res["batch_id"]


def test_單一交易_中途失敗全部不寫(tmp_path, monkeypatch):
    c, _ = _conn(tmp_path)
    keys = _pending(c)
    pv = ipr.batch_preview(c, keys, "not_service")
    real = ipr._insert_review
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("fail midway")
        return real(*a, **k)

    monkeypatch.setattr(ipr, "_insert_review", boom)
    with pytest.raises(RuntimeError):
        ipr.batch_apply(c, keys, "not_service", None, None, False, pv["digest"], None, "u")
    assert calls["n"] == 3
    assert _n(c, "ip_relation_review") == 0 and _n(c, "ip_relation_batch") == 0
    assert _pending(c) == keys


def test_digest過期_預覽後清單有變動就拒絕_不寫(tmp_path):
    c, _ = _conn(tmp_path)
    keys = _pending(c)
    pv = ipr.batch_preview(c, keys, "not_service")
    ipr.review(c, keys[0], "not_service", None, None, "bob")             # 預覽後有人單筆判了一筆
    with pytest.raises(ipr.StalePlan):
        ipr.batch_apply(c, keys, "not_service", None, None, False, pv["digest"], None, "u")
    assert _n(c, "ip_relation_batch") == 0 and _n(c, "ip_relation_review") == 1
    with pytest.raises(ValueError):
        ipr.batch_apply(c, keys, "not_service", None, None, False, "", None, "u")     # 沒有 digest 不給套
    # 判定內容不同（預覽是 not_service，套用改成 data_error）也算不一致
    pv2 = ipr.batch_preview(c, keys, "not_service")
    with pytest.raises(ipr.StalePlan):
        ipr.batch_apply(c, keys, "data_error", None, "x", False, pv2["digest"], None, "u")


def test_不覆蓋已判定_除非勾選覆蓋_復原整批會還原被覆蓋的舊判定(tmp_path):
    c, _ = _conn(tmp_path)
    keys = _pending(c)
    ipr.review(c, keys[0], "data_error", None, "原本人工判的", "bob")
    pv = ipr.batch_preview(c, keys, "not_service")
    assert pv["skipped"] == 1 and pv["skipped_by_reason"] == {"already_judged": 1} and pv["will_apply"] == len(keys) - 1
    r = ipr.batch_apply(c, keys, "not_service", None, None, False, pv["digest"], None, "u")
    assert r["applied"] == len(keys) - 1 and r["skipped"] == 1
    cur = {x["item_key"]: x for x in ipr.listing(c)["dismissed"]}
    assert cur[keys[0]]["review"]["note"] == "原本人工判的"                 # 人工那筆沒被動
    # 勾覆蓋
    pv2 = ipr.batch_preview(c, [keys[0]], "not_service", overwrite=True)
    assert pv2["will_overwrite"] == 1 and pv2["will_overwrite_by_verdict"] == {"資料錯誤（待補）": 1}
    r2 = ipr.batch_apply(c, [keys[0]], "not_service", None, None, True, pv2["digest"], None, "u")
    assert {x["item_key"]: x for x in ipr.listing(c)["dismissed"]}[keys[0]]["review"]["note"] is None
    out = ipr.batch_undo(c, r2["batch_id"], "u", None)
    assert out["restored"] == 1 and out["conflicts"] == []
    back = {x["item_key"]: x for x in ipr.listing(c)["dismissed"]}[keys[0]]
    assert back["review"]["note"] == "原本人工判的" and back["review"]["verdict"] == "data_error"


def test_上限500筆_超過擋下(tmp_path):
    c, _ = _conn(tmp_path)
    many = [f"ip:198.51.100.{i % 250}-{i}" for i in range(501)]
    with pytest.raises(ValueError, match="500"):
        ipr.batch_preview(c, many, "not_service")
    with pytest.raises(ValueError, match="500"):
        ipr.batch_apply(c, many, "not_service", None, None, False, "x", None, "u")
    ok = [f"ip:198.51.100.{i}" for i in range(500)]                      # 剛好 500：不擋（只是都不在範圍）
    assert ipr.batch_preview(c, ok, "not_service")["skipped"] == 500
    with pytest.raises(ValueError):
        ipr.batch_preview(c, [], "not_service")


def test_欄位驗證同單筆(tmp_path):
    c, _ = _conn(tmp_path)
    keys = _pending(c)
    with pytest.raises(ValueError):
        ipr.batch_preview(c, keys, "shared_service")
    with pytest.raises(ValueError):
        ipr.batch_preview(c, keys, "data_error")
    with pytest.raises(ValueError):
        ipr.batch_preview(c, keys, "亂寫")


def test_整批復原_回到待核_衝突不默默覆蓋(tmp_path):
    c, _ = _conn(tmp_path)
    keys = _pending(c)
    pv = ipr.batch_preview(c, keys, "not_service")
    r = ipr.batch_apply(c, keys, "not_service", None, None, False, pv["digest"], None, "u")
    # 批次之後有人把第 1 筆單筆復原再改判
    rid = {x["item_key"]: x for x in ipr.listing(c)["dismissed"]}[keys[0]]["review"]["id"]
    ipr.undo(c, rid, "bob", None)
    ipr.review(c, keys[0], "data_error", None, "後來人工改的", "bob")
    with pytest.raises(ipr.BatchConflict) as ei:
        ipr.batch_undo(c, r["batch_id"], "u", None)
    assert [x["item_key"] for x in ei.value.conflicts] == [keys[0]]
    assert len(_pending(c)) == 0 and ipr.listing(c)["counts"]["dismissed"] == len(keys)   # 整批沒動
    out = ipr.batch_undo(c, r["batch_id"], "u", None, skip_conflicts=True)
    assert out["restored"] == len(keys) - 1 and len(out["conflicts"]) == 1
    assert _pending(c) == keys[1:] or sorted(_pending(c)) == sorted(keys[1:])
    cur = {x["item_key"]: x for x in ipr.listing(c)["dismissed"]}
    assert cur[keys[0]]["review"]["note"] == "後來人工改的"              # 衝突那筆沒被蓋
    with pytest.raises(ValueError):
        ipr.batch_undo(c, r["batch_id"], "u", None)                      # 不可重複復原
    with pytest.raises(LookupError):
        ipr.batch_undo(c, "IPB-nope", "u", None)


def test_整批復原_乾淨情況全部回待核(tmp_path):
    c, _ = _conn(tmp_path)
    before = _snapshot(c)
    keys = _pending(c)
    pv = ipr.batch_preview(c, keys, "not_service")
    r = ipr.batch_apply(c, keys, "not_service", None, None, False, pv["digest"], None, "u")
    out = ipr.batch_undo(c, r["batch_id"], "u", "誤標")
    assert out["restored"] == len(keys) and not out["conflicts"]
    assert sorted(_pending(c)) == sorted(keys) and _snapshot(c) == before
    assert ipr.list_batches(c)[0]["undone_at"]


# ------------------------------------------------------------------ API：權限
@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.delenv(api.AUTH_DISABLED_ENV, raising=False)
    p = tmp_path / "t.db"
    db.init_db(p)
    c0 = db.get_connection(p)
    _seed(c0)
    for n, role in (("admin", "admin"), ("alice", "user"), ("vera", "viewer")):
        db.create_user(c0, n, auth.hash_password(_PW), role=role)
    c0.close()

    def _ov():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _ov

    class E:
        path = p

        def client(self, name):
            t = TestClient(api.app)
            assert t.post("/api/auth/login", json={"username": name, "password": _PW}).status_code == 200
            return t

        def conn(self):
            return db.get_connection(p)

    yield E()
    api.app.dependency_overrides.pop(api.get_db, None)


def test_API_非管理員403_管理員走完預覽套用復原_並有操作紀錄(env):
    admin = env.client("admin")
    keys = [x["item_key"] for x in admin.get("/api/ip-relations").json()["pending"]]
    body = {"item_keys": keys, "verdict": "not_service", "filter_text": "全部"}
    for who in ("alice", "vera"):
        cl = env.client(who)
        assert cl.post("/api/ip-relations/batch/preview", json=body).status_code == 403
        assert cl.post("/api/ip-relations/batch/apply", json={**body, "digest": "x"}).status_code == 403
        assert cl.post("/api/ip-relations/batch/IPB-x/undo", json={}).status_code == 403
    c = env.conn()
    assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='ip_relation_batch'").fetchone()   # 403 沒有建表
    c.close()
    pv = admin.post("/api/ip-relations/batch/preview", json=body)
    assert pv.status_code == 200 and pv.json()["will_apply"] == len(keys)
    c = env.conn()
    assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='ip_relation_batch'").fetchone()   # 預覽不建表
    c.close()
    assert admin.post("/api/ip-relations/batch/apply", json={**body, "digest": "wrong"}).status_code == 409
    ap = admin.post("/api/ip-relations/batch/apply", json={**body, "digest": pv.json()["digest"]})
    assert ap.status_code == 200, ap.text
    bid = ap.json()["batch_id"]
    assert env.client("alice").get("/api/ip-relations/batches").json()["batches"][0]["batch_id"] == bid     # 讀＝登入即可
    c = env.conn()
    assert c.execute("SELECT count(*) FROM activity_log WHERE path = '/api/ip-relations/batch/apply'").fetchone()[0] >= 1
    c.close()
    assert admin.post(f"/api/ip-relations/batch/{bid}/undo", json={}).json()["restored"] == len(keys)
    assert admin.post(f"/api/ip-relations/batch/{bid}/undo", json={}).status_code == 400
    assert admin.post("/api/ip-relations/batch/IPB-none/undo", json={}).status_code == 404


def test_API_超過500筆回400(env):
    admin = env.client("admin")
    r = admin.post("/api/ip-relations/batch/preview",
                   json={"item_keys": [f"ip:198.51.100.{i}" for i in range(501)], "verdict": "not_service"})
    assert r.status_code == 400 and "500" in r.text


# ------------------------------------------------------------------ 審查後補強（GPT 對手式審查）
def test_跨批次交錯_A覆寫B_復原B後A再度現行_仍不可復原A(tmp_path):
    c, _ = _conn(tmp_path)
    keys = _pending(c)
    pa = ipr.batch_preview(c, keys, "not_service")
    a = ipr.batch_apply(c, keys, "not_service", None, None, False, pa["digest"], None, "u")
    pb = ipr.batch_preview(c, keys, "data_error", note="x", overwrite=True)
    b = ipr.batch_apply(c, keys, "data_error", None, "x", True, pb["digest"], None, "u")
    ipr.batch_undo(c, b["batch_id"], "u", None)                       # A 的 review 又變回現行
    assert ipr.listing(c)["counts"]["dismissed"] == len(keys)
    with pytest.raises(ipr.BatchConflict) as ei:
        ipr.batch_undo(c, a["batch_id"], "u", None)
    assert len(ei.value.conflicts) == len(keys)
    assert ipr.listing(c)["counts"]["dismissed"] == len(keys)         # A 沒被動


def test_A後沒人動過_單獨復原A仍可(tmp_path):
    c, _ = _conn(tmp_path)
    keys = _pending(c)
    pa = ipr.batch_preview(c, keys, "not_service")
    a = ipr.batch_apply(c, keys, "not_service", None, None, False, pa["digest"], None, "u")
    assert ipr.batch_undo(c, a["batch_id"], "u", None)["restored"] == len(keys)


def test_舊庫digest過期_409_且不留任何schema變動(tmp_path):
    c, p = _conn(tmp_path)
    keys = _pending(c)
    pv = ipr.batch_preview(c, keys, "not_service")
    assert not c.execute("SELECT 1 FROM sqlite_master WHERE name LIKE 'ip_relation%'").fetchone()
    db.insert_hardware(c, asset_serial="S-9", hostname="app-z", ip="192.0.2.77")        # 預覽後清單有變動
    c.commit()
    master_before = sorted(tuple(r) for r in c.execute("SELECT type, name, sql FROM sqlite_master"))
    with pytest.raises(ipr.StalePlan):
        ipr.batch_apply(c, keys, "not_service", None, None, False, pv["digest"], None, "u")
    assert sorted(tuple(r) for r in c.execute("SELECT type, name, sql FROM sqlite_master")) == master_before
    assert not c.execute("SELECT 1 FROM sqlite_master WHERE name LIKE 'ip_relation%'").fetchone()


def test_舊版review表缺欄位_digest過期不補欄位_正確digest才補並套用(tmp_path):
    c, _ = _conn(tmp_path)
    c.execute("CREATE TABLE ip_relation_review (id INTEGER PRIMARY KEY AUTOINCREMENT, item_key TEXT NOT NULL, fingerprint TEXT NOT NULL,"
              " verdict TEXT NOT NULL, relation_type TEXT, machine_keys TEXT, note TEXT, decided_by TEXT, decided_at TEXT NOT NULL,"
              " undone_at TEXT, undone_by TEXT, undo_note TEXT)")
    c.commit()
    keys = _pending(c)
    pv = ipr.batch_preview(c, keys, "not_service")
    with pytest.raises(ipr.StalePlan):
        ipr.batch_apply(c, keys, "not_service", None, None, False, "stale", None, "u")
    assert {r[1] for r in c.execute("PRAGMA table_info(ip_relation_review)")} == {
        "id", "item_key", "fingerprint", "verdict", "relation_type", "machine_keys", "note", "decided_by", "decided_at",
        "undone_at", "undone_by", "undo_note"}
    assert not c.execute("SELECT 1 FROM sqlite_master WHERE name = 'ip_relation_batch'").fetchone()
    ok = ipr.batch_apply(c, keys, "not_service", None, None, False, pv["digest"], None, "u")
    assert ok["applied"] == len(keys)


def test_digest涵蓋顯示內容_key集合不變但顯示名稱變了_必須409(tmp_path):
    c, _ = _conn(tmp_path)
    keys = _pending(c)
    pv = ipr.batch_preview(c, keys, "not_service")
    fp_before = {x["item_key"]: x["fingerprint"] for x in ipr.listing(c)["pending"]}
    c.execute("UPDATE hardware SET hostname = 'APP-A' WHERE asset_serial = 'S-1'")      # 同一台（key 不變），顯示名稱／原因文字變了
    c.commit()
    now = {x["item_key"]: x for x in ipr.listing(c)["pending"]}
    assert {k: v["fingerprint"] for k, v in now.items()} == fp_before                 # 指紋沒變，只有內容變
    with pytest.raises(ipr.StalePlan):
        ipr.batch_apply(c, keys, "not_service", None, None, False, pv["digest"], None, "u")
    assert _n(c, "ip_relation_review") == 0
