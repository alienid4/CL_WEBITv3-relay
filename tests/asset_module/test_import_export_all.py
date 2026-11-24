"""2.068.0：資料匯入頁每個來源都要能匯出（使用者要把測試機的資料搬到正式機）。

守的重點：
1. 每個新來源匯得出、而且「Excel 原封匯得回去」（表頭跟匯入端認得的一樣）——匯不回去的 Excel 比沒有更糟。
2. AD 人員名單＝全體員工個資：不提供任何匯出（2.068.2，之後改 LDAPS 直連同步）；匯入也不留原始檔。
3. 新來源的匯出跟既有匯出同一道權限：一般帳號／只能看的帳號拿不到。
4. 原始檔只在匯入成功後留，匯出時檔名就是當初的檔名（副檔名對，才匯得回去）。
5. Excel 下載檔名是 .xlsx（舊版畫面存成 `business_system.excel`，匯回被拒）。
"""
import io
import sys
from pathlib import Path
from urllib.parse import unquote

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
import import_export as ie  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ROLE_AWARE_TESTS = True
_PW = "s3cure-pass!"          # 測試用假密碼（不是真實憑證）


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """兩個 DB（A＝測試機、B＝正式機）：use() 切到哪個，API 就打哪個。"""
    monkeypatch.delenv(api.AUTH_DISABLED_ENV, raising=False)
    paths = {"A": tmp_path / "a" / "t.db", "B": tmp_path / "b" / "t.db"}
    monkeypatch.setenv("ASSET_DB_PATH", str(paths["A"]))   # 原始檔跟著 DB 走，不能寫到預設的 data/
    for pth in paths.values():
        pth.parent.mkdir(parents=True, exist_ok=True)
        db.init_db(pth)
        conn = db.get_connection(pth)
        for name, role in (("admin", "admin"), ("alice", "user"), ("vera", "viewer")):
            db.create_user(conn, name, auth.hash_password(_PW), role=role)
        conn.close()
    state = {"cur": "A"}

    def _override_get_db():
        conn = db.get_connection(paths[state["cur"]])
        try:
            yield conn
        finally:
            conn.close()

    api.app.dependency_overrides[api.get_db] = _override_get_db

    class E:
        path = paths["A"]

        def use(self, which):
            state["cur"] = which
            monkeypatch.setenv("ASSET_DB_PATH", str(paths[which]))

        def client(self, name):
            c = TestClient(api.app)
            r = c.post("/api/auth/login", json={"username": name, "password": _PW})
            assert r.status_code == 200, r.text
            return c

        def conn(self, which=None):
            return db.get_connection(paths[which or state["cur"]])

    yield E()
    api.app.dependency_overrides.pop(api.get_db, None)


def _xlsx(headers, rows) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _read_xlsx(data: bytes):
    ws = openpyxl.load_workbook(io.BytesIO(data)).active
    return [list(r) for r in ws.iter_rows(values_only=True)]


def _fname(resp) -> str:
    cd = resp.headers.get("content-disposition", "")
    return unquote(cd.split("filename*=UTF-8''", 1)[1]) if "filename*=" in cd else cd


def _biz(c, *ids):
    for a in ids:
        c.execute("INSERT INTO business_system (api_id, name) VALUES (?, ?)", (a, f"{a}系統"))
    c.commit()


# ---------------------------------------------------------------- 每個來源都有登記、格式清楚
def test_匯入頁的來源都登記了匯出_AD除外():
    for key in ("sys_class", "syscat", "segments", "certs", "alert_contact", "srv_sheet"):
        assert key in ie.SOURCES, f"{key} 沒有匯出"
    assert "ad" not in ie.SOURCES, "AD 人員名單不提供任何匯出（2.068.2）"
    assert ie.SOURCES["certs"].excel is False and ie.SOURCES["srv_sheet"].excel is False


def test_dump只給匯入吃得回去的來源():
    assert [k for k, s in ie.SOURCES.items() if s.dump] == ["account_audit"]


def test_來源清單回報格式與原始檔是哪一批(env):
    admin = env.client("admin")
    c = env.conn()
    _biz(c, "N-001")
    c.close()
    assert admin.post("/api/business-systems/import-class",
                      files={"file": ("APID_1119.xlsx", _xlsx(["APID", "系統類別"], [["N-001", "第一類"]]))}).status_code == 200
    by = {s["key"]: s for s in admin.get("/api/import/sources").json()["sources"]}
    assert "ad" not in by
    assert by["certs"]["formats"] == {"original": True, "excel": False, "dump": False}
    assert by["sys_class"]["original_name"] == "APID_1119.xlsx" and by["sys_class"]["original_saved_at"]
    assert "生效" in (by["alert_contact"]["excel_note"] or "")


# ---------------------------------------------------------------- AD：任何匯出都不提供
@pytest.mark.parametrize("kind", ["original", "excel", "dump"])
def test_AD名單任何匯出都不提供(env, kind):
    r = env.client("admin").get(f"/api/import/ad/export/{kind}")
    assert r.status_code in (403, 404), r.status_code


def test_AD匯入不留原始檔(env):
    data = _xlsx(["SamAccountName", "DisplayName"], [["01000003", "王五"]])
    r = env.client("admin").post("/api/ad/import", files={"file": ("ad.xlsx", data)})
    assert r.status_code == 200, r.text
    assert not (ie.archive_root() / "ad").exists()


# ---------------------------------------------------------------- 權限、快取、紀錄
@pytest.mark.parametrize("src", ["sys_class", "syscat", "segments", "certs", "alert_contact", "srv_sheet"])
@pytest.mark.parametrize("kind", ["original", "excel"])
def test_新來源匯出_一般帳號拿不到(env, src, kind):
    for who in ("alice", "vera"):
        assert env.client(who).get(f"/api/import/{src}/export/{kind}").status_code == 403, who


def test_匯出不進快取_而且留下載紀錄(env):
    c = env.conn()
    _biz(c, "N-001")
    c.close()
    r = env.client("admin").get("/api/import/business_system/export/excel")
    assert r.status_code == 200 and r.headers.get("cache-control") == "no-store"
    assert _fname(r).endswith(".xlsx")
    c = env.conn()
    log = c.execute("SELECT export_name, row_count, filename FROM export_log WHERE status = 200 ORDER BY id DESC").fetchone()
    c.close()
    assert "業務系統對照表" in log["export_name"] and log["row_count"] == 1 and log["filename"].endswith(".xlsx")


def test_原始檔不存在時_不記成功的匯出(env):
    r = env.client("admin").get("/api/import/sys_class/export/original")
    assert r.status_code == 404
    c = env.conn()
    n = c.execute("SELECT COUNT(*) FROM export_log WHERE status = 200").fetchone()[0]
    c.close()
    assert n == 0


def test_不支援的格式回400並講原因(env):
    r = env.client("admin").get("/api/import/certs/export/excel")
    assert r.status_code == 400 and "原始檔" in r.json()["detail"]
    r = env.client("admin").get("/api/import/dynassets/export/dump")
    assert r.status_code == 400 and "dump" in r.json()["detail"]


# ---------------------------------------------------------------- 公式注入
def test_Excel字串寫成文字_不會變公式_匯回值不變(env):
    c = env.conn()
    c.execute("INSERT INTO business_system (api_id, name, ap_department) VALUES ('N-001', '=1+1', '@SUM(A1)')")
    c.commit()
    c.close()
    data = env.client("admin").get("/api/import/business_system/export/excel").content
    ws = openpyxl.load_workbook(io.BytesIO(data)).active
    head = [x.value for x in ws[1]]
    cell = ws.cell(row=2, column=head.index("name") + 1)
    assert cell.data_type == "s" and cell.value == "=1+1", (cell.data_type, cell.value)
    env.use("B")
    r = env.client("admin").post("/api/business-systems/import", files={"file": ("bs.xlsx", data)})
    assert r.status_code == 200, r.text
    c = env.conn("B")
    assert c.execute("SELECT name FROM business_system WHERE api_id='N-001'").fetchone()[0] == "=1+1"
    c.close()


# ---------------------------------------------------------------- 真實往返：A 匯出 API → B 匯入 API
def test_系統類別_真實往返_含人工覆寫的生效值(env):
    c = env.conn("A")
    _biz(c, "N-001", "N-002", "N-003")
    c.execute("UPDATE business_system SET sys_class='第一類' WHERE api_id IN ('N-001','N-002')")
    c.execute("UPDATE business_system SET sys_class_override='第三類' WHERE api_id='N-002'")
    c.execute("UPDATE business_system SET sys_class_override='第二類' WHERE api_id='N-003'")
    c.commit()
    eff_sql = ("SELECT api_id, COALESCE(sys_class_override, sys_class) FROM business_system "
               "WHERE COALESCE(sys_class_override, sys_class) IS NOT NULL ORDER BY api_id")
    want = [tuple(x) for x in c.execute(eff_sql).fetchall()]
    c.close()
    data = env.client("admin").get("/api/import/sys_class/export/excel").content

    env.use("B")
    c = env.conn("B")
    _biz(c, "N-001", "N-002", "N-003")
    c.close()
    r = env.client("admin").post("/api/business-systems/import-class", files={"file": ("cls.xlsx", data)})
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 3
    c = env.conn("B")
    got = [tuple(x) for x in c.execute(eff_sql).fetchall()]
    c.close()
    assert got == want == [("N-001", "第一類"), ("N-002", "第三類"), ("N-003", "第二類")]


def test_告警聯絡_真實往返_含人工覆寫的生效值(env):
    import alert_contact
    c = env.conn("A")
    alert_contact.ensure(c)
    c.execute("INSERT INTO alert_contact (api_id, ap_team, unit, system_name, sys_class, email) VALUES "
              "('N-001', 'AP一組', '測試部', '甲系統', '第一類', 'a@example.test'),"
              "('N-002', 'AP二組', '測試部', '乙系統', '第二類', 'b@example.test')")
    c.execute("INSERT INTO alert_contact_override (api_id, email, ap_team) VALUES ('N-002', 'oncall@example.test', NULL),"
              "('N-003', 'c@example.test', 'AP三組')")
    c.commit()
    fields = ("ap_team", "unit", "system_name", "sys_class", "email")
    want = {a: {f: alert_contact.Resolver(c).get(a)[f] for f in fields} for a in ("N-001", "N-002", "N-003")}
    c.close()
    data = env.client("admin").get("/api/import/alert_contact/export/excel").content
    assert len(_read_xlsx(data)) == 4

    env.use("B")
    b = env.client("admin")
    pv = b.post("/api/v2/alert-contacts/import", files={"file": ("ac.xlsx", data)}, data={"confirm": "false"})
    assert pv.status_code == 200 and pv.json()["can_confirm"], pv.text
    r = b.post("/api/v2/alert-contacts/import", files={"file": ("ac.xlsx", data)},
               data={"confirm": "true", "plan_digest": pv.json()["plan_digest"]})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 3
    c = env.conn("B")
    got = {a: {f: alert_contact.Resolver(c).get(a)[f] for f in fields} for a in ("N-001", "N-002", "N-003")}
    c.close()
    assert got == want
    assert want["N-002"]["email"] == "oncall@example.test"


def test_網段_Excel跟原本網段匯出同一份(env):
    import segments
    c = env.conn()
    assert ie.export_rows(c, "segments") == segments.export_current_rows(c)
    c.close()


# ---------------------------------------------------------------- 原始檔：匯入成功才留、檔名不變、存不下就清
def test_網段匯入成功後_原始檔可以原樣下載(env):
    data = _xlsx(["網段", "地點"], [["10.99.1.0/24", "測試機房"]])
    admin = env.client("admin")
    r = admin.post("/api/segments/import", files={"file": ("網段配置表_20261119.xlsx", data)})
    assert r.status_code == 200, r.text
    r = admin.get("/api/import/segments/export/original")
    assert r.status_code == 200 and r.headers.get("cache-control") == "no-store"
    assert r.content == data, "原始檔一個位元都不能變"
    assert _fname(r) == "網段配置表_20261119.xlsx"


def test_系統類別匯入失敗_不留原始檔(env):
    r = env.client("admin").post("/api/business-systems/import-class",
                                 files={"file": ("bad.xlsx", _xlsx(["亂七八糟"], [["x"]]))})
    assert r.status_code == 400
    assert ie.original_for("sys_class") is None


def test_原始檔存不下來_要清掉舊的_不可以讓上一批頂替(env, monkeypatch, capsys):
    ie.save_original("segments", "上一批.xlsx", b"old")
    assert ie.original_for("segments") is not None

    def _boom(*a, **k):
        raise OSError(28, "No space left on device", str(ie.archive_root() / "segments" / "x"))

    monkeypatch.setattr(ie, "save_original", _boom)
    ie.keep_original("segments", "這一批.xlsx", b"new")
    assert ie.original_for("segments") is None, "存檔失敗卻留著上一批，會被當成這一批的原始檔"
    out = capsys.readouterr().out
    assert "OSError" in out and str(ie.archive_root()) not in out, "訊息不可以印絕對路徑"


def test_跨來源下載_前端讀得到檔名():
    """前端在 :3000、後端在 :8000——沒開放 Content-Disposition，瀏覽器就讀不到檔名，存成沒有副檔名的檔。"""
    from starlette.middleware.cors import CORSMiddleware
    m = next(m for m in api.app.user_middleware if m.cls is CORSMiddleware)
    assert "Content-Disposition" in (m.kwargs.get("expose_headers") or [])
