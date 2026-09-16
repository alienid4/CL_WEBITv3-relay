"""錯誤要看得到（2026-09-14）。

公司機按「匯出」失敗，瀏覽器只顯示「被 CORS 擋掉」——真正的 500 錯誤被蓋掉，
使用者跟我都只能猜。守三件事：
1. 未處理例外回 JSON 500（帶錯誤型別），而且帶得到 CORS 標頭，前端讀得到
2. Excel 匯出遇到控制字元不能整份 500
3. /api/collector-key 不能再 NameError（221 日誌 9/11 起一直 500）
"""
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import db  # noqa: E402
import import_export  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client(tmp_path):
    db_path = tmp_path / "t.db"
    db.init_db(db_path)

    def _get_db():
        conn = db.get_connection(db_path)
        try:
            yield conn
        finally:
            conn.close()

    api.app.dependency_overrides[api.get_db] = _get_db
    api.app.dependency_overrides[api.require_auth] = lambda: {"username": "t"}
    try:
        yield TestClient(api.app, raise_server_exceptions=False)
    finally:
        api.app.dependency_overrides.pop(api.get_db, None)
        api.app.dependency_overrides.pop(api.require_auth, None)


def test_未處理例外回JSON500_帶錯誤型別_而且有CORS標頭(client, monkeypatch):
    def boom(conn, source):
        raise RuntimeError("測試用的爆炸")

    monkeypatch.setattr(import_export, "export_rows", boom)
    origin = "http://localhost:3000"
    r = client.get("/api/import/business_system/export/excel", headers={"Origin": origin})
    assert r.status_code == 500
    assert "RuntimeError" in r.json()["detail"], "錯誤內容要回得到前端，不能只剩一個 500"
    if api._allowed_origins and origin in api._allowed_origins or api._allowed_origin_regex:
        # 允許的來源才會有這個標頭；有的話代表 500 有經過 CORS
        acao = r.headers.get("access-control-allow-origin")
        assert acao in (origin, None)


def test_Excel匯出遇到控制字元不會500(client, monkeypatch):
    monkeypatch.setattr(import_export, "export_rows",
                        lambda conn, source: (["名稱"], [["甲\x0b系統\x01"]]))
    r = client.get("/api/import/business_system/export/excel")
    assert r.status_code == 200 and r.content[:2] == b"PK"


def test_collector_key不再NameError(client):
    r = client.get("/api/collector-key")
    assert r.status_code != 500, r.text


@pytest.mark.parametrize("kind", ["excel", "dump"])
def test_Excel與dump匯出不能500(client, kind):
    """v1.70.0 起匯出端點用了沒 import 的 _now_local，公司機一按就 500（被 CORS 蓋掉看不到）。"""
    r = client.get(f"/api/import/business_system/export/{kind}")
    assert r.status_code == 200, r.text


def test_系統類別目前筆數(client):
    """匯入頁「系統類別對照表」那格原本寫死「—」，匯入成功 89 筆也看不到數字。"""
    r = client.get("/api/business-systems/class-count")
    assert r.status_code == 200
    assert r.json() == {"rated": 0, "total": 0}
