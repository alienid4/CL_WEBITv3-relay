"""納管一台不可以連帶重跑整個機隊。

2026-08-28 使用者實測：畫面已經印出「完成。… 現在可以收集這台。」，
按鈕的計時器卻還在跑到 97 秒、120 秒。慢的不是納管腳本，是收尾那兩行——
它們不帶條件，等於每納管一台就把**所有**已登記資產重新試連、
把**所有**已納管機器重收一次 facts。

成本是 N²：納到第 100 台時那一台要等前面 99 台跑完。而下一步要做的
正是整個網段幾百台的批次納管。

另一半同樣重要：原本收尾包在 `except Exception: pass`——
「跑了 90 秒然後全部失敗」跟「一切正常」在畫面上長得一模一樣。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
import manage_state  # noqa: E402
import onboard_engine  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

TARGET = "10.0.0.50"
OTHERS = ["10.0.0.51", "10.0.0.52", "10.0.0.53"]


def _client(tmp):
    db_path = Path(tmp) / "test.db"
    db.init_db(db_path)

    def _override():
        conn = db.get_connection(db_path)
        try:
            yield conn
        finally:
            conn.close()

    api.app.dependency_overrides[api.get_db] = _override
    return TestClient(api.app), db_path


def _login(client, db_path):
    conn = db.get_connection(db_path)
    try:
        db.create_user(conn, "tester", auth.hash_password("s3cure-pass!"))
        # 一台要納管的 ＋ 三台早就在清單裡的
        db.insert_hardware(conn, asset_serial="TARGET", ip=TARGET)
        for i, ip in enumerate(OTHERS):
            db.insert_hardware(conn, asset_serial=f"OTHER-{i}", ip=ip, collect_ok=1)
        conn.commit()
    finally:
        conn.close()
    r = client.post("/api/auth/login",
                    json={"username": "tester", "password": "s3cure-pass!"})
    assert r.status_code == 200


def _onboard(client, monkeypatch, *, post_raises=False):
    """跑一次納管，回傳收尾兩支函式實際被呼叫時收到的參數。"""
    monkeypatch.setattr(
        onboard_engine, "resolve_collector_ip", lambda conn: "10.0.0.221")
    monkeypatch.setattr(
        onboard_engine, "onboard",
        lambda **kw: onboard_engine.OnboardResult(True, "execute", "納管腳本執行完成", ""))

    seen: dict = {}

    def fake_refresh(conn, **kw):
        seen["refresh_kw"] = kw
        if post_raises:
            raise RuntimeError("試連逾時")
        return {"checked": 1, "ok": 1, "failed": 0}

    def fake_facts(conn, **kw):
        seen["facts_kw"] = kw
        return {"updated": 1}

    monkeypatch.setattr(manage_state, "refresh_collect_status", fake_refresh)
    monkeypatch.setattr(manage_state, "collect_facts_into_assets", fake_facts)

    resp = client.post("/api/onboard", json={
        "ip": TARGET, "platform": "linux", "username": "root", "password": "x"})
    assert resp.status_code == 200, resp.text
    seen["resp"] = resp.json()
    return seen


def test_納管完只試連這一台_不要重跑全機隊(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            seen = _onboard(client, monkeypatch)

            assert seen["refresh_kw"].get("only_ip") == TARGET, (
                "納管完成後對整個機隊重新試連——納到第 N 台就要重跑前 N-1 台，"
                "成本 N²。批次納管幾百台時後面每一台都要等前面全部跑完")
        finally:
            api.app.dependency_overrides.clear()


def test_納管完只收這一台的_facts(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            seen = _onboard(client, monkeypatch)

            assert seen["facts_kw"].get("only_serial") == "TARGET", (
                "納管完成後對所有已納管機器重收 facts。"
                "collect_facts_into_assets 本來就有 only_serial 參數，要傳")
        finally:
            api.app.dependency_overrides.clear()


def test_收尾失敗不可以靜默(monkeypatch):
    """收尾失敗確實不影響「納管本身已完成」，但不能因此讓人看不到。

    原本是 `except Exception: pass`——於是「跑了 90 秒然後全部失敗」
    跟「一切正常」在畫面上長得一模一樣，人只會覺得「怎麼這麼久」。
    """
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            seen = _onboard(client, monkeypatch, post_raises=True)

            body = seen["resp"]
            assert body["ok"] is True, "收尾失敗不該把納管本身判成失敗"
            assert "失敗" in body["message"], (
                f"收尾出錯卻沒有在訊息裡講：{body['message']!r}")
            assert "試連逾時" in body["message"], "沒有帶出實際的錯誤原因，人無從查起"
        finally:
            api.app.dependency_overrides.clear()
