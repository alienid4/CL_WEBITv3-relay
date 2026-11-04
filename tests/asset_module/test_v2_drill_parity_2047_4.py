"""2.047.4（總管：下鑽數字跟筆數要一致）：
  - 異常報告「已由 vCenter 校正 N 台」→ /api/dashboard/vcenter-verified，跟 composition 的 N 同一個條件
  - 「要查看 N 台」「最常卡在 X」→ /api/health/drill，跟 /api/health/summary 同一份體檢
  - ⚙ 資料匯入頁「合約檔」「值班電話名冊」兩列 → /api/contacts/import-status
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import db  # noqa: E402

from test_api import _client, _insert_hardware  # noqa: E402


def _seed(db_path):
    _insert_hardware(db_path, "S-1", "web01", "10.99.1.1", vm_uuid="u-1")
    _insert_hardware(db_path, "S-2", "web02", "10.99.1.2", vm_uuid="  ")      # 空白不算校正
    _insert_hardware(db_path, "S-3", "db01", "10.99.1.3")
    _insert_hardware(db_path, "S-4", "db02", "10.99.1.4", vm_uuid="u-4", asset_status="報廢")


def test_vCenter校正_下鑽筆數等於數字():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _seed(db_path)
            n = client.get("/api/dashboard/composition").json()["data_quality"]["verified_by_vcenter"]
            items = client.get("/api/dashboard/vcenter-verified").json()["items"]
            assert n == len(items) == 2, (n, items)
            assert {x["asset_serial"] for x in items} == {"S-1", "S-4"}
        finally:
            api.app.dependency_overrides.clear()


def test_要查看與體檢項目_下鑽筆數等於數字():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _seed(db_path)
            s = client.get("/api/health/summary").json()
            items = client.get("/api/health/drill").json()["items"]
            assert len(items) == s["needs_review"], (s, items)
            assert s["needs_review"] > 0, "測試資料要有要查看的機器，不然驗不到東西"
            for label, n in s["by_issue"].items():
                got = client.get("/api/health/drill", params={"issue": label}).json()["items"]
                assert len(got) == n, label
                assert all(label in x["issues"] for x in got)
            assert all(x["asset_serial"] != "S-4" for x in items), "退役不體檢"
        finally:
            api.app.dependency_overrides.clear()


def test_資料匯入頁_合約與值班名冊狀態():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            r = client.get("/api/contacts/import-status")
            assert r.status_code == 200, r.text
            assert r.json() == {"contracts": {"rows": 0, "last_imported_at": None, "filename": None},
                                "roster": {"systems": 0, "last_imported_at": None, "filename": None}}
            c = db.get_connection(db_path)
            c.execute("INSERT INTO contract_import (filename, imported_by, imported_at, row_count, matched, unmatched, ambiguous, is_current) "
                      "VALUES ('old.xlsx', 't', '2026-10-01 09:00:00', 5, 5, 0, 0, 0)")
            c.execute("INSERT INTO contract_import (filename, imported_by, imported_at, row_count, matched, unmatched, ambiguous, is_current) "
                      "VALUES ('new.xlsx', 't', '2026-11-01 09:00:00', 7, 6, 1, 0, 1)")
            c.execute("INSERT INTO system_oncall (api_id, phone) VALUES ('N-001', '0200000000')")
            c.execute("INSERT INTO contact_audit (kind, level, key, changed_at) VALUES ('oncall_roster_import', 'import', 'roster.xlsx', '2026-11-02 10:00:00')")
            c.commit()
            c.close()
            s = client.get("/api/contacts/import-status").json()
            assert s["contracts"] == {"rows": 7, "last_imported_at": "2026-11-01 09:00:00", "filename": "new.xlsx"}, "只算目前生效的那份"
            assert s["roster"] == {"systems": 1, "last_imported_at": "2026-11-02 10:00:00", "filename": "roster.xlsx"}
        finally:
            api.app.dependency_overrides.clear()
