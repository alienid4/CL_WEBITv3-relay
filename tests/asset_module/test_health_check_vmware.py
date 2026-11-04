"""天條：VMware 設備（ESXi／vCenter）不做即時檢測（2.006.1，找碴 C2）。

主機履歷的「即時檢測」打 /api/health-check/run。這支原本沒有走 judge_for_asset：
platform_from_os 認不出「VMware ESXi 8.0.3」，退回用埠號猜成 linux，然後拿收集金鑰去 SSH ESXi——
那正是天條禁止的（ESXi 只能透過 vCenter／RVTools 取資料）。

這裡釘住：VMware 登記的目標**一個都不能**進 health_probe.check_batch；結果列標成「沒有去探測」，
不算連不上、不算異常。
"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import db  # noqa: E402
import health_probe  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def env(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        c = db.get_connection(p)
        rows = [("A-ESX", "esx01.example.local", "10.99.7.11", "VMware ESXi 8.0.3"),
                ("A-LNX", "lnx01", "10.99.7.21", "Red Hat Enterprise Linux 8.6"),
                ("A-MULTI", "esx02", "10.99.7.31, 10.99.8.31", "VMware ESXi 7.0 U3"),
                ("A-SHORT", "esx03", "10.99.7.51", "VMware ESXi 8.0.2"),            # 登記成短名稱
                ("A-FQDN", "esx04.corp.example", "10.99.7.61", "VMware ESXi 8.0.2")]  # 登記成 FQDN
        for s, h, ip, os_ in rows:
            c.execute("INSERT INTO hardware (asset_serial, hostname, ip, os) VALUES (?,?,?,?)", (s, h, ip, os_))
        # 沒有登記、只出現在 vCenter（RVTools）ESXi 名單的那台
        c.execute("INSERT INTO source_record (source, payload) VALUES ('vcenter', ?)",
                  (json.dumps({"esxi_host": "10.99.7.41"}),))
        c.commit()
        c.close()

        def _get_db():
            cc = db.get_connection(p)
            try:
                yield cc
            finally:
                cc.close()

        seen: list[list[str]] = []

        def fake_batch(ips, **kw):
            seen.append(list(ips))
            return [{"ip": ip, "platform": "linux", "reachable": True, "error": None, "unresolved": False,
                     "overall": "green", "notes": [], "not_probed": None, "complete": True, "coverage": None}
                    for ip in ips]

        monkeypatch.setattr(health_probe, "check_batch", fake_batch)
        # DNS：別名 vmalias 解析到 esx03 的 IP；其他名稱解析不到（不打真的 DNS）
        import socket
        dns = {"vmalias.corp.example": "10.99.7.51", "lnx-alias": "10.99.7.21"}

        def fake_gai(host, *a, **k):
            if host in dns:
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (dns[host], 0))]
            raise socket.gaierror("not found")
        monkeypatch.setattr(socket, "getaddrinfo", fake_gai)
        api.app.dependency_overrides[api.get_db] = _get_db
        api.app.dependency_overrides[api.require_auth] = lambda: {"username": "tester", "role": "admin"}
        try:
            yield TestClient(api.app, raise_server_exceptions=False), seen
        finally:
            api.app.dependency_overrides.pop(api.get_db, None)
            api.app.dependency_overrides.pop(api.require_auth, None)


def _probed(seen):
    return {ip for batch in seen for ip in batch}


def test_ESXi登記的IP不會送進探測(env):
    client, seen = env
    r = client.post("/api/health-check/run", json={"ips": ["10.99.7.11"]})
    assert r.status_code == 200
    assert "10.99.7.11" not in _probed(seen), "VMware 設備被送去 SSH／WinRM 探測了（天條）"
    item = r.json()["items"][0]
    assert item["overall"] == "skipped" and item["not_probed"].startswith("VMware 設備")
    s = r.json()["summary"]
    assert s["not_probed"] == 1 and s["unreachable"] == 0 and s["red"] == 0


def test_用主機名稱或第二個IP也擋得住(env):
    client, seen = env
    r = client.post("/api/health-check/run", json={"ips": ["esx01", "10.99.8.31", "https://esx02/ui"]})
    assert r.status_code == 200
    assert not _probed(seen), f"VMware 設備被送去探測：{_probed(seen)}"
    assert all(i["overall"] == "skipped" for i in r.json()["items"])


def test_只在vCenter名單上的ESXi也擋(env):
    client, seen = env
    r = client.post("/api/health-check/run", json={"ips": ["10.99.7.41"]})
    assert "10.99.7.41" not in _probed(seen)
    assert r.json()["items"][0]["not_probed"].startswith("VMware 設備")


def test_一般主機照常檢測_混在一起只擋VMware(env):
    client, seen = env
    r = client.post("/api/health-check/run", json={"ips": ["10.99.7.21", "10.99.7.11"]})
    assert _probed(seen) == {"10.99.7.21"}
    items = {i["ip"]: i for i in r.json()["items"]}
    assert items["10.99.7.21"]["overall"] == "green"
    assert items["10.99.7.11"]["overall"] == "skipped"


def test_FQDN_結尾點_短名稱_別名都擋得住(env):
    """審查 H1：ESXi 登記成短名稱、目標打 FQDN／結尾帶點／大寫，或登記成 FQDN、目標打短名稱，
    或目標是 DNS 別名（解析到 ESXi 的 IP）——都不能送進探測。"""
    client, seen = env
    targets = ["esx03.corp.example.", "ESX03.corp.example", "esx04", "esx04.corp.example.", "vmalias.corp.example"]
    r = client.post("/api/health-check/run", json={"ips": targets})
    assert r.status_code == 200
    assert not _probed(seen), f"VMware 設備被送去探測：{_probed(seen)}"
    assert {i["ip"] for i in r.json()["items"] if i["overall"] == "skipped"} == set(targets)


def test_別名解析到一般主機照常檢測(env):
    client, seen = env
    client.post("/api/health-check/run", json={"ips": ["lnx-alias"]})
    assert _probed(seen) == {"lnx-alias"}


def test_結果順序等於輸入順序(env):
    client, seen = env
    order = ["10.99.7.11", "10.99.7.21", "esx03"]
    r = client.post("/api/health-check/run", json={"ips": order})
    assert [i["ip"] for i in r.json()["items"]] == order


# ── 審查 M3：沒登記、用 IP 打到的 VMware，連上後自我檢查就離開 ─────────────
@pytest.mark.parametrize("platform", ["linux", "aix"])
def test_連上後偵測到VMware_指標一個都不跑(platform):
    import onboard_engine

    sent = []

    def fake_ssh(ip, cmd, key, acct, timeout=14):
        sent.append(cmd)
        # 模擬遠端：守門句偵測到 VMkernel → 只印標記
        return True, onboard_engine.VMWARE_REFUSED_MARKER + "\n", ""
    r = health_probe.check_one("10.99.9.9", platform=platform, key_path="k", account="a", _ssh=fake_ssh, _svc={})
    assert r["overall"] == "skipped" and r["not_probed"] == "VMware 設備（連上後偵測到）不做即時檢測"
    assert len(sent) == 1, "偵測到 VMware 之後還繼續送指令"
    guard = onboard_engine.vmware_marker_guard_sh()
    assert sent[0].startswith(guard) and "VMkernel" in guard and onboard_engine.VCSA_MARKER_TEST in guard


# ── 審查 M4：週六健康基準、納管後第一份基準也不可以 SSH VMware ─────────────
def _db_with_esxi(tmp):
    p = Path(tmp) / "m4.db"
    db.init_db(p)
    c = db.get_connection(p)
    c.execute("INSERT INTO hardware (asset_serial, hostname, ip, os, collect_ok) VALUES ('E-1','esx09','10.99.7.91','VMware ESXi 7.0',1)")
    c.execute("INSERT INTO hardware (asset_serial, hostname, ip, os, collect_ok) VALUES ('L-1','lnx09','10.99.7.92','Red Hat Enterprise Linux 9',1)")
    c.commit()
    return c


def test_週基準不探測已納管的ESXi(monkeypatch, tmp_path):
    import health_baseline

    conn = _db_with_esxi(tmp_path)
    monkeypatch.setattr(health_baseline, "_target_ips", lambda c: ["10.99.7.91", "10.99.7.92"])
    import manage_state
    monkeypatch.setattr(manage_state, "collect_platform_of", lambda c, ip: "linux")   # 不去探埠猜平台
    seen = []

    def fake_batch(ips, **kw):
        seen.extend(ips)
        return []
    out = health_baseline.run_weekly_baseline(conn, _check_batch=fake_batch)
    assert seen == ["10.99.7.92"] and out["skipped_vmware"] == 1
    conn.close()


def test_納管後第一份基準不探測ESXi(monkeypatch, tmp_path):
    import post_onboard

    conn = _db_with_esxi(tmp_path)
    monkeypatch.setattr(post_onboard, "JOBS", [])
    import manage_state
    monkeypatch.setattr(manage_state, "collect_platform_of", lambda c, ip: "linux")
    called = []
    monkeypatch.setattr(health_probe, "check_one", lambda ip, **k: called.append(ip) or {"ip": ip, "overall": "green", "ports": []})
    post_onboard.run_now(conn, "E-1")
    assert called == [], "納管後健檢對 ESXi 發了 SSH"
    post_onboard.run_now(conn, "L-1")
    assert called == ["10.99.7.92"]
    conn.close()


def test_主機履歷_天條前已納管的ESXi要講出來(tmp_path):
    import v2_views

    conn = _db_with_esxi(tmp_path)
    m = v2_views.machine(conn, "esx09")
    assert m["vmware"] and m["vmware_legacy"]
    assert v2_views.machine(conn, "lnx09")["vmware_legacy"] is False
    conn.close()


def test_多IP字串逐一拆開判(tmp_path):
    """審查 N2：「10.99.8.31, 10.99.9.99」這種目標，第一個 IP 是 ESXi 就要擋。"""
    import onboard_eligibility as oe

    conn = _db_with_esxi(tmp_path)
    assert "10.99.7.91, 10.99.9.99" in oe.vmware_targets(conn, ["10.99.7.91, 10.99.9.99"])
    assert "10.99.9.99, 10.99.7.91" in oe.vmware_targets(conn, ["10.99.9.99, 10.99.7.91"])
    assert not oe.vmware_targets(conn, ["10.99.7.92, 10.99.9.99"])
    conn.close()
