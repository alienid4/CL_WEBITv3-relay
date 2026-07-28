"""平台／角色分類 + 全域搜尋。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import asset_classify  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_PW = "test-password-123"


def _client(tmp):
    db_path = Path(tmp) / "t.db"
    db.init_db(db_path)

    def _override():
        conn = db.get_connection(db_path)
        try:
            yield conn
        finally:
            conn.close()

    api.app.dependency_overrides[api.get_db] = _override
    return TestClient(api.app), db_path


def _login(client, db_path):
    conn = db.get_connection(db_path)
    try:
        db.create_user(conn, "tester", auth.hash_password(_PW))
    finally:
        conn.close()
    assert client.post("/api/auth/login",
                       json={"username": "tester", "password": _PW}).status_code == 200


def _svc(conn, ip, port):
    conn.execute(
        "INSERT INTO host_service (ip, proto, port, bind_addr, exposure, source, "
        "first_seen, last_seen) VALUES (?,'tcp',?,'0.0.0.0','all','test','t','t')",
        (ip, port),
    )


def _seed(conn):
    db.insert_hardware(conn, asset_serial="A-1", hostname="dbsrv", ip="203.0.113.10",
                       os="Rocky Linux 9.7", environment="正式")
    db.insert_hardware(conn, asset_serial="A-2", hostname="winapp", ip="203.0.113.11",
                       os="Microsoft Windows Server 2019", environment="正式")
    db.insert_hardware(conn, asset_serial="A-3", hostname="aixbox", ip="203.0.113.12",
                       os="AIX 7.2", environment="測試")
    db.insert_hardware(conn, asset_serial="A-4", hostname="idle", ip="203.0.113.13",
                       os="Rocky Linux 9.7")
    _svc(conn, "203.0.113.10", 3306)     # DB
    _svc(conn, "203.0.113.10", 22)
    _svc(conn, "203.0.113.11", 443)      # Web
    _svc(conn, "203.0.113.12", 22)       # 只有管理通道
    conn.commit()


def test_角色由服務推導():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        try:
            _seed(conn)
            cls = asset_classify.classify_all(conn)
            assert cls["A-1"]["roles"] == ["db"]
            assert cls["A-2"]["roles"] == ["web"]
            # 只有 SSH 的機器不算 Web 也不算「普通主機」，就是「僅管理通道」
            assert cls["A-3"]["roles"] == ["mgmt"]
        finally:
            conn.close()


def test_沒收過服務是未知不是主機():
    """把「還沒查」講成「查過了是普通主機」是製造假確定性。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        try:
            _seed(conn)
            cls = asset_classify.classify_all(conn)
            assert cls["A-4"]["roles"] == ["unknown"]
        finally:
            conn.close()


def test_平台判定三種都分得出來():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        try:
            _seed(conn)
            cls = asset_classify.classify_all(conn)
            assert cls["A-1"]["platform"] == "Linux"
            assert cls["A-2"]["platform"] == "Windows"
            assert cls["A-3"]["platform"] == "AIX/Unix"
        finally:
            conn.close()


def test_api依平台與角色篩選():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            conn = db.get_connection(db_path)
            try:
                _seed(conn)
            finally:
                conn.close()

            win = client.get("/api/assets", params={"platform": "Windows"}).json()
            assert {r["asset_serial"] for r in win} == {"A-2"}

            dbs = client.get("/api/assets", params={"role": "db"}).json()
            assert {r["asset_serial"] for r in dbs} == {"A-1"}

            # 多選（逗號分隔）
            multi = client.get("/api/assets", params={"platform": "Linux,AIX/Unix"}).json()
            assert {r["asset_serial"] for r in multi} == {"A-1", "A-3", "A-4"}

            # 平台＋角色可疊加
            both = client.get("/api/assets",
                              params={"platform": "Linux", "role": "db"}).json()
            assert {r["asset_serial"] for r in both} == {"A-1"}
        finally:
            api.app.dependency_overrides.clear()


def test_資產清單有帶出平台與角色欄位():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            conn = db.get_connection(db_path)
            try:
                _seed(conn)
            finally:
                conn.close()
            rows = client.get("/api/assets").json()
            one = [r for r in rows if r["asset_serial"] == "A-1"][0]
            assert one["platform"] == "Linux"
            assert one["roles"] == ["db"]
        finally:
            api.app.dependency_overrides.clear()


# ---- 全域搜尋 ----

def test_搜尋要登入():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        try:
            assert client.get("/api/search", params={"q": "x"}).status_code == 401
        finally:
            api.app.dependency_overrides.clear()


def test_搜尋分組回傳():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            conn = db.get_connection(db_path)
            try:
                _seed(conn)
                conn.execute("INSERT INTO systems (id, label, category) "
                             "VALUES ('core','核心交易系統','核心')")
                conn.commit()
            finally:
                conn.close()

            r = client.get("/api/search", params={"q": "dbsrv"}).json()
            keys = {g["key"] for g in r["groups"]}
            assert "assets" in keys

            r2 = client.get("/api/search", params={"q": "核心"}).json()
            assert "systems" in {g["key"] for g in r2["groups"]}
        finally:
            api.app.dependency_overrides.clear()


def test_搜數字當埠號精準比():
    """搜 22 不該把 8022、2222 全撈進來——最想要的那筆會被淹掉。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            conn = db.get_connection(db_path)
            try:
                _seed(conn)
                _svc(conn, "203.0.113.10", 8022)
                _svc(conn, "203.0.113.11", 2222)
                conn.commit()
            finally:
                conn.close()

            r = client.get("/api/search", params={"q": "22"}).json()
            svc = [g for g in r["groups"] if g["key"] == "services"]
            assert svc, "應該要有服務分組"
            titles = " ".join(i["title"] for i in svc[0]["items"])
            assert "8022" not in titles and "2222" not in titles
        finally:
            api.app.dependency_overrides.clear()


def test_搜尋找得到掃到但未登記的主機():
    """「這個 IP 是什麼」最常就是在問未登記的機器；只搜已登記等於答不出來。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            conn = db.get_connection(db_path)
            try:
                _seed(conn)
                conn.execute(
                    "INSERT INTO scan_history (ip, hostname, scan_ok, os_guess, scan_time) "
                    "VALUES ('203.0.113.99','mystery',1,'Linux/Unix（TTL≈64）','2026-07-21 10:00:00')"
                )
                conn.commit()
            finally:
                conn.close()

            r = client.get("/api/search", params={"q": "203.0.113.99"}).json()
            assert "unregistered" in {g["key"] for g in r["groups"]}
        finally:
            api.app.dependency_overrides.clear()


def test_空關鍵字不炸也不回全部():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            r = client.get("/api/search", params={"q": "   "})
            assert r.status_code == 200
            assert r.json()["groups"] == []
        finally:
            api.app.dependency_overrides.clear()
