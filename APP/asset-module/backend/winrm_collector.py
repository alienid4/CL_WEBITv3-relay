"""Windows 原生收集：WinRM／CIM。

## 為什麼不用 SSH（2026-07-19 使用者定案：「Windows 應該靠 Windows 方式，不該走 SSH」）

先前硬把 Unix 那套（OpenSSH ＋ 本機唯讀帳號 ＋ 公鑰）套到 Windows，撞了一連串牆，
而**每一面牆都是典範錯置**，不是 Windows 的問題：

  新帳號沒 profile → 找不到 .ssh/     ← Unix 家目錄慣例
  authorized_keys 有 BOM 就壞          ← Unix 純文字檔慣例
  sshd_config 同指令只認第一條          ← OpenSSH 設定語意
  New-LocalUser 不給群組                ← 拿 useradd 的心智模型硬套

更關鍵的是**規模**：公司幾千台 Windows 不可能一台台裝 OpenSSH、建本機帳號、佈金鑰；
在 AD 環境那根本不是標準做法。

## Windows 的正解

**收集資料完全不需要建帳號。** WinRM（WS-Man，Windows 內建的遠端管理）＋ CIM 查詢
就能拿到主機名／OS／序號／機型——那正是我們要的全部。網域環境用一組服務帳號即可
涵蓋所有機器，不必碰每一台。

  Linux    → SSH ＋ 金鑰 ＋ 唯讀帳號
  Windows  → WinRM/CIM ＋ 服務帳號（不建帳號、不佈金鑰、不碰 sshd）

## 目標機需要什麼

WinRM 沒開的機器要跑一次 `Enable-PSRemoting -Force`（Windows 原生指令，
網域環境通常已由 GPO 開好）。這比「裝 OpenSSH ＋ 建帳號 ＋ 佈金鑰 ＋ 改 sshd_config」
單純得多，而且是 Windows 管理員本來就熟悉的做法。

## 憑證

WinRM 需要帳密，SSH 用金鑰——但**兩者同一類**：221 上本來就存著收集用的私鑰，
存一組 Windows 服務帳號憑證是等價的事（業界 CMDB 工具都有憑證庫）。
差別是 Windows 這條需要加密的憑證庫，由 credential_store 負責。
"""
from __future__ import annotations

# CIM 查詢：一次拿齊我們要的欄位。用 PowerShell 輸出 key=value，好解析且不依賴 JSON。
# 刻意不使用 ConvertTo-Json——舊版 PowerShell 的 JSON 輸出格式不穩，key=value 最保險。
FACTS_PS = r"""
$ErrorActionPreference='SilentlyContinue'
$os  = Get-CimInstance Win32_OperatingSystem
$cs  = Get-CimInstance Win32_ComputerSystem
$bios= Get-CimInstance Win32_BIOS
$net = Get-CimInstance Win32_NetworkAdapterConfiguration | Where-Object { $_.IPEnabled -eq $true } | Select-Object -First 1
"hostname=" + $env:COMPUTERNAME
"os=" + $os.Caption
"os_version=" + $os.Version
"device_model=" + $cs.Model
"vendor=" + $cs.Manufacturer
"hw_serial=" + $bios.SerialNumber
"mac=" + $net.MACAddress
"is_vm=" + $(if ($cs.Model -match 'Virtual|VMware|KVM|Xen') { '1' } else { '0' })
"""


# M2 服務發現的 Windows 版。對應 Linux 的 `ss -tlnp`：
# Get-NetTCPConnection -State Listen 就是 Windows 原生的「誰在聽埠」。
#
# ⚠️ 不要改成 netstat -ano：那要再自己對 PID 查行程名，而且輸出格式在不同語系
# 的 Windows 上會變（中文版欄位標題是中文），解析很脆。CIM/Cmdlet 輸出跟語系無關。
#
# 輸出格式刻意跟 Linux 那條對齊成「bind:port process」，讓解析器共用同一支。
SERVICES_PS = r"""
$ErrorActionPreference='SilentlyContinue'
Get-NetTCPConnection -State Listen | ForEach-Object {
  $p = (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName
  "LISTEN 0 0 $($_.LocalAddress):$($_.LocalPort) *:* " + $(if ($p) { "users:((`"$p`"))" } else { "" })
}
"""


def collect_services(host: str, username: str, password: str, runner=None,
                     transport: str = "ntlm", timeout: int = 30) -> str:
    """對一台 Windows 收監聽清單，回傳「跟 ss 對齊」的原始文字。

    刻意回原始文字而不是解析後的結構：解析交給 service_collector.parse_listen，
    兩個平台共用同一支解析器，才不會 Linux 修了 bug、Windows 那條還留著。

    ⚠️ password 用完即丟，不進回傳值、不進 log（同 collect）。
    """
    if runner is not None:
        return runner(host, SERVICES_PS)

    import winrm  # 延後匯入：沒裝 pywinrm 時不影響其他功能

    session = winrm.Session(
        f"http://{host}:5985/wsman",
        auth=(username, password),
        transport=transport,
        read_timeout_sec=timeout + 10,
        operation_timeout_sec=timeout,
    )
    r = session.run_ps(SERVICES_PS)
    if r.status_code != 0:
        raise ConnectionError(
            (r.std_err or b"").decode("utf-8", "replace")[:300] or "WinRM 執行失敗"
        )
    return (r.std_out or b"").decode("utf-8", "replace")


def parse_facts(output: str) -> dict:
    """把 key=value 輸出解析成 dict。空值一律 None，不留空字串。

    為什麼分開成純函式：這是唯一有邏輯的部分，要能不碰網路直接測。
    """
    out: dict = {}
    for line in (output or "").splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip()
        out[k.strip()] = v or None
    if out.get("is_vm") is not None:
        out["is_vm"] = 1 if out["is_vm"] == "1" else 0
    # 只回我們認得的欄位，避免把目標機的雜訊帶進資產表
    allowed = ("hostname", "os", "os_version", "device_model", "vendor",
               "hw_serial", "mac", "is_vm")
    return {k: out.get(k) for k in allowed if k in out}


def collect(host: str, username: str, password: str, runner=None,
            transport: str = "ntlm", timeout: int = 30) -> dict:
    """對一台 Windows 收 facts。

    ⚠️ password 只在本函式與 runner 之間傳遞、用完即丟——不寫回傳值、不寫 log。
    runner 可注入，測試不碰真網路、不碰真密碼。

    transport 預設 ntlm：獨立機（非網域）也能用，且 NTLM 會加密內容，
    不像 basic 需要 AllowUnencrypted（那等於明文，內網也不該用）。
    """
    if runner is not None:
        return parse_facts(runner(host, FACTS_PS))

    import winrm  # 延後匯入：沒裝 pywinrm 時不影響其他功能

    session = winrm.Session(
        f"http://{host}:5985/wsman",
        auth=(username, password),
        transport=transport,
        read_timeout_sec=timeout + 10,
        operation_timeout_sec=timeout,
    )
    r = session.run_ps(FACTS_PS)
    if r.status_code != 0:
        raise ConnectionError(
            (r.std_err or b"").decode("utf-8", "replace")[:300] or "WinRM 執行失敗"
        )
    return parse_facts((r.std_out or b"").decode("utf-8", "replace"))


def enable_hint(collector_ip: str = "YOUR_SERVER_IP") -> str:
    """WinRM 連不上時給的解法。

    ⚠️ 實測（.101）發現：光跑 Enable-PSRemoting 不夠。服務會起來、5985 也會監聽，
    但如果那張網卡被 Windows 判定成「公用網路」，防火牆的公用設定檔規則是**關閉**的，
    外面照樣連不進來——而症狀只是「連線逾時」，完全看不出是防火牆設定檔的問題。

    這裡刻意建議「只放行收集來源 IP」而不是「把網路改成私人」：
    改成私人會一次放寬那張網卡的所有防火牆行為，暴露面大得多；
    只開一個埠給一台機器，其餘完全不動。
    """
    return (
        "這台的 WinRM 連不上。在該機器以系統管理員 PowerShell 執行：\n"
        "  1) Enable-PSRemoting -Force\n"
        f"  2) New-NetFirewallRule -DisplayName \"WinRM from webit3 collector\" "
        f"-Direction Inbound -Protocol TCP -LocalPort 5985 "
        f"-RemoteAddress {collector_ip} -Action Allow -Profile Any\n"
        "（第 2 步常被忽略：網卡若被判為「公用網路」，Enable-PSRemoting 開的規則不含公用設定檔，"
        "外面仍連不進來。只放行收集來源 IP 比把網路改成私人安全得多。"
        "網域環境通常已由 GPO 處理，不需逐台執行）"
    )


# 保留舊常數供既有呼叫點使用
ENABLE_HINT = enable_hint()
