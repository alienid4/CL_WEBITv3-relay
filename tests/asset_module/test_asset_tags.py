"""機器標籤（2026-09-21 使用者：「我覺得用 tag，而且可以變更」）。

情境：一台在 CIA 上有 5 筆登記、用途都是「…DB」、橫跨 3 個 AP ID，
使用者問「看得出來為什麼放一起嗎? 共用 DB?」——系統沒地方記這件事。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import asset_tags as at  # noqa: E402
import db  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    # 同一台（主機名＋IP 相同）五筆登記，用途都是 DB，橫跨三個 AP ID
    for sn, name, purpose, api in (
            ("HW-1", "示範期貨中台", "期貨中台 DB", "N-038"),
            ("HW-2", "CTI", "客服話務系統 DB", "N-044"),
            ("HW-3", "經紀週邊支援系統", "經紀週邊支援DB", "N-035"),
            ("HW-4", "自製郵件發送系統", "CMail DB", "N-035"),
            ("HW-5", "證券與期權公開資料查詢服務", "證券與期權公開資料查詢服務DB", "N-035")):
        db.insert_hardware(c, asset_serial=sn, hostname="dbsvr1", ip="192.0.2.31",
                           asset_name=name, asset_purpose=purpose, api_id=api, asset_status="使用中")
    # 另一台：兩個系統共用，但不是 DB
    db.insert_hardware(c, asset_serial="HW-9", hostname="ap1", ip="192.0.2.40",
                       asset_name="A系統", asset_purpose="AP 主機", api_id="N-001", asset_status="使用中")
    db.insert_hardware(c, asset_serial="HW-10", hostname="ap1", ip="192.0.2.40",
                       asset_name="B系統", asset_purpose="AP 主機", api_id="N-002", asset_status="使用中")
    c.commit()
    return c


def test_標籤掛在台上_同一台任一筆序號都看得到(tmp_path):
    c = _conn(tmp_path)
    at.add_tag(c, "HW-1", at.SHARED_DB, note="三個系統共用這台 DB", by="tester")
    for sn in ("HW-1", "HW-3", "HW-5"):
        tags = [t["tag"] for t in at.list_tags(c, sn)]
        assert tags == [at.SHARED_DB], f"{sn} 應該看得到同一台的標籤"
    assert at.list_tags(c, "HW-9") == [], "別台不受影響"


def test_可以改_可以刪(tmp_path):
    c = _conn(tmp_path)
    at.add_tag(c, "HW-1", "待確認", note="先標著", by="a")
    at.add_tag(c, "HW-1", "待確認", note="問過 DBA 了，確實共用", by="b")   # 同名＝更新
    t = at.list_tags(c, "HW-1")
    assert len(t) == 1 and t[0]["note"].endswith("確實共用") and t[0]["created_by"] == "b"
    assert at.remove_tag(c, "HW-3", "待確認")["removed"] == 1, "同一台任一筆序號都能刪"
    assert at.list_tags(c, "HW-1") == []


def test_每個標籤都記得誰貼的與為什麼(tmp_path):
    c = _conn(tmp_path)
    at.add_tag(c, "HW-1", at.SHARED_DB, note="DBA 確認同一個實例", by="tester")
    t = at.list_tags(c, "HW-1")[0]
    assert t["note"] and t["created_by"] == "tester" and t["created_at"]
    assert t["source"] == "manual"


def test_建議只是建議_一定附依據而且不會自己存(tmp_path):
    c = _conn(tmp_path)
    s = at.suggest(c, "HW-1")
    assert [x["tag"] for x in s] == [at.SHARED_DB]
    assert "5 筆" in s[0]["why"] and "N-035" in s[0]["why"]
    assert s[0]["confidence"] == "推論" and s[0]["next"], "推論要標明，並講下一步怎麼變成證據"
    assert at.list_tags(c, "HW-1") == [], "建議不可以自己寫進資料"
    # 採用之後就不再建議
    at.add_tag(c, "HW-1", at.SHARED_DB, by="t", source="suggested")
    assert at.suggest(c, "HW-1") == []


def test_不是DB的共用主機建議另一個標籤(tmp_path):
    c = _conn(tmp_path)
    s = at.suggest(c, "HW-9")
    assert [x["tag"] for x in s] == [at.SHARED_AP]
    assert "N-001" in s[0]["why"] and "N-002" in s[0]["why"]


def test_單筆登記不建議_空標籤擋下來(tmp_path):
    c = _conn(tmp_path)
    db.insert_hardware(c, asset_serial="HW-20", hostname="solo", ip="192.0.2.50",
                       asset_purpose="單機 DB", asset_status="使用中")
    c.commit()
    assert at.suggest(c, "HW-20") == [], "只有一筆登記沒有「為什麼放一起」的問題"
    with pytest.raises(ValueError):
        at.add_tag(c, "HW-20", "   ")
    with pytest.raises(ValueError):
        at.add_tag(c, "查無此序號", "x")


def test_全站標籤清單給篩選用(tmp_path):
    c = _conn(tmp_path)
    at.add_tag(c, "HW-1", at.SHARED_DB, by="t")
    at.add_tag(c, "HW-9", at.SHARED_DB, by="t")
    at.add_tag(c, "HW-9", "測試機", by="t")
    assert at.all_tags(c) == [{"tag": at.SHARED_DB, "machines": 2}, {"tag": "測試機", "machines": 1}]


def test_端點_貼刪與建議都走得通(tmp_path):
    """走 HTTP——這才是畫面實際走的那條路（前一天 test_version_file 才因為直接呼叫而測假的）。"""
    import api
    from fastapi.testclient import TestClient

    c = _conn(tmp_path)
    # ⚠️ 不可以先 monkeypatch api.get_db：路由綁的是**原本那個函式物件**，
    # 換掉之後 dependency_overrides 的鍵就對不上，等於沒覆寫（會打到正式庫）。
    api.app.dependency_overrides[api.get_db] = lambda: c
    api.app.dependency_overrides[api.require_auth] = lambda: {"username": "tester"}
    try:
        with TestClient(api.app) as cl:
            r = cl.get("/api/assets/HW-1/tags").json()
            assert r["registrations"] == 5
            assert [s["tag"] for s in r["suggestions"]] == [at.SHARED_DB]
            assert cl.post("/api/assets/HW-1/tags",
                           json={"tag": at.SHARED_DB, "note": "DBA 確認同一實例", "source": "suggested"}
                           ).status_code == 200
            r2 = cl.get("/api/assets/HW-5/tags").json()       # 同一台的另一筆序號
            assert [x["tag"] for x in r2["tags"]] == [at.SHARED_DB]
            assert r2["suggestions"] == [], "採用後不再建議"
            assert cl.get("/api/asset-tags").json()["items"] == [{"tag": at.SHARED_DB, "machines": 1}]
            assert cl.delete(f"/api/assets/HW-3/tags/{at.SHARED_DB}").json()["removed"] == 1
            assert cl.get("/api/assets/HW-1/tags").json()["tags"] == []
            assert cl.get("/api/assets/查無/tags").status_code == 404
            assert cl.post("/api/assets/HW-1/tags", json={"tag": "  "}).status_code == 400
    finally:
        api.app.dependency_overrides.clear()
