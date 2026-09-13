"""一鍵批次自動納管：每台試密碼→佈金鑰→（可選）統一密碼成第一組。

守這功能存在的理由與底線（使用者 2026-09-06 定案）：
1. 密碼依序試、第一組通就停（不盲噴）——A 是現行密碼，大多一次就中
2. unify_password：登入用的不是第一組才改（用 A 進來的本來就是 A，不動）
3. 順序＝先佈金鑰再改密碼（改密碼出事還有金鑰進得去）
4. 回傳每台一列，**絕對不含任何密碼**——這是不落地的核心保證
5. 失敗分階段：connect（密碼都不對/連不上）vs onboard vs unify（已納管但改密碼失敗）
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import onboard_engine as eng  # noqa: E402
from onboard_engine import OnboardResult  # noqa: E402


def _probe_runner(correct: dict[str, str], platform: str = "Linux"):
    """correct: {ip: 正確密碼}。密碼對回 banner，不對回登入失敗字串。"""
    def runner(host, username, password):
        if correct.get(host) == password:
            return f"{platform}\n0\nHASSUDO"
        return "Permission denied, please try again."
    return runner


def _account_of(_platform):
    return "webit3scan"


def test_用第一組密碼登入成功_不需要改密碼():
    probe = _probe_runner({"10.99.0.1": "A密碼"})
    onboard_exec = lambda **kw: OnboardResult(True, "done", "納管完成")
    # passwd_executor 不該被呼叫——用 A 登入的本來就是 A
    def passwd_exec(**kw):
        raise AssertionError("用第一組密碼登入，不該去改密碼")

    out = eng.batch_auto_onboard(
        [{"ip": "10.99.0.1"}], username="root", passwords=["A密碼", "B密碼"],
        collector_ip="10.99.0.250", account_of=_account_of, unify_password=True,
        probe_runner=probe, onboard_executor=onboard_exec, passwd_executor=passwd_exec)
    assert out[0]["login_ok"] and out[0]["onboarded"]
    assert out[0]["password_index"] == 0
    assert out[0]["password_unified"] is False   # 本來就是 A，沒改


def test_用第二組密碼登入_會把密碼統一成第一組():
    probe = _probe_runner({"10.99.0.2": "B密碼"})
    onboard_exec = lambda **kw: OnboardResult(True, "done", "納管完成")
    passwd_calls = []
    def passwd_exec(**kw):
        passwd_calls.append(kw)
        return OnboardResult(True, "done", "密碼已更新")

    out = eng.batch_auto_onboard(
        [{"ip": "10.99.0.2"}], username="root", passwords=["A密碼", "B密碼"],
        collector_ip="10.99.0.250", account_of=_account_of, unify_password=True,
        probe_runner=probe, onboard_executor=onboard_exec, passwd_executor=passwd_exec)
    assert out[0]["password_index"] == 1
    assert out[0]["onboarded"] and out[0]["password_unified"] is True
    assert len(passwd_calls) == 1, "用 B 登入的要改成 A"


def test_不勾統一密碼_就算用B登入也不改():
    probe = _probe_runner({"10.99.0.3": "B密碼"})
    onboard_exec = lambda **kw: OnboardResult(True, "done", "納管完成")
    def passwd_exec(**kw):
        raise AssertionError("沒勾 unify，不該改密碼")

    out = eng.batch_auto_onboard(
        [{"ip": "10.99.0.3"}], username="root", passwords=["A密碼", "B密碼"],
        collector_ip="10.99.0.250", account_of=_account_of, unify_password=False,
        probe_runner=probe, onboard_executor=onboard_exec, passwd_executor=passwd_exec)
    assert out[0]["onboarded"] and out[0]["password_unified"] is False


def test_兩組密碼都不對_不納管_標成connect():
    probe = _probe_runner({})   # 沒有正確密碼
    onboard_exec = lambda **kw: (_ for _ in ()).throw(AssertionError("進不去不該納管"))
    out = eng.batch_auto_onboard(
        [{"ip": "10.99.0.4"}], username="root", passwords=["A密碼", "B密碼"],
        collector_ip="10.99.0.250", account_of=_account_of,
        probe_runner=probe, onboard_executor=onboard_exec)
    assert out[0]["login_ok"] is False
    assert out[0]["onboarded"] is False
    assert out[0]["fail_stage"] == "connect"


def test_先佈金鑰再改密碼_順序不能反():
    """納管失敗就不該去改密碼——不然把密碼改了卻沒佈金鑰，兩條路都可能沒了。"""
    probe = _probe_runner({"10.99.0.5": "B密碼"})
    onboard_exec = lambda **kw: OnboardResult(False, "execute", "腳本沒跑完")
    def passwd_exec(**kw):
        raise AssertionError("納管都失敗了，絕不能去改密碼")

    out = eng.batch_auto_onboard(
        [{"ip": "10.99.0.5"}], username="root", passwords=["A密碼", "B密碼"],
        collector_ip="10.99.0.250", account_of=_account_of, unify_password=True,
        probe_runner=probe, onboard_executor=onboard_exec, passwd_executor=passwd_exec)
    assert out[0]["onboarded"] is False
    assert out[0]["password_unified"] is False


def test_納管成功但改密碼失敗_不蓋掉已納管_標成unify():
    probe = _probe_runner({"10.99.0.6": "B密碼"})
    onboard_exec = lambda **kw: OnboardResult(True, "done", "納管完成")
    passwd_exec = lambda **kw: OnboardResult(False, "execute", "chpasswd 失敗")

    out = eng.batch_auto_onboard(
        [{"ip": "10.99.0.6"}], username="root", passwords=["A密碼", "B密碼"],
        collector_ip="10.99.0.250", account_of=_account_of, unify_password=True,
        probe_runner=probe, onboard_executor=onboard_exec, passwd_executor=passwd_exec)
    assert out[0]["onboarded"] is True, "納管已成功這個事實不能被改密碼失敗蓋掉"
    assert out[0]["password_unified"] is False
    assert out[0]["fail_stage"] == "unify"


def test_回傳結果絕不含任何密碼():
    """不落地的核心：整包結果 dump 成字串，不能出現任何一組密碼。"""
    probe = _probe_runner({"10.99.0.7": "超機密B_XYZ"})
    onboard_exec = lambda **kw: OnboardResult(True, "done", "納管完成")
    passwd_exec = lambda **kw: OnboardResult(True, "done", "密碼已更新")

    out = eng.batch_auto_onboard(
        [{"ip": "10.99.0.7"}], username="root", passwords=["超機密A_ABC", "超機密B_XYZ"],
        collector_ip="10.99.0.250", account_of=_account_of, unify_password=True,
        probe_runner=probe, onboard_executor=onboard_exec, passwd_executor=passwd_exec)
    dumped = json.dumps(out, ensure_ascii=False)
    assert "超機密A_ABC" not in dumped
    assert "超機密B_XYZ" not in dumped
    assert out[0]["password_index"] == 1   # 只講「第幾組」，不講密碼


def test_改密碼腳本裡不會echo密碼():
    """腳本文字本身含密碼（chpasswd 需要），但腳本自己不能把密碼印出來。"""
    script = eng.build_linux_passwd_script("我的新密碼123", "root")
    # 腳本裡有密碼是必然的（要餵給 chpasswd），但不能有 echo/print 把它顯示出來
    for line in script.splitlines():
        low = line.lower().strip()
        if low.startswith("echo") or low.startswith("printf"):
            assert "我的新密碼123" not in line, "腳本不可以 echo 出密碼"


# ===== OS 分流：RHEL 直接批次，非 RHEL 保守 =====

def test_RHEL系列判定():
    assert eng.is_rhel_family("Red Hat Enterprise Linux 9.3")
    assert eng.is_rhel_family("Rocky Linux 9.2")
    assert eng.is_rhel_family("CentOS Linux 7")
    assert eng.is_rhel_family("AlmaLinux 8")


def test_非RHEL一律當不熟_走保守():
    """判不出來或非 RHEL 都回 False——當「不熟」，由上層走『先試一台』。"""
    assert eng.is_rhel_family("Debian GNU/Linux 12") is False
    assert eng.is_rhel_family("Ubuntu 22.04") is False
    assert eng.is_rhel_family("SUSE Linux Enterprise 15") is False
    assert eng.is_rhel_family("AIX") is False
    assert eng.is_rhel_family(None) is False
    assert eng.is_rhel_family("") is False
