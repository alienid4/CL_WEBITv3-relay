"""收集紀錄／分析檔（2026-09-15 使用者：「修復至少 5 次，做個收集 LOG 或分析的」）。

要守的：
1. 失敗當下的環境（paramiko 載自哪、traceback）要存得下來、讀得回來
2. **密碼絕不進紀錄**（分析檔會被整份轉寄）
3. 寫紀錄失敗不可以讓收集失敗
4. 只留最近 N 筆，不無限長大
"""
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import collect_log  # noqa: E402
import db  # noqa: E402
import onboard_engine  # noqa: E402
import san_collector  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

FAKE_PW = "FAKE-PW-must-not-appear-in-log"


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    yield c
    c.close()


@pytest.fixture()
def client(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)

    def _get_db():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _get_db
    api.app.dependency_overrides[api.require_auth] = lambda: {"username": "tester"}
    try:
        yield TestClient(api.app, raise_server_exceptions=False)
    finally:
        api.app.dependency_overrides.pop(api.get_db, None)
        api.app.dependency_overrides.pop(api.require_auth, None)


def test_紀錄帶得出環境與traceback(conn):
    try:
        raise AttributeError("module 'paramiko' has no attribute 'SSHClient'")
    except AttributeError as exc:
        collect_log.record(conn, "san_collect", "192.0.2.157", False, str(exc), actor="t", exc=exc)
    r = collect_log.recent(conn)[0]
    assert r["ok"] == 0 and r["kind_label"] == "SAN 收集"
    d = r["detail"]
    for key in ("pid", "python", "cwd", "sys_path", "paramiko", "traceback"):
        assert key in d, f"缺 {key}——少了這段就又得猜"
    assert "SSHClient" in d["traceback"]


def test_只留最近N筆(conn, monkeypatch):
    monkeypatch.setattr(collect_log, "MAX_ROWS", 3)
    for i in range(5):
        collect_log.record(conn, "selfheal", None, True, f"第 {i} 次")
    rows = collect_log.recent(conn, 10)
    assert [r["message"] for r in rows] == ["第 4 次", "第 3 次", "第 2 次"]


def test_寫紀錄失敗不影響呼叫端(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    c.close()
    collect_log.record(c, "san_collect", "192.0.2.1", False, "x")   # 關掉的連線也不能丟例外


def test_SAN收集失敗會留紀錄_而且密碼不在裡面(client, monkeypatch):
    def boom(ip, username, password, timeout=40):
        raise RuntimeError("SSH 連線失敗：module 'paramiko' has no attribute 'SSHClient'")

    monkeypatch.setattr(san_collector, "collect", boom)
    r = client.post("/api/san/collect",
                    json={"ip": "192.0.2.157", "username": "admin", "password": FAKE_PW})
    assert r.status_code == 502
    items = client.get("/api/system/collect-log").json()["items"]
    assert items and items[0]["kind"] == "san_collect" and items[0]["ok"] == 0
    assert "SSHClient" in items[0]["message"]
    assert FAKE_PW not in str(items), "密碼絕不能進收集紀錄"
    text = client.get("/api/system/collect-log/export")
    assert text.status_code == 200 and "收集分析檔" in text.text
    assert FAKE_PW not in text.text


def test_連線失敗會留下原始traceback(monkeypatch):
    def fail(host, username, password, timeout):
        raise AttributeError("module 'paramiko' has no attribute 'SSHClient'")

    monkeypatch.setattr(onboard_engine, "_ssh_connect", fail)
    rc, _ = onboard_engine._ssh_exec("192.0.2.200", "u", FAKE_PW, "switchshow")
    assert rc == onboard_engine.SSH_CONNECT_FAILED
    tb = onboard_engine.last_connect_trace("192.0.2.200")
    assert tb and "AttributeError" in tb and FAKE_PW not in tb


def test_開始收集會留紀錄_失敗也留(client, monkeypatch):
    import collect_dispatch

    # 2026-09-16 起 summarize 多回 empty_addresses（完全沒回應又沒登記＝空位址，不算待辦）。
    # 這裡是假資料，要跟著補上，否則訊息會把 254 個空位址說成「已登記但只能匯入」。
    monkeypatch.setattr(collect_dispatch, "run_dispatch", lambda conn, targets, triggered_by=None: {
        "run_id": 1, "total": 254, "collected": 0, "needs_action": 0, "not_onboardable": 0,
        "empty_addresses": 254,
        "by_status": {"import_only": 254}, "by_route": {"import": 254}, "results": []})
    assert client.post("/api/collect/dispatch", json={"targets": "192.0.2.0/24"}).status_code == 200

    def boom(conn, targets, triggered_by=None):
        raise RuntimeError("探測器壞了")

    monkeypatch.setattr(collect_dispatch, "run_dispatch", boom)
    assert client.post("/api/collect/dispatch", json={"targets": "192.0.2.0/24"}).status_code == 500

    items = client.get("/api/system/collect-log", params={"kind": "dispatch"}).json()["items"]
    assert [i["ok"] for i in items] == [0, 1], "失敗那筆在前、成功那筆在後"
    assert "探測器壞了" in items[0]["message"]
    assert "空位址" in items[1]["message"] and "254" in items[1]["message"]


def test_紀錄可以依動作種類篩(conn):
    collect_log.record(conn, "san_collect", "192.0.2.1", True, "a")
    collect_log.record(conn, "dispatch", "192.0.2.0/24", True, "b")
    assert [r["kind"] for r in collect_log.recent(conn, kinds=["dispatch"])] == ["dispatch"]
    assert len(collect_log.recent(conn)) == 2
