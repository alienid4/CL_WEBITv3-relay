"""SAN 收集結果同步進資產（身家調查表，2026-09-15）。

使用者：「.158 的 PuTTY 紀錄，你要紀錄到資產查詢——應該說身家調查表。」

要守的：
1. switch 回報的機箱序號寫進資產的「設備序號」——**只補空的**
2. 資產上已經有不同的序號時**不覆蓋**，回報衝突給人判斷
3. 解析結果帶 details（韌體／序號／port／光模組），匯入 API 回傳看得到
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

TRANSCRIPT = """\
TESTSW01:admin> version
Fabric OS:  v9.1.1b
TESTSW01:admin> switchshow
switchName:\tTESTSW01
switchState:\tOnline
Index Port Address  Media Speed   State       Proto
==================================================
   0   0   010000   id    N32\t  Online      FC  F-Port  20:00:00:00:00:00:aa:01
TESTSW01:admin> fabricshow
Switch ID   Worldwide Name          Enet IP Addr    FC IP Addr      Name
  1: fffc01 10:00:00:00:00:00:00:01 192.0.2.158  0.0.0.0        >"TESTSW01"
TESTSW01:admin> chassisshow
CHASSIS/WWN  Unit: 1
Part Num:     \t\t0000000TEST
Serial Num:   \t\tTESTSN01
"""


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


def _hw_serial(p, serial):
    c = db.get_connection(p)
    try:
        return c.execute("SELECT hw_serial FROM hardware WHERE asset_serial=?", (serial,)).fetchone()[0]
    finally:
        c.close()


def test_設備序號空的就補上_匯入結果帶details(env):
    c, p = env
    conn = db.get_connection(p)
    db.insert_hardware(conn, asset_serial="SAN-158", ip="192.0.2.158", os="儲存設備")
    conn.close()
    r = c.post("/api/san/import", json={"ip": "192.0.2.158", "transcript": TRANSCRIPT})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["asset_serial_filled"] == ["SAN-158"] and body["asset_serial_conflicts"] == []
    assert _hw_serial(p, "SAN-158") == "TESTSN01"
    det = c.get("/api/san/switches/192.0.2.158").json()["data"]["details"]
    assert det["chassis"]["serial"] == "TESTSN01" and det["version"]["fos"] == "v9.1.1b"
    assert det["mgmt_ip"] == "192.0.2.158"


def test_資產已有不同序號不覆蓋_回報衝突(env):
    c, p = env
    conn = db.get_connection(p)
    db.insert_hardware(conn, asset_serial="SAN-158", ip="192.0.2.158", os="儲存設備", hw_serial="HUMAN-SN")
    conn.close()
    body = c.post("/api/san/import", json={"ip": "192.0.2.158", "transcript": TRANSCRIPT}).json()
    assert body["asset_serial_filled"] == []
    assert body["asset_serial_conflicts"][0] == {"asset_serial": "SAN-158", "asset_has": "HUMAN-SN",
                                                "switch_says": "TESTSN01"}
    assert _hw_serial(p, "SAN-158") == "HUMAN-SN", "人填的序號不能被蓋掉"
