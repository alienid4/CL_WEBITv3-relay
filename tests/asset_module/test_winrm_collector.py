"""Windows 原生收集（WinRM/CIM）。

使用者定案：「Windows 應該靠 Windows 方式修，不該走 SSH」。
先前把 Unix 那套硬套 Windows 撞了一連串牆（沒 profile、BOM、sshd_config 指令順序、
New-LocalUser 不給群組），每一面都是典範錯置。

WinRM 的關鍵好處：**收集資料完全不需要在目標機建帳號、佈金鑰、改設定。**
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import winrm_collector as wc  # noqa: E402


def test_解析CIM輸出():
    raw = (
        "hostname=DESKTOP-ABC\n"
        "os=Microsoft Windows 10 Pro\n"
        "os_version=10.0.19045\n"
        "device_model=VMware Virtual Platform\n"
        "vendor=VMware, Inc.\n"
        "hw_serial=VMware-56 4d 04\n"
        "mac=00:0C:29:11:22:33\n"
        "is_vm=1\n"
    )
    f = wc.parse_facts(raw)
    assert f["hostname"] == "DESKTOP-ABC"
    assert f["os"] == "Microsoft Windows 10 Pro"
    assert f["hw_serial"] == "VMware-56 4d 04"
    assert f["is_vm"] == 1


def test_空值一律None_不留空字串():
    """空字串跟 None 混用會讓「這欄沒填」有兩種寫法，篩選與排序就分岔
    （這在 os/device_model 上實際踩過）。"""
    f = wc.parse_facts("hostname=PC1\nhw_serial=\ndevice_model=\n")
    assert f["hostname"] == "PC1"
    assert f["hw_serial"] is None and f["device_model"] is None


def test_只收認得的欄位_不把目標機雜訊帶進資產():
    f = wc.parse_facts("hostname=PC1\n隨便的東西=不該進來\nPS C:\> 提示字元雜訊\n")
    assert "隨便的東西" not in f
    assert set(f) <= {"hostname", "os", "os_version", "device_model", "vendor",
                      "hw_serial", "mac", "is_vm"}


def test_實體機判定():
    assert wc.parse_facts("is_vm=0\n")["is_vm"] == 0
    assert wc.parse_facts("is_vm=1\n")["is_vm"] == 1


def test_可注入runner_不碰真網路也不碰真密碼():
    """收集邏輯要能在家測完，真連線由 UI 觸發（密碼不該經過 AI）。"""
    seen = {}

    def fake(host, ps):
        seen["host"] = host
        seen["ps"] = ps
        return "hostname=FAKE-PC\nos=Microsoft Windows Server 2022\nis_vm=0\n"

    f = wc.collect("10.0.0.5", "svc", "FAKE-PW", runner=fake)
    assert f["hostname"] == "FAKE-PC" and f["is_vm"] == 0
    assert seen["host"] == "10.0.0.5"
    # 查詢必須用 CIM，不是叫目標機執行奇怪的東西
    assert "Get-CimInstance" in seen["ps"]
    assert "Win32_BIOS" in seen["ps"]      # 序號來源


def test_不需要在目標機建帳號或佈金鑰():
    """這是改走 WinRM 的核心價值：收集不必動目標機的帳號與設定。"""
    assert "New-LocalUser" not in wc.FACTS_PS
    assert "authorized_keys" not in wc.FACTS_PS
    assert "sshd" not in wc.FACTS_PS.lower()


def test_未開WinRM要給Windows原生的處理方式_且要含防火牆那步():
    """實測（.101）：光跑 Enable-PSRemoting 不夠——服務會起來、5985 也會監聽，
    但網卡若被判為「公用網路」，防火牆的公用設定檔規則是關的，外面照樣連不進來，
    而症狀只是「連線逾時」，完全看不出是防火牆設定檔問題。

    提示必須包含第二步，否則使用者會卡在一個看不出原因的地方。
    """
    hint = wc.enable_hint("10.0.0.9")
    assert "Enable-PSRemoting" in hint
    assert "New-NetFirewallRule" in hint, "漏了防火牆那步（實測最常卡的地方）"
    assert "5985" in hint
    assert "10.0.0.9" in hint, "要帶入實際的收集來源 IP，不要讓人自己填"
    # 建議窄的做法：只放行來源 IP，而不是把網路改成私人
    assert "公用" in hint
