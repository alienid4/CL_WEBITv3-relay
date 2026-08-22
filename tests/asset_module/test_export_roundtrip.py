"""匯出→匯入 round-trip：/api/export 匯出的 xlsx 必須能原封不動再匯入，且關鍵欄位一致。
（先前 export 用 field_meta label 當表頭、跟 import 期待的 field_mapping 標題對不上，round-trip 會失敗。）
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import auth  # noqa: E402
import db  # noqa: E402
import api  # noqa: E402
from excel_import import import_excel  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_PW = "test-password-123"


def _client(tmp):
    db_path = Path(tmp) / "src.db"
    db.init_db(db_path)

    def _override_get_db():
        conn = db.get_connection(db_path)
        try:
            yield conn
        finally:
            conn.close()

    api.app.dependency_overrides[api.get_db] = _override_get_db
    return TestClient(api.app), db_path


def _login(client, db_path):
    conn = db.get_connection(db_path)
    try:
        db.create_user(conn, "tester", auth.hash_password(_PW))
    finally:
        conn.close()
    assert client.post("/api/auth/login", json={"username": "tester", "password": _PW}).status_code == 200


def test_export_can_be_reimported():
    with tempfile.TemporaryDirectory() as tmp:
        client, src_db = _client(tmp)
        try:
            _login(client, src_db)
            # 種一台硬體 + 綁一筆人員/軟體
            conn = db.get_connection(src_db)
            db.insert_hardware(
                conn, asset_serial="RT-001", hostname="rt-host01", ip="10.9.9.9",
                api_id="core_x", owner="王五", custodian="趙六", environment="正式",
                device_model="Dell R760", os="RHEL 8.9", integrity=3, confidentiality=2, availability=1,
            )
            conn.execute(
                "INSERT INTO personnel (asset_serial, person_name, phone) VALUES ('RT-001','王五','0900')"
            )
            conn.execute(
                "INSERT INTO software (asset_serial, asset_name, db_software) VALUES ('RT-001','核心AP','Oracle')"
            )
            conn.commit()
            conn.close()

            # 匯出
            resp = client.get("/api/export")
            assert resp.status_code == 200
            xlsx = Path(tmp) / "exp.xlsx"
            xlsx.write_bytes(resp.content)

            # 匯進一個全新的 DB
            dst_db = Path(tmp) / "dst.db"
            db.init_db(dst_db)
            conn2 = db.get_connection(dst_db)
            summary = import_excel(xlsx, conn2)

            # 硬體關鍵欄位要一致
            row = conn2.execute("SELECT * FROM hardware WHERE asset_serial='RT-001'").fetchone()
            assert row is not None, f"round-trip 後找不到硬體，summary={summary}"
            assert row["hostname"] == "rt-host01"
            assert row["ip"] == "10.9.9.9"
            assert row["api_id"] == "core_x"
            assert row["owner"] == "王五"
            assert row["custodian"] == "趙六"
            assert row["environment"] == "正式"
            # 人員/軟體也要進得來
            assert conn2.execute("SELECT COUNT(*) FROM personnel WHERE asset_serial='RT-001'").fetchone()[0] == 1
            assert conn2.execute("SELECT COUNT(*) FROM software WHERE asset_serial='RT-001'").fetchone()[0] == 1
            assert summary["sheets"]["硬體"]["inserted"] == 1
            conn2.close()
        finally:
            api.app.dependency_overrides.clear()
