"""N1 收集入口收斂（決策 C4）：一個動作進來，系統自己選路，結果分得出成敗。

這片真正的價值在「分派邏輯」——四種探測結果各該走哪條路、各該告訴使用者做什麼。
所以測試的重點是**每種路徑各挑一台走一遍**，而不是驗證網路。探測／SSH 試連／WinRM
收集全部注入假的，家裡就能把整條判定鏈測到。

另外守三條這片自己引進的風險：
1. 不自動把未登記主機建成資產（跟排程自動納管同一條底線）
2. WinRM 密碼不進結果表、不進回應
3. 一次貼太大的網段要擋下來，不要讓端點跑到天荒地老
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import auth  # noqa: E402
import collect_dispatch as cd  # noqa: E402
import credential_store as cs  # noqa: E402
import db  # noqa: E402
import api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_STUB_CREDENTIAL = "test-password-123"
FAKE_WINRM_PW = "FAKE-TEST-PW-not-real-N1"


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _seed_asset(conn, ip, serial, collect_ok=0):
    db.insert_hardware(conn, asset_serial=serial, ip=ip, collect_ok=collect_ok,
                       environment="正式", asset_status="使用中")


# 四台，一台一條路：22 開→SSH／445 開→WinRM／活著但都不通→Agent／沒回應→只能匯入
FOUR_ROUTES = {
    "10.0.0.1": [22, 80],
    "10.0.0.2": [445, 3389],
    "10.0.0.3": [8000],
    "10.0.0.4": None,
}


def _prober(ip):
    return FOUR_ROUTES.get(ip)


# ===== parse_targets =====

def test_網段展開成可用主機位址():
    ips = cd.parse_targets("192.168.5.0/30")
    assert ips == ["192.168.5.1", "192.168.5.2"]


def test_三種寫法可以混用_並保留輸入順序():
    ips = cd.parse_targets("10.1.1.5, 10.1.1.10-12\n192.168.9.0/30")
    assert ips == ["10.1.1.5", "10.1.1.10", "10.1.1.11", "10.1.1.12",
                   "192.168.9.1", "192.168.9.2"]


def test_重複的目標只算一次():
    assert cd.parse_targets("10.1.1.5 10.1.1.5\n10.1.1.5") == ["10.1.1.5"]


def test_格式錯誤要明確擋下來_不要默默略過():
    for bad in ("not-an-ip", "10.1.1.0/99", "10.1.1.20-5"):
        try:
            cd.parse_targets(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad} 應該被擋下來")


def test_一次貼太大的網段要擋下來():
    try:
        cd.parse_targets("10.0.0.0/16")
    except ValueError as exc:
        assert "上限" in str(exc)
        return
    raise AssertionError("/16 應該超過上限被擋")


def test_超大網段是先看大小才展開_不是展開才發現():
    """IPv6 的 /64 有 2^64 個位址。先展開才檢查上限＝記憶體被吃光、服務整個倒，
    不是慢一點而已。這條測試會在幾毫秒內回來；若實作退回成先展開，它會直接掛住。"""
    try:
        cd.parse_targets("2001:db8::/64")
    except ValueError as exc:
        assert "上限" in str(exc)
        return
    raise AssertionError("IPv6 /64 應該被擋")


def test_IPv6不能用範圍簡寫_不要炸成500():
    try:
        cd.parse_targets("2001:db8::1-5")
    except ValueError:
        return
    raise AssertionError("IPv6 範圍簡寫應該被擋成 ValueError（端點才回得了 400）")


# ===== choose_route：分派核心 =====

def test_四種探測結果各對應一條路():
    assert cd.choose_route(True, [22, 80]) == cd.ROUTE_SSH
    assert cd.choose_route(True, [445, 3389]) == cd.ROUTE_WINRM
    assert cd.choose_route(True, [5985]) == cd.ROUTE_WINRM
    assert cd.choose_route(True, [8000]) == cd.ROUTE_AGENT
    assert cd.choose_route(False, []) == cd.ROUTE_IMPORT


def test_兩個都開時走SSH_因為收集鏈主線是SSH():
    # Windows 裝了 OpenSSH Server 時 22 跟 445 都會開。WinRM 只收得到 facts，
    # 服務／帳號盤點都走 SSH——能走 SSH 就不該退回 WinRM。
    assert cd.choose_route(True, [22, 445, 5985]) == cd.ROUTE_SSH


# ===== run_dispatch：每種路徑各挑一台走一遍 =====

def test_每種路徑各挑一台_結果表分得出四種下場():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed_asset(conn, "10.0.0.1", "A-1")
        _seed_asset(conn, "10.0.0.4", "A-4")

        out = cd.run_dispatch(
            conn, "10.0.0.1 10.0.0.2 10.0.0.3 10.0.0.4",
            prober=_prober,
            ssh_prober=lambda ip: (True, None),
            winrm_runner=lambda host, ps: "hostname=WINBOX\nos=Windows Server 2019",
            cred_key_path=str(Path(tmp) / "cred.key"),
        )
        by_ip = {r["ip"]: r for r in out["results"]}

        assert by_ip["10.0.0.1"]["route"] == cd.ROUTE_SSH
        assert by_ip["10.0.0.1"]["status"] == cd.STATUS_COLLECTED
        assert by_ip["10.0.0.2"]["route"] == cd.ROUTE_WINRM
        assert by_ip["10.0.0.2"]["status"] == cd.STATUS_COLLECTED
        assert by_ip["10.0.0.3"]["route"] == cd.ROUTE_AGENT
        assert by_ip["10.0.0.3"]["status"] == cd.STATUS_NEEDS_AGENT
        assert by_ip["10.0.0.4"]["route"] == cd.ROUTE_IMPORT
        assert by_ip["10.0.0.4"]["status"] == cd.STATUS_IMPORT_ONLY

        # 一張表要看得出「成功幾台、要人工處理幾台」
        assert out["total"] == 4
        assert out["collected"] == 2
        assert out["needs_action"] == 2
        conn.close()


def test_SSH通了要把已納管寫回資產_畫面立刻反映實況():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed_asset(conn, "10.0.0.1", "A-1", collect_ok=0)
        cd.run_dispatch(conn, "10.0.0.1", prober=_prober, ssh_prober=lambda ip: (True, None))
        row = conn.execute(
            "SELECT collect_ok, collect_checked_at FROM hardware WHERE asset_serial='A-1'"
        ).fetchone()
        assert row["collect_ok"] == 1
        assert row["collect_checked_at"]
        conn.close()


def test_SSH連不上要說是身分問題_並保留原始原因():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed_asset(conn, "10.0.0.1", "A-1")
        out = cd.run_dispatch(
            conn, "10.0.0.1", prober=_prober,
            ssh_prober=lambda ip: (False, "Permission denied (publickey)"))
        r = out["results"][0]
        assert r["status"] == cd.STATUS_NEEDS_CREDENTIAL
        # 「金鑰沒佈」跟「機器不在」要做的事完全不同，原因不能被吞掉
        assert "Permission denied" in r["message"]
        assert out["collected"] == 0 and out["needs_action"] == 1
        conn.close()


def test_收集器自己那台不會被報成待佈身分():
    """收集器本機不需要 webit3scan（系統自己就是 ansible 主機，決策 C2）。
    少了本機分支，把自己的網段貼進來會亮一個永遠修不好的假紅燈。"""
    import manage_state

    orig_local, orig_runner = manage_state.local_ips, manage_state._local_runner
    manage_state.local_ips = lambda: {"10.0.0.1"}
    manage_state._local_runner = lambda *a, **k: (lambda host, cmd: "collector-221\n")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            conn = _conn(tmp)
            _seed_asset(conn, "10.0.0.1", "A-1")
            out = cd.run_dispatch(conn, "10.0.0.1", prober=_prober)   # 不注入 ssh_prober
            r = out["results"][0]
            assert r["status"] == cd.STATUS_COLLECTED
            assert "本機" in r["message"]
            conn.close()
    finally:
        manage_state.local_ips, manage_state._local_runner = orig_local, orig_runner


def test_沒有WinRM憑證時要說去哪裡設定_不是含糊的失敗():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        out = cd.run_dispatch(conn, "10.0.0.2", prober=_prober,
                              cred_key_path=str(Path(tmp) / "cred.key"))
        r = out["results"][0]
        assert r["status"] == cd.STATUS_NEEDS_CREDENTIAL
        assert "憑證" in r["message"]
        conn.close()


def test_WinRM收集失敗要如實回報_不吞成成功():
    def _boom(host, ps):
        raise ConnectionError("WinRM 認證失敗")

    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        key = str(Path(tmp) / "cred.key")
        cs.save(conn, "win-lab", "winrm", "svc_scan", FAKE_WINRM_PW,
                scope="10.0.0.", key_path=key)
        out = cd.run_dispatch(conn, "10.0.0.2", prober=_prober,
                              winrm_runner=_boom, cred_key_path=key)
        r = out["results"][0]
        assert r["status"] == cd.STATUS_FAILED
        assert "認證失敗" in r["message"]
        conn.close()


# ===== 這片自己引進的風險 =====

def test_不自動把未登記主機建成資產():
    """收不收一台來路不明的機器是人的決定——跟排程自動納管同一條底線。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        out = cd.run_dispatch(conn, "10.0.0.1", prober=_prober,
                              ssh_prober=lambda ip: (True, None))
        assert conn.execute("SELECT COUNT(*) c FROM hardware").fetchone()["c"] == 0
        # 但要講清楚下一步：收得到卻沒地方落
        assert "納入管理" in out["results"][0]["message"]
        assert out["results"][0]["registered"] == 0
        conn.close()


def test_WinRM密碼不進結果表也不進資料庫():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        key = str(Path(tmp) / "cred.key")
        cs.save(conn, "win-lab", "winrm", "svc_scan", FAKE_WINRM_PW,
                scope="10.0.0.", key_path=key)
        out = cd.run_dispatch(conn, "10.0.0.2", prober=_prober,
                              winrm_runner=lambda h, p: "hostname=WINBOX",
                              cred_key_path=key)
        assert FAKE_WINRM_PW not in repr(out)
        stored = conn.execute(
            "SELECT ip, open_ports, route, status, message FROM collect_dispatch_result"
        ).fetchall()
        assert all(FAKE_WINRM_PW not in str(tuple(r)) for r in stored)
        conn.close()


def test_結果有存下來_重新整理還原得回上一次():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        cd.run_dispatch(conn, "10.0.0.3 10.0.0.4", prober=_prober)
        latest = cd.latest_run(conn)
        assert latest["run"]["status"] == "ok"
        assert latest["run"]["target_count"] == 2
        assert {r["ip"] for r in latest["results"]} == {"10.0.0.3", "10.0.0.4"}
        assert latest["needs_action"] == 2
        conn.close()


def test_中途爆掉的run不會永遠卡在running():
    def _boom(ip):
        raise OSError("網路整段不通")

    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            cd.run_dispatch(conn, "10.0.0.1", prober=_boom)
        except OSError:
            pass
        row = conn.execute("SELECT status, error FROM collect_dispatch_run").fetchone()
        assert row["status"] == "failed"
        assert row["error"]
        conn.close()


# ===== API 層 =====

def _client(tmp):
    db_path = Path(tmp) / "test.db"
    db.init_db(db_path)

    def _override_get_db():
        conn = db.get_connection(db_path)
        try:
            yield conn
        finally:
            conn.close()

    api.app.dependency_overrides[api.get_db] = _override_get_db
    client = TestClient(api.app)
    conn = db.get_connection(db_path)
    try:
        db.create_user(conn, "tester", auth.hash_password(_STUB_CREDENTIAL))
    finally:
        conn.close()
    assert client.post("/api/auth/login",
                       json={"username": "tester", "password": _STUB_CREDENTIAL}
                       ).status_code == 200
    return client, db_path


def test_端點_目標格式錯誤回400而不是500():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        r = client.post("/api/collect/dispatch", json={"targets": "這不是IP"})
        assert r.status_code == 400
        api.app.dependency_overrides.clear()


def test_端點_沒跑過時latest不炸_回空結果():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        r = client.get("/api/collect/dispatch/latest")
        assert r.status_code == 200
        assert r.json()["results"] == []
        api.app.dependency_overrides.clear()


def test_端點_取安裝包會補最小資產並核發key():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        r = client.post("/api/collect/agent-package", json={"ip": "10.0.0.3"})
        assert r.status_code == 200
        body = r.json()
        assert body["created_asset"] is True
        assert body["asset_serial"] == "AUTO-10.0.0.3"
        assert body["files"]["agent_key"]
        # key 只存 hash，DB 裡查不到明文
        conn = db.get_connection(db_path)
        try:
            row = conn.execute("SELECT key_hash FROM host_api_key").fetchone()
            assert row["key_hash"] != body["files"]["agent_key"]
        finally:
            conn.close()
        api.app.dependency_overrides.clear()


def test_端點_未登入一律401():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        db.init_db(db_path)

        def _override_get_db():
            conn = db.get_connection(db_path)
            try:
                yield conn
            finally:
                conn.close()

        api.app.dependency_overrides[api.get_db] = _override_get_db
        client = TestClient(api.app)
        assert client.post("/api/collect/dispatch",
                           json={"targets": "10.0.0.1"}).status_code == 401
        assert client.get("/api/collect/dispatch/latest").status_code == 401
        assert client.post("/api/collect/agent-package",
                           json={"ip": "10.0.0.1"}).status_code == 401
        api.app.dependency_overrides.clear()
