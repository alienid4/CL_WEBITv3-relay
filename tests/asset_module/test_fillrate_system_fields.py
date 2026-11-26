"""資料品質「填充率最低的欄位（待補資料）」不可把系統狀態欄位當成使用者該補的資料（Codex W221-015，2026-10-31）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

from routers.config_audit import is_system_state_field  # noqa: E402


def test_系統狀態欄位不算待補():
    for col in ["pending_at", "pending_by", "pending_note", "pending_status", "root_key_at", "collect_error",
                "manual_updated_at", "collect_ok", "kind_override", "os_source", "sys_class_override"]:
        assert is_system_state_field(col), col


def test_人工維護的業務欄位仍列在待補():
    for col in ["physical_location", "rack_no", "owner", "custodian", "api_id", "device_model", "os",
                "environment", "hostname", "ip"]:
        assert not is_system_state_field(col), col


def test_api_回傳分開兩組():
    import db
    import api
    from fastapi.testclient import TestClient
    import tempfile
    d = Path(tempfile.mkdtemp()) / "t.db"
    db.init_db(d)
    def _o():
        c = db.get_connection(d)
        try:
            yield c
        finally:
            c.close()
    api.app.dependency_overrides[api.get_db] = _o
    import os
    os.environ[api.AUTH_DISABLED_ENV] = "1"
    try:
        r = TestClient(api.app).get("/api/data-quality/field-fill")
        assert r.status_code == 200
        j = r.json()
        cols = {f["column"] for f in j["fields"]}
        scols = {f["column"] for f in j["system_fields"]}
        assert not (cols & scols)
        assert "pending_at" not in cols and "collect_error" not in cols
        assert "physical_location" in cols
    finally:
        os.environ.pop(api.AUTH_DISABLED_ENV, None)
        api.app.dependency_overrides.clear()
