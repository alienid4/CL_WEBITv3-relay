"""值班電話名冊匯入（#11）＋夜間速查自動檢測開關後端（W221-N1）。
全部假資料：系統代號 TEST-*、電話 0900-000-xxx。釘住：dry-run 不寫庫、確認才寫、衝突不自動覆蓋、格式錯、
非 admin 403、稽核有寫（且不記整份檔案）、重複匯入冪等、設定預設開且只 admin 可改。"""
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import daily_risk as dr  # noqa: E402
import db  # noqa: E402

ADMIN = {"username": "admin"}
USER = {"username": "someone"}
HEAD = "系統代號 api_id,姓名,電話,角色,備註\n"


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.insert_hardware(c, asset_serial="T1", hostname="h1", ip="10.99.8.1", api_id="TEST-001")
    db.insert_hardware(c, asset_serial="T2", hostname="h2", ip="10.99.8.2", api_id="TEST-002")
    return c


def _csv(body: str) -> bytes:
    return (HEAD + body).encode("utf-8")


def _up(data: bytes, name="r.csv"):
    return SimpleNamespace(filename=name, file=io.BytesIO(data))


def _imp(c, data, session=ADMIN, name="r.csv", plan=None):
    p = plan or api.roster_preview_endpoint(file=_up(data, name), session=ADMIN, conn=c)
    return api.roster_import_endpoint(file=_up(data, name), file_sha256=p["file_sha256"], plan_digest=p["plan_digest"],
                                      session=session, conn=c)


def _oncall_rows(c):
    return [dict(r) for r in c.execute("SELECT * FROM system_oncall ORDER BY api_id")]


def _audits(c):
    return [dict(r) for r in c.execute("SELECT * FROM contact_audit ORDER BY id")]


GOOD = _csv("TEST-001,王小明,0900-000-001,值班,夜間\nTEST-001,李小華,0900-000-002,備援,\nTEST-002,陳小美,0900-000-003,負責人,\n")


def test_dry_run不寫庫(tmp_path):
    c = _conn(tmp_path)
    p = dr.roster_plan(c, "r.csv", GOOD)
    assert p["counts"] == {"new": 3, "update": 0, "unchanged": 0, "conflict": 0, "error": 0}
    assert _oncall_rows(c) == [] and _audits(c) == []
    out = api.roster_preview_endpoint(file=_up(GOOD), session=ADMIN, conn=c)
    assert out["counts"]["new"] == 3
    assert _oncall_rows(c) == [] and _audits(c) == []


def test_確認後才寫_三種角色各落各處(tmp_path):
    c = _conn(tmp_path)
    r = _imp(c, GOOD)
    assert r["written_rows"] == 3 and r["systems_written"] == 2
    rows = {x["api_id"]: x for x in _oncall_rows(c)}
    assert rows["TEST-001"]["phone"] == "0900-000-001" and rows["TEST-001"]["note"] == "夜間"
    roles = json.loads(rows["TEST-001"]["roles_json"])
    assert roles["值班"]["name"] == "王小明" and roles["備援"]["phone"] == "0900-000-002"
    assert rows["TEST-002"]["phone"] is None
    assert json.loads(rows["TEST-002"]["roles_json"])["負責人"]["name"] == "陳小美"
    # 既有的值班電話查詢（夜間速查／事故）仍讀得到
    assert dr.list_oncall(c)[0]["phone"] == "0900-000-001"


def test_重複匯入冪等(tmp_path):
    c = _conn(tmp_path)
    dr.roster_apply(c, "r.csv", GOOD, "admin")
    snap = _oncall_rows(c)
    n = len(_audits(c))
    p = dr.roster_plan(c, "r.csv", GOOD)
    assert p["counts"] == {"new": 0, "update": 0, "unchanged": 3, "conflict": 0, "error": 0}
    r = dr.roster_apply(c, "r.csv", GOOD, "admin")
    assert r["written_rows"] == 0
    assert _oncall_rows(c) == snap
    assert len(_audits(c)) == n + 1      # 只多一筆摘要（寫入 0 筆），沒有逐系統異動


def test_更新列出改前改後(tmp_path):
    c = _conn(tmp_path)
    dr.roster_apply(c, "r.csv", GOOD, "admin")
    p = dr.roster_plan(c, "r.csv", _csv("TEST-001,王小明,0900-000-009,值班,夜間\n"))
    x = p["rows"][0]
    assert x["status"] == "update" and x["before"]["phone"] == "0900-000-001" and x["after"]["phone"] == "0900-000-009"


def test_同系統同角色多筆不同_列衝突不覆蓋(tmp_path):
    c = _conn(tmp_path)
    data = _csv("TEST-001,甲,0900-000-001,值班,\nTEST-001,乙,0900-000-002,值班,\nTEST-002,丙,0900-000-003,值班,\n")
    p = dr.roster_plan(c, "r.csv", data)
    assert [x["status"] for x in p["rows"]] == ["conflict", "conflict", "new"]
    r = dr.roster_apply(c, "r.csv", data, "admin")
    assert r["systems_written"] == 1 and r["skipped"] == 2
    assert [x["api_id"] for x in _oncall_rows(c)] == ["TEST-002"]


def test_完全相同的重複列只算一筆(tmp_path):
    c = _conn(tmp_path)
    p = dr.roster_plan(c, "r.csv", _csv("TEST-001,甲,0900-000-001,值班,\nTEST-001,甲,0900-000-001,值班,\n"))
    assert [x["status"] for x in p["rows"]] == ["new", "unchanged"]


@pytest.mark.parametrize("line,why", [
    ("TEST-999,甲,0900-000-001,值班,", "找不到系統代號"),
    ("TEST-001,甲,0900-000-001,組長,", "角色只能是"),
    ("TEST-001,甲,,值班,", "缺電話"),
    ("TEST-001,甲,abc,值班,", "電話格式"),
    ("TEST-001,甲,12,值班,", "電話格式"),
    (",甲,0900-000-001,值班,", "缺系統代號"),
])
def test_格式錯誤逐列列出_不寫(tmp_path, line, why):
    c = _conn(tmp_path)
    p = dr.roster_plan(c, "r.csv", _csv(line + "\n"))
    assert p["rows"][0]["status"] == "error" and why in p["rows"][0]["reason"]
    dr.roster_apply(c, "r.csv", _csv(line + "\n"), "admin")
    assert _oncall_rows(c) == []


def test_缺必要欄位直接擋():
    with pytest.raises(ValueError):
        dr.roster_plan(None, "r.csv", "姓名,電話\n甲,0900\n".encode("utf-8"))


def test_電話只去空白與統一分隔_不改號碼():
    assert dr.norm_phone("  0900-000-001 ; 02-1234 5678 ") == "0900-000-001、02-1234 5678"
    assert dr.norm_phone("0900-000-001,0900-000-002") == "0900-000-001、0900-000-002"
    assert dr.norm_phone(912000001.0) == "912000001"
    assert dr.norm_phone(None) == ""


def test_非admin全部403(tmp_path, monkeypatch):
    monkeypatch.delenv("WEBIT3_DISABLE_AUTH", raising=False)
    c = _conn(tmp_path)
    for call in (lambda: api.roster_preview_endpoint(file=_up(GOOD), session=USER, conn=c),
                 lambda: api.roster_import_endpoint(file=_up(GOOD), file_sha256="", plan_digest="", session=USER, conn=c),
                 lambda: api.roster_template_endpoint(fmt="csv", session=USER, conn=c)):
        with pytest.raises(api.HTTPException) as e:
            call()
        assert e.value.status_code == 403
    assert _oncall_rows(c) == []


def test_稽核有寫_有改前改後_且不記整份檔案(tmp_path):
    c = _conn(tmp_path)
    dr.roster_apply(c, "名冊.csv", GOOD, "admin")
    dr.roster_apply(c, "名冊.csv", _csv("TEST-001,王小明,0900-000-009,值班,夜間\n"), "admin")
    a = _audits(c)
    sys_rows = [x for x in a if x["kind"] == "oncall" and x["key"] == "TEST-001"]
    assert len(sys_rows) == 2 and sys_rows[0]["before_json"] is None
    assert json.loads(sys_rows[1]["before_json"])["phone"] == "0900-000-001"
    assert json.loads(sys_rows[1]["after_json"])["phone"] == "0900-000-009"
    assert all(x["changed_by"] == "admin" and x["changed_at"] for x in a)
    summ = [x for x in a if x["kind"] == "oncall_roster_import"]
    assert len(summ) == 2 and json.loads(summ[0]["after_json"])["filename"] == "名冊.csv"
    assert "0900" not in summ[0]["after_json"]          # 摘要只有檔名與筆數


def test_範本csv_與_xlsx_可被自己讀回(tmp_path):
    c = _conn(tmp_path)
    r = api.roster_template_endpoint(fmt="csv", session=ADMIN, conn=c)
    p = dr.roster_plan(c, "t.csv", r.body)
    assert p["row_count"] == 3 and p["counts"]["new"] == 3
    empty = tmp_path / "e.db"                                         # 真實環境沒有 TEST-001 → 範例列直接匯入會被擋
    db.init_db(empty)
    assert dr.roster_plan(db.get_connection(empty), "t.csv", r.body)["counts"]["error"] == 3
    x = api.roster_template_endpoint(fmt="xlsx", session=ADMIN, conn=c)
    p2 = dr.roster_plan(c, "t.xlsx", x.body)
    assert p2["row_count"] == 3
    with pytest.raises(api.HTTPException):
        api.roster_template_endpoint(fmt="pdf", session=ADMIN, conn=c)


def test_手動改值班電話_不會刪掉備援與負責人(tmp_path):
    c = _conn(tmp_path)
    dr.roster_apply(c, "r.csv", GOOD, "admin")
    dr.save_oncall(c, "TEST-001", "", "", "admin")        # 清空值班電話
    row = [x for x in _oncall_rows(c) if x["api_id"] == "TEST-001"][0]
    assert row["phone"] is None and "備援" in json.loads(row["roles_json"])
    assert "值班" not in json.loads(row["roles_json"])


# ---------------------------------------------------------------- W221-N1 夜間速查設定
def test_自動檢測預設開(tmp_path):
    c = _conn(tmp_path)
    out = api.night_settings_endpoint(session=USER, conn=c)
    assert out["auto_live"] is True


def test_關閉後讀得到_只admin能改(tmp_path, monkeypatch):
    monkeypatch.delenv("WEBIT3_DISABLE_AUTH", raising=False)
    c = _conn(tmp_path)
    from routers import risk
    with pytest.raises(api.HTTPException) as e:
        api.night_settings_save_endpoint(risk.NightSettingsBody(auto_live=False), session=USER, conn=c)
    assert e.value.status_code == 403
    assert api.night_settings_endpoint(session=USER, conn=c)["auto_live"] is True
    api.night_settings_save_endpoint(risk.NightSettingsBody(auto_live=False), session=ADMIN, conn=c)
    assert api.night_settings_endpoint(session=USER, conn=c)["auto_live"] is False
    api.night_settings_save_endpoint(risk.NightSettingsBody(auto_live=True), session=ADMIN, conn=c)
    assert api.night_settings_endpoint(session=USER, conn=c)["auto_live"] is True
    assert [x["kind"] for x in _audits(c)] == ["night_setting", "night_setting"]


# ---------------------------------------------------------------- 第二輪：Codex 審查補強
def test_預覽後DB被改_確認409_不寫(tmp_path):
    c = _conn(tmp_path)
    p = api.roster_preview_endpoint(file=_up(GOOD), session=ADMIN, conn=c)
    dr.save_oncall(c, "TEST-001", "0900-000-777", None, "other")      # 別人在預覽後改了
    before = _oncall_rows(c)
    with pytest.raises(api.HTTPException) as e:
        _imp(c, GOOD, plan=p)
    assert e.value.status_code == 409 and "重新預覽" in e.value.detail
    assert _oncall_rows(c) == before


def test_換檔_確認409(tmp_path):
    c = _conn(tmp_path)
    p = api.roster_preview_endpoint(file=_up(GOOD), session=ADMIN, conn=c)
    other = _csv("TEST-001,甲,0900-000-001,值班,\n")
    with pytest.raises(api.HTTPException) as e:
        _imp(c, other, plan=p)
    assert e.value.status_code == 409 and _oncall_rows(c) == []


def test_缺sha_也是409(tmp_path):
    c = _conn(tmp_path)
    with pytest.raises(api.HTTPException) as e:
        api.roster_import_endpoint(file=_up(GOOD), file_sha256="", plan_digest="", session=ADMIN, conn=c)
    assert e.value.status_code == 409 and _oncall_rows(c) == []


def test_相同則成功_且再來一次冪等(tmp_path):
    c = _conn(tmp_path)
    assert _imp(c, GOOD)["written_rows"] == 3
    assert _imp(c, GOOD)["written_rows"] == 0


def test_只改備註_姓名保留_改電話_姓名清掉且有稽核(tmp_path):
    c = _conn(tmp_path)
    dr.roster_apply(c, "r.csv", GOOD, "admin")
    dr.save_oncall(c, "TEST-001", "0900-000-001", "新備註", "admin")
    row = [x for x in _oncall_rows(c) if x["api_id"] == "TEST-001"][0]
    assert json.loads(row["roles_json"])["值班"]["name"] == "王小明" and row["note"] == "新備註"
    dr.save_oncall(c, "TEST-001", "0900-000-555", "新備註", "admin")
    row = [x for x in _oncall_rows(c) if x["api_id"] == "TEST-001"][0]
    assert "值班" not in json.loads(row["roles_json"])
    last = [x for x in _audits(c) if x["kind"] == "oncall" and x["key"] == "TEST-001"][-1]
    assert json.loads(json.loads(last["before_json"])["roles_json"])["值班"]["name"] == "王小明"


def test_超大檔被拒_有上限的讀取():
    big = io.BytesIO(b"x" * (dr.ROSTER_MAX_BYTES + 1000))
    with pytest.raises(ValueError):
        dr.read_limited(big)
    assert big.tell() == dr.ROSTER_MAX_BYTES + 1          # 只讀到 上限+1 就停
    assert dr.read_limited(io.BytesIO(b"ok")) == b"ok"


def test_zip炸彈樣本被擋(tmp_path):
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("a.xml", b"0" * (31 * 1024 * 1024))       # 高壓縮比：檔很小、解壓後超過 30MB
    assert len(buf.getvalue()) < 200 * 1024
    c = _conn(tmp_path)
    with pytest.raises(ValueError, match="過大"):
        dr.roster_plan(c, "b.xlsx", buf.getvalue())
    buf2 = io.BytesIO()
    with zipfile.ZipFile(buf2, "w") as z:
        for i in range(101):
            z.writestr(f"f{i}.xml", b"x")
    with pytest.raises(ValueError, match="過大"):
        dr.roster_plan(c, "b.xlsx", buf2.getvalue())
    with pytest.raises(ValueError):
        dr.roster_plan(c, "b.xlsx", b"not a zip")


def test_列數超過上限解析途中中止(tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "ROSTER_MAX_ROWS", 5)
    c = _conn(tmp_path)
    data = _csv("TEST-001,甲,0900-000-001,值班,\n" * 40)
    with pytest.raises(ValueError, match="列數太多"):
        dr.roster_plan(c, "r.csv", data)


@pytest.mark.parametrize("col", ["name", "note", "api"])
@pytest.mark.parametrize("lead", ["=", "+", "-", "@"])
def test_公式字元開頭_列為格式錯誤_不寫(tmp_path, col, lead):
    c = _conn(tmp_path)
    api_id, name, note = "TEST-001", "甲", ""
    if col == "name":
        name = lead + "cmd1"
    elif col == "note":
        note = lead + "1+1"
    else:
        api_id = lead + "TEST-001"
    line = f"{api_id},{name},0900-000-001,值班,{note}\n"
    p = dr.roster_plan(c, "r.csv", _csv(line))
    assert p["rows"][0]["status"] == "error" and "公式字元" in p["rows"][0]["reason"]
    dr.roster_apply(c, "r.csv", _csv(line), "admin")
    assert _oncall_rows(c) == []        # 沒寫進庫，匯出就不可能帶出公式


def test_電話允許加號開頭_但必須是電話字元(tmp_path):
    c = _conn(tmp_path)
    p = dr.roster_plan(c, "r.csv", _csv("TEST-001,甲,+886-900-000-001,值班,\nTEST-002,乙,=1+1,值班,\n"))
    assert [x["status"] for x in p["rows"]] == ["new", "error"]


def test_檔名清洗():
    assert dr.clean_filename("C:\\x\\..\\a/b/名冊\x00\n.csv") == "名冊.csv"
    assert len(dr.clean_filename("a" * 300)) == 100
    assert dr.clean_filename(None) == ""


def test_稽核檔名已清洗(tmp_path):
    c = _conn(tmp_path)
    dr.roster_apply(c, "../../x/名冊\x07.csv", GOOD, "admin")
    summ = [x for x in _audits(c) if x["kind"] == "oncall_roster_import"][0]
    assert summ["key"] == "名冊.csv" and json.loads(summ["after_json"])["filename"] == "名冊.csv"
