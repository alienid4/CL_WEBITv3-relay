"""2.1 主機查詢「批次偵測存活」（2.041.97；舊版缺口 230 第 1 項）。

守的是：只收 serials 且伺服器端只認現役已登記主機（Codex 235）、VMware 設備（ESXi 與 vCenter）排除而客體 VM 不排除、
非現役／查無／無 IP 略過並說明、數量上限、盤中無確認票證 409、viewer 403、不登入不送指令（只有 ping 與 TCP 探測，全部假的）、
結果與報告一致、標下線只寫「待 CIA 異動」。IP 一律 10.99.x，沒有任何真實連線。
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import db  # noqa: E402
import host_ping  # noqa: E402
import quiet_window as qw  # noqa: E402
import v2_alive  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ALIVE_OUT = "3 packets transmitted, 3 received, 0% packet loss\nrtt min/avg/max/mdev = 0.4/1.2/2.0/0.5 ms"
DEAD_OUT = "3 packets transmitted, 0 received, 100% packet loss"

CALLS: list[str] = []


def fake_run(cmd, timeout):
    CALLS.append(cmd[-1])
    return (0, ALIVE_OUT) if cmd[-1] == "10.99.1.1" else (1, DEAD_OUT)


def fake_tcp(ip, ports=None, timeout=0.6, icmp=True):
    CALLS.append(ip)
    return [22] if ip == "10.99.1.2" else None


_ORIG_ICMP = host_ping.icmp


def _set_ping(monkeypatch, runner):
    """host_ping.icmp／check 的 runner 預設值在定義時就綁死，只 patch _run 擋不住真的 ping；這裡把 icmp 整個換掉。"""
    monkeypatch.setattr(host_ping, "icmp", lambda ip, runner_=None, **kw: _ORIG_ICMP(ip, runner=runner))


def _seed(conn):
    hw = [
        ("H-1", "app01", "10.99.1.1", "使用中", "Red Hat Enterprise Linux 9", 0),      # ping 通＝存活
        ("H-2", "app02", "10.99.1.2", "使用中", "Windows Server 2019", 0),             # ICMP 被擋、TCP 通＝存活
        ("H-3", "app03", "10.99.1.3", "使用中", "Red Hat Enterprise Linux 8", 0),      # 都不通＝連不上
        ("H-4", "old01", "10.99.1.4", "停用", "Red Hat Enterprise Linux 7", 0),        # 非現役
        ("H-5", "noip", "", "使用中", "Red Hat Enterprise Linux 9", 0),                # 沒 IP
        ("E-1", "esx01", "10.99.2.1", "使用中", "VMware ESXi 8.0.3", 0),               # ESXi
        ("E-2", "vcsa01", "10.99.2.2", "使用中", "VMware Photon OS (64-bit)", 1),      # vCenter（VCSA）
        ("V-1", "guest01", "10.99.2.3", "使用中", "Red Hat Enterprise Linux 9", 1),    # 跑在 VMware 上的客體 VM：不排除
    ]
    for sn, h, ip, st, os_, vm in hw:
        db.insert_hardware(conn, asset_serial=sn, hostname=h, ip=ip, asset_status=st, os=os_, is_vm=vm,
                           **({"asset_purpose": "vCenter"} if sn == "E-2" else {}))
    conn.commit()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import time

    for _ in range(40):                      # 上一個測試遺留的背景探測先收乾淨
        if host_ping.inflight() == 0:
            break
        time.sleep(0.1)
    v2_alive.reset_cache()
    CALLS.clear()
    p = tmp_path / "t.db"
    db.init_db(p)
    conn = db.get_connection(p)
    _seed(conn)
    conn.close()
    _set_ping(monkeypatch, fake_run)
    monkeypatch.setattr(host_ping.net_scan, "_probe_host", fake_tcp)
    who = {"u": {"username": "tester", "role": "admin"}}

    def _get_db():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _get_db
    api.app.dependency_overrides[api.require_auth] = lambda: who["u"]
    try:
        yield TestClient(api.app, raise_server_exceptions=False), p, who
    finally:
        # 背景還在跑的探測要在假探測被還原之前收完，否則會打到真的 net_scan（不得有任何真實連線）
        for _ in range(50):
            if host_ping.inflight() == 0:
                break
            time.sleep(0.1)
        api.app.dependency_overrides.pop(api.get_db, None)
        api.app.dependency_overrides.pop(api.require_auth, None)
        qw.set_clock(None)


def _by_serial(rep):
    return {r["asset_serial"]: r for r in rep["rows"]}


def test_只偵測現役已登記_VMware設備排除_客體VM不排除(env):
    c, _, _ = env
    r = c.post("/api/v2/hosts/alive-check", json={"serials": ["H-1", "H-2", "H-3", "H-4", "H-5", "E-1", "E-2", "V-1", "NOPE"]})
    assert r.status_code == 200, r.text
    rep = r.json()
    rows = _by_serial(rep)
    assert (rows["H-1"]["state"], rows["H-2"]["state"], rows["H-3"]["state"]) == ("alive", "alive", "no_response")
    assert rows["V-1"]["state"] in ("alive", "no_response"), "跑在 VMware 上的客體 VM 要照常偵測"
    assert rows["E-1"]["state"] == "excluded" and rows["E-2"]["state"] == "excluded", "ESXi 與 vCenter 都排除"
    assert "VMware" in rows["E-1"]["verdict"]
    for sn, why in (("H-4", "非現役"), ("H-5", "沒有登記 IP"), ("NOPE", "查無此資產序號")):
        assert rows[sn]["state"] == "skipped" and why in rows[sn]["verdict"], sn
    # 沒有任何連線打向 VMware 設備或非現役機器
    assert not {"10.99.2.1", "10.99.2.2", "10.99.1.4"} & set(CALLS)
    assert rep["counts"]["excluded"] == 2 and rep["counts"]["skipped"] == 3 and rep["requested"] == 9
    assert rep["counts"]["alive"] + rep["counts"]["no_response"] + rep["counts"]["unknown"] == 4


def test_不接受任意IP_只認序號(env):
    c, _, _ = env
    r = c.post("/api/v2/hosts/alive-check", json={"serials": ["10.99.9.9", "10.99.1.1"], "ips": ["10.99.9.9"]})
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert all(x["state"] == "skipped" for x in rows), "IP 當序號丟進來只會查無，不會被探測"
    assert "10.99.9.9" not in CALLS and "10.99.1.1" not in CALLS


def test_上限與空清單(env):
    c, _, _ = env
    assert c.post("/api/v2/hosts/alive-check", json={"serials": []}).status_code == 400
    over = [f"X-{i}" for i in range(v2_alive.MAX_SERIALS + 1)]
    r = c.post("/api/v2/hosts/alive-check", json={"serials": over})
    assert r.status_code == 400 and "請分批" in r.json()["detail"]
    assert not CALLS


def test_viewer不可_窗內無確認409_確認後放行並留稽核(env):
    c, p, who = env
    who["u"] = {"username": "vera", "role": "viewer"}
    assert c.post("/api/v2/hosts/alive-check", json={"serials": ["H-1"]}).status_code == 403
    who["u"] = {"username": "tester", "role": "admin"}
    qw.set_clock(lambda: datetime(2026, 11, 2, 10, 0))          # 週一 10:00＝盤中靜默窗內
    r = c.post("/api/v2/hosts/alive-check", json={"serials": ["H-1"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "quiet_window_confirm"
    assert not CALLS, "沒確認就不能有任何連線"
    ok = c.post("/api/v2/hosts/alive-check?confirm_in_window=true", json={"serials": ["H-1"]})
    assert ok.status_code == 200 and CALLS
    conn = db.get_connection(p)
    try:
        n = conn.execute("SELECT COUNT(*) FROM quiet_window_log WHERE kind = 'manual_confirmed' AND entry = 'manual_diag'").fetchone()[0]
    finally:
        conn.close()
    assert n == 1


def test_不登入不送指令_只有ping與TCP探測(env):
    import inspect

    src = inspect.getsource(v2_alive)
    for bad in ("paramiko", "ssh_probe.run", "winrm", "subprocess", "socket", "onboard_engine", "sshpass"):
        assert bad not in src, bad
    # 探測只經 host_ping 的 ping 與 net_scan 的 TCP 連線；假函式收到的只有 IP
    c, _, _ = env
    c.post("/api/v2/hosts/alive-check", json={"serials": ["H-1", "H-3"]})
    assert set(CALLS) <= {"10.99.1.1", "10.99.1.3"}


def test_結果與報告一致_報告可再查(env):
    c, p, _ = env
    rep = c.post("/api/v2/hosts/alive-check", json={"serials": ["H-1", "H-2", "H-3", "E-1", "H-4"]}).json()
    got = c.get(f"/api/v2/hosts/alive-runs/{rep['run_id']}").json()
    assert got["rows"] == rep["rows"] and got["counts"] == rep["counts"] and got["requested"] == 5
    assert sum(rep["counts"].values()) == len(rep["rows"]) == 5          # 加總＝列數＝下鑽
    lst = c.get("/api/v2/hosts/alive-runs").json()["items"][0]
    assert lst["run_id"] == rep["run_id"] and lst["alive"] == 2 and lst["no_response"] == 1
    assert lst["excluded"] == 1 and lst["skipped"] == 1 and lst["created_by"] == "tester"
    assert c.get("/api/v2/hosts/alive-runs/99999").status_code == 404
    # 同一份也在舊版的報告表裡（只含真的偵測的 3 台）
    old = c.get(f"/api/tools/alive-check/runs/{rep['run_id']}").json()
    assert old["total"] == 3 and old["scope"] == v2_alive.SCOPE


def test_不改資產狀態_標下線只寫待CIA異動(env):
    c, p, _ = env
    rep = c.post("/api/v2/hosts/alive-check", json={"serials": ["H-3"]}).json()
    assert _by_serial(rep)["H-3"]["state"] == "no_response"
    conn = db.get_connection(p)
    try:
        assert conn.execute("SELECT asset_status FROM hardware WHERE asset_serial='H-3'").fetchone()[0] == "使用中", "偵測不改狀態"
    finally:
        conn.close()
    r = c.post("/api/assets/batch-status", json={"serials": ["H-3"], "status": "停用",
                                                 "reason": f"偵測存活無回應，報告 #{rep['run_id']}", "scope": "machine"})
    assert r.status_code == 200 and r.json()["updated"] == ["H-3"]
    conn = db.get_connection(p)
    try:
        assert conn.execute("SELECT asset_status FROM hardware WHERE asset_serial='H-3'").fetchone()[0] == "使用中", "只記待 CIA 異動，不直接改狀態"
        n = conn.execute("SELECT COUNT(*) FROM cia_pending_change WHERE asset_serial='H-3'").fetchone()[0]
    finally:
        conn.close()
    assert n == 1


def test_整批時間上限_來不及的記無法判斷(env, monkeypatch):
    import time

    def slow(cmd, timeout):
        time.sleep(0.6)
        return 0, ALIVE_OUT

    _set_ping(monkeypatch, slow)
    monkeypatch.setattr(v2_alive, "BATCH_DEADLINE", 0.1)
    c, _, _ = env
    rep = c.post("/api/v2/hosts/alive-check", json={"serials": ["H-1", "H-3"]}).json()
    assert rep["deadline_hit"] is True
    assert {r["state"] for r in rep["rows"]} == {"unknown"} and "來不及" in rep["rows"][0]["verdict"]
