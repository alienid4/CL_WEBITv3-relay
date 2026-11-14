"""2.047.3：資料匯入頁「單據（Word）」那一列用的 GET /api/documents/summary——份數與最後匯入時間，不撈整份清單。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import db  # noqa: E402

from test_api import _client  # noqa: E402


def test_單據摘要_份數與最後匯入時間():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            r = client.get("/api/documents/summary")
            assert r.status_code == 200, r.text
            assert r.json()["total"] == 0 and r.json()["last_imported_at"] is None, "沒有單據：0 份、沒有時間（不是空字串）"
            conn = db.get_connection(db_path)
            cols = {c["name"] for c in conn.execute("PRAGMA table_info(doc_archive)").fetchall()}
            base = {"doc_type": "provision_form", "file_name": "a.docx", "bind_confidence": "review"}
            for i, at in enumerate(("2026-11-01 10:00:00", "2026-11-03 09:30:00")):
                row = {**base, "file_name": f"a{i}.docx", "imported_at": at}
                for c in ("file_ext", "file_sha256", "sha256", "text", "file_path", "imported_by"):
                    if c in cols:
                        row[c] = f"x{i}"
                row = {k: v for k, v in row.items() if k in cols}
                conn.execute(f"INSERT INTO doc_archive ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
            conn.commit()
            conn.close()
            s = client.get("/api/documents/summary").json()
            assert s["total"] == 2 and s["last_imported_at"] == "2026-11-03 09:30:00"
            assert s == client.get("/api/documents").json()["summary"], "跟單據檔案室同一份計算"
        finally:
            api.app.dependency_overrides.clear()
