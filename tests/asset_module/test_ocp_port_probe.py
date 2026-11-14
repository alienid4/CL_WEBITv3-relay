"""新主機掃描就認出 OCP（2026-10-22，第一層：探 10250）。

使用者：「做」。前提是正式區收集端只申請開通 22／445／3389／5985——探 10250 要先向網路單位
加申請，所以**預設關**、開關在 6 系統設定，關著時行為要跟以前一模一樣。
開著＋10250 有回應 → 歸 OCP 頁簽、不試連（fail-closed）；VMware 判定仍最優先；人工指定可搬回 SSH。

IP 一律範例位址（192.0.2.x），主機名是假的。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import collect_dispatch as cd  # noqa: E402
import db  # noqa: E402

SETTINGS_VUE = (ROOT / "APP" / "asset-module" / "frontend" / "pages" / "settings" / "index.vue"
                ).read_text(encoding="utf-8")


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _hw(conn, serial, ip, os_text, hostname):
    db.insert_hardware(conn, asset_serial=serial, ip=ip, os=os_text, hostname=hostname,
                       environment="測試", asset_status="使用中")
    conn.commit()


def _on(conn):
    cd.save_dispatch_settings(conn, {"ocp_port_probe": 1})


def _ports(*ports):
    return lambda ip: list(ports)


def _spy():
    called = []
    return called, (lambda ip: called.append(ip) or (False, "Permission denied (publickey)."))


def _run(conn, ip, prober, ssh_prober):
    return cd.run_dispatch(conn, ip, prober=prober, ssh_prober=ssh_prober,
                           ttl_prober=lambda _ip: None)["results"][0]


# ===================== 開關 =====================

def test_開關預設關_可存可讀_超出範圍拒絕():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        s = cd.dispatch_settings(conn)
        assert s["ocp_port_probe"] == 0 and s["default"]["ocp_port_probe"] == 0
        assert cd.ocp_port_probe_on(conn) is False
        _on(conn)
        assert cd.ocp_port_probe_on(conn) is True
        try:
            cd.save_dispatch_settings(conn, {"ocp_port_probe": 2})
            raise AssertionError("2 應該被拒")
        except ValueError:
            pass
        conn.close()


def _captured_ports(conn, monkeypatch):
    import fast_probe
    seen = []

    def fake(ip, ports, timeout=0.6, icmp=True, ping=None):
        seen.append(tuple(ports))
        return [22]
    monkeypatch.setattr(fast_probe, "probe_host", fake)
    _c, ssh = _spy()
    cd.run_dispatch(conn, "192.0.2.50", ssh_prober=ssh, ttl_prober=lambda _ip: None)
    return seen


def test_開關關著_預設探測器不探10250(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        assert _captured_ports(conn, monkeypatch) == [cd.DISPATCH_PORTS]
        conn.close()


def test_開關開著_預設探測器只多探10250一個埠(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _on(conn)
        assert _captured_ports(conn, monkeypatch) == [cd.DISPATCH_PORTS + (10250,)]
        conn.close()


# ===================== 判定 =====================

def test_開關關著_就算10250有回應_行為照舊():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        called, ssh = _spy()
        r = _run(conn, "192.0.2.51", _ports(22, 10250), ssh)
        assert called == ["192.0.2.51"], "關著就照舊試連"
        assert r["tab"] == cd.TAB_SSH and r["status"] != cd.STATUS_NOT_ONBOARDABLE
        conn.close()


def test_開關開著_10250有回應_歸OCP_不試連():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _on(conn)
        called, ssh = _spy()
        for ip, ports in (("192.0.2.52", (22, 10250)), ("192.0.2.53", (10250,))):
            r = _run(conn, ip, _ports(*ports), ssh)
            assert r["status"] == cd.STATUS_NOT_ONBOARDABLE, r
            assert r["not_onboardable_kind"] == "immutable" and r["tab"] == cd.TAB_OCP
            assert "10250（kubelet）有回應" in r["message"] and "疑似 OCP 節點" in r["message"]
        assert called == [], "fail-closed：不試連"
        conn.close()


def test_開關開著_10250沒開_照舊():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _on(conn)
        called, ssh = _spy()
        r = _run(conn, "192.0.2.54", _ports(22), ssh)
        assert called == ["192.0.2.54"]
        assert r["tab"] == cd.TAB_SSH
        conn.close()


def test_VMware仍最優先_登記ESXi同時開10250仍歸ESXi():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _on(conn)
        _hw(conn, "E-55", "192.0.2.55", "VMware ESXi 8.0.3", "esx-example-55")
        called, ssh = _spy()
        r = _run(conn, "192.0.2.55", _ports(22, 10250), ssh)
        assert r["tab"] == cd.TAB_ESXI and r["not_onboardable_kind"] == "esxi"
        assert called == []
        conn.close()


def test_人工指定搬回SSH_10250有回應也照樣試連():
    import os_override
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _on(conn)
        _hw(conn, "R-56", "192.0.2.56", "Red Hat Enterprise Linux 9", "rhel-example-56")
        os_override.set_override(conn, "rhel-example-56", "192.0.2.56", "R-56",
                                 cd.os_type_for_tab(cd.TAB_SSH), None, "誤判", "tester")
        called, ssh = _spy()
        r = _run(conn, "192.0.2.56", _ports(22, 10250), ssh)
        assert called == ["192.0.2.56"]
        assert r["tab"] == cd.TAB_SSH
        conn.close()


# ===================== 畫面 =====================

def test_系統設定有開關_註明要先申請開通():
    assert 'v-model="dEdit.ocp_port_probe"' in SETTINGS_VUE
    assert "ocp_port_probe: dEdit.ocp_port_probe ? 1 : 0" in SETTINGS_VUE
    assert "申請開通" in SETTINGS_VUE and "TCP 10250" in SETTINGS_VUE
