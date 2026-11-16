"""第 17 批獨立審查修正（2.053.1）：5.1 找設備的全部機房搜尋數字＝清單（截斷要講）、組合搜尋找不到的詞、
超過一年未盤的 365 天邊界。全部假資料。"""
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import rack_inventory as ri  # noqa: E402
import rack_layout as rl  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _seed(conn, n=5):
    for i in range(n):
        db.insert_hardware(conn, asset_serial=f"S-{i}", hostname=f"fakeapp{i:02d}", ip=f"10.99.6.{i + 1}",
                           physical_location="01_甲機房", rack_no=f"B1/{i + 1}U", device_model="DELL R750")
    conn.commit()


def test_數字是全部筆數_清單截斷時有旗標_limit放大就拿到全部(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        r = rl.search(conn, "R750", limit=3)
        h = r["angles"]["hardware"]
        assert r["counts"]["hardware"] == h["total"] == 5
        assert len(h["matches"]) == 3 and h["truncated"] is True and h["limit"] == 3
        full = rl.search(conn, "R750", limit=20000)["angles"]["hardware"]
        assert len(full["matches"]) == full["total"] == 5 and full["truncated"] is False
        assert rl.search(conn, "x")["angles"]["hardware"]["truncated"] is False        # 太短：空結果也有旗標
    finally:
        conn.close()


def test_API_limit參數_預設300_有上限(tmp_path):
    import api
    import auth
    from fastapi.testclient import TestClient
    p = tmp_path / "t.db"
    db.init_db(p)

    def _get_db():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()
    api.app.dependency_overrides[api.get_db] = _get_db
    try:
        c = db.get_connection(p)
        _seed(c)
        db.create_user(c, "tester", auth.hash_password("test-password-123"))
        c.close()
        cl = TestClient(api.app)
        assert cl.post("/api/auth/login", json={"username": "tester", "password": "test-password-123"}).status_code == 200
        h = cl.get("/api/racks/search", params={"q": "R750", "limit": 2}).json()["angles"]["hardware"]
        assert h["total"] == 5 and len(h["matches"]) == 2 and h["truncated"] is True
        assert cl.get("/api/racks/search", params={"q": "R750"}).json()["angles"]["hardware"]["limit"] == 300
        assert cl.get("/api/racks/search", params={"q": "R750", "limit": 10 ** 9}).json()["angles"]["hardware"]["limit"] == 20000
        low = cl.get("/api/racks/search", params={"q": "R750", "limit": 0}).json()["angles"]["hardware"]         # 下限：0／負數＝1（2.054.1）
        assert low["limit"] == 1 and len(low["matches"]) == 1 and low["total"] == 5 and low["truncated"] is True
        assert cl.get("/api/racks/search", params={"q": "R750", "limit": -5}).json()["angles"]["hardware"]["limit"] == 1
    finally:
        api.app.dependency_overrides.clear()


def test_組合搜尋_標組合_找不到的詞列出來(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        r = rl.search(conn, "R750 qqzzxx")
        assert r.get("mode") == "multi" and r["angles"]["hardware"]["label"] == "組合"
        assert r["multi"]["unknown"] == ["qqzzxx"] and r["counts"]["hardware"] == 5
    finally:
        conn.close()


def test_超過一年未盤_365天邊界():
    """cut＝今天－365 天；上次盤點 < cut 才算未盤：364、365 天前不算，366 天前算；沒盤過一定算。"""
    today = date.today()
    last = {k: (today - timedelta(days=d)).isoformat() for k, d in (("d364", 364), ("d365", 365), ("d366", 366))}
    got = ri.stale_keys(None, ["d364", "d365", "d366", "never"], last)
    assert got == ["d366", "never"], got
    assert ri.STALE_DAYS == 365


def test_畫面_截斷提示_匯出全部_組合標籤與找不到():
    d = (FE / "pages/v2/datacenter.vue").read_text(encoding="utf-8")
    assert "xsCur?.truncated" in d and ":export-all=\"xsCur?.truncated ? xsExportAll : null\"" in d
    assert "limit: 20000" in d
    assert "{{ xsLabel(a) }}" in d and "xsUnknown" in d and "找不到：" in d
    assert "auditDrillKeys(r, kind, rack)" in d and "staleCountOf(room.value, t.rack)" in d
