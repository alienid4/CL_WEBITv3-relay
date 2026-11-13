"""EOS 批三＋批二補強（2.041.80）。

* 覆蓋率與通知引擎共用同一個候選母體／取值函式（eos_notify.resolve_machines）；同一台多筆不同 OS／型號有確定性並標待核
* 覆蓋率磚與下鑽共用同一份快照（短 TTL＋資料指紋），下鑽伺服器端分頁／排序，size=0 全部
* 合約（contract_row）影子比對與差異報告：只比較、不改任何 EOS 日期／可信度／風險、不寄通知

不連 221、不寄任何信；假 EOS 表＋暫時 SQLite。
"""
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

ROLE_AWARE_TESTS = True
CSRF_RAW_CLIENT = True
ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import db  # noqa: E402
import eos  # noqa: E402
import eos_contract as ec  # noqa: E402
import eos_evidence as ee  # noqa: E402
import eos_notify as en  # noqa: E402
from test_eos_batch2 import FAKE_OS, _api, _hw, _seed  # noqa: E402

TODAY = date(2026, 11, 2)
FAKE_HW = [
    {"name": "Dell PowerEdge R760", "eos_date": "2027-01-01", "source_url": "https://example.invalid/d", "confidence": "confirmed"},
    {"name": "Dell PowerEdge R650", "eos_date": "2026-06", "source_url": "https://example.invalid/e", "confidence": "confirmed"},
]


@pytest.fixture(autouse=True)
def fake_tables():
    eos._os_table = FAKE_OS
    eos._hw_table = FAKE_HW
    ee.clear_snapshot()
    yield
    eos.reload_tables()
    ee.clear_snapshot()


@pytest.fixture()
def conn(tmp_path):
    db.init_db(tmp_path / "t.db")
    c = db.get_connection(tmp_path / "t.db")
    _seed(c)
    try:
        yield c
    finally:
        c.close()


# ===================================================================== A1 覆蓋率共用通知引擎的母體與取值
def test_覆蓋率與通知引擎呼叫同一個函式(conn, monkeypatch):
    calls = []
    real = en.resolve_machines

    def spy(c):
        calls.append(1)
        return real(c)
    monkeypatch.setattr(en, "resolve_machines", spy)
    ee.coverage_rows(conn)
    en.eos_candidates(conn, TODAY)
    assert len(calls) == 2                                           # 兩邊都走它，不各自查詢


def test_覆蓋率與通知取值逐台一致(conn):
    _hw(conn, "S8", "srv-h", "10.99.0.8", os_="Rocky Linux 9", model="Dell PowerEdge R760")
    conn.commit()
    cov = {(r["mk"], r["track"]): r for r in ee.coverage_rows(conn)}
    for c in en.eos_candidates(conn, TODAY)[0]:
        r = cov[(c["mk"], c["track"])]
        assert r["canonical"] == c["item"] and r["bucket"] == "confirmed"


def test_同台多筆不同值_確定性且標待核_不拼接(conn):
    # 同一台（同 hostname＋ip）登記三筆：OS 兩種、型號兩種，且分屬不同列
    _hw(conn, "M3", "multi", "10.99.9.9", os_="Ubuntu 18.04", model="Dell PowerEdge R650")
    _hw(conn, "M1", "multi", "10.99.9.9", os_="Rocky Linux 9", model=None)
    _hw(conn, "M2", "multi", "10.99.9.9", os_=None, model="Dell PowerEdge R760")
    conn.commit()
    rows = [r for r in ee.coverage_rows(conn) if r["hostname"] == "multi"]
    o = next(r for r in rows if r["track"] == "os")
    h = next(r for r in rows if r["track"] == "hw")
    assert o["raw_value"] == "Rocky Linux 9" and o["needs_review"] and o["conflict_values"] == "Rocky Linux 9；Ubuntu 18.04"
    assert h["raw_value"] == "Dell PowerEdge R760" and h["needs_review"] and h["conflict_values"] == "Dell PowerEdge R650；Dell PowerEdge R760"   # 序號最小者(M2)
    # 取值各自來自一列：OS 取 M1、型號取 M2/M3 其一，不拼出新值
    assert o["raw_value"] in {"Rocky Linux 9", "Ubuntu 18.04"} and h["raw_value"] in {"Dell PowerEdge R650", "Dell PowerEdge R760"}
    ov = ee.coverage_overview(conn, ee.coverage_rows(conn))
    assert ov["needs_review"] == 2
    assert len(ee.coverage_drill(ee.coverage_rows(conn), None, None, None, True)) == 2
    # 插入順序顛倒，結果不變（確定性）
    conn.execute("DELETE FROM hardware WHERE hostname='multi'")
    _hw(conn, "M2", "multi", "10.99.9.9", os_=None, model="Dell PowerEdge R760")
    _hw(conn, "M1", "multi", "10.99.9.9", os_="Rocky Linux 9", model=None)
    _hw(conn, "M3", "multi", "10.99.9.9", os_="Ubuntu 18.04", model="Dell PowerEdge R650")
    conn.commit()
    rows2 = [r for r in ee.coverage_rows(conn) if r["hostname"] == "multi"]
    assert {r["track"]: (r["raw_value"], r["conflict_values"]) for r in rows2} == {r["track"]: (r["raw_value"], r["conflict_values"]) for r in rows}


def test_單一值不標待核(conn):
    assert not any(r["needs_review"] for r in ee.coverage_rows(conn))
    assert ee.coverage_overview(conn)["needs_review"] == 0


def test_通知引擎輸出不因共用函式改變(conn):
    c, stats = en.eos_candidates(conn, TODAY)
    assert stats["machines"] == 6
    assert sorted((x["hostname"], x["track"]) for x in c) == [("srv-a", "os"), ("srv-b", "os"), ("srv-f", "hw")]


# ===================================================================== A2 快照與分頁
def test_快照_磚與下鑽同一份_只算一次(conn, monkeypatch):
    n = []
    real = ee.coverage_rows

    def counting(c):
        n.append(1)
        return real(c)
    monkeypatch.setattr(ee, "coverage_rows", counting)
    a = ee.coverage_snapshot(conn, now_mono=100.0)
    b = ee.coverage_snapshot(conn, now_mono=110.0)                   # TTL 內、資料沒變
    assert a is b and len(n) == 1
    c = ee.coverage_snapshot(conn, now_mono=100.0 + ee.SNAPSHOT_TTL + 1)
    assert len(n) == 2 and c["id"] == a["id"]                         # 過期重算（資料沒變，指紋相同）


def test_快照_資料變了立刻作廢(conn):
    a = ee.coverage_snapshot(conn, now_mono=100.0)
    _hw(conn, "S9", "srv-new", "10.99.0.50", os_="Rocky Linux 9")
    conn.commit()
    b = ee.coverage_snapshot(conn, now_mono=101.0)                   # 才 1 秒，但資料指紋變了
    assert b is not a and b["overview"]["machines"] == a["overview"]["machines"] + 1 and b["id"] != a["id"]


def test_快照_別名規則變了也作廢(conn):
    a = ee.coverage_snapshot(conn, now_mono=100.0)
    conn.execute("INSERT INTO normalize_alias (kind, raw_value, canonical) VALUES ('os','Totally Unknown OS 7','Rocky Linux 9')")
    conn.commit()
    b = ee.coverage_snapshot(conn, now_mono=101.0)
    assert b["id"] != a["id"]
    assert b["overview"]["tracks"]["os"]["buckets"]["unmatched"] == a["overview"]["tracks"]["os"]["buckets"]["unmatched"] - 1


def test_快照_不同資料庫不互相沿用(tmp_path):
    outs = []
    for name in ("a.db", "b.db"):
        db.init_db(tmp_path / name)
        c = db.get_connection(tmp_path / name)
        if name == "a.db":
            _seed(c)
        outs.append(ee.coverage_snapshot(c, now_mono=100.0)["overview"]["machines"])
        c.close()
    assert outs == [6, 0]


def test_加總等於下鑽_快照版(conn):
    s = ee.coverage_snapshot(conn)
    for t in s["overview"]["tiles"]:
        assert t["count"] == len(ee.coverage_drill(s["rows"], t["track"], t["bucket"]))


def test_分頁與排序純函式(conn):
    rows = ee.coverage_drill(ee.coverage_rows(conn), "os", None)
    assert ee.page_rows(rows, 1, 0) == rows                           # size=0 全部
    p1, p2 = ee.page_rows(rows, 1, 4), ee.page_rows(rows, 2, 4)
    assert len(p1) == 4 and len(p2) == len(rows) - 4 and p1 + p2 == rows
    s = ee.sort_rows(rows, "hostname", "desc")
    hs = [r["hostname"] for r in s]
    assert hs == sorted(hs, reverse=True)
    assert ee.sort_rows(rows, "evil;drop", "asc") == rows             # 不在白名單：維持預設
    blank = ee.sort_rows(rows, "matched_name", "asc")
    assert all(r["matched_name"] for r in blank[:-1] if r["matched_name"]) and blank[-1]["matched_name"] in (None, "")
    assert [r["no"] for r in ee.sort_rows(rows, "hostname", "asc")] != [] and {r["no"] for r in s} == {r["no"] for r in rows}   # 項次固定


def test_API_下鑽分頁排序匯出全部與磚一致(tmp_path):
    cl, p, api = _api(tmp_path, [("alice", "user")])
    c = db.get_connection(p)
    _seed(c)
    c.close()
    a = cl("alice")
    ov = a.get("/api/eos/coverage").json()
    assert "snapshot" in ov and ov["snapshot"]["id"]
    full = a.get("/api/eos/coverage/rows", params={"track": "os", "size": 0}).json()
    assert full["total"] == len(full["rows"]) == 6 and full["snapshot"]["id"] == ov["snapshot"]["id"]
    pg1 = a.get("/api/eos/coverage/rows", params={"track": "os", "size": 4, "page": 1}).json()
    pg2 = a.get("/api/eos/coverage/rows", params={"track": "os", "size": 4, "page": 2}).json()
    assert pg1["total"] == pg2["total"] == 6 and len(pg1["rows"]) == 4 and len(pg2["rows"]) == 2
    assert pg1["rows"] + pg2["rows"] == full["rows"]
    desc = a.get("/api/eos/coverage/rows", params={"track": "os", "size": 0, "sort": "hostname", "dir": "desc"}).json()["rows"]
    assert [r["hostname"] for r in desc] == sorted((r["hostname"] for r in full["rows"]), reverse=True)
    assert {r["no"] for r in desc} == {r["no"] for r in full["rows"]}
    assert a.get("/api/eos/coverage/rows", params={"dir": "sideways"}).status_code == 422
    assert a.get("/api/eos/coverage/rows", params={"size": -1}).status_code == 422
    for t in ov["tiles"]:
        got = a.get("/api/eos/coverage/rows", params={"track": t["track"], "bucket": t["bucket"], "size": 3}).json()
        assert got["total"] == t["count"]
    assert a.get("/api/eos/coverage/rows", params={"review": "true"}).json()["total"] == ov["needs_review"]


def test_API_覆蓋率讀取不重複全表計算(tmp_path, monkeypatch):
    cl, p, api = _api(tmp_path, [("alice", "user")])
    c = db.get_connection(p)
    _seed(c)
    c.close()
    n = []
    real = ee.coverage_rows
    monkeypatch.setattr(ee, "coverage_rows", lambda cn: (n.append(1), real(cn))[1])
    a = cl("alice")
    a.get("/api/eos/coverage")
    for _ in range(3):
        a.get("/api/eos/coverage/rows", params={"track": "os", "size": 2})
    a.get("/api/eos/evidence")
    assert len(n) == 1


# ===================================================================== B 合約影子比對
def _contract(c, rows, current=1):
    c.execute("INSERT INTO contract_import (filename, imported_by, imported_at, row_count, is_current) VALUES ('c.xlsx','t','2026-11-01',?,?)",
              (len(rows), current))
    iid = c.execute("SELECT MAX(id) FROM contract_import").fetchone()[0]
    for i, r in enumerate(rows, 1):
        c.execute("INSERT INTO contract_row (import_id, row_no, serial, model, hostname, vendor, contract_no, end_date, match_status, match_by, matched_serials) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (iid, i, r.get("serial"), r.get("model"), r.get("hostname"), "V", f"K{i}", r.get("end"),
                   r.get("status", "matched"), r.get("by", "serial"), json.dumps(r.get("serials", []))))
    c.commit()


def _seed_contract(c):
    _hw(c, "H1", "hw-a", "10.99.1.1", model="Dell PowerEdge R760")     # EOS 2027-01-01
    _hw(c, "H2", "hw-b", "10.99.1.2", model="Dell PowerEdge R650")     # EOS 2026-06（月精度）
    _hw(c, "H3", "hw-c", "10.99.1.3")                                   # 型號未填
    _hw(c, "H4", "hw-d", "10.99.1.4", model="Dell PowerEdge R760")
    c.commit()
    _contract(c, [
        {"serial": "H1", "model": "Dell PowerEdge R760", "end": "2027-06-30", "serials": ["H1"]},       # 合約晚於 EOS
        {"serial": "H2", "model": "Dell PowerEdge R650", "end": "2026-06-30", "serials": ["H2"]},       # 月精度 EOS 當月底：不算晚
        {"serial": "H3", "model": "Dell PowerEdge R760", "end": "2027-12-31", "serials": ["H3"]},       # 資產沒型號、合約有 EOS
        {"serial": "H4", "model": "Dell PowerEdge R650", "end": "2026-01-01", "serials": ["H4"]},       # 型號不一致
        {"serial": "ZZ", "model": "Dell PowerEdge R760", "end": "2027-01-01", "status": "unmatched", "serials": []},   # 未對到
        {"serial": "H1", "model": None, "end": None, "serials": ["H1"]},                                 # 缺資料：不補、不比
    ])


def test_影子比對_四類差異各一(conn):
    _seed_contract(conn)
    res = ec.shadow_rows(conn)
    got = {(r["type"], r["row_no"]) for r in res["rows"]}
    assert got == {("contract_after_eos", 1), ("eos_only_in_contract", 3), ("model_mismatch", 4), ("unlinked", 5)}
    assert res["contract_rows"] == 6 and res["compared"] == 5
    by = {r["type"]: r for r in res["rows"]}
    assert "2027-06-30" in by["contract_after_eos"]["detail"] and by["contract_after_eos"]["hostname"] == "hw-a"
    assert by["eos_only_in_contract"]["detail"] == "資產型號未填" and by["eos_only_in_contract"]["eos_date"] == "2027-01-01"
    assert "≠" in by["model_mismatch"]["detail"]
    assert by["unlinked"]["hostname"] is None and "未對到" in by["unlinked"]["detail"]
    assert [r["no"] for r in res["rows"]] == [1, 2, 3, 4]            # 固定項次、依類型順序


def test_影子比對_月精度只在確定晚於時才標(conn):
    _hw(conn, "H2", "hw-b", "10.99.1.2", model="Dell PowerEdge R650")
    conn.commit()
    _contract(conn, [{"serial": "H2", "model": "Dell PowerEdge R650", "end": "2026-06-30", "serials": ["H2"]}])
    assert ec.shadow_rows(conn)["rows"] == []                          # 月底當天：不晚於
    conn.execute("UPDATE contract_row SET end_date='2026-07-01'")
    conn.commit()
    assert [r["type"] for r in ec.shadow_rows(conn)["rows"]] == ["contract_after_eos"]


def test_影子比對_以型號對到的不比型號不一致(conn):
    _hw(conn, "H1", "hw-a", "10.99.1.1", model="Dell PowerEdge R760")
    conn.commit()
    _contract(conn, [{"model": "Dell PowerEdge R650", "end": "2025-01-01", "by": "model", "serials": ["H1"]}])
    assert ec.shadow_rows(conn)["rows"] == []


def test_影子比對_只看目前生效匯入(conn):
    _seed_contract(conn)
    _contract(conn, [{"serial": "H1", "model": "Dell PowerEdge R760", "end": "2026-12-01", "serials": ["H1"]}], current=0)
    assert ec.shadow_rows(conn)["contract_rows"] == 6                 # 舊的（is_current=0）不看


def test_影子比對_舊庫沒有合約表與沒有匯入(tmp_path):
    p = tmp_path / "old.db"
    db.init_db(p)
    c = db.get_connection(p)
    ov = ec.shadow_overview(c)
    assert ov["has_import"] is False and ov["rows"] == [] and ov["tiles"][0]["count"] == 0
    c.execute("DROP TABLE contract_row")
    c.execute("DROP TABLE contract_import")
    c.commit()
    assert ec.shadow_overview(c)["has_import"] is False                # 表不存在也不報錯
    c.close()


def test_影子比對_加總等於下鑽(conn):
    _seed_contract(conn)
    ov = ec.shadow_overview(conn)
    assert ov["tiles"][0]["count"] == len(ov["rows"]) == sum(t["count"] for t in ov["tiles"][1:])
    for t in ov["tiles"][1:]:
        assert len(ec.shadow_overview(conn, t["key"])["rows"]) == t["count"]


def test_影子比對_只讀_不改EOS不改可信度不影響通知(tmp_path):
    p = tmp_path / "ro.db"
    db.init_db(p)
    c = db.get_connection(p)
    _seed(c)
    _seed_contract(c)
    before_eos = json.dumps([eos._os_entries(), eos._hw_entries()], sort_keys=True)
    cand_before = en.eos_candidates(c, TODAY)
    trust_before = [eos.trust_level(e) for _, e in ee.all_entries()]
    c.close()
    raw = p.read_bytes()
    ro = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    ec.shadow_overview(ro)
    ro.close()
    assert p.read_bytes() == raw                                       # 唯讀：不寫庫、不建表
    c = db.get_connection(p)
    assert json.dumps([eos._os_entries(), eos._hw_entries()], sort_keys=True) == before_eos
    assert en.eos_candidates(c, TODAY) == cand_before
    assert [eos.trust_level(e) for _, e in ee.all_entries()] == trust_before
    assert not ee._tbl(c, "eos_notify_log") or c.execute("SELECT COUNT(*) FROM eos_notify_log").fetchone()[0] == 0   # 不寄通知
    c.close()


def test_影子比對_說明不含核准一詞(conn):
    _seed_contract(conn)
    blob = json.dumps(ec.shadow_overview(conn), ensure_ascii=False) + "".join(ec.TYPE_LABEL.values()) + ec.EFFECT_NOTE
    assert "核准" not in blob and "不知道" not in blob
    assert "不會改" in ec.EFFECT_NOTE


def test_API_合約影子比對(tmp_path):
    cl, p, api = _api(tmp_path, [("vera", "viewer"), ("alice", "user")])
    c = db.get_connection(p)
    _seed(c)
    _seed_contract(c)
    c.close()
    for who in ("vera", "alice"):
        a = cl(who)
        ov = a.get("/api/eos/contract-shadow").json()
        assert ov["has_import"] and ov["tiles"][0]["count"] == len(ov["rows"]) == 4
        assert "不會改" in ov["effect_note"]
        for t in ov["tiles"][1:]:
            assert len(a.get("/api/eos/contract-shadow", params={"flag": t["key"]}).json()["rows"]) == t["count"]
        assert a.get("/api/eos/contract-shadow", params={"flag": "zzz"}).status_code == 400
    # 沒有任何寫入入口
    assert a.cl.post("/api/eos/contract-shadow", json={}, headers=a._h()).status_code in (404, 405)


def test_API_合約影子比對_需登入(tmp_path):
    cl, p, api = _api(tmp_path, [("alice", "user")])
    from fastapi.testclient import TestClient
    anon = TestClient(api.app, raise_server_exceptions=False)
    assert anon.get("/api/eos/contract-shadow").status_code in (401, 403)


def test_verify_numbers_新增檢查乾淨資料全過(conn):
    import verify_numbers as vn
    _seed_contract(conn)
    vn.RESULTS.clear()
    vn.CHECKS = vn.FAILS = 0
    vn.check_eos_evidence(conn, TODAY)
    bad = [r for r in vn.RESULTS if r["page"] == "EOS 證據與覆蓋" and r["ok"] is not True]
    assert not bad, bad
    names = " ".join(r["name"] for r in vn.RESULTS)
    assert "合約影子比對" in names and "待核數" in names and "共用的快照" in names


def test_影子比對_標已對到卻找不到現役資產_列為未對到(conn):
    _hw(conn, "R1", "retired-x", "10.99.1.9", model="Dell PowerEdge R760", status="報廢")
    conn.commit()
    _contract(conn, [
        {"serial": "R1", "model": "Dell PowerEdge R760", "end": "2027-01-01", "serials": ["R1"]},      # 退役資產
        {"serial": "Q", "model": "Dell PowerEdge R760", "end": "2027-01-01", "serials": []},            # 沒有序號
    ])
    conn.execute("UPDATE contract_row SET matched_serials='{壞掉' WHERE row_no=2")                     # JSON 壞掉
    conn.commit()
    res = ec.shadow_rows(conn)
    assert [(r["type"], r["row_no"]) for r in res["rows"]] == [("unlinked", 1), ("unlinked", 2)]
    assert all("找不到對應的現役資產" in r["detail"] for r in res["rows"]) and res["compared"] == 0
