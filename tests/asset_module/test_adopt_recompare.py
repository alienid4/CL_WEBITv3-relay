"""① 納入管理 + ② 掃完自動重比對（DB/service 層，確定性）。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import scan_service  # noqa: E402


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    c = db.get_connection()
    yield c
    c.close()


def test_遷移加了技術層新欄位(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(hardware)")}
    assert {"subnet", "mac", "hw_serial"} <= cols


def test_納入管理登記含新欄位(conn):
    hid = db.insert_hardware(
        conn, asset_serial="ADOPT-1.2.3.4", ip="1.2.3.4", hostname="h1",
        subnet="1.2.3.0/24", mac="aa:bb:cc", environment="正式",
    )
    row = conn.execute("SELECT * FROM hardware WHERE id = ?", (hid,)).fetchone()
    assert row["ip"] == "1.2.3.4" and row["subnet"] == "1.2.3.0/24" and row["mac"] == "aa:bb:cc"


def test_掃完自動重比對_未登記主機產生漏登記(conn):
    conn.execute(
        "INSERT INTO scan_history (scan_time, hostname, ip, scan_ok) VALUES ('2026-07-18 01:00:00','h9','9.9.9.9',1)"
    )
    conn.commit()
    scan_service._recompare(conn)
    rows = conn.execute("SELECT issue_type FROM comparison_result WHERE ip = '9.9.9.9'").fetchall()
    assert any(r["issue_type"] == "漏登記" for r in rows)


def test_納入管理後不再判漏登記(conn):
    conn.execute(
        "INSERT INTO scan_history (scan_time, hostname, ip, scan_ok) VALUES ('2026-07-18 02:00:00','h8','8.8.8.8',1)"
    )
    db.insert_hardware(conn, asset_serial="X8", ip="8.8.8.8", hostname="h8", environment="正式")
    conn.commit()
    scan_service._recompare(conn)
    rows = conn.execute(
        "SELECT 1 FROM comparison_result WHERE ip = '8.8.8.8' AND issue_type = '漏登記'"
    ).fetchall()
    assert rows == []


def test_納管候選清單帶出指紋供人辨識(conn):
    """F5：未受管主機在納管清單要能看到 MAC/廠商/OS猜測/開放埠，
    否則畫面只有光禿禿一個 IP，人認不出是什麼、無從決定納不納管。"""
    from fastapi.testclient import TestClient
    import api
    import auth

    conn.execute(
        "INSERT INTO scan_history (scan_time, ip, hostname, scan_ok, mac, mac_vendor, open_ports, ttl, os_guess) "
        "VALUES ('2026-07-18 03:00:00','172.16.0.9','',1,'00:0c:29:11:22:33','VMware','22,3389',128,'Windows（RDP 3389）')"
    )
    db.create_user(conn, "u", auth.hash_password("test-password-123"))
    conn.commit()

    def _override():
        c = db.get_connection()
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _override
    try:
        client = TestClient(api.app)
        client.post("/api/auth/login", json={"username": "u", "password": "test-password-123"})
        rows = client.get("/api/scan/unregistered").json()
        me = next(r for r in rows if r["ip"] == "172.16.0.9")
        assert me["mac_vendor"] == "VMware"
        assert me["os_guess"] == "Windows（RDP 3389）"
        assert me["open_ports"] == "22,3389"
        assert me["mac"] == "00:0c:29:11:22:33"
    finally:
        api.app.dependency_overrides.clear()


def test_納管當下就撤銷該台的漏登記_不等下次掃描(conn):
    """實際踩到：使用者納管 5 台後，儀表板仍顯示「漏登記 6 筆」，實際只有 1 台沒登記。
    原因是撤銷只發生在「下一次掃描後的重比對」，中間那段時間畫面在說謊。

    剛做完的動作沒有反映在畫面上，是最容易讓人對系統失去信任的一種 bug。
    """
    from fastapi.testclient import TestClient
    import api
    import auth

    conn.execute(
        "INSERT INTO scan_history (scan_time, ip, hostname, scan_ok) "
        "VALUES ('2026-07-19 05:00:00','10.1.1.7','',1)"
    )
    conn.execute(
        "INSERT INTO comparison_result (detected_at, ip, hostname, issue_type, is_read) "
        "VALUES ('2026-07-19 05:00:01','10.1.1.7','','漏登記',0)"
    )
    # 另一台沒被納管的，不可以被連坐撤銷
    conn.execute(
        "INSERT INTO comparison_result (detected_at, ip, hostname, issue_type, is_read) "
        "VALUES ('2026-07-19 05:00:01','10.1.1.8','','漏登記',0)"
    )
    db.create_user(conn, "u2", auth.hash_password("test-password-123"))
    conn.commit()

    def _override():
        c = db.get_connection()
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _override
    try:
        client = TestClient(api.app)
        client.post("/api/auth/login", json={"username": "u2", "password": "test-password-123"})

        r = client.post("/api/assets/adopt", json={
            "fields": {"ip": "10.1.1.7", "asset_purpose": "測試", "environment": "正式"}
        })
        assert r.status_code == 200, r.text
        assert r.json()["retracted_issues"] == 1

        open_rows = conn.execute(
            "SELECT ip FROM comparison_result WHERE issue_type='漏登記' AND is_read=0"
        ).fetchall()
        ips = {row["ip"] for row in open_rows}
        assert "10.1.1.7" not in ips, "納管的那台仍掛著漏登記"
        assert "10.1.1.8" in ips, "不該連坐撤銷別台的漏登記"
    finally:
        api.app.dependency_overrides.clear()


def test_納管預設資產狀態為使用中_不留空(conn):
    """使用者反映：納管進來的 6 台，狀態全顯示「未知」。

    但它們會出現在納管候選清單，正是因為**掃描掃到它們活著**——
    明明知道在跑卻寫「未知」是錯的資訊，不是保守。留空還會讓狀態排序整片沉到最後。
    """
    from fastapi.testclient import TestClient
    import api
    import auth

    db.create_user(conn, "u3", auth.hash_password("test-password-123"))
    conn.commit()

    def _override():
        c = db.get_connection()
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _override
    try:
        client = TestClient(api.app)
        client.post("/api/auth/login", json={"username": "u3", "password": "test-password-123"})

        # 沒填狀態 → 預設使用中
        r = client.post("/api/assets/adopt", json={
            "fields": {"ip": "10.2.2.1", "asset_purpose": "測試", "environment": "正式"}
        })
        assert r.status_code == 200, r.text
        row = conn.execute("SELECT asset_status FROM hardware WHERE ip='10.2.2.1'").fetchone()
        assert row["asset_status"] == "使用中"

        # 有明確填就照填的，不可被預設值蓋掉
        r = client.post("/api/assets/adopt", json={
            "fields": {"ip": "10.2.2.2", "asset_purpose": "測試",
                       "environment": "正式", "asset_status": "維修中"}
        })
        assert r.status_code == 200, r.text
        row = conn.execute("SELECT asset_status FROM hardware WHERE ip='10.2.2.2'").fetchone()
        assert row["asset_status"] == "維修中"
    finally:
        api.app.dependency_overrides.clear()
