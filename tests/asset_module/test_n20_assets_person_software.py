"""N20（V61）：2.1 主機查詢「逐筆登記」補舊 /assets 的人員、軟體、任意欄位條件。

/api/assets 新增 person／software（只讀、參數化、部分比對），跟既有 filter_field 同值篩選、q 可交集；
X-Total-Count 跟著同一份條件（匯出全部＝同一支 API 不帶 limit）。前端接線用原始碼檢查釘住。
"""
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
from test_api import _client, _insert_hardware  # noqa: E402

FE = ROOT / "APP" / "asset-module" / "frontend"


def _seed(db_path):
    _insert_hardware(db_path, "S-1", "web01", "10.99.1.1", device_model="DL380")
    _insert_hardware(db_path, "S-2", "db01", "10.99.1.2", device_model="DL380")
    _insert_hardware(db_path, "S-3", "app01", "10.99.1.3", device_model="R740")
    conn = db.get_connection(db_path)
    try:
        conn.execute("INSERT INTO personnel (asset_serial, person_name, proxy1, belong_department) VALUES ('S-1', '使用者A', '使用者B', '系統部')")
        conn.execute("INSERT INTO personnel (asset_serial, person_name) VALUES ('S-3', '使用者C')")
        conn.execute("INSERT INTO software (asset_serial, asset_name, db_software) VALUES ('S-2', '核心資料庫', 'Oracle 19c')")
        conn.execute("INSERT INTO software (asset_serial, asset_name, db_software) VALUES ('S-3', '應用程式', 'Tomcat')")
        conn.commit()
    finally:
        conn.close()


def _serials(resp):
    assert resp.status_code == 200, resp.text
    return sorted(r["asset_serial"] for r in resp.json())


def test_人員條件_比對姓名代理人部門():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        _seed(db_path)
        assert _serials(client.get("/api/assets", params={"person": "者A"})) == ["S-1"]
        assert _serials(client.get("/api/assets", params={"person": " 使用者B "})) == ["S-1"]
        assert _serials(client.get("/api/assets", params={"person": "系統部"})) == ["S-1"]
        assert _serials(client.get("/api/assets", params={"person": "沒有這個人"})) == []
        # 空字串＝不篩
        assert _serials(client.get("/api/assets", params={"person": ""})) == ["S-1", "S-2", "S-3"]


def test_軟體條件_比對資料庫軟體與資產名稱():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        _seed(db_path)
        assert _serials(client.get("/api/assets", params={"software": "oracle"})) == ["S-2"]
        assert _serials(client.get("/api/assets", params={"software": "應用"})) == ["S-3"]


def test_條件交集_總筆數跟著條件():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        _seed(db_path)
        r = client.get("/api/assets", params={"filter_field": "device_model", "filter_value": "DL380", "software": "Oracle", "limit": 1})
        assert _serials(r) == ["S-2"]
        assert r.headers["X-Total-Count"] == "1"
        assert _serials(client.get("/api/assets", params={"person": "者C", "software": "Tomcat"})) == ["S-3"]
        assert _serials(client.get("/api/assets", params={"person": "者A", "software": "Tomcat"})) == []


def test_SQL字元當一般文字_不出錯():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        _seed(db_path)
        assert _serials(client.get("/api/assets", params={"person": "'; DROP TABLE hardware;--"})) == []
        assert _serials(client.get("/api/assets")) == ["S-1", "S-2", "S-3"]
        # % 與 _ 是字面字元，不當萬用字元（1404 審查 P3）
        assert _serials(client.get("/api/assets", params={"person": "%"})) == []
        assert _serials(client.get("/api/assets", params={"software": "_"})) == []


def test_前端_條件列寫進網址且可分享():
    util = (FE / "utils" / "assetRecords.ts").read_text(encoding="utf-8")
    keys = re.search(r"RECORD_FILTER_KEYS = \[(.*?)\] as const", util, re.S).group(1)
    assert "'person'" in keys and "'software'" in keys
    vue = (FE / "components" / "v2" / "AssetRecords.vue").read_text(encoding="utf-8")
    assert "submitConds" in vue and "person:" in vue and "software:" in vue and "filter_field: fieldPick.value" in vue
    assert "人員：${f.person}" in vue and "軟體：${f.software}" in vue     # 已選條件標籤可移除
    hosts = (FE / "pages" / "v2" / "hosts.vue").read_text(encoding="utf-8")
    assert "依人員／軟體／欄位值找登記" in hosts
