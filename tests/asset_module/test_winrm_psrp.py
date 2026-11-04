"""Windows 收集改走 PSRP（2026-10-20）。

為什麼：pywinrm run_ps 開的是 WinRS cmd shell，授權看 WinRM RootSDDL（預設只給 Administrators）；
最小權限帳號（Remote Management Users 成員）在公司機收到 WS-Man fault 5 AccessDenied。
預設 PowerShell 端點 `Microsoft.PowerShell` 自己的 SDDL 就含 Remote Management Users，所以改走 PSRP。

這裡用假的 pypsrp 模組（塞進 sys.modules）測：不碰網路、不碰真密碼。
"""
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import winrm_collector as wc  # noqa: E402


# ── 假 pypsrp：類別名稱跟真的一樣（分類邏輯看類別名稱與屬性，不硬 import pypsrp）──
class AuthenticationError(Exception):
    pass


class WSManFaultError(Exception):
    @property
    def code(self):
        return self.args[0]

    @property
    def reason(self):
        return self.args[2]


class WinRMTransportError(Exception):
    @property
    def code(self):
        return self.args[1]

    @property
    def response_text(self):
        return self.args[2]


def _install_fake(monkeypatch, *, output=None, raise_on_open=None, had_errors=False, errors=()):
    seen = {"wsman_kwargs": None, "config": None, "scripts": [], "cmdlets": []}

    class FakeWSMan:
        def __init__(self, server, **kw):
            seen["server"] = server
            seen["wsman_kwargs"] = kw

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class FakePool:
        def __init__(self, wsman, configuration_name=None):
            seen["config"] = configuration_name

        def __enter__(self):
            if raise_on_open is not None:
                raise raise_on_open
            return self

        def __exit__(self, *a):
            return False

    class FakePS:
        def __init__(self, pool):
            self.output = list(output or [])
            self.had_errors = had_errors
            self.streams = types.SimpleNamespace(error=list(errors))

        def add_script(self, s):
            seen["scripts"].append(s)
            return self

        def add_cmdlet(self, c):
            seen["cmdlets"].append(c)
            return self

        def add_parameter(self, *a):
            return self

        def invoke(self):
            return self.output

    pkg = types.ModuleType("pypsrp")
    m_ws = types.ModuleType("pypsrp.wsman")
    m_ws.WSMan = FakeWSMan
    m_ps = types.ModuleType("pypsrp.powershell")
    m_ps.PowerShell = FakePS
    m_ps.RunspacePool = FakePool
    monkeypatch.setitem(sys.modules, "pypsrp", pkg)
    monkeypatch.setitem(sys.modules, "pypsrp.wsman", m_ws)
    monkeypatch.setitem(sys.modules, "pypsrp.powershell", m_ps)
    return seen


def test_成功_走預設PowerShell端點_NTLM加密_HTTP5985(monkeypatch):
    seen = _install_fake(monkeypatch, output=["hostname=WIN-A", "os=Windows Server 2019", "is_vm=1"])
    f = wc.collect("10.99.0.5", "CORP\\svc", "FAKE-PW")
    assert f == {"hostname": "WIN-A", "os": "Windows Server 2019", "is_vm": 1}
    kw = seen["wsman_kwargs"]
    assert seen["server"] == "10.99.0.5"
    assert seen["config"] == "Microsoft.PowerShell", "要連預設端點，那個 SDDL 才含 Remote Management Users"
    assert kw["port"] == 5985 and kw["ssl"] is False
    assert kw["auth"] == "ntlm", "不可以是 basic"
    assert kw["encryption"] == "always", "NTLM over HTTP 一定要訊息加密，不可降成 never"
    assert kw["read_timeout"] > kw["operation_timeout"]
    # 腳本以資料送出，不包 Invoke-Expression、不 base64
    assert seen["scripts"] == [wc.FACTS_PS]
    assert "Invoke-Expression" not in seen["cmdlets"]


def test_四個收集函式都走同一條PSRP(monkeypatch):
    import software_collector

    seen = _install_fake(monkeypatch, output=["x"])
    wc.collect_services("10.99.0.5", "u", "p")
    wc.collect_software("10.99.0.5", "u", "p")
    wc.collect_health("10.99.0.5", "u", "p")
    assert seen["scripts"] == [wc.SERVICES_PS, software_collector.WINDOWS_PS, wc.HEALTH_PS]


def test_帳密錯_回認證失敗_不帶出帳號密碼(monkeypatch):
    _install_fake(monkeypatch, raise_on_open=AuthenticationError(
        "Failed to authenticate the user CORP\\svc with ntlm"))
    with pytest.raises(wc.WinRMAuthError) as ei:
        wc.collect("10.99.0.5", "CORP\\svc", "SECRET-PW")
    msg = str(ei.value)
    assert "帳號或密碼被拒" in msg
    assert "SECRET-PW" not in msg and "svc" not in msg


def test_沒權限_fault5_提示加進RemoteManagementUsers_不再叫人改RootSDDL(monkeypatch):
    _install_fake(monkeypatch, raise_on_open=WSManFaultError(
        5, "host", "存取被拒。", None, None, None))   # 中文版訊息：靠數字碼判斷，不靠字面
    with pytest.raises(wc.WinRMAccessDenied) as ei:
        wc.collect_services("10.99.0.5", "u", "p")
    msg = str(ei.value)
    assert "Remote Management Users" in msg
    assert "不需要修改 WinRM RootSDDL" in msg
    assert isinstance(ei.value, ConnectionError), "呼叫端 except Exception 照舊接得住"


def test_沒權限_只有子碼AccessDenied也認得(monkeypatch):
    _install_fake(monkeypatch, raise_on_open=WSManFaultError(
        "w:AccessDenied", None, "Access is denied.", None, None, None))
    with pytest.raises(wc.WinRMAccessDenied):
        wc.collect_health("10.99.0.5", "u", "p")


def test_HTTP500解不出fault但內容有AccessDenied也算沒權限(monkeypatch):
    _install_fake(monkeypatch, raise_on_open=WinRMTransportError(
        "http", 500, '<f:WSManFault Code="5"><f:Message>w:AccessDenied</f:Message></f:WSManFault>'))
    with pytest.raises(wc.WinRMAccessDenied):
        wc.collect("10.99.0.5", "u", "p")


def test_連不上_保留呼叫端判斷用的字樣_才會附上開WinRM提示(monkeypatch):
    import requests

    _install_fake(monkeypatch, raise_on_open=requests.ConnectionError(
        "HTTPConnectionPool(host='10.99.0.5', port=5985): Max retries exceeded with url: /wsman"))
    with pytest.raises(wc.WinRMUnreachable) as ei:
        wc.collect("10.99.0.5", "u", "p")
    head = str(ei.value)[:200]       # 呼叫端只看前 200 字
    # manage_state／service_inventory／software_inventory 看 "timed out"；health_probe 看 "refused"
    assert "timed out" in head and "refused" in head


def test_逾時也算連不上(monkeypatch):
    import requests

    _install_fake(monkeypatch, raise_on_open=requests.ConnectTimeout("connect timeout=30"))
    with pytest.raises(wc.WinRMUnreachable):
        wc.collect_software("10.99.0.5", "u", "p")


def test_認不得的錯誤原樣丟出_不改寫成別的原因(monkeypatch):
    _install_fake(monkeypatch, raise_on_open=ValueError("weird"))
    with pytest.raises(ValueError, match="weird"):
        wc.collect("10.99.0.5", "u", "p")


def test_沒輸出又有錯誤才算執行失敗(monkeypatch):
    _install_fake(monkeypatch, output=[], had_errors=True, errors=["boom"])
    with pytest.raises(ConnectionError, match="boom"):
        wc.collect_services("10.99.0.5", "u", "p")


def test_有輸出時錯誤串流不擋結果(monkeypatch):
    _install_fake(monkeypatch, output=["@@OS", "caption=Win"], had_errors=True, errors=["minor"])
    assert "caption=Win" in wc.collect_health("10.99.0.5", "u", "p")


def test_runner注入仍然優先_不碰PSRP(monkeypatch):
    seen = _install_fake(monkeypatch, output=["should-not-be-used"])
    out = wc.collect_services("10.99.0.5", "u", "p", runner=lambda h, ps: "LISTEN x")
    assert out == "LISTEN x" and seen["wsman_kwargs"] is None


def test_真的pypsrp例外也分類得對():
    """防止 pypsrp 改類別名稱或屬性後，分類悄悄失效（沒裝 pypsrp 就跳過）。"""
    exc = pytest.importorskip("pypsrp.exceptions")
    assert isinstance(wc._classify("h", exc.AuthenticationError("x")), wc.WinRMAuthError)
    fault = exc.WSManFaultError(5, "m", "Access is denied.", None, None, None)
    assert isinstance(wc._classify("h", fault), wc.WinRMAccessDenied)
    other = exc.WSManFaultError(2150858770, "m", "unreachable", None, None, None)
    assert wc._classify("h", other) is other


def test_程式碼不准出現Basic_不加密_Bypass_EncodedCommand():
    src = (ROOT / "APP" / "asset-module" / "backend" / "winrm_collector.py").read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert 'auth="basic"' not in code.lower()
    assert 'encryption="never"' not in code
    assert "-ExecutionPolicy" not in code and "EncodedCommand" not in code
    assert "import winrm" not in code, "WinRS（run_ps）那條會撞 RootSDDL，不要再用"


def test_軟體清單讀兩個Uninstall機碼_跳過無名與系統元件_不用Win32_Product():
    import software_collector as sc

    ps = sc.WINDOWS_PS
    # 2026-10-20：兩個機碼逐一讀、各自 try（讀不到印「權限不足」，不靜默變 0 筆）
    assert r"HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'" in ps
    assert r"HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'" in ps
    assert "W3Err 'software'" in ps
    assert "$_.DisplayName -and -not $_.SystemComponent" in ps
    assert "Win32_Product" not in ps, "會觸發 MSI 一致性檢查（事件 1035），107 台就是 SOC 雜訊"


def test_PSRP回來的LF分行軟體清單解析得出來(monkeypatch):
    """PSRP 輸出是一行一個字串、用 \\n 接起來（不是 run_ps 的 CRLF）——解析器要照樣吃。"""
    import software_collector as sc

    _install_fake(monkeypatch, output=[
        "SRC=registry",
        "PKG\t7-Zip 23.01 (x64)\t23.01\tx64\t20240115\tIgor Pavlov",
        "PKG\tMicrosoft Visual C++ 2015 Redistributable\t14.0.24215\tx86\t\tMicrosoft Corporation",
    ])
    raw = wc.collect_software("10.99.0.5", "u", "p")
    src, pkgs = sc.parse_packages(raw)
    assert src == "registry"
    by = {p["name"]: p for p in pkgs}
    assert by["7-Zip 23.01 (x64)"]["version"] == "23.01"
    assert by["7-Zip 23.01 (x64)"]["arch"] == "x64"
    assert by["Microsoft Visual C++ 2015 Redistributable"]["arch"] == "x86"


def test_只有錯誤串流沒有輸出_也算執行失敗(monkeypatch):
    """pypsrp 的 had_errors 只在 pipeline FAILED 才為真；非終止錯誤只進錯誤串流。"""
    _install_fake(monkeypatch, output=[], had_errors=False, errors=["denied"])
    with pytest.raises(ConnectionError, match="denied"):
        wc.collect_health("10.99.0.5", "u", "p")


def test_遠端腳本卡住_有總時限_停掉並回報不是連不上(monkeypatch):
    """2026-10-20 公司機：收集停在 6144/9906 很久。pypsrp invoke() 會吞 OperationTimeout 一直等，
    遠端 WMI 卡住整輪就停。要有總時限：逾時 stop、丟 WinRMScriptTimeout（不是「連不上」）。"""
    _install_fake(monkeypatch, output=["x"])
    m_ps = sys.modules["pypsrp.powershell"]
    m_ps.PSInvocationState = types.SimpleNamespace(RUNNING="running", COMPLETED="completed")
    hit = {"stop": False, "polls": 0}

    class HungPS(m_ps.PowerShell):
        state = "init"

        def begin_invoke(self):
            self.state = "running"

        def poll_invoke(self, timeout=None):
            hit["polls"] += 1
            assert timeout is not None and timeout <= 30, "每次 poll 的等待要受剩餘時間限制"
            if hit["polls"] > 50:
                raise AssertionError("總時限沒生效（會無限等）")

        def stop(self):
            hit["stop"] = True

        def end_invoke(self):
            raise AssertionError("卡住的不可以走到 end_invoke")

    m_ps.PowerShell = HungPS
    monkeypatch.setattr(wc, "SCRIPT_DEADLINE_S", 0)
    import time as _t

    clock = [0.0]

    def fake_mono():
        clock[0] += 1.0
        return clock[0]

    monkeypatch.setattr(_t, "monotonic", fake_mono)
    with pytest.raises(wc.WinRMScriptTimeout) as e:
        wc._psrp_run("10.99.0.5", "u", "FAKE-PW", "x", timeout=3)
    assert hit["stop"], "逾時要送 stop 給遠端"
    assert "FAKE-PW" not in str(e.value)
    assert not isinstance(e.value, ConnectionError), "不可以講成連不上（下一步會指錯）"


def test_真的pypsrp有我們用到的介面():
    """替身測不到真介面：裝了 pypsrp 的機器（221／公司）上確認 begin/poll/end/stop/state 都在。"""
    ps_mod = pytest.importorskip("pypsrp.powershell")
    import inspect

    for name in ("begin_invoke", "poll_invoke", "end_invoke", "stop"):
        assert hasattr(ps_mod.PowerShell, name), name
    assert "timeout" in inspect.signature(ps_mod.PowerShell.poll_invoke).parameters
    assert hasattr(ps_mod.PSInvocationState, "RUNNING")


def test_正常跑完_走begin_poll_end(monkeypatch):
    _install_fake(monkeypatch, output=["hostname=WIN-A"])
    m_ps = sys.modules["pypsrp.powershell"]
    m_ps.PSInvocationState = types.SimpleNamespace(RUNNING="running", COMPLETED="completed")

    class OkPS(m_ps.PowerShell):
        state = "init"

        def begin_invoke(self):
            self.state = "running"

        def poll_invoke(self, timeout=None):
            self.state = "completed"

        def end_invoke(self):
            return self.output

    m_ps.PowerShell = OkPS
    assert "WIN-A" in wc._psrp_run("10.99.0.5", "u", "p", "x", timeout=30)
