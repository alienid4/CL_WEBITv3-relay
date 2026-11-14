"""Codex 1193：CIA 匯入「欄位對應」把某欄設成「不匯入」後，那一列要還看得到、能改回「匯入」。

做法：停用的列記在 field_mapping.json 的 `_disabled`（{分頁: {Excel 標題: 原本欄位}}），
GET 一併回、畫面列出；匯入／匯出讀對應表的地方（parse_mapping）看不到它，所以停用的照舊不匯入。
雙向都釘：停用後看得到＋不匯入、改回後恢復匯入、舊格式檔（沒有 _disabled）不壞、啟用欄位的匯入結果不變。
⚠ D4（CIA 匯入設定）。全假資料。
"""
import json
import sys
import tempfile
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
import excel_import  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_STUB_CREDENTIAL = "test-password-123"
_REAL_MAPPING = excel_import.MAPPING_PATH.read_bytes()


@pytest.fixture
def env(monkeypatch, tmp_path):
    """暫存的 field_mapping.json（從正式那份複製）＋登入好的 client＋DB 路徑。"""
    mp = tmp_path / "field_mapping.json"
    mp.write_bytes(_REAL_MAPPING)
    monkeypatch.setattr(excel_import, "MAPPING_PATH", mp)
    original = api.MAPPING_PATH
    api.MAPPING_PATH = mp                       # api 的 __setattr__ 會同步進各 router 模組
    db_path = tmp_path / "t.db"
    db.init_db(db_path)

    def _ov():
        cn = db.get_connection(db_path)
        try:
            yield cn
        finally:
            cn.close()
    api.app.dependency_overrides[api.get_db] = _ov
    cn = db.get_connection(db_path)
    db.create_user(cn, "tester", auth.hash_password(_STUB_CREDENTIAL))
    cn.close()
    client = TestClient(api.app)
    assert client.post("/api/auth/login", json={"username": "tester", "password": _STUB_CREDENTIAL}).status_code == 200
    try:
        yield client, mp, db_path, tmp_path
    finally:
        api.MAPPING_PATH = original
        api.app.dependency_overrides.clear()


def _hostname_header(fm: dict) -> str:
    return next(h for h, f in fm["硬體"].items() if f == "hostname")


def _set(client, header: str, value: str) -> dict:
    fm = client.get("/api/import/field-mapping").json()
    fm["硬體"][header] = value
    r = client.put("/api/import/field-mapping", json={"mapping": fm})   # 畫面原封送回（含 _disabled）
    assert r.status_code == 200, r.text
    return r.json()


def _workbook(path: Path, host_header: str) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "硬體"
    ws.append(["資產序號", host_header, "IP", "設備機型"])
    ws.append(["HW-FM-0001", "fm-host-01", "10.99.1.11", "Fake Model"])
    wb.save(path)
    return path


def _import_row(db_path: Path, xlsx: Path, mapping=None) -> dict:
    cn = db.get_connection(db_path)
    try:
        excel_import.import_excel(xlsx, cn, mapping=mapping)
        row = cn.execute("SELECT asset_serial, hostname, ip, device_model FROM hardware WHERE asset_serial='HW-FM-0001'").fetchone()
        out = dict(row)
        cn.execute("DELETE FROM hardware")
        cn.commit()
        return out
    finally:
        cn.close()


def test_1193_停用硬體主機名稱後_欄位對應仍看得到這列(env):
    client, _mp, _db, _t = env
    header = _hostname_header(client.get("/api/import/field-mapping").json())
    _set(client, header, "不匯入")
    after = client.get("/api/import/field-mapping").json()
    assert header not in after["硬體"]                                   # 分頁對應（匯入用）裡沒有它
    assert after["_disabled"]["硬體"][header] == "hostname"              # 但畫面拿得到、記得原本欄位


def test_停用的欄位匯入時不讀_其他欄位照舊(env):
    client, mp, db_path, tmp = env
    header = _hostname_header(client.get("/api/import/field-mapping").json())
    xlsx = _workbook(tmp / "a.xlsx", header)
    before = _import_row(db_path, xlsx)
    assert before["hostname"] == "fm-host-01"
    _set(client, header, "不匯入")
    assert "hostname" not in excel_import.load_mapping()["硬體"].values()
    after = _import_row(db_path, xlsx)
    assert not after["hostname"]                                          # 停用的照舊不匯入
    assert {k: v for k, v in after.items() if k != "hostname"} == {k: v for k, v in before.items() if k != "hostname"}
    saved = mp.read_text(encoding="utf-8")
    assert "不匯入" not in saved                                          # 存的是原本欄位，不是字面值
    assert "_comment" in json.loads(saved)


def test_改回匯入後恢復匯入_停用清單清掉(env):
    client, mp, db_path, tmp = env
    header = _hostname_header(client.get("/api/import/field-mapping").json())
    _set(client, header, "不匯入")
    body = _set(client, header, "hostname")
    assert body["硬體"][header] == "hostname"
    assert "_disabled" not in body
    assert "_disabled" not in json.loads(mp.read_text(encoding="utf-8"))
    assert _import_row(db_path, _workbook(tmp / "b.xlsx", header))["hostname"] == "fm-host-01"


def test_舊格式檔沒有disabled_GET與匯入對應表跟以前一樣(env):
    client, mp, _db, _t = env
    assert "_disabled" not in json.loads(_REAL_MAPPING)
    got = client.get("/api/import/field-mapping").json()
    assert got.pop("_version")                                            # Codex 1185：多回設定檔版本（儲存時帶回）
    assert got == excel_import.parse_mapping(_REAL_MAPPING)              # 沒有停用列＝GET 形狀不變
    # 原樣存回一次：沒有任何停用，檔案不會多出 _disabled
    assert client.put("/api/import/field-mapping", json={"mapping": got}).status_code == 200
    assert "_disabled" not in json.loads(mp.read_text(encoding="utf-8"))
    assert excel_import.parse_mapping(mp.read_bytes()) == excel_import.parse_mapping(_REAL_MAPPING)


def test_disabled區塊不影響啟用欄位的匯入對應表():
    data = json.loads(_REAL_MAPPING)
    with_disabled = dict(data)
    with_disabled["_disabled"] = {"硬體": {"某個舊標題": "hostname"}}
    raw = json.dumps(with_disabled, ensure_ascii=False).encode("utf-8")
    assert excel_import.parse_mapping(raw) == excel_import.parse_mapping(_REAL_MAPPING)
    assert excel_import.parse_disabled(raw) == {"硬體": {"某個舊標題": "hostname"}}
    assert excel_import.parse_disabled(_REAL_MAPPING) == {}
    # 壞格式：只影響畫面，不擋匯入
    bad = dict(data)
    bad["_disabled"] = ["x"]
    raw_bad = json.dumps(bad, ensure_ascii=False).encode("utf-8")
    assert excel_import.parse_disabled(raw_bad) == {}
    assert excel_import.parse_mapping(raw_bad) == excel_import.parse_mapping(_REAL_MAPPING)


def test_舊畫面沒送停用列_停用照留(env):
    """舊頁（pages/import.vue ?legacy）只送分頁對應、不知道停用列：存一次不能把停用清單洗掉。"""
    client, _mp, _db, _t = env
    header = _hostname_header(client.get("/api/import/field-mapping").json())
    _set(client, header, "不匯入")
    fm = client.get("/api/import/field-mapping").json()
    fm.pop("_disabled")
    assert client.put("/api/import/field-mapping", json={"mapping": fm}).status_code == 200
    assert client.get("/api/import/field-mapping").json()["_disabled"]["硬體"][header] == "hostname"


def test_不存在的欄位照舊擋_停用列不能改成亂填(env):
    client, _mp, _db, _t = env
    header = _hostname_header(client.get("/api/import/field-mapping").json())
    _set(client, header, "不匯入")
    fm = client.get("/api/import/field-mapping").json()
    fm["硬體"][header] = "no_such_column"
    assert client.put("/api/import/field-mapping", json={"mapping": fm}).status_code == 400
    assert client.put("/api/import/field-mapping", json={"mapping": {"不存在分頁": {}}}).status_code == 400
