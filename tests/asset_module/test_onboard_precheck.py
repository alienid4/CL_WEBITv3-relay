"""預檢（只看不動，v1.468.9）。

2026-10-19 使用者（公司機）：怕把網路設備、或不是 RHEL／CentOS／Debian 的機器納管進來。
預檢＝唯讀登入一次，用**一鍵納管同一支探測、同一份平台白名單**下結論；不建帳號、不跑腳本、
不寫 onboard_audit，密碼不落地。例子 IP 一律 10.99.x，密碼一律假值。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACK = ROOT / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACK))

import onboard_engine as oe  # noqa: E402
import precheck_service as ps  # noqa: E402

FE = ROOT / "APP" / "asset-module" / "frontend"
MODAL = (FE / "components" / "PrecheckModal.vue").read_text(encoding="utf-8")
ADOPT = (FE / "pages" / "adopt.vue").read_text(encoding="utf-8")
ROUTER = (BACK / "routers" / "onboard.py").read_text(encoding="utf-8")
FAKE_PW = "Fake#Pw-123"

RAW = {
    "10.99.4.1": 'Linux\n0\nHASSUDO\nOSREL_ID=rhel\nOSREL_PRETTY_NAME="Red Hat Enterprise Linux 8.9 (Ootpa)"\n',
    "10.99.4.2": 'Linux\n1001\nNOSUDO\nOSREL_ID=ubuntu\nOSREL_PRETTY_NAME="Ubuntu 22.04.4 LTS"\n',
    "10.99.4.3": 'Linux\n0\nHASSUDO\nOSREL_ID=almalinux\nOSREL_PRETTY_NAME="AlmaLinux 9.3"\n',
    "10.99.4.4": 'Linux\n0\nHASSUDO\nOSREL_ID=debian\nOSREL_PRETTY_NAME="Debian 11"\nAPPLIANCE_MARKER=vyos\n',
    "10.99.4.5": "VMkernel\n0\nNOSUDO\nVMware ESXi 8.0.2 build-1\n",
    "10.99.4.6": "AIX\n0\nNOSUDO\n",
    "10.99.4.7": "% Invalid input detected at '^' marker.\n",
}


def _runner(host, username, password):
    assert password == FAKE_PW
    return RAW[host]


def _by(out):
    return {r["ip"]: r for r in out["results"]}


def test_結論跟一鍵納管同一份白名單():
    out = ps.run(list(RAW), "root", FAKE_PW, runner=_runner)
    by = _by(out)
    assert by["10.99.4.1"]["verdict"] == ps.V_OK and "Red Hat Enterprise Linux 8.9" in by["10.99.4.1"]["pretty_name"]
    # 不是 root 又沒 sudo：一鍵納管會被拒——預檢不可以說「可以納管」（審查 #3）
    assert by["10.99.4.2"]["verdict"] == ps.V_NEED_ROOT and not by["10.99.4.2"]["onboardable"]
    assert by["10.99.4.3"]["verdict"] == ps.V_NO, "Alma 不在白名單（同一份 onboard_platform_rules.json）"
    assert by["10.99.4.4"]["verdict"] == ps.V_NO and by["10.99.4.4"]["appliance"] == ["vyos"]
    assert by["10.99.4.5"]["verdict"] == ps.V_NO and "ESXi" in by["10.99.4.5"]["reason"], "VMware 天條"
    assert by["10.99.4.6"]["verdict"] == ps.V_OK, "AIX 可以（走 ksh 一鍵納管）"
    assert by["10.99.4.7"]["verdict"] == ps.V_NO, "非 POSIX shell（網路設備）→ 不納管"
    # 跟引擎同一個判斷：引擎會拒絕的，預檢一定說不會納管
    for ip, raw in RAW.items():
        p = oe.parse_probe(raw)
        if oe.vmware_refusal(p["os"]) or p.get("refusal"):
            assert by[ip]["verdict"] == ps.V_NO, ip


def test_帳密被拒_還沒成功過就自動停止_沒試的標未執行(monkeypatch):
    calls = []

    def fake_probe(host, username, password, timeout=20, runner=None):
        calls.append(host)
        return {"os": "", "uid": None, "has_sudo": False,
                "error": "登入被拒——帳號或密碼不對，或該帳號不允許 SSH 登入"}
    monkeypatch.setattr(oe, "probe_target", fake_probe)
    ips = [f"10.99.4.{i}" for i in range(10, 20)]
    out = ps.run(ips, "root", FAKE_PW, concurrency=1)
    assert len(calls) == 1 and out["auto_stopped"]
    assert out["counts"][ps.V_STOPPED] == 9 and out["counts"][ps.V_LOGIN] == 1
    assert all(r["verdict"] in (ps.V_LOGIN, ps.V_STOPPED) for r in out["results"])


def test_密碼不出現在回傳裡():
    out = ps.run(list(RAW), "root", FAKE_PW, runner=_runner)
    assert FAKE_PW not in repr(out)


def test_只看不動_不碰納管腳本與稽核():
    src = (BACK / "precheck_service.py").read_text(encoding="utf-8")
    for bad in ("onboard(", "run_bootstrap", "onboard_audit", "INSERT", "UPDATE", "DELETE", "sudoers"):
        code = re.sub(r'""".*?"""', "", src, flags=re.S)
        code = re.sub(r"#.*", "", code)
        assert bad not in code, f"預檢不可以 {bad}"
    seg = ROUTER.partition('"/api/onboard/precheck"')[2][:2500]
    assert "Depends(require_auth)" in seg and "body.password" in seg
    assert "password" not in seg.split("action=")[1][:300], "操作紀錄不可以有密碼"


def test_前端_單台與批次都有_密碼送出就清():
    assert "@precheck=\"(ip: string) => { precheckIps = [ip] }\"" in ADOPT
    assert "precheckIps = [...probeSelected]" in ADOPT
    assert "precheckIps = [...tagSelected]" not in ADOPT and "precheckIps = [...moveSelected]" not in ADOPT
    assert "cred.password = ''" in MODAL.split("async function run()")[1][:300]
    assert re.search(r'<SortTh k="verdict"', MODAL)
    assert "不建帳號、不佈金鑰、不改 sudoers、不跑任何納管腳本" in MODAL


def test_先有一台成功_之後的帳密被拒不會停(monkeypatch):
    seq = {"10.99.4.30": {"os": "linux", "uid": 0, "has_sudo": True, "os_release": {"ID": "rhel"}},
           "10.99.4.31": {"os": "", "uid": None, "has_sudo": False, "error": "登入被拒——帳號或密碼不對"},
           "10.99.4.32": {"os": "", "uid": None, "has_sudo": False, "error": "登入被拒——帳號或密碼不對"}}
    monkeypatch.setattr(oe, "probe_target", lambda host, u, p, timeout=20, runner=None: dict(seq[host]))
    out = ps.run(list(seq), "root", FAKE_PW, concurrency=1)
    assert not out["auto_stopped"] and out["counts"][ps.V_LOGIN] == 2


def test_某台判斷出錯_照實寫出錯_不被說成已停止(monkeypatch):
    def boom(host, u, p, timeout=20, runner=None):
        raise RuntimeError(f"secret {p}")
    monkeypatch.setattr(oe, "probe_target", boom)
    out = ps.run(["10.99.4.40", "10.99.4.41"], "root", FAKE_PW, concurrency=2)
    assert all(r["verdict"] == ps.V_LOGIN and "預檢出錯（RuntimeError）" in r["reason"] for r in out["results"])
    assert FAKE_PW not in repr(out), "例外訊息不可以把密碼帶出來"


def test_只接受IP():
    import pytest
    with pytest.raises(ValueError):
        ps.run(["10.99.4.1", "db.example"], "root", FAKE_PW, runner=_runner)
