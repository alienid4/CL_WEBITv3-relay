"""服務盤點寫入層：upsert、消失標記、回歸清除、只收已納管、API 端點與排序。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
import service_inventory  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_PW = "test-password-123"

SS_A = """State  Recv-Q Send-Q Local Address:Port  Peer Address:Port Process
LISTEN 0      128          0.0.0.0:22         0.0.0.0:*     users:(("sshd",pid=1,fd=3))
LISTEN 0      511          0.0.0.0:80         0.0.0.0:*     users:(("nginx",pid=2,fd=6))
LISTEN 0      70         127.0.0.1:3306            *:*      users:(("mysqld",pid=3,fd=2))
"""
# 第二次收：nginx 不見了（服務被停掉），多了 8000
SS_B = """State  Recv-Q Send-Q Local Address:Port  Peer Address:Port Process
LISTEN 0      128          0.0.0.0:22         0.0.0.0:*     users:(("sshd",pid=1,fd=3))
LISTEN 0      128          0.0.0.0:8000       0.0.0.0:*     users:(("uvicorn",pid=9,fd=8))
LISTEN 0      70         127.0.0.1:3306            *:*      users:(("mysqld",pid=3,fd=2))
"""


def _runner_for(text):
    def run(host, cmd):
        return "" if "systemctl" in cmd else text
    return run


def _db(tmp):
    db_path = Path(tmp) / "t.db"
    db.init_db(db_path)
    return db_path


def _seed_host(conn, serial="A-001", ip="203.0.113.50", collect_ok=1):
    db.insert_hardware(conn, asset_serial=serial, hostname="web1", ip=ip,
                       collect_ok=collect_ok)


def test_收集寫入並帶出新增數():
    with tempfile.TemporaryDirectory() as tmp:
        conn = db.get_connection(_db(tmp))
        try:
            _seed_host(conn)
            r = service_inventory.collect_services(conn, runner=_runner_for(SS_A))
            assert r["status"] == "ok"
            assert r["services"] == 3
            rows = service_inventory.list_services(conn)
            assert {x["port"] for x in rows} == {22, 80, 3306}
            assert r["hosts"][0]["added"] == 3
        finally:
            conn.close()


def test_服務消失是標記不是刪除():
    """「上週在聽 80、今天不聽了」本身就是要看的事實，刪掉就查不出來。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = db.get_connection(_db(tmp))
        try:
            _seed_host(conn)
            service_inventory.collect_services(conn, runner=_runner_for(SS_A))
            r2 = service_inventory.collect_services(conn, runner=_runner_for(SS_B))
            assert r2["hosts"][0]["gone"] == 1

            live = {x["port"] for x in service_inventory.list_services(conn)}
            assert live == {22, 3306, 8000}          # 預設看不到消失的

            all_rows = service_inventory.list_services(conn, include_gone=True)
            gone = [x for x in all_rows if x["gone_at"]]
            assert len(gone) == 1 and gone[0]["port"] == 80   # 資料還在，查得到
        finally:
            conn.close()


def test_服務回來要清掉消失標記():
    with tempfile.TemporaryDirectory() as tmp:
        conn = db.get_connection(_db(tmp))
        try:
            _seed_host(conn)
            service_inventory.collect_services(conn, runner=_runner_for(SS_A))
            service_inventory.collect_services(conn, runner=_runner_for(SS_B))   # 80 消失
            service_inventory.collect_services(conn, runner=_runner_for(SS_A))   # 80 回來
            live = {x["port"] for x in service_inventory.list_services(conn)}
            assert 80 in live
            row = [x for x in service_inventory.list_services(conn) if x["port"] == 80][0]
            assert row["gone_at"] is None
        finally:
            conn.close()


def test_只收已納管主機():
    """沒納管的機器連不進去，收也是白收——不該被算進候選、也不該產生失敗噪音。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = db.get_connection(_db(tmp))
        try:
            _seed_host(conn, "A-001", "203.0.113.50", collect_ok=1)
            _seed_host(conn, "A-002", "203.0.113.51", collect_ok=0)
            _seed_host(conn, "A-003", "203.0.113.52", collect_ok=None)
            r = service_inventory.collect_services(conn, runner=_runner_for(SS_A))
            assert r["candidates"] == 1
            assert {x["ip"] for x in service_inventory.list_services(conn)} == {"203.0.113.50"}
        finally:
            conn.close()


def test_單台失敗不中斷整批且原因留著():
    with tempfile.TemporaryDirectory() as tmp:
        conn = db.get_connection(_db(tmp))
        try:
            _seed_host(conn, "A-001", "203.0.113.50")
            _seed_host(conn, "A-002", "203.0.113.51")

            def flaky(host, cmd):
                if host == "203.0.113.51":
                    raise OSError("timeout")
                return "" if "systemctl" in cmd else SS_A

            r = service_inventory.collect_services(conn, runner=flaky)
            assert len(r["failed"]) == 1
            assert "timeout" in r["failed"][0]["error"]
            assert r["services"] == 3          # 好的那台照收
            assert r["status"] == "ok"
        finally:
            conn.close()


def test_摘要區分確定與推測():
    """有行程名佐證的 vs 只有埠號推測的要分開算，不然使用者不知道這份資料多可信。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = db.get_connection(_db(tmp))
        try:
            _seed_host(conn)
            noroot = """State Recv-Q Send-Q Local Address:Port Peer Address:Port
LISTEN 0 128 0.0.0.0:22 0.0.0.0:*
LISTEN 0 128 0.0.0.0:9999 0.0.0.0:*
"""
            service_inventory.collect_services(conn, runner=_runner_for(noroot))
            s = service_inventory.service_summary(conn)
            assert s["live"] == 2
            assert s["confirmed"] == 0          # 都沒有行程名
            assert s["exposed"] == 2            # 都綁 0.0.0.0
            assert s["hosts"] == 1
        finally:
            conn.close()


# ---- API ----

def _client(tmp):
    db_path = _db(tmp)

    def _override_get_db():
        conn = db.get_connection(db_path)
        try:
            yield conn
        finally:
            conn.close()

    api.app.dependency_overrides[api.get_db] = _override_get_db
    return TestClient(api.app), db_path


def _login(client, db_path):
    conn = db.get_connection(db_path)
    try:
        db.create_user(conn, "tester", auth.hash_password(_PW))
    finally:
        conn.close()
    assert client.post("/api/auth/login",
                       json={"username": "tester", "password": _PW}).status_code == 200


def test_服務端點都要登入():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        try:
            assert client.get("/api/services").status_code == 401
            assert client.get("/api/services/summary").status_code == 401
            assert client.post("/api/services/collect").status_code == 401
        finally:
            api.app.dependency_overrides.clear()


def test_api排序走白名單且空值排最後():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            conn = db.get_connection(db_path)
            try:
                _seed_host(conn, "A-001", "203.0.113.50")
                service_inventory.collect_services(conn, runner=_runner_for(SS_A))
                # 手動塞一筆沒有 process 的，驗證空值排序
                conn.execute(
                    "INSERT INTO host_service (ip, proto, port, bind_addr, exposure, source, "
                    "first_seen, last_seen) VALUES ('203.0.113.50','tcp',9999,'0.0.0.0','all',"
                    "'test','2026-01-01','2026-01-01')"
                )
                conn.commit()
            finally:
                conn.close()

            r = client.get("/api/services", params={"sort_by": "port", "order": "desc"})
            assert r.status_code == 200
            ports = [x["port"] for x in r.json()["items"]]
            assert ports == sorted(ports, reverse=True)

            # 亂給排序欄位要退回預設，不可以讓它拼進 SQL 或炸掉
            assert client.get("/api/services",
                              params={"sort_by": "port; DROP TABLE hardware"}).status_code == 200

            r3 = client.get("/api/services", params={"sort_by": "process"})
            procs = [x["process"] for x in r3.json()["items"]]
            assert procs[-1] is None          # 空值一律最後
        finally:
            api.app.dependency_overrides.clear()


def test_api篩選與收合基礎服務():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            conn = db.get_connection(db_path)
            try:
                _seed_host(conn, "A-001", "203.0.113.50")
                service_inventory.collect_services(conn, runner=_runner_for(SS_A))
            finally:
                conn.close()

            all_ports = {x["port"] for x in client.get("/api/services").json()["items"]}
            assert 22 in all_ports

            no_infra = client.get("/api/services", params={"include_infra": False}).json()["items"]
            assert 22 not in {x["port"] for x in no_infra}

            only_local = client.get("/api/services",
                                    params={"exposure": "localhost"}).json()["items"]
            assert {x["port"] for x in only_local} == {3306}

            by_serial = client.get("/api/services",
                                   params={"asset_serial": "A-001"}).json()["items"]
            assert len(by_serial) == 3
            assert by_serial[0]["hostname"] == "web1"    # 有帶出主機名，畫面才不用再查一次
        finally:
            api.app.dependency_overrides.clear()
