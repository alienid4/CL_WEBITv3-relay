"""W221-013：基線失效頁空狀態三態（無基線／尚未回檢／已回檢 N 台）。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import api  # noqa: E402

from test_api import _client  # noqa: E402
from test_golive import _golive_ok  # noqa: E402


def test_無已上線基線():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        try:
            s = client.get("/api/drift/summary").json()
            assert s["state"] == "no_baseline"
            assert s["baseline_count"] == 0
            assert s["last_run"] is None
        finally:
            api.app.dependency_overrides.clear()


def test_有基線但尚未回檢_與_回檢後():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _golive_ok(client, db_path, "DS-001", "10.5.1.1")
            s = client.get("/api/drift/summary").json()
            assert s["state"] == "never_checked"
            assert s["baseline_count"] > 0
            assert s["last_run"] is None

            r = client.post("/api/drift/recheck").json()
            assert "unknown_count" in r
            s = client.get("/api/drift/summary").json()
            assert s["state"] == "checked"
            assert s["checked_assets"] == 1
            assert s["last_run"]
            assert s["unknown_count"] == r["unknown_count"]
        finally:
            api.app.dependency_overrides.clear()
