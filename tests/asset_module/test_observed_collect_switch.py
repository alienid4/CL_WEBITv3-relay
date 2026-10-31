"""連線收集的總開關／保留天數／本輪摘要（2026-10-29，拓撲資料缺口）。

連線本來就跟服務盤點同一次往返收（ss／netstat／Get-NetTCPConnection），這裡釘住新增的三件事：
- 總開關 observed_conn_collect（預設開）：關掉＝指令不含 ESTAB、Windows 走只問 Listen 的腳本、不存任何連線
- 保留天數 observed_conn_retention_days（預設 60）：每輪結尾刪掉過期的列
- 本輪摘要 result["connections"]；涵蓋率多了「伺服器分母」
- 沒有新增任何提權／base64／Bypass；VMware 設備仍被擋
例子 IP 一律 10.99.x。
"""
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import manage_state  # noqa: E402
import observed_deps as od  # noqa: E402
import service_collector as sc  # noqa: E402
import service_inventory  # noqa: E402
import winrm_collector  # noqa: E402

SS_OUT = """LISTEN 0      128          0.0.0.0:22         0.0.0.0:*
LISTEN 0      128          0.0.0.0:1521       0.0.0.0:*
ESTAB  0      0         10.99.1.10:1521    10.99.2.20:40001
ESTAB  0      0         10.99.1.10:51000   10.99.5.5:443
ESTAB  0      0          127.0.0.1:1521     127.0.0.1:40009
"""
AIX_OUT = """f1000e0001d0b3b8 tcp4       0      0  *.22               *.*                LISTEN
f1000e0002a1c3b8 tcp4       0      0  10.99.1.30.1521    10.99.2.40.33211   ESTABLISHED
"""


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.set_setting(c, "collector_ip", "10.99.200.1")
    return c


def _linux_env(monkeypatch, out=SS_OUT):
    cmds = []

    def runner(host, cmd):
        cmds.append(cmd)
        return out if "ss -tan" in cmd or "netstat -Aan" in cmd else ""

    monkeypatch.setattr(service_inventory, "_platform_for", lambda *a, **k: "linux")
    return runner, cmds


def test_預設開_收到連線並存下_有本輪摘要(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    db.insert_hardware(conn, asset_serial="L-1", hostname="app01", ip="10.99.1.10", collect_ok=1)
    conn.commit()
    runner, cmds = _linux_env(monkeypatch)
    out = service_inventory.collect_services(conn, runner=runner)
    assert any("estab" in c for c in cmds)
    s = out["connections"]
    assert s["enabled"] is True and s["hosts"] == 1 and s["added"] == 2 and s["retention_days"] == 60
    assert conn.execute("SELECT COUNT(*) FROM host_connection").fetchone()[0] == 2


def test_總開關關掉_指令不含ESTAB_也不存任何連線(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    db.set_setting(conn, od.COLLECT_KEY, "0")
    db.insert_hardware(conn, asset_serial="L-1", hostname="app01", ip="10.99.1.10", collect_ok=1)
    conn.commit()
    runner, cmds = _linux_env(monkeypatch)
    out = service_inventory.collect_services(conn, runner=runner)
    assert cmds and not any("estab" in c.lower() for c in cmds), "關掉時 grep 只留 listen"
    assert out["connections"]["enabled"] is False and out["connections"]["hosts"] == 0
    assert conn.execute("SELECT COUNT(*) FROM host_connection").fetchone()[0] == 0
    assert out["hosts"][0]["found"] == 2, "服務盤點本身不受影響"


def test_總開關關掉_就算主機回了ESTAB也不存(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    db.set_setting(conn, od.COLLECT_KEY, "off")
    db.insert_hardware(conn, asset_serial="L-1", hostname="app01", ip="10.99.1.10", collect_ok=1)
    conn.commit()
    monkeypatch.setattr(service_inventory, "_platform_for", lambda *a, **k: "linux")
    service_inventory.collect_services(conn, runner=lambda h, c: SS_OUT)   # 不管指令，一律回含 ESTAB 的輸出
    assert conn.execute("SELECT COUNT(*) FROM host_connection").fetchone()[0] == 0


def test_AIX指令兩種模式(tmp_path):
    on = sc.AIX_CMDS["listen"]
    off = sc.listen_only_cmd(on)
    assert "estab" in on and "estab" not in off.lower() and "netstat -Aan" in off
    assert "estab" not in sc.listen_only_cmd(sc.LINUX_CMDS["listen"]).lower()


def test_AIX解析_ESTABLISHED排除LISTEN():
    rows = sc.parse_connections(AIX_OUT)
    assert [(r["local_port"], r["peer_ip"], r["peer_port"]) for r in rows] == [(1521, "10.99.2.40", 33211)]


def test_Windows腳本兩種模式():
    on, off = winrm_collector.SERVICES_PS, winrm_collector.SERVICES_PS_NO_CONN
    assert "-State Listen,Established" in on and "cap = 20000" in on
    assert "Established" not in off.split("function W3Ep")[1].split("$es = @{")[0]
    assert "-State Listen " in off and "cap = 0" in off and "ESTAB_CAP_HERE" not in off
    assert off.endswith(winrm_collector.IIS_PS) and "@@IIS" in off


def test_Windows開關關掉用NO_CONN腳本(tmp_path, monkeypatch):
    import credential_store
    import win_onboard

    conn = _conn(tmp_path)
    db.set_setting(conn, od.COLLECT_KEY, "0")
    db.insert_hardware(conn, asset_serial="W-1", hostname="win01", ip="10.99.40.10", collect_ok=1,
                       os="Microsoft Windows Server 2019 Standard")
    conn.commit()
    seen = []
    monkeypatch.setattr(win_onboard, "collect_block", lambda *a, **k: None)
    monkeypatch.setattr(credential_store, "pick_for_host", lambda *a, **k: "win-svc")
    monkeypatch.setattr(credential_store, "get_for_use", lambda *a, **k: ("svc", "FAKE-TEST-PW-not-real-1357"))
    monkeypatch.setattr(credential_store, "audit_use", lambda *a, **k: None)
    monkeypatch.setattr(service_inventory, "_platform_for", lambda *a, **k: "windows")
    monkeypatch.setattr(winrm_collector, "_run",
                        lambda host, u, p, script, timeout, runner=None, deadline_s=None:
                        seen.append(script) or "LISTEN 0 0 0.0.0.0:5985 *:* users:((\"System\"))\n"
                                                "ESTAB 0 0 10.99.40.10:50123 10.99.42.30:445\n")
    out = service_inventory.collect_services(conn)
    assert seen and seen[0] is winrm_collector.SERVICES_PS_NO_CONN
    assert conn.execute("SELECT COUNT(*) FROM host_connection").fetchone()[0] == 0
    assert out["connections"]["enabled"] is False


def test_保留天數_過期的列每輪結尾刪掉(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    old = (datetime.now() - timedelta(days=61)).strftime("%Y-%m-%d %H:%M:%S")
    recent = (datetime.now() - timedelta(days=59)).strftime("%Y-%m-%d %H:%M:%S")
    for ip, ts in (("10.99.2.1", old), ("10.99.2.2", recent)):
        conn.execute("INSERT INTO host_connection (host_ip, asset_serial, direction, local_port, peer_ip, "
                     "peer_port, count, first_seen, last_seen) VALUES ('10.99.1.99','X','outbound',0,?,443,1,?,?)",
                     (ip, ts, ts))
    conn.commit()
    runner, _ = _linux_env(monkeypatch)
    db.insert_hardware(conn, asset_serial="L-1", hostname="app01", ip="10.99.1.10", collect_ok=1)
    conn.commit()
    out = service_inventory.collect_services(conn, runner=runner)
    assert out["connections"]["expired"] == 1
    left = {r[0] for r in conn.execute("SELECT peer_ip FROM host_connection WHERE host_ip='10.99.1.99'")}
    assert left == {"10.99.2.2"}


def test_保留天數可設定_壞值退回預設(tmp_path):
    conn = _conn(tmp_path)
    assert od.retention_days(conn) == 60
    db.set_setting(conn, od.RETENTION_KEY, "7")
    assert od.retention_days(conn) == 7
    db.set_setting(conn, od.RETENTION_KEY, "abc")
    assert od.retention_days(conn) == 60
    db.set_setting(conn, od.RETENTION_KEY, "0")
    assert od.retention_days(conn) == 60


def test_VMware設備不收連線(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    db.insert_hardware(conn, asset_serial="E-1", hostname="esx01", ip="10.99.9.9", collect_ok=1,
                       os="VMware ESXi 7.0")
    conn.commit()
    calls = []
    monkeypatch.setattr(service_inventory, "_platform_for", lambda *a, **k: "linux")
    service_inventory.collect_services(conn, runner=lambda h, c: calls.append(h) or SS_OUT)
    assert calls == [] and conn.execute("SELECT COUNT(*) FROM host_connection").fetchone()[0] == 0


def test_涵蓋率有伺服器分母(tmp_path):
    conn = _conn(tmp_path)
    db.insert_hardware(conn, asset_serial="L-1", hostname="app01", ip="10.99.1.10",
                       os="Red Hat Enterprise Linux 8")
    db.insert_hardware(conn, asset_serial="N-1", hostname="sw01", ip="10.99.7.1", os="Cisco IOS",
                       device_model="Catalyst 9300")
    conn.commit()
    od.store(conn, "10.99.1.10", "L-1", [{"direction": "outbound", "local_port": 0, "peer_ip": "10.99.5.5",
                                         "peer_port": 443, "count": 1}])
    cov = od.coverage(conn)
    assert cov["total"] >= 2 and cov["server_total"] == 1 and cov["server_observed"] == 1
    assert cov["server_pct"] == 100.0 and cov["collect_enabled"] is True and cov["retention_days"] == 60


def test_API_開關與保留天數(tmp_path):
    from fastapi.testclient import TestClient

    import api
    import auth

    p = tmp_path / "api.db"
    db.init_db(p)

    def _get_db():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _get_db
    try:
        c = TestClient(api.app)
        assert c.put("/api/blast/observed-collect", json={"enabled": False}).status_code == 401
        k = db.get_connection(p)
        db.create_user(k, "tester", auth.hash_password("test-password-123"))
        k.close()
        assert c.post("/api/auth/login", json={"username": "tester", "password": "test-password-123"}).status_code == 200
        r = c.put("/api/blast/observed-collect", json={"enabled": False, "retention_days": 14})
        assert r.status_code == 200 and r.json() == {"enabled": False, "retention_days": 14}
        assert c.put("/api/blast/observed-collect", json={"retention_days": 0}).status_code == 400
        assert c.get("/api/blast/observed-coverage").json()["collect_enabled"] is False
    finally:
        api.app.dependency_overrides.clear()


def test_沒有新增提權或編碼():
    """連線收集沿用既有唯讀指令：沒有 sudo、沒有 base64／EncodedCommand／Bypass。"""
    cmds = " ".join(list(sc.LINUX_CMDS.values()) + list(sc.AIX_CMDS.values()))
    scripts = winrm_collector.SERVICES_PS + winrm_collector.SERVICES_PS_NO_CONN
    for text in (cmds, scripts):
        assert not re.search(r"sudo|base64|EncodedCommand|ExecutionPolicy|Bypass|sshpass", text, re.I)
