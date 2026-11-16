"""憑證管理第一階段（certs.py／routers/certs.py）。

守的事：資料模型兩層（同名正／測、同 VIP 不同 SNI 並存）、時間以『距到期瞬間』判斷（含到期當日 00:10 UTC）、
匯入預覽→確認（衝突不默默覆蓋、重匯不覆蓋人工修改與觀測）、人名遮罩與去識別匯出、讀取路徑不建表不寫庫。
測試資料一律虛構：192.0.2.x／198.51.100.x、example.test、虛構人名。
"""
import hashlib
import io
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import certs  # noqa: E402
import db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ROLE_AWARE_TESTS = True
_PW = "s3cure-pass!"          # 測試用假密碼
UTC = timezone.utc
NOW = datetime(2026, 11, 1, 0, 0, 0, tzinfo=UTC)

HEADERS = ["憑證名稱", "F5 BIG IP", "主機 IP", "F5是否\n掛憑證", "OS/站台類型", "正/測", "申請單位", "申請窗口", "AP窗口",
           "Infra執行人員", "預排更換時間", "AP驗測人員", "憑證更換是否\n需要公告"]


def _xlsx(rows, headers=HEADERS, junk_top=0):
    wb = Workbook()
    ws = wb.active
    for _ in range(junk_top):
        ws.append(["說明文字"])
    ws.append(headers)
    for r in rows:
        ws.append(r)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def _row(name="www.example.test", bigip="198.51.100.10", host="192.0.2.21", f5="是", os="IIS", env="正式", unit="營運部",
         c1="洪小明", c2="林大華", infra="陳阿德", planned="", tester="黃小美", ann="是"):
    return [name, bigip, host, f5, os, env, unit, c1, c2, infra, planned, tester, ann]


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    yield c
    c.close()


# ------------------------------------------------------------------ 時間
def test_到期當日0010UTC_剛好到期那一刻起就是已過期_不是日期相減():
    na = "2026-11-01T00:10:00Z"           # UTC+8 = 08:10
    assert certs.status_of(na, datetime(2026, 11, 1, 0, 9, 59, tzinfo=UTC)) == "d7"      # 還差 1 秒：尚未過期
    assert certs.status_of(na, datetime(2026, 11, 1, 0, 10, 0, tzinfo=UTC)) == "expired"  # 到期瞬間
    assert certs.status_of(na, datetime(2026, 11, 1, 0, 11, 0, tzinfo=UTC)) == "expired"  # 本地日期同一天，也已過期
    assert certs.remain_text(na, datetime(2026, 11, 1, 0, 11, 0, tzinfo=UTC)).startswith("已過期")
    assert certs.fmt_local(na) == "2026-11-01 08:10 (UTC+8)"


def test_狀態分段邊界():
    def st(hours):
        return certs.status_of("2026-12-01T00:00:00Z", datetime(2026, 12, 1, tzinfo=UTC) - timedelta(hours=hours))
    assert st(7 * 24) == "d7" and st(7 * 24 + 1) == "d30"
    assert st(30 * 24) == "d30" and st(30 * 24 + 1) == "d60"
    assert st(60 * 24) == "d60" and st(60 * 24 + 1) == "ok"
    assert certs.status_of(None) == "none"


def test_時間輸入_無時區視為UTC加8_日期只有天視為當日2359():
    assert certs.to_utc_iso("2026-11-30 23:59") == "2026-11-30T15:59:00Z"
    assert certs.to_utc_iso("2026-11-30") == "2026-11-30T15:59:59Z"
    assert certs.to_utc_iso("2026-11-30T10:00:00Z") == "2026-11-30T10:00:00Z"
    assert certs.to_utc_iso("2026/11/30 08:00+08:00") == "2026-11-30T00:00:00Z"
    with pytest.raises(ValueError):
        certs.to_utc_iso("下週三")


def test_風險等級門檻():
    th = [45, 30, 14]
    now = NOW
    f = lambda days: certs.risk_label((now + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"), th, now)  # noqa: E731
    assert f(60) == "" and f(45) == "低" and f(30) == "中" and f(14) == "高" and f(-1) == "已過期"
    assert certs.validate_thresholds([14, 45, 30]) == [45, 30, 14]
    for bad in ([1, 2], [0, 5, 9], [5, 5, 9], [1, 2, 3651], ["a", 2, 3]):
        with pytest.raises(ValueError):
            certs.validate_thresholds(bad)


# ------------------------------------------------------------------ 資料模型
def test_同名正式測試_同VIP不同SNI_都能並存_但同一把鍵不行(conn):
    base = {"name": "shop-cert", "endpoint": "198.51.100.10", "port": 443, "mount_type": "F5"}
    a = certs.create_item(conn, {**base, "environment": "正式", "sni": "a.example.test"}, "u", True)["id"]
    b = certs.create_item(conn, {**base, "environment": "正式", "sni": "b.example.test"}, "u", True)["id"]   # 同 VIP 不同 SNI
    c = certs.create_item(conn, {**base, "environment": "測試", "sni": "a.example.test"}, "u", True)["id"]   # 同名不同環境
    assert len({a, b, c}) == 3
    with pytest.raises(certs.KeyConflict):
        certs.create_item(conn, {**base, "environment": "正式", "sni": "A.example.test"}, "u", True)         # SNI 大小寫正規化後相同
    seqs = [r["seq"] for r in certs.all_rows(conn)]
    assert seqs == [1, 2, 3]


def test_觀測歷史_最新一筆決定狀態_相同識別不重複(conn):
    iid = certs.create_item(conn, {"name": "x", "environment": "正式", "endpoint": "192.0.2.5"}, "u", True)["id"]
    certs.add_observation(conn, iid, {"not_after": "2026-12-31 23:59", "observed_at": "2026-10-01 10:00",
                                      "issuer_dn": "CN=Example Test CA", "serial": "0a:bc"}, "u")
    certs.add_observation(conn, iid, {"not_after": "2027-12-31 23:59", "observed_at": "2026-10-20 10:00",
                                      "issuer_dn": "CN=Example Test CA", "serial": "0d:ef", "san": "a.example.test, b.example.test",
                                      "fingerprint_sha256": "AA:" * 31 + "AA"}, "u")
    row = certs.all_rows(conn, NOW)[0]
    assert row["not_after_utc"] == "2027-12-31T15:59:00Z" and row["status"] == "ok"
    with pytest.raises(certs.KeyConflict):
        certs.add_observation(conn, iid, {"not_after": "2026-12-31 23:59", "issuer_dn": "CN=Example Test CA", "serial": "0A:BC"}, "u")
    d = certs.item_detail(conn, iid, True, NOW)
    assert len(d["observations"]) == 2 and d["observations"][0]["san"] == ["a.example.test", "b.example.test"]
    with pytest.raises(ValueError):
        certs.add_observation(conn, iid, {"not_after": "2030-01-01", "fingerprint_sha256": "zz"}, "u")


def test_卡片加總等於清單_下鑽同一份計算_停用不計入(conn):
    for i, (days, act) in enumerate([(-3, 1), (2, 1), (20, 1), (45, 1), (200, 1), (None, 1), (5, 0)]):
        iid = certs.create_item(conn, {"name": f"c{i}", "environment": "正式", "endpoint": f"192.0.2.{i + 1}"}, "u", True)["id"]
        if days is not None:
            na = (NOW + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
            certs.add_observation(conn, iid, {"not_after": na}, "u")
        if not act:
            certs.set_active(conn, iid, False, "u", "retired")
    s = certs.summary(conn, NOW)
    cnt = {c["key"]: c["count"] for c in s["cards"]}
    assert cnt == {"expired": 1, "d7": 1, "d30": 1, "d60": 1, "ok": 1, "none": 1}
    assert s["total_active"] == 6 and s["inactive"] == 1 and sum(cnt.values()) == s["total_active"]
    for k, n in cnt.items():
        assert len(certs.list_items(conn, status=k, now=NOW)) == n
    assert len(certs.list_items(conn, include_inactive=True, now=NOW)) == 7


def test_停用保留歷史_可重新啟用(conn):
    iid = certs.create_item(conn, {"name": "x", "environment": "正式", "endpoint": "192.0.2.5"}, "u", True)["id"]
    certs.add_observation(conn, iid, {"not_after": "2027-01-01 00:00"}, "u")
    certs.set_active(conn, iid, False, "u", "retired")
    assert certs.list_items(conn) == []
    assert len(certs.item_detail(conn, iid, True)["observations"]) == 1
    with pytest.raises(certs.KeyConflict) as ei:
        certs.create_item(conn, {"name": "x2", "environment": "正式", "endpoint": "192.0.2.5"}, "u", True)
    assert "已停用" in str(ei.value)
    certs.set_active(conn, iid, True, "u")
    assert len(certs.list_items(conn)) == 1


def test_replace欄位只存_預設manual(conn):
    iid = certs.create_item(conn, {"name": "x", "environment": "正式", "endpoint": "192.0.2.5"}, "u", True)["id"]
    d = certs.item_detail(conn, iid, True)
    assert d["replace_method"] == "manual" and d["replace_status"] == ""


# ------------------------------------------------------------------ 匯入
def test_匯入預覽_新增_表頭換行容忍_不寫庫(conn):
    data = _xlsx([_row(), _row(name="www.example.test", env="測試", bigip="198.51.100.11", host="192.0.2.31")], junk_top=2)
    plan = certs.import_plan(conn, "x.xlsx", data)
    assert plan["counts"]["new"] == 2 and all(r["reason"] for r in plan["rows"])
    # 同名但環境不同 → 視為不同項目，不是衝突
    assert plan["rows"][1]["status"] == "new" and "環境不同" not in plan["rows"][0]["reason"]
    assert conn.execute("SELECT count(*) FROM cert_item").fetchone()[0] == 0
    # F5 掛憑證 → 端點取 F5 BIG IP、掛載 F5
    assert plan["rows"][0]["key_text"].startswith("正式｜198.51.100.10:443") and "F5" in plan["rows"][0]["key_text"]


def test_匯入_缺表頭與缺端點(conn):
    with pytest.raises(ValueError):
        certs.import_plan(conn, "x.xlsx", _xlsx([["a"]], headers=["別的欄"]))
    plan = certs.import_plan(conn, "x.xlsx", _xlsx([_row(name="內部系統憑證", bigip="", host="", f5="否")]))
    assert plan["rows"][0]["status"] == "error"


def test_匯入_確認寫入_重匯不覆蓋人工修改_也不碰觀測(conn):
    data = _xlsx([_row(planned="2026-12-05 22:00"), _row(name="api.example.test", bigip="198.51.100.12", host="192.0.2.22")])
    plan = certs.import_plan(conn, "x.xlsx", data)
    res = certs.import_apply(conn, "x.xlsx", data, "adm", {}, plan["file_sha256"], plan["plan_digest"])
    assert res["created"] == 2
    items = certs.list_items(conn, now=NOW)
    assert len(items) == 2
    iid = next(i["id"] for i in items if i["name"] == "www.example.test")
    # planned_text 可解析才存結構化（UTC+8 22:00 → 14:00Z）；解析不了不報錯
    assert conn.execute("SELECT planned_at_utc FROM cert_item WHERE id=?", (iid,)).fetchone()[0] == "2026-12-05T14:00:00Z"
    # 人工改：申請單位、窗口；並登錄一筆觀測
    certs.update_item(conn, iid, {"req_unit": "資訊處", "req_contact": "手改窗口"}, "alice", True)
    certs.add_observation(conn, iid, {"not_after": "2027-03-01 00:00", "issuer_dn": "CN=Example Test CA"}, "alice")
    # 檔案更新了申請單位與窗口，再匯一次
    data2 = _xlsx([_row(unit="業務部", c1="檔案窗口", planned="12/5 22:00-24:00"),
                   _row(name="api.example.test", bigip="198.51.100.12", host="192.0.2.22")])
    plan2 = certs.import_plan(conn, "y.xlsx", data2)
    r0 = plan2["rows"][0]
    assert r0["status"] == "update" and "人工修改過" in r0["reason"]
    assert plan2["rows"][1]["status"] == "skip"
    certs.import_apply(conn, "y.xlsx", data2, "adm", {}, plan2["file_sha256"], plan2["plan_digest"])
    d = certs.item_detail(conn, iid, True)
    assert d["req_unit"] == "資訊處" and d["req_contact"] == "手改窗口"       # 人工欄位沒被蓋
    assert d["planned_text"] == "12/5 22:00-24:00" and d["planned_at_utc"] is None      # 原樣存文字；解析不了的不報錯
    assert len(d["observations"]) == 1 and d["observations"][0]["not_after_utc"] == "2027-02-28T16:00:00Z"
    # 再匯一次完全相同的檔 → 全部略過
    plan3 = certs.import_plan(conn, "y.xlsx", data2)
    assert plan3["counts"]["update"] == 0 and plan3["counts"]["skip"] == 2


def test_匯入_同名同環境端點不同_列為衝突_沒選擇就不寫_選候選才更新(conn):
    iid = certs.create_item(conn, {"name": "www.example.test", "environment": "正式", "endpoint": "192.0.2.99", "mount_type": "IIS"},
                            "u", True)["id"]
    data = _xlsx([_row()])
    plan = certs.import_plan(conn, "x.xlsx", data)
    r = plan["rows"][0]
    assert r["status"] == "conflict" and [c["id"] for c in r["candidates"]] == [iid]
    res = certs.import_apply(conn, "x.xlsx", data, "adm", {}, plan["file_sha256"], plan["plan_digest"])
    assert res["unresolved"] == 1 and res["created"] == 0 and len(certs.list_items(conn, now=NOW)) == 1   # 不默默覆蓋也不默默新增
    with pytest.raises(ValueError):
        certs.import_apply(conn, "x.xlsx", data, "adm", {str(r["row_no"]): "不存在的id"})
    res = certs.import_apply(conn, "x.xlsx", data, "adm", {str(r["row_no"]): iid}, plan["file_sha256"], plan["plan_digest"])
    assert res["updated"] == 1
    d = certs.item_detail(conn, iid, True)
    assert d["endpoint"] == "198.51.100.10" and d["mount_type"] == "F5" and d["req_unit"] == "營運部"


def test_匯入_檔內重複為衝突_選new可當新項目(conn):
    data = _xlsx([_row(), _row()])
    plan = certs.import_plan(conn, "x.xlsx", data)
    assert [r["status"] for r in plan["rows"]] == ["new", "conflict"] and "檔內重複" in plan["rows"][1]["reason"]
    res = certs.import_apply(conn, "x.xlsx", data, "adm", {str(plan["rows"][1]["row_no"]): "skip"})
    assert res["created"] == 1 and res["skipped"] == 1


def test_匯入_確認時資料已變動就拒絕(conn):
    data = _xlsx([_row()])
    plan = certs.import_plan(conn, "x.xlsx", data)
    certs.create_item(conn, {"name": "別的", "environment": "正式", "endpoint": "192.0.2.77"}, "u", True)      # 預覽之後庫變了（seq/既有清單變）
    other = _xlsx([_row(), _row(name="z.example.test", bigip="198.51.100.13")])
    with pytest.raises(certs.PlanChanged):
        certs.import_apply(conn, "x.xlsx", other, "adm", {}, plan["file_sha256"], plan["plan_digest"])


def test_匯入檔有到期日欄位_只當第一筆觀測(conn):
    hd = HEADERS + ["憑證到期日"]
    data = _xlsx([_row() + ["2027-06-30"]], headers=hd)
    certs.import_apply(conn, "x.xlsx", data, "adm", {})
    iid = certs.list_items(conn, now=NOW)[0]["id"]
    d = certs.item_detail(conn, iid, True)
    assert d["observations"][0]["source"] == "import" and d["observations"][0]["not_after_utc"] == "2027-06-30T15:59:59Z"
    certs.add_observation(conn, iid, {"not_after": "2028-01-01 00:00"}, "alice")
    data2 = _xlsx([_row() + ["2027-07-31"]], headers=hd)
    certs.import_apply(conn, "y.xlsx", data2, "adm", {})
    assert len(certs.item_detail(conn, iid, True)["observations"]) == 2     # 重匯沒有再加（也沒蓋掉人工那筆）


# ------------------------------------------------------------------ 遮罩
def test_遮罩():
    assert certs.mask_person("洪小明") == "洪○"
    assert certs.mask_person("洪小明、林大華") == "洪○、林○"
    assert certs.mask_person("ext 12345678") == "e○、○○○"
    assert certs.mask_person("") == "" and certs.mask_person(None) == ""


# ------------------------------------------------------------------ 讀取路徑
def test_唯讀連線_全部讀取函式可用_檔案位元組不變(tmp_path):
    # 範圍說明：這裡釘的是「憑證相關資料表」（cert_item／cert_observation／app_settings 的憑證設定）讀取不寫庫。
    # API 層的 GET 因 require_auth 的登入心跳（activity.touch_session）會寫 sessions 表，那是全站共通行為，不在本測試範圍；
    # 所以這裡用 certs 函式直接在 mode=ro 連線上跑，不經 HTTP。
    p = tmp_path / "t.db"
    db.init_db(p)
    w = db.get_connection(p)
    iid = certs.create_item(w, {"name": "x", "environment": "正式", "endpoint": "192.0.2.5", "req_contact": "洪小明"}, "u", True)["id"]
    certs.add_observation(w, iid, {"not_after": "2027-01-01 00:00"}, "u")
    w.close()
    before = hashlib.sha256(p.read_bytes()).hexdigest()
    ro = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    ro.execute("PRAGMA query_only=ON")
    assert certs.summary(ro)["total_active"] == 1
    assert len(certs.list_items(ro, q="192.0.2")) == 1
    assert certs.item_detail(ro, iid, False)["req_contact"] == "洪○"
    assert certs.export_rows(ro, with_contacts=False)[1]
    assert certs.import_plan(ro, "x.xlsx", _xlsx([_row()]))["counts"]["conflict"] == 0
    ro.close()
    assert hashlib.sha256(p.read_bytes()).hexdigest() == before


def test_沒有憑證表_讀取回空_不建表():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA query_only=ON")
    s = certs.summary(c)
    assert s["total_active"] == 0 and s["ready"] is False
    assert certs.list_items(c) == []
    with pytest.raises(LookupError):
        certs.item_detail(c, "x", False)
    assert certs.import_plan(c, "x.xlsx", _xlsx([_row()]))["counts"]["new"] == 1
    assert c.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0


def test_讀取函式不呼叫ensure_tables():
    src = (ROOT / "APP" / "asset-module" / "backend" / "certs.py").read_text(encoding="utf-8")
    # ensure_tables 只能出現在：定義、寫入入口（create/update/set_active/add_observation/import_apply）
    for fn in ("def all_rows", "def summary", "def list_items", "def item_detail", "def import_plan", "def export_rows"):
        body = src.split(fn, 1)[1].split("\ndef ", 1)[0]
        assert "ensure_tables" not in body, fn
    routers = (ROOT / "APP" / "asset-module" / "backend" / "routers" / "certs.py").read_text(encoding="utf-8")
    assert "ensure_tables" not in routers


# ------------------------------------------------------------------ API：權限、遮罩、匯出
@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.delenv(api.AUTH_DISABLED_ENV, raising=False)
    p = tmp_path / "t.db"
    db.init_db(p)

    def _ov():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _ov
    c = db.get_connection(p)
    for n, role in (("admin", "admin"), ("alice", "user"), ("vera", "viewer")):
        db.create_user(c, n, auth.hash_password(_PW), role=role)
    c.close()

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


def _mk(env):
    a = env.client("admin")
    r = a.post("/api/certs/items", json={"name": "www.example.test", "environment": "正式", "endpoint": "198.51.100.10",
                                          "mount_type": "F5", "req_unit": "營運部", "req_contact": "洪小明", "infra_owner": "陳阿德"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_API_人名遮罩_管理員看全貌_一般帳號看遮罩_清單完全沒有人名(env):
    iid = _mk(env)
    admin, user, viewer = env.client("admin"), env.client("alice"), env.client("vera")
    assert admin.get(f"/api/certs/items/{iid}").json()["req_contact"] == "洪小明"
    d = user.get(f"/api/certs/items/{iid}").json()
    assert d["req_contact"] == "洪○" and d["infra_owner"] == "陳○" and d["contacts_masked"] is True
    assert viewer.get(f"/api/certs/items/{iid}").json()["req_contact"] == "洪○"
    lst = user.get("/api/certs/items").text
    assert "洪" not in lst and "陳" not in lst and "營運部" in lst


def test_API_一般帳號編輯不能改聯絡人_遮罩值不會被存回去(env):
    iid = _mk(env)
    user, admin = env.client("alice"), env.client("admin")
    r = user.put(f"/api/certs/items/{iid}", json={"req_unit": "資訊處", "req_contact": "洪○"})
    assert r.status_code == 200 and r.json()["changed"] == ["req_unit"]
    assert admin.get(f"/api/certs/items/{iid}").json()["req_contact"] == "洪小明"


def test_API_權限分級(env):
    iid = _mk(env)
    user, viewer, admin = env.client("alice"), env.client("vera"), env.client("admin")
    assert viewer.post("/api/certs/items", json={"name": "z"}).status_code == 403
    assert viewer.get("/api/certs/summary").status_code == 200
    assert user.post("/api/certs/items", json={"name": "z", "environment": "正式", "endpoint": "192.0.2.1"}).status_code == 200
    assert user.post(f"/api/certs/items/{iid}/observations", json={"not_after": "2027-01-01 00:00"}).status_code == 200
    assert user.post(f"/api/certs/items/{iid}/deactivate", json={"reason_code": "retired"}).status_code == 200
    assert user.post(f"/api/certs/items/{iid}/reactivate").status_code == 200
    assert user.put("/api/certs/settings", json={"thresholds": [40, 20, 10]}).status_code == 403
    assert user.post("/api/certs/export-identified", json={"purpose_code": "audit"}).status_code == 403
    assert user.post("/api/certs/import/preview", files={"file": ("a.xlsx", _xlsx([_row()]))}).status_code == 403
    assert admin.put("/api/certs/settings", json={"thresholds": [40, 20, 10]}).json()["thresholds"] == [40, 20, 10]
    assert admin.put("/api/certs/settings", json={"thresholds": [40, 20]}).status_code == 400
    assert admin.get("/api/certs/summary").json()["thresholds"] == [40, 20, 10]
    assert user.get("/api/certs/summary").json()["can_see_contacts"] is False


def test_API_匯入預覽與確認_流程(env):
    admin = env.client("admin")
    data = _xlsx([_row(), _row(name="api.example.test", bigip="198.51.100.12", host="192.0.2.22")])
    pv = admin.post("/api/certs/import/preview", files={"file": ("a.xlsx", data)}).json()
    assert pv["counts"]["new"] == 2
    r = admin.post("/api/certs/import/apply", files={"file": ("a.xlsx", data)},
                   data={"file_sha256": pv["file_sha256"], "plan_digest": pv["plan_digest"], "decisions": "{}"})
    assert r.status_code == 200 and r.json()["created"] == 2
    r2 = admin.post("/api/certs/import/apply", files={"file": ("a.xlsx", data)},
                    data={"file_sha256": "x", "plan_digest": "y", "decisions": "{}"})
    assert r2.status_code == 409
    assert admin.post("/api/certs/import/apply", files={"file": ("a.xlsx", data)}, data={"decisions": "[1]"}).status_code == 400
    assert admin.post("/api/certs/import/preview", files={"file": ("a.xlsx", b"not a zip")}).status_code == 400


def test_API_匯出_預設去識別_含人名需管理員加用途_並寫稽核(env):
    _mk(env)
    admin, user = env.client("admin"), env.client("alice")
    # 清單表格的匯出走前端 /api/export/table-xlsx，資料來源是清單 API；它本來就沒有人名欄
    assert all(h not in certs.export_rows(env.conn(), False)[0] for h in ("申請窗口", "AP窗口", "Infra執行人員", "AP驗測人員"))
    assert "洪小明" not in str(certs.export_rows(env.conn(), False)[1])
    assert admin.post("/api/certs/export-identified", json={"purpose_code": ""}).status_code == 400
    assert admin.post("/api/certs/export-identified", json={"purpose_code": "亂寫"}).status_code == 400
    assert admin.post("/api/certs/export-identified", json={"purpose_code": "other"}).status_code == 400
    assert admin.post("/api/certs/export-identified", json={"purpose_code": "other", "other_note": "短"}).status_code == 400
    r = admin.post("/api/certs/export-identified", json={"purpose_code": "other", "other_note": "給洪小明的特殊說明"})
    assert r.status_code == 200
    ws = load_workbook(io.BytesIO(r.content)).active
    flat = " ".join(str(c.value) for row in ws.iter_rows() for c in row)
    assert "洪小明" in flat and "陳阿德" in flat
    c = env.conn()
    log = c.execute("SELECT path FROM activity_log WHERE action='cert_export_identified'").fetchall()
    assert len(log) == 1 and "用途代號：other" in log[0][0] and "洪小明" not in log[0][0] and "特殊說明" not in log[0][0]
    ex = [dict(x) for x in c.execute("SELECT * FROM export_log")]
    assert any("含人名" in str(x) for x in ex) and not any("特殊說明" in str(x) or "洪小明" in str(x) for x in ex)   # 自由文字不進任何稽核表
    c.close()
    assert user.post("/api/certs/export-identified", json={"purpose_code": "audit"}).status_code == 403


def test_API_操作紀錄不含人名(env):
    iid = _mk(env)
    c = env.conn()
    paths = " ".join(r[0] for r in c.execute("SELECT path FROM activity_log WHERE action LIKE 'cert_%'"))
    assert "洪小明" not in paths and iid in paths
    c.close()


def test_API_詳情404與重複409(env):
    iid = _mk(env)
    a = env.client("admin")
    assert a.get("/api/certs/items/nope").status_code == 404
    r = a.post("/api/certs/items", json={"name": "dup", "environment": "正式", "endpoint": "198.51.100.10", "mount_type": "F5"})
    assert r.status_code == 409
    assert a.post("/api/certs/items", json={"name": ""}).status_code == 400
    assert a.get(f"/api/certs/items/{iid}").json()["key_text"].startswith("正式")


def test_verify_numbers_憑證不變式_有資料時全過_且會抓到亂掉的數字(conn, monkeypatch):
    import verify_numbers as vn
    fails0 = vn.FAILS
    for i, days in enumerate([-3, 2, 20, 45, 200, None]):
        iid = certs.create_item(conn, {"name": f"c{i}", "environment": "正式", "endpoint": f"192.0.2.{i + 1}"}, "u", True)["id"]
        if days is not None:
            certs.add_observation(conn, iid, {"not_after": (datetime.now(UTC) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")}, "u")
    before = len(vn.RESULTS)
    vn.check_certs(conn)
    mine = vn.RESULTS[before:]
    assert len(mine) >= 5 and not [x for x in mine if x["ok"] is False], mine
    # 突變：讓下鑽清單少一筆 → 不變式必須紅
    orig = certs.list_items
    monkeypatch.setattr(certs, "list_items", lambda c, status=None, **k: orig(c, status=status, **k)[:-1] if status == "ok" else orig(c, status=status, **k))
    before = len(vn.RESULTS)
    vn.check_certs(conn)
    assert [x for x in vn.RESULTS[before:] if x["ok"] is False]
    vn.FAILS = fails0


# ------------------------------------------------------------------ 審查修正（2026-11-01）
def test_空欄位NULL換空字串原樣PUT_不算修改_不鎖人工欄位_之後重匯仍能補進(conn):
    iid = certs.create_item(conn, {"name": "www.example.test", "environment": "正式", "endpoint": "198.51.100.10", "sni": "www.example.test", "mount_type": "F5"}, "u", True)["id"]
    blank = {k: "" for k in ("host_ips", "f5_bigip", "os_type", "req_unit", "req_contact", "ap_contact", "infra_owner", "ap_tester",
                             "planned_text", "announce_needed", "replace_status", "note")}
    res = certs.update_item(conn, iid, {**blank, "name": "www.example.test", "replace_method": "manual", "port": 443}, "alice", True)
    assert res["changed"] == []
    assert certs.item_detail(conn, iid, True)["manual_fields"] == []
    assert conn.execute("SELECT updated_by FROM cert_item WHERE id=?", (iid,)).fetchone()[0] is None      # 沒寫入
    data = _xlsx([_row()])
    plan = certs.import_plan(conn, "x.xlsx", data)
    assert plan["rows"][0]["status"] == "update"
    certs.import_apply(conn, "x.xlsx", data, "adm", {}, plan["file_sha256"], plan["plan_digest"])
    assert certs.item_detail(conn, iid, True)["req_unit"] == "營運部"       # 仍可補進
    # 真的有改才鎖
    assert certs.update_item(conn, iid, {"req_unit": "資訊處"}, "alice", True)["changed"] == ["req_unit"]


def test_API_原樣PUT空欄位_不寫稽核(env):
    iid = _mk(env)
    admin = env.client("admin")
    n0 = env.conn().execute("SELECT count(*) FROM activity_log WHERE action='cert_update'").fetchone()[0]
    r = admin.put(f"/api/certs/items/{iid}", json={"note": "", "os_type": ""})
    assert r.json()["changed"] == []
    assert env.conn().execute("SELECT count(*) FROM activity_log WHERE action='cert_update'").fetchone()[0] == n0


def test_Excel日期儲存格與文字日期_到期日一致():
    hd = HEADERS + ["憑證到期日"]
    wb = Workbook()
    ws = wb.active
    ws.append(hd)
    ws.append(_row(name="a.example.test", bigip="198.51.100.21") + [datetime(2026, 11, 30)])           # Excel 日期（00:00:00）
    ws.append(_row(name="b.example.test", bigip="198.51.100.22") + ["2026-11-30"])                      # 文字
    ws.append(_row(name="c.example.test", bigip="198.51.100.23") + [datetime(2026, 11, 30, 10, 30)])    # 有時間的不動
    b = io.BytesIO()
    wb.save(b)
    sandbox = sqlite3.connect(":memory:")
    sandbox.row_factory = sqlite3.Row
    plan = certs.import_plan(sandbox, "x.xlsx", b.getvalue())
    exp = [r["expiry_utc"] for r in plan["rows"]]
    assert exp[0] == exp[1] == "2026-11-30T15:59:59Z"
    assert exp[2] == "2026-11-30T02:30:00Z"


def test_極端日期_回400不是500_匯入預覽標列錯誤不整份失敗(conn):
    for bad in ("0001-01-01", "9999-12-31 23:59-08:00", "0001-01-01 00:00+08:00", "1999-12-31", "2101-01-01"):
        with pytest.raises(ValueError):
            certs.to_utc_iso(bad)
    assert certs.parse_planned("0001-01-01 00:00") is None
    hd = HEADERS + ["憑證到期日"]
    data = _xlsx([_row(name="a.example.test", bigip="198.51.100.21") + ["0001-01-01 00:00+08:00"],
                  _row(name="b.example.test", bigip="198.51.100.22") + ["9999-12-31 23:59-08:00"],
                  _row(name="c.example.test", bigip="198.51.100.23") + ["2027-01-01"]], headers=hd)
    plan = certs.import_plan(conn, "x.xlsx", data)
    assert [r["status"] for r in plan["rows"]] == ["new", "new", "new"]
    assert "到期日欄位看不懂" in plan["rows"][0]["reason"] and "到期日欄位看不懂" in plan["rows"][1]["reason"]
    assert plan["rows"][0]["expiry_utc"] is None and plan["rows"][2]["expiry_utc"]
    certs.import_apply(conn, "x.xlsx", data, "adm", {})         # 寫入也不會炸


def test_API_極端日期與decisions非字串_回400(env):
    iid = _mk(env)
    admin = env.client("admin")
    for bad in ("0001-01-01", "9999-12-31 23:59-08:00"):
        assert admin.post(f"/api/certs/items/{iid}/observations", json={"not_after": bad}).status_code == 400
    data = _xlsx([_row()])
    for dec in ('{"3": 5}', '{"3": null}', '{"3": ["a"]}', '{"3": {"x": 1}}'):
        r = admin.post("/api/certs/import/apply", files={"file": ("a.xlsx", data)}, data={"decisions": dec})
        assert r.status_code == 400, (dec, r.text)


def test_API_非管理員看不到建立者更新者帳號(env):
    iid = _mk(env)
    admin, user, viewer = env.client("admin"), env.client("alice"), env.client("vera")
    admin.put(f"/api/certs/items/{iid}", json={"req_unit": "資訊處"})
    admin.post(f"/api/certs/items/{iid}/observations", json={"not_after": "2027-01-01 00:00"})
    a = admin.get(f"/api/certs/items/{iid}").json()
    assert a["created_by"] == "admin" and a["updated_by"] == "admin" and a["observations"][0]["created_by"] == "admin"
    for cl in (user, viewer):
        d = cl.get(f"/api/certs/items/{iid}").json()
        assert "created_by" not in d and "updated_by" not in d
        assert all("created_by" not in o for o in d["observations"])
        assert "admin" not in str({k: v for k, v in d.items() if k not in ("key_text",)})


def test_同時新增同一把鍵_撞唯一索引回409不是500(env, monkeypatch):
    iid = _mk(env)
    import certs as c
    monkeypatch.setattr(c, "_find_by_key", lambda *a, **k: None)         # 模擬事前檢查之後才被別人搶先
    admin = env.client("admin")
    r = admin.post("/api/certs/items", json={"name": "dup", "environment": "正式", "endpoint": "198.51.100.10", "mount_type": "F5"})
    assert r.status_code == 409, r.text
    other = admin.post("/api/certs/items", json={"name": "o", "environment": "正式", "endpoint": "192.0.2.50"}).json()["id"]
    r = admin.put(f"/api/certs/items/{other}", json={"environment": "正式", "endpoint": "198.51.100.10", "mount_type": "F5"})
    assert r.status_code == 409, r.text
    assert iid


def test_前端守則_匯入統一走資料匯入頁_用途為下拉():
    fe = ROOT / "APP" / "asset-module" / "frontend" / "pages" / "v2" / "certs"
    idx = (fe / "index.vue").read_text(encoding="utf-8")
    assert "k: 'import'" not in idx and "/api/certs/import" not in idx          # 憑證盤點頁沒有自己的匯入（檢查表第 6 項）
    assert 'to="/v2/settings?st=import"' in idx                                                 # 改成連到資料匯入頁
    assert 'v-model="purposeCode"' in idx and 'v-model="purpose"' not in idx
    imp = (ROOT / "APP" / "asset-module" / "frontend" / "pages" / "import.vue").read_text(encoding="utf-8")
    assert "<CertImportPanel" in imp and "憑證調查表" in imp
    det = (fe / "[id].vue").read_text(encoding="utf-8")
    assert "String(form[k] ?? '') !== String(d.value[k] ?? '')" in det          # 只送有改的欄位
