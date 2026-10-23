"""重複資產：刪之前一定要留快照，而且看得出是從哪條路進來的。

使用者 2026-09-23 在公司機看到 AIX 每台兩筆，說「你要幫我自動合併」。

**但那個「合併」是真的把 asset_serial 從 hardware／software／personnel 三張表刪掉，
原本還明寫不做快照。** 清冊是稽核對象——「少了一台」跟「合併掉一台」事後看起來
一模一樣，沒有快照就永遠分不出來，也還原不回去。

這組測試守兩件：
  1. **沒有快照就不准刪**——刪掉的整列要存得下來，事後查得到
  2. **每一筆要看得出是從哪條路進來的**——不然只能一直手動合併，
     下個月同樣的東西再長一次
"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        c = db.get_connection(p)
        # 同一台被登記兩次：一筆匯入（有機型）、一筆掃描收編（序號帶 ADOPT-）
        db.insert_hardware(c, asset_serial="A-000123", hostname="sec01",
                           ip="10.99.167.12", asset_status="使用中",
                           device_model="IBM S1024", os="AIX 7.2")
        db.insert_hardware(c, asset_serial="ADOPT-10.99.167.12", hostname="sec01",
                           ip="10.99.167.12", asset_status="使用中", os="AIX 7.2")
        c.commit()
        c.close()

        def _get_db():
            x = db.get_connection(p)
            try:
                yield x
            finally:
                x.close()

        api.app.dependency_overrides[api.get_db] = _get_db
        api.app.dependency_overrides[api.require_auth] = lambda: {"username": "tester"}
        try:
            yield TestClient(api.app, raise_server_exceptions=False), p
        finally:
            api.app.dependency_overrides.pop(api.get_db, None)
            api.app.dependency_overrides.pop(api.require_auth, None)


def _group(cl):
    r = cl.get("/api/assets/duplicates")
    assert r.status_code == 200
    data = r.json()
    groups = data["groups"] if isinstance(data, dict) else data
    assert groups, "沒有抓到重複組"
    return groups[0]


def test_每一筆要看得出是從哪條路進來的(client):
    """只看得到兩筆長得一樣，答不出是哪兩條路撞的，就只能一直手動合併。"""
    cl, _ = client
    rows = _group(cl)["members"]
    by = {r["asset_serial"]: r["origin"] for r in rows}
    assert "掃描收編" in by["ADOPT-10.99.167.12"]["how"]
    assert "匯入或手動" in by["A-000123"]["how"]
    # 四樣都要在：來路、建立時間、是不是匯入來的、有沒有強識別碼
    for o in by.values():
        assert set(o) >= {"how", "created_at", "from_import", "strong_ids"}


def test_沒有強識別碼要看得出來(client):
    """兩筆都沒有強識別碼時，身分解析只能靠主機名＋IP——
    那正是「IP 還沒補齊就各自建了一筆」會發生的情境。看得到才查得下去。"""
    cl, _ = client
    for r in _group(cl)["members"]:
        assert r["origin"]["strong_ids"] == []


def test_刪之前一定要留下整列快照(client):
    cl, p = client
    r = cl.post("/api/assets/duplicates/release",
                json={"hostname": "sec01", "ip": "10.99.167.12",
                      "release_serials": ["ADOPT-10.99.167.12"]})
    assert r.status_code == 200, r.text

    c = db.get_connection(p)
    try:
        rows = c.execute("SELECT * FROM merge_audit").fetchall()
        assert len(rows) == 1, "刪了卻沒有留快照"
        a = dict(rows[0])
        assert a["released_serial"] == "ADOPT-10.99.167.12"
        assert a["kept_serials"] == "A-000123", "沒記下留了哪一筆"
        assert a["actor"] == "tester", "沒記下是誰按的"
        snap = json.loads(a["snapshot"])
        # **整列**要存下來，不是只存序號——還原要靠它
        assert snap["hostname"] == "sec01" and snap["ip"] == "10.99.167.12"
        # 真的被刪掉了（快照不是拿來取代刪除的）
        assert c.execute("SELECT COUNT(*) FROM hardware").fetchone()[0] == 1
    finally:
        c.close()


def test_併過什麼要查得到(client):
    """使用者下週看到台數少了，要分得出「合併掉的」與「掉資料的」。"""
    cl, _ = client
    cl.post("/api/assets/duplicates/release",
            json={"hostname": "sec01", "ip": "10.99.167.12",
                  "release_serials": ["ADOPT-10.99.167.12"]})
    items = cl.get("/api/assets/duplicates/merged").json()["items"]
    assert len(items) == 1
    it = items[0]
    assert it["released_serial"] == "ADOPT-10.99.167.12"
    assert it["hostname"] == "sec01" and it["merged_at"]
