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

# ── 傳輸層：PSRP（PowerShell Remoting Protocol），不是 WinRS cmd shell（2026-10-20）──
#
# 原本用 pywinrm `Session.run_ps()`：它開的是 WinRS **cmd shell**
# （resource URI …/windows/shell/cmd），授權看 WinRM 服務的 `RootSDDL`——Windows 預設只給
# Administrators 執行權。最小權限的收集帳號（本機「Remote Management Users」成員、非管理員）
# 因此在公司機收到 WS-Man fault code 5 `w:AccessDenied`（HTTP 500）。AD 端不同意對上百台改 RootSDDL。
#
# 改走 PSRP 連預設的 `Microsoft.PowerShell` session configuration：它有**自己的** SDDL，
# Server 2012 起預設就包含 BUILTIN\Remote Management Users（S-1-5-32-580）——零 AD／主機改動。
#
# 合規硬規則（金融業）：
#   · NTLM over HTTP 5985 ＋ **訊息加密強制開**（encryption="always"）；永不 Basic、永不 AllowUnencrypted。
#   · 腳本用 PSRP 的 add_script 以「資料」送過去，**不用** Invoke-Expression（pypsrp Client.execute_ps
#     內部會包一層 IEX，那是 SOC 常見告警樣式，所以這裡自己組 pipeline）、不 base64、不 -EncodedCommand、
#     不 ExecutionPolicy Bypass（貼上的 script 不受執行原則約束，本來就不需要）。
#   · 只唯讀查詢；不收任何秘密。
PSRP_CONFIGURATION = "Microsoft.PowerShell"
WINRM_HTTP_PORT = 5985


class WinRMAuthError(ConnectionError):
    """帳密被拒（HTTP 401）。下一步：查憑證，不是查防火牆。"""


class WinRMAccessDenied(ConnectionError):
    """認證過了但沒有權限（WS-Man fault 5 / AccessDenied）。下一步：把帳號加進 Remote Management Users。"""


class WinRMUnreachable(ConnectionError):
    """TCP 連不上或逾時。下一步：開 WinRM／防火牆（enable_hint）。"""


class WinRMScriptTimeout(RuntimeError):
    """連上了、腳本也送出了，但遠端一直沒跑完（例如 WMI／CIM 查詢卡住）。下一步：看那台的 WMI 服務。

    2026-10-20 公司機：9906 個位址的收集停在 6144 很久不動。pypsrp 的 invoke() 會把每一次
    OperationTimeout 吞掉再收一次（while state == RUNNING），**沒有總時限**——遠端腳本卡住
    就一直等下去，整輪收集跟著停在那一批。所以這裡自己加總時限，逾時就 stop 並丟這個錯。
    不繼承 ConnectionError／TimeoutError：那會被 _classify 講成「連不上」，下一步就指錯方向。
    """


#: 一支收集腳本最多跑多久（秒）。正常 Windows 主機 10～40 秒；超過就當遠端卡住。
SCRIPT_DEADLINE_S = 180


AUTH_FAILED_TEXT = ("WinRM 帳號或密碼被拒（NTLM 認證失敗，HTTP 401）——"
                    "請確認收集憑證的帳密、網域前綴（DOMAIN\\帳號）以及帳號沒有被鎖定")
ACCESS_DENIED_TEXT = ("WinRM 認證成功但沒有權限（Access denied）——"
                      "請把收集帳號加進該機本機群組「Remote Management Users」"
                      "（不需要系統管理員權限，也不需要修改 WinRM RootSDDL）")


def _is_access_denied(exc: Exception) -> bool:
    """WS-Man fault code 5（ERROR_ACCESS_DENIED）。看數字碼——訊息文字會隨系統語言變（中文版是「存取被拒」）。"""
    code = getattr(exc, "code", None)
    if type(exc).__name__ == "WSManFaultError":
        if code == 5 or str(code) == "5":
            return True
        txt = f"{getattr(exc, 'reason', '') or ''} {exc}"
        return "AccessDenied" in txt or "Access is denied" in txt
    if type(exc).__name__ == "WinRMTransportError":
        body = str(getattr(exc, "response_text", "") or "")
        return code == 500 and ("AccessDenied" in body or 'Code="5"' in body)
    return False


def _classify(host: str, exc: Exception) -> Exception:
    """把傳輸層的例外轉成三種「下一步不同」的錯誤；認不得的原樣回傳（不吞、不改寫成別的原因）。

    ⚠️ 訊息不可含密碼。pypsrp 的 AuthenticationError 會帶帳號名——我們不轉述它，改用固定文字。
    連不上的訊息保留 "timed out／refused／Max retries" 字樣：呼叫端（manage_state 等）靠這些字
    決定要不要附上 enable_hint()。
    """
    name = type(exc).__name__
    if name == "AuthenticationError":
        return WinRMAuthError(AUTH_FAILED_TEXT)
    if _is_access_denied(exc):
        return WinRMAccessDenied(ACCESS_DENIED_TEXT)
    net_errors: tuple = (ConnectionError, TimeoutError)   # 沒被 requests 包起來的 socket 錯誤
    try:
        import requests

        net_errors += (requests.ConnectionError, requests.Timeout)
    except ImportError:   # noqa: F401 - 沒有 requests 時只認內建的網路錯誤
        pass
    if isinstance(exc, net_errors):
        return WinRMUnreachable(
            f"WinRM 連不上（{host}:{WINRM_HTTP_PORT}，連線逾時或被拒 timed out / refused）：{exc}")
    return exc


def _invoke_with_deadline(ps, host: str, limit_s: float) -> None:
    """ps.invoke() 加總時限（見 WinRMScriptTimeout）。沒有 begin_invoke 的物件（舊版／測試替身）照舊 invoke()。"""
    import time

    if not hasattr(ps, "begin_invoke"):
        ps.invoke()
        return
    from pypsrp.powershell import PSInvocationState

    end = time.monotonic() + limit_s
    ps.begin_invoke()
    while ps.state == PSInvocationState.RUNNING:
        if time.monotonic() > end:
            try:
                ps.stop()
            except Exception:  # noqa: BLE001 - 停不掉也照樣回報逾時；連線隨 with 區塊關閉
                pass
            raise WinRMScriptTimeout(
                f"WinRM 腳本在 {host} 跑超過 {int(limit_s)} 秒沒有結束，已中止"
                "（遠端可能卡在 WMI／CIM 查詢；這一台略過，不影響其他台）")
        # 每次 poll 的伺服器端等待不超過剩下的時間，總時限才不會被一次 poll 拖過頭
        ps.poll_invoke(timeout=max(1, min(30, int(end - time.monotonic()))))
    ps.end_invoke()


#: 大腳本（例：Windows 組態檢核 286 條，約 200 KB）的單一 WS-Man 訊息上限。
#: pypsrp 會把 PSRP 訊息切片、分多次 Send 送出，每片大小依 max_envelope_size；但它看到伺服器是
#: PSRP 2.2（PowerShell 3+）就自動把上限從 150 KB 拉到 500 KB——伺服器的 MaxEnvelopeSizekb
#: 被調低（舊系統預設 150、或強化設定）時，一片 500 KB 會被整個拒絕。大腳本固定用 150 KB 以內的片，
#: 任何主機都收得下（只是多一兩次往返）。設成「不等於 153600」才不會被 pypsrp 自動拉高。
LARGE_SCRIPT_BYTES = 64 * 1024
SAFE_ENVELOPE_BYTES = 150 * 1024 - 512


def _wsman_extra(script: str) -> dict:
    """依腳本大小決定 WSMan 的額外參數（只有大腳本才改，小腳本維持原行為）。"""
    if len(script.encode("utf-8")) > LARGE_SCRIPT_BYTES:
        return {"max_envelope_size": SAFE_ENVELOPE_BYTES}
    return {}


#: 分段送（使用者 2026-10-21：「太大就分段」）：超過 CHUNK_CALL_LIMIT 的腳本不一次送，
#: 在同一個 PSRP 工作階段（同一個 runspace）分多次把文字以「參數（資料）」append 進一個變數，
#: 最後一次才組回來執行。每一次遠端呼叫（指令＋資料）都 ≤ CHUNK_CALL_LIMIT，
#: 所以就算對方 MaxEnvelopeSizekb 只有 150 也收得下。資料走參數、不拼進指令字串——不用處理跳脫，
#: 也不會被當成程式碼解析。
CHUNK_CALL_LIMIT = 64 * 1024
CHUNK_DATA_BYTES = 48 * 1024        # 序列化（XML 跳脫 &lt; 等）會變大，留空間給它
_CHUNK_INIT = "$global:__w3chunks = New-Object System.Text.StringBuilder"
_CHUNK_APPEND = "param([string]$c) [void]$global:__w3chunks.Append($c)"
# 組回來、清掉變數、在本工作階段執行（AMSI／腳本區塊記錄看得到完整內容，跟一次送完全一樣）
_CHUNK_RUN = ("$__w3s = $global:__w3chunks.ToString(); Remove-Variable -Name __w3chunks -Scope Global; "
              "& ([scriptblock]::Create($__w3s))")


def _split_utf8(text: str, limit: int) -> list[str]:
    """依 UTF-8 位元組數切段（不會把一個字切成兩半）。"""
    out, cur, n = [], [], 0
    for ch in text:
        b = len(ch.encode("utf-8"))
        if n + b > limit and cur:
            out.append("".join(cur))
            cur, n = [], 0
        cur.append(ch)
        n += b
    if cur:
        out.append("".join(cur))
    return out


def _remote_calls(script: str) -> list[tuple[str, dict]]:
    """這段腳本要分成哪幾次遠端呼叫：[(指令, 參數)]。小腳本＝一次；大腳本＝初始化＋N 段 append＋執行。
    最後一次的輸出才是腳本結果。"""
    if len(script.encode("utf-8")) <= CHUNK_CALL_LIMIT:
        return [(script, {})]
    calls: list[tuple[str, dict]] = [(_CHUNK_INIT, {})]
    calls += [(_CHUNK_APPEND, {"c": part}) for part in _split_utf8(script, CHUNK_DATA_BYTES)]
    calls.append((_CHUNK_RUN, {}))
    return calls


def _psrp_run(host: str, username: str, password: str, script: str, timeout: int,
              deadline_s: int | None = None) -> str:
    """用 PSRP 在預設 `Microsoft.PowerShell` 端點跑一段唯讀腳本，回輸出文字。

    回傳語意跟舊的 run_ps 對齊：成功回 stdout 文字；**完全沒輸出且有錯誤**才 raise ConnectionError。
    有輸出但錯誤串流有東西（腳本都設了 SilentlyContinue，理論上不會）→ 照樣回輸出：
    解析器本來就「少一格就標不知道」，不會把缺的格子當成 0。

    ⚠️ password 只在這個函式的區域變數裡，不進回傳值、不進 log、不進例外訊息。
    """
    from pypsrp.powershell import PowerShell, RunspacePool  # 延後匯入：沒裝不影響其他功能
    from pypsrp.wsman import WSMan

    try:
        with WSMan(
            host,
            port=WINRM_HTTP_PORT,
            ssl=False,
            auth="ntlm",
            encryption="always",          # NTLM 訊息加密；不可降成 never（那等於明文）
            username=username,
            password=password,
            no_proxy=True,                # 帶 NTLM 的請求不經環境變數裡的 proxy
            connection_timeout=min(timeout, 30),
            operation_timeout=timeout,
            read_timeout=timeout + 10,    # 必須大於 operation_timeout
            **_wsman_extra(script),       # 大腳本：每片 ≤150 KB（見 SAFE_ENVELOPE_BYTES）
        ) as wsman:
            # pypsrp 預設單一 runspace（min=max=1）：分段送時每次 append 的變數都在同一個工作階段裡。
            # 不可以調大 max_runspaces——分段的資料會散在不同 runspace（test_win_fcb 守門）
            with RunspacePool(wsman, configuration_name=PSRP_CONFIGURATION) as pool:
                calls = _remote_calls(script)
                for prep, params in calls[:-1]:          # 分段送：只送資料，不執行腳本本體
                    pre = PowerShell(pool)
                    pre.add_script(prep)
                    for k, v in params.items():
                        pre.add_parameter(k, v)
                    _invoke_with_deadline(pre, host, max(SCRIPT_DEADLINE_S, timeout))
                    if pre.had_errors:
                        errs = [str(e) for e in (getattr(pre.streams, "error", None) or [])]
                        raise ConnectionError("分段送出腳本失敗：" + ("；".join(errs))[:300])
                ps = PowerShell(pool)
                ps.add_script(calls[-1][0])
                ps.add_cmdlet("Out-String").add_parameter("Stream")
                # deadline_s：個別腳本要的總時限（例：Windows 組態檢核 286 條）；WSMan 的
                # operation_timeout 維持 timeout，不跟著拉長（伺服器端 MaxTimeoutms 有上限）
                _invoke_with_deadline(ps, host, max(SCRIPT_DEADLINE_S, timeout, deadline_s or 0))
                lines = [str(o) for o in (ps.output or []) if o is not None]
                had_errors = bool(ps.had_errors)
                errors = [str(e) for e in (getattr(ps.streams, "error", None) or [])]
    except Exception as exc:  # noqa: BLE001 - 分類後重新丟出，原因如實保留
        mapped = _classify(host, exc)
        if mapped is exc:
            raise
        raise mapped from exc

    out = "\n".join(lines)
    # had_errors 只在 pipeline FAILED 才為真；只寫進錯誤串流（非終止錯誤）時也要算
    if (had_errors or errors) and not out.strip():
        raise ConnectionError(("；".join(errors))[:300] or "WinRM 執行失敗")
    return out


def _run(host: str, username: str, password: str, script: str, timeout: int,
         runner=None, deadline_s: int | None = None) -> str:
    """四個收集函式共用的唯一入口。runner 可注入（測試不碰網路、不碰真密碼）。"""
    if runner is not None:
        return runner(host, script)
    if deadline_s is None:
        return _psrp_run(host, username, password, script, timeout)
    return _psrp_run(host, username, password, script, timeout, deadline_s=deadline_s)


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
$wp = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
"is_admin=" + $(if ($wp.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { '1' } else { '0' })
"run_as=" + $env:USERDOMAIN + '\' + $env:USERNAME
"""


# ── 非管理員收集的共用約定（2026-10-20，Windows 收集包 1）─────────────────────────────
#
# 收集帳號是「Remote Management Users」成員、**不是**系統管理員。有些查詢非管理員拿不到
# （典型：Win32_Service 走 Service Control Manager，網路登入的非管理員常被拒或拿到空清單）。
# 規則：**每一項各自 try／catch，拿不到就印一行 ERR**，不可以靜默變成「0 筆」
# （跟 test_no_silent_catch 同一個精神：沒收到 ≠ 沒有）。格式：
#
#     ERR<TAB>項目<TAB>DENIED|FAIL|EMPTY<TAB>原始訊息
#
# DENIED＝權限不足、FAIL＝其他錯誤、EMPTY＝沒報錯但一筆都沒有（非管理員查 SCM 的常見樣子）。
# Python 端 parse_errors()／explain_errors() 轉成「權限不足（需 X）」給人看。
#
# SOC 硬規則（2026-10-20 公司 SOC 事件後再次明訂；test_agent_scripts_security 守門）：
# 只用 PSRP 在記憶體裡跑 cmdlet／script block——不寫 .ps1、不帶 File 參數、不改執行原則、
# 不送編碼指令／base64、不用 Invoke-Expression、不下載、不 Add-Type、不碰反射與 AMSI。
_PS_ERR_FN = r"""
function W3Err($item, $e) {
  $m = ("" + $e.Exception.Message) -replace '[\t\r\n]+', ' '
  $k = 'FAIL'
  if (("" + $e.CategoryInfo.Category) -eq 'PermissionDenied' -or
      $e.Exception -is [System.UnauthorizedAccessException] -or
      $m -match 'Access is denied|Access denied|0x80070005|0x80041003|拒絕存取|存取被拒|存取遭拒') { $k = 'DENIED' }
  "ERR`t$item`t$k`t$m"
}
"""

#: 每一項「權限不足」時，要補的是什麼權限（給人看的「需 X」）
PERM_NEED = {
    "services": "本機 Administrators，或由 AD 授予收集帳號列舉服務（SCM）的權限",
    "services_registry": "讀取 HKLM\\SYSTEM\\CurrentControlSet\\Services 的權限",
    "ports": "讀取 root\\StandardCimv2 的 MSFT_NetTCPConnection（WMI 命名空間權限）",
    "connections": "讀取 TCP 連線表的權限",
    "software": "讀取 HKLM\\SOFTWARE\\…\\Uninstall 登錄機碼的權限",
    "users": "列舉本機帳號（SAM）的權限；Server 2016 起受 RestrictRemoteSAM 原則限制",
    "users_cim": "WMI root\\cimv2 的讀取權限",
    "members": "列舉本機群組成員的權限",
    "localgroups": "列舉本機群組的權限",
}

#: 項目的中文名
ITEM_LABEL = {
    "services": "Windows 服務清單", "services_registry": "Windows 服務清單（登錄檔）",
    "ports": "監聽埠", "connections": "已建立連線", "software": "軟體清單", "users": "本機帳號", "users_cim": "本機帳號（CIM）",
    "members": "群組成員", "localgroups": "本機群組清單",
}


def parse_errors(text: str) -> list[dict]:
    """抽出 ERR 行 → [{item, kind, message}]。"""
    out = []
    for raw in (text or "").splitlines():
        line = raw.rstrip("\r\n")
        if not line.startswith("ERR\t"):
            continue
        parts = line.split("\t", 3) + ["", "", ""]
        out.append({"item": parts[1].strip(), "kind": (parts[2].strip() or "FAIL").upper(),
                    "message": parts[3].strip()})
    return out


def explain_error(err: dict) -> str:
    """一筆 ERR → 給人看的一句話。權限不足一律寫成「權限不足（需 X）」。"""
    item = err.get("item") or ""
    base, _, sub = item.partition(":")
    label = ITEM_LABEL.get(base, base) + (f"「{sub}」" if sub else "")
    if err.get("kind") in ("DENIED", "EMPTY"):
        need = PERM_NEED.get(base, "系統管理員權限")
        extra = "（沒有報錯，但一筆都沒回——非管理員常見）" if err.get("kind") == "EMPTY" else ""
        return f"{label}：權限不足（需 {need}）{extra}"
    msg = (err.get("message") or "")[:160]
    return f"{label}：收集失敗（{msg or '沒有訊息'}）"


def explain_errors(errors: list[dict]) -> list[str]:
    return [explain_error(e) for e in errors or []]


# M2 服務發現的 Windows 版。對應 Linux 的 `ss -tanp | grep listen|estab`：
# Get-NetTCPConnection -State Listen,Established（2026-10-26 加 Established，給 observed_deps
# 推系統上下游；同一次執行、不多登入）。ESTAB 行格式跟 ss 對齊：`ESTAB 0 0 <本機> <對方>`，
# IPv6 加中括號。
#
# 退路（舊版 Windows 沒有 NetTCPIP 模組、或 CIM 被拒）：.NET IPGlobalProperties 的
# GetActiveTcpListeners／GetActiveTcpConnections——在 PSRP 行程內跑、**不起子行程**。
# ⚠️ 不用 netstat -ano：由 wsmprovhost 起 netstat.exe 是 EDR 偵測樣式（第五節守門會紅）。
# 退路拿不到 PID（監聽埠沒有行程名）；有用到就多印一行 PORTSRC=dotnet。
#
# 監聽埠的輸出格式刻意跟 Linux 那條對齊成「bind:port process」，讓解析器共用同一支。
# 2026-10-20 加：Win32_Service 服務清單（SVC 行：名稱、顯示名、狀態、啟動方式、執行帳號、PID）；
# 監聽埠的行程名後面補上「這個 PID 是哪幾個服務」（svchost 才看得出是 WinRM 還是 RPC）。
# 非管理員查 Win32_Service 被拒／回空 → ERR 一行，改讀登錄檔 Services 機碼（SVCREG 行，
# 有名稱與啟動方式、**沒有執行狀態**——狀態只有 SCM 講得出來，不猜）。
SERVICES_PS = r"""
$ErrorActionPreference='SilentlyContinue'
""" + _PS_ERR_FN + r"""
"SRC=winrm"
$svcs = @()
$svcErr = $false
try { $svcs = @(Get-CimInstance -ClassName Win32_Service -ErrorAction Stop) } catch { W3Err 'services' $_; $svcErr = $true }
# 執行檔的檔案公司名（判「非微軟」用）：只讀檔案版本資訊，不執行；同一個執行檔只查一次（svchost 共用）
$coOf = @{}
function W3Co($p) {
  $p = ("" + $p).Trim()
  $exe = ''
  if ($p.StartsWith('"')) { $i = $p.IndexOf('"', 1); if ($i -gt 1) { $exe = $p.Substring(1, $i - 1) } }
  if (-not $exe) { $m = [regex]::Match($p, '^(.+?\.exe)', 'IgnoreCase'); if ($m.Success) { $exe = $m.Groups[1].Value } else { $exe = ($p -split ' ')[0] } }
  $exe = [Environment]::ExpandEnvironmentVariables($exe)
  if ($exe -match '^\\SystemRoot\\') { $exe = $env:windir + $exe.Substring(11) }
  if (-not $exe) { return '' }
  if (-not $coOf.ContainsKey($exe)) {
    $co = ''
    $fi = Get-Item -LiteralPath $exe -ErrorAction SilentlyContinue
    if ($fi) { $co = "" + $fi.VersionInfo.CompanyName }
    $coOf[$exe] = ($co -replace '[\t\r\n]+', ' ')
  }
  $coOf[$exe]
}
function W3T1($s) { ("" + $s) -replace '[\t\r\n]+', ' ' }
$byPid = @{}
if ($svcs.Count -gt 0) {
  foreach ($s in $svcs) {
    $dn = ("" + $s.DisplayName) -replace '[\t\r\n]+', ' '
    "SVC`t$($s.Name)`t$dn`t$($s.State)`t$($s.StartMode)`t$($s.StartName)`t$($s.ProcessId)`t$(W3T1 $s.PathName)`t$(if ($s.DelayedAutoStart) { '1' } else { '0' })`t$(W3T1 $s.Description)`t$(W3Co $s.PathName)"
    $k = [int]$s.ProcessId
    if ($k -gt 0) {
      if (-not $byPid.ContainsKey($k)) { $byPid[$k] = @() }
      $byPid[$k] += $s.Name
    }
  }
} else {
  if (-not $svcErr) { "ERR`tservices`tEMPTY`tWin32_Service returned no rows" }
  try {
    Get-ChildItem -Path 'HKLM:\SYSTEM\CurrentControlSet\Services' -ErrorAction Stop | ForEach-Object {
      $p = Get-ItemProperty -LiteralPath $_.PSPath
      if ($p.ImagePath -and ([int]$p.Type -band 0x30)) {
        $mode = switch ([int]$p.Start) { 0 {'Boot'} 1 {'System'} 2 {'Auto'} 3 {'Manual'} 4 {'Disabled'} default {''} }
        $dn = "" + $p.DisplayName
        if ($dn.StartsWith('@')) { $dn = '' }
        $dn = $dn -replace '[\t\r\n]+', ' '
        $ds = "" + $p.Description
        if ($ds.StartsWith('@')) { $ds = '' }
        "SVCREG`t$($_.PSChildName)`t$dn`t$mode`t$($p.ObjectName)`t$(W3T1 $p.ImagePath)`t$(if ([int]$p.DelayedAutostart -eq 1) { '1' } else { '0' })`t$(W3T1 $ds)`t$(W3Co $p.ImagePath)"
      }
    }
  } catch { W3Err 'services_registry' $_ }
}
function W3Pn($k) {
  $k = [int]$k
  $p = (Get-Process -Id $k -ErrorAction SilentlyContinue).ProcessName
  if ($p -and $byPid.ContainsKey($k)) { $p = $p + ' (' + ($byPid[$k] -join ',') + ')' }
  if ($p) { "users:((`"$p`"))" } else { "" }
}
function W3Ep($a, $n) { $a = "" + $a; if ($a.Contains(':')) { '[' + $a + ']:' + $n } else { $a + ':' + $n } }
$tcp = $null
$tcpErr = $null
try { $tcp = @(Get-NetTCPConnection -State Listen,Established -ErrorAction Stop) } catch { $tcpErr = $_ }
# 已建立連線最多送回 ESTAB_CAP 條（忙碌的 SQL／IIS 主機可能上萬條）；超過的略過、最後印 TRUNC 行講略過幾條
# 計數放在 hashtable（參考型別）：函式裡改得到，不用 $script: 範圍修飾（PSRP 跑的 script block 不一定是 script 範圍）
$es = @{ n = 0; skip = 0; cap = ESTAB_CAP_HERE }
function W3Es($la, $lp, $ra, $rp) {
  if ($es.n -ge $es.cap) { $es.skip++; return }
  $es.n++
  "ESTAB 0 0 " + (W3Ep $la $lp) + " " + (W3Ep $ra $rp)
}
if ($tcpErr -eq $null) {
  foreach ($c in $tcp) {
    if (("" + $c.State) -eq 'Listen') {
      "LISTEN 0 0 $($c.LocalAddress):$($c.LocalPort) *:* " + (W3Pn $c.OwningProcess)
    } else {
      W3Es $c.LocalAddress $c.LocalPort $c.RemoteAddress $c.RemotePort
    }
  }
} else {
  # 退路：.NET 的 IPGlobalProperties（行程內，不起子行程；沒有 PID→沒有行程名）。
  # 監聽與連線分開算：監聽拿到了、連線那段才失敗 → 照用監聽，另外報 connections 的錯（不是 ports）
  $lsOut = 0
  $lsErr = $null
  $gp = $null
  try {
    $gp = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties()
    foreach ($l in @($gp.GetActiveTcpListeners())) {
      $lsOut++
      "LISTEN 0 0 " + (W3Ep $l.Address $l.Port) + " *:* "
    }
  } catch { $lsErr = $_ }
  if ($lsOut -gt 0) {
    "PORTSRC=dotnet"
    if ($lsErr) { W3Err 'ports' $lsErr }      # 監聽清單讀到一半：照用，但講出來
    try {
      foreach ($c in @($gp.GetActiveTcpConnections())) {
        if (("" + $c.State) -eq 'Established') {
          W3Es $c.LocalEndPoint.Address $c.LocalEndPoint.Port $c.RemoteEndPoint.Address $c.RemoteEndPoint.Port
        }
      }
    } catch { W3Err 'connections' $_ }
  } else {
    # 兩條都拿不到監聽：回報主路徑的錯（權限不足會被認成 DENIED）
    W3Err 'ports' $tcpErr
  }
}
if ($es.skip -gt 0) { "TRUNC`tconnections`t$($es.skip)" }
"""

# IIS（2026-10-20）：站台／綁定／應用程式／虛擬目錄／集區，附在服務收集同一次往返（解析見 iis_inventory）。
# 讀 applicationHost.config（唯讀）＋登錄的 SSL 綁定＋憑證存放區的指紋／到期日；不用 appcmd.exe、不讀密碼。
IIS_PS = r"""
"@@IIS"
$w3 = Test-Path -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Services\W3SVC'
"IIS=" + $(if ($w3) { '1' } else { '0' })
if ($w3) {
  function W3T2($s) { ("" + $s) -replace '[\t\r\n]+', ' ' }
  $cfg = Join-Path $env:windir 'System32\inetsrv\config\applicationHost.config'
  $x = $null
  try { $x = [xml](Get-Content -LiteralPath $cfg -Raw -ErrorAction Stop) } catch { W3Err 'iis' $_ }
  if ($x) {
    $mod = [bool](Get-Module -ListAvailable -Name WebAdministration)
    if ($mod) { Import-Module WebAdministration -ErrorAction SilentlyContinue }
    $ah = $x.configuration.'system.applicationHost'
    $pd = $ah.applicationPools.applicationPoolDefaults
    foreach ($p in @($ah.applicationPools.add)) {
      if (-not $p) { continue }
      $st = ''
      if ($mod) { try { $st = (Get-WebAppPoolState -Name $p.name -ErrorAction Stop).Value } catch { $st = '' } }
      $clr = if ($p.managedRuntimeVersion -ne $null) { $p.managedRuntimeVersion } elseif ($pd.managedRuntimeVersion -ne $null) { $pd.managedRuntimeVersion } else { 'v4.0' }
      if ($clr -eq '') { $clr = 'NoManagedCode' }
      $pipe = if ($p.managedPipelineMode) { $p.managedPipelineMode } elseif ($pd.managedPipelineMode) { $pd.managedPipelineMode } else { 'Integrated' }
      $idt = if ($p.processModel.identityType) { $p.processModel.identityType } elseif ($pd.processModel.identityType) { $pd.processModel.identityType } else { 'ApplicationPoolIdentity' }
      "POOL`t$(W3T2 $p.name)`t$st`t$clr`t$pipe`t$idt`t$(W3T2 $p.processModel.userName)"
    }
    $certOf = @{}
    foreach ($root in @('HKLM:\SYSTEM\CurrentControlSet\Services\HTTP\Parameters\SslBindingInfo',
                        'HKLM:\SYSTEM\CurrentControlSet\Services\HTTP\Parameters\SslSniBindingInfo')) {
      if (-not (Test-Path -LiteralPath $root)) { continue }
      try {
        Get-ChildItem -LiteralPath $root -ErrorAction Stop | ForEach-Object {
          $h = (Get-ItemProperty -LiteralPath $_.PSPath).SslCertHash
          if ($h) { $certOf[$_.PSChildName.ToLower()] = (($h | ForEach-Object { $_.ToString('X2') }) -join '') }
        }
      } catch { W3Err 'iis_cert' $_ }
    }
    foreach ($s in @($ah.sites.site)) {
      if (-not $s) { continue }
      $st = ''
      if ($mod) { try { $st = (Get-WebsiteState -Name $s.name -ErrorAction Stop).Value } catch { $st = '' } }
      $rootApp = @($s.application) | Where-Object { $_.path -eq '/' } | Select-Object -First 1
      $rootDir = if ($rootApp) { @($rootApp.virtualDirectory) | Where-Object { $_.path -eq '/' } | Select-Object -First 1 }
      "SITE`t$($s.id)`t$(W3T2 $s.name)`t$st`t$(W3T2 $rootDir.physicalPath)`t$($s.serverAutoStart)"
      foreach ($b in @($s.bindings.binding)) {
        if (-not $b) { continue }
        $thumb = ''; $exp = ''; $subj = ''
        if ($b.protocol -eq 'https') {
          $parts = ("" + $b.bindingInformation).Split(':')
          $ip = if ($parts[0] -eq '*' -or $parts[0] -eq '') { '0.0.0.0' } else { $parts[0] }
          $port = $parts[1]; $hostname = if ($parts.Count -gt 2) { $parts[2] } else { '' }
          foreach ($k in @("$($hostname):$port", "$($ip):$port", "[::]:$port")) {
            if ($k -and $certOf.ContainsKey($k.ToLower())) { $thumb = $certOf[$k.ToLower()]; break }
          }
          if ($thumb) {
            $c = Get-Item -LiteralPath ("Cert:\LocalMachine\My\" + $thumb) -ErrorAction SilentlyContinue
            if ($c) { $exp = $c.NotAfter.ToString('yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture); $subj = W3T2 $c.Subject }
          }
        }
        "BIND`t$($s.id)`t$($b.protocol)`t$(W3T2 $b.bindingInformation)`t$thumb`t$exp`t$subj"
      }
      foreach ($a in @($s.application)) {
        if (-not $a) { continue }
        $pool = if ($a.applicationPool) { $a.applicationPool } elseif ($s.applicationDefaults.applicationPool) { $s.applicationDefaults.applicationPool } elseif ($ah.sites.applicationDefaults.applicationPool) { $ah.sites.applicationDefaults.applicationPool } else { 'DefaultAppPool' }
        $ad = @($a.virtualDirectory) | Where-Object { $_.path -eq '/' } | Select-Object -First 1
        "APP`t$($s.id)`t$(W3T2 $a.path)`t$(W3T2 $pool)`t$(W3T2 $ad.physicalPath)"
        foreach ($v in @($a.virtualDirectory)) {
          if (-not $v -or $v.path -eq '/') { continue }
          "VDIR`t$($s.id)`t$(W3T2 $a.path)`t$(W3T2 $v.path)`t$(W3T2 $v.physicalPath)"
        }
      }
    }
  }
}
"""

#: 一次最多送回幾條已建立連線（腳本端上限；超過的略過並印 TRUNC 行）。
#: 2000 組的儲存上限在 observed_deps；這裡擋的是傳輸量（2 萬條約 1MB，30 秒逾時內傳得完）。
WIN_ESTAB_CAP = 20000
#: 連線收集總開關關掉時用的版本（observed_deps.COLLECT_KEY）：只問 Listen、連線上限 0（.NET 退路也一條都不送回）
SERVICES_PS_NO_CONN = (SERVICES_PS.replace("-State Listen,Established", "-State Listen")
                       .replace("ESTAB_CAP_HERE", "0")) + IIS_PS
SERVICES_PS = SERVICES_PS.replace("ESTAB_CAP_HERE", str(WIN_ESTAB_CAP)) + IIS_PS


# 帳號盤點的 Windows 版（2026-10-20）。**只收本機帳號＋特權本機群組的成員**，不收密碼雜湊、
# 不碰 SAM 檔。網域帳號只在「它是本機特權群組成員」時列出（Administrators 裡的
# DOMAIN\Domain Admins 才是稽核真正要看的），其餘網域帳號歸 AD 管、不在這裡。
#
# 群組用 SID 指定（S-1-5-32-544＝Administrators…），不用名稱：中文版 Windows 群組名是中文。
# Get-LocalUser 不在（極舊系統）或被拒 → 退回 Win32_UserAccount（沒有最後登入／改密碼時間）；
# Get-LocalGroupMember 遇到孤兒 SID 會整組報錯（已知問題）→ 退回 Win32_Group 關聯查詢。
ACCOUNT_GROUPS = (
    ("S-1-5-32-544", "Administrators"),
    ("S-1-5-32-555", "Remote Desktop Users"),
    ("S-1-5-32-580", "Remote Management Users"),
    ("S-1-5-32-551", "Backup Operators"),
    ("S-1-5-32-547", "Power Users"),
    ("S-1-5-32-578", "Hyper-V Administrators"),
)
ACCOUNTS_PS = r"""
$ErrorActionPreference='SilentlyContinue'
""" + _PS_ERR_FN + r"""
function W3T($d) { if ($d) { ([datetime]$d).ToString('yyyy-MM-dd HH:mm:ss') } else { '' } }
function W3S($s) { ("" + $s) -replace '[\t\r\n]+', ' ' }
"SRC=localaccounts"
"COMPUTER=$env:COMPUTERNAME"
$cs = Get-CimInstance -ClassName Win32_ComputerSystem
"DOMAIN=$($cs.Domain)"
"PARTOFDOMAIN=$(if ($cs.PartOfDomain) { '1' } else { '0' })"
$got = $false
if (Get-Command Get-LocalUser -ErrorAction SilentlyContinue) {
  try {
    $users = @(Get-LocalUser -ErrorAction Stop)
    foreach ($u in $users) {
      $rid = ("" + $u.SID).Split('-')[-1]
      "USER`t$($u.Name)`t$(if ($u.Enabled) { '1' } else { '0' })`t$(W3T $u.LastLogon)`t$(W3T $u.PasswordLastSet)`t$(W3T $u.PasswordExpires)`t$(W3T $u.AccountExpires)`t$(if ($u.PasswordRequired) { '1' } else { '0' })`t$rid`t$(W3S $u.Description)`tsamr`t$($u.SID)"
    }
    $got = $true
  } catch { W3Err 'users' $_ }
}
if (-not $got) {
  try {
    Get-CimInstance -ClassName Win32_UserAccount -Filter "LocalAccount=True" -ErrorAction Stop | ForEach-Object {
      $rid = ("" + $_.SID).Split('-')[-1]
      "USER`t$($_.Name)`t$(if ($_.Disabled) { '0' } else { '1' })`t`t`t`t`t$(if ($_.PasswordRequired) { '1' } else { '0' })`t$rid`t$(W3S $_.Description)`tcim`t$($_.SID)"
    }
  } catch { W3Err 'users_cim' $_ }
}
$groups = @(GROUPS_HERE)
foreach ($g in $groups) {
  try {
    Get-LocalGroupMember -SID $g[0] -ErrorAction Stop | ForEach-Object {
      "MEMBER`t$($g[0])`t$($g[1])`t$(W3S $_.Name)`t$($_.ObjectClass)`t$($_.PrincipalSource)`t$($_.SID)"
    }
  } catch {
    $eid = "" + $_.FullyQualifiedErrorId
    # 這台本來就沒有這個群組（例如沒裝 Hyper-V）：不是錯誤，照實不列
    if ($eid -notmatch 'GroupNotFound') {
      $first = $_
      try {
        $grp = Get-CimInstance -ClassName Win32_Group -Filter "LocalAccount=True AND SID='$($g[0])'" -ErrorAction Stop
        if ($grp) {
          Get-CimAssociatedInstance -InputObject $grp -ResultClassName Win32_Account -ErrorAction Stop | ForEach-Object {
            $oc = if ($_.CimClass.CimClassName -eq 'Win32_Group') { 'Group' } else { 'User' }
            "MEMBER`t$($g[0])`t$($g[1])`t$($_.Domain)\$($_.Name)`t$oc`t`t$($_.SID)"
          }
        } else { W3Err "members:$($g[1])" $first }
      } catch { W3Err "members:$($g[1])" $first }
    }
  }
}
# 其餘本機群組的成員（2026-10-21：Windows 帳號表「本機群組」欄要列全部隸屬，不只特權群組）。
# 一樣唯讀；某一組讀不到（孤兒 SID 等已知問題）只計數、不算整項失敗——特權群組才是稽核主體，
# 已在上面各自報錯；這裡少一組只會讓「本機群組」欄不完整，NOTE 行讓畫面講得出來。
$privSid = @{}
foreach ($g in $groups) { $privSid[$g[0]] = 1 }
if (Get-Command Get-LocalGroup -ErrorAction SilentlyContinue) {
  $lgFail = 0
  try {
    foreach ($lg in @(Get-LocalGroup -ErrorAction Stop)) {
      $gs = "" + $lg.SID
      if ($privSid.ContainsKey($gs)) { continue }
      try {
        Get-LocalGroupMember -SID $gs -ErrorAction Stop | ForEach-Object {
          "MEMBER`t$gs`t$(W3S $lg.Name)`t$(W3S $_.Name)`t$($_.ObjectClass)`t$($_.PrincipalSource)`t$($_.SID)`tother"
        }
      } catch { $lgFail++ }
    }
  } catch { W3Err 'localgroups' $_ }
  if ($lgFail -gt 0) { "NOTE`tlocalgroups`t$lgFail" }
}
""".replace("GROUPS_HERE", ",".join(f"@('{sid}','{name}')" for sid, name in ACCOUNT_GROUPS))


def collect_services(host: str, username: str, password: str, runner=None,
                     transport: str = "ntlm", timeout: int = 30, with_connections: bool = True) -> str:
    """對一台 Windows 收監聽清單，回傳「跟 ss 對齊」的原始文字。

    刻意回原始文字而不是解析後的結構：解析交給 service_collector.parse_listen，
    兩個平台共用同一支解析器，才不會 Linux 修了 bug、Windows 那條還留著。

    ⚠️ password 用完即丟，不進回傳值、不進 log（同 collect）。
    """
    return _run(host, username, password, SERVICES_PS if with_connections else SERVICES_PS_NO_CONN, timeout, runner)


def collect_software(host: str, username: str, password: str, runner=None,
                     transport: str = "ntlm", timeout: int = 60) -> str:
    """對一台 Windows 收「程式和功能」清單，回傳原始文字（解析交給 software_collector）。

    ⚠️ 未在真機驗證。password 用完即丟，不進回傳值、不進 log（同 collect）。
    timeout 比服務盤點長：登錄檔兩個 Uninstall 機碼加起來可能上千筆。
    """
    import software_collector

    return _run(host, username, password, software_collector.WINDOWS_PS, timeout, runner)


def collect_accounts(host: str, username: str, password: str, runner=None,
                     transport: str = "ntlm", timeout: int = 60) -> str:
    """對一台 Windows 收本機帳號＋特權本機群組成員，回原始文字（解析交給 account_collector）。

    非管理員可跑；拿不到的項目印 ERR 行（見 _PS_ERR_FN），不會靜默變成 0 個帳號。
    ⚠️ password 用完即丟，不進回傳值、不進 log（同 collect）。不收任何密碼雜湊。
    """
    return _run(host, username, password, ACCOUNTS_PS, timeout, runner)



# 值班健檢（8-3）的 Windows 版。對應 Linux 那支 _METRICS_CMD：
# **一次往返收齊**，用 @@MARKER 切段，解析交給 health_probe，兩個平台共用切段器。
#
# 三個刻意的取捨，每一個都是「寧可少一格，不要填一個假的」：
#
# 1. **全部走 CIM，不用效能計數器**（Get-Counter '\Memory\...'）。
#    計數器的路徑名稱**會隨系統語言變**——中文版 Windows 是「\記憶體\...」，
#    在英文版寫死的路徑會查不到，而錯誤長得像「這台沒有這個指標」。
#    CIM 的屬性名跟語言無關。
#
# 2. **時間同步讀登錄 W32Time\Parameters（Type／NtpServer）**，不跑 w32tm.exe。
#    2026-10-20 協調決策：收集不在目標機起任何子行程（SOC 敏感）。原本的 `w32tm /query /source`
#    會起 w32tm.exe；登錄值是「設定的來源」不是「當下實際同步到的來源」，但 NoSync＝只靠本機時鐘
#    這個判斷不變，輸出格式（rc=／src=）也不變，health_probe 照舊解析。值跟語言無關。
#
# 3. **沒有 iowait、沒有 load average、沒有「唯讀重掛」**。
#    Windows 沒有這三個概念的等價物。這裡就不收，由 health_probe 標成
#    「這個平台沒有這個指標」——**不可以填 0**，0 會被讀成「量到了，而且很好」。
HEALTH_PS = r"""
$ErrorActionPreference='SilentlyContinue'
$os = Get-CimInstance Win32_OperatingSystem
$cs = Get-CimInstance Win32_ComputerSystem

"@@OS"
"caption=" + $os.Caption
"version=" + $os.Version

"@@UP"
if ($os.LastBootUpTime) {
  "boot=" + $os.LastBootUpTime.ToString("yyyy-MM-dd HH:mm:ss")
  "now="  + (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
}

"@@CPU"
"ncpu=" + $cs.NumberOfLogicalProcessors
$cpu = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
if ($cpu -ne $null) { "usage=" + $cpu }

"@@MEM"
"total_kb=" + $os.TotalVisibleMemorySize
"free_kb="  + $os.FreePhysicalMemory
"commit_total_kb=" + $os.TotalVirtualMemorySize
"commit_free_kb="  + $os.FreeVirtualMemory

"@@DISK"
Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | ForEach-Object {
  "$($_.DeviceID)|$($_.Size)|$($_.FreeSpace)"
}

"@@SVC"
Get-CimInstance Win32_Service -Filter "StartMode='Auto' AND State<>'Running'" | ForEach-Object {
  "$($_.Name)|$($_.DisplayName)|$($_.State)"
}

"@@LISTEN"
Get-NetTCPConnection -State Listen | ForEach-Object {
  $p = (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName
  "LISTEN 0 0 $($_.LocalAddress):$($_.LocalPort) *:* " + $(if ($p) { "users:((`"$p`"))" } else { "" })
}

"@@TIME"
try {
  $tp = Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\Parameters' -ErrorAction Stop
  "rc=0"
  if ("" + $tp.Type -eq 'NoSync') { "src=Local CMOS Clock (Type=NoSync)" }
  elseif ("" + $tp.Type -eq 'NT5DS') { "src=domain hierarchy (Type=NT5DS)" }
  elseif ($tp.NtpServer) { "src=" + ("" + $tp.NtpServer).Trim() + " (Type=" + $tp.Type + ")" }
} catch { "rc=1" }

"@@END"
"""


def collect_health(host: str, username: str, password: str, runner=None,
                   transport: str = "ntlm", timeout: int = 30) -> str:
    """值班健檢：對一台 Windows 收齊指標，回**原始文字**（解析交給 health_probe）。

    回原始文字而不是結構，理由同 collect_services：解析只有一份，
    兩邊修同一支，不會這裡修好了那裡還留著。

    ⚠️ password 用完即丟，不進回傳值、不進 log（同 collect）。
    """
    return _run(host, username, password, HEALTH_PS, timeout, runner)


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
    if out.get("is_admin") is not None:
        out["is_admin"] = 1 if out["is_admin"] == "1" else 0
    # 只回我們認得的欄位，避免把目標機的雜訊帶進資產表
    # （is_admin／run_as 不進 hardware——manage_state.FACT_FIELDS 擋著；由 record_session 另存）
    allowed = ("hostname", "os", "os_version", "device_model", "vendor",
               "hw_serial", "mac", "is_vm", "is_admin", "run_as")
    return {k: out.get(k) for k in allowed if k in out}


def collect(host: str, username: str, password: str, runner=None,
            transport: str = "ntlm", timeout: int = 30) -> dict:
    """對一台 Windows 收 facts。

    ⚠️ password 只在本函式與 runner 之間傳遞、用完即丟——不寫回傳值、不寫 log。
    runner 可注入，測試不碰真網路、不碰真密碼。

    認證固定 NTLM ＋ 訊息加密（見檔頭 PSRP 段）：獨立機（非網域）也能用，
    不像 basic 需要 AllowUnencrypted（那等於明文，內網也不該用）。
    `transport` 參數只為相容舊呼叫端保留，**不會**被拿來切換成 basic。
    """
    return parse_facts(_run(host, username, password, FACTS_PS, timeout, runner))


#: 不是本機管理員時，資產頁那一句（決策 WINRM-ADMIN1）
NOT_ADMIN_NOTICE = ("webit3sc 不是本機管理員：服務／IIS／排程可能讀不完整"
                    "（見 AD 申請單：AI/AD申請單_WEBIT3收集帳號_2026-10-20.md）")


def record_session(conn, ip: str, asset_serial: str | None, facts: dict) -> None:
    """記下這次 WinRM session 是不是本機管理員（決策 WINRM-ADMIN1）。舊腳本沒回報就不動。"""
    if not isinstance(facts, dict) or facts.get("is_admin") is None:
        return
    from db import _now_local

    conn.execute(
        "INSERT INTO win_collect_session (ip, asset_serial, account, is_admin, checked_at) "
        "VALUES (?,?,?,?,?) ON CONFLICT(ip) DO UPDATE SET asset_serial=excluded.asset_serial, "
        "account=excluded.account, is_admin=excluded.is_admin, checked_at=excluded.checked_at",
        ((ip or "").strip(), asset_serial, facts.get("run_as"), int(facts["is_admin"]), _now_local()))
    conn.commit()


def session_for(conn, ip: str) -> dict | None:
    """這個 IP 最近一次 WinRM session 的身分（沒收過回 None）。"""
    try:
        row = conn.execute("SELECT * FROM win_collect_session WHERE ip = ?",
                           ((ip or "").strip(),)).fetchone()
    except Exception:  # noqa: BLE001 - 舊庫沒有這張表：當作不知道
        return None
    return dict(row) if row else None


def enable_hint(collector_ip: str | None = None) -> str:
    """WinRM 連不上時給的解法。

    collector_ip 不給就現場解析（畫面設定 → 環境變數 → 自動偵測），**沒有寫死的預設**。
    原本簽章預設是開發機位址，於是每台公司主機的錯誤訊息都叫人去放行一個不存在的位址——
    照著做會「規則加好了、WinRM 還是連不進來」，比不給提示更難查（2026-10-06 查出）。

    ⚠️ 這裡**刻意不 raise**。這支只在「已經出錯、正在組錯誤訊息」時被呼叫；
    在錯誤路徑上再丟例外會把原始故障蓋掉，變成查不到真因。
    解不出來就在訊息裡明寫「這一格沒填、要去哪裡填」——人看得懂，也不會照著貼錯值。

    ⚠️ 實測（示範網段內一台）發現：光跑 Enable-PSRemoting 不夠。服務會起來、5985 也會監聽，
    但如果那張網卡被 Windows 判定成「公用網路」，防火牆的公用設定檔規則是**關閉**的，
    外面照樣連不進來——而症狀只是「連線逾時」，完全看不出是防火牆設定檔的問題。

    這裡刻意建議「只放行收集來源 IP」而不是「把網路改成私人」：
    改成私人會一次放寬那張網卡的所有防火牆行為，暴露面大得多；
    只開一個埠給一台機器，其餘完全不動。
    """
    if not collector_ip:
        import onboard_engine

        collector_ip = (onboard_engine.resolve_collector_ip() or "").strip()
    if not collector_ip:
        collector_ip = "<這台收集器的位址：請在「設定 → 自動納管」填收集器位址後重試>"
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


# ENABLE_HINT 這個「模組載入時就算好」的常數已經移除（2026-10-06），呼叫點改成直接
# 呼叫 enable_hint()。兩個理由，第二個是真的 bug：
#   1. enable_hint() 現在會解析收集器位址（可能開 UDP socket 探預設路由）。
#      放在模組層等於**import 就有副作用**，backend 任何一支 import 到它都會跑，
#      解不出來時還會讓整個服務起不來。
#   2. 收集器位址是**畫面上可以改的設定**。在 import 時算好一次，之後使用者改了設定，
#      錯誤訊息永遠停在舊值——而且沒有任何跡象，只會讓人照著舊位址開防火牆開不通。
# 要字串就呼叫 enable_hint()，一次呼叫一次解析，永遠反映當下設定。
