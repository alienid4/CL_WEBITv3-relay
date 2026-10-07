"""已退役不算有問題＋批次標記下線＋CIA 待異動（2026-09-15 使用者：「兩個都要做」）。

使用者：「我有抓到一些設備已下線，我要怎麼變更。」
查證發現兩件事：①納管統計沒排除停用／報廢，改了還是算失聯；②CIA 清冊重匯會把狀態蓋回去。

要守的：
1. 停用／報廢／閒置 → 已退役，不算有問題、漏斗不算要處理
2. 批次改狀態：原因必填、狀態限定、每台記一筆 CIA 待異動；沒變的不記
3. 單台在詳細頁改狀態也要記
4. 標記已同步後預設清單不再列，但資料還在（稽核軌跡）
"""
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import db  # noqa: E402
import manage_state as ms  # noqa: E402
import pipeline  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def env(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    conn = db.get_connection(p)
    for i, st in enumerate(["使用中", "使用中", "停用"]):
        db.insert_hardware(conn, asset_serial=f"A-{i}", hostname=f"h{i}", ip=f"192.0.2.{i + 1}", asset_status=st)
    conn.close()

    def _get_db():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _get_db
    api.app.dependency_overrides[api.require_auth] = lambda: {"username": "tester"}
    try:
        yield TestClient(api.app, raise_server_exceptions=False), p
    finally:
        api.app.dependency_overrides.pop(api.get_db, None)
        api.app.dependency_overrides.pop(api.require_auth, None)


def test_退役優先_不算失聯():
    assert ms.classify(True, seen_in_scan=False, collect_ok=None, retired=True) == ms.RETIRED
    assert ms.RETIRED in ms.ALL_STATES and ms.RETIRED not in ms.NEEDS_ACTION_STATES
    assert ms.NEXT_ACTION[ms.RETIRED]
    assert "retired" in pipeline.STAGE_INDEX and "retired" not in pipeline.TODO_STAGES


def test_統計_停用的不算有問題(env):
    _, p = env
    conn = db.get_connection(p)
    try:
        s = ms.summarize(conn)
    finally:
        conn.close()
    assert s["counts"][ms.RETIRED] == 1
    assert s["retired_total"] == 1
    assert s["needs_action_total"] == 2, "兩台使用中但掃不到＝失聯；停用那台不算"
    assert s["total_known"] == 3


def test_批次改狀態_原因必填_狀態限定(env):
    c, _ = env
    assert c.post("/api/assets/batch-status", json={"serials": ["A-0"], "status": "報廢", "reason": ""}).status_code == 400
    assert c.post("/api/assets/batch-status", json={"serials": ["A-0"], "status": "亂寫", "reason": "x"}).status_code == 400


def test_批次改狀態_記CIA待異動_沒變的不記(env):
    c, _ = env
    r = c.post("/api/assets/batch-status",
               json={"serials": ["A-0", "A-2", "NOPE"], "status": "停用", "reason": "現場確認已下線"})
    assert r.status_code == 200
    body = r.json()
    assert body["updated"] == ["A-0"] and body["unchanged"] == ["A-2"] and body["not_found"] == ["NOPE"]
    items = c.get("/api/cia-pending").json()["items"]
    assert len(items) == 1
    it = items[0]
    assert (it["asset_serial"], it["old_value"], it["new_value"], it["reason"], it["changed_by"]) == \
        ("A-0", "使用中", "停用", "現場確認已下線", "tester")


def test_單台詳細頁改狀態也記(env):
    c, _ = env
    assert c.put("/api/assets/A-1", json={"fields": {"asset_status": "報廢"}}).status_code == 200
    items = c.get("/api/cia-pending").json()["items"]
    assert items and items[0]["asset_serial"] == "A-1" and items[0]["new_value"] == "報廢"
    assert items[0]["reason"] == "資產詳細頁編輯"


def test_標記已同步_預設不列但資料還在_可匯出(env):
    c, _ = env
    c.post("/api/assets/batch-status", json={"serials": ["A-0"], "status": "報廢", "reason": "下線"})
    pid = c.get("/api/cia-pending").json()["items"][0]["id"]
    assert c.post("/api/cia-pending/synced", json={"ids": [pid]}).json()["marked"] == 1
    assert c.get("/api/cia-pending").json()["items"] == []
    all_items = c.get("/api/cia-pending", params={"include_synced": True}).json()["items"]
    assert all_items[0]["synced_at"] and all_items[0]["synced_by"] == "tester"
    x = c.get("/api/cia-pending/export", params={"include_synced": True})
    assert x.status_code == 200 and x.content[:2] == b"PK"
