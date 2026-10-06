"""SAN 離線匯入的標準指令清單（2026-09-15 使用者：「你要放在離線版，讓他去 COPY 出來的格式才會統一」）。

要守的：
1. 清單就是使用者給的 14 個指令、照這個順序（畫面一鍵複製的就是這份）
2. 貼回來的畫面每個指令都認得到（含 version、sfpshow -all）
3. 匯入結果要講出缺哪幾個指令，不能只說「成功」
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

USER_LIST = ["switchname", "version", "switchshow", "fabricshow", "nscamshow", "nsshow",
             "cfgactvshow", "zoneshow", "alishow", "chassisshow", "firmwareshow", "islshow",
             "porterrshow", "sfpshow -all"]


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


def test_清單就是使用者給的那14個_順序一樣():
    assert san_collector.READONLY_COMMANDS == USER_LIST
    g = san_collector.offline_guide()
    assert g["commands"] == USER_LIST
    assert set(g["min_required"]) <= {c.split()[0] for c in USER_LIST}
    assert g["steps"], "要附步驟，不然每個人貼回來的格式不一樣"


def test_每個指令貼回來都認得到():
    text = "\n".join(f"B24_left:admin> {c}\n（{c} 的輸出）" for c in USER_LIST)
    outs = san_collector.split_transcript(text)
    assert set(outs) == {c.split()[0] for c in USER_LIST}


def test_API_清單端點(client):
    r = client.get("/api/san/offline-guide")
    assert r.status_code == 200 and r.json()["commands"] == USER_LIST


def test_匯入結果講出缺哪幾個指令(client):
    text = ("B24_left:admin> version\nFabric OS: v6.4.0c\n"
            "B24_left:admin> switchshow\nswitchName: B24_left\n")
    r = client.post("/api/san/import", json={"ip": "192.0.2.223", "transcript": text})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "version" in body["imported_cmds"]
    assert "fabricshow" in body["missing_cmds"] and "switchshow" not in body["missing_cmds"]
    items = client.get("/api/system/collect-log", params={"kind": "san_import"}).json()["items"]
    assert items and items[0]["kind"] == "san_import"
