"""主機履歷概要補充（2.060.x，舊 1.x 資產頁下架前補齊的落差）：CIA 登記卡、體檢、EOS 徽章、RVTools 證人、
待 CIA 異動、分類。全部取自舊頁同一支 asset_detail（數字一樣）。全部假資料（IP 10.99.x）。"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import v2_sections as vs  # noqa: E402
from routers import assets as ra  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.insert_hardware(c, asset_serial="A-1", hostname="app01", ip="10.99.3.1", os="Red Hat Enterprise Linux 7",
                       device_model="ProLiant DL380 Gen9", confidentiality="3", integrity="2", availability="4",
                       user_name="王小明", owner="資訊部", custodian="李小華", usage_unit="交易部", owning_company="證券",
                       inventory_division="資訊處", inventory_department="系統部", asset_name="交易主機", group_name="交易群",
                       request_no="REQ-1", remark="測試備註", quantity="1", big_ip_vip="無", asset_status="使用中",
                       pending_status="停用", pending_note="現場確認已關機", pending_by="tester", pending_at="2026-11-10 10:00:00")
    db.insert_hardware(c, asset_serial="A-2", hostname="app02", ip="10.99.3.2", usage_unit="交易部", quantity="3")
    db.insert_hardware(c, asset_serial="DYN-10.99.3.1", hostname="app01", ip="10.99.3.1")
    c.commit()
    yield c
    c.close()


def test_CIA登記卡_欄位與說明取field_meta_數量只在大於1才列_帳外登記不列(conn):
    p = vs.profile(conn, "A-1")
    assert [x["serial"] for x in p["cia"]] == ["A-1"]                    # DYN- 那筆不是 CIA 登記
    f = {x["k"]: x for x in p["cia"][0]["fields"]}
    assert f["confidentiality"]["label"] == "機密性" and f["confidentiality"]["value"] == "3"
    meta = json.loads((ROOT / "APP" / "asset-module" / "backend" / "field_meta.json").read_text(encoding="utf-8"))["fields"]
    assert f["confidentiality"]["help"] == meta["confidentiality"]["help"]      # 0–5 分級說明跟舊頁同一份
    for k in ("user_name", "owner", "custodian", "usage_unit", "owning_company", "inventory_division", "inventory_department",
              "asset_name", "group_name", "request_no", "remark", "big_ip_vip", "integrity", "availability"):
        assert k in f, k
    assert "quantity" not in f                                           # 數量 1 不列
    card = p["cia"][0]
    assert card["asset_status"] == "使用中" and card["pending_status"] == "停用" and card["data_at"]
    assert p["eos"]["guess_src"]["source"].startswith("由資產用途")
    assert f["usage_unit"]["drill"] and not f["remark"]["drill"]
    p2 = vs.profile(conn, "A-2")
    assert {x["k"]: x["value"] for x in p2["cia"][0]["fields"]}["quantity"] == "3"


def test_體檢_EOS_RVTools_分類_跟舊頁asset_detail同一份(conn):
    p = vs.profile(conn, "A-1")
    d = ra.asset_detail("A-1", None, conn)
    assert p["health"] == d["health"]
    assert p["eos"]["os"] == d["os_eos"] and p["eos"]["hw"] == d["hardware_eos"]
    assert p["eos"]["os_guess"] == d["os_guess"] and p["eos"]["model_guess"] == d["model_guess"]
    assert p["eos"]["os_link"].startswith("/v2/eos?tab=os&edk=os&edv=")
    assert p["eos"]["hw_link"].startswith("/v2/eos?tab=hw&edk=model&edv=")
    assert p["rvtools"] == d["rvtools"]
    assert p["classify"]["os_type"] == d["classify"]["os_type"]
    h = p["health"]
    assert {"machine", "data", "issues"} <= set(h) and "check_states" in h


def test_待CIA異動(conn):
    p = vs.profile(conn, "A-1")
    assert p["pending"] == [{"serial": "A-1", "asset_status": "使用中", "pending_status": "停用", "note": "現場確認已關機",
                             "by": "tester", "at": "2026-11-10 10:00:00"}]
    assert vs.profile(conn, "A-2")["pending"] == []


def test_值下鑽_同欄同值_白名單(conn):
    r = vs.same_value(conn, "usage_unit", "交易部")
    assert r["label"] == "使用單位" or r["label"].startswith("使用單位")
    assert {x["serial"] for x in r["items"]} == {"A-1", "A-2"} and r["total"] == 2
    r1 = vs.same_value(conn, "usage_unit", "交易部", limit=1)                 # 畫面只拿 1 筆：total 仍是實際筆數
    assert (r1["shown"], r1["total"], r1["truncated"], r1["max"]) == (1, 2, True, 2000)
    assert vs.same_value(conn, "usage_unit", "交易部", limit=999999)["shown"] == 2   # 上限 2000，不會炸
    with pytest.raises(ValueError):
        vs.same_value(conn, "remark", "測試備註")                          # 自由文字不下鑽
    with pytest.raises(ValueError):
        vs.same_value(conn, "asset_serial; DROP TABLE hardware", "x")


def test_API(tmp_path, conn):
    import api
    from fastapi.testclient import TestClient

    p = tmp_path / "t.db"

    def _get_db():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()
    api.app.dependency_overrides[api.get_db] = _get_db
    api.app.dependency_overrides[api.require_auth] = lambda: {"username": "t", "role": "admin"}
    try:
        cl = TestClient(api.app)
        r = cl.get("/api/v2/machine/profile", params={"key": "A-1"})
        assert r.status_code == 200 and r.json()["cia"][0]["serial"] == "A-1"
        assert cl.get("/api/v2/machine/profile", params={"key": "沒有這台"}).status_code == 404
        r = cl.get("/api/v2/machine/same-value", params={"field": "usage_unit", "value": "交易部"})
        assert r.status_code == 200 and r.json()["total"] == 2
        assert cl.get("/api/v2/machine/same-value", params={"field": "remark", "value": "x"}).status_code == 400
    finally:
        api.app.dependency_overrides.clear()
