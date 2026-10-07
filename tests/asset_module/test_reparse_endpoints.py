"""重判讀兩個端點（2026-10-07，搬遷重建第一批）。

這兩支刻意跟 `/collect` 分開：**一個會連出去、一個不會**。
合成一個端點的話，使用者按下去之前分不出這次會不會碰到正式機。
所以測的重點之一就是「它真的不需要任何連線也能跑完」。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

MDSTAT = "md0 : active raid1 sdb1[1] sda1[0]\n      976630464 blocks super 1.2 [2/2] [UU]\n"
MULTIPATH = (
    "mpatha (3600508b4000156d700012000000b0000) dm-3 HP,HSV200\n"
    "size=100G features='0' hwhandler='0' wp=rw\n"
    "`-+- policy='round-robin 0' prio=1 status=active\n"
    "  `- 1:0:0:7 sdb 8:16 active ready running\n"
)
ZONESHOW = (
    "Defined configuration:\n"
    " cfg:\tPROD_CFG\tZ_A\n"
    " cfg:\tSPARE_CFG\tZ_B\n"
    " zone:\tZ_A\n\t\t10:00:00:00:c9:aa:bb:01\n"
    " zone:\tZ_B\n\t\t10:00:00:00:c9:aa:bb:02\n"
    "\nEffective configuration:\n cfg:\tPROD_CFG\n"
)


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


def test_host_spec_reparse_端點從既有原文挖出明細(env):
    c, p = env
    conn = db.get_connection(p)
    conn.execute(
        "INSERT INTO host_spec (asset_serial, ip, platform, spec_json, raw_json, collected_at) "
        "VALUES (?,?,?,?,?,?)",
        ("S1", "192.0.2.10", "linux", json.dumps({"raid_hw": True}),
         json.dumps({"mdstat": MDSTAT, "multipath": MULTIPATH}), "2026-09-01 10:00:00"))
    conn.commit()
    conn.close()

    r = c.post("/api/host-spec/reparse")
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 1

    conn = db.get_connection(p)
    try:
        row = conn.execute("SELECT spec_json, collected_at FROM host_spec WHERE asset_serial='S1'").fetchone()
    finally:
        conn.close()
    sj = json.loads(row["spec_json"])
    assert sj["multipath_detail"][0]["wwid"] == "3600508b4000156d700012000000b0000"
    assert sj["multipath_detail"][0]["paths"][0]["lun"] == "7"
    assert sj["raid_sw_detail"][0]["members"][0]["dev"] == "sdb1"
    # 重判讀沒有讓資料變新
    assert row["collected_at"] == "2026-09-01 10:00:00"


def test_host_spec_reparse_可以只做一台(env):
    c, p = env
    conn = db.get_connection(p)
    for s in ("S1", "S2"):
        conn.execute(
            "INSERT INTO host_spec (asset_serial, ip, platform, raw_json, collected_at) "
            "VALUES (?,?,?,?,?)",
            (s, "192.0.2.10", "linux", json.dumps({"mdstat": MDSTAT}), "2026-09-01 10:00:00"))
    conn.commit()
    conn.close()
    body = c.post("/api/host-spec/reparse", params={"asset_serial": "S1"}).json()
    assert body["total"] == 1 and body["updated"] == 1


def test_host_spec_reparse_沒有任何資料時不報錯_回零(env):
    c, _ = env
    body = c.post("/api/host-spec/reparse").json()
    assert body["total"] == 0 and body["updated"] == 0


def test_san_reparse_端點挖出定義過的全部_zone(env):
    c, p = env
    conn = db.get_connection(p)
    conn.execute(
        "INSERT INTO san_switch (ip, data_json, raw_json, collected_at) VALUES (?,?,?,?)",
        ("192.0.2.158", json.dumps({"rows": []}),
         json.dumps({"zoneshow": ZONESHOW}), "2026-09-13 10:00:00"))
    conn.commit()
    conn.close()

    r = c.post("/api/san/reparse")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["updated"] == 1
    u = body["updated_detail"][0]
    # cfgactvshow 只會給啟用中那份；這裡要看到兩個 cfg、兩個 zone
    assert u["configs"] == 2 and u["defined_zones"] == 2

    det = c.get("/api/san/switches/192.0.2.158").json()["data"]["details"]
    assert det["zoning_all"]["active_cfg"] == "PROD_CFG"
    assert "SPARE_CFG" in det["zoning_all"]["configs"]


def test_san_reparse_沒有原文時講原因_不算失敗(env):
    c, p = env
    conn = db.get_connection(p)
    conn.execute("INSERT INTO san_switch (ip, data_json, raw_json) VALUES (?,?,NULL)",
                 ("192.0.2.159", json.dumps({"rows": []})))
    conn.commit()
    conn.close()
    body = c.post("/api/san/reparse").json()
    assert body["no_raw"] == 1 and body["failed"] == 0 and body["updated"] == 0
