"""交易日情境標記：來源由伺服器時間判定、開盤後預告欄位鎖定、修訂保留歷史、作廢保留原文、稽核同交易、標記不觸發取數、權限。全部假資料、可注入的 now。"""
import gzip
import json
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import capacity_analysis as ca  # noqa: E402
import capacity_markers as mk  # noqa: E402
import nmon_series  # noqa: E402

D = "2026-09-10"            # 週四（交易日）
BODY = {"trade_date": D, "mtype": "大量交易日", "reason_cat": "預期大漲", "expected_level": "高", "basis": "預期央行消息，法人預估量能放大"}


def at(day: str, hh: int, mm: int = 0, ss: int = 0) -> datetime:
    y, m, d = map(int, day.split("-"))
    return datetime(y, m, d, hh, mm, ss)


def mem():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
    c.execute(nmon_series.SERIES_SQL)
    return c


# ---------------- 來源由伺服器時間判定
@pytest.mark.parametrize("now,kind", [
    (at("2026-09-09", 16), "pre_announce"),                 # 前一天
    (at(D, 8, 59, 59), "pre_announce"),                     # 當天開盤前最後一秒
    (at(D, 9, 0, 0), "intraday"),                           # 09:00 整
    (at(D, 13, 29, 59), "intraday"),
    (at(D, 13, 30, 0), "post_fact"),                        # 收盤 13:30 起
    (at("2026-09-11", 8), "post_fact"),                     # 隔天
])
def test_來源由伺服器時間判定(now, kind):
    c = mem()
    m = mk.create(c, {**BODY, "source_kind": "pre_announce"}, "u1", now)         # 就算硬塞 source_kind 也沒用
    assert m["source_kind"] == kind and mk.KINDS[kind] == mk.listing(c, now=now)[0]["kind_label"]


def test_連結事件不改變來源判定():
    c = mem()
    c.execute("CREATE TABLE incident_event (id INTEGER PRIMARY KEY, title TEXT)")
    c.execute("INSERT INTO incident_event VALUES (1,'a'),(2,'b')")
    m = mk.create(c, BODY, "u1", at(D, 14))                                       # 事後補標
    assert mk.set_events(c, m["id"], [2, 1], "u1", at(D, 15))["source_kind"] == "post_fact"
    assert mk.listing(c, now=at(D, 15))[0]["event_ids"] == [1, 2]
    mk.set_events(c, m["id"], [], "u1", at(D, 16))
    assert mk.listing(c, now=at(D, 16))[0]["event_ids"] == []
    with pytest.raises(ValueError, match="找不到事件"):
        mk.set_events(c, m["id"], [99], "u1", at(D, 16))


# ---------------- 鎖定與修訂
def test_開盤前可直接改_有前後值稽核_開盤後預告欄位鎖定_備註仍可補():
    c = mem()
    m = mk.create(c, BODY, "u1", at("2026-09-09", 17))
    r = mk.update_direct(c, m["id"], {"expected_level": "中", "basis": "更新依據"}, "u1", at(D, 8, 30))
    assert r["expected_level"] == "中" and r["source_kind"] == "pre_announce"
    tr = mk.audit_trail(c, m["id"])
    assert [t["action"] for t in tr] == ["create", "update"] and tr[1]["before"]["expected_level"] == "高" and tr[1]["after"]["expected_level"] == "中"
    with pytest.raises(PermissionError, match="鎖定"):
        mk.update_direct(c, m["id"], {"expected_level": "低"}, "u1", at(D, 9, 0))        # 開盤後不能直接改
    assert mk.get(c, m["id"])["expected_level"] == "中"
    r = mk.update_direct(c, m["id"], {"note": "開盤後補一句備註"}, "u1", at(D, 10))          # 備註類欄位不在鎖定名單
    assert r["note"] == "開盤後補一句備註"
    with pytest.raises(ValueError, match="交易日期不能改"):
        mk.update_direct(c, m["id"], {"trade_date": "2026-09-11"}, "u1", at(D, 8))


def test_修訂保留歷史_原始預告永遠在_只能修訂最新一版():
    c = mem()
    m = mk.create(c, BODY, "u1", at("2026-09-09", 17))
    r1 = mk.revise(c, m["id"], {"expected_level": "中", "basis": "開盤後修正預期"}, "u2", at(D, 10))
    assert r1["supersedes_id"] == m["id"] and r1["source_kind"] == "intraday"          # 修訂版依自己的建立時間判定
    orig = mk.get(c, m["id"])
    assert orig["expected_level"] == "高" and orig["basis"] == BODY["basis"] and orig["source_kind"] == "pre_announce"      # 原始不變
    st = {x["id"]: x["status"] for x in mk.listing(c, now=at(D, 11))}
    assert st == {m["id"]: "superseded", r1["id"]: "active"}
    with pytest.raises(ValueError, match="更新的修訂版"):
        mk.revise(c, m["id"], {"note": "x"}, "u2", at(D, 12))
    r2 = mk.revise(c, r1["id"], {"expected_level": "低"}, "u2", at(D, 14))
    assert r2["source_kind"] == "post_fact" and r2["supersedes_id"] == r1["id"]
    with pytest.raises(ValueError, match="修訂版取代"):
        mk.update_direct(c, m["id"], {"note": "x"}, "u1", at(D, 8))


def test_作廢保留原文與原因_不刪除_需要原因():
    c = mem()
    m = mk.create(c, BODY, "u1", at("2026-09-09", 17))
    with pytest.raises(ValueError, match="原因"):
        mk.void(c, m["id"], "  ", "admin", at(D, 10))
    v = mk.void(c, m["id"], "重複建立", "admin", at(D, 10))
    assert v["voided_by"] == "admin" and v["void_reason"] == "重複建立" and v["basis"] == BODY["basis"] and v["voided_at"]
    assert c.execute("SELECT COUNT(*) FROM capacity_day_marker").fetchone()[0] == 1
    with pytest.raises(ValueError, match="已作廢"):
        mk.void(c, m["id"], "again", "admin", at(D, 11))
    with pytest.raises(ValueError, match="已作廢"):
        mk.revise(c, m["id"], {"note": "x"}, "u", at(D, 11))
    assert mk.annotated_days(c) == {}                                                     # 作廢後不參與分析


# ---------------- 驗證
@pytest.mark.parametrize("patch,msg", [
    ({"trade_date": "2026/09/10"}, "YYYY-MM-DD"), ({"mtype": "亂填"}, "類型"), ({"reason_cat": "亂填"}, "原因類別"),
    ({"expected_level": "超高"}, "預期程度"), ({"basis": ""}, "依據"), ({"ranges": [{"start": "10:00", "end": "09:00"}]}, "起點"),
    ({"ranges": [{"start": "09:00", "end": "10:00"}, {"start": "09:30", "end": "11:00"}]}, "重疊"), ({"ranges": [{"start": "9am", "end": "10:00"}]}, "HH:MM"),
    ({"trade_date": "2030-01-01"}, "太遠"),
])
def test_欄位驗證(patch, msg):
    with pytest.raises(ValueError, match=msg):
        mk.create(mem(), {**BODY, **patch}, "u", at("2026-09-09", 10))


def test_時段範圍預設展開_可自訂_左閉右開():
    c = mem()
    assert mk.create(c, BODY, "u", at("2026-09-09", 10))["ranges"] == mk.DEFAULT_RANGES
    r = mk.create(c, {**BODY, "ranges": [{"start": "09:00", "end": "09:10"}]}, "u", at("2026-09-09", 10))["ranges"]
    assert r == [{"start": "09:00", "end": "09:10"}]


def test_類型與原因類別可設定_必須含大量交易日():
    with pytest.raises(ValueError, match="大量交易日"):
        mk.validate_options(["其他情境"], ["預期大漲"])
    c = mem()
    ca.save_config(c, {"marker_types": ["大量交易日", "系統維運日"], "marker_reasons": ["預期大漲", "法說會"]})
    m = mk.create(c, {**BODY, "mtype": "系統維運日", "reason_cat": "法說會"}, "u", at("2026-09-09", 10))
    assert m["mtype"] == "系統維運日" and ca.get_config(c)["marker_types"] == ["大量交易日", "系統維運日"]


# ---------------- 分析端：只讀有效的事前預告＋事後補標，分開標示
def test_分析端標註日_事前與事後分開_盤中與其他類型不納入():
    c = mem()
    c.execute("CREATE TABLE incident_event (id INTEGER PRIMARY KEY)")
    mk.create(c, {**BODY, "trade_date": "2026-09-07"}, "u", at("2026-09-04", 10))                       # 事前預告
    mk.create(c, {**BODY, "trade_date": "2026-09-08"}, "u", at("2026-09-08", 15))                       # 事後補標
    mk.create(c, {**BODY, "trade_date": "2026-09-09"}, "u", at("2026-09-09", 10))                       # 盤中標記（不納入）
    mk.create(c, {**BODY, "trade_date": "2026-09-10", "mtype": "其他情境"}, "u", at("2026-09-04", 10))  # 其他類型（不納入）
    both = mk.create(c, {**BODY, "trade_date": "2026-09-11"}, "u", at("2026-09-04", 10))
    mk.create(c, {**BODY, "trade_date": "2026-09-11"}, "u", at("2026-09-11", 16))                       # 同一天再有事後補標
    assert mk.annotated_days(c) == {"2026-09-07": "pre", "2026-09-08": "post", "2026-09-11": "pre+post"}
    mk.void(c, both["id"], "測試", "admin", at("2026-09-12", 9))
    assert mk.annotated_days(c)["2026-09-11"] == "post"


def test_分析結果的標註對照_事前與事後分開():
    c = mem()
    for d in ("2026-09-07", "2026-09-08", "2026-09-09"):
        row = {"ts": [f"{m // 60:02d}:{m % 60:02d}:00" for m in range(540, 810)], "cols": {"cpu_busy": [(95.0 if d == "2026-09-07" else 80.0 if d == "2026-09-08" else 30.0)] * 270}, "kind": "raw"}
        c.execute("INSERT INTO nmon_series VALUES ('H',?,60,270,?,'x')", (d, gzip.compress(json.dumps(row).encode())))
    mk.create(c, {**BODY, "trade_date": "2026-09-07"}, "u", at("2026-09-04", 10))
    mk.create(c, {**BODY, "trade_date": "2026-09-08"}, "u", at("2026-09-08", 15))
    ca.save_config(c, {"params": {"min_days": 1}})
    h = ca.analyze_host(c, "H", ca.get_config(c))
    assert h["annotated_kinds"] == {"2026-09-07": "pre", "2026-09-08": "post"}
    cell = [x for x in h["cells"] if x["slot"] == "mid" and x["metric"] == "cpu_busy" and x["day_type"] == "normal"][0]
    assert cell["annotated_cmp"]["by_kind"] == {"pre": {"days": 1, "max": 95.0}, "post": {"days": 1, "max": 80.0}}
    assert cell["annotated_cmp"]["other_max"] == 30.0 and cell["annotated_cmp"]["annotated_days"] == 2


# ---------------- API：權限、稽核同交易、不觸發取數
def _api(tmp, users):
    import api
    import auth
    import db
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

    def client(name):
        cl = TestClient(api.app, raise_server_exceptions=False)
        assert cl.post("/api/auth/login", json={"username": name, "password": "test-password-123"}).status_code == 200
        return cl
    return client, p, api


def test_API_只有admin群組可寫_user與viewer對所有標記寫入端點都是403_登入停用也是403():
    import db
    import transfer_policy as tp
    tp.set_clock(lambda: datetime(2026, 9, 9, 17, tzinfo=tp.TZ))
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            client, p, api = _api(tmp, [("admin", "admin"), ("alice", "user"), ("vera", "viewer")])
            alice, admin, vera = client("alice"), client("admin"), client("vera")
            r = admin.post("/api/v2/capacity/markers", json={**BODY, "source_kind": "post_fact"})
            assert r.status_code == 200 and r.json()["source_kind"] == "pre_announce" and r.json()["created_by"] == "admin"
            mid = r.json()["id"]
            writes = [("post", "/api/v2/capacity/markers", BODY), ("put", f"/api/v2/capacity/markers/{mid}", {"note": "x"}),
                      ("post", f"/api/v2/capacity/markers/{mid}/revise", {"expected_level": "低"}), ("put", f"/api/v2/capacity/markers/{mid}/events", {"event_ids": []}),
                      ("post", f"/api/v2/capacity/markers/{mid}/void", {"reason": "x"})]
            for who in (alice, vera):
                for method, url, body in writes:
                    assert getattr(who, method)(url, json=body).status_code == 403, (method, url)
                assert who.get("/api/v2/capacity/markers").status_code == 200 and len(who.get("/api/v2/capacity/markers").json()["markers"]) == 1       # 唯讀：清單、歷史看得到
                assert who.get(f"/api/v2/capacity/markers/{mid}/audit").status_code == 200
            assert admin.post(f"/api/v2/capacity/markers/{mid}/revise", json={"expected_level": "低"}).status_code == 200
            assert admin.post(f"/api/v2/capacity/markers/{mid}/void", json={"reason": ""}).status_code == 400                    # 作廢要原因
            assert admin.post(f"/api/v2/capacity/markers/{mid}/void", json={"reason": "重複"}).status_code == 200
            assert [m["status"] for m in vera.get("/api/v2/capacity/markers").json()["markers"] if m["id"] == mid] == ["voided"]
            n = len(admin.get("/api/v2/capacity/markers").json()["markers"])
            cn = db.get_connection(p)
            cn.execute("INSERT OR REPLACE INTO app_settings VALUES ('auth_disabled', '1')")       # 登入停用：假 session 是 admin，但標記寫入 fail-closed
            cn.commit()
            cn.close()
            for method, url, body in writes:
                assert getattr(admin, method)(url, json=body).status_code == 403, ("auth_disabled", method, url)
            assert admin.get("/api/v2/capacity/markers").status_code == 200 and len(admin.get("/api/v2/capacity/markers").json()["markers"]) == n
            api.app.dependency_overrides.clear()
    finally:
        tp.set_clock(None)


def test_API_開盤後直接修改回409_稽核寫不進去資料回滾_標記不觸發取數(monkeypatch):
    import db
    import transfer_policy as tp
    calls = []
    monkeypatch.setattr(ca, "wu_refresh", lambda *a, **k: calls.append(1))
    tp.set_clock(lambda: datetime(2026, 9, 9, 17, tzinfo=tp.TZ))
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            client, p, api = _api(tmp, [("admin", "admin")])
            alice = admin = client("admin")
            mid = alice.post("/api/v2/capacity/markers", json=BODY).json()["id"]
            tp.set_clock(lambda: datetime(2026, 9, 10, 9, 5, tzinfo=tp.TZ))              # 開盤後
            assert alice.put(f"/api/v2/capacity/markers/{mid}", json={"expected_level": "低"}).status_code == 409
            assert alice.put(f"/api/v2/capacity/markers/{mid}", json={"note": "備註可補"}).status_code == 200
            cn = db.get_connection(p)
            cn.execute("CREATE TRIGGER no_audit BEFORE INSERT ON activity_log WHEN NEW.action LIKE 'capacity_marker%' BEGIN SELECT RAISE(ABORT, 'audit down'); END")
            cn.commit()
            cn.close()
            n0 = len(alice.get("/api/v2/capacity/markers").json()["markers"])
            assert alice.post("/api/v2/capacity/markers", json={**BODY, "trade_date": "2026-09-11"}).status_code >= 500
            assert alice.post(f"/api/v2/capacity/markers/{mid}/revise", json={"expected_level": "低"}).status_code >= 500
            assert admin.post(f"/api/v2/capacity/markers/{mid}/void", json={"reason": "x"}).status_code >= 500
            m = alice.get("/api/v2/capacity/markers").json()["markers"]
            assert len(m) == n0 and m[0]["status"] == "active" and m[0]["note"] == "備註可補"          # 全部回滾
            tr = alice.get(f"/api/v2/capacity/markers/{mid}/audit").json()["audit"]
            assert [t["action"] for t in tr] == ["create", "update"]                                      # 稽核表也沒有殘留（同交易）
            api.app.dependency_overrides.clear()
    finally:
        tp.set_clock(None)
    assert calls == []                                                                            # 新增／修改／修訂／作廢都沒有觸發取數


def test_標記模組不碰取數():
    src = (ROOT / "APP" / "asset-module" / "backend" / "capacity_markers.py").read_text(encoding="utf-8")
    assert "wu_refresh" not in src.replace("取數", "") and "import capacity_analysis" not in src and "whatsup" not in src.lower().replace("whatsup 查詢", "")


def test_API_重複修訂與重複作廢回409():
    import transfer_policy as tp
    tp.set_clock(lambda: datetime(2026, 9, 9, 17, tzinfo=tp.TZ))
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            client, p, api = _api(tmp, [("admin", "admin")])
            admin = client("admin")
            mid = admin.post("/api/v2/capacity/markers", json=BODY).json()["id"]
            assert admin.post(f"/api/v2/capacity/markers/{mid}/revise", json={"expected_level": "低"}).status_code == 200
            assert admin.post(f"/api/v2/capacity/markers/{mid}/revise", json={"expected_level": "中"}).status_code == 409        # 同一原版只能有一個修訂版
            assert admin.post(f"/api/v2/capacity/markers/{mid}/void", json={"reason": "x"}).status_code == 200
            assert admin.post(f"/api/v2/capacity/markers/{mid}/void", json={"reason": "y"}).status_code == 409                   # 重複作廢
            assert admin.post(f"/api/v2/capacity/markers/{mid}/revise", json={"expected_level": "低"}).status_code == 409        # 作廢後不得再修訂
            api.app.dependency_overrides.clear()
    finally:
        tp.set_clock(None)


def test_路由內再擋一次_就算access_policy被繞過_非admin與登入停用仍403():
    """access_policy 是第一道（ADMIN_WRITE）；路由內 _marker_write 是第二道。直接呼叫它，證明第二道自己會擋。"""
    from fastapi import HTTPException
    from routers import capacity as route
    c = mem()
    c.row_factory = sqlite3.Row                                           # 路由用的連線是 Row 工廠
    calls = []
    for role in ("user", "viewer"):
        with pytest.raises(HTTPException) as ei:
            route._marker_write(c, None, {"username": "x", "role": role}, "capacity_marker", "t", lambda m, who: calls.append(1))
        assert ei.value.status_code == 403 and "管理員群組" in ei.value.detail
    c.execute("INSERT INTO app_settings VALUES ('auth_disabled', '1')")
    with pytest.raises(HTTPException) as ei:
        route._marker_write(c, None, {"username": "x", "role": "admin"}, "capacity_marker", "t", lambda m, who: calls.append(1))
    assert ei.value.status_code == 403 and "登入停用" in ei.value.detail
    assert calls == []                                                    # 被擋時完全沒執行寫入
