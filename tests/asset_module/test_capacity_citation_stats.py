"""容量分析封存（2.042.06）：封存引用（新增／更正／作廢，只增不改、版本鏈可見、所有版本都保護）＋中位數／超標分鐘
（依來源取樣間隔換算、覆蓋不足寫不知道、不由平均／P95 推、不補 0）。全部假資料（Codex 204186／204190 的案例），不連網路。"""
import gzip
import json
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import capacity_analysis as ca  # noqa: E402
import capacity_archive as ar  # noqa: E402
import quiet_window as qw  # noqa: E402
from test_capacity_archive import TODAY, _api, _old, mem, put  # noqa: E402

THR = 80.0


def pts(vals, itv=60, start=0, mins=None, maxs=None, inferred=False):
    """合成取樣點：(值, 桶內最小, 桶內最大, 距時段起點秒數, 來源列間隔秒, 間隔是否推算)，一點接一點。"""
    return [(v, (mins[i] if mins else None), (maxs[i] if maxs else None), start + i * itv, itv, inferred) for i, v in enumerate(vals)]


def ds(per_day, status="ok", slot_secs=180, thr=THR, bucket=False):
    return ca.dist_stats(per_day, bucket, status, slot_secs, thr)


# ---------------------------------------------------------------- 中位數／超標分鐘（純函式，Codex 案例）
def test_raw奇數與偶數樣本中位數_一般未加權():
    r = ds({"D": pts([0, 20, 100])})
    assert r["median"] == 20 and "未加權" in r["median_note"]
    assert ds({"D": pts([0, 20, 40, 100])}, slot_secs=240)["median"] == 30.0


def test_超標是嚴格大於_等於門檻不算_依來源間隔換算():
    r = ds({"D": pts([0, 80, 90])})
    assert r["over_min"] == 1 and r["over_lo"] == r["over_hi"] == 1 and r["over_missing_min"] == 0 and "精確" in r["over_note"]
    # WhatsUp CPU 3 分鐘一點：同樣三個點，超標一點＝3 分鐘
    assert ds({"D": pts([0, 80, 90], itv=180)}, slot_secs=540)["over_min"] == 3


def test_有缺漏的取樣_只給下限與上限_缺的不當成沒超標():
    # 時段 10 分鐘、1 分鐘一點，只收到 8 點（缺 2 分鐘）
    r = ds({"D": pts([90, 90, 10, 10, 10, 10, 10, 10])}, status="low_coverage", slot_secs=600)
    assert r["over_min"] is None and r["over_lo"] == 2 and r["over_hi"] == 4 and r["over_missing_min"] == 2
    assert "至少 2 分、至多 4 分" in r["over_text"]
    assert r["median"] is None and r["median_note"].startswith("不知道")          # 覆蓋不足：中位數不知道，不以平均或 P95 代替


def test_間隔是推算的_不換算分鐘_兩個點之間的空白不是已知時間():
    """09:00 與 09:10 兩點都是 90、來源沒寫間隔：不能說超標 10 或 20 分鐘。"""
    r = ds({"D": pts([90, 90], itv=600, inferred=True)}, slot_secs=1800)
    assert r["over_min"] is None and r["over_lo"] is None and r["over_hi"] is None and "推算" in r["over_note"]
    assert r["over_text"].startswith("不知道")


def test_彙整桶只有平均與最大值_中位數不可得_超標只給範圍():
    r = ds({"D": pts([40, 60], itv=300, maxs=[90, 95])}, slot_secs=600, bucket=True)
    assert r["median"] is None and "不可得" in r["median_note"]
    assert r["over_lo"] == 0 and r["over_hi"] == 10 and r["over_min"] is None              # 不因為 max 超標就算滿 5 分鐘
    # 有桶內最小值：最小 > 門檻＝整桶確定超標；最大 ≤ 門檻＝確定沒超標 → 可以給精確值
    r = ds({"D": pts([85, 50], itv=180, mins=[81, 40], maxs=[95, 70])}, slot_secs=360, bucket=True)
    assert r["over_min"] == 3 and r["over_lo"] == r["over_hi"] == 3


@pytest.mark.parametrize("status", ["no_data", "not_collected", "coarse_interval"])
def test_無資料_未收集_間隔太粗_都是不知道_不是0(status):
    r = ds({}, status=status, slot_secs=600)
    assert r["median"] is None and r["over_min"] is None and r["over_lo"] is None and r["over_text"].startswith("不知道")


def test_單位不是百分比_超標不適用():
    r = ds({"D": pts([1, 2, 3])}, thr=None)
    assert r["over_text"] == "不適用" and r["median"] == 2


# ---------------------------------------------------------------- 審查補強：每點自己的間隔、裁到時段邊界
def test_同一天混合5分鐘與10分鐘取樣_10分鐘那一點算10分鐘():
    """前兩點來自 5 分鐘一點的來源列、第三點來自 10 分鐘一點的來源列；不能拿當天最小間隔（5 分）套到每一點。"""
    day = [(10, None, None, 0, 300, False), (10, None, None, 300, 300, False), (90, None, None, 600, 600, False)]
    r = ds({"D": day}, slot_secs=1200)
    assert r["over_min"] == 10 and r["over_missing_min"] == 0


def test_每點不超過到下一點的距離_也不超過來源記錄的間隔_多出來的是缺漏():
    # 5 分鐘一點的來源，中間缺一點（00:00、00:10）：第一點只代表 5 分鐘，另外 5 分鐘是沒有資料
    day = [(90, None, None, 0, 300, False), (10, None, None, 600, 300, False)]
    r = ds({"D": day}, slot_secs=900)
    assert r["over_lo"] == 5 and r["over_missing_min"] == 5 and r["over_hi"] == 10 and r["over_min"] is None


def test_取樣點只算落在時段內的部分_時段結束前1分鐘開始的5分鐘點只算1分鐘():
    day = [(10, None, None, 0, 300, False), (10, None, None, 300, 300, False), (90, None, None, 540, 300, False)]
    r = ds({"D": day}, slot_secs=600)
    assert r["over_min"] == 1 and r["over_missing_min"] == 0          # 第二點到下一點只有 4 分鐘；第三點被時段終點裁成 1 分鐘


def test_其餘時間_只算不屬於任何設定時段的部分():
    clip = ca.other_clipper([(540, 570)])                                  # 設定時段 09:00–09:30
    assert clip(8 * 3600 + 58 * 60, 300) == 120                             # 08:58 開始的 5 分鐘點：09:00 以後屬於時段，不算
    assert clip(23 * 3600 + 58 * 60, 300) == 120                            # 不跨到隔天


def test_analyze_host每格都有中位數與超標分鐘_同日兩個來源不混算():
    c = mem()
    put(c, cpu=50.0, start=540, end=570)                      # NMON 09:00–09:30 每分鐘一點、全部 50
    res = ca.analyze_host(c, "H", {**ca.get_config(c), "params": {**ca.get_config(c)["params"], "min_days": 1}}, "2026-09-10", "2026-09-10")
    cell = next(x for x in res["cells"] if x["metric"] == "cpu_busy" and x["slot"] == "open" and x["day_type"] == "normal")
    assert cell["status"] == "ok" and cell["median"] == 50 and cell["over_min"] == 0 and cell["over_missing_min"] == 0
    assert all(k in cell for k in ca.DIST_KEYS)
    # 每格只有一個來源（同日只採一個來源）：不會把兩個來源的樣本加在一起
    assert len({x["source"] for x in res["cells"] if x["metric"] == "cpu_busy" and x["slot"] == "open"}) == 1


def test_日摘要封存記錄中位數與超標分鐘_演算法版本2():
    c = mem()
    put(c, cpu=90.0, start=540, end=570)
    body = ar.summarize(c, "H", "2026-09-10")
    cell = next(x for x in body["cells"] if x["metric"] == "cpu_busy" and x["slot"] == "open")
    assert cell["median"] == 90 and cell["over_min"] == 30 and cell["over_threshold"] == 80
    assert ar.ALGO_VERSION.endswith("/2")


# ---------------------------------------------------------------- 封存引用
def _two_versions(c):
    _old(c, ["2024-09-12", "2024-09-11", "2024-09-10"])
    put(c, day="2024-09-12", cpu=99.0, replace=True)
    ar.archive_one(c, "H", "2024-09-12", "revision", "fp2", "t", now=datetime(2026, 9, 11, 16))
    ids = {r[1]: r[0] for r in c.execute("SELECT id, version FROM capacity_day_archive WHERE day='2024-09-12'")}
    return ids


def test_新增_更正_作廢_只增不改_版本鏈看得到():
    c = mem()
    ids = _two_versions(c)
    a = ar.citation_create(c, {"title": "2026Q3 容量簽呈", "purpose": "簽呈附件", "refs": [f"archive:{ids[1]}"]}, "admin", TODAY)
    c.commit()
    assert a["version"] == 1 and a["status"] == "active" and a["resolved"][0]["rows"] == 1 and a["is_latest"]
    b = ar.citation_revise(c, a["id"], {"refs": [f"archive:{ids[1]}", "2024-09-11|H"]}, "admin", TODAY)
    c.commit()
    assert b["version"] == 2 and b["supersedes_id"] == a["id"] and b["root_id"] == a["id"] and b["title"] == "2026Q3 容量簽呈"
    old = ar.citation_get(c, a["id"])
    assert old["status"] == "superseded" and old["refs"] == [f"archive:{ids[1]}"]                 # 舊版原封不動
    assert [x["version"] for x in b["chain_versions"]] == [2, 1]
    v = ar.citation_void(c, b["id"], "簽呈撤回", "admin", TODAY)
    c.commit()
    assert v["status"] == "voided" and v["void_reason"] == "簽呈撤回" and v["voided_by"] == "admin" and v["refs"] == b["refs"]
    data = ar.citations(c)
    assert data["counts"] == {"active": 0, "superseded": 1, "voided": 1} and data["chains"] == 1
    acts = [r[0] for r in c.execute("SELECT action FROM capacity_day_archive_audit WHERE action LIKE 'citation%' ORDER BY id")]
    assert acts == ["citation_create", "citation_revise", "citation_void"]
    # 資料庫層：引用與作廢紀錄都不能改、不能刪
    for q in ("UPDATE capacity_archive_citation SET title='x'", "DELETE FROM capacity_archive_citation",
              "UPDATE capacity_archive_citation_void SET reason='x'", "DELETE FROM capacity_archive_citation_void"):
        with pytest.raises(sqlite3.DatabaseError):
            c.execute(q)


def test_舊版與作廢的引用_一樣保護被引用的版本鏈():
    c = mem()
    ids = _two_versions(c)                                     # 2024-09-12 兩版、09-11、09-10 各一版，全部過期
    a = ar.citation_create(c, {"title": "報告A", "refs": [f"archive:{ids[1]}"]}, "admin", TODAY)            # 引用 09-12 第 1 版
    ar.citation_revise(c, a["id"], {"refs": ["2024-09-11|H"]}, "admin", TODAY)                         # 更正後改引用 09-11（舊版仍引用 09-12）
    x = ar.citation_create(c, {"title": "報告B", "refs": ["2024-09-10"]}, "admin", TODAY)
    ar.citation_void(c, x["id"], "誤建", "admin", TODAY)                                                  # 作廢的仍保護 09-10
    c.commit()
    pv = ar.purge_preview(c, "admin", TODAY)
    c.commit()
    assert pv["rows"] == 0 and pv["protected_rows"] == 4 and pv["protected_host_days"] == 3
    why = {p_["day"]: " ".join(p_["reasons"]) for p_ in pv["protected_sample"]}
    assert "被更正取代的舊版引用" in why["2024-09-12"] and "封存引用" in why["2024-09-11"] and "已作廢的引用" in why["2024-09-10"]
    # 對照組：沒被引用的過期鏈可以清
    _old(c, ["2024-09-09"])
    pv = ar.purge_preview(c, "admin", TODAY)
    c.commit()
    assert pv["rows"] == 1 and pv["sample"][0]["day"] == "2024-09-09"


@pytest.mark.parametrize("refs,msg", [([], "至少"), (["garbage"], "看不懂"), (["archive:999999"], "找不到"), (["2030-01-01"], "找不到"),
                                      (["2024-09-12|nohost"], "找不到"), ("archive:1", None)])
def test_引用格式與存在性驗證(refs, msg):
    c = mem()
    _old(c, ["2024-09-12"])
    if msg is None:
        rid = c.execute("SELECT id FROM capacity_day_archive").fetchone()[0]
        out = ar.citation_create(c, {"title": "t", "refs": f"archive:{rid}"}, "admin", TODAY)
        assert out["refs"] == [f"archive:{rid}"]
        return
    with pytest.raises(ValueError, match=msg):
        ar.citation_create(c, {"title": "t", "refs": refs}, "admin", TODAY)
    assert ar.citations(c)["rows"] == []


def test_只能更正或作廢最新且未作廢的那一版():
    c = mem()
    _old(c, ["2024-09-12"])
    a = ar.citation_create(c, {"title": "t", "refs": ["2024-09-12"]}, "admin", TODAY)
    b = ar.citation_revise(c, a["id"], {"note": "補說明"}, "admin", TODAY)
    with pytest.raises(ar.ArchiveConflict):
        ar.citation_revise(c, a["id"], {"note": "又改"}, "admin", TODAY)           # 舊版不能再更正
    with pytest.raises(ar.ArchiveConflict):
        ar.citation_void(c, a["id"], "x", "admin", TODAY)                         # 舊版不能作廢
    ar.citation_void(c, b["id"], "撤回", "admin", TODAY)
    with pytest.raises(ar.ArchiveConflict):
        ar.citation_void(c, b["id"], "再一次", "admin", TODAY)
    with pytest.raises(ar.ArchiveConflict):
        ar.citation_revise(c, b["id"], {"note": "作廢後更正"}, "admin", TODAY)
    with pytest.raises(ValueError):
        ar.citation_void(c, b["id"], "  ", "admin", TODAY)
    with pytest.raises(LookupError):
        ar.citation_revise(c, 999, {}, "admin", TODAY)


def test_讀取不建表():
    c = mem()
    assert ar.citations(c) == {"rows": [], "counts": {"active": 0, "superseded": 0, "voided": 0}, "chains": 0}
    assert c.execute("SELECT count(*) FROM sqlite_master WHERE name='capacity_archive_citation'").fetchone()[0] == 0


def test_API_引用寫入只有管理員_稽核同交易_大家可看():
    import db
    qw.set_clock(lambda: datetime(2026, 9, 12, 10))
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            client, p, api = _api(tmp, [("admin", "admin"), ("alice", "user"), ("vera", "viewer")])
            cn = db.get_connection(p)
            ar.ensure(cn)
            put(cn, day="2026-09-10")
            ar.archive_one(cn, "H", "2026-09-10", "final", "fp", "t", now=datetime(2026, 9, 11, 16))
            cn.commit()
            rid = cn.execute("SELECT id FROM capacity_day_archive").fetchone()[0]
            cn.close()
            admin, alice, vera = client("admin"), client("alice"), client("vera")
            body = {"title": "簽呈", "refs": [f"archive:{rid}"]}
            for who in (alice, vera):
                assert who.post("/api/v2/capacity/archive-citations", json=body).status_code == 403
            r = admin.post("/api/v2/capacity/archive-citations", json=body)
            assert r.status_code == 200, r.text
            cid = r.json()["id"]
            for who in (alice, vera):
                assert who.post(f"/api/v2/capacity/archive-citations/{cid}/revise", json={"note": "x"}).status_code == 403
                assert who.post(f"/api/v2/capacity/archive-citations/{cid}/void", json={"reason": "x"}).status_code == 403
                lst = who.get("/api/v2/capacity/archive-citations")
                assert lst.status_code == 200 and lst.json()["counts"]["active"] == 1
            assert admin.post("/api/v2/capacity/archive-citations", json={"title": "x", "refs": ["bad"]}).status_code == 400
            r2 = admin.post(f"/api/v2/capacity/archive-citations/{cid}/revise", json={"note": "更正"})
            assert r2.status_code == 200 and r2.json()["version"] == 2
            assert admin.post(f"/api/v2/capacity/archive-citations/{cid}/revise", json={"note": "再"}).status_code == 409
            assert admin.post("/api/v2/capacity/archive-citations/999/void", json={"reason": "x"}).status_code == 404
            assert admin.post(f"/api/v2/capacity/archive-citations/{r2.json()['id']}/void", json={"reason": "撤回"}).status_code == 200
            assert admin.get(f"/api/v2/capacity/archive-citations/{cid}").json()["status"] == "superseded"
            cn = db.get_connection(p)
            acts = [x[0] for x in cn.execute("SELECT action FROM activity_log WHERE action LIKE 'capacity_citation%' ORDER BY id")]
            assert acts == ["capacity_citation_create", "capacity_citation_revise", "capacity_citation_void"]
            cn.execute("INSERT OR REPLACE INTO app_settings VALUES ('auth_disabled', '1')")
            cn.commit()
            cn.close()
            assert admin.post("/api/v2/capacity/archive-citations", json=body).status_code == 403      # 登入停用：fail-closed
            api.app.dependency_overrides.clear()
    finally:
        qw.set_clock(None)


def test_API_操作紀錄寫不進去_引用一併回滾(monkeypatch):
    import activity
    import db
    qw.set_clock(lambda: datetime(2026, 9, 12, 10))
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            client, p, api = _api(tmp, [("admin", "admin")])
            cn = db.get_connection(p)
            ar.ensure(cn)
            put(cn, day="2026-09-10")
            ar.archive_one(cn, "H", "2026-09-10", "final", "fp", "t", now=datetime(2026, 9, 11, 16))
            cn.commit()
            cn.close()
            admin = client("admin")
            monkeypatch.setattr(activity, "log", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("audit down")))
            r = admin.post("/api/v2/capacity/archive-citations", json={"title": "t", "refs": ["2026-09-10"]})
            assert r.status_code == 500
            cn = db.get_connection(p)
            assert cn.execute("SELECT count(*) FROM capacity_archive_citation").fetchone()[0] == 0
            assert cn.execute("SELECT count(*) FROM capacity_day_archive_audit WHERE action LIKE 'citation%'").fetchone()[0] == 0
            cn.close()
            api.app.dependency_overrides.clear()
    finally:
        qw.set_clock(None)


def test_verify_numbers封存不變式_正常全綠_突變會紅():
    import verify_numbers as vn
    c = mem()
    ids = _two_versions(c)
    a = ar.citation_create(c, {"title": "A", "refs": [f"archive:{ids[1]}"]}, "admin", TODAY)
    ar.citation_revise(c, a["id"], {"refs": ["2024-09-11|H"]}, "admin", TODAY)
    c.commit()
    f0 = vn.FAILS
    vn.check_capacity_archive(c)
    assert vn.FAILS == f0, vn.RESULTS[-6:]
    # 突變：一筆演算法 2 的日摘要格子下限大於上限
    bad = {"cells": [{"over_lo": 5, "over_hi": 1, "over_min": None, "median": None, "bucket": False, "status": "ok"}]}
    c.execute("INSERT INTO capacity_day_archive (host, day, version, stage, algo_version, sources_json, granularity_json, identity_json, summary_json, "
              "content_hash, created_at, created_by) VALUES ('Z','2026-01-05',1,'final',?,'[]','{}','{}',?,'h','t','t')", (ar.ALGO_VERSION, json.dumps(bad)))
    vn.check_capacity_archive(c)
    assert vn.FAILS == f0 + 1
    vn.FAILS = f0



# ---------------------------------------------------------------- 審查補強：引用讀不到一律 fail-closed
@pytest.mark.parametrize("drop", ["capacity_archive_citation_void", "capacity_archive_citation"])
def test_引用表不完整_清理拒絕_讀得到的引用照樣保護(drop):
    c = mem()
    _old(c, ["2024-09-12", "2024-09-11"])
    ar.citation_create(c, {"title": "A", "refs": ["2024-09-12"]}, "admin", TODAY)
    c.commit()
    c.execute(f"DROP TABLE {drop}")
    c.commit()
    pv = ar.purge_preview(c, "admin", TODAY)
    c.commit()
    assert pv["blocked"] and "無法確認" in pv["blocked"] and any("不完整" in u for u in pv["unparsed_refs"])
    if drop == "capacity_archive_citation_void":
        assert pv["protected_rows"] == 1 and pv["protected_sample"][0]["day"] == "2024-09-12"     # 引用列讀得到：照樣保護
    with pytest.raises(ar.ArchiveConflict):
        ar.purge(c, "admin", pv["rows"], pv["cutoff"], True, TODAY)
    assert c.execute("SELECT COUNT(*) FROM capacity_day_archive").fetchone()[0] == 2


def test_引用表讀取出錯_清理拒絕(monkeypatch):
    c = mem()
    _old(c, ["2024-09-12"])
    ar.citation_create(c, {"title": "A", "refs": ["2024-09-12"]}, "admin", TODAY)
    c.commit()
    real = ar._citation_rows_for_protection

    class Boom:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, q, *a):
            if "FROM capacity_archive_citation c" in q:
                raise sqlite3.DatabaseError("disk I/O error")
            return self.conn.execute(q, *a)
    monkeypatch.setattr(ar, "_citation_rows_for_protection", lambda conn: real(Boom(conn)))
    pv = ar.purge_preview(c, "admin", TODAY)
    assert pv["blocked"] and any("讀取失敗" in u for u in pv["unparsed_refs"])


def test_兩張引用表一起建():
    c = mem()
    ar.ensure(c)
    names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'capacity_archive_citation%'")}
    assert names == {"capacity_archive_citation", "capacity_archive_citation_void"}
    # 建表中途失敗：整批 DDL 回滾，不會只剩一張
    c2 = mem()
    bad = list(ar.DDL)
    i = next(n for n, q in enumerate(bad) if "capacity_archive_citation_void (" in q)
    bad.insert(i, "CREATE TABLE this is not sql")
    orig = ar.DDL
    ar.DDL = bad
    try:
        with pytest.raises(sqlite3.OperationalError):
            ar.ensure(c2)
    finally:
        ar.DDL = orig
    assert c2.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'capacity_archive_citation%'").fetchone()[0] == 0



def test_引用表只剩一張_不自動補建_寫入拒絕():
    c = mem()
    _old(c, ["2024-09-12"])
    a = ar.citation_create(c, {"title": "A", "refs": ["2024-09-12"]}, "admin", TODAY)
    c.commit()
    c.execute("DROP TABLE capacity_archive_citation_void")
    c.commit()
    ar.ensure(c)                                                                 # 不會把缺的那張悄悄補回來
    assert c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='capacity_archive_citation_void'").fetchone()[0] == 0
    for fn, args in ((ar.citation_create, ({"title": "B", "refs": ["2024-09-12"]},)), (ar.citation_revise, (a["id"], {"note": "x"})),
                     (ar.citation_void, (a["id"], "x"))):
        with pytest.raises(ar.ArchiveConflict, match="不完整"):
            fn(c, *args, "admin", TODAY)



# ---------------------------------------------------------------- 審查第二輪：同時間取樣、整天沒有取樣、表狀態查不到
def test_同一時間兩個超標取樣只算一次_超標上限不超過時段長度():
    day = [(90, None, None, 0, 300, False), (95, None, None, 0, 300, False), (10, None, None, 300, 300, False)]
    r = ds({"D": day}, slot_secs=600)
    assert r["over_min"] == 5 and r["over_missing_min"] == 0
    many = [(99, None, None, 0, 600, False)] * 5                      # 五個一模一樣時間的點
    r = ds({"D": many}, slot_secs=600)
    assert r["over_min"] == 10 and r["over_hi"] <= 10
    r = ds({"D": many}, slot_secs=600, bucket=True)
    assert r["over_hi"] <= 10


def test_期間內某天這個時段完全沒有取樣_整段算沒有資料():
    full = pts([10] * 10, itv=60)                                       # 10 分鐘時段、1 分鐘一點、全部未超標
    r = ca.dist_stats({"D1": full, "D3": full}, False, "ok", 600, THR, None, ["D1", "D2", "D3"])
    assert r["over_lo"] == 0 and r["over_missing_min"] == 10 and r["over_hi"] == 10 and r["over_min"] is None
    r = ca.dist_stats({"D1": full, "D3": full}, False, "ok", 600, THR, None, ["D1", "D3"])
    assert r["over_min"] == 0                                            # 對照：沒有缺的日子就是精確值 0


def _cpu_open(res, dt="normal"):
    return next(x for x in res["cells"] if x["metric"] == "cpu_busy" and x["slot"] == "open" and x["day_type"] == dt)


def test_來源整天停擺_中間那天完全沒有列_算缺漏且覆蓋率下降():
    """三個交易日的區間，來源在中間那天一筆列都沒有（不是缺幾點）：那天整段 30 分鐘算沒有資料、覆蓋率掉到約 67%。"""
    c = mem()
    for d in ("2026-09-09", "2026-09-11"):                                  # 週三、週五有資料；週四（09-10）整天沒有列
        put(c, day=d, cpu=50.0, start=540, end=570)
    cfg = {**ca.get_config(c), "params": {**ca.get_config(c)["params"], "min_days": 1}}
    res = ca.analyze_host(c, "H", cfg, "2026-09-09", "2026-09-11")
    cell = _cpu_open(res)
    assert cell["samples"] == 60 and cell["expected"] == 90 and cell["coverage_pct"] == 66.7
    assert cell["status"] == "low_coverage" and cell["median"] is None                       # 覆蓋不足 → 中位數無法判定
    assert cell["over_missing_min"] == 30 and cell["over_lo"] == 0 and cell["over_hi"] == 30 and cell["over_min"] is None
    # 對照：三天都有資料 → 覆蓋 100%、精確值 0
    put(c, day="2026-09-10", cpu=50.0, start=540, end=570)
    ca._CACHE.clear() if hasattr(ca, "_CACHE") else None
    cell = _cpu_open(ca.analyze_host(c, "H", cfg, "2026-09-09", "2026-09-11"))
    assert cell["coverage_pct"] == 100.0 and cell["over_min"] == 0 and cell["over_missing_min"] == 0


def test_還沒開始收集的日子不算停擺_週末不算交易日的缺漏():
    c = mem()
    put(c, day="2026-09-11", cpu=50.0, start=540, end=570)                  # 週五才開始有資料；區間從週一開始
    put(c, day="2026-09-14", cpu=50.0, start=540, end=570)                  # 下週一（中間的週六日不是交易日）
    cfg = {**ca.get_config(c), "params": {**ca.get_config(c)["params"], "min_days": 1}}
    cell = _cpu_open(ca.analyze_host(c, "H", cfg, "2026-09-07", "2026-09-14"))
    assert cell["coverage_pct"] == 100.0 and cell["over_missing_min"] == 0


def test_寫入時表狀態查不到_回衝突不是原始錯誤(monkeypatch):
    c = mem()
    _old(c, ["2024-09-12"])

    class Broken:
        def __init__(self, conn):
            self.conn = conn
            self.in_transaction = conn.in_transaction

        def execute(self, q, *a):
            if "sqlite_master" in q and "capacity_archive_citation" in q:
                raise sqlite3.DatabaseError("database disk image is malformed")
            return self.conn.execute(q, *a)

        def __getattr__(self, k):
            return getattr(self.conn, k)
    with pytest.raises(ar.ArchiveConflict, match="無法確認"):
        ar.ensure(Broken(c))
    with pytest.raises(ar.ArchiveConflict, match="無法確認"):
        ar._cite_tables(Broken(c))
    with pytest.raises(ar.ArchiveConflict, match="無法確認"):
        ar.citations(Broken(c))


def test_API_引用表狀態查不到_回409(monkeypatch):
    import db
    qw.set_clock(lambda: datetime(2026, 9, 12, 10))
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            client, p, api = _api(tmp, [("admin", "admin")])
            admin = client("admin")

            def boom(conn):
                raise ar.ArchiveConflict("封存引用的資料表狀態無法確認（測試）")
            monkeypatch.setattr(ar, "_cite_tables", boom)
            r = admin.post("/api/v2/capacity/archive-citations", json={"title": "t", "refs": ["2026-09-10"]})
            assert r.status_code == 409 and "無法確認" in r.json()["detail"]
            assert admin.get("/api/v2/capacity/archive-citations").status_code == 409
            api.app.dependency_overrides.clear()
    finally:
        qw.set_clock(None)



# ---------------------------------------------------------------- 審查第四輪：數字核對的獨立重算也算停擺日（中間與尾端）
def _outage_db():
    from test_capacity_review_fixes import mem_conn, put_nmon
    c = mem_conn()
    for d in ("2026-09-07", "2026-09-08", "2026-09-10", "2026-09-11"):      # 09-09（週三）整天沒有列＝中間停擺
        put_nmon(c, "V2", d, lambda m: 90.0 if 9 * 60 <= m < 9 * 60 + 10 else 5.0, interval=60)
    put_nmon(c, "Z9", "2026-09-14", lambda m: 5.0, interval=60)              # 別台有 09-14 的資料 → 分析區間到 09-14，V2 尾端停擺一天
    return c


def _v2_open(c):
    cfg = ca.get_config(c)
    a = ca.analyze_cached(c, "V2", cfg, "2026-06-17", "2026-09-14")
    return next(x for x in a["cells"] if x["metric"] == "cpu_busy" and x["slot"] == "open" and x["day_type"] == "normal")


def test_停擺日_正式計算含中間與尾端_獨立重算一致():
    from test_capacity_review_fixes import _run_verify
    c = _outage_db()
    cell = _v2_open(c)
    assert cell["expected"] == 6 * 30 and cell["samples"] == 4 * 30                    # 應有 6 個交易日（含 09-09 與 09-14）
    assert cell["over_missing_min"] == 60 and cell["over_lo"] == 40 and cell["over_hi"] == 100
    assert _run_verify(c) == 0


@pytest.mark.parametrize("which", ["middle", "trailing"])
def test_停擺日_正式值漏算_獨立重算會紅(monkeypatch, which):
    """突變：正式計算假裝沒有那一天的停擺（中間或尾端各少算 30 分鐘、少 30 個應有點）→ 數字核對一定要紅。"""
    from test_capacity_review_fixes import _run_verify
    c = _outage_db()
    real = ca.analyze_cached

    def bad(conn, h, cfg, *a, **k):
        import copy
        r = copy.deepcopy(real(conn, h, cfg, *a, **k))
        if h == "V2":
            for x in r["cells"]:
                if x["metric"] == "cpu_busy" and x["slot"] == "open" and x["day_type"] == "normal":
                    x["over_missing_min"] -= 30
                    x["over_hi"] -= 30
                    if which == "trailing":
                        x["expected"] -= 30
        return r
    monkeypatch.setattr(ca, "analyze_cached", bad)
    assert _run_verify(c) >= 1
