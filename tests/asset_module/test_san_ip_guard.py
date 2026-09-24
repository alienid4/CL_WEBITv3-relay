"""SAN 離線匯入 IP 防呆（2026-09-15 使用者：「IP 防呆要」）。

背景：.157 那次匯入的 WWPN 跟 .158 一模一樣，懷疑貼錯台——A 台的畫面匯進 B 台，
資產身家調查表就整份錯了，而且看不出來。

要守的：
1. switch 自己回報的管理 IP 跟填的不同 → 409 擋下、**不寫入**，但原文照樣存檔
2. 使用者確認後帶 force 才匯入，紀錄上標明是強制
3. 有串接的 fabric 會列多台 switch，要認「自己這台」（> 記號），不能取第一列
4. 沒有 fabricshow（無法驗證）照常匯入，但回報 unverified
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import db  # noqa: E402
import san_parse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def transcript(reported_ip: str | None) -> str:
    t = ("SW158:admin> switchshow\nswitchName:\tSW158\n"
         "Index Port Address  Media Speed   State       Proto\n"
         "==================================================\n"
         "   0   0   010000   id    N32\t  Online      FC  F-Port  20:00:00:00:00:00:aa:01\n")
    if reported_ip:
        t += ("SW158:admin> fabricshow\n"
              "Switch ID   Worldwide Name          Enet IP Addr    FC IP Addr      Name\n"
              f"  1: fffc01 10:00:00:00:00:00:00:01 {reported_ip}  0.0.0.0        >\"SW158\"\n")
    return t


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


def test_IP一致照常匯入(client):
    r = client.post("/api/san/import", json={"ip": "192.0.2.158", "transcript": transcript("192.0.2.158")})
    assert r.status_code == 200 and r.json()["ip_check"] == "match"


def test_IP不一致擋下_不寫入_但原文有存(client):
    r = client.post("/api/san/import", json={"ip": "192.0.2.157", "transcript": transcript("192.0.2.158")})
    assert r.status_code == 409
    assert "192.0.2.158" in r.json()["detail"] and "192.0.2.157" in r.json()["detail"]
    assert client.get("/api/san/switches/192.0.2.157").status_code == 404, "被擋下的不能寫進去"
    items = client.get("/api/san/switches/192.0.2.157/archive").json()["items"]
    assert len(items) == 1, "被擋下的原文也要存"


def test_確認後強制匯入_紀錄標明強制(client):
    r = client.post("/api/san/import", json={"ip": "192.0.2.157", "transcript": transcript("192.0.2.158"),
                                             "force": True})
    assert r.status_code == 200 and r.json()["ip_check"] == "mismatch_forced"
    log = client.get("/api/system/collect-log", params={"kind": "san_import"}).json()["items"][0]
    assert "強制" in log["message"]


def test_沒有fabricshow無法驗證_照常匯入但講清楚(client):
    r = client.post("/api/san/import", json={"ip": "192.0.2.158", "transcript": transcript(None)})
    assert r.status_code == 200 and r.json()["ip_check"] == "unverified"


def test_串接的fabric要認自己這台不是第一列():
    fab = ("Switch ID   Worldwide Name          Enet IP Addr    FC IP Addr      Name\n"
           "  1: fffc01 10:00:00:00:00:00:00:01 192.0.2.10   0.0.0.0        \"OTHER\"\n"
           "  2: fffc02 10:00:00:00:00:00:00:02 192.0.2.20   0.0.0.0        >\"ME\"\n")
    assert san_parse.local_mgmt_ip(fab) == "192.0.2.20"
    no_mark = fab.replace('>"ME"', '"ME"')
    assert san_parse.local_mgmt_ip(no_mark, "ME") == "192.0.2.20"
    assert san_parse.local_mgmt_ip(no_mark) is None, "多台又沒記號、沒名字可對 → 不猜"
