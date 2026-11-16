"""2.058.14（批 23 第四輪審查 f04bb3da）：納管／登入入口只接受 IPv4（onboard_eligibility.onboard_ipv4／login_target_refusal）。
P1——/api/onboard/windows 原本接受任何 IPv6、win_onboard.refusal 用原字串查資產，`::ffff:` 等寫法查不到 ESXi 登記就用 WinRM 登入；
/api/onboard/revoke 原本沒有 VMware 關卡。P2——主機名稱解析不到 IPv4 就拒絕（不退回名稱比對）；索引與 getaddrinfo 結果用標準寫法。
繞道表：每一種寫法對 batch-probe、precheck、/api/onboard、/api/onboard/windows、/api/onboard/revoke 都是零次連線。"""
import socket
import sys
import tempfile
from pathlib import Path

import pytest

from test_v2_port_b23 import ROOT

sys.path.insert(0, str(ROOT / "tests" / "asset_module"))
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

from test_v2_port_b23_review2 import _setup  # noqa: E402

ESXI = "10.99.0.11"      # _setup 登記成 VMware ESXi
GOOD = "10.99.0.12"

BYPASS = [
    # IPv4-mapped（折回 IPv4 → 擋在 VMware）
    "::ffff:10.99.0.11", "::FFFF:a63:b", "0:0:0:0:0:ffff:a63:b",
    # NAT64、IPv4-compatible、IPv4-translated、6to4
    "64:ff9b::a63:b", "64:ff9b::10.99.0.11", "::10.99.0.11", "::ffff:0:a63:b", "2002:a63:b::1",
    # zone、括號
    "fe80::1%eth0", "10.99.0.11%eth0", "[10.99.0.11]", "[::ffff:10.99.0.11]",
    # 八進位、十六進位、整數、縮寫
    "010.99.0.11", "0x0a.0x63.0.0x0b", "0x0a63000b", "174260235", "10.99.11",
    # 結尾的點、帶埠、清單
    "10.99.0.11.", "10.99.0.11:22", "10.99.0.11,10.99.0.12", "10.99.0.11 10.99.0.12",
    # 解析不到的主機名稱
    "esx-nowhere.example",
]

_REAL_GAI = socket.getaddrinfo


def _fake_gai(host, *a, **k):
    if host == "good-host.example":
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (GOOD, 0))]
    if host == "esx-dns.example":       # 只回 IPv4-mapped：要折回才對得到 ESXi 登記（M7）
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::ffff:10.99.0.11", 0, 0, 0))]
    if host == "mixed-host.example":
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (GOOD, 0)),
                (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", 0, 0, 0))]
    raise socket.gaierror("no such host")


@pytest.fixture
def env(monkeypatch):
    tried = []
    import onboard_engine
    import win_onboard

    monkeypatch.setattr(socket, "getaddrinfo", _fake_gai)

    def fake_onboard(**kw):
        tried.append(("onboard", kw["host"]))
        return onboard_engine.OnboardResult(False, "connect", "假的：不連線")

    def fake_revoke(**kw):
        tried.append(("revoke", kw["host"]))
        return onboard_engine.OnboardResult(False, "connect", "假的：不連線")

    def fake_win(conn, ip, **kw):
        tried.append(("win", ip))
        return {"ok": False, "message": "假的：不連線"}
    monkeypatch.setattr(onboard_engine, "onboard", fake_onboard)
    monkeypatch.setattr(onboard_engine, "revoke", fake_revoke)
    monkeypatch.setattr(win_onboard, "onboard_one", fake_win)
    with tempfile.TemporaryDirectory() as tmp:
        tb, client = _setup(tmp, monkeypatch, tried)
        try:
            yield client, tried, tmp
        finally:
            tb.api.app.dependency_overrides.clear()


def _all_endpoints(client, ip):
    """回每個入口的 (狀態碼, 回應)。"""
    out = {}
    r = client.post("/api/onboard/batch-probe", json={"ips": [ip], "username": "root", "passwords": ["x1x"]})
    out["batch-probe"] = r
    r = client.post("/api/onboard/precheck", json={"ips": [ip], "username": "root", "password": "x1x"})
    out["precheck"] = r
    out["onboard"] = client.post("/api/onboard", json={"ip": ip, "platform": "linux", "username": "root", "password": "x1x"})
    out["windows"] = client.post("/api/onboard/windows", json={"ip": ip})
    out["revoke"] = client.post("/api/onboard/revoke", json={"ip": ip, "platform": "linux", "username": "root",
                                                             "password": "x1x", "confirm": "YES"})
    return out


@pytest.mark.parametrize("ip", BYPASS)
def test_繞道表_每個入口都零次連線(env, ip):
    client, tried, _ = env
    out = _all_endpoints(client, ip)
    for name in ("batch-probe", "precheck"):
        assert out[name].status_code == 200, (name, out[name].text)
        row = out[name].json()["results"][0]
        assert row.get("vmware") is True or row.get("invalid_ip") is True, (name, row)
    for name in ("onboard", "windows", "revoke"):
        assert out[name].status_code == 400, (name, out[name].text)
        t = out[name].text
        assert ("IPv4" in t) or ("VMware" in t), (name, t)
    assert tried == [], tried


def test_一般IPv4_每個入口都照常走到連線那步(env):
    client, tried, _ = env
    out = _all_endpoints(client, GOOD)
    assert out["batch-probe"].status_code == 200 and out["precheck"].status_code == 200
    # probe_target（批次試密碼、預檢）記的是主機字串；其餘三個入口記 (入口, 主機)
    assert tried.count(GOOD) == 2, tried
    assert [t for t in tried if isinstance(t, tuple)] == [("onboard", GOOD), ("win", GOOD), ("revoke", GOOD)], tried


def test_主機名稱_解析得到IPv4才登入_解析不到或混了IPv6就拒絕(env):
    client, tried, _ = env
    r = client.post("/api/onboard", json={"ip": "good-host.example", "platform": "linux", "username": "root", "password": "x1x"})
    assert r.status_code == 200, r.text
    assert tried == [("onboard", GOOD)]   # 2.058.15：連的是檢查過的那個 IPv4，不是主機名稱
    tried.clear()
    for host in ("esx-nowhere.example", "mixed-host.example", "esx-dns.example"):
        r = client.post("/api/onboard", json={"ip": host, "platform": "linux", "username": "root", "password": "x1x"})
        assert r.status_code == 400, (host, r.text)
    assert tried == []


def test_M7_getaddrinfo結果_IPv4mapped折回再比對(env):
    import onboard_eligibility as oe
    assert oe._resolve_ips("esx-dns.example") == {ESXI}


def test_M2_登記寫成IPv4mapped_索引用標準寫法(env):
    _client, _tried, tmp = env
    import db
    import onboard_eligibility as oe
    conn = db.get_connection(Path(tmp) / "t.db")
    try:
        db.insert_hardware(conn, asset_serial="A-21", ip="::ffff:10.99.0.21", os="VMware ESXi 8.0.3",
                           hostname="esx-mapped-21", environment="測試", asset_status="使用中")
        conn.commit()
        _rows, _by_name, by_ip = oe._hw_index(conn)
        assert "10.99.0.21" in by_ip
        assert oe.vmware_targets(conn, ["10.99.0.21"])
        assert oe.login_target_refusal(conn, "10.99.0.21")[1]
    finally:
        conn.close()


def test_onboard_ipv4():
    import onboard_eligibility as oe
    assert oe.onboard_ipv4(" 10.99.0.12 ") == GOOD
    assert oe.onboard_ipv4("::ffff:10.99.0.12") == GOOD
    for x in BYPASS[3:]:
        assert oe.onboard_ipv4(x) is None, x
    assert oe.is_hostname("good-host.example") and oe.is_hostname("esx01")
    for x in ("010.99.0.11", "0x0a63000b", "174260235", "10.99.11", "10.99.0.11.", "a..b", "-x.example"):
        assert not oe.is_hostname(x), x


def test_batch_auto_只收IPv4_其他寫法列不可納管(env):
    _client, _tried, tmp = env
    import batch_onboard_service as bos
    import db
    conn = db.get_connection(Path(tmp) / "t.db")
    try:
        out = bos.classify_targets(conn, ["::ffff:10.99.0.11", "64:ff9b::a63:b"])
        assert [(x["ip"], x["reason"]) for x in out["excluded"] if x.get("kind") == "invalid"] == [
            ("64:ff9b::a63:b", "只接受 IPv4 位址")]
        every = [x["ip"] for b in out.values() for x in b]
        assert "10.99.0.11" in every and "::ffff:10.99.0.11" not in every     # 一律標準寫法
        assert not out["run"] and not out["unknown"]
    finally:
        conn.close()


def test_Windows取消納管_只收IPv4_標準寫法(env, monkeypatch):
    """Windows 取消納管不登入目標機，但狀態表用 IP 當鍵：只收 IPv4、一律標準寫法（_win_ip 單獨守這條）。"""
    client, tried, _ = env
    import win_onboard
    got = []
    monkeypatch.setattr(win_onboard, "revoke", lambda conn, ip, by=None: got.append(ip) or {"ok": True, "message": "ok"})
    for bad in ("64:ff9b::a63:b", "::10.99.0.12", "010.99.0.12", "good-host.example"):
        r = client.post("/api/onboard/windows/revoke", json={"ip": bad, "confirm": "YES"})
        assert r.status_code == 400 and "IPv4" in r.text, (bad, r.text)
    r = client.post("/api/onboard/windows/revoke", json={"ip": "::ffff:10.99.0.12", "confirm": "YES"})
    assert r.status_code == 200, r.text
    assert got == ["10.99.0.12"]
