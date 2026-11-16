"""W221-008 迴歸：中文帳號的 workbook 下載不得 500；openapi 產生不得拋例外。

起因（v2.017.4）：X-Updated-By 放中文 → HTTP 標頭只吃 latin-1 → UnicodeEncodeError；
DispatchWeeklyBody 沒進 _app_shared.__all__ → /openapi.json 500。
"""
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import db  # noqa: E402
import patch_store  # noqa: E402

from test_api import _client  # noqa: E402


def test_中文updated_by的workbook_GET回200():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            conn = db.get_connection(db_path)
            try:
                patch_store.put(conn, "workbook", b"PK-fake-bytes",
                                content_type="application/octet-stream",
                                file_name="弱點總表_十月.xlsx", by="王小明")
            finally:
                conn.close()
            r = client.get("/api/patch/blob/workbook")
            assert r.status_code == 200, r.text
            assert unquote(r.headers["X-Updated-By"]) == "王小明"
            assert unquote(r.headers["X-File-Name"]) == "弱點總表_十月.xlsx"
            assert r.content == b"PK-fake-bytes"
        finally:
            api.app.dependency_overrides.clear()


def test_openapi不拋例外():
    schema = api.app.openapi()
    assert schema["paths"]
    # 每週收集設定的 body 必須被解析成 requestBody，不是 query 參數
    put = schema["paths"]["/api/collect/dispatch/weekly"]["put"]
    assert "requestBody" in put
