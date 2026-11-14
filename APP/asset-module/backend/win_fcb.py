"""Windows 組態檢核（依組態安全配置檢核表 v4.0，286 條），2026-10-21。

## 是什麼

公司的 Windows 組態檢核基準（286 條）。腳本本體是 `scripts/fcb_windowsV###.ps1`
（由 `scripts/build_fcb_windows.py` 從同事維護的上游檔轉出；換新版＝重轉、流水號 +1）。
取代 2026-10-20 的自製簡易檢核 W-V001（使用者 2026-10-21：「用公司的規範」）——W-V001 已停用，
舊結果不計入任何數字（見 config_audit.CURRENT_SQL／SUPERSEDED_LIKE）。

## 怎麼跑（收集全部的「組態檢核」那一步，或 8-2／資產頁的一鍵收集）

1. 經既有 WinRM／PSRP 連線，把**腳本內容**當資料送過去（PSRP add_script），在記憶體執行：
   不複製檔案、不帶 File 參數、不改執行原則、不送編碼指令、不 Invoke-Expression。
2. 先看收集帳號是不是本機 Administrators：**不是就整段不跑**，回 NOT_ADMIN 標記——
   上游腳本在非管理員下 secedit 會失敗、34 條變 Error，那份結果不完整，不收。
3. 是管理員：建一個隨機暫存目錄，把腳本本體包在 `& { ... } -OutputDir <暫存>` 裡跑，
   讀回它寫的 TXT（UTF-8）印在兩個標記之間，**finally 刪掉整個暫存目錄**。
   secedit 的匯出暫存檔由腳本本身在 finally 刪除。
4. Python 端把 TXT 交給 config_audit.import_report——跟 8-2 人工匯入**同一個解析器**，
   所以自動收集與手動匯入的結果不可能長得不一樣。

## SOC（使用者 2026-10-21 方案 B，已知會被看到）

腳本會在目標機起 `secedit.exe` 與 `auditpol.exe` 兩個子行程（唯讀查詢），且需要本機管理員。
這是全專案「Windows 收集不起子行程」守門的**唯一例外**，只限這支腳本、只限這兩個執行檔
（test_agent_scripts_security 守門）。6 系統設定有開關（預設開），SOC 有意見時可以關：
關掉後這一步顯示「略過」＋原因，不跑任何東西。

## 來源

上游 sha256 只記在 commit 訊息（使用者要求同事的版號不出現在系統裡）。
"""
from __future__ import annotations

import re
from pathlib import Path

#: 6 系統設定開關（app_settings）。預設開——使用者 2026-10-21 選「自動跑」。
SETTING_ENABLED = "win_config_audit_fcb"
DISABLED_REASON = "Windows 組態檢核已在 6 系統設定關閉（會在目標機執行 secedit／auditpol）"
NOT_ADMIN_TEXT = "需本機管理員才能做 Windows 組態檢核"

#: 結果標示（畫面、匯入紀錄都用這一句）
BASELINE_LABEL = "Windows 組態檢核（依組態安全配置檢核表 v4.0，286 條）"
#: 本專案這支腳本的版本戳前綴（腳本內 $ScriptVersion = 'WEBIT-WINCFG-V###'）
STAMP_PREFIX = "WEBIT-WINCFG-"

#: 這一步最多跑多久（秒）。286 條＋secedit／auditpol，正常一兩分鐘；卡住就中止、照實講逾時。
DEADLINE_S = 600

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
SCRIPT_GLOB = "fcb_windowsV*.ps1"

MARK_RUNAS = "__W3CFG_RUNAS__"
MARK_BEGIN = "__W3CFG_BEGIN__"
MARK_END = "__W3CFG_END__"
MARK_ERR = "__W3CFG_ERR__"
MARK_STALE = "__W3CFG_STALE__"


def enabled(conn) -> bool:
    from db import get_setting

    return (get_setting(conn, SETTING_ENABLED, "1") or "1").strip() != "0"


def latest_script() -> Path:
    """scripts/ 下流水號最大的 fcb_windowsV###.ps1（三位補零，文字排序＝數字排序）。"""
    cands = sorted(SCRIPTS_DIR.glob(SCRIPT_GLOB))
    if not cands:
        raise FileNotFoundError(f"找不到 {SCRIPT_GLOB}（scripts/ 下沒有 Windows 組態檢核腳本）")
    return cands[-1]


def script_body(text: str) -> str:
    """去掉檔頭說明（給人看的註解與 <# #> 區塊），只留要執行的部分。

    檔頭有「手動執行」的說明（含檔名），送到目標機沒有意義，也免得被誤認成落地腳本。"""
    t = text.lstrip("﻿").replace("\r\n", "\n")
    m = re.match(r"(?:[ \t]*#[^\n]*\n|[ \t]*\n)*[ \t]*<#.*?#>\n", t, flags=re.S)
    return t[m.end():] if m else t


#: 上次被中斷（逾時 ps.stop()／連線斷掉、遠端行程被收掉）時 finally 不一定跑得到，暫存目錄會留著。
#: 下一次跑之前先清掉「本系統命名、而且超過這麼久沒動」的舊目錄（遠大於 DEADLINE_S，不會誤刪正在跑的）。
STALE_MINUTES = 30

# 包裝層只用 PowerShell 2.0 也能「解析」的語法（例：不用 6>$null）：整段是一次解析，
# 舊版一遇到不認得的語法會整段不跑、連 RUNAS 都回不來，畫面只能講「沒有回應」。
# 腳本本體需要 5.1（用到 ::new()），版本不夠就照實講、不跑。
_WRAPPER = r"""$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$__w3id = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$__w3adm = (New-Object System.Security.Principal.WindowsPrincipal($__w3id)).IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
"__W3CFG_RUNAS__`t" + $__w3id.Name + "`t" + $(if ($__w3adm) { '1' } else { '0' })
if ($__w3adm -and $PSVersionTable.PSVersion.Major -lt 5) {
  "__W3CFG_ERR__`t" + "這台 PowerShell 版本 $($PSVersionTable.PSVersion)，Windows 組態檢核需要 5.1 以上，這次沒有執行"
} elseif ($__w3adm) {
  $__w3tmp = [System.IO.Path]::GetTempPath()
  # 清上次中斷留下的暫存目錄：只認本系統的命名（w3cfg_ + 32 位 hex），而且 __STALE_MIN__ 分鐘以上沒動
  $__w3old = @(Get-ChildItem -LiteralPath $__w3tmp -Filter 'w3cfg_*' -ErrorAction SilentlyContinue |
    Where-Object { $_.PSIsContainer -and $_.Name -match '^w3cfg_[0-9a-f]{32}$' -and $_.LastWriteTime -lt (Get-Date).AddMinutes(-__STALE_MIN__) })
  $__w3cleaned = 0
  foreach ($__w3o in $__w3old) {
    try { [System.IO.Directory]::Delete($__w3o.FullName, $true); $__w3cleaned++ }
    catch { "__W3CFG_ERR__`t" + "上次留下的暫存目錄刪不掉：$($__w3o.FullName)" }
  }
  if ($__w3cleaned -gt 0) { "__W3CFG_STALE__`t$__w3cleaned" }
  $__w3dir = Join-Path $__w3tmp ('w3cfg_' + [guid]::NewGuid().ToString('N'))
  $__w3envtmp = $env:TMP; $__w3envtemp = $env:TEMP
  try {
    $null = [System.IO.Directory]::CreateDirectory($__w3dir)
    # 腳本自己的暫存檔（secedit 匯出）也落在這個目錄：中途被中斷，下一次會一起清掉
    $env:TMP = $__w3dir; $env:TEMP = $__w3dir
    # Write-Host 只是給人看的進度字樣：遮掉（函式優先於 cmdlet），不用資訊串流重導（5.0 才有）
    function Write-Host { }
    $null = & {
__BODY__
    } -OutputDir $__w3dir
    $__w3txt = @(Get-ChildItem -LiteralPath $__w3dir -Filter 'FCB_*.txt')
    if ($__w3txt.Count -eq 1) {
      '__W3CFG_BEGIN__'
      [System.IO.File]::ReadAllText($__w3txt[0].FullName, [System.Text.Encoding]::UTF8)
      '__W3CFG_END__'
    } else {
      "__W3CFG_ERR__`t" + "腳本沒有產出結果檔（找到 $($__w3txt.Count) 份）"
    }
  } catch {
    "__W3CFG_ERR__`t" + (("" + $_.Exception.Message) -replace '[\t\r\n]+', ' ')
  } finally {
    $env:TMP = $__w3envtmp; $env:TEMP = $__w3envtemp
    try { [System.IO.Directory]::Delete($__w3dir, $true) } catch { "__W3CFG_ERR__`t" + "暫存目錄沒刪掉：$__w3dir" }
  }
}
""".replace("__STALE_MIN__", str(STALE_MINUTES))


def build_payload(script_text: str) -> str:
    """組出經 PSRP 送出去的整段 PowerShell（純文字；腳本本體原樣嵌在 script block 裡）。"""
    body = script_body(script_text)
    if "__BODY__" in body:
        raise ValueError("腳本本體含保留字 __BODY__")
    return _WRAPPER.replace("__BODY__", body.rstrip("\n"))


def parse_output(raw: str) -> dict:
    """PSRP 輸出 → {"state": "ok"|"not_admin"|"error", "runas", "is_admin", "txt", "error"}。

    * 沒有 RUNAS 標記：腳本根本沒跑起來（不是「沒有問題」）→ error
    * 不是管理員 → not_admin（整段沒跑）
    * 有 BEGIN／END → ok，txt 是兩標記之間的全文
    * 其餘 → error，帶腳本回的原因
    """
    text = (raw or "").replace("\r\n", "\n")
    out: dict = {"state": "error", "runas": None, "is_admin": None, "txt": None, "error": None}
    errs: list[str] = []
    for line in text.split("\n"):
        if line.startswith(MARK_RUNAS + "\t"):
            parts = line.split("\t") + ["", ""]
            out["runas"] = parts[1].strip() or None
            out["is_admin"] = parts[2].strip() == "1"
        elif line.startswith(MARK_ERR + "\t"):
            errs.append(line.split("\t", 1)[1].strip())
        elif line.startswith(MARK_STALE + "\t"):
            n = line.split("\t", 1)[1].strip()
            if n.isdigit() and int(n) > 0:
                errs.append(f"清掉上次中斷留下的暫存目錄 {n} 個")
    if out["is_admin"] is None:
        out["error"] = "；".join(errs) or "腳本沒有回應（WinRM 連上了但沒有任何輸出）"
        return out
    if not out["is_admin"]:
        out["state"] = "not_admin"
        return out
    b, e = text.find(MARK_BEGIN + "\n"), text.rfind("\n" + MARK_END)
    if b >= 0 and e > b:
        out["state"] = "ok"
        out["txt"] = text[b + len(MARK_BEGIN) + 1:e].lstrip("﻿") + "\n"
        if errs:
            out["error"] = "；".join(errs)      # 例：暫存目錄沒刪掉——結果照收，原因照講
        return out
    out["error"] = "；".join(errs) or "沒有收到結果（輸出不完整，可能中途被中斷）"
    return out


def not_admin_message(runas: str | None) -> str:
    who = f"收集帳號 {runas} " if runas else "收集帳號"
    return (f"{NOT_ADMIN_TEXT}——{who}不是這台的本機 Administrators 成員（或遠端 UAC 過濾了管理員權限），這次沒有執行，"
            "不算合規也不算不合規")
