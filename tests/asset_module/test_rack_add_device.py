"""機櫃畫面「在這個機櫃新增設備」：POST /api/racks/add-device（2.007.17）。
管理員限定；U 位重疊要確認；序號必填且唯一；新增後待上線、出現在該機櫃上。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import auth  # noqa: E402
import db  # noqa: E402
import api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_STUB = "test-password-123"


def _client(tmp, user="admin"):
    db_path = Path(tmp) / "test.db"
    db.init_db(db_path)

    def _override():
        conn = db.get_connection(db_path)
        try:
            yield conn
        finally:
            conn.close()

    api.app.dependency_overrides[api.get_db] = _override
    c = TestClient(api.app)
    conn = db.get_connection(db_path)
    try:
        db.create_user(conn, user, auth.hash_password(_STUB))
        db.insert_hardware(conn, asset_serial="S-OLD", hostname="old1", ip="10.99.7.1",
                           physical_location="02_內湖機房", rack_no="B8/10-11U", asset_status="使用中")
        conn.commit()
    finally:
        conn.close()
    assert c.post("/api/auth/login", json={"username": user, "password": _STUB}).status_code == 200
    return c, db_path


def _body(**kw):
    b = {"room": "內湖機房", "rack": "B8", "u_start": 20, "u_end": 21, "asset_serial": "S-NEW",
         "device_model": "IBM S922", "asset_name": "新 S922"}
    b.update(kw)
    return b


def test_新增後待上線且出現在該機櫃():
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _client(tmp)
        r = c.post("/api/racks/add-device", json=_body())
        assert r.status_code == 200, r.text
        hw = r.json()["hardware"]
        assert hw["asset_status"] == "待上線"
        assert hw["rack_no"] == "B8/20-21U" and hw["physical_location"] == "02_內湖機房"
        det = c.get("/api/racks/detail", params={"room": "內湖機房", "rack": "B8"}).json()
        assert any(d.get("asset_serial") == "S-NEW" or "S-NEW" in str(d) for d in det["devices"])


def test_非管理員擋下():
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _client(tmp, user="tester")
        assert c.post("/api/racks/add-device", json=_body()).status_code == 403


def test_序號必填且不可重複():
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _client(tmp)
        assert c.post("/api/racks/add-device", json=_body(asset_serial="  ")).status_code == 400
        assert c.post("/api/racks/add-device", json=_body(asset_serial="S-OLD")).status_code == 409


def test_U位重疊要確認():
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _client(tmp)
        r = c.post("/api/racks/add-device", json=_body(u_start=10, u_end=10))
        assert r.status_code == 409
        assert r.json()["detail"]["overlap"][0]["asset_serial"] == "S-OLD"
        r = c.post("/api/racks/add-device", json=_body(u_start=10, u_end=10, confirm_overlap=True))
        assert r.status_code == 200, r.text


def test_參數錯誤與未知機房():
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _client(tmp)
        assert c.post("/api/racks/add-device", json=_body(u_start=30, u_end=20)).status_code == 400
        assert c.post("/api/racks/add-device", json=_body(u_start=0)).status_code == 400
        assert c.post("/api/racks/add-device", json=_body(room="不存在機房")).status_code == 400


def test_序號留空_給暫時編號NEW_要有主機名_CIA補登後算同一台():
    """2026-10-28（做法 A）：新設備 CIA 還沒給編號 → 序號留空，系統給 NEW-<IP>；沒主機名擋下（不然補登後會算兩台）。"""
    import rack_layout
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _client(tmp)
        assert c.post("/api/racks/add-device", json=_body(asset_serial="")).status_code == 400          # 沒主機名
        r = c.post("/api/racks/add-device", json=_body(asset_serial="", hostname="newhost1", ip="10.99.7.50"))
        assert r.status_code == 200, r.text
        hw = r.json()["hardware"]
        assert hw["asset_serial"] == "NEW-10.99.7.50" and hw["asset_status"] == "待上線"
        # 沒有 IP → 用主機名
        r2 = c.post("/api/racks/add-device", json=_body(asset_serial="", hostname="newhost2", u_start=30, u_end=30))
        assert r2.status_code == 200 and r2.json()["hardware"]["asset_serial"] == "NEW-newhost2"
        # 之後 CIA 補登同一台（同主機名＋IP、正式編號）→ 全站只算一台
        conn = db.get_connection(db_path)
        try:
            db.insert_hardware(conn, asset_serial="HW-90001", hostname="newhost1", ip="10.99.7.50",
                               physical_location="02_內湖機房", rack_no="B8/20-21U", asset_status="使用中")
            conn.commit()
            same = [m for m in rack_layout.machines(conn) if str(m.get("name", "")).lower() == "newhost1"]
            assert len(same) == 1
        finally:
            conn.close()


def test_機型要寫成_廠牌_型號():
    """2026-10-28：新增設備的機型一律「廠牌 型號」；整理空白／換行、廠牌統一寫法；只寫廠牌／沒廠牌／泛稱擋下。"""
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _client(tmp)
        for bad in ("", "S922", "DELL", "HPE Server", "(VM)"):
            r = c.post("/api/racks/add-device", json=_body(device_model=bad, asset_serial="S-X"))
            assert r.status_code == 400 and "例：" in r.json()["detail"], (bad, r.text)
        r = c.post("/api/racks/add-device", json=_body(device_model="  dell" + chr(10) + " R760 ", asset_serial="S-OK1"))
        assert r.status_code == 200, r.text
        assert r.json()["hardware"]["device_model"] == "DELL R760"
        opts = c.get("/api/models/options").json()
        assert {"model": "DELL R760", "count": 1} in opts["items"] and opts["example"]


def test_廠牌清單在系統設定自己增刪_限管理員_留紀錄():
    """2026-10-28 使用者：廠牌清單不要放 JSON，要能自己設定。存 app_settings；新增的廠牌馬上能用在機型檢查。"""
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _client(tmp)
        r = c.get("/api/model-vendors").json()
        names = [v["name"] for v in r["vendors"]]
        assert "DELL" in names and r["can_edit"] is True
        # 沒有的廠牌先擋；加進去就能用（別名也行）
        assert c.post("/api/racks/add-device", json=_body(device_model="Sugon X620", asset_serial="S-S1")).status_code == 400
        new = r["vendors"] + [{"name": "Sugon", "aliases": ["曙光"]}]
        assert c.put("/api/model-vendors", json={"vendors": new}).status_code == 200
        ok = c.post("/api/racks/add-device", json=_body(device_model="曙光 X620", asset_serial="S-S2"))
        assert ok.status_code == 200 and ok.json()["hardware"]["device_model"] == "SUGON X620"
        # 重複／空白擋下
        assert c.put("/api/model-vendors", json={"vendors": new + [{"name": "sugon", "aliases": []}]}).status_code == 400
        assert c.put("/api/model-vendors", json={"vendors": [{"name": "  ", "aliases": []}]}).status_code == 400
        # 留操作紀錄
        conn = db.get_connection(db_path)
        try:
            n = conn.execute("SELECT COUNT(*) FROM activity_log WHERE path = '/api/model-vendors'").fetchone()[0]
        finally:
            conn.close()
        assert n >= 1


def test_廠牌清單非管理員不能改():
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _client(tmp, user="tester")
        assert c.get("/api/model-vendors").json()["can_edit"] is False
        assert c.put("/api/model-vendors", json={"vendors": [{"name": "X", "aliases": []}]}).status_code == 403


def test_序號標準化_依廠牌檢查碼數():
    """2026-10-28 使用者：序號要像廠牌一樣正規化。去空白連字號、一律大寫、只能英數字；廠牌有碼數規定就要符合（Dell 7 碼）。"""
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _client(tmp)
        ok = c.post("/api/racks/add-device", json=_body(device_model="dell r760", hw_serial=" f39-y1k4 ", asset_serial="S-SN1"))
        assert ok.status_code == 200, ok.text
        assert ok.json()["hardware"]["hw_serial"] == "F39Y1K4"
        # 碼數不對、有符號都擋，訊息講出規定
        r = c.post("/api/racks/add-device", json=_body(device_model="Dell R760", hw_serial="ABC123", asset_serial="S-SN2"))
        assert r.status_code == 400 and "7 碼英數" in r.json()["detail"]
        assert c.post("/api/racks/add-device", json=_body(device_model="Dell R760", hw_serial="AB#1234", asset_serial="S-SN3")).status_code == 400
        # 沒有碼數規定的廠牌：只要求英數字、至少 5 碼；序號可以不填
        assert c.post("/api/racks/add-device", json=_body(device_model="Aruba AP 515", hw_serial="AB12", asset_serial="S-SN4")).status_code == 400
        assert c.post("/api/racks/add-device", json=_body(device_model="Aruba AP 515", hw_serial="ab12cd", asset_serial="S-SN5", u_start=25, u_end=25)).status_code == 200
        assert c.post("/api/racks/add-device", json=_body(device_model="Aruba AP 515", hw_serial="", asset_serial="S-SN6", u_start=40, u_end=40)).status_code == 200
        # 管理員可以在設定裡調整碼數
        vs = c.get("/api/model-vendors").json()["vendors"]
        for v in vs:
            if v["name"] == "DELL":
                v["serial"] = "6-7"
        assert c.put("/api/model-vendors", json={"vendors": vs}).status_code == 200
        assert c.post("/api/racks/add-device", json=_body(device_model="Dell R760", hw_serial="ABC123", asset_serial="S-SN7", u_start=42, u_end=42)).status_code == 200
        vs[0]["serial"] = "9-7"
        assert c.put("/api/model-vendors", json={"vendors": vs}).status_code == 400


def test_新增帶系統與管理IP_管理卡併進這台_錯的擋下():
    """2026-10-29：系統（業務系統代碼）、管理 IP（另建「主機名-iDRAC」管理卡，機櫃上併進這台、不算第二台）。"""
    import rack_layout
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _client(tmp)
        conn = db.get_connection(db_path)
        conn.execute("INSERT INTO business_system (api_id, name) VALUES ('SYS-A', '假系統甲')")
        conn.commit()
        conn.close()
        base = dict(asset_serial="", hostname="web77", ip="10.99.7.77", device_model="Dell R760", hw_serial="ABC1234")
        # 驗證都在建立之前：系統代碼不存在、管理 IP 格式錯、沒主機名、同主機 IP、種類不認得——一筆都不能建
        assert c.post("/api/racks/add-device", json=_body(**base, api_id="NOPE")).status_code == 400
        assert c.post("/api/racks/add-device", json=_body(**base, mgmt_ip="10.99.7")).status_code == 400
        assert c.post("/api/racks/add-device", json=_body(**{**base, "hostname": "", "asset_serial": "X-1"}, mgmt_ip="10.99.7.177")).status_code == 400
        assert c.post("/api/racks/add-device", json=_body(**base, mgmt_ip="10.99.7.77")).status_code == 400
        assert c.post("/api/racks/add-device", json=_body(**base, mgmt_ip="10.99.7.177", mgmt_kind="XYZ")).status_code == 400
        assert c.post("/api/racks/add-device", json=_body(**base, mgmt_ip="10.99.7.1")).status_code == 409          # 被 S-OLD 用掉
        conn = db.get_connection(db_path)
        assert conn.execute("SELECT COUNT(*) FROM hardware WHERE asset_serial LIKE 'NEW-%'").fetchone()[0] == 0
        conn.close()
        r = c.post("/api/racks/add-device", json=_body(**base, api_id="SYS-A", mgmt_ip="10.99.7.177", mgmt_kind="iDRAC"))
        assert r.status_code == 200, r.text
        assert r.json()["hardware"]["api_id"] == "SYS-A" and r.json()["mgmt"]["hostname"] == "web77-iDRAC"
        assert r.json()["mgmt"]["ip"] == "10.99.7.177" and r.json()["mgmt"]["asset_serial"] == "NEW-10.99.7.177"
        conn = db.get_connection(db_path)
        ms = [m for m in rack_layout.rack_detail(conn, "內湖機房", "B8")["devices"] if m["u_start"] == 20]
        host = [m for m in ms if m["name"] == "web77"]
        assert len(host) == 1 and host[0]["mgmt"] and host[0]["mgmt"][0]["ip"] == "10.99.7.177"     # 併進主機面板的管理卡
        assert not [m for m in rack_layout.rack_detail(conn, "內湖機房", "B8")["devices"] if m["name"] == "web77-iDRAC" and m.get("box_role") == "box"]
        conn.close()


def test_管理卡_同名主機擋下_0000擋下(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _client(tmp)
        conn = db.get_connection(db_path)
        db.insert_hardware(conn, asset_serial="S-DUP", hostname="web88-iDRAC", ip="10.99.7.88", asset_status="使用中")
        conn.commit()
        conn.close()
        base = dict(asset_serial="", hostname="web88", ip="10.99.7.89", device_model="Dell R760")
        assert c.post("/api/racks/add-device", json=_body(**base, mgmt_ip="10.99.7.188")).status_code == 409      # 同名會被併成同一台
        assert c.post("/api/racks/add-device", json=_body(**{**base, "hostname": "web90"}, mgmt_ip="0.0.0.0")).status_code == 400


# Q91：這個檔案測的是「誰能做什麼」，不套 conftest 的「測試帳號預設 admin」相容層（見 tests/conftest.py）。
ROLE_AWARE_TESTS = True
