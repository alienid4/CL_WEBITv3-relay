"""預檢＝預告一鍵納管會做什麼（2026-10-20 使用者）。

原本預檢自己寫一份判斷：Windows 那台預檢說「不會納管，請開 WinRM」，一鍵納管卻實際走 Windows 那條。
改成兩邊呼叫同一組判斷（onboard_engine.onboard_decision／decide_after_probe）。這裡逐一把每種平台結果
丟給**真的 onboard()**（執行器換成記錄器、不碰網路）跟預檢，比對：
  · 預檢說會納管 ⇔ onboard() 真的叫了執行器（＝走到副作用那一步）
  · 會納管時，執行器收到的平台 ＝ 預檢說的平台（Windows 就是 Windows）
  · 不會納管時，onboard() 回的 stage ＝ 預檢說會停的那一關
例子 IP 一律 10.99.x，密碼一律假值。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import onboard_engine as oe  # noqa: E402
import precheck_service as ps  # noqa: E402

PUB = "ssh-ed25519 AAAATESTKEY webit3-collector"
CIP = "10.99.0.5"
PW = "Fake#Pw-123"
ACCTS = {"linux": "webit3sc", "aix": "webit3sc", "windows": "webit3sc"}

#: 平台結果 → 探測回的東西（raw 走 parse_probe；error 模擬登不進去）
CASES = {
    "linux_ok_root": {"raw": 'Linux\n0\nHASSUDO\nOSREL_ID=rhel\nOSREL_PRETTY_NAME="Red Hat Enterprise Linux 8.9"\n'},
    "linux_ok_sudo": {"raw": 'Linux\n1001\nHASSUDO\nOSREL_ID=rhel\nOSREL_PRETTY_NAME="Red Hat Enterprise Linux 9.2"\n'},
    "linux_refused_distro": {"raw": 'Linux\n0\nHASSUDO\nOSREL_ID=almalinux\nOSREL_PRETTY_NAME="AlmaLinux 9.3"\n'},
    "appliance_marker": {"raw": 'Linux\n0\nHASSUDO\nOSREL_ID=debian\nOSREL_PRETTY_NAME="Debian 11"\nAPPLIANCE_MARKER=vyos\n'},
    "aix_root": {"raw": "AIX\n0\nNOSUDO\n"},
    "aix_not_root_with_sudo": {"raw": "AIX\n205\nHASSUDO\n"},
    "windows": {"raw": "MINGW64_NT-10.0-19045\nWindows\n"},
    "esxi": {"raw": "VMkernel\n0\nNOSUDO\nVMware ESXi 8.0.2 build-1\n"},
    "vcsa": {"raw": "Linux\n0\nHASSUDO\nVMWARE_APPLIANCE\n"},
    "no_root_no_sudo": {"raw": 'Linux\n1001\nNOSUDO\nOSREL_ID=ubuntu\nOSREL_PRETTY_NAME="Ubuntu 22.04.4 LTS"\n'},
    "unknown_shell": {"raw": "% Invalid input detected at '^' marker.\n"},
    "login_fail": {"error": "登入被拒——帳號或密碼不對，或該帳號不允許 SSH 登入"},
}

EXPECT = {
    "linux_ok_root": (ps.V_OK, "linux"), "linux_ok_sudo": (ps.V_OK, "linux"),
    "linux_refused_distro": (ps.V_NO, None), "appliance_marker": (ps.V_NO, None),
    "aix_root": (ps.V_OK, "aix"), "aix_not_root_with_sudo": (ps.V_NEED_ROOT, None),
    "windows": (ps.V_OK, "windows"), "esxi": (ps.V_NO, None), "vcsa": (ps.V_NO, None),
    "no_root_no_sudo": (ps.V_NEED_ROOT, None), "unknown_shell": (ps.V_NO, None),
    "login_fail": (ps.V_LOGIN, None),
}


def _probe_of(case):
    c = CASES[case]
    if "error" in c:
        return {"os": "", "uid": None, "has_sudo": False, "error": c["error"]}
    return oe.parse_probe(c["raw"])


def _run_onboard(monkeypatch, case, platform):
    """真的 onboard()；探測換成這個案例，執行器換成記錄器。回 (result, 執行器收到的參數或 None)。"""
    monkeypatch.setattr(oe, "probe_target", lambda host, u, p, timeout=20, runner=None: _probe_of(case))
    seen = {}

    def recorder(**kw):
        seen.update(kw)
        return oe.OnboardResult(True, "execute", "ok", "")

    r = oe.onboard("10.99.7.1", platform, "sysinfra", PW, CIP, pubkey=PUB,
                   executor=recorder, account="webit3sc", script="#!/bin/sh\necho test\n",
                   probe_runner=lambda *a: "")   # 有給 probe_runner＝照樣探測（同真實路徑）
    return r, (seen or None)


def _run_precheck(monkeypatch, case, platform):
    monkeypatch.setattr(oe, "probe_target", lambda host, u, p, timeout=20, runner=None: _probe_of(case))
    return ps.run(["10.99.7.1"], "sysinfra", PW, platform=platform, collector_ip=CIP,
                  accounts=ACCTS)["results"][0]


@pytest.mark.parametrize("case", list(CASES))
def test_預檢結論等於一鍵納管實際做的事_畫面選linux(monkeypatch, case):
    pre = _run_precheck(monkeypatch, case, "linux")
    r, ran = _run_onboard(monkeypatch, case, "linux")
    want_verdict, want_plat = EXPECT[case]
    assert pre["verdict"] == want_verdict, (case, pre["reason"])
    assert pre["onboardable"] == (ran is not None), f"{case}：預檢說{pre['verdict']}，onboard() 執行器{'有' if ran else '沒'}被叫"
    if ran:
        assert ran["platform"] == pre["would"]["platform"] == want_plat, case
    else:
        assert r.stage == pre["would"]["stage"], (case, r.stage, pre["would"])
    assert PW not in repr(pre)


def test_Windows_預檢說會用Windows方式_而且一鍵納管真的跑Windows腳本(monkeypatch):
    pre = _run_precheck(monkeypatch, "windows", "linux")
    assert pre["verdict"] == ps.V_OK and "會用 Windows 方式納管" in pre["reason"]
    assert "WinRM" not in pre["reason"], "舊的錯誤說法（請開 WinRM）不可以再出現"
    _, ran = _run_onboard(monkeypatch, "windows", "linux")
    assert ran["platform"] == "windows" and "New-LocalUser" in ran["script"]


@pytest.mark.parametrize("case", ["linux_ok_root", "windows", "unknown_shell"])
def test_畫面選Windows_一鍵納管不探測直接走Windows_預檢照實說(monkeypatch, case):
    pre = _run_precheck(monkeypatch, case, "windows")
    _, ran = _run_onboard(monkeypatch, case, "windows")
    assert ran is not None and ran["platform"] == "windows"
    assert pre["verdict"] == ps.V_OK and pre["would"]["platform"] == "windows"


def test_收集器位址不對_兩邊都在連線前停(monkeypatch):
    monkeypatch.setattr(oe, "probe_target", lambda *a, **k: pytest.fail("不可以連線"))
    pre = ps.run(["10.99.7.1"], "sysinfra", PW, collector_ip="YOUR_SERVER_IP", accounts=ACCTS)["results"][0]
    r = oe.onboard("10.99.7.1", "linux", "sysinfra", PW, "YOUR_SERVER_IP", pubkey=PUB,
                   executor=lambda **k: pytest.fail("不可以執行"), script="x")
    assert pre["verdict"] == ps.V_NO and not r.ok and r.stage == pre["would"]["stage"] == "connect"
    assert pre["reason"] == r.message


def test_預檢沒有自己的判斷_一律走onboard_decision():
    src = (ROOT / "APP" / "asset-module" / "backend" / "precheck_service.py").read_text(encoding="utf-8")
    assert "onboard_decision(" in src and "decide_after_probe(" in src
    for own in ("platform_refusal(", "vmware_refusal(", "請開 WinRM", "has_sudo\"]"):
        assert own not in src, f"預檢不可以自己判斷（{own}）——要跟一鍵納管同一支"


@pytest.mark.parametrize("case", ["esxi", "vcsa"])
def test_畫面選Windows_VMware設備照樣擋_引擎跟預檢一致(monkeypatch, case):
    """2026-10-20 獨立審查：原本選 Windows 完全不探測，VMware 設備選成 Windows 引擎沒守門（天條漏洞）。"""
    pre = _run_precheck(monkeypatch, case, "windows")
    r, ran = _run_onboard(monkeypatch, case, "windows")
    assert ran is None and not r.ok and r.stage == oe.ESXI_REFUSED_STAGE == pre["would"]["stage"]
    assert pre["verdict"] == ps.V_NO



def test_畫面選Windows_登不進去就停_不再讓執行器多登一次(monkeypatch):
    """2026-10-20 審查：探測失敗還放行，會讓同一組錯密碼多一筆登入失敗（網域帳號鎖定門檻）。"""
    pre = _run_precheck(monkeypatch, "login_fail", "windows")
    r, ran = _run_onboard(monkeypatch, "login_fail", "windows")
    assert ran is None and not r.ok and r.stage == "connect" == pre["would"]["stage"]
    assert pre["verdict"] == ps.V_LOGIN


def test_畫面選Windows_正常Windows_執行器收到的是原本那份Windows腳本(monkeypatch):
    _, ran = _run_onboard(monkeypatch, "windows", "windows")
    assert ran["platform"] == "windows" and ran["host"] == "10.99.7.1" and ran["username"] == "sysinfra"
    assert ran["script"] == oe.build_script("windows", PUB, CIP, "webit3sc", None)
