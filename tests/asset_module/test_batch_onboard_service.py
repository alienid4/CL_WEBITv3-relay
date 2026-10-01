"""批次自動納管的背景服務層：環境別分流、啟動驗證、狀態、密碼不落地。

守的重點：
1. 只有「環境別＝測試」自動跑；正式/備援擋掉；未登記當未知（要人確認）
2. 勾統一密碼要 confirm=YES（會改正式資料且收不回）
3. batch_onboard_result 表裡**永遠不存密碼**
"""
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import batch_onboard_service as svc  # noqa: E402
import db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_PW = "test-password-123"


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    conn = db.get_connection(p)
    db.insert_hardware(conn, asset_serial="HW-T", ip="10.99.1.1", os="Red Hat Enterprise Linux 8 (64-bit)", hostname="test-a",
                       environment="測試", asset_status="使用中")
    db.insert_hardware(conn, asset_serial="HW-P", ip="10.99.1.2", os="Red Hat Enterprise Linux 8 (64-bit)", hostname="prod-a",
                       environment="正式", asset_status="使用中")
    db.insert_hardware(conn, asset_serial="HW-S", ip="10.99.1.3", os="Red Hat Enterprise Linux 8 (64-bit)", hostname="standby-a",
                       environment="備援", asset_status="使用中")
    # OS 分流靠掃描的開放埠：22＝Linux(ssh)、5985＝Windows(winrm)。
    # 每台都要有一筆「掃到活著」的紀錄，classify 才認得（方案 B：只納管掃到活著的）。
    st = "2026-09-06 10:00:00"
    for ip, ports in [("10.99.1.1", "22"), ("10.99.1.2", "22"), ("10.99.1.3", "22")]:
        conn.execute("INSERT INTO scan_history (scan_time, ip, scan_ok, open_ports) "
                     "VALUES (?,?,1,?)", (st, ip, ports))
    conn.commit()
    return p, conn


# ===== OS 分流 ＋ 環境別分流 =====

def test_只有Linux測試機能自動跑_正式備援擋掉():
    with tempfile.TemporaryDirectory() as tmp:
        _, conn = _conn(tmp)
        try:
            out = svc.classify_targets(conn, ["10.99.1.1", "10.99.1.2", "10.99.1.3"])
            assert [t["ip"] for t in out["run"]] == ["10.99.1.1"]           # Linux+測試
            assert {t["ip"] for t in out["blocked_production"]} == {"10.99.1.2", "10.99.1.3"}
        finally:
            conn.close()


def test_Windows另外歸一堆_不會被當密碼錯():
    """網段裡的 Windows（開 5985）要被認出來、單獨歸類，不能拿 Linux 方式硬打、
    也不能誤判成登入失敗。"""
    with tempfile.TemporaryDirectory() as tmp:
        _, conn = _conn(tmp)
        try:
            conn.execute("INSERT INTO scan_history (scan_time, ip, scan_ok, open_ports) "
                         "VALUES ('2026-09-06 10:00:00','10.99.1.50',1,'5985,3389')")
            conn.commit()
            out = svc.classify_targets(conn, ["10.99.1.50"])
            assert [t["ip"] for t in out["windows"]] == ["10.99.1.50"]
            assert not any(t["ip"] == "10.99.1.50" for t in out["run"])
        finally:
            conn.close()


def test_網段展開只取掃到活著的_不盲展開整段():
    """給 CIDR 不是硬展成 254 個 IP，是只取掃描掃到活著、落在段內的那些。"""
    with tempfile.TemporaryDirectory() as tmp:
        _, conn = _conn(tmp)
        try:
            out = svc.classify_targets(conn, ["10.99.1.0/24"])
            got = {t["ip"] for t in out["run"]} | {t["ip"] for t in out["blocked_production"]}
            assert got == {"10.99.1.1", "10.99.1.2", "10.99.1.3"}   # 只有掃到的三台，不是 254 個
        finally:
            conn.close()


def test_未登記機器不自動跑_要人工勾選():
    """未登記的機器**不會自動納管**，改成請人確認（2026-09-08 A6 拍板）。

    以前是「網段標成測試就自動跑」。問題在網段只講得出「這段是測試環境」，
    講不出「這台是伺服器還是交換器」——而交換器、儲存設備、iDRAC 管理卡
    都開 22，banner 還常自報 Linux。資產庫查無的機器就是無從判斷。

    不是把路堵死：它會進 unknown 桶，使用者在畫面上勾了（include_ips）就會跑。
    差別只在**預設不跑**，要有人按下去。
    """
    with tempfile.TemporaryDirectory() as tmp:
        _, conn = _conn(tmp)
        try:
            lo = svc._ip_int("10.99.7.0")
            hi = svc._ip_int("10.99.7.255")
            conn.execute("INSERT INTO network_segment (raw_cidr, cidr, net_start, net_end, environment) "
                         "VALUES ('10.99.7.0/24','10.99.7.0/24',?,?,'測試')", (lo, hi))
            conn.execute("INSERT INTO scan_history (scan_time, ip, scan_ok, open_ports) "
                         "VALUES ('2026-09-06 10:00:00','10.99.7.20',1,'22')")
            conn.commit()
            out = svc.classify_targets(conn, ["10.99.7.20"])
            assert out["run"] == [], "未登記的機器不該自動跑"
            assert [t["ip"] for t in out["unknown"]] == ["10.99.7.20"]
            assert "資產庫查無" in out["unknown"][0]["reason"]
        finally:
            conn.close()


def test_全是正式機時啟動被擋():
    with tempfile.TemporaryDirectory() as tmp:
        _, conn = _conn(tmp)
        try:
            with pytest.raises(ValueError):
                svc.start(conn, ["10.99.1.2"], "root", ["A"], unify_password=False,
                          triggered_by="tester")
        finally:
            conn.close()


def test_沒給密碼被擋():
    with tempfile.TemporaryDirectory() as tmp:
        _, conn = _conn(tmp)
        try:
            with pytest.raises(ValueError):
                svc.start(conn, ["10.99.1.1"], "root", [], unify_password=False,
                          triggered_by="tester")
        finally:
            conn.close()


# ===== API 層：統一密碼要 confirm=YES =====

def _client(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    conn = db.get_connection(p)
    db.create_user(conn, "tester", auth.hash_password(_PW))
    db.insert_hardware(conn, asset_serial="HW-P", ip="10.99.1.2", environment="正式",
                       os="Red Hat Enterprise Linux 8 (64-bit)",
                       asset_status="使用中")
    # 掃描紀錄：兩台都開 22（Linux/SSH），classify 才會依環境別分堆而不是丟到 other。
    # 10.99.1.2＝正式(已登記)→blocked；10.99.9.9＝未登記又無網段→unknown。
    for ip in ("10.99.1.2", "10.99.9.9"):
        conn.execute("INSERT INTO scan_history (scan_time, ip, scan_ok, open_ports) "
                     "VALUES ('2026-09-06 10:00:00',?,1,'22')", (ip,))
    conn.commit()
    conn.close()

    def _override():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _override
    client = TestClient(api.app)
    assert client.post("/api/auth/login",
                       json={"username": "tester", "password": _PW}).status_code == 200
    return client


def test_勾統一密碼但沒打YES_被擋():
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(tmp)
        try:
            r = client.post("/api/onboard/batch-auto", json={
                "ips": ["10.99.1.2"], "username": "root", "passwords": ["A", "B"],
                "unify_password": True, "confirm": ""})
            assert r.status_code == 400
            assert "YES" in r.json()["detail"]
        finally:
            api.app.dependency_overrides.clear()


def test_preview不動機器_只回分類():
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(tmp)
        try:
            r = client.post("/api/onboard/batch-auto/preview", json={
                "ips": ["10.99.1.2", "10.99.9.9"], "username": "root", "passwords": ["A"]})
            assert r.status_code == 200
            body = r.json()
            assert body["blocked_production"][0]["ip"] == "10.99.1.2"
            assert body["unknown"][0]["ip"] == "10.99.9.9"
        finally:
            api.app.dependency_overrides.clear()
