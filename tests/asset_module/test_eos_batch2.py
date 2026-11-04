"""EOS 批二（2.041.73）：官方證據、待重查、覆蓋率與未匹配、對照規則歷史、可信度語意統一。

不連 221、不寄任何信；假 EOS 表（monkeypatch eos._os_table／_hw_table）＋暫時 SQLite。
"""
import io
import re
import sqlite3
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

import pytest

ROLE_AWARE_TESTS = True
CSRF_RAW_CLIENT = True       # 自己帶 CSRF header（不依賴 conftest 的自動補丁）
ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import db  # noqa: E402
import eos  # noqa: E402
import eos_evidence as ee  # noqa: E402
import eos_follow as ef  # noqa: E402
import eos_notify as en  # noqa: E402

TODAY = date(2026, 11, 2)
NOW = datetime(2026, 11, 2, 10, 0, 0)

FAKE_OS = [
    {"name": "Rocky Linux 9", "eos_kind": "End of Life", "eos_date": "2027-03-01", "source_url": "https://example.invalid/r", "confidence": "confirmed"},
    {"name": "Ubuntu 18.04", "eos_kind": "End of Support", "eos_date": "2023-05", "source_url": "https://example.invalid/u", "confidence": "confirmed"},
    {"name": "Legacy OS 1", "eos_date": "2030-01-01", "source_url": "https://example.invalid/l", "confidence": "unverified"},
    {"name": "Forum OS", "eos_date": None, "source_url": None, "confidence": "source_rejected"},
    {"name": "Attach OS", "eos_date": "2029-12-31", "source_url": "https://example.invalid/a", "confidence": "confirmed_via_attachment"},
]
FAKE_HW = [{"name": "Dell PowerEdge R760", "eos_date": "2027-01-01", "source_url": "https://example.invalid/d", "confidence": "confirmed"}]


@pytest.fixture(autouse=True)
def fake_tables():
    eos._os_table = FAKE_OS
    eos._hw_table = FAKE_HW
    yield
    eos.reload_tables()


def _hw(c, serial, host, ip, os_=None, model=None, status="使用中"):
    c.execute("INSERT INTO hardware (asset_serial, hostname, ip, os, device_model, asset_status, environment) VALUES (?,?,?,?,?,?,'正式')",
              (serial, host, ip, os_, model, status))


def _seed(c):
    _hw(c, "S1", "srv-a", "10.99.0.1", os_="Rocky Linux 9")                                   # os 已確認
    _hw(c, "S2", "srv-b", "10.99.0.2", os_="Ubuntu 18.04")                                    # os 已確認（月精度）
    _hw(c, "S3", "srv-c", "10.99.0.3", os_="Legacy OS 1")                                     # os 推定
    _hw(c, "S4", "srv-d", "10.99.0.4", os_="Forum OS")                                        # os 未知（表內有、無可用日期）
    _hw(c, "S5", "srv-e", "10.99.0.5", os_="Totally Unknown OS 7")                            # os 未匹配
    _hw(c, "S6", "srv-f", "10.99.0.6", model="Dell PowerEdge R760")                           # hw 已確認，os 未填
    _hw(c, "S7", "srv-g", "10.99.0.7", os_="Rocky Linux 9", status="報廢")                  # 退役不計
    c.commit()


@pytest.fixture()
def conn(tmp_path):
    db.init_db(tmp_path / "t.db")
    c = db.get_connection(tmp_path / "t.db")
    _seed(c)
    try:
        yield c
    finally:
        c.close()


FULL = {"verified_by": "王小明", "verified_on": "2026-10-30", "quote_text": "Rocky Linux 9 reaches end of life on 2027-03-01.",
        "scope_text": "Rocky Linux 9.x 全部小版本", "date_precision": "day"}


# ===================================================================== 可信度語意
def test_trust_level_三詞與is_trusted同一把尺():
    assert eos.trust_level(FAKE_OS[0]) == "confirmed" and eos.TRUST_LABEL["confirmed"] == "已確認"
    assert eos.trust_level(FAKE_OS[1]) == "confirmed"            # 月精度仍是已確認
    assert eos.trust_level(FAKE_OS[2]) == "presumed" and eos.TRUST_LABEL["presumed"] == "推定"
    assert eos.trust_level(FAKE_OS[3]) == "unknown" and eos.TRUST_LABEL["unknown"] == "未知"
    assert eos.trust_level(FAKE_OS[4]) == "confirmed"
    assert eos.trust_level(None) == "unknown"
    assert eos.trust_level({"eos_date": "9999-99-99", "confidence": "confirmed"}) == "unknown"
    assert eos.trust_level({"eos_date": None, "confidence": "no_single_date"}) == "unknown"
    for e in FAKE_OS + FAKE_HW + [None]:
        assert (eos.trust_level(e) == "confirmed") == eos.is_trusted(e)


def test_date_precision():
    assert [eos.date_precision(x) for x in ("2027-03-01", "2029-07", None, "2027-02-30", "junk")] == ["day", "month", "none", "none", "none"]
    a = eos.assess(FAKE_OS[1])
    assert a["trust_label"] == "已確認" and a["date_precision"] == "month"


def test_Excel匯出可信度用統一用詞_且追加證據欄位(tmp_path):
    from openpyxl import load_workbook
    cl, p, api = _api(tmp_path, [("admin", "admin")])
    c = db.get_connection(p)
    _seed(c)
    ee.save_evidence(c, "os", "Rocky Linux 9", FULL, None, "alice", NOW)
    c.close()
    r = cl("admin").get("/api/eos/export")
    assert r.status_code == 200
    wb = load_workbook(io.BytesIO(r.content))
    rows = [list(row) for ws in wb.worksheets for row in ws.iter_rows(values_only=True)]
    head = next(x for x in rows if x and "可信度" in x)
    assert head[-5:] == ["日期精度", "來源核對碼", "證據完整度", "查證人", "查證日期"]
    ci = head.index("可信度")
    vals = {x[ci] for x in rows if x is not head and x and x[ci] not in (None, "可信度")}
    assert vals <= {"已確認", "推定", "未知"} and "已確認" in vals
    rocky = next(x for x in rows if x and x[head.index("主機名稱")] == "srv-a")
    assert rocky[-5:] == ["day", "confirmed", "證據完整", "alice", "2026-10-30"]
    ubuntu = next(x for x in rows if x and x[head.index("主機名稱")] == "srv-b")
    assert ubuntu[-5] == "month" and ubuntu[-3] == "尚無證據記錄"


def test_通知信頁尾用統一用詞(conn):
    foot = "\n".join(en._footer({"machines": 3, "hw_with_date": 1, "os_with_date": 2, "unverified": 1}))
    assert "「已確認」" in foot and "推定" in foot and "未知" in foot and "來源未核對" in foot


def test_ICS只含已確認_描述帶可信度用詞():
    vp = ROOT / "APP" / "asset-module" / "frontend" / "pages" / "eos.vue"
    t = vp.read_text(encoding="utf-8")
    assert "可信度：${r.trustLabel}" in t
    ics = (ROOT / "APP" / "asset-module" / "frontend" / "utils" / "eosIcs.ts").read_text(encoding="utf-8")
    assert "it.trusted !== true" in ics and "trustLabel" in ics


# ===================================================================== 證據
def test_證據新增_完整度_缺什麼就說缺什麼(conn):
    with pytest.raises(ValueError, match="官方原文引用"):
        ee.save_evidence(conn, "os", "Rocky Linux 9", {"verified_on": "2026-10-30"}, None, "alice", NOW)
    with pytest.raises(ValueError, match="查證日期"):
        ee.save_evidence(conn, "os", "Rocky Linux 9", {"quote_text": "x"}, None, "alice", NOW)
    out = ee.save_evidence(conn, "os", "Rocky Linux 9", {"verified_on": "2026-10-30", "quote_text": "原文"}, None, "alice", NOW)
    assert out["state"] == "partial" and "適用範圍" in out["missing"] and "日期精度" in out["missing"]
    assert out["evidence"]["verified_by"] == "alice"            # 沒填查證人 → 記為操作者
    out = ee.save_evidence(conn, "os", "Rocky Linux 9", {"scope_text": "9.x", "date_precision": "day"}, "補齊", "alice", NOW, expected_version=1)
    assert out["state"] == "complete" and out["missing"] == []


def test_日期精度必須與表內日期一致_零寬字元視為空(conn):
    out = ee.save_evidence(conn, "os", "Ubuntu 18.04", {**FULL, "date_precision": "day"}, None, "a", NOW)
    assert out["state"] == "partial" and any("日期精度" in m for m in out["missing"])      # 表內是 2023-05（月）
    out = ee.save_evidence(conn, "os", "Ubuntu 18.04", {"date_precision": "month"}, "改正", "a", NOW, expected_version=1)
    assert out["state"] == "complete"
    c = conn
    c.execute("UPDATE eos_evidence SET scope_text = ? WHERE item_name = 'Ubuntu 18.04'", (chr(0x200b) * 2 + " ",))
    c.commit()
    ev = ee.evidence_map(c)[("os", "Ubuntu 18.04")]
    assert ee.evidence_state(FAKE_OS[1], ev, TODAY)[0] == "partial"


def test_查證日期不能在未來_網址格式_未知欄位(conn):
    with pytest.raises(ValueError, match="未來"):
        ee.save_evidence(conn, "os", "Rocky Linux 9", {**FULL, "verified_on": "2026-11-03"}, None, "a", NOW)
    with pytest.raises(ValueError, match="有效日期"):
        ee.save_evidence(conn, "os", "Rocky Linux 9", {**FULL, "verified_on": "2026-02-30"}, None, "a", NOW)
    with pytest.raises(ValueError, match="http"):
        ee.save_evidence(conn, "os", "Rocky Linux 9", {**FULL, "source_url": "javascript:alert(1)"}, None, "a", NOW)
    with pytest.raises(ValueError, match="不認得"):
        ee.save_evidence(conn, "os", "Rocky Linux 9", {**FULL, "eos_date": "2099-01-01"}, None, "a", NOW)
    with pytest.raises(LookupError):
        ee.save_evidence(conn, "os", "不存在的項目", FULL, None, "a", NOW)
    with pytest.raises(ValueError, match="kind"):
        ee.save_evidence(conn, "zz", "Rocky Linux 9", FULL, None, "a", NOW)
    assert not ee._tbl(conn, "eos_evidence") or conn.execute("SELECT COUNT(*) FROM eos_evidence").fetchone()[0] == 0


def test_更正要理由_版本鎖_歷史前後值(conn):
    ee.save_evidence(conn, "os", "Rocky Linux 9", FULL, None, "a", NOW)
    with pytest.raises(ValueError, match="理由"):
        ee.save_evidence(conn, "os", "Rocky Linux 9", {"scope_text": "新"}, None, "a", NOW, expected_version=1)
    with pytest.raises(ee.Conflict):
        ee.save_evidence(conn, "os", "Rocky Linux 9", {"scope_text": "新"}, "改", "a", NOW, expected_version=7)
    with pytest.raises(ValueError, match="不能清空"):
        ee.save_evidence(conn, "os", "Rocky Linux 9", {"quote_text": None}, "清", "a", NOW, expected_version=1)
    out = ee.save_evidence(conn, "os", "Rocky Linux 9", {"scope_text": "新範圍"}, "官方更新了範圍", "b", NOW, expected_version=1)
    assert out["changed"] and out["evidence"]["version"] == 2 and out["evidence"]["updated_by"] == "b"
    same = ee.save_evidence(conn, "os", "Rocky Linux 9", {"scope_text": "新範圍"}, "沒變", "b", NOW, expected_version=2)
    assert same["changed"] is False
    h = ee.evidence_detail(conn, "os", "Rocky Linux 9", TODAY)["history"]
    assert [x["op"] for x in h] == ["update", "create"] and "新範圍" in h[0]["after_json"] and "9.x" in h[0]["before_json"] and h[0]["reason"] == "官方更新了範圍"


def test_稽核與資料同交易_稽核失敗整筆回滾(conn):
    def boom(c, info):
        raise RuntimeError("audit down")
    with pytest.raises(RuntimeError):
        ee.save_evidence(conn, "os", "Rocky Linux 9", FULL, None, "a", NOW, audit_hook=boom)
    assert conn.execute("SELECT COUNT(*) FROM eos_evidence").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM eos_evidence_history").fetchone()[0] == 0
    seen = []
    ee.save_evidence(conn, "os", "Rocky Linux 9", FULL, None, "a", NOW, audit_hook=lambda c, i: seen.append(i))
    assert seen[0]["op"] == "create" and seen[0]["item_key"] == "os|Rocky Linux 9" and "quote_text" in seen[0]["fields"]


def test_證據不改EOS表與可信度_也不改通知候選(conn):
    before_tbl = [dict(e) for e in FAKE_OS]
    before_c = en.eos_candidates(conn, TODAY)[0]
    ee.save_evidence(conn, "os", "Legacy OS 1", FULL, None, "a", NOW)          # 對「推定」項目補完整證據
    assert FAKE_OS == before_tbl
    assert eos.trust_level(FAKE_OS[2]) == "presumed"
    assert en.eos_candidates(conn, TODAY)[0] == before_c


# ===================================================================== 待重查
def test_待重查_舊confirmed無完整證據才列_補齊後離開(conn):
    ov = ee.evidence_overview(conn, TODAY)
    t = {x["key"]: x["count"] for x in ov["tiles"]}
    assert t["all"] == 6 and t["recheck"] == 4                       # confirmed×3（含 attachment）＋ hw confirmed；Legacy/Forum 不是 confirmed
    names = {r["name"] for r in ov["rows"] if r["recheck"]}
    assert names == {"Rocky Linux 9", "Ubuntu 18.04", "Attach OS", "Dell PowerEdge R760"}
    assert t["presumed"] == 1 and t["unknown"] == 1
    assert t["complete"] + t["partial"] + t["none"] == t["all"]
    ee.save_evidence(conn, "os", "Rocky Linux 9", FULL, None, "a", NOW)
    ov = ee.evidence_overview(conn, TODAY)
    t = {x["key"]: x["count"] for x in ov["tiles"]}
    assert t["recheck"] == 3 and t["complete"] == 1
    assert all(t2["count"] == len(ee.evidence_drill(ov, t2["key"])) for t2 in ov["tiles"])        # 加總＝下鑽
    assert [r["no"] for r in ov["rows"]] == list(range(1, 7))                                    # 項次固定
    ee.save_evidence(conn, "os", "Ubuntu 18.04", {**FULL, "date_precision": "day"}, None, "a", NOW)  # 精度不符：仍待重查
    ov = ee.evidence_overview(conn, TODAY)
    ub = next(r for r in ov["rows"] if r["name"] == "Ubuntu 18.04")
    assert ub["recheck"] and "日期精度" in ub["recheck_reason"]


def test_待重查旗標不改既有日期與通知(conn):
    n_before = len(en.eos_candidates(conn, TODAY)[0])
    ee.evidence_overview(conn, TODAY)
    assert len(en.eos_candidates(conn, TODAY)[0]) == n_before


# ===================================================================== 覆蓋率
def test_覆蓋率_每列一格_加總等於下鑽(conn):
    rows = ee.coverage_rows(conn)
    ov = ee.coverage_overview(conn, rows)
    assert ov["machines"] == 6                                       # 退役不計
    o, h = ov["tracks"]["os"]["buckets"], ov["tracks"]["hw"]["buckets"]
    assert o == {"confirmed": 2, "presumed": 1, "unknown": 1, "unmatched": 1, "no_value": 1, "not_applicable": 0}
    assert h["confirmed"] == 1 and h["no_value"] == 5 and sum(h.values()) == 6
    for t in ov["tracks"].values():
        assert sum(t["buckets"].values()) == t["total"] == ov["machines"]
    assert all(x["count"] == len(ee.coverage_drill(rows, x["track"], x["bucket"])) for x in ov["tiles"])
    assert ov["tracks"]["os"]["confirmed_rate"] == round(2 / 5 * 100, 1)
    d = ee.coverage_drill(rows, "os", "unmatched")
    assert [x["hostname"] for x in d] == ["srv-e"] and [x["no"] for x in d] == [1]


def test_未匹配清單_彙總_下鑽_項次(conn):
    _hw(conn, "S8", "srv-h", "10.99.0.8", os_="Totally Unknown OS 7")
    _hw(conn, "S9", "srv-i", "10.99.0.9", os_="Another Mystery")
    conn.commit()
    rows = ee.coverage_rows(conn)
    ov = ee.coverage_overview(conn, rows)
    um = ov["unmatched"]
    assert [(g["canonical"], g["machines"]) for g in um][0][1] == 2 and [g["no"] for g in um] == list(range(1, len(um) + 1))
    assert sum(g["machines"] for g in um if g["track"] == "os") == ov["tracks"]["os"]["buckets"]["unmatched"] == len(ee.coverage_drill(rows, "os", "unmatched"))
    top = um[0]
    assert len(ee.coverage_drill(rows, "os", "unmatched", top["canonical"])) == top["machines"]


def test_覆蓋率只讀_不建表不寫庫(tmp_path):
    p = tmp_path / "ro.db"
    db.init_db(p)
    c = db.get_connection(p)
    _seed(c)
    c.close()
    before = p.read_bytes()
    ro = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    ee.coverage_overview(ro)
    ee.evidence_overview(ro, TODAY)
    ee.mapping_history(ro)
    ee.evidence_detail(ro, "os", "Rocky Linux 9", TODAY)
    ro.close()
    assert p.read_bytes() == before
    c = db.get_connection(p)
    assert not ee._tbl(c, "eos_evidence") and not ee._tbl(c, "eos_mapping_history")
    c.close()


def test_覆蓋率母體與通知引擎同一批機器(conn):
    rows = ee.coverage_rows(conn)
    covered = {r["mk"] for r in rows if r["track"] == "os" and r["bucket"] in ("confirmed", "presumed")}
    cand_mk = {c["mk"] for c in en.eos_candidates(conn, TODAY)[0] if c["track"] == "os"}
    assert cand_mk == {r["mk"] for r in rows if r["track"] == "os" and r["bucket"] == "confirmed"}   # 通知只含已確認
    assert cand_mk <= covered


# ===================================================================== 對照規則歷史
def _api(tmp, users):
    import api
    import auth
    from fastapi.testclient import TestClient
    p = Path(tmp) / "t.db"
    db.init_db(p)

    def _ov():
        cn = db.get_connection(p)
        try:
            yield cn
        finally:
            cn.close()
    api.app.dependency_overrides[api.get_db] = _ov
    cn = db.get_connection(p)
    for name, role in users:
        db.create_user(cn, name, auth.hash_password("test-password-123"))
        cn.execute("UPDATE users SET role = ? WHERE username = ?", (role, name))
    cn.commit()
    cn.close()

    class C:
        def __init__(self, name):
            self.cl = TestClient(api.app, raise_server_exceptions=False)
            assert self.cl.post("/api/auth/login", json={"username": name, "password": "test-password-123"}).status_code == 200

        def _h(self):
            return {"X-CSRF-Token": self.cl.cookies.get("csrf_token") or ""}

        def get(self, url, **kw):
            return self.cl.get(url, **kw)

        def put(self, url, json=None):
            return self.cl.put(url, json=json, headers=self._h())

        def post(self, url, json=None):
            return self.cl.post(url, json=json, headers=self._h())

        def delete(self, url):
            return self.cl.delete(url, headers=self._h())

    def client(name):
        return C(name)
    return client, p, api


def test_對照規則歷史_別名與覆寫的新增更正刪除(tmp_path):
    cl, p, api = _api(tmp_path, [("admin", "admin"), ("alice", "user")])
    a = cl("alice")
    assert a.post("/api/normalize/alias", {"kind": "os", "raw_value": "RHEL 9.x", "canonical": "Red Hat Enterprise Linux 9"}).status_code == 200
    assert a.post("/api/normalize/alias", {"kind": "os", "raw_value": "RHEL 9.x", "canonical": "Red Hat Enterprise Linux 9.4", "note": "改"}).status_code == 200
    aid = a.get("/api/normalize/alias").json()[0]["id"]
    assert a.delete(f"/api/normalize/alias/{aid}").status_code == 200
    assert a.post("/api/normalize/canonical-override", {"kind": "os", "old_canonical": "X1", "new_canonical": "X2"}).status_code == 200
    assert a.delete("/api/normalize/canonical-override/os/X1").status_code == 200
    assert a.post("/api/eos/category-override", {"canonical": "X2", "category": "firmware"}).status_code == 200
    assert a.post("/api/eos/category-override", {"canonical": "X2", "category": "other"}).status_code == 200
    assert a.delete("/api/eos/category-override/X2").status_code == 200
    h = a.get("/api/eos/mapping-history").json()["rows"]
    assert [(x["rule_table"], x["op"]) for x in reversed(h)] == [
        ("normalize_alias", "create"), ("normalize_alias", "update"), ("normalize_alias", "delete"),
        ("normalize_canonical_override", "create"), ("normalize_canonical_override", "delete"),
        ("eos_category_override", "create"), ("eos_category_override", "update"), ("eos_category_override", "delete")]
    assert [x["no"] for x in h] == list(range(1, 9)) and all(x["by"] == "alice" for x in h)
    upd = next(x for x in h if x["rule_table"] == "normalize_alias" and x["op"] == "update")
    assert "Red Hat Enterprise Linux 9" in upd["before_json"] and "9.4" in upd["after_json"]
    assert all(x["rule_label"] and x["op_label"] for x in h)
    # 規則本身的行為不變
    assert a.delete("/api/normalize/alias/99999").status_code == 404
    assert a.delete("/api/eos/category-override/nope").status_code == 404
    assert a.post("/api/eos/category-override", {"canonical": "X", "category": "bad"}).status_code == 400


def test_對照規則歷史寫不進去_規則變更整筆回滾(tmp_path):
    cl, p, api = _api(tmp_path, [("alice", "user")])
    c = db.get_connection(p)
    ee.ensure_tables(c)
    c.execute("CREATE TRIGGER no_hist BEFORE INSERT ON eos_mapping_history BEGIN SELECT RAISE(ABORT, 'history down'); END")
    c.commit()
    c.close()
    a = cl("alice")
    r = a.post("/api/normalize/alias", {"kind": "os", "raw_value": "RHEL 9.x", "canonical": "RHEL 9"})
    assert r.status_code == 500
    c = db.get_connection(p)
    assert c.execute("SELECT COUNT(*) FROM normalize_alias").fetchone()[0] == 0          # 規則沒有悄悄生效
    c.close()


# ===================================================================== API
def test_API_證據權限_viewer不能寫_user可寫_未知欄位400_未知項目404(tmp_path):
    cl, p, api = _api(tmp_path, [("admin", "admin"), ("alice", "user"), ("vera", "viewer")])
    body = {"kind": "os", "name": "Rocky Linux 9", **FULL}
    # 這支測試要用真實 EOS 表的項目：挑表內第一個 os 項目名
    eos.reload_tables()
    name = eos._os_entries()[0]["name"]
    body = {"kind": "os", "name": name, **FULL}
    vera, alice = cl("vera"), cl("alice")
    assert vera.put("/api/eos/evidence", body).status_code == 403
    ov = vera.get("/api/eos/evidence").json()
    assert ov["can_write"] is False and "只能看" in ov["write_block"]
    assert alice.get("/api/eos/evidence").json()["can_write"] is True
    assert alice.put("/api/eos/evidence", {**body, "eos_date": "2099-01-01"}).status_code == 400
    assert alice.put("/api/eos/evidence", {**body, "name": "不存在"}).status_code == 404
    ok = alice.put("/api/eos/evidence", body)
    assert ok.status_code == 200 and ok.json()["changed"] is True and ok.json()["evidence"]["updated_by"].startswith("alice")
    assert alice.put("/api/eos/evidence", {**body, "scope_text": "x"}).status_code == 400            # 更正沒理由
    assert alice.put("/api/eos/evidence", {**body, "scope_text": "x", "reason": "r", "expected_version": 9}).status_code == 409
    ok2 = alice.put("/api/eos/evidence", {**body, "scope_text": "x", "reason": "r", "expected_version": 1})
    assert ok2.status_code == 200 and ok2.json()["evidence"]["version"] == 2
    det = alice.get("/api/eos/evidence/detail", params={"kind": "os", "name": name}).json()
    assert det["evidence"]["scope_text"] == "x" and len(det["history"]) == 2
    assert alice.get("/api/eos/evidence/detail", params={"kind": "os", "name": "沒有"}).status_code == 404
    assert alice.get("/api/eos/evidence", params={"flag": "zzz"}).status_code == 400
    assert alice.get("/api/eos/coverage/rows", params={"track": "zz"}).status_code == 400
    assert alice.get("/api/eos/coverage/rows", params={"bucket": "zz"}).status_code == 400
    # 稽核同交易留下紀錄、且不含原文全文
    c = db.get_connection(p)
    acts = [r["path"] for r in c.execute("SELECT path FROM activity_log WHERE action LIKE 'eos_evidence_%'")]
    c.close()
    assert len(acts) == 2 and all("EOS 證據" in a and FULL["quote_text"] not in a for a in acts)


def test_API_登入停用時證據寫入fail_closed(tmp_path):
    cl, p, api = _api(tmp_path, [("admin", "admin")])
    adm = cl("admin")
    import routers.vulns as rv
    orig = rv.auth_disabled
    rv.auth_disabled = lambda conn: True
    try:
        name = eos._os_entries()[0]["name"]
        r = adm.put("/api/eos/evidence", {"kind": "os", "name": name, **FULL})
        assert r.status_code == 403 and "登入停用" in r.json()["detail"]
        assert adm.get("/api/eos/evidence").json()["can_write"] is False
    finally:
        rv.auth_disabled = orig


def test_API_覆蓋率端點與下鑽一致(tmp_path):
    cl, p, api = _api(tmp_path, [("alice", "user")])
    c = db.get_connection(p)
    _seed(c)
    c.close()
    a = cl("alice")
    ov = a.get("/api/eos/coverage").json()
    assert ov["machines"] == 6
    for t in ov["tiles"]:
        got = a.get("/api/eos/coverage/rows", params={"track": t["track"], "bucket": t["bucket"]}).json()["rows"]
        assert len(got) == t["count"]
    g = ov["unmatched"][0]
    rows = a.get("/api/eos/coverage/rows", params={"track": g["track"], "bucket": "unmatched", "item": g["canonical"]}).json()["rows"]
    assert len(rows) == g["machines"]
    evo = a.get("/api/eos/evidence").json()
    assert {t["key"] for t in evo["tiles"]} >= {"all", "recheck", "complete", "partial", "none", "presumed", "unknown"}
    flt = a.get("/api/eos/evidence", params={"flag": "recheck"}).json()
    assert len(flt["rows"]) == next(t["count"] for t in flt["tiles"] if t["key"] == "recheck")


# ===================================================================== verify_numbers
def test_verify_numbers_EOS證據與覆蓋_乾淨資料全過_造假會變紅(conn, monkeypatch):
    import verify_numbers as vn
    ee.save_evidence(conn, "os", "Rocky Linux 9", FULL, None, "alice", NOW)
    ee.log_mapping_change(conn, "normalize_alias", "create", {"kind": "os", "raw_value": "x"}, None, {"canonical": "y"}, "alice", NOW)
    conn.commit()
    vn.RESULTS.clear()
    vn.CHECKS = vn.FAILS = 0
    vn.check_eos_evidence(conn, TODAY)
    bad = [r for r in vn.RESULTS if r["page"] == "EOS 證據與覆蓋" and r["ok"] is not True]
    assert vn.CHECKS >= 15 and not bad, bad
    # 造假：磚數字和下鑽不同源 → 觀察模式下是 ⚠（None）；開 strict 變 ❌
    real = ee.coverage_overview

    def fake(c, rows=None):
        o = real(c, rows)
        o["tiles"] = [{**t, "count": t["count"] + (1 if t["key"] == "os|confirmed" else 0)} for t in o["tiles"]]
        return o
    monkeypatch.setattr(ee, "coverage_overview", fake)
    vn.RESULTS.clear()
    vn.CHECKS = vn.FAILS = 0
    vn.check_eos_evidence(conn, TODAY)
    assert any(r["ok"] is None and "磚的數字" in r["name"] for r in vn.RESULTS)
    assert vn.FAILS == 0                                              # 觀察模式不中止部署
    db.set_setting(conn, "verify_strict_eos", "1")
    vn.RESULTS.clear()
    vn.CHECKS = vn.FAILS = 0
    vn.check_eos_evidence(conn, TODAY)
    assert vn.FAILS >= 1
    monkeypatch.undo()
    # 造假：待重查旗標被改（把 complete 判成不完整以外的邏輯）→ 獨立重算抓得到
    monkeypatch.setattr(ee, "evidence_state", lambda e, ev, today=None: ("none", []))
    vn.RESULTS.clear()
    vn.CHECKS = vn.FAILS = 0
    vn.check_eos_evidence(conn, TODAY)
    assert any(r["ok"] is False and "待重查" in r["name"] for r in vn.RESULTS)


def test_verify_numbers_證據表髒資料被抓到(conn):
    import verify_numbers as vn
    ee.save_evidence(conn, "os", "Rocky Linux 9", FULL, None, "a", NOW)
    conn.execute("UPDATE eos_evidence SET verified_on = '2099-01-01'")
    conn.commit()
    db.set_setting(conn, "verify_strict_eos", "1")
    vn.RESULTS.clear()
    vn.CHECKS = vn.FAILS = 0
    vn.check_eos_evidence(conn, TODAY)
    assert any(r["ok"] is False and "證據記錄" in r["name"] for r in vn.RESULTS)


# ===================================================================== 詞彙
def test_使用者可見文字不出現_核准():
    files = [BACKEND / "eos_evidence.py", ROOT / "APP/asset-module/frontend/components/v2/EosEvidence.vue",
             ROOT / "APP/asset-module/frontend/utils/eosEvidence.ts"]
    for f in files:
        assert "核准" not in f.read_text(encoding="utf-8"), f.name
    assert "核准" not in str(ee.BUCKET_LABEL) + str(ee.STATE_LABEL) + str(ee.FIELD_LABEL)


def test_舊庫相容_ensure_tables冪等(conn):
    ee.ensure_tables(conn)
    ee.ensure_tables(conn)
    ee.save_evidence(conn, "os", "Rocky Linux 9", FULL, None, "a", NOW)
    ee.ensure_tables(conn)
    assert conn.execute("SELECT COUNT(*) FROM eos_evidence").fetchone()[0] == 1



def test_查證人一律是登入者_既有證據更正必帶版本(conn):
    c = conn
    out = ee.save_evidence(c, "os", "Rocky Linux 9", dict(FULL, verified_by="冒名者"), None, "alice", NOW)
    assert out["evidence"]["verified_by"] == "alice"                      # 自填的查證人被忽略
    with pytest.raises(ValueError):                                       # 更正既有證據不帶版本＝拒絕
        ee.save_evidence(c, "os", "Rocky Linux 9", {"scope_text": "x"}, "理由", "bob", NOW)
    out2 = ee.save_evidence(c, "os", "Rocky Linux 9", {"scope_text": "x"}, "理由", "bob", NOW, expected_version=out["evidence"]["version"])
    assert out2["evidence"]["verified_by"] == "bob"
