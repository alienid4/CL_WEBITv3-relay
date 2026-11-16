"""2.058.15（批 23 最後一輪審查 8f638d57）：
P1——排程自動納管登入前也過共用關卡 login_target_refusal（只有 IP 的 AUTO 登記＋vCenter 名單只有 FQDN＋PTR 指過去 → 擋下、留稽核）。
P2——scrub_secret 目標機自報欄位（appliance／os_id／version_id／refusal…）也看；自動納管 IP 欄去空白、拆多 IP，拒絕的留原因；
主機名稱連「檢查過的那個 IPv4」；驗證接受主機名稱；批次自動納管／自動納管標準寫法／驗證 IPv4／憑證測試關卡的守門。"""
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

ESX_FQDN = "esx31.corp.example"


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _scan(conn, ip, ports="22"):
    conn.execute("INSERT INTO scan_history (scan_time, ip, hostname, scan_ok, open_ports) VALUES (?,?,?,1,?)",
                 ("2026-11-09 01:00:00", ip, None, ports))
    conn.commit()


class _Exec:
    def __init__(self):
        self.calls = []

    def __call__(self, host, username, password, platform, script, collector_ip, **kw):
        self.calls.append(host)
        return eng.OnboardResult(True, "execute", "納管腳本執行完成", "完成。")


_cred = lambda ip: ("ssh-lab", "svc_boot", "FAKE-TEST-PW-not-real-R5")   # noqa: E731


@pytest.fixture
def ptr(monkeypatch):
    """vCenter ESXi 名單只有 FQDN；10.99.0.31 的 PTR 指到它；其他 IP 沒有 PTR。"""
    monkeypatch.setattr(oe, "esxi_host_names", lambda conn: oe._name_keys(ESX_FQDN))
    monkeypatch.setattr(oe, "_ptr_lookup", lambda ip: {ESX_FQDN} if ip == "10.99.0.31" else set())
    monkeypatch.setattr(oe, "resolver_health", lambda conn: ("ok", "vc.example"))


# ===== P1 =====
def test_P1_排程自動納管_只有IP的AUTO登記_PTR指到vCenter的ESXi_零次登入(ptr):
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            ao.save_segment(conn, "10.99.0.")
            conn.execute("INSERT INTO hardware (asset_serial, ip) VALUES (?, ?)", ("AUTO-10.99.0.31", "10.99.0.31"))
            conn.commit()
            _scan(conn, "10.99.0.31")
            ex = _Exec()
            r = ao.run_auto_onboard(conn, "10.99.0.221", executor=ex, cred_lookup=_cred)
            assert ex.calls == []
            d = [x for x in r["details"] if x["ip"] == "10.99.0.31"]
            assert d and d[0]["action"] == "skipped" and "VMware" in d[0]["message"]
            a = conn.execute("SELECT stage, message, trigger FROM onboard_audit WHERE target_ip = ?", ("10.99.0.31",)).fetchall()
            assert a and "VMware" in a[-1]["message"] and a[-1]["trigger"] == "auto"
        finally:
            conn.close()


def test_P1_判不出來也不登入(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            ao.save_segment(conn, "10.99.0.")
            conn.execute("INSERT INTO hardware (asset_serial, ip) VALUES (?, ?)", ("AUTO-10.99.0.32", "10.99.0.32"))
            conn.commit()
            _scan(conn, "10.99.0.32")

            def boom(*a, **k):
                raise RuntimeError("db locked")
            monkeypatch.setattr(oe, "vmware_targets", boom)
            ex = _Exec()
            r = ao.run_auto_onboard(conn, "10.99.0.221", executor=ex, cred_lookup=_cred)
            assert ex.calls == [] and "無法確認" in r["details"][0]["message"]
        finally:
            conn.close()


# ===== P2-2 =====
def test_P2_自動納管IP欄_去空白_拆多IP_標準寫法_拒絕的留原因(monkeypatch):
    monkeypatch.setattr(oe, "_ptr_lookup", lambda ip: set())
    monkeypatch.setattr(oe, "resolver_health", lambda conn: ("ok", "vc.example"))
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            ao.save_segment(conn, "10.99.0.")
            for serial, ip in (("A-43", " 10.99.0.43"), ("A-41", "10.99.0.41, 10.99.0.42"),
                               ("A-46", "::ffff:10.99.0.46"), ("A-45", "010.99.0.45")):
                conn.execute("INSERT INTO hardware (asset_serial, ip) VALUES (?, ?)", (serial, ip))
            conn.commit()
            for ip in ("10.99.0.41", "10.99.0.42", "10.99.0.43", "10.99.0.46"):
                _scan(conn, ip)
            _scan(conn, "::ffff:10.99.0.46")   # 讓那筆的四態判定是「未納管」（summarize 用登記的原字串對掃描）
            cands = {c["ip"]: c for c in ao.find_candidates(conn)}
            assert {"10.99.0.41", "10.99.0.43", "10.99.0.46"} <= set(cands) and "10.99.0.42" not in cands
            assert cands["10.99.0.41"]["ips"] == ["10.99.0.41", "10.99.0.42"]   # 2.058.16：一筆登記＝一個候選
            assert " 10.99.0.43" not in cands and "::ffff:10.99.0.46" not in cands
            assert cands["010.99.0.45"]["platform"] == "invalid"
            ex = _Exec()
            r = ao.run_auto_onboard(conn, "10.99.0.221", executor=ex, cred_lookup=_cred)
            assert sorted(ex.calls) == ["10.99.0.41", "10.99.0.43", "10.99.0.46"]
            bad = [d for d in r["details"] if d["ip"] == "010.99.0.45"]
            assert bad and bad[0]["action"] == "skipped" and "只接受 IPv4" in bad[0]["message"]
            assert conn.execute("SELECT 1 FROM onboard_audit WHERE target_ip = ? AND message LIKE ?",
                                ("010.99.0.45", "%只接受 IPv4%")).fetchone()
        finally:
            conn.close()


# ===== P2-1 =====
def test_P2_scrub_目標機自報欄位也看():
    from _app_shared import scrub_secret as s, SECRET_HIDDEN as H
    row = {"ip": "10.99.0.1", "appliance": ["x pw9", "ok"], "os_id": "pw9", "version_id": "8.pw9",
           "refusal": "VMware pw9", "would": {"note": ["a pw9"]}, "uid": 0, "pretty_name": "RHEL"}
    assert s(row, "pw9") == {"ip": "10.99.0.1", "appliance": [H, "ok"], "os_id": H, "version_id": H,
                             "refusal": H, "would": {"note": [H]}, "uid": 0, "pretty_name": "RHEL"}


# ===== P2-3：主機名稱連檢查過的 IPv4；驗證接受主機名稱 =====
def _fake_gai(host, *a, **k):
    if host == "good-host.example":
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.99.0.12", 0))]
    raise socket.gaierror("no such host")


@pytest.fixture
def api_env(monkeypatch):
    from test_v2_port_b23_review2 import _setup
    tried = []
    monkeypatch.setattr(socket, "getaddrinfo", _fake_gai)
    with tempfile.TemporaryDirectory() as tmp:
        tb, client = _setup(tmp, monkeypatch, tried)
        try:
            yield client, tried, tmp
        finally:
            tb.api.app.dependency_overrides.clear()


def test_P2_主機名稱_連的是檢查過的IPv4_進度用主機名稱也查得到(api_env, monkeypatch):
    client, tried, _ = api_env
    seen = []

    def fake_onboard(**kw):
        seen.append(kw["host"])
        eng.progress_note(kw["host"], "[*] 測試中")
        return eng.OnboardResult(False, "connect", "假的")
    monkeypatch.setattr(eng, "onboard", fake_onboard)
    r = client.post("/api/onboard", json={"ip": "good-host.example", "platform": "linux", "username": "root", "password": "x1x"})
    assert r.status_code == 200, r.text
    assert seen == ["10.99.0.12"]
    assert eng.progress_of("good-host.example")["stage"] == "測試中"


def test_P2_驗證_IPv4以外不收集_主機名稱可以(api_env, monkeypatch):
    client, tried, tmp = api_env
    import manage_state
    got = []
    monkeypatch.setattr(manage_state, "collect_facts_into_assets",
                        lambda conn, only_serial=None, **k: got.append(only_serial) or {"updated": 0, "failed": []})
    import collect_account_migration as cam
    monkeypatch.setattr(cam, "settle_after_onboard", lambda conn, ip, platform: {"ok": True})
    for bad in ("64:ff9b::a63:b", "010.99.0.12", "esx-nowhere.example"):
        r = client.post("/api/onboard/verify", json={"ip": bad, "platform": "linux"})
        assert r.status_code == 200 and r.json()["ok"] is False and "收集未執行" in r.json()["error"], (bad, r.text)
    assert got == []
    conn = db.get_connection(Path(tmp) / "t.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM hardware WHERE asset_serial LIKE 'AUTO-%'").fetchone()[0] == 0
    finally:
        conn.close()
    r = client.post("/api/onboard/verify", json={"ip": "good-host.example", "platform": "linux"})
    assert r.status_code == 200, r.text
    assert got == ["AUTO-10.99.0.12"]


# ===== P2-4：批次自動納管每台關卡、憑證測試關卡 =====
def test_P2_批次自動納管_登入前每台過關卡(ptr, monkeypatch):
    import batch_onboard_service as bos
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            db.insert_hardware(conn, asset_serial="L-31", ip="10.99.0.31", os="Red Hat Enterprise Linux 8.8",
                               hostname="lnx31", environment="測試", asset_status="使用中")
            conn.commit()
            _scan(conn, "10.99.0.31")
            assert [x["ip"] for x in bos.classify_targets(conn, ["10.99.0.31"])["run"]] == ["10.99.0.31"]
            started = []
            monkeypatch.setattr(threading, "Thread", lambda *a, **k: started.append(k) or type("T", (), {"start": lambda self: None})())
            with pytest.raises(ValueError) as e:
                bos.start(conn, ["10.99.0.31"], "root", ["x1x"], unify_password=False, triggered_by="t")
            assert "VMware" in str(e.value) and started == []
        finally:
            conn.close()


def test_P2_憑證測試_登入前過VMware關卡(ptr, monkeypatch):
    import api
    import auth
    import credential_store as cs
    import winrm_collector
    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient

    key = Fernet.generate_key()
    monkeypatch.setattr(cs, "_load_or_create_key", lambda path=cs.KEY_PATH_DEFAULT: key)
    runs = []
    monkeypatch.setattr(winrm_collector, "_run", lambda *a, **k: runs.append(a[0]) or "HOST\nsvc")
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "t.db"
        db.init_db(db_path)

        def _dep():
            c = db.get_connection(db_path)
            try:
                yield c
            finally:
                c.close()
        api.app.dependency_overrides[api.get_db] = _dep
        try:
            client = TestClient(api.app)
            c = db.get_connection(db_path)
            db.create_user(c, "admin", auth.hash_password("s3cure-pass!"))
            c.close()
            assert client.post("/api/auth/login", json={"username": "admin", "password": "s3cure-pass!"}).status_code == 200
            assert client.post("/api/credentials", json={"name": "win-t", "kind": "winrm", "username": "svc_x",
                                                         "password": "FAKE-PW-r5", "scope": "10.99.0.", "note": "t"}).status_code in (200, 201)
            r = client.post("/api/credentials/win-t/test", json={"ip": "10.99.0.31"})
            assert r.status_code == 400 and "VMware" in r.text, r.text
            assert runs == []
        finally:
            api.app.dependency_overrides.clear()
