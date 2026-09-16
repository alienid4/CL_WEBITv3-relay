"""SAN 原文存檔＋資產詳細頁（2026-09-15）。

使用者：「離線匯入時的紀錄，你通通都要記錄，哪一天用得到，不知道。」
「我都收集了……SAN SW 產出的資料也要放進來。就像履歷表一樣。」

要守的：
1. 貼上的整段原文**每次都存、只增不改**，連解析失敗的也存
2. 下載回來跟當初貼的一字不差
3. 線上收集成功也存一份
4. 儲存設備的詳細頁不能再給「一鍵納管」
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import db  # noqa: E402
import san_collector  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def env(tmp_path):
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
        yield TestClient(api.app, raise_server_exceptions=False), p
    finally:
        api.app.dependency_overrides.pop(api.get_db, None)
        api.app.dependency_overrides.pop(api.require_auth, None)


GOOD = ("B24_left:admin> switchname\nB24_left\n"
        "B24_left:admin> switchshow\nswitchName: B24_left\n"
        "這一行是沒認到的雜訊，也要原樣留著\n")


def test_離線匯入每次都存原文_只增不改_下載一字不差(env):
    c, _ = env
    for _ in range(2):
        assert c.post("/api/san/import", json={"ip": "192.0.2.157", "transcript": GOOD}).status_code == 200
    items = c.get("/api/san/switches/192.0.2.157/archive").json()["items"]
    assert len(items) == 2, "兩次匯入要留兩份，不能覆蓋"
    assert items[0]["kind"] == "offline_import" and items[0]["ok"] == 1
    assert "switchshow" in items[0]["recognized"] and "fabricshow" in items[0]["missing"]
    t = c.get(f"/api/san/archive/{items[0]['id']}/text")
    assert t.status_code == 200 and t.text == GOOD


def test_解析失敗的原文也要存(env):
    c, _ = env
    bad = "隨便貼的東西\n沒有任何指令回音行\n"
    r = c.post("/api/san/import", json={"ip": "192.0.2.158", "transcript": bad})
    assert r.status_code == 400 and "原文已存檔" in r.json()["detail"]
    items = c.get("/api/san/switches/192.0.2.158/archive").json()["items"]
    assert len(items) == 1 and items[0]["ok"] == 0
    assert c.get(f"/api/san/archive/{items[0]['id']}/text").text == bad


def test_線上收集成功也存一份(env, monkeypatch):
    c, _ = env
    monkeypatch.setattr(san_collector, "collect", lambda ip, u, p, timeout=40: san_collector._parse_outputs(
        {"switchshow": "switchName: sw1\n", "version": "Fabric OS: v9.1\n"}))
    r = c.post("/api/san/collect", json={"ip": "192.0.2.160", "username": "u", "password": "FAKE-PW"})
    assert r.status_code == 200, r.text
    items = c.get("/api/san/switches/192.0.2.160/archive").json()["items"]
    assert items and items[0]["kind"] == "online_collect"
    text = c.get(f"/api/san/archive/{items[0]['id']}/text").text
    assert "Fabric OS: v9.1" in text and "FAKE-PW" not in text


def test_查無存檔回404(env):
    c, _ = env
    assert c.get("/api/san/archive/99999/text").status_code == 404


def test_儲存設備詳細頁不給一鍵納管(env):
    c, p = env
    conn = db.get_connection(p)
    db.insert_hardware(conn, asset_serial="SAN-1", ip="192.0.2.157", os="儲存設備",
                       asset_name="IBM San Switch(FS5300)")
    db.insert_hardware(conn, asset_serial="LNX-1", ip="192.0.2.20", os="Rocky Linux 9")
    conn.close()
    assert c.get("/api/assets/SAN-1").json()["hardware"]["onboard_block"]["kind"] == "appliance"
    assert c.get("/api/assets/LNX-1").json()["hardware"]["onboard_block"] is None
