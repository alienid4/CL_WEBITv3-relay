"""2.058.17（批 23 審查 ca2578fb）：
P1-1——「我沒有密碼：取得納管指令」發指令時過共用關卡、背景試連（paste_watch）連線前再過一次；這版之前的等待列是 VMware／主機名稱的標 expired、不連。
P1-2——排程自動納管每輪先做 DNS 健康檢查：壞了整輪不納管；通過＝「沒有 PTR」可以當真；沒有可測的名稱＝連「沒有 PTR」也不登入。
P2——AST 守門 _login_address；驗證連的是檢查過的 IPv4；主機名稱的每一個 IPv4 都判；批次自動納管連線前每台再過一次。"""
import ast
import socket
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACK = ROOT / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACK))
sys.path.insert(0, str(ROOT / "tests" / "asset_module"))

import auto_onboard as ao  # noqa: E402
import db  # noqa: E402
import onboard_eligibility as oe  # noqa: E402
import onboard_engine as eng  # noqa: E402
import paste_watch as pw  # noqa: E402

ESX_NAME = "esx31.corp.example"     # vCenter 名單上；解析到 10.99.0.77；沒有 PTR
ESX_REG = "10.99.0.11"              # _setup 登記成 ESXi


def _gai(host, *a, **k):
    table = {ESX_NAME: ["10.99.0.77"], "good-host.example": ["10.99.0.12"],
             "two-ips.example": ["10.99.0.10", ESX_REG]}   # 排序後 ESXi 在第二個（只判第一個就會漏）
    if host in table:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in table[host]]
    raise socket.gaierror("no such host")


@pytest.fixture
def env(monkeypatch):
    from test_v2_port_b23_review2 import _setup
    tried = []
    monkeypatch.setattr(socket, "getaddrinfo", _gai)
    monkeypatch.setattr(oe, "esxi_host_names", lambda conn: oe._name_keys(ESX_NAME))
    monkeypatch.setattr(oe, "_ptr_lookup", lambda ip: set())
    with tempfile.TemporaryDirectory() as tmp:
        tb, client = _setup(tmp, monkeypatch, tried)
        try:
            yield client, tried, tmp
        finally:
            tb.api.app.dependency_overrides.clear()


# ===== P1-1 發指令 =====
@pytest.mark.parametrize("target", [ESX_REG, ESX_NAME, "::ffff:10.99.0.11", "64:ff9b::a63:b"])
def test_取得納管指令_ESXi或不合格的拒絕(env, target):
    client, tried, tmp = env
    r = client.post("/api/onboard/paste/issue", json={"ips": [target]})
    assert r.status_code == 400, r.text
    d = r.json()["detail"]      # 2.058.18：畫面要逐台列原因
    assert d["refused"][0]["ip"] == target and d["refused"][0]["reason"] and d["message"]
    conn = db.get_connection(Path(tmp) / "t.db")
    try:
        pw._ensure(conn)
        assert conn.execute("SELECT COUNT(*) FROM onboard_paste_pending").fetchone()[0] == 0
    finally:
        conn.close()


def test_取得納管指令_混在一起_只登記檢查過的IPv4(env):
    client, tried, tmp = env
    r = client.post("/api/onboard/paste/issue", json={"ips": ["10.99.0.12", ESX_REG, "good-host.example"]})
    assert r.status_code == 200, r.text
    assert r.json()["issued"] == ["10.99.0.12"]
    assert [x["ip"] for x in r.json()["refused"]] == [ESX_REG]


# ===== P1-1 背景試連 =====
def _pending(conn, rows):
    pw._ensure(conn)
    for ip in rows:
        conn.execute("INSERT INTO onboard_paste_pending (ip, issued_at, state) VALUES (?, ?, 'waiting')",
                     (ip, "2026-11-09 01:00:00"))
    conn.commit()


def test_舊的等待列是ESXi或主機名稱_零次SSH_標過期(env):
    _client, _tried, tmp = env
    from datetime import datetime
    conn = db.get_connection(Path(tmp) / "t.db")
    try:
        _pending(conn, [ESX_REG, ESX_NAME, "10.99.0.77", "::ffff:10.99.0.11", "::ffff:10.99.0.12"])
        probes, roots = [], []
        pw.check(conn, probe=lambda ip, acct: probes.append(ip) or (False, "x"), root_probe=lambda ip: roots.append(ip),
                 collect=lambda s: {}, post_start=lambda s, b: None, now=datetime(2026, 11, 9, 2, 0, 0))
        assert probes == ["10.99.0.77"] and roots == []      # 只有那個不是 VMware 的 IP 去試
        st = {r["ip"]: (r["state"], r["reason"]) for r in conn.execute("SELECT ip, state, reason FROM onboard_paste_pending")}
        assert st[ESX_REG][0] == "expired" and "VMware" in st[ESX_REG][1]
        assert st[ESX_NAME][0] == "expired" and "IPv4" in st[ESX_NAME][1]
        assert st["::ffff:10.99.0.11"][0] == "expired"
        assert st["::ffff:10.99.0.12"][0] == "expired" and "IPv4" in st["::ffff:10.99.0.12"][1]   # 不是標準寫法：不連
        assert st["10.99.0.77"][0] != "expired"      # 這個 IP 本身不是 VMware（沒登記、沒 PTR）→ 照常試
    finally:
        conn.close()


def test_等待列_PTR反查失敗_這輪不連_不標過期(env, monkeypatch):
    _client, _tried, tmp = env
    from datetime import datetime
    monkeypatch.setattr(oe, "_ptr_lookup", lambda ip: None)
    conn = db.get_connection(Path(tmp) / "t.db")
    try:
        _pending(conn, ["10.99.0.12"])
        probes = []
        pw.check(conn, probe=lambda ip, acct: probes.append(ip) or (False, "x"), root_probe=lambda ip: False,
                 collect=lambda s: {}, post_start=lambda s, b: None, now=datetime(2026, 11, 9, 2, 0, 0))
        assert probes == []
        r = conn.execute("SELECT state, reason FROM onboard_paste_pending").fetchone()
        assert r["state"] == "waiting" and r["reason"] == oe.PTR_FAIL_ERROR
    finally:
        conn.close()


def test_等待列_一般IP照常試(env):
    _client, _tried, tmp = env
    from datetime import datetime
    conn = db.get_connection(Path(tmp) / "t.db")
    try:
        _pending(conn, ["10.99.0.12"])
        probes = []
        pw.check(conn, probe=lambda ip, acct: probes.append(ip) or (False, "x"), root_probe=lambda ip: False,
                 collect=lambda s: {}, post_start=lambda s, b: None, now=datetime(2026, 11, 9, 2, 0, 0))
        assert probes == ["10.99.0.12"]
    finally:
        conn.close()


# ===== P1-2 DNS 健康檢查 =====
def _auto_conn(tmp, ip="10.99.0.61"):
    p = Path(tmp) / "a.db"
    db.init_db(p)
    conn = db.get_connection(p)
    ao.save_segment(conn, "10.99.0.")
    conn.execute("INSERT INTO hardware (asset_serial, ip) VALUES (?, ?)", (f"H-{ip}", ip))
    conn.execute("INSERT INTO scan_history (scan_time, ip, hostname, scan_ok, open_ports) VALUES (?,?,?,1,?)",
                 ("2026-11-09 01:00:00", ip, None, "22"))
    conn.commit()
    return conn


class _Exec:
    def __init__(self):
        self.calls = []

    def __call__(self, host, username, password, platform, script, collector_ip, **kw):
        self.calls.append(host)
        return eng.OnboardResult(True, "execute", "完成", "完成。")


_cred = lambda ip: ("ssh-lab", "svc_boot", "FAKE-TEST-PW-r7")   # noqa: E731


def _run(tmp, monkeypatch, *, vc=None, local=(), resolves=(), ptr=lambda ip: set()):
    monkeypatch.setattr(oe, "esxi_host_names", lambda conn: set())
    monkeypatch.setattr(oe, "vcenter_fqdns", lambda conn, limit=3: list(vc or []))
    monkeypatch.setattr(oe, "_local_only_names", lambda: set(local))
    monkeypatch.setattr(oe, "_resolve_ips", lambda h: {"10.99.0.200"} if h in resolves else (
        {h} if oe.canon_ip(h) else set()))
    monkeypatch.setattr(oe, "_ptr_lookup", ptr)
    conn = _auto_conn(tmp)
    ex = _Exec()
    r = ao.run_auto_onboard(conn, "10.99.0.221", executor=ex, cred_lookup=_cred)
    audits = [x["message"] for x in conn.execute("SELECT message FROM onboard_audit WHERE target_ip='10.99.0.61'")]
    conn.close()
    return r, ex.calls, audits


def test_DNS健康_vCenter名稱解析得到_沒有PTR照常納管(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        r, calls, _ = _run(tmp, monkeypatch, vc=["vc-esx01.corp.example"], resolves={"vc-esx01.corp.example"})
        assert calls == ["10.99.0.61"]


def test_DNS異常_整輪不納管_每台留稽核(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        r, calls, audits = _run(tmp, monkeypatch, vc=["vc-esx01.corp.example"], resolves=set())
        assert calls == [] and r["details"][0]["action"] == "skipped"
        assert audits and audits[0].startswith("DNS 異常，本輪不納管")


def test_DNS健康_hosts檔裡的名稱不算數_走最嚴格(monkeypatch):
    # 2.058.19 審查 P1：vCenter 名稱寫在 hosts 檔＝DNS 壞了也解析得到，不能拿來證明 DNS 正常
    with tempfile.TemporaryDirectory() as tmp:
        r, calls, audits = _run(tmp, monkeypatch, vc=["vc-esx01.corp.example"], local={"vc-esx01.corp.example"},
                                resolves={"vc-esx01.corp.example"}, ptr=lambda ip: set())
        assert calls == [] and audits == [oe.PTR_FAIL_ERROR]     # 沒有 PTR 也不登入（"all"）


def test_resolver_health_不再用收集器自己的名字(monkeypatch):
    import socket
    monkeypatch.setattr(oe, "vcenter_fqdns", lambda conn, limit=3: [])
    monkeypatch.setattr(oe, "_resolve_ips", lambda h: {"10.99.0.1"})      # 什麼名字都「解析得到」
    monkeypatch.setattr(socket, "getfqdn", lambda *a: "collector.corp.example")
    assert oe.resolver_health(None) == ("none", None)
    assert not hasattr(oe, "_collector_fqdn")


def test_resolver_health_hosts檔讀取出錯_當作分不出來(monkeypatch):
    def boom():
        raise PermissionError("hosts")
    monkeypatch.setattr(oe, "_local_only_names", boom)
    monkeypatch.setattr(oe, "vcenter_fqdns", lambda conn, limit=3: ["a.example"])
    monkeypatch.setattr(oe, "_resolve_ips", lambda h: {"10.99.0.1"})
    assert oe.resolver_health(None) == ("none", None)


def test_local_only_names_讀hosts檔(monkeypatch, tmp_path):
    p = tmp_path / "hosts"
    p.write_text("127.0.0.1 localhost\n10.99.0.5  VC-ESX01.corp.example. alias5  # 註解 x.example\n", encoding="utf-8")
    monkeypatch.setattr(oe, "_hosts_file_path", lambda: str(p))
    got = oe._local_only_names()
    assert {"localhost", "vc-esx01.corp.example", "alias5"} <= got and "x.example" not in got


def test_is_vmware_platform_規則讀不到_登入判定拒絕(monkeypatch):
    import os_class

    def boom(*a, **k):
        raise ValueError("rules")
    monkeypatch.setattr(os_class, "classify", boom)
    monkeypatch.setattr(oe, "esxi_host_names", lambda conn: set())
    monkeypatch.setattr(oe, "_ptr_lookup", lambda ip: set())
    with tempfile.TemporaryDirectory() as tmp:
        conn = _auto_conn(tmp)
        try:
            targets, why = oe.login_targets(conn, "10.99.0.61")
        finally:
            conn.close()
    assert targets == [] and why and "不登入" in why


def test_沒有可測的名稱_沒有PTR也不登入_有PTR才登(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        r, calls, audits = _run(tmp, monkeypatch, ptr=lambda ip: set())
        assert calls == [] and audits == [oe.PTR_FAIL_ERROR]
    with tempfile.TemporaryDirectory() as tmp:
        r, calls, _ = _run(tmp, monkeypatch, ptr=lambda ip: {"web61.corp.example"})
        assert calls == ["10.99.0.61"]


def test_DNS健康_逾時或其他錯誤仍然拒絕(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        r, calls, audits = _run(tmp, monkeypatch, vc=["vc-esx01.corp.example"], resolves={"vc-esx01.corp.example"},
                                ptr=lambda ip: None)
        assert calls == [] and audits == [oe.PTR_FAIL_ERROR]


def test_resolver_health_分支(monkeypatch):
    monkeypatch.setattr(oe, "vcenter_fqdns", lambda conn, limit=3: [])
    monkeypatch.setattr(oe, "_local_only_names", lambda: set())
    assert oe.resolver_health(None) == ("none", None)
    monkeypatch.setattr(oe, "vcenter_fqdns", lambda conn, limit=3: ["a.example", "b.example"])
    monkeypatch.setattr(oe, "_resolve_ips", lambda h: {"10.99.0.1"} if h == "b.example" else set())
    assert oe.resolver_health(None) == ("ok", "b.example")
    monkeypatch.setattr(oe, "_resolve_ips", lambda h: set())
    assert oe.resolver_health(None) == ("fail", "a.example")


def test_vcenter_fqdns_只取完整名稱():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "v.db"
        db.init_db(p)
        conn = db.get_connection(p)
        try:
            import json
            for h in ("esx01.corp.example", "10.99.0.5", "esx02", "esx03.corp.example."):
                conn.execute("INSERT INTO source_record (source, payload) VALUES ('vcenter', ?)", (json.dumps({"esxi_host": h}),))
            conn.commit()
            assert oe.vcenter_fqdns(conn) == ["esx01.corp.example", "esx03.corp.example"]
        except Exception as exc:  # source_record 欄位不同的話顯示出來
            raise AssertionError(f"source_record 結構：{exc}")
        finally:
            conn.close()


# ===== P2 =====
def test_AST守門_login_address只在onboard_eligibility():
    bad = []
    for p in BACK.rglob("*.py"):
        if p.name == "onboard_eligibility.py" or ".venv" in p.parts:
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for n in ast.walk(tree):
            if isinstance(n, ast.Attribute) and n.attr in ("_login_address", "login_address"):
                bad.append(f"{p.name}:{n.lineno}")
            if isinstance(n, ast.ImportFrom) and any(a.name in ("_login_address", "login_address") for a in n.names):
                bad.append(f"{p.name}:{n.lineno}")
            if isinstance(n, ast.Name) and n.id in ("_login_address", "login_address"):
                bad.append(f"{p.name}:{n.lineno}")
            if isinstance(n, ast.Constant) and n.value in ("_login_address", "login_address"):
                bad.append(f"{p.name}:{n.lineno}（getattr 字串）")
    assert bad == []


def test_驗證_連的是檢查過的那個IPv4(env, monkeypatch):
    client, tried, _ = env
    import collect_account_migration as cam
    import manage_state
    settled, collected = [], []
    monkeypatch.setattr(cam, "settle_after_onboard", lambda conn, ip, platform: settled.append(ip) or {"ok": True})
    monkeypatch.setattr(manage_state, "collect_facts_into_assets",
                        lambda conn, only_serial=None, **k: collected.append(only_serial) or {"updated": 0, "failed": []})
    r = client.post("/api/onboard/verify", json={"ip": "good-host.example", "platform": "linux"})
    assert r.status_code == 200, r.text
    assert settled == ["10.99.0.12"] and collected == ["AUTO-10.99.0.12"]


def test_主機名稱解析出的每一個IPv4都判(env):
    client, tried, _ = env
    for path, body in (("/api/onboard", {"platform": "linux", "username": "root", "password": "x1x"}),
                       ("/api/onboard/verify", {"platform": "linux"})):
        r = client.post(path, json={"ip": "two-ips.example", **body})
        if path == "/api/onboard":
            assert r.status_code == 400 and "VMware" in r.text, r.text
        else:
            assert r.json()["ok"] is False and "VMware" in r.json()["error"], r.text


def test_批次自動納管_連線前每台再過一次(env, monkeypatch):
    _client, _tried, tmp = env
    import batch_onboard_service as bos
    calls = []
    monkeypatch.setattr(eng, "batch_auto_onboard", lambda targets, **kw: calls.append(targets) or [
        {"ip": targets[0]["ip"], "login_ok": False, "password_index": None, "platform": None, "onboarded": False,
         "password_unified": False, "fail_stage": "connect", "fail_reason": "x"}])
    import onboard_bundle
    monkeypatch.setattr(onboard_bundle, "bundle_for", lambda conn: "#!/bin/sh\n")
    monkeypatch.setattr(eng, "collector_pubkey", lambda *a, **k: "ssh-ed25519 AAAA test")
    monkeypatch.setattr(eng, "resolve_collector_ip", lambda conn: "10.99.0.221")
    dbp = Path(tmp) / "t.db"
    conn = db.get_connection(dbp)
    try:
        conn.execute("INSERT INTO batch_onboard_run (triggered_by, target_count, unify, status, started_at, targets_json) "
                     "VALUES ('t', 2, 0, 'running', '2026-11-09 01:00:00', '[]')")
        run_id = conn.execute("SELECT MAX(id) FROM batch_onboard_run").fetchone()[0]
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(bos, "get_connection", lambda p: db.get_connection(dbp))
    bos._do_batch(dbp, run_id, [{"ip": ESX_REG}, {"ip": "10.99.0.12"}], "root", ["x1x"], False)
    assert [t[0]["ip"] for t in calls] == ["10.99.0.12"]
    conn = db.get_connection(dbp)
    try:
        r = conn.execute("SELECT fail_stage, fail_reason FROM batch_onboard_result WHERE ip = ?", (ESX_REG,)).fetchone()
        assert r["fail_stage"] == "refused" and "VMware" in r["fail_reason"]
    finally:
        conn.close()


def test_主機名稱_每一個解析出的IPv4都判_DNS輪替也不漏(env, monkeypatch):
    """DNS 輪替：第一次解析回兩個 IP（其中一個是 ESXi），之後只回另一個。關卡要判第一次解析出的**每一個** IPv4，
    不能只判要連的那一個、再靠 vmware_targets 自己重新解析（重新解析可能拿到不同的一組）。"""
    client, tried, _ = env
    n = {"c": 0}

    def rr(host, *a, **k):
        if host == "rr-host.example":
            n["c"] += 1
            ips = ["10.99.0.10", ESX_REG] if n["c"] == 1 else ["10.99.0.10"]
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in ips]
        return _gai(host, *a, **k)
    monkeypatch.setattr(socket, "getaddrinfo", rr)
    r = client.post("/api/onboard", json={"ip": "rr-host.example", "platform": "linux", "username": "root", "password": "x1x"})
    assert r.status_code == 400 and "VMware" in r.text, r.text
