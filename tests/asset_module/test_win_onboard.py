"""Windows 勾選才收（2026-10-22，win_onboard.py）。

使用者怎麼用：2-1 WinRM 頁簽，Windows 預設不登入、不收集；勾選按「納管」才登入＋驗本機管理員＋收一輪全部，
之後的開始收集（手動／排程）才包含它。「重新納管」再驗一次＋再收一輪；「取消納管」停止收集、資料保留。

這裡守的是那條承諾本身：**沒納管的 Windows，收集時連登入都不試**（注入的 winrm_runner 一次都不被呼叫）。
WinRM 一律注入假的——家裡連不到真的 Windows，真機驗收要在公司機做。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import auth  # noqa: E402
import collect_dispatch as cd  # noqa: E402
import db  # noqa: E402
import api  # noqa: E402
import win_onboard as wo  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_STUB_CREDENTIAL = "test-password-123"
WIN_IP = "10.99.7.20"
WIN_FACTS = "hostname=WINBOX01\nos=Windows Server 2019\nis_admin=1\nrun_as=LAB\\svc_scan"


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _seed(conn, ip=WIN_IP, serial="W-1", os_text="Windows Server 2019", hostname="WINBOX01"):
    db.insert_hardware(conn, asset_serial=serial, ip=ip, hostname=hostname, os=os_text,
                       environment="正式", asset_status="使用中")
    conn.commit()


def _prober(ip):
    return {WIN_IP: [445, 3389, 5985]}.get(ip)


class _Spy:
    """記下被呼叫幾次；可以設回什麼。"""
    def __init__(self, ret=WIN_FACTS):
        self.calls = []
        self.ret = ret

    def __call__(self, *a, **k):
        self.calls.append(a)
        if isinstance(self.ret, Exception):
            raise self.ret
        return self.ret


def _onboard(conn, ip=WIN_IP, runner=None, by="tester", key=None):
    q = []
    health = _Spy(ret="")
    out = wo.onboard_one(conn, ip, by=by, winrm_runner=runner or _Spy(), cred_key_path=key,
                         enqueue=lambda db_path, serials, **k: q.append((list(serials), k)),
                         health_runner=health)
    return out, q, health


# ===== 1. 沒納管：收集時不登入 =====

def test_沒納管的Windows_收集時一次都不登入():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn)
        spy = _Spy()
        out = cd.run_dispatch(conn, WIN_IP, prober=_prober, winrm_runner=spy)
        r = out["results"][0]
        assert spy.calls == [], "沒納管的 Windows 不可以被登入"
        assert r["route"] == cd.ROUTE_WINRM            # 探測照做：走哪條路照樣看得到
        assert r["status"] == cd.STATUS_WIN_PENDING
        assert r["message"] == "未納管：勾選後按納管"
        assert cd.STATUS_LABEL[cd.STATUS_WIN_PENDING] == "待納管"
        # 也不寫成已納管
        assert conn.execute("SELECT collect_ok FROM hardware WHERE asset_serial='W-1'").fetchone()[0] in (0, None)
        conn.close()


def test_沒納管的Windows_連憑證都不取():
    import credential_store

    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn)
        orig = credential_store.pick_for_host
        picked = []
        credential_store.pick_for_host = lambda *a, **k: picked.append(a) or None
        try:
            cd.run_dispatch(conn, WIN_IP, prober=_prober)
        finally:
            credential_store.pick_for_host = orig
        assert picked == []
        conn.close()


def test_查不到納管狀態時當作沒納管_不登入():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn)
        conn.execute("DROP TABLE win_onboard_state")
        spy = _Spy()
        out = cd.run_dispatch(conn, WIN_IP, prober=_prober, winrm_runner=spy)
        assert spy.calls == []
        assert out["results"][0]["status"] == cd.STATUS_WIN_PENDING
        conn.close()


# ===== 2. 納管：登入＋管理員＋收一輪全部 =====

def test_納管_登入記管理員_排進全部收集_健康快照():
    with tempfile.TemporaryDirectory() as tmp:
        import credential_store as cs

        conn = _conn(tmp)
        key = str(Path(tmp) / "cred.key")
        cs.save(conn, "win-lab", "winrm", "LAB\svc_scan", "FAKE-PW-not-real-W2", scope="10.99.", key_path=key)
        _seed(conn)
        spy = _Spy()
        out, q, health = _onboard(conn, runner=spy, key=key)
        assert out["ok"] is True and out["opted_in"] is True
        assert out["is_admin"] == 1
        assert len(spy.calls) == 1                        # 登入收 facts 一次
        assert q and q[0][0] == ["W-1"]                   # 全部收集（post_onboard 那一套）排進去了
        assert q[0][1].get("source") == "onboard"
        assert health.calls, "健康快照要收"
        assert conn.execute("SELECT COUNT(*) FROM health_snapshot WHERE ip=? AND source='onboard'",
                            (WIN_IP,)).fetchone()[0] == 1
        st = wo.lookup(conn, WIN_IP)
        assert st["opted_in"] == 1 and st["is_admin"] == 1 and st["onboarded_by"] == "tester"
        a = conn.execute("SELECT platform, trigger, ok, login_user FROM onboard_audit WHERE target_ip=?",
                         (WIN_IP,)).fetchall()
        assert [tuple(x) for x in a] == [("windows", "winrm", 1, "LAB\svc_scan")]
        assert "FAKE-PW-not-real-W2" not in repr(out)
        conn.close()


def test_納管後_收集會登入():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn)
        _onboard(conn)
        spy = _Spy()
        out = cd.run_dispatch(conn, WIN_IP, prober=_prober, winrm_runner=spy)
        assert len(spy.calls) == 1
        assert out["results"][0]["status"] == cd.STATUS_COLLECTED
        assert out["results"][0]["win"]["opted_in"] is True
        conn.close()


def test_納管後的全部收集_Windows每一樣都跑():
    """排進去的那一套就是 post_onboard（規格／服務＋IIS／軟體／帳號／排程／OS 重建／組態檢核）。
    這裡確認 Windows 這台在那一套裡沒有被略過的項目。"""
    import post_onboard

    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn)
        _onboard(conn)
        skipped = [j for j in post_onboard.JOBS if post_onboard._platform_skip(conn, j, "W-1")]
        assert skipped == [], skipped
        conn.close()


def test_重新納管_失敗不會把已納管取消():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn)
        _onboard(conn)
        out, q, _ = _onboard(conn, runner=_Spy(ret=ConnectionError("WinRM 認證失敗")))
        assert out["ok"] is False and out["action"] == "重新納管"
        assert "認證失敗" in out["message"]
        assert q == []
        assert wo.lookup(conn, WIN_IP)["opted_in"] == 1
        assert wo.lookup(conn, WIN_IP)["last_ok"] == 0
        conn.close()


def test_納管失敗_不記成已納管_沒登記的也不留資產():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        out, q, _ = _onboard(conn, runner=_Spy(ret=ConnectionError("連不上")))
        assert out["ok"] is False and out["opted_in"] is False
        assert q == []
        assert not wo.is_opted_in(conn, WIN_IP)
        assert conn.execute("SELECT COUNT(*) FROM hardware").fetchone()[0] == 0
        # 失敗也留紀錄（2-1「上次失敗」／納管失敗頁簽讀它）
        assert conn.execute("SELECT ok FROM onboard_audit WHERE target_ip=?", (WIN_IP,)).fetchone()[0] == 0
        conn.close()


def test_沒登記的納管成功_補最小資產讓資料有地方落():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        out, q, _ = _onboard(conn)
        assert out["ok"] is True
        assert q[0][0] == [f"AUTO-{WIN_IP}"]
        assert wo.is_opted_in(conn, WIN_IP)
        conn.close()


def test_VMware天條_判成ESXi的不登入():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn, os_text="VMware ESXi 8.0.3", hostname="esx01")
        spy = _Spy()
        out, q, health = _onboard(conn, runner=spy)
        assert out["ok"] is False and out["stage"] == "refused"
        assert spy.calls == [] and health.calls == [] and q == []
        assert not wo.is_opted_in(conn, WIN_IP)
        conn.close()


# ===== 3. 取消納管 =====

def test_取消納管_之後收集不再登入_資料保留():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn)
        _onboard(conn)
        res = wo.revoke(conn, WIN_IP, by="tester")
        assert res["ok"] is True
        row = conn.execute("SELECT collect_ok, collect_error, hostname FROM hardware WHERE asset_serial='W-1'").fetchone()
        assert row["collect_ok"] == 0 and "取消納管" in row["collect_error"]
        assert row["hostname"] == "WINBOX01"                 # 資料保留
        spy = _Spy()
        out = cd.run_dispatch(conn, WIN_IP, prober=_prober, winrm_runner=spy)
        assert spy.calls == []
        assert out["results"][0]["status"] == cd.STATUS_WIN_PENDING
        # 納管紀錄最後一筆是取消（2-1 不會再把舊的「已收集」疊回來）
        assert conn.execute("SELECT trigger FROM onboard_audit WHERE target_ip=? ORDER BY id DESC LIMIT 1",
                            (WIN_IP,)).fetchone()[0] == "revoke"
        conn.close()


def test_取消納管後_舊的已收集結果讀成待納管():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn)
        _onboard(conn)
        cd.run_dispatch(conn, WIN_IP, prober=_prober, winrm_runner=_Spy())   # 存一筆 collected
        wo.revoke(conn, WIN_IP, by="tester")
        latest = cd.latest_run(conn)
        r = [x for x in latest["results"] if x["ip"] == WIN_IP][0]
        assert r["status"] == cd.STATUS_WIN_PENDING
        assert "取消納管" in r["message"]
        conn.close()


# ===== 4. 上線前已收過的視為已納管 =====

def test_舊資料_已WinRM收成功過的視為已納管_只跑一次():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn)
        _seed(conn, ip="10.99.7.21", serial="W-2", hostname="WINBOX02")
        _seed(conn, ip="10.99.7.22", serial="W-3", hostname="WINBOX03")
        conn.execute("INSERT INTO collect_dispatch_run (id, status) VALUES (1, 'ok')")
        conn.execute("INSERT INTO collect_dispatch_result (run_id, ip, alive, route, status) "
                     "VALUES (1, ?, 1, 'winrm', 'collected')", (WIN_IP,))
        conn.execute("INSERT INTO collect_dispatch_result (run_id, ip, alive, route, status) "
                     "VALUES (1, '10.99.7.22', 1, 'winrm', 'failed')")
        conn.execute("INSERT INTO win_collect_session (ip, is_admin) VALUES ('10.99.7.21', 0)")
        conn.execute("DELETE FROM app_settings WHERE key = ?", (wo.GRANDFATHER_KEY,))
        conn.commit()
        assert wo.grandfather(conn) == 2
        assert wo.is_opted_in(conn, WIN_IP) and wo.is_opted_in(conn, "10.99.7.21")
        assert not wo.is_opted_in(conn, "10.99.7.22")          # 只失敗過的不算
        # 取消之後重跑（例如服務重啟）不會又被收回來
        wo.revoke(conn, WIN_IP, by="tester")
        assert wo.grandfather(conn) == 0
        assert not wo.is_opted_in(conn, WIN_IP)
        conn.close()


def test_舊資料_init_db會收進來():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        _seed(conn)
        conn.execute("INSERT INTO collect_dispatch_run (id, status) VALUES (1, 'ok')")
        conn.execute("INSERT INTO collect_dispatch_result (run_id, ip, alive, route, status) "
                     "VALUES (1, ?, 1, 'winrm', 'collected')", (WIN_IP,))
        conn.execute("DELETE FROM app_settings WHERE key = ?", (wo.GRANDFATHER_KEY,))
        conn.commit()
        conn.close()
        db.init_db(p)        # 升版後服務啟動
        conn = db.get_connection(p)
        assert wo.is_opted_in(conn, WIN_IP)
        conn.close()


# ===== 5. 裝了 OpenSSH 的 Windows（22 也開）=====

def test_22也開的Windows_Windows納管過就走WinRM():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn)
        _onboard(conn)
        spy = _Spy()
        ssh = _Spy(ret=(False, "Permission denied"))
        out = cd.run_dispatch(conn, WIN_IP, prober=lambda ip: [22, 445, 5985],
                              winrm_runner=spy, ssh_prober=ssh)
        assert out["results"][0]["route"] == cd.ROUTE_WINRM
        assert len(spy.calls) == 1 and ssh.calls == []
        conn.close()


def test_22也開但沒Windows納管_照舊走SSH_不登WinRM():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn)
        spy = _Spy()
        out = cd.run_dispatch(conn, WIN_IP, prober=lambda ip: [22, 445, 5985],
                              winrm_runner=spy, ssh_prober=lambda ip: (False, "Permission denied"))
        assert out["results"][0]["route"] == cd.ROUTE_SSH
        assert spy.calls == []
        conn.close()


# ===== 6. 權限面板 =====

def test_權限面板_帳號只有名字_群組讀設定_管理員是否分得出():
    import credential_store as cs

    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        key = str(Path(tmp) / "cred.key")
        cs.save(conn, "win-lab", "winrm", "LAB\\svc_scan", "FAKE-PW-not-real-W1", scope="10.99.", key_path=key)
        _seed(conn)
        _onboard(conn, key=key)
        p = wo.permissions(conn)
        assert "FAKE-PW-not-real-W1" not in repr(p)
        assert p["accounts"][0]["username"] == "LAB\\svc_scan"
        assert p["admin_group"] == ""                       # 沒設定就空白（組織名稱不寫死在程式）
        db.set_setting(conn, wo.ADMIN_GROUP_KEY, "GRP-EXAMPLE")
        assert wo.permissions(conn)["admin_group"] == "GRP-EXAMPLE"
        assert p["admin_yes"] == 1 and p["admin_no"] == 0
        assert any("執行原則" in n for n in p["never"])
        assert {r["item"] for r in p["reads"]} >= {"services", "software", "users"}
        conn.close()


# ===== 7. 端點 =====

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
    return client, db_path


def test_端點_都要登入():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _client(tmp)
        try:
            assert client.get("/api/onboard/windows/state").status_code == 401
            assert client.get("/api/onboard/windows/permissions").status_code == 401
            assert client.post("/api/onboard/windows", json={"ip": WIN_IP}).status_code == 401
            assert client.post("/api/onboard/windows/revoke",
                               json={"ip": WIN_IP, "confirm": "YES"}).status_code == 401
        finally:
            api.app.dependency_overrides.clear()


def test_端點_取消納管要確認_IP格式錯回400():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            assert client.post("/api/auth/login", json={"username": "tester",
                               "password": _STUB_CREDENTIAL}).status_code == 200
            assert client.post("/api/onboard/windows/revoke", json={"ip": WIN_IP}).status_code == 400
            assert client.post("/api/onboard/windows", json={"ip": "不是IP"}).status_code == 400
            conn = db.get_connection(db_path)
            _seed(conn)
            _onboard(conn)
            conn.close()
            st = client.get("/api/onboard/windows/state", params={"ips": WIN_IP}).json()["items"]
            assert st[WIN_IP]["opted_in"] is True
            assert client.get("/api/onboard/windows/state").json()["items"][WIN_IP]["is_admin"] == 1
            r = client.post("/api/onboard/windows/revoke", json={"ip": WIN_IP, "confirm": "YES"})
            assert r.status_code == 200 and r.json()["ok"] is True
            assert client.get("/api/onboard/windows/permissions").status_code == 200
        finally:
            api.app.dependency_overrides.clear()


# ===== 8. 獨立審查 2026-10-22：IP 回收／退役／整台取消／收集器第二道／D3 =====

def _dispatch_rows(conn, ip):
    conn.execute("INSERT INTO collect_dispatch_run (id, status) VALUES (1, 'ok')")
    conn.execute("INSERT INTO collect_dispatch_result (run_id, ip, alive, open_ports, route, status) "
                 "VALUES (1, ?, 1, '445,5985', 'winrm', 'collected')", (ip,))
    conn.commit()


def test_IP回收_上線前用IP記的已納管_IP換給別台後不算():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _dispatch_rows(conn, WIN_IP)
        conn.execute("DELETE FROM app_settings WHERE key = ?", (wo.GRANDFATHER_KEY,))
        conn.commit()
        assert wo.grandfather(conn) == 1                       # 那時沒登記：用 ip: 記
        assert wo.is_opted_in(conn, WIN_IP)
        _seed(conn, serial="NEW-B", hostname="NEWHOST-B")      # 之後這個 IP 登記成另一台
        assert not wo.is_opted_in(conn, WIN_IP)
        spy = _Spy()
        out = cd.run_dispatch(conn, WIN_IP, prober=_prober, winrm_runner=spy)
        assert spy.calls == [] and out["results"][0]["status"] == cd.STATUS_WIN_PENDING
        conn.close()


def test_IP回收_同IP換了序號不同的另一台_不算已納管():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn, serial="OLD-A", hostname="OLDHOST-A")
        _onboard(conn)
        assert wo.is_opted_in(conn, WIN_IP)
        conn.execute("UPDATE hardware SET asset_status = '報廢' WHERE asset_serial = 'OLD-A'")
        _seed(conn, serial="NEW-B", hostname="NEWHOST-B")
        assert not wo.is_opted_in(conn, WIN_IP)
        assert WIN_IP not in wo.state_map(conn, [WIN_IP])
        conn.close()


def test_登記退役_不算已納管_2_1講請重新納管_重新納管後可收():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn, serial="OLD-A", hostname="OLDHOST-A")
        _onboard(conn)
        conn.execute("UPDATE hardware SET asset_status = '報廢' WHERE asset_serial = 'OLD-A'")
        conn.commit()
        assert not wo.is_opted_in(conn, WIN_IP)
        spy = _Spy()
        out = cd.run_dispatch(conn, WIN_IP, prober=_prober, winrm_runner=spy)
        r = out["results"][0]
        assert spy.calls == [] and r["status"] == cd.STATUS_WIN_PENDING
        assert r["message"] == wo.RETIRED_MESSAGE == "登記已退役，請重新納管"
        # 重新納管：補一筆新的現役登記（不寫到退役那筆），之後算已納管
        res, q, _ = _onboard(conn)
        assert res["ok"] is True and res["asset_serial"].startswith(f"AUTO-{WIN_IP}")
        assert wo.is_opted_in(conn, WIN_IP)
        assert conn.execute("SELECT asset_status FROM hardware WHERE asset_serial='OLD-A'").fetchone()[0] == "報廢"
        conn.close()


def test_登記退役_合併檢視讀取也講請重新納管():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn, serial="OLD-A", hostname="OLDHOST-A")
        _onboard(conn)
        cd.run_dispatch(conn, WIN_IP, prober=_prober, winrm_runner=_Spy())     # 存一筆 collected
        conn.execute("UPDATE hardware SET asset_status = '報廢' WHERE asset_serial = 'OLD-A'")
        conn.commit()
        lr = [x for x in cd.latest_run(conn)["results"] if x["ip"] == WIN_IP][0]
        assert lr["status"] == cd.STATUS_WIN_PENDING
        assert lr["message"].startswith(wo.RETIRED_MESSAGE)
        conn.close()


def _two_ip_machine(conn):
    import machine_identity

    db.set_setting(conn, "machine_key_mode", "hostname")
    machine_identity.refresh(conn)
    _seed(conn, ip=WIN_IP, serial="A1", hostname="WINMULTI01")
    _seed(conn, ip="10.99.7.31", serial="A2", hostname="WINMULTI01")


def test_取消納管_整台每筆登記都停_兄弟登記的收集器也不登入():
    import manage_state
    import service_inventory
    import software_inventory
    import account_inventory
    import credential_store

    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _two_ip_machine(conn)
        _onboard(conn, runner=_Spy(ret=WIN_FACTS.replace("WINBOX01", "WINMULTI01")))
        import machine_identity
        machine_identity.refresh(conn)     # 口徑是行程層級的狀態，中途別的連線可能重設過
        assert set(manage_state.same_machine_serials(conn, "A1")) == {"A1", "A2"}
        assert wo.is_opted_in(conn, "10.99.7.31")              # 同一台的另一個 IP 也算
        manage_state.mark_collect_ok(conn, "A1", "2026-10-22 10:00:00")
        conn.commit()
        assert wo.revoke(conn, WIN_IP, by="tester")["ok"]
        rows = {r[0]: r[1] for r in conn.execute("SELECT asset_serial, collect_ok FROM hardware")}
        assert rows == {"A1": 0, "A2": 0}
        picked = []
        orig = credential_store.pick_for_host
        credential_store.pick_for_host = lambda *a, **k: picked.append(a) or None
        try:
            for fn in (software_inventory._collect_windows, service_inventory._collect_windows_services,
                       account_inventory._collect_windows_accounts, manage_state._collect_windows):
                failed = []
                assert fn(conn, "10.99.7.31", "A2", failed) is None
                assert failed and failed[0]["error"] == wo.NOT_OPTED_COLLECT, fn.__name__
        finally:
            credential_store.pick_for_host = orig
        assert picked == [], "沒納管的不可以連憑證都去取"
        conn.close()


def test_收集器第二道_規格_OS重建_排程_沒納管不登入():
    import credential_store
    import schedule_inventory
    import win_host_spec
    import win_rebuild_collector

    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn)
        conn.execute("UPDATE hardware SET collect_ok = 1 WHERE asset_serial = 'W-1'")
        conn.commit()
        picked = []
        orig = credential_store.pick_for_host
        credential_store.pick_for_host = lambda *a, **k: picked.append(a) or None
        try:
            assert win_host_spec.collect_and_store(conn, "W-1", WIN_IP) == {
                "ok": False, "error": wo.NOT_OPTED_COLLECT}
            assert win_rebuild_collector.collect_for(conn, "W-1", WIN_IP) == (None, wo.NOT_OPTED_COLLECT)
            r = schedule_inventory.collect_windows(conn, only_serial="W-1")
            assert wo.NOT_OPTED_COLLECT in repr(r), r
        finally:
            credential_store.pick_for_host = orig
        assert picked == []
        conn.close()


def test_週健康基準_沒納管的Windows不登入_納管的照收():
    import health_baseline

    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn)
        _seed(conn, ip="10.99.7.21", serial="W-2", hostname="WINBOX02")
        _onboard(conn, ip="10.99.7.21")
        conn.execute("UPDATE hardware SET collect_ok = 1")
        conn.commit()
        seen = []
        health_baseline.run_weekly_baseline(
            conn, _check_batch=lambda ips, **k: seen.extend(ips) or [])
        assert seen == ["10.99.7.21"]
        conn.close()


def test_Windows納管失敗不會把SSH那一列講成納管失敗():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn)
        _onboard(conn, runner=_Spy(ret=ConnectionError("401 unauthorized")))
        out = cd.run_dispatch(conn, WIN_IP, prober=lambda ip: [22],
                              ssh_prober=lambda ip: (False, "Permission denied (publickey)"))
        assert out["results"][0]["status"] == cd.STATUS_NEEDS_CREDENTIAL
        conn.close()


def test_納管失敗清單_含Windows_筆數等於匯出_拒絕的不列():
    import onboard_failures as of

    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn)
        _seed(conn, ip="10.99.7.40", serial="ESX-1", os_text="VMware ESXi 8.0.3", hostname="esx01")
        _onboard(conn, runner=_Spy(ret=ConnectionError("401 unauthorized")))
        r2, _, _ = _onboard(conn, ip="10.99.7.40")
        assert r2["stage"] == "refused"
        items = of.current_failures(conn, elsewhere={})
        assert [(i["ip"], i["platform"]) for i in items] == [(WIN_IP, "windows")]
        assert of.summary(conn, elsewhere={})["total"] == len(items) == len(of.export_rows(conn))
        conn.close()


def test_直接打API也擋_另有納管方式與人工指定():
    import os_override

    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn, os_text="NetApp ONTAP 9.8", hostname="nas01")
        spy = _Spy()
        out, _, _ = _onboard(conn, runner=spy)
        assert out["stage"] == "refused" and spy.calls == [] and "另有納管方式" in out["message"]
        # 人看過、指定成 WinRM 收集 → 以人為準
        os_override.set_override(conn, "nas01", WIN_IP, "W-1", cd.os_type_for_tab(cd.TAB_WINRM),
                                 None, "測試", "tester")
        out, _, _ = _onboard(conn, runner=spy)
        assert out["ok"] is True and len(spy.calls) == 1
        # 人工指定成儲存設備 → 擋
        _seed(conn, ip="10.99.7.41", serial="W-9", hostname="WIN09")
        os_override.set_override(conn, "WIN09", "10.99.7.41", "W-9", cd.os_type_for_tab(cd.TAB_STORAGE),
                                 None, "測試", "tester")
        spy2 = _Spy()
        out, _, _ = _onboard(conn, ip="10.99.7.41", runner=spy2)
        assert out["stage"] == "refused" and spy2.calls == []
        conn.close()


def test_VMware_人工指定成WinRM也照擋():
    import os_override

    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn, os_text="VMware ESXi 8.0.3", hostname="esx01")
        os_override.set_override(conn, "esx01", WIN_IP, "W-1", cd.os_type_for_tab(cd.TAB_WINRM),
                                 None, "測試", "tester")
        spy = _Spy()
        out, _, _ = _onboard(conn, runner=spy)
        assert out["stage"] == "refused" and spy.calls == []
        conn.close()


def test_init_db裡的舊資料轉換失敗不擋啟動_下次重試(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        conn.execute("DELETE FROM app_settings WHERE key = ?", (wo.GRANDFATHER_KEY,))
        conn.commit()
        conn.close()

        def boom(_c):
            raise RuntimeError("壞了")
        monkeypatch.setattr(wo, "grandfather", boom)
        db.init_db(p)                                         # 不炸
        conn = db.get_connection(p)
        assert not db.get_setting(conn, wo.GRANDFATHER_KEY)   # 沒記成跑過 → 下次重試
        conn.close()


def test_待納管讀取_上千列不逐列認台():
    """3000 列 WinRM（300 台納管中）：讀取時的疊加與狀態要在 1 秒內（審查要求量測）。"""
    import time

    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        conn.execute("INSERT INTO collect_dispatch_run (id, status) VALUES (1, 'ok')")
        for i in range(3000):
            ip = f"10.99.{i // 250}.{i % 250 + 1}"
            db.insert_hardware(conn, asset_serial=f"S{i}", ip=ip, hostname=f"WIN{i:05d}", os="Windows Server 2019")
            conn.execute("INSERT INTO collect_dispatch_result (run_id, ip, alive, open_ports, route, status, message) "
                         "VALUES (1, ?, 1, '445,5985', 'winrm', 'collected', 'x')", (ip,))
            if i % 10 == 0:
                wo._upsert(conn, ip, wo._asset_for_ip(conn, ip), opted_in=1)
        conn.commit()
        rs = cd._merged_results(conn, None)
        t0 = time.perf_counter()
        n = cd._overlay_win_optin(conn, rs)
        cd._attach_win(conn, rs)
        took = time.perf_counter() - t0
        assert n == 2700
        assert sum(1 for r in rs if (r.get("win") or {}).get("opted_in")) == 300
        assert took < 1.0, took
        conn.close()


def test_組態檢核_沒納管的Windows不登入_連憑證都不取(monkeypatch):
    import config_audit
    import credential_store
    import win_fcb

    monkeypatch.setattr(win_fcb, "enabled", lambda _c: True)
    picked = []
    monkeypatch.setattr(credential_store, "pick_for_host", lambda *a, **k: picked.append(a) or None)
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn)
        row = dict(conn.execute("SELECT * FROM hardware WHERE asset_serial='W-1'").fetchone())
        try:
            config_audit._collect_windows(conn, row, WIN_IP, "W-1")
        except ValueError as exc:
            assert str(exc) == wo.NOT_OPTED_COLLECT
        else:
            raise AssertionError("沒納管的 Windows 應該被擋下")
        assert picked == []
        conn.close()
