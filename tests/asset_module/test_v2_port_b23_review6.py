"""2.058.16（批 23 審查 0c16be7c）：
P1-A——驗證用主機名稱時，打進來的名稱也要判 VMware（原本只拿解析出的 IP 判）：所有收主機名稱的入口走同一支 login_targets。
P1-B——一筆登記多個 IP：任一個被拒＝整筆不納管（同一台機器的另一個 IP 不可以照登）。
P2——關卡丟例外只擋那一台、不中斷整輪；排程 PTR 反查失敗＝「反查失敗，下一輪再試」；取消納管用主機名稱輪詢進度。"""
import socket
import sys
import tempfile
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(ROOT / "tests" / "asset_module"))

import auto_onboard as ao  # noqa: E402
import db  # noqa: E402
import onboard_eligibility as oe  # noqa: E402
import onboard_engine as eng  # noqa: E402

ESX_NAME = "esx31.corp.example"     # 在 vCenter ESXi 名單上；解析到 10.99.0.77；沒有 PTR
ESX_IP = "10.99.0.77"


def _gai(host, *a, **k):
    if host == ESX_NAME:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ESX_IP, 0))]
    if host == "good-host.example":
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.99.0.12", 0))]
    raise socket.gaierror("no such host")


@pytest.fixture
def env(monkeypatch):
    from test_v2_port_b23_review2 import _setup
    import collect_account_migration as cam
    import manage_state
    import win_onboard

    tried = []
    monkeypatch.setattr(socket, "getaddrinfo", _gai)
    monkeypatch.setattr(oe, "esxi_host_names", lambda conn: oe._name_keys(ESX_NAME))
    monkeypatch.setattr(oe, "_ptr_lookup", lambda ip: set())

    def rec(kind):
        def f(*a, **kw):
            tried.append((kind, kw.get("host") or (a[1] if len(a) > 1 else a[0] if a else None)))
            return eng.OnboardResult(False, "connect", "假的：不連線")
        return f
    monkeypatch.setattr(eng, "onboard", rec("onboard"))
    monkeypatch.setattr(eng, "revoke", rec("revoke"))
    monkeypatch.setattr(win_onboard, "onboard_one", lambda conn, ip, **kw: tried.append(("win", ip)) or {"ok": False})
    monkeypatch.setattr(cam, "settle_after_onboard", lambda conn, ip, platform: tried.append(("settle", ip)) or {"ok": True})
    monkeypatch.setattr(manage_state, "collect_facts_into_assets",
                        lambda conn, only_serial=None, **k: tried.append(("collect", only_serial)) or {"updated": 0, "failed": []})
    with tempfile.TemporaryDirectory() as tmp:
        tb, client = _setup(tmp, monkeypatch, tried)
        try:
            yield client, tried, tmp
        finally:
            tb.api.app.dependency_overrides.clear()


ENTRIES = {
    "onboard": lambda c, h: c.post("/api/onboard", json={"ip": h, "platform": "linux", "username": "root", "password": "x1x"}),
    "revoke": lambda c, h: c.post("/api/onboard/revoke", json={"ip": h, "platform": "linux", "username": "root",
                                                               "password": "x1x", "confirm": "YES"}),
    "verify": lambda c, h: c.post("/api/onboard/verify", json={"ip": h, "platform": "linux"}),
    "windows": lambda c, h: c.post("/api/onboard/windows", json={"ip": h}),
    "batch-probe": lambda c, h: c.post("/api/onboard/batch-probe", json={"ips": [h], "username": "root", "passwords": ["x1x"]}),
    "precheck": lambda c, h: c.post("/api/onboard/precheck", json={"ips": [h], "username": "root", "password": "x1x"}),
}


@pytest.mark.parametrize("entry", sorted(ENTRIES))
def test_主機名稱在vCenter名單上_沒有PTR_每個入口零次連線(env, entry):
    client, tried, tmp = env
    r = ENTRIES[entry](client, ESX_NAME)
    assert r.status_code in (200, 400), r.text
    if entry == "verify":
        assert r.json()["ok"] is False and "VMware" in r.json()["error"]
        conn = db.get_connection(Path(tmp) / "t.db")
        try:
            assert conn.execute("SELECT COUNT(*) FROM hardware WHERE asset_serial LIKE 'AUTO-%'").fetchone()[0] == 0
        finally:
            conn.close()
    elif r.status_code == 400:
        assert "VMware" in r.text or "IPv4" in r.text
    else:
        row = r.json()["results"][0]
        assert row.get("invalid_ip") or row.get("vmware")
    assert tried == [], tried


def test_對照_直接打那個IP不是VMware_照常走到連線(env):
    """名稱是唯一的線索：直接打 10.99.0.77（沒登記、沒 PTR）不算 VMware——證明上面擋下來靠的是名稱。"""
    client, tried, _ = env
    ENTRIES["verify"](client, ESX_IP)
    assert ("settle", ESX_IP) in tried


def test_取消納管_用主機名稱輪詢進度(env, monkeypatch):
    client, tried, _ = env

    def fake_revoke(**kw):
        eng.progress_note(kw["host"], "[*] 收回中")
        return eng.OnboardResult(False, "connect", "假的")
    monkeypatch.setattr(eng, "revoke", fake_revoke)
    r = ENTRIES["revoke"](client, "good-host.example")
    assert r.status_code == 200, r.text
    assert eng.progress_of("good-host.example")["stage"] == "收回中"


def test_login_address只在共用關卡裡用():
    """防再犯：入口不可以自己「先解析、再只判 IP」——一律呼叫 login_targets／login_target_refusal。"""
    back = ROOT / "APP" / "asset-module" / "backend"
    for p in back.rglob("*.py"):
        if p.name == "onboard_eligibility.py":
            continue
        s = p.read_text(encoding="utf-8")
        assert "login_address(" not in s, p


# ===== 排程自動納管 =====
def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _scan(conn, ip):
    conn.execute("INSERT INTO scan_history (scan_time, ip, hostname, scan_ok, open_ports) VALUES (?,?,?,1,?)",
                 ("2026-11-09 01:00:00", ip, None, "22"))
    conn.commit()


class _Exec:
    def __init__(self):
        self.calls = []

    def __call__(self, host, username, password, platform, script, collector_ip, **kw):
        self.calls.append(host)
        return eng.OnboardResult(True, "execute", "完成", "完成。")


_cred = lambda ip: ("ssh-lab", "svc_boot", "FAKE-TEST-PW-r6")   # noqa: E731


def _auto(monkeypatch, records, ptr=None):
    monkeypatch.setattr(oe, "esxi_host_names", lambda conn: oe._name_keys("esx30.corp.example"))
    monkeypatch.setattr(oe, "_ptr_lookup", ptr or (lambda ip: {"esx30.corp.example"} if ip == "10.99.0.31" else set()))
    monkeypatch.setattr(oe, "resolver_health", lambda conn: ("ok", "vc.example"))
    tmp = tempfile.mkdtemp()
    conn = _conn(tmp)
    ao.save_segment(conn, "10.99.0.")
    for serial, ip in records:
        conn.execute("INSERT INTO hardware (asset_serial, ip) VALUES (?, ?)", (serial, ip))
        for one in ao._split_ips(ip):
            _scan(conn, one)
        _scan(conn, ip)
    conn.commit()
    return conn


def _audits(conn, ip):
    return [r["message"] for r in conn.execute("SELECT message FROM onboard_audit WHERE target_ip = ? ORDER BY id", (ip,))]


def test_多IP登記_其中一個是ESXi_整筆不納管(monkeypatch):
    conn = _auto(monkeypatch, [("M-30", "10.99.0.30, 10.99.0.31")])
    try:
        ex = _Exec()
        r = ao.run_auto_onboard(conn, "10.99.0.221", executor=ex, cred_lookup=_cred)
        assert ex.calls == []
        assert r["details"][0]["action"] == "skipped" and "整筆" in r["details"][0]["message"]
        assert _audits(conn, "10.99.0.30") and _audits(conn, "10.99.0.31")
    finally:
        conn.close()


def test_多IP登記_其中一個不是IPv4_整筆不納管(monkeypatch):
    conn = _auto(monkeypatch, [("M-33", "10.99.0.33, 010.99.0.34")])
    try:
        ex = _Exec()
        r = ao.run_auto_onboard(conn, "10.99.0.221", executor=ex, cred_lookup=_cred)
        assert ex.calls == [] and "只接受 IPv4" in r["details"][0]["message"]
        assert _audits(conn, "10.99.0.33")
    finally:
        conn.close()


def test_多IP登記_都過關卡_只登一次(monkeypatch):
    conn = _auto(monkeypatch, [("M-35", "10.99.0.35, 10.99.0.36")])
    try:
        ex = _Exec()
        ao.run_auto_onboard(conn, "10.99.0.221", executor=ex, cred_lookup=_cred)
        assert ex.calls == ["10.99.0.35"]
    finally:
        conn.close()


def test_排程_PTR反查失敗_這台跳過_下一輪再試_其他照跑(monkeypatch):
    conn = _auto(monkeypatch, [("P-37", "10.99.0.37"), ("P-38", "10.99.0.38")],
                 ptr=lambda ip: None if ip == "10.99.0.37" else set())
    try:
        ex = _Exec()
        r = ao.run_auto_onboard(conn, "10.99.0.221", executor=ex, cred_lookup=_cred)
        assert ex.calls == ["10.99.0.38"]
        d = {x["ip"]: x for x in r["details"]}
        assert d["10.99.0.37"]["action"] == "skipped" and d["10.99.0.37"]["message"] == "反查失敗，下一輪再試"
        assert _audits(conn, "10.99.0.37") == ["反查失敗，下一輪再試"]
    finally:
        conn.close()


def test_排程_關卡丟例外_只擋那一台_留稽核_其他照跑(monkeypatch):
    conn = _auto(monkeypatch, [("E-39", "10.99.0.39"), ("E-40", "10.99.0.40")])
    real = oe.login_targets

    def flaky(conn_, raw, **kw):
        if raw == "10.99.0.39":
            raise RuntimeError("boom")
        return real(conn_, raw, **kw)
    monkeypatch.setattr(oe, "login_targets", flaky)
    try:
        ex = _Exec()
        r = ao.run_auto_onboard(conn, "10.99.0.221", executor=ex, cred_lookup=_cred)
        assert ex.calls == ["10.99.0.40"]
        assert "無法確認" in {x["ip"]: x for x in r["details"]}["10.99.0.39"]["message"]
        assert _audits(conn, "10.99.0.39")
    finally:
        conn.close()


def test_ptr_lookup_分得出沒有PTR與查詢失敗(monkeypatch):
    def raise_(e):
        def f(ip):
            raise e
        return f
    monkeypatch.setattr(socket, "gethostbyaddr", raise_(socket.herror(1, "Unknown host")))
    assert oe._ptr_lookup("10.99.0.1") == set()
    monkeypatch.setattr(socket, "gethostbyaddr", raise_(socket.herror(11004, "no data")))
    assert oe._ptr_lookup("10.99.0.1") == set()
    monkeypatch.setattr(socket, "gethostbyaddr", raise_(socket.herror(2, "try again")))
    assert oe._ptr_lookup("10.99.0.1") is None
    monkeypatch.setattr(socket, "gethostbyaddr", raise_(OSError("network down")))
    assert oe._ptr_lookup("10.99.0.1") is None
    monkeypatch.setattr(socket, "gethostbyaddr", lambda ip: ("h1.example", ["h1"], [ip]))
    assert oe._ptr_lookup("10.99.0.1") == {"h1.example", "h1"}
