"""POST /api/onboard/verify：本機執行納管指令跑完後立即試連驗證。
覆蓋：成功回 hostname/os、失敗回可行動的原因、沒有資產記錄時自動補一筆最小記錄。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import auth  # noqa: E402
import db  # noqa: E402
import api  # noqa: E402
import manage_state  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


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
    return TestClient(api.app), db_path


def _login(client, db_path):
    conn = db.get_connection(db_path)
    try:
        db.create_user(conn, "tester", auth.hash_password("s3cure-pass!"))
    finally:
        conn.close()
    resp = client.post("/api/auth/login", json={"username": "tester", "password": "s3cure-pass!"})
    assert resp.status_code == 200


def test_verify_without_login_is_401():
    with tempfile.TemporaryDirectory() as tmp:
        client, _db_path = _client(tmp)
        try:
            resp = client.post("/api/onboard/verify", json={"ip": "10.0.0.5", "platform": "linux"})
            assert resp.status_code == 401
        finally:
            api.app.dependency_overrides.clear()


def test_verify_success_returns_hostname_and_os(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)

            def fake_collect(conn, only_serial=None, **kw):
                conn.execute(
                    "UPDATE hardware SET hostname = ?, os = ? WHERE asset_serial = ?",
                    ("AP01", "Ubuntu 22.04", only_serial),
                )
                conn.commit()
                return {"updated": 1, "failed": [], "candidates": 1}

            monkeypatch.setattr(manage_state, "collect_facts_into_assets", fake_collect)

            resp = client.post("/api/onboard/verify", json={"ip": "10.0.0.5", "platform": "linux"})
            assert resp.status_code == 200
            body = resp.json()
            assert body["ok"] is True
            assert body["hostname"] == "AP01"
            assert body["os"] == "Ubuntu 22.04"

            # 沒有預先建資產記錄，端點要自動補一筆最小記錄
            conn = db.get_connection(db_path)
            try:
                row = conn.execute("SELECT * FROM hardware WHERE ip = '10.0.0.5'").fetchone()
            finally:
                conn.close()
            assert row is not None
            assert row["hostname"] == "AP01"
        finally:
            api.app.dependency_overrides.clear()


def test_verify_failure_returns_actionable_error(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)

            def fake_collect(conn, only_serial=None, **kw):
                return {
                    "updated": 0,
                    "failed": [{"asset_serial": only_serial, "error": "沒有可用的收集憑證"}],
                    "candidates": 1,
                }

            monkeypatch.setattr(manage_state, "collect_facts_into_assets", fake_collect)

            resp = client.post("/api/onboard/verify", json={"ip": "10.0.0.9", "platform": "windows"})
            assert resp.status_code == 200
            body = resp.json()
            assert body["ok"] is False
            assert "沒有可用的收集憑證" in body["error"]
        finally:
            api.app.dependency_overrides.clear()


def test_verify_rejects_unknown_platform():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            resp = client.post("/api/onboard/verify", json={"ip": "10.0.0.5", "platform": "solaris"})
            assert resp.status_code == 400
        finally:
            api.app.dependency_overrides.clear()


# ── 2026-10-27（deploy E2E 抓到）：新建的 AUTO-<ip> 還不是 collect_ok=1，驗證卻要求 collect_ok=1 才收 → 永遠失敗 ──
def _real_collect_env(monkeypatch, calls):
    """用真的 collect_facts_into_assets，只把連線那層換成假的（不碰網路）。"""
    import collect_account_migration
    import facts_collector
    import post_onboard

    monkeypatch.setattr(collect_account_migration, "settle_after_onboard", lambda c, ip, plat: (calls.append(("settle", ip)) or {}))
    monkeypatch.setattr(post_onboard, "start", lambda *a, **k: None)
    monkeypatch.setattr(manage_state, "collect_platform_of", lambda *a, **k: "linux")
    monkeypatch.setattr(manage_state, "_runner_for", lambda *a, **k: (lambda cmd: ""))
    monkeypatch.setattr(manage_state, "_save_trace", lambda *a, **k: None)

    def fake_facts(run, ip, platform):
        calls.append(("collect", ip))
        return {"hostname": "e2e01", "os": "Rocky Linux 9.4"}
    monkeypatch.setattr(facts_collector, "collect", fake_facts)


def test_verify_新AUTO登記_收集成功才標已納管(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            calls = []
            _real_collect_env(monkeypatch, calls)
            body = client.post("/api/onboard/verify", json={"ip": "10.99.6.21", "platform": "linux"}).json()
            assert body["ok"] is True, body
            assert ("collect", "10.99.6.21") in calls
            conn = db.get_connection(db_path)
            try:
                r = conn.execute("SELECT * FROM hardware WHERE asset_serial = 'AUTO-10.99.6.21'").fetchone()
            finally:
                conn.close()
            assert r["collect_ok"] == 1 and r["hostname"] == "e2e01"
        finally:
            api.app.dependency_overrides.clear()


def test_collect指名_不要求collect_ok_但擋VMware與退役(monkeypatch):
    calls = []
    _real_collect_env(monkeypatch, calls)
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        try:
            conn.execute("INSERT INTO hardware (asset_serial, ip) VALUES ('AUTO-10.99.6.31', '10.99.6.31')")
            conn.execute("INSERT INTO hardware (asset_serial, ip, os) VALUES ('E-9', '10.99.6.32', 'VMware ESXi 8.0.3')")
            conn.commit()
            r = manage_state.collect_facts_into_assets(conn, only_serial="AUTO-10.99.6.31")
            assert r["updated"] == 1 and r["candidates"] == 1
            r = manage_state.collect_facts_into_assets(conn, only_serial="E-9")
            assert r["updated"] == 0 and r["failed"][0]["skipped"] == "vmware"
            assert ("collect", "10.99.6.32") not in calls, "VMware 被收集了（天條）"
            r = manage_state.collect_facts_into_assets(conn, only_serial="NOPE")
            assert r["failed"][0]["skipped"] == "no_row" and "沒有" in r["failed"][0]["error"]
            # 不指名（全機隊）照舊只收 collect_ok=1
            assert manage_state.collect_facts_into_assets(conn)["candidates"] == 0
        finally:
            conn.close()


def test_verify_VMware不試連不收集(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            conn = db.get_connection(db_path)
            conn.execute("INSERT INTO hardware (asset_serial, hostname, ip, os) VALUES ('E-8','esx08','10.99.6.41','VMware ESXi 7.0')")
            conn.commit()
            conn.close()
            calls = []
            _real_collect_env(monkeypatch, calls)
            body = client.post("/api/onboard/verify", json={"ip": "10.99.6.41", "platform": "linux"}).json()
            assert body["ok"] is False and "VMware" in body["error"]
            assert calls == [], f"VMware 被試連／收集：{calls}"
            c = db.get_connection(db_path)
            try:   # 審查 N2：VMware 不留空殼 AUTO 登記（擋在補登記之前）
                assert c.execute("SELECT COUNT(*) FROM hardware WHERE asset_serial LIKE 'AUTO-%'").fetchone()[0] == 0
            finally:
                c.close()
        finally:
            api.app.dependency_overrides.clear()


# ── 審查（c8b01009）N1／N3 與全機隊收集的天條 ─────────────────────────────
def _db(tmp, rows):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    for r in rows:
        cols = ",".join(r)
        c.execute(f"INSERT INTO hardware ({cols}) VALUES ({','.join('?' for _ in r)})", tuple(r.values()))
    c.commit()
    return c


def test_同IP另一筆是VMware_空白AUTO登記也不收(monkeypatch):
    calls = []
    _real_collect_env(monkeypatch, calls)
    with tempfile.TemporaryDirectory() as tmp:
        conn = _db(tmp, [{"asset_serial": "C-1", "hostname": "esx11", "ip": "10.99.6.51", "os": "VMware ESXi 7"},
                         {"asset_serial": "AUTO-10.99.6.51", "ip": "10.99.6.51"}])
        try:
            r = manage_state.collect_facts_into_assets(conn, only_serial="AUTO-10.99.6.51")
            assert r["failed"][0]["skipped"] == "vmware" and calls == []
        finally:
            conn.close()


def test_退役的指名目標不收(monkeypatch):
    calls = []
    _real_collect_env(monkeypatch, calls)
    status = next(iter(manage_state.RETIRED_STATUS))
    with tempfile.TemporaryDirectory() as tmp:
        conn = _db(tmp, [{"asset_serial": "R-1", "ip": "10.99.6.61", "asset_status": status}])
        try:
            r = manage_state.collect_facts_into_assets(conn, only_serial="R-1")
            assert r["failed"][0]["skipped"] == "retired" and calls == []
        finally:
            conn.close()


def test_連上後偵測到VMware_facts指令一個都不送(monkeypatch):
    import onboard_engine

    _real_collect_env(monkeypatch, [])
    import facts_collector
    monkeypatch.undo()          # 用真的 facts_collector.collect，只換 runner
    monkeypatch.setattr(manage_state, "collect_platform_of", lambda *a, **k: "linux")
    monkeypatch.setattr(manage_state, "_save_trace", lambda *a, **k: None)
    sent = []

    def runner(host, cmd):
        sent.append(cmd)
        return onboard_engine.VMWARE_REFUSED_MARKER if "__WEBIT_NOT_VMWARE__" in cmd else "x"
    monkeypatch.setattr(manage_state, "_runner_for", lambda *a, **k: runner)
    with tempfile.TemporaryDirectory() as tmp:
        conn = _db(tmp, [{"asset_serial": "AUTO-10.99.6.71", "ip": "10.99.6.71"}])
        try:
            r = manage_state.collect_facts_into_assets(conn, only_serial="AUTO-10.99.6.71")
            assert r["updated"] == 0 and r["failed"][0]["skipped"] == "vmware_on_target"
            assert r["failed"][0]["error"] == facts_collector.VMWARE_ON_TARGET
            assert len(sent) == 1, f"偵測到 VMware 之後還送了 facts 指令：{sent[1:]}"
        finally:
            conn.close()


def test_全機隊收集不收天條前已納管的ESXi(monkeypatch):
    calls = []
    _real_collect_env(monkeypatch, calls)
    with tempfile.TemporaryDirectory() as tmp:
        conn = _db(tmp, [{"asset_serial": "E-1", "ip": "10.99.6.81", "os": "VMware ESXi 7", "collect_ok": 1},
                         {"asset_serial": "L-1", "ip": "10.99.6.82", "os": "Red Hat Enterprise Linux 9", "collect_ok": 1}])
        try:
            r = manage_state.collect_facts_into_assets(conn)
            assert [c for c in calls if c[0] == "collect"] == [("collect", "10.99.6.82")]
            assert any(f.get("skipped") == "vmware" and f["asset_serial"] == "E-1" for f in r["failed"])
            import account_inventory
            import service_inventory
            import software_inventory
            assert {h["asset_serial"] for h in account_inventory._targets(conn)} == {"L-1"}
            assert {h["asset_serial"] for h in service_inventory._resolve_targets(conn)} == {"L-1"}
            assert {h["asset_serial"] for h in software_inventory._resolve_targets(conn)} == {"L-1"}
            hosts = {h["asset_serial"]: h for h in account_inventory.list_collectable_hosts(conn)}
            assert hosts["E-1"]["excluded"] and hosts["E-1"]["vmware"] and not hosts["L-1"]["excluded"]
        finally:
            conn.close()


def test_verify_收集時還不是已納管_跳過的講原因不探uname(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path)
            seen = []
            calls = []
            _real_collect_env(monkeypatch, calls)
            import facts_collector

            def fake_platform(conn, ip, *a, **k):
                # 用端點自己那條連線看（未提交的改動也看得到）：收集當下 collect_ok 不可以已經是 1
                seen.append(conn.execute("SELECT collect_ok FROM hardware WHERE ip = ?", (ip,)).fetchone()[0])
                return "linux"
            monkeypatch.setattr(manage_state, "collect_platform_of", fake_platform)
            monkeypatch.setattr(facts_collector, "collect", lambda run, ip, platform: {"hostname": "e2e02"})
            c0 = db.get_connection(db_path)                     # 已存在、collect_ok 空白的登記（已 commit，看得到）
            c0.execute("INSERT INTO hardware (asset_serial, ip) VALUES ('AUTO-10.99.6.91', '10.99.6.91')")
            c0.commit()
            c0.close()
            assert client.post("/api/onboard/verify", json={"ip": "10.99.6.91", "platform": "linux"}).json()["ok"]
            assert seen == [None], f"收集當下 collect_ok 應該還是空白（不可以先標成已納管）：{seen}"
            # 跳過（例：退役）→「收集未執行：原因」，而且不再 SSH 探 uname
            probes = []
            monkeypatch.setattr(manage_state, "probe_uname", lambda ip: probes.append(ip) or "Linux")
            monkeypatch.setattr(manage_state, "collect_facts_into_assets", lambda conn, only_serial=None, **k: {
                "updated": 0, "candidates": 0, "failed": [{"asset_serial": only_serial, "skipped": "retired", "error": "X 已退役，不收集"}]})
            body = client.post("/api/onboard/verify", json={"ip": "10.99.6.92", "platform": "linux"}).json()
            assert body["ok"] is False and body["error"].startswith("收集未執行：X 已退役") and probes == []
        finally:
            api.app.dependency_overrides.clear()


def test_只有IP的AUTO登記_反解名稱在vCenter_ESXi名單也不收(monkeypatch):
    """審查探針 c4：vCenter 名單寫 FQDN、資產庫只有 IP 的 AUTO 登記——用 PTR 反解出的名稱去比。"""
    import json
    import socket

    calls = []
    _real_collect_env(monkeypatch, calls)
    monkeypatch.setattr(socket, "gethostbyaddr", lambda ip: ("esx6.corp.example.com", [], [ip]) if ip == "10.99.6.96" else (_ for _ in ()).throw(socket.herror()))
    with tempfile.TemporaryDirectory() as tmp:
        conn = _db(tmp, [{"asset_serial": "AUTO-10.99.6.96", "ip": "10.99.6.96"}])
        try:
            conn.execute("INSERT INTO source_record (source, payload) VALUES ('vcenter', ?)",
                         (json.dumps({"esxi_host": "esx6.corp.example.com"}),))
            conn.commit()
            r = manage_state.collect_facts_into_assets(conn, only_serial="AUTO-10.99.6.96")
            assert r["failed"][0]["skipped"] == "vmware" and calls == []
        finally:
            conn.close()
