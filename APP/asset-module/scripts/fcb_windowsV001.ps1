# Windows 組態檢核 fcb_windowsV001.ps1
# Windows 組態檢核（依組態安全配置檢核表 v4.0，286 條）
# 版本戳：WEBIT-WINCFG-V001（腳本內 $ScriptVersion）
# 手動執行：以本機系統管理員開啟 PowerShell，在檔案所在目錄執行本腳本；產出 FCB_<主機名>_<IP>.txt／.html，
# 把 .txt 拿到資訊戰情室 8-2 組態檢核匯入。自動收集（收集全部）由系統經 WinRM 在記憶體執行，不落地本檔。
# 唯讀：只讀取設定（secedit 匯出到暫存檔後立即刪除、auditpol 查詢、登錄、服務、防火牆），不修改任何設定。
<#
.SYNOPSIS
    Windows 組態檢核（依 Windows組態安全配置檢核表-v4.0(結案)，286 條）
.DESCRIPTION
    每一條都讀取本機「實際值」再與基準比對，結果分四種：
      Compliant       符合
      Non-Compliant   不符合（含「未設定」：基準要求的值根本不存在）
      Not-Applicable  不適用（例如服務未安裝）
      Error           查不到（權限不足、指令失敗、該條判定方式尚未核對）
    「查不到」絕不併入「符合」。

    輸出 TXT（7 欄、分號分隔，UTF-8）與 HTML；TXT 可直接匯入資訊戰情室 8-2 組態檢核。
    收尾有 Check ended at 與 Check summary（條數，用來判斷檔案是否截斷）。

    請以「系統管理員」身分執行；非管理員時 secedit 匯出會失敗，帳戶原則與權限指派共 34 條會是 Error。
.PARAMETER OutputDir
    TXT 與 HTML 的輸出目錄（預設為腳本所在目錄）
.PARAMETER AuditCompare
    進階稽核原則的比對方式：Exact＝必須與基準完全相同（預設）；AtLeast＝至少包含基準要求的成功／失敗
#>

[CmdletBinding()]
param(
    [string]$OutputDir = "",
    [ValidateSet('Exact', 'AtLeast')]
    [string]$AuditCompare = 'Exact'
)

$ScriptVersion = 'WEBIT-WINCFG-V001'
$BaselineName  = 'Windows組態安全配置檢核表-v4.0(結案)'

$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) { $ScriptDir = (Get-Location).Path }
if ([string]::IsNullOrWhiteSpace($OutputDir)) { $OutputDir = $ScriptDir }
if (-not (Test-Path $OutputDir)) { New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null }

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "  Windows 組態安全配置檢核工具 $ScriptVersion" -ForegroundColor Cyan
Write-Host "  檢核基準: $BaselineName" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# 1. 系統基本資訊
# ---------------------------------------------------------------------------
$ComputerName = $env:COMPUTERNAME
$Identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$IsAdmin = (New-Object System.Security.Principal.WindowsPrincipal($Identity)).IsInRole(
    [System.Security.Principal.WindowsBuiltInRole]::Administrator)
$RunAs = "$($Identity.Name) (Administrators=$(if ($IsAdmin) { '是' } else { '否' }))"

# IP：有預設閘道的介面優先，再依介面計量值（metric）由小到大；不寫死任何網段。
$IPAddress = ""
try {
    $routes = @(Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
        Sort-Object -Property @{ Expression = { $_.RouteMetric + $_.InterfaceMetric } })
    foreach ($rt in $routes) {
        $cand = Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $rt.InterfaceIndex -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' } | Select-Object -First 1
        if ($cand) { $IPAddress = $cand.IPAddress; break }
    }
    if (-not $IPAddress) {
        $cand = Get-CimInstance Win32_NetworkAdapterConfiguration -ErrorAction SilentlyContinue |
            Where-Object { $_.IPEnabled } | ForEach-Object { $_.IPAddress } |
            Where-Object { $_ -match '^\d{1,3}(\.\d{1,3}){3}$' -and $_ -notmatch '^(127\.|169\.254\.)' } |
            Select-Object -First 1
        if ($cand) { $IPAddress = $cand }
    }
} catch {}
if (-not $IPAddress) { $IPAddress = "0.0.0.0" }   # 查不到就寫佔位值，不假裝是 127.0.0.1

$OSInfo = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
$OSName = if ($OSInfo -and $OSInfo.Caption) { $OSInfo.Caption } else { "Microsoft Windows" }
$OSVersion = if ($OSInfo -and $OSInfo.Version) { $OSInfo.Version } else { "未知" }
$KernelVersion = ""
try {
    $cv = Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion" -ErrorAction SilentlyContinue
    if ($cv.DisplayVersion) { $KernelVersion = [string]$cv.DisplayVersion }
    elseif ($cv.ReleaseId) { $KernelVersion = [string]$cv.ReleaseId }
} catch {}
if (-not $KernelVersion -and $OSInfo) { $KernelVersion = [string]$OSInfo.BuildNumber }

$CheckTime = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
Write-Host "[+] 主機名稱: $ComputerName" -ForegroundColor Green
Write-Host "[+] IP 地址 : $IPAddress" -ForegroundColor Green
Write-Host "[+] 作業系統: $OSName ($OSVersion)" -ForegroundColor Green
Write-Host "[+] 執行身分: $RunAs" -ForegroundColor $(if ($IsAdmin) { 'Green' } else { 'Red' })
if (-not $IsAdmin) {
    Write-Host "[!] 未以系統管理員執行：帳戶原則與權限指派會無法讀取（結果為 Error，不會當成符合）" -ForegroundColor Red
}

# ---------------------------------------------------------------------------
# 2. 蒐集現行設定。每一個來源都記錄「有沒有讀成功」——讀失敗不能當成符合。
# ---------------------------------------------------------------------------
Write-Host "`n[*] 匯出本機安全性原則 (secedit)..." -ForegroundColor Yellow
$SecContent = @{}
$SecError = ""
$TempSecFile = [System.IO.Path]::GetTempFileName()
try {
    & secedit /export /cfg $TempSecFile /areas SECURITYPOLICY USER_RIGHTS /quiet | Out-Null
    $secRc = $LASTEXITCODE
    if (Test-Path $TempSecFile) {
        Get-Content $TempSecFile | ForEach-Object {
            if ($_ -match '^\s*([^=\[]+?)\s*=\s*(.*?)\s*$') { $SecContent[$matches[1].Trim()] = $matches[2].Trim() }
        }
    }
    if ($SecContent.Count -eq 0) { $SecError = "secedit 匯出失敗（離開碼 $secRc）" }
} catch { $SecError = "secedit 無法執行：$($_.Exception.Message)" }
finally { Remove-Item $TempSecFile -Force -ErrorAction SilentlyContinue }

Write-Host "[*] 讀取進階稽核原則 (auditpol，以 GUID 比對，與語系無關)..." -ForegroundColor Yellow
$AuditMap = @{}
$AuditError = ""
try {
    $raw = & auditpol /get /category:* /r 2>$null
    $apRc = $LASTEXITCODE
    if ($raw) {
        $rows = $raw | Where-Object { $_ -match '\S' } | Select-Object -Skip 1 |
            ConvertFrom-Csv -Header 'Machine', 'Target', 'Sub', 'Guid', 'Inc', 'Exc'
        foreach ($r in $rows) {
            if ($r.Guid -match '^\{[0-9A-Fa-f-]+\}$') { $AuditMap[$r.Guid.ToUpper()] = $r }
        }
    }
    if ($AuditMap.Count -eq 0) { $AuditError = "auditpol 查詢失敗（離開碼 $apRc）" }
} catch { $AuditError = "auditpol 無法執行：$($_.Exception.Message)" }

Write-Host "[*] 讀取防火牆生效設定 (ActiveStore)..." -ForegroundColor Yellow
$FwProfiles = @{}
$FwError = ""
try {
    Get-NetFirewallProfile -PolicyStore ActiveStore -ErrorAction Stop | ForEach-Object { $FwProfiles[[string]$_.Name] = $_ }
} catch { $FwError = "防火牆設定讀取失敗：$($_.Exception.Message)" }
$AllRules = @(
    [PSCustomObject]@{
        Index = 1
        ID = 'TWGCB-01-007-0001'
        Category = '帳戶原則\密碼原則'
        GPO = '電腦設定\Windows設定\安全性設定\帳戶原則\密碼原則\密碼最短使用期限'
        Name = '密碼最短使用期限'
        Standard = '1天'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 2
        ID = 'TWGCB-01-007-0002'
        Category = '帳戶原則\密碼原則'
        GPO = '電腦設定\Windows設定\安全性設定\帳戶原則\密碼原則\密碼最長使用期限'
        Name = '密碼最長使用期限'
        Standard = '90天'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 3
        ID = 'TWGCB-01-007-0003'
        Category = '帳戶原則\密碼原則'
        GPO = '電腦設定\Windows設定\安全性設定\帳戶原則\密碼原則\最小密碼長度'
        Name = '最小密碼長度'
        Standard = '7個字元'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = '依管理辦法,保留原設定值'
    }
    [PSCustomObject]@{
        Index = 4
        ID = 'TWGCB-01-007-0004'
        Category = '帳戶原則\密碼原則'
        GPO = '電腦設定\Windows設定\安全性設定\帳戶原則\密碼原則\密碼必須符合複雜性需求'
        Name = '密碼必須符合複雜性需求'
        Standard = '已啟用'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 5
        ID = 'TWGCB-01-007-0005'
        Category = '帳戶原則\密碼原則'
        GPO = '電腦設定\Windows設定\安全性設定\帳戶原則\密碼原則\強制執行密碼歷程記錄'
        Name = '強制執行密碼歷程記錄'
        Standard = '3記憶的密碼'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 6
        ID = 'TWGCB-01-007-0006'
        Category = '帳戶原則\密碼原則'
        GPO = '電腦設定\Windows設定\安全性設定\帳戶原則\密碼原則\使用可還原的加密來存放密碼'
        Name = '使用可還原的加密來存放密碼'
        Standard = '已停用'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 7
        ID = 'TWGCB-01-007-0007'
        Category = '帳戶原則\帳戶鎖定原則'
        GPO = '電腦設定\Windows設定\安全性設定\帳戶原則\帳戶鎖定原則\帳戶鎖定閾值'
        Name = '帳戶鎖定閾值'
        Standard = '3次不正確的登入嘗試'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = '依管理辦法,保留原設定值'
    }
    [PSCustomObject]@{
        Index = 8
        ID = 'TWGCB-01-007-0008'
        Category = '帳戶原則\帳戶鎖定原則'
        GPO = '電腦設定\Windows設定\安全性設定\帳戶原則\帳戶鎖定原則\重設帳戶鎖定計數器的時間間隔'
        Name = '重設帳戶鎖定計數器的時間間隔'
        Standard = '15分鐘'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 9
        ID = 'TWGCB-01-007-0009'
        Category = '帳戶原則\帳戶鎖定原則'
        GPO = '電腦設定\Windows設定\安全性設定\帳戶原則\帳戶鎖定原則\帳戶鎖定時間'
        Name = '帳戶鎖定期間'
        Standard = '15分鐘'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 10
        ID = 'TWGCB-01-007-0010'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\Microsoft網路用戶端：傳送未加密的密碼到其他廠商的SMB伺服器'
        Name = 'Microsoft網路用戶端：傳送未加密的密碼到其他廠商的SMB伺服器'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Services\LanmanWorkstation\Parameters\EnablePlainTextPassword'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 11
        ID = 'TWGCB-01-007-0011'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\Microsoft網路用戶端：數位簽章用戶端的通訊(如果伺服器同意)'
        Name = 'Microsoft網路用戶端：數位簽章用戶端的通訊(如果伺服器同意)'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Services\LanmanWorkstation\Parameters\EnableSecuritySignature'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 12
        ID = 'TWGCB-01-007-0012'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\Microsoft網路用戶端：數位簽章用戶端的通訊(自動)'
        Name = 'Microsoft網路用戶端：數位簽章用戶端的通訊(自動)'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Services\LanmanWorkstation\Parameters\RequireSecuritySignature'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 13
        ID = 'TWGCB-01-007-0013'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\Microsoft網路伺服器：伺服器SPN目標名稱驗證層級'
        Name = 'Microsoft網路伺服器：伺服器SPN目標名稱驗證層級'
        Standard = '關閉'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Services\LanManServer\Parameters\SMBServerNameHardeningLevel'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 14
        ID = 'TWGCB-01-007-0014'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\Microsoft網路伺服器：數位簽章伺服器的通訊(自動)'
        Name = 'Microsoft網路伺服器：數位簽章伺服器的通訊(自動)'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Services\LanManServer\Parameters\requiresecuritysignature'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 15
        ID = 'TWGCB-01-007-0015'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\Microsoft網路伺服器：數位簽章伺服器的通訊(如果用戶端同意)'
        Name = 'Microsoft網路伺服器：數位簽章伺服器的通訊(如果用戶端同意)'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Services\LanManServer\Parameters\enablesecuritysignature'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 16
        ID = 'TWGCB-01-007-0016'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\Microsoft網路伺服器：暫停工作階段前，要求的閒置時間'
        Name = 'Microsoft網路伺服器：暫停工作階段前，要求的閒置時間'
        Standard = '15分鐘'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Services\LanManServer\Parameters\autodisconnect'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 17
        ID = 'TWGCB-01-007-0017'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\Microsoft網路伺服器：當登入時數到期時，中斷用戶端連線'
        Name = 'Microsoft網路伺服器：當登入時數到期時，中斷用戶端連線'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Services\LanManServer\Parameters\enableforcedlogoff'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 18
        ID = 'TWGCB-01-007-0034'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\互動式登入：在密碼到期前提示使用者變更密碼'
        Name = '互動式登入：在密碼到期前提示使用者變更密碼'
        Standard = '7天'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Microsoft\Windows NT\CurrentVersion\Winlogon\passwordexpirywarning'
        Result = '▓符合  □不符合'
        Note = '依管理辦法,保留原設定值'
    }
    [PSCustomObject]@{
        Index = 19
        ID = 'TWGCB-01-007-0036'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\互動式登入：網域控制站無法使用時，要快取的先前登入次數'
        Name = '互動式登入：網域控制站無法使用時，要快取的先前登入次數'
        Standard = '4次'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Microsoft\Windows NT\CurrentVersion\Winlogon\cachedlogonscount'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 20
        ID = 'TWGCB-01-007-0037'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\互動式登入：智慧卡移除操作'
        Name = '互動式登入：智慧卡移除操作'
        Standard = '鎖定工作站'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Microsoft\Windows NT\CurrentVersion\Winlogon\scremoveoption'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 21
        ID = 'TWGCB-01-007-0038'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\互動式登入：須有智慧卡'
        Name = '互動式登入：須有智慧卡'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Microsoft\Windows\CurrentVersion\Policies\System\scforceoption'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 22
        ID = 'TWGCB-01-007-0039'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\互動式登入：不要求按CTRL+ALT+DEL鍵'
        Name = '互動式登入：不要求按CTRL+ALT+DEL鍵'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Microsoft\Windows\CurrentVersion\Policies\System\DisableCAD'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 23
        ID = 'TWGCB-01-007-0040'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\互動式登入：不要顯示上次登入的使用者名稱'
        Name = '互動式登入：不要顯示上次登入的使用者名稱'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Microsoft\Windows\CurrentVersion\Policies\System\DontDisplayLastUserName'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 24
        ID = 'TWGCB-01-007-0044'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\系統物件：加強內部系統物件的預設權限(例如：符號連結)'
        Name = '系統物件：加強內部系統物件的預設權限(例如：符號連結)'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Control\Session Manager\ProtectionMode'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 25
        ID = 'TWGCB-01-007-0045'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\系統物件：要求不區分大小寫用於非Windows子系統'
        Name = '系統物件：要求不區分大小寫用於非Windows子系統'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Control\Session Manager\Kernel\ObCaseInsensitive'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 26
        ID = 'TWGCB-01-007-0049'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\使用者帳戶控制：允許UIAccess應用程式不使用安全桌面來提升權限'
        Name = '使用者帳戶控制：允許UIAccess應用程式不使用安全桌面來提升權限'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Microsoft\Windows\CurrentVersion\Policies\System\EnableUIADesktopToggle'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 27
        ID = 'TWGCB-01-007-0051'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\使用者帳戶控制：僅針對已簽署與驗證過的可執行檔，提高其權限'
        Name = '使用者帳戶控制：僅針對已簽署與驗證過的可執行檔，提高其權限'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Microsoft\Windows\CurrentVersion\Policies\System\ValidateAdminCodeSignatures'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 28
        ID = 'TWGCB-01-007-0052'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\使用者帳戶控制：所有系統管理員均以管理員核准模式執行'
        Name = '使用者帳戶控制：所有系統管理員均以管理員核准模式執行'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Microsoft\Windows\CurrentVersion\Policies\System\EnableLUA'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 29
        ID = 'TWGCB-01-007-0053'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\使用者帳戶控制：偵測應用程式安裝，並提示提升權限'
        Name = '使用者帳戶控制：偵測應用程式安裝，並提示提升權限'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Microsoft\Windows\CurrentVersion\Policies\System\EnableInstallerDetection'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 30
        ID = 'TWGCB-01-007-0055'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\使用者帳戶控制：標準使用者之提升權限提示的行為'
        Name = '使用者帳戶控制：標準使用者之提升權限提示的行為'
        Standard = '提示輸入認證'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Microsoft\Windows\CurrentVersion\Policies\System\ConsentPromptBehaviorUser'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 31
        ID = 'TWGCB-01-007-0056'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\使用者帳戶控制：僅針對在安全位置安裝的UIAccess應用程式，提高其權限'
        Name = '使用者帳戶控制：僅針對在安全位置安裝的UIAccess應用程式，提高其權限'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Microsoft\Windows\CurrentVersion\Policies\System\EnableSecureUIAPaths'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 32
        ID = 'TWGCB-01-007-0057'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\使用者帳戶控制：將檔案及登錄寫入失敗虛擬化並儲存至每一使用者位置'
        Name = '使用者帳戶控制：將檔案及登錄寫入失敗虛擬化並儲存至每一使用者位置'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Microsoft\Windows\CurrentVersion\Policies\System\EnableVirtualization'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 33
        ID = 'TWGCB-01-007-0058'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\修復主控台：允許軟碟複製以及存取所有磁碟和所有資料夾'
        Name = '修復主控台：允許軟碟複製以及存取所有磁碟和所有資料夾'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Microsoft\Windows NT\CurrentVersion\Setup\RecoveryConsole\setcommand'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 34
        ID = 'TWGCB-01-007-0059'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\修復主控台：允許自動系統管理登入'
        Name = '修復主控台：允許自動系統管理登入'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Microsoft\Windows NT\CurrentVersion\Setup\RecoveryConsole\securitylevel'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 35
        ID = 'TWGCB-01-007-0060'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\帳戶：限制使用空白密碼的本機帳戶僅能登入到主控台'
        Name = '帳戶：限制使用空白密碼的本機帳戶僅能登入到主控台'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Control\Lsa\LimitBlankPasswordUse'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 36
        ID = 'TWGCB-01-007-0061'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\帳戶：重新命名來賓帳戶名稱'
        Name = '帳戶：重新命名來賓帳戶名稱'
        Standard = 'Renamed_Guest'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 37
        ID = 'TWGCB-01-007-0065'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\裝置：防止使用者安裝印表機驅動程式'
        Name = '裝置：防止使用者安裝印表機驅動程式'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Control\Print\Providers\LanMan Print Services\Servers\AddPrinterDrivers'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 38
        ID = 'TWGCB-01-007-0066'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\裝置：允許卸除而不須登入'
        Name = '裝置：允許卸除而不須登入'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Microsoft\Windows\CurrentVersion\Policies\System\undockwithoutlogon'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 39
        ID = 'TWGCB-01-007-0067'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\裝置：允許格式化以及退出卸除式媒體'
        Name = '裝置：允許格式化以及退出卸除式媒體'
        Standard = 'Administrators'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Microsoft\Windows NT\CurrentVersion\Winlogon\AllocateDASD'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 40
        ID = 'TWGCB-01-007-0068'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\裝置：CD-ROM存取只限於登入本機的使用者'
        Name = '裝置：CD-ROM存取只限於登入本機的使用者'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Microsoft\Windows NT\CurrentVersion\Winlogon\AllocateCDRoms'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 41
        ID = 'TWGCB-01-007-0069'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\裝置：軟碟機存取只限於登入本機的使用者'
        Name = '裝置：軟碟機存取只限於登入本機的使用者'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Microsoft\Windows NT\CurrentVersion\Winlogon\AllocateFloppies'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 42
        ID = 'TWGCB-01-007-0070'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\網域成員：停用電腦帳戶密碼變更'
        Name = '網域成員：停用電腦帳戶密碼變更'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Services\Netlogon\Parameters\disablepasswordchange'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 43
        ID = 'TWGCB-01-007-0071'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\網域成員：最長電腦帳戶密碼有效期'
        Name = '網域成員：最長電腦帳戶密碼有效期'
        Standard = '30天'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Services\Netlogon\Parameters\maximumpasswordage'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 44
        ID = 'TWGCB-01-007-0072'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\網域成員：安全通道資料加以數位加密或簽章(自動)'
        Name = '網域成員：安全通道資料加以數位加密或簽章(自動)'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Services\Netlogon\Parameters\requiresignorseal'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 45
        ID = 'TWGCB-01-007-0073'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\網域成員：安全通道資料加以數位簽章(可能的話)'
        Name = '網域成員：安全通道資料加以數位簽章(可能的話)'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Services\Netlogon\Parameters\signsecurechannel'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 46
        ID = 'TWGCB-01-007-0074'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\網域成員：要求增強式(Windows 2000或更新)工作階段金鑰'
        Name = '網域成員：要求增強式(Windows 2000或更新)工作階段金鑰'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Services\Netlogon\Parameters\requirestrongkey'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 47
        ID = 'TWGCB-01-007-0075'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\網域成員：安全通道資料加以數位加密(可能的話)'
        Name = '網域成員：安全通道資料加以數位加密(可能的話)'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Services\Netlogon\Parameters\sealsecurechannel'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 48
        ID = 'TWGCB-01-007-0076'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\網路存取：讓Everyone權限套用到匿名使用者'
        Name = '網路存取：讓Everyone權限套用到匿名使用者'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Control\Lsa\EveryoneIncludesAnonymous'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 49
        ID = 'TWGCB-01-007-0077'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\網路存取：共用和安全性模式用於本機帳戶'
        Name = '網路存取：共用和安全性模式用於本機帳戶'
        Standard = '傳統 - 本機使用者以自身身份驗證'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Control\Lsa\ForceGuest'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 50
        ID = 'TWGCB-01-007-0078'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\網路存取：不允許SAM帳戶的匿名列舉'
        Name = '網路存取：不允許SAM帳戶的匿名列舉'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Control\Lsa\RestrictAnonymousSAM'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 51
        ID = 'TWGCB-01-007-0079'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\網路存取：限制匿名存取具名管道和共用'
        Name = '網路存取：限制匿名存取具名管道和共用'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Services\LanManServer\Parameters\restrictnullsessaccess'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 52
        ID = 'TWGCB-01-007-0080'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\網路存取：可遠端存取的登錄路徑及子路徑'
        Name = '網路存取：可遠端存取的登錄路徑及子路徑'
        Standard = 'System\CurrentControlSet\Control\Print\Printers System\CurrentControlSet\Services\Eventlog Software\Microsoft\OLAP Server Software\Microsoft\Windows NT\CurrentVersion\Print Software\Microsoft\Windows NT\CurrentVersion\Windows System\CurrentControlSet\Control\ContentIndex System\CurrentControlSet\Control\Terminal Server System\CurrentControlSet\Control\Terminal Server\UserConfig System\CurrentControlSet\Control\Terminal Server\DefaultUserConfiguration Software\Microsoft\Windows NT\CurrentVersion\Perflib System\CurrentControlSet\Services\SysmonLog'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Control\SecurePipeServers\Winreg\AllowedPaths\Machine'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 53
        ID = 'TWGCB-01-007-0081'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\網路存取：允許匿名SID/名稱轉譯'
        Name = '網路存取：允許匿名SID/名稱轉譯'
        Standard = '已停用'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 54
        ID = 'TWGCB-01-007-0082'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\網路存取：可以匿名存取的共用'
        Name = '網路存取：可以匿名存取的共用'
        Standard = '無'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Services\LanManServer\Parameters\NullSessionShares'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 55
        ID = 'TWGCB-01-007-0083'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\網路存取：可以匿名存取的具名管道'
        Name = '網路存取：可以匿名存取的具名管道'
        Standard = '無'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Services\LanManServer\Parameters\NullSessionPipes'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 56
        ID = 'TWGCB-01-007-0084'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\網路存取：可遠端存取的登錄路徑'
        Name = '網路存取：可遠端存取的登錄路徑'
        Standard = 'System\CurrentControlSet\Control\ProductOptions System\CurrentControlSet\Control\Server Applications Software\Microsoft\Windows NT\CurrentVersion'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Control\SecurePipeServers\Winreg\AllowedExactPaths\Machine'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 57
        ID = 'TWGCB-01-007-0085'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\網路存取：不允許存放網路驗證的密碼與認證'
        Name = '網路存取：不允許存放網路驗證的密碼與認證'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Control\Lsa\DisableDomainCreds'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 58
        ID = 'TWGCB-01-007-0087'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\網路安全性：下次密碼變更時不儲存LAN Manager雜湊數值'
        Name = '網路安全性：下次密碼變更時不儲存LAN Manager雜湊數值'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Control\Lsa\NoLMHash'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 59
        ID = 'TWGCB-01-007-0088'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\網路安全性：允許Local System對NTLM使用電腦身分識別'
        Name = '網路安全性：允許Local System對NTLM使用電腦身分識別'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Control\Lsa\UseMachineId'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 60
        ID = 'TWGCB-01-007-0089'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\網路安全性：LAN Manager驗證等級'
        Name = '網路安全性：LAN Manager驗證等級'
        Standard = '只傳送 NTLMv2 回應\拒絕 LM 和 NTLM'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Control\Lsa\LmCompatibilityLevel'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 61
        ID = 'TWGCB-01-007-0090'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\網路安全性：允許對此電腦的PKU2U驗證要求使用線上身分識別'
        Name = '網路安全性：允許對此電腦的PKU2U驗證要求使用線上身分識別'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Control\Lsa\pku2u\AllowOnlineID'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 62
        ID = 'TWGCB-01-007-0091'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\網路安全性：NTLM SSP為主的(包含安全RPC)用戶端的最小工作階段安全性'
        Name = '網路安全性：NTLM SSP為主的(包含安全RPC)伺服器的最小工作階段安全性'
        Standard = '要求128位元加密'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Control\Lsa\MSV1_0\NTLMMinServerSec'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 63
        ID = 'TWGCB-01-007-0092'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\網路安全性：允許LocalSystem NULL工作階段回復'
        Name = '網路安全性：允許LocalSystem NULL工作階段回復'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Control\Lsa\MSV1_0\allownullsessionfallback'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 64
        ID = 'TWGCB-01-007-0091'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\網路安全性：NTLM SSP為主的(包含安全RPC)用戶端的最小工作階段安全性'
        Name = '網路安全性：NTLM SSP為主的(包含安全RPC)用戶端的最小工作階段安全性'
        Standard = '要求NTLMv2工作階段安全性 要求128位元加密'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Control\Lsa\MSV1_0\NTLMMinClientSec'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 65
        ID = 'TWGCB-01-007-0095'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\網路安全性：LDAP用戶端簽章要求'
        Name = '網路安全性：LDAP用戶端簽章要求'
        Standard = '交涉簽章'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Services\LDAP\LDAPClientIntegrity'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 66
        ID = 'TWGCB-01-007-0096'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\網路安全性：強制限制登入時數'
        Name = '網路安全性：強制限制登入時數'
        Standard = '已停用'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 67
        ID = 'TWGCB-01-007-0097'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\稽核：當無法記錄安全性稽核時，系統立即關機'
        Name = '稽核：當無法記錄安全性稽核時，系統立即關機'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Control\Lsa\crashonauditfail'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 68
        ID = 'TWGCB-01-007-0098'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\稽核：稽核通用系統物件的存取'
        Name = '稽核：稽核通用系統物件的存取'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Control\Lsa\AuditBaseObjects'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 69
        ID = 'TWGCB-01-007-0099'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\稽核：稽核備份與還原權限的使用'
        Name = '稽核：稽核備份與還原權限的使用'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Control\Lsa\fullprivilegeauditing'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 70
        ID = 'TWGCB-01-007-0100'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\稽核：強制執行稽核原則子類別設定(Windows Vista或更新版本)以覆寫稽核原則類別設定'
        Name = '稽核：強制執行稽核原則子類別設定(WindowsVista或更新版本)以覆寫稽核原則類別設定'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Control\Lsa\scenoapplylegacyauditpolicy'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 71
        ID = 'TWGCB-01-007-0101'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\關機：允許不登入就將系統關機'
        Name = '關機：允許不登入就將系統關機'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Microsoft\Windows\CurrentVersion\Policies\System\ShutdownWithoutLogon'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 72
        ID = 'TWGCB-01-007-0102'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\關機：清除虛擬記憶體分頁檔'
        Name = '關機：清除虛擬記憶體分頁檔'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\System\CurrentControlSet\Control\Session Manager\Memory Management\ClearPageFileAtShutdown'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 73
        ID = 'TWGCB-01-007-0105'
        Category = '本機原則\使用者權限指派'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\使用者權限指派\關閉系統'
        Name = '關閉系統'
        Standard = 'Administrators'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 74
        ID = 'TWGCB-01-007-0106'
        Category = '本機原則\使用者權限指派'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\使用者權限指派\調整處理程序的記憶體配額'
        Name = '調整處理程序的記憶體配額'
        Standard = 'Administrators, Local Service, Network Service'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 75
        ID = 'TWGCB-01-007-0108'
        Category = '本機原則\使用者權限指派'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\使用者權限指派\取得檔案或其他物件的擁有權'
        Name = '取得檔案或其他物件的擁有權'
        Standard = 'Administrators'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 76
        ID = 'TWGCB-01-007-0111'
        Category = '本機原則\使用者權限指派'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\使用者權限指派\載入及解除載入裝置驅動程式'
        Name = '載入及解除載入裝置驅動程式'
        Standard = 'Administrators'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 77
        ID = 'TWGCB-01-007-0112'
        Category = '本機原則\使用者權限指派'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\使用者權限指派\拒絕以服務方式登入'
        Name = '拒絕以服務方式登入'
        Standard = 'Guests'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 78
        ID = 'TWGCB-01-007-0114'
        Category = '本機原則\使用者權限指派'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\使用者權限指派\備份檔案及目錄'
        Name = '備份檔案及目錄'
        Standard = 'Administrators'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 79
        ID = 'TWGCB-01-007-0117'
        Category = '本機原則\使用者權限指派'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\使用者權限指派\變更系統時間'
        Name = '變更系統時間'
        Standard = 'LOCAL SERVICE, Administrators'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 80
        ID = 'TWGCB-01-007-0119'
        Category = '本機原則\使用者權限指派'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\使用者權限指派\修改韌體環境值'
        Name = '修改韌體環境值'
        Standard = 'Administrators'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 81
        ID = 'TWGCB-01-007-0120'
        Category = '本機原則\使用者權限指派'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\使用者權限指派\建立通用物件'
        Name = '建立通用物件'
        Standard = 'Administrators, SERVICE, LOCAL SERVICE, NETWORK SERVICE'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 82
        ID = 'TWGCB-01-007-0121'
        Category = '本機原則\使用者權限指派'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\使用者權限指派\設定檔系統效能'
        Name = '設定檔系統效能'
        Standard = 'Administrators(預設)'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 83
        ID = 'TWGCB-01-007-0122'
        Category = '本機原則\使用者權限指派'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\使用者權限指派\產生安全性稽核'
        Name = '產生安全性稽核'
        Standard = 'Local Service, Network Service'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 84
        ID = 'TWGCB-01-007-0125'
        Category = '本機原則\使用者權限指派'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\使用者權限指派\變更時區'
        Name = '變更時區'
        Standard = 'LOCAL SERVICE, Administrators'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 85
        ID = 'TWGCB-01-007-0126'
        Category = '本機原則\使用者權限指派'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\使用者權限指派\執行磁碟區維護工作'
        Name = '執行磁碟區維護工作'
        Standard = 'Administrators'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 86
        ID = 'TWGCB-01-007-0128'
        Category = '本機原則\使用者權限指派'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\使用者權限指派\強制從遠端系統進行關閉'
        Name = '強制從遠端系統進行關閉'
        Standard = 'Administrators'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 87
        ID = 'TWGCB-01-007-0129'
        Category = '本機原則\使用者權限指派'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\使用者權限指派\建立符號連結'
        Name = '建立符號連結'
        Standard = 'Administrators'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 88
        ID = 'TWGCB-01-007-0130'
        Category = '本機原則\使用者權限指派'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\使用者權限指派\從銜接站移除電腦'
        Name = '從銜接站移除電腦'
        Standard = 'Administrators'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 89
        ID = 'TWGCB-01-007-0132'
        Category = '本機原則\使用者權限指派'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\使用者權限指派\建立分頁檔'
        Name = '建立分頁檔'
        Standard = 'Administrators'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 90
        ID = 'TWGCB-01-007-0133'
        Category = '本機原則\使用者權限指派'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\使用者權限指派\增加排程優先順序'
        Name = '增加排程優先順序'
        Standard = 'Administrators'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 91
        ID = 'TWGCB-01-007-0134'
        Category = '本機原則\使用者權限指派'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\使用者權限指派\管理稽核及安全性記錄檔'
        Name = '管理稽核及安全性記錄檔'
        Standard = 'Administrators'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 92
        ID = 'TWGCB-01-007-0140'
        Category = '本機原則\使用者權限指派'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\使用者權限指派\設定檔單一處理程序'
        Name = '設定檔單一處理程序'
        Standard = 'Administrators(預設)'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 93
        ID = 'TWGCB-01-007-0144'
        Category = '本機原則\使用者權限指派'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\使用者權限指派\允許本機登入'
        Name = '允許本機登入'
        Standard = 'Administrators、Backup Operators、Users'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 94
        ID = 'TWGCB-01-007-0145'
        Category = '系統管理範本\Windows元件'
        GPO = '電腦設定\系統管理範本\Windows元件\Windows Installer\永遠以較高的特殊權限安裝'
        Name = '永遠以較高的特殊權限安裝'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Policies\Microsoft\Windows\Installer\AlwaysInstallElevated'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 95
        ID = 'TWGCB-01-007-0149'
        Category = '系統管理範本\Windows元件'
        GPO = '電腦設定\系統管理範本\Windows元件\自動播放原則\不允許非磁碟區裝置的自動播放'
        Name = '不允許非磁碟區裝置的自動播放'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\Explorer\NoAutoplayfornonVolume'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 96
        ID = 'TWGCB-01-007-0151'
        Category = '系統管理範本\Windows元件'
        GPO = '電腦設定\系統管理範本\Windows元件\遠端桌面服務\遠端桌面工作階段主機\安全性\設定用戶端連線加密層級'
        Name = '設定用戶端連線加密層級'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Policies\Microsoft\Windows NT\Terminal Services\MinEncryptionLevel'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 97
        ID = 'TWGCB-01-007-0194 (FCB-01-001-0095)'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\DS存取\稽核詳細目錄服務複寫'
        Name = '稽核詳細目錄服務複寫'
        Standard = '沒有稽核'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 98
        ID = 'TWGCB-01-007-0195 (FCB-01-001-0094)'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\DS存取\稽核目錄服務複寫'
        Name = '稽核目錄服務複寫'
        Standard = '沒有稽核'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 99
        ID = 'TWGCB-01-007-0196'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\系統\稽核系統完整性'
        Name = '稽核系統完整性'
        Standard = '成功與失敗'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 100
        ID = 'TWGCB-01-007-0197'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\系統\稽核IPsec驅動程式'
        Name = '稽核IPsec驅動程式'
        Standard = '成功與失敗'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 101
        ID = 'TWGCB-01-007-0198'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\系統\稽核安全性系統延伸'
        Name = '稽核安全性系統延伸'
        Standard = '成功與失敗'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 102
        ID = 'TWGCB-01-007-0199'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\系統\稽核其他系統事件'
        Name = '稽核其他系統事件'
        Standard = '成功與失敗'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 103
        ID = 'TWGCB-01-007-0200'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\系統\稽核安全性狀態變更'
        Name = '稽核安全性狀態變更'
        Standard = '成功與失敗'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 104
        ID = 'TWGCB-01-007-0201'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\帳戶物件存取\稽核其他物件存取事件'
        Name = '稽核其他物件存取事件'
        Standard = '成功與失敗'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 105
        ID = 'TWGCB-01-007-0202'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\帳戶物件存取\稽核控制代碼操作'
        Name = '稽核控制代碼操作'
        Standard = '沒有稽核'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 106
        ID = 'TWGCB-01-007-0204'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\帳戶物件存取\稽核篩選平台連線'
        Name = '稽核篩選平台連線'
        Standard = '沒有稽核'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 107
        ID = 'TWGCB-01-007-0205'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\帳戶物件存取\稽核憑證服務'
        Name = '稽核憑證服務'
        Standard = '沒有稽核'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 108
        ID = 'TWGCB-01-007-0207'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\帳戶物件存取\稽核SAM'
        Name = '稽核SAM'
        Standard = '沒有稽核'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 109
        ID = 'TWGCB-01-007-0208'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\帳戶物件存取\稽核篩選平台封包丟棄'
        Name = '稽核篩選平台封包丟棄'
        Standard = '沒有稽核'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 110
        ID = 'TWGCB-01-007-0209'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\帳戶物件存取\稽核產生的應用程式'
        Name = '稽核產生的應用程式'
        Standard = '沒有稽核'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 111
        ID = 'TWGCB-01-007-0210'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\帳戶物件存取\稽核登錄'
        Name = '稽核登錄'
        Standard = '成功與失敗'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 112
        ID = 'TWGCB-01-007-0212'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\帳戶物件存取\稽核核心物件'
        Name = '稽核核心物件'
        Standard = '沒有稽核'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 113
        ID = 'TWGCB-01-007-0213'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\原則變更\稽核篩選平台原則變更'
        Name = '稽核篩選平台原則變更'
        Standard = '沒有稽核'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 114
        ID = 'TWGCB-01-007-0214'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\原則變更\稽核驗證原則變更'
        Name = '稽核驗證原則變更'
        Standard = '成功'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 115
        ID = 'TWGCB-01-007-0215'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\原則變更\稽核授權原則變更'
        Name = '稽核授權原則變更'
        Standard = '成功'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 116
        ID = 'TWGCB-01-007-0216'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\原則變更\稽核「稽核原則變更」'
        Name = '稽核「稽核原則變更」'
        Standard = '成功與失敗'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 117
        ID = 'TWGCB-01-007-0217'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\原則變更\稽核MPSSVC規則層級原則變更'
        Name = '稽核MPSSVC規則層級原則變更'
        Standard = '成功與失敗'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 118
        ID = 'TWGCB-01-007-0218'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\原則變更\稽核其他原則變更事件'
        Name = '稽核其他原則變更事件'
        Standard = '失敗'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 119
        ID = 'TWGCB-01-007-0219'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\特殊權限使用\稽核機密特殊權限使用'
        Name = '稽核機密特殊權限使用'
        Standard = '成功與失敗'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 120
        ID = 'TWGCB-01-007-0220'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\特殊權限使用\稽核非機密特殊權限使用'
        Name = '稽核非機密特殊權限使用'
        Standard = '沒有稽核'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 121
        ID = 'TWGCB-01-007-0221'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\特殊權限使用\稽核其他特殊權限使用事件'
        Name = '稽核其他特殊權限使用事件'
        Standard = '沒有稽核'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 122
        ID = 'TWGCB-01-007-0222'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\帳戶管理'
        Name = '稽核Kerberos驗證服務'
        Standard = '成功與失敗'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 123
        ID = 'TWGCB-01-007-0223'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\帳戶登入\稽核認證驗證'
        Name = '稽核認證驗證'
        Standard = '成功與失敗'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 124
        ID = 'TWGCB-01-007-0224'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\帳戶登入\稽核Kerberos服務票證操作'
        Name = '稽核Kerberos服務票證操作'
        Standard = '成功與失敗'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 125
        ID = 'TWGCB-01-007-0225'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\帳戶登入\稽核其他帳戶登入事件'
        Name = '稽核其他帳戶登入事件'
        Standard = '成功與失敗'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 126
        ID = 'TWGCB-01-007-0226 (FCB-01-001-0096)'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\帳戶管理\稽核應用程式群組管理'
        Name = '稽核應用程式群組管理'
        Standard = '沒有稽核'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 127
        ID = 'TWGCB-01-007-0227'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\帳戶管理\稽核電腦帳戶管理'
        Name = '稽核電腦帳戶管理'
        Standard = '成功與失敗'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 128
        ID = 'TWGCB-01-007-0228'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\帳戶管理\稽核其他帳戶管理事件'
        Name = '稽核其他帳戶管理事件'
        Standard = '成功與失敗'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 129
        ID = 'TWGCB-01-007-0229'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\帳戶管理\稽核安全性群組管理'
        Name = '稽核安全性群組管理'
        Standard = '成功與失敗'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 130
        ID = 'TWGCB-01-007-0230'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\帳戶管理\稽核發佈群組管理'
        Name = '稽核發佈群組管理'
        Standard = '成功'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 131
        ID = 'TWGCB-01-007-0231'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\帳戶管理\稽核使用者帳戶管理'
        Name = '稽核使用者帳戶管理'
        Standard = '成功與失敗'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 132
        ID = 'TWGCB-01-007-0233'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\登入/登出\稽核IPsec延伸模式'
        Name = '稽核IPsec延伸模式'
        Standard = '沒有稽核'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 133
        ID = 'TWGCB-01-007-0234'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\登入/登出\稽核網路原則伺服器'
        Name = '稽核網路原則伺服器'
        Standard = '成功'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 134
        ID = 'TWGCB-01-007-0235'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\登入/登出\稽核IPsec主要模式'
        Name = '稽核IPsec主要模式'
        Standard = '沒有稽核'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 135
        ID = 'TWGCB-01-007-0236 (FCB-01-001-0097)'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\登入/登出\稽核其他登入/登出事件'
        Name = '稽核其他登入/登出事件'
        Standard = '成功與失敗'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 136
        ID = 'TWGCB-01-007-0237'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\登入/登出\稽核登出'
        Name = '稽核登出'
        Standard = '成功與失敗'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 137
        ID = 'TWGCB-01-007-0238'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\登入/登出\稽核登入'
        Name = '稽核登入'
        Standard = '成功與失敗'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 138
        ID = 'TWGCB-01-007-0239'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\登入/登出\稽核IPsec快速模式'
        Name = '稽核IPsec快速模式'
        Standard = '沒有稽核'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 139
        ID = 'TWGCB-01-007-0240'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\登入/登出\稽核特殊登入'
        Name = '稽核特殊登入'
        Standard = '成功'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 140
        ID = 'TWGCB-01-007-0241'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\詳細追蹤\稽核RPC事件'
        Name = '稽核RPC事件'
        Standard = '沒有稽核'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 141
        ID = 'TWGCB-01-007-0242'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\詳細追蹤\稽核DPAPI活動'
        Name = '稽核DPAPI活動'
        Standard = '沒有稽核'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 142
        ID = 'TWGCB-01-007-0243'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\詳細追蹤\稽核建立處理程序'
        Name = '稽核建立處理程序'
        Standard = '成功'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 143
        ID = 'TWGCB-01-007-0244'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\詳細追蹤\稽核終止處理程序'
        Name = '稽核終止處理程序'
        Standard = '成功與失敗'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 144
        ID = 'TWGCB-01-007-0064'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\帳戶：Guest帳戶狀態'
        Name = '帳戶：Guest帳戶狀態'
        Standard = '已停用'
        RegPath = '(無)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 145
        ID = 'TWGCB-01-007-0164'
        Category = '系統管理範本\Windows元件'
        GPO = '電腦設定\系統管理範本\Windows元件\OneDrive\防止使用 OneDrive 儲存檔案'
        Name = '防止使用 OneDrive 儲存檔案'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Policies\Microsoft\Windows\Skydrive\DisableFileSync'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 146
        ID = 'TWGCB-01-007-0167'
        Category = '系統管理範本\Windows元件'
        GPO = '電腦設定\系統管理範本\Windows元件\市集\關閉市集應用程式'
        Name = '關閉市集應用程式'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Policies\Microsoft\WindowsStore\RemoveWindowsStore'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 147
        ID = 'TWGCB-01-007-0168'
        Category = '系統管理範本\Windows元件'
        GPO = '電腦設定\系統管理範本\Windows元件\市集\關閉自動下載和安裝更新'
        Name = '關閉自動下載和安裝更新'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Policies\Microsoft\WindowsStore\AutoDownload'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 148
        ID = 'TWGCB-01-007-0169'
        Category = '系統管理範本\Windows元件'
        GPO = '電腦設定\系統管理範本\Windows元件\市集\關閉更新至最新版 Windows 的服務'
        Name = '關閉更新至最新版 Windows 的服務'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Policies\Microsoft\WindowsStore\DisableOSUpgrade'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 149
        ID = 'TWGCB-01-007-0173'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\稽核建立處理程序\在建立處理程序事件中包含命令列'
        Name = '在建立處理程序事件中包含命令列'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Microsoft\Windows\CurrentVersion\Policies\System\Audit\ProcessCreationIncludeCmdLine_Enabled'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 150
        ID = 'TWGCB-01-007-0174'
        Category = '系統管理範本\Windows元件'
        GPO = '電腦設定\系統管理範本\Windows元件\搜尋\允許加密檔案索引'
        Name = '允許加密檔案索引'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\Windows Search\AllowIndexingEncryptedStoresOrItems'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 151
        ID = 'TWGCB-01-007-0175'
        Category = '系統管理範本\Windows元件'
        GPO = '電腦設定\系統管理範本\Windows元件\搜尋\設定 [搜尋] 中可以分享的資訊'
        Name = '設定 [搜尋] 中可以分享的資訊'
        Standard = '已啟用：匿名資訊'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\Windows Search\ConnectedSearchPrivacy'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 152
        ID = 'TWGCB-01-007-0176'
        Category = '系統管理範本\Windows元件'
        GPO = '電腦設定\系統管理範本\Windows元件\應用程式執行階段\允許選用Microsoft帳戶'
        Name = '允許選用Microsoft帳戶'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Microsoft\Windows\CurrentVersion\Policies\System\MSAOptional'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 153
        ID = 'TWGCB-01-007-0177'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\網際網路通訊管理\網際網路通訊設定\關閉Windows Messenger客戶經驗改進計畫'
        Name = '關閉Windows Messenger客戶經驗改進計畫'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Policies\Microsoft\Messenger\Client\CEIP'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 154
        ID = 'TWGCB-01-007-0178'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\網際網路通訊管理\網際網路通訊設定\關閉搜尋小幫手內容檔更新'
        Name = '關閉搜尋小幫手內容檔更新'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Policies\Microsoft\SearchCompanion\DisableContentFileUpdates'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 155
        ID = 'TWGCB-01-007-0185'
        Category = '系統管理範本\控制台'
        GPO = '電腦設定\系統管理範本\控制台\個人化\防止啟用鎖定畫面投影片放映'
        Name = '防止啟用鎖定畫面投影片放映'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\Software\Policies\Microsoft\Windows\Personalization\NoLockScreenSlideshow'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 156
        ID = 'TWGCB-01-007-0267'
        Category = '系統管理範本\Windows元件'
        GPO = '電腦設定\系統管理範本\Windows元件\Windows Defender\MAPS\加入 Microsoft MAPS'
        Name = '加入 Microsoft MAPS'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows Defender\SpyNet\SpyNetReporting'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 157
        ID = 'TWGCB-01-007-0146'
        Category = '系統管理範本\Windows元件'
        GPO = '電腦設定\系統管理範本\Windows元件\Windows Installer\允許使用者控制安裝'
        Name = '允許使用者控制安裝'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\Installer\EnableUserControl'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 158
        ID = 'TWGCB-01-007-0147'
        Category = '系統管理範本\Windows元件'
        GPO = '電腦設定\系統管理範本\Windows元件\Windows Installer\防止出現 Windows Installer 指令碼的 Internet Explorer 安全性提示'
        Name = '防止出現 Windows Installer 指令碼的 Internet Explorer 安全性提示'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\Installer\SafeForScripting'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 159
        ID = 'TWGCB-01-007-0269'
        Category = '系統管理範本\Windows元件'
        GPO = '電腦設定\系統管理範本\Windows元件\Windows 遠端管理 (WinRM)\WinRM 用戶端\允許未加密的流量'
        Name = '允許未加密的流量'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\WinRM\Client\AllowUnencryptedTraffic'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 160
        ID = 'TWGCB-01-007-0270'
        Category = '系統管理範本\Windows元件'
        GPO = '電腦設定\系統管理範本\Windows元件\Windows 遠端管理 (WinRM)\WinRM 用戶端\允許基本驗證'
        Name = '允許基本驗證'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\WinRM\Client\AllowBasic'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 161
        ID = 'TWGCB-01-007-0272'
        Category = '系統管理範本\Windows元件'
        GPO = '電腦設定\系統管理範本\Windows元件\Windows 遠端管理 (WinRM)\WinRM 服務\允許未加密的流量'
        Name = '允許未加密的流量'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\WinRM\Service\AllowUnencryptedTraffic'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 162
        ID = 'TWGCB-01-007-0273'
        Category = '系統管理範本\Windows元件'
        GPO = '電腦設定\系統管理範本\Windows元件\Windows 遠端管理 (WinRM)\WinRM 服務\允許基本驗證'
        Name = '允許基本驗證'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\WinRM\Service\AllowBasic'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 163
        ID = 'TWGCB-01-007-0150'
        Category = '系統管理範本\Windows元件'
        GPO = '電腦設定\系統管理範本\Windows元件\自動播放原則\設定 AutoRun 的預設行為'
        Name = '設定 AutoRun 的預設行為'
        Standard = '已啟用：不執行任何 Autorun 命令'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer\NoAutorun'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 164
        ID = 'TWGCB-01-007-0274 (FCB-01-001-0035)'
        Category = '系統管理範本\Windows元件'
        GPO = '電腦設定\系統管理範本\Windows元件\定位和感應器\關閉定位'
        Name = '關閉定位'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\LocationAndSensors\DisableLocation'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 165
        ID = 'TWGCB-01-007-0275'
        Category = '系統管理範本\Windows元件'
        GPO = '電腦設定\系統管理範本\Windows元件\認證使用者介面\不要顯示密碼顯示按鈕'
        Name = '不要顯示密碼顯示按鈕'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\CredUI\DisablePasswordReveal'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 166
        ID = 'TWGCB-01-007-0276'
        Category = '系統管理範本\Windows元件'
        GPO = '電腦設定\系統管理範本\Windows元件\認證使用者介面\提升權限時列舉系統管理員帳戶'
        Name = '提升權限時列舉系統管理員帳戶'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\CredUI\EnumerateAdministrators'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 167
        ID = 'TWGCB-01-007-0153 (FCB-01-001-0102)'
        Category = '系統管理範本\Windows元件'
        GPO = '電腦設定\系統管理範本\Windows元件\遠端桌面服務\遠端桌面工作階段主機\安全性\需要安全的 RPC 通訊'
        Name = '需要安全的 RPC 通訊'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services\fEncryptRPCTraffic'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 168
        ID = 'TWGCB-01-007-0155'
        Category = '系統管理範本\Windows元件'
        GPO = '電腦設定\系統管理範本\Windows元件\遠端桌面服務\遠端桌面工作階段主機\暫存資料夾\不要使用每一工作階段的暫存資料夾'
        Name = '不要使用每一工作階段的暫存資料夾'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services\PerSessionTempDir'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 169
        ID = 'TWGCB-01-007-0156'
        Category = '系統管理範本\Windows元件'
        GPO = '電腦設定\系統管理範本\Windows元件\遠端桌面服務\遠端桌面工作階段主機\暫存資料夾\結束後不刪除暫存資料夾'
        Name = '結束後不刪除暫存資料夾'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services\DeleteTempDirsOnExit'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 170
        ID = 'TWGCB-01-007-0157'
        Category = '系統管理範本\Windows元件'
        GPO = '電腦設定\系統管理範本\Windows元件\遠端桌面服務\遠端桌面連線用戶端\不允許儲存密碼'
        Name = '不允許儲存密碼'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services\DisablePasswordSaving'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 171
        ID = 'TWGCB-01-007-0278'
        Category = '系統管理範本\Windows元件'
        GPO = '電腦設定\系統管理範本\Windows元件\檔案總管\損毀時關閉終止堆集'
        Name = '損毀時關閉終止堆集'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\Explorer\NoHeapTerminationOnCorruption'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 172
        ID = 'TWGCB-01-007-0279'
        Category = '系統管理範本\Windows元件'
        GPO = '電腦設定\系統管理範本\Windows元件\檔案總管\關閉殼層通訊協定受保護模式'
        Name = '關閉殼層通訊協定受保護模式'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer\PreXPSP2ShellProtocolBehavior'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 173
        ID = 'TWGCB-01-007-0280'
        Category = '系統管理範本\Windows元件'
        GPO = '電腦設定\系統管理範本\Windows元件\檔案總管\關閉檔案總管的資料執行防止'
        Name = '關閉檔案總管的資料執行防止'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\ExplorerNoDataExecutionPrevention'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 174
        ID = 'TWGCB-01-007-0171'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\登入\列舉加入網域電腦上的本機使用者'
        Name = '列舉加入網域電腦上的本機使用者'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\System\EnumerateLocalUsers'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 175
        ID = 'TWGCB-01-007-0172'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\登入\關閉鎖定畫面上的應用程式通知'
        Name = '關閉鎖定畫面上的應用程式通知'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\System\DisableLockScreenAppNotifications'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 176
        ID = 'TWGCB-01-007-0281'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\開機初期啟動的反惡意程式碼\開機啟動驅動程式初始化原則'
        Name = '開機啟動驅動程式初始化原則'
        Standard = '已啟用：良好和不明'
        RegPath = 'HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Policies\EarlyLaunch\DriverLoadPolicy'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 177
        ID = 'TWGCB-01-007-0282 (FCB-01-001-0103)'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\電源管理\睡眠設定\喚醒電腦時必須使用密碼 (一般電源)'
        Name = '喚醒電腦時必須使用密碼 (一般電源)'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Power\PowerSettings\0e796bdb-100d-47d6-a2d5-f7d2daa51f51\ACSettingIndex'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 178
        ID = 'TWGCB-01-007-0283 (FCB-01-001-0104)'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\電源管理\睡眠設定\喚醒電腦時必須使用密碼 (使用電池)'
        Name = '喚醒電腦時必須使用密碼 (使用電池)'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Power\PowerSettings\0e796bdb-100d-47d6-a2d5-f7d2daa51f51\DCSettingIndex'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 179
        ID = 'TWGCB-01-007-0284'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\疑難排解與診斷\Microsoft 支援服務診斷工具\Microsoft 支援服務診斷工具: 開啟 MSDT 與支援提供者的互動式通訊'
        Name = 'Microsoft 支援服務診斷工具: 開啟 MSDT 與支援提供者的互動式通訊'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\ScriptedDiagnosticsProvider\Policy\DisableQueryRemoteServer'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 180
        ID = 'TWGCB-01-007-0285'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\疑難排解與診斷\Windows 效能 PerfTrack\啟用/停用 PerfTrack'
        Name = '啟用/停用 PerfTrack'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\WDI\{9c5a40da-b965-4fc3-8781-88dd50a6299d}\ScenarioExecutionEnabled'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 181
        ID = 'TWGCB-01-007-0180'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\網際網路通訊管理\網際網路通訊設定\關閉 HTTP 上的列印'
        Name = '關閉 HTTP 上的列印'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows NT\Printers\DisableHTTPPrinting'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 182
        ID = 'TWGCB-01-007-0181'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\網際網路通訊管理\網際網路通訊設定\關閉 Windows客戶經驗改進計畫'
        Name = '關閉 Windows客戶經驗改進計畫'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\SQMClient\Windows\CEIPEnable'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 183
        ID = 'TWGCB-01-007-0182'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\網際網路通訊管理\網際網路通訊設定\關閉手寫辨識錯誤報告'
        Name = '關閉手寫辨識錯誤報告'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\HandwritingErrorReports\PreventHandwritingErrorReports'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 184
        ID = 'TWGCB-01-007-0183'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\網際網路通訊管理\網際網路通訊設定\關閉市集的存取權'
        Name = '關閉市集的存取權'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\Explorer\NoUseStoreOpenWith'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 185
        ID = 'TWGCB-01-007-0184'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\網際網路通訊管理\網際網路通訊設定\關閉透過 HTTP 下載印表機驅動程式'
        Name = '關閉透過 HTTP 下載印表機驅動程式'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows NT\Printers\DisableWebPnPDownload'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 186
        ID = 'TWGCB-01-007-0286'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\遠端協助\設定提供遠端協助'
        Name = '設定提供遠端協助'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services\fAllowUnsolicited'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 187
        ID = 'TWGCB-01-007-0287'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\遠端協助\設定請求遠端協助'
        Name = '設定請求遠端協助'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services\fAllowToGetHelp'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 188
        ID = 'TWGCB-01-007-0288'
        Category = '系統管理範本\網路'
        GPO = '電腦設定\系統管理範本\網路\Microsoft 對等網路服務\關閉 Microsoft 對等網路服務'
        Name = '關閉 Microsoft 對等網路服務'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Peernet\Disabled'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 189
        ID = 'TWGCB-01-007-0289'
        Category = '系統管理範本\網路'
        GPO = '電腦設定\系統管理範本\網路\Windows Connect Now\使用 Windows Connect Now 進行無線設定'
        Name = '使用 Windows Connect Now 進行無線設定'
        Standard = '已停用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\WCN\Registrars\EnableRegistrars HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\WCN\Registrars\DisableUPnPRegistrar HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\WCN\Registrars\DisableInBand802DOT11Registrar HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\WCN\Registrars\DisableFlashConfigRegistrar HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\WCN\Registrars\DisableWPDRegistrar'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 190
        ID = 'TWGCB-01-007-0290'
        Category = '系統管理範本\網路'
        GPO = '電腦設定\系統管理範本\網路\Windows Connect Now\禁止存取 Windows Connect Now 精靈'
        Name = '禁止存取 Windows Connect Now 精靈'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\WCN\UI\DisableWcnUi'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 191
        ID = 'TWGCB-01-007-0293'
        Category = '系統管理範本\網路'
        GPO = '電腦設定\系統管理範本\網路\網路連線\要求網域使用者在設定網路的位置時必須提升權限'
        Name = '要求網域使用者在設定網路的位置時必須提升權限'
        Standard = '已啟用'
        RegPath = 'HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\Network Connections\NC_StdDomainUserSetLocation'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 192
        ID = 'FCB-01-001-0001'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows 設定\安全性設定\本機原則\安全性選項\網域控制站： 允許易受攻擊的 Netlogon 安全通道連線'
        Name = '允許易受攻擊的 Netlogon 安全通道連線'
        Standard = '保留空白'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 193
        ID = 'FCB-01-001-0005'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows 設定\安全性設定\進階稽核原則設定\系統稽核原則\物件存取\稽核抽取式存放裝置'
        Name = '稽核抽取式存放裝置'
        Standard = '成功與失敗'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 194
        ID = 'FCB-01-001-0006'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows 設定\安全性設定\進階稽核原則設定\系統稽核原則\登入/登出\稽核群組成員資格'
        Name = '稽核群組成員資格'
        Standard = '成功'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 195
        ID = 'FCB-01-001-0007'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows 設定\安全性設定\進階稽核原則設定\系統稽核原則\詳細追蹤\稽核 PNP 活動'
        Name = '稽核 PNP 活動'
        Standard = '成功'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 196
        ID = 'FCB-01-001-0008'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\安全性設定\本機原則\安全性選項\帳戶: 封鎖 Microsoft 帳戶'
        Name = '帳戶: 封鎖 Microsoft 帳戶'
        Standard = '用戶無法添加或使用 Microsoft 帳戶登錄'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 197
        ID = 'FCB-01-001-0009'
        Category = 'Windows Defender 防火牆'
        GPO = '電腦設定\Windows設定\安全性設定\具有進階安全性的 Windows Defender 防火牆\Windows Defender 防火牆內容\公用設定檔\記錄\自訂\大小限制 (KB)'
        Name = '大小限制 (KB)'
        Standard = '16384KB或以上'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 198
        ID = 'FCB-01-001-0010'
        Category = 'Windows Defender 防火牆'
        GPO = '電腦設定\Windows設定\安全性設定\具有進階安全性的 Windows Defender 防火牆\Windows Defender 防火牆內容\公用設定檔\記錄\自訂\名稱'
        Name = '名稱'
        Standard = '%系統Root%\系統32\logfiles\firewall\publicfw.log'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 199
        ID = 'FCB-01-001-0011'
        Category = 'Windows Defender 防火牆'
        GPO = '電腦設定\Windows設定\安全性設定\具有進階安全性的 Windows Defender 防火牆\Windows Defender 防火牆內容\公用設定檔\記錄\自訂\記錄丟棄的封包'
        Name = '記錄丟棄的封包'
        Standard = '是'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 200
        ID = 'FCB-01-001-0012'
        Category = 'Windows Defender 防火牆'
        GPO = '電腦設定\Windows設定\安全性設定\具有進階安全性的 Windows Defender 防火牆\Windows Defender 防火牆內容\公用設定檔\記錄\自訂\記錄成功的連接'
        Name = '記錄成功的連接'
        Standard = '是'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 201
        ID = 'FCB-01-001-0013'
        Category = 'Windows Defender 防火牆'
        GPO = '電腦設定\Windows設定\安全性設定\具有進階安全性的 Windows Defender 防火牆\Windows Defender 防火牆內容\私人設定檔\記錄\自訂\大小限制 (KB)'
        Name = '大小限制 (KB)'
        Standard = '16384KB或以上'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 202
        ID = 'FCB-01-001-0014'
        Category = 'Windows Defender 防火牆'
        GPO = '電腦設定\Windows設定\安全性設定\具有進階安全性的 Windows Defender 防火牆\Windows Defender 防火牆內容\私人設定檔\記錄\自訂\名稱'
        Name = '名稱'
        Standard = '%系統Root%\系統32\logfiles\firewall\privatefw.log'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 203
        ID = 'FCB-01-001-0015'
        Category = 'Windows Defender 防火牆'
        GPO = '電腦設定\Windows設定\安全性設定\具有進階安全性的 Windows Defender 防火牆\Windows Defender 防火牆內容\私人設定檔\記錄\自訂\記錄丟棄的封包'
        Name = '記錄丟棄的封包'
        Standard = '是'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 204
        ID = 'FCB-01-001-0016'
        Category = 'Windows Defender 防火牆'
        GPO = '電腦設定\Windows設定\安全性設定\具有進階安全性的 Windows Defender 防火牆\Windows Defender 防火牆內容\私人設定檔\記錄\自訂\記錄成功的連接'
        Name = '記錄成功的連接'
        Standard = '是'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 205
        ID = 'FCB-01-001-0017'
        Category = 'Windows Defender 防火牆'
        GPO = '電腦設定\Windows設定\安全性設定\具有進階安全性的 Windows Defender 防火牆\Windows Defender 防火牆內容\網域設定檔\記錄\自訂\大小限制 (KB)'
        Name = '大小限制 (KB)'
        Standard = '16384KB或以上'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 206
        ID = 'FCB-01-001-0018'
        Category = 'Windows Defender 防火牆'
        GPO = '電腦設定\Windows設定\安全性設定\具有進階安全性的 Windows Defender 防火牆\Windows Defender 防火牆內容\網域設定檔\記錄\自訂\名稱'
        Name = '名稱'
        Standard = '%系統Root%\系統32\logfiles\firewall\domainfw.log'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 207
        ID = 'FCB-01-001-0019'
        Category = 'Windows Defender 防火牆'
        GPO = '電腦設定\Windows設定\安全性設定\具有進階安全性的 Windows Defender 防火牆\Windows Defender 防火牆內容\網域設定檔\記錄\自訂\記錄丟棄的封包'
        Name = '記錄丟棄的封包'
        Standard = '是'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 208
        ID = 'FCB-01-001-0020'
        Category = 'Windows Defender 防火牆'
        GPO = '電腦設定\Windows設定\安全性設定\具有進階安全性的 Windows Defender 防火牆\Windows Defender 防火牆內容\網域設定檔\記錄\自訂\記錄成功的連接'
        Name = '記錄成功的連接'
        Standard = '是'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 209
        ID = 'FCB-01-001-0021'
        Category = '系統管理範本\[開始] 功能表和工作列'
        GPO = '電腦設定\系統管理範本\[開始] 功能表和工作列\通知\關閉通知網路使用方式'
        Name = '關閉通知網路使用方式'
        Standard = '已啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 210
        ID = 'FCB-01-001-0022'
        Category = '系統管理範本\MS Security Guide'
        GPO = '電腦設定\系統管理範本\MS Security Guide\NetBT NodeType configuration'
        Name = 'NetBT NodeType configuration'
        Standard = 'P-Node (建議)'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 211
        ID = 'FCB-01-001-0023'
        Category = '系統管理範本\MS Security Guide'
        GPO = '電腦設定\系統管理範本\MS Security Guide\WDigest Authentication'
        Name = 'WDigest Authentication'
        Standard = '停用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 212
        ID = 'FCB-01-001-0024'
        Category = '系統管理範本\MS Security Guide'
        GPO = '電腦設定\系統管理範本\MS Security Guide\Apply UAC restrictions to local accounts on network logons'
        Name = 'Apply UAC restrictions to local accounts on network logons'
        Standard = '啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 213
        ID = 'FCB-01-001-0025'
        Category = '系統管理範本\Windows 元件'
        GPO = '電腦設定\系統管理範本\Windows 元件\Windows Installer\禁止非系統管理員套用廠商簽署的更新'
        Name = '禁止非系統管理員套用廠商簽署的更新'
        Standard = '啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 214
        ID = 'FCB-01-001-0026'
        Category = '系統管理範本\Windows 元件'
        GPO = '電腦設定\系統管理範本\Windows 元件\Windows Media Player\防止自動更新'
        Name = '防止自動更新'
        Standard = '啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 215
        ID = 'FCB-01-001-0027'
        Category = '系統管理範本\Windows 元件'
        GPO = '電腦設定\系統管理範本\Windows 元件\Windows PowerShell\打開 PowerShell 指令碼區塊記錄'
        Name = '打開 PowerShell 指令碼區塊記錄'
        Standard = '停用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 216
        ID = 'FCB-01-001-0028'
        Category = '系統管理範本\Windows 元件'
        GPO = '電腦設定\系統管理範本\Windows 元件\Windows PowerShell\打開 PowerShell 轉譯'
        Name = '打開 PowerShell 轉譯'
        Standard = '已停用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 217
        ID = 'FCB-01-001-0029'
        Category = '系統管理範本\Windows 元件'
        GPO = '電腦設定\系統管理範本\Windows 元件\Windows Update\舊版原則\選取接收品質更新的時機有使用者登入時不自動重新開機以完成排定的自動更新安裝'
        Name = '選取接收品質更新的時機有使用者登入時不自動重新開機以完成排定的自動更新安裝'
        Standard = '停用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 218
        ID = 'FCB-01-001-0030'
        Category = '系統管理範本\Windows 元件'
        GPO = '電腦設定\系統管理範本\Windows 元件\Windows Update\管理 Windows Update 提供的更新\選取接收品質更新的時機'
        Name = '選取接收品質更新的時機'
        Standard = '啟用: 0天'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 219
        ID = 'FCB-01-001-0031'
        Category = '系統管理範本\Windows 元件'
        GPO = '電腦設定\系統管理範本\Windows 元件\Windows Update\管理 Windows Update 提供的更新\選取接收預覽組建及功能更新的時機'
        Name = '選取接收預覽組建及功能更新的時機'
        Standard = '啟用: 半年通道，180天或以上'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 220
        ID = 'FCB-01-001-0032'
        Category = '系統管理範本\Windows 元件'
        GPO = '電腦設定\系統管理範本\Windows 元件\Windows 安全性\應用程式與瀏覽器保護\防止使用者修改設定'
        Name = '防止使用者修改設定'
        Standard = '啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 221
        ID = 'FCB-01-001-0033'
        Category = '系統管理範本\Windows 元件'
        GPO = '電腦設定\系統管理範本\Windows 元件\事件紀錄服務\安裝\控制記錄檔達到其大小上限時的事件記錄檔行為'
        Name = '控制記錄檔達到其大小上限時的事件記錄檔行為'
        Standard = '停用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 222
        ID = 'FCB-01-001-0034'
        Category = '系統管理範本\Windows元件'
        GPO = '電腦設定\系統管理範本\Windows元件\事件記錄服務\安裝\指定記錄檔大小上限 (KB)'
        Name = '指定記錄檔大小上限 (KB)'
        Standard = '已啟用: 32,768 或以上'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 223
        ID = 'FCB-01-001-0036'
        Category = '系統管理範本\Windows 元件'
        GPO = '電腦設定\系統管理範本\Windows 元件\軟體保護平台\關閉 KMS 用戶端在線 AVS 驗證'
        Name = '關閉 KMS 用戶端在線 AVS 驗證'
        Standard = '啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 224
        ID = 'FCB-01-001-0037'
        Category = '系統管理範本\Windows 元件'
        GPO = '電腦設定\系統管理範本\Windows 元件\資料收集與預覽組建\不顯示意見反應通知'
        Name = '不顯示意見反應通知'
        Standard = '已啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 225
        ID = 'FCB-01-001-0038'
        Category = '系統管理範本\Windows 元件'
        GPO = '電腦設定\系統管理範本\Windows 元件\資料收集與預覽組建\切換使用者對測試人員組建的控制'
        Name = '切換使用者對測試人員組建的控制'
        Standard = '已停用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 226
        ID = 'FCB-01-001-0039'
        Category = '系統管理範本\Windows 元件'
        GPO = '電腦設定\系統管理範本\Windows 元件\資料收集與預覽組建\為「已連線使用者體驗與遙測」服務設定已驗證的 Proxy'
        Name = '為「已連線使用者體驗與遙測」服務設定已驗證的 Proxy'
        Standard = '已啟用: 停用已驗證的 Proxy'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 227
        ID = 'FCB-01-001-0040'
        Category = '系統管理範本\Windows 元件'
        GPO = '電腦設定\系統管理範本\Windows 元件\遠端桌面服務\遠端工作階段主機\連線\限制遠端桌面服務的使用者只能使用一個遠端桌面服務工作階段'
        Name = '限制遠端桌面服務的使用者只能使用一個遠端桌面服務工作階段'
        Standard = '已啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 228
        ID = 'FCB-01-001-0043'
        Category = '系統管理範本\Windows 元件'
        GPO = '電腦設定\系統管理範本\Windows 元件\遠端桌面服務\遠端桌面工作階段主機\裝置及資源重新導向\不允許 COM 連接埠重新導向'
        Name = '不允許 COM 連接埠重新導向'
        Standard = '啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 229
        ID = 'FCB-01-001-0044'
        Category = '系統管理範本\Windows 元件'
        GPO = '電腦設定\系統管理範本\Windows 元件\遠端桌面服務\遠端桌面工作階段主機\裝置及資源重新導向\不允許 LPT 連接埠重新導向'
        Name = '不允許 LPT 連接埠重新導向'
        Standard = '啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 230
        ID = 'FCB-01-001-0045'
        Category = '系統管理範本\Windows 元件'
        GPO = '電腦設定\系統管理範本\Windows 元件\遠端桌面服務\遠端桌面工作階段主機\裝置及資源重新導向\不允許支援的隨插即用裝置重新導向'
        Name = '不允許支援的隨插即用裝置重新導向'
        Standard = '已啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 231
        ID = 'FCB-01-001-0046'
        Category = '系統管理範本\Windows 元件'
        GPO = '電腦設定\系統管理範本\Windows 元件\遠端桌面服務\遠端桌面工作階段主機\裝置及資源重新導向\不允許智慧卡裝置重新導向'
        Name = '不允許智慧卡裝置重新導向'
        Standard = '停用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 232
        ID = 'FCB-01-001-0047'
        Category = '系統管理範本\Windows 元件'
        GPO = '電腦設定\系統管理範本\Windows 元件\應用程式套件部署\允許 Windows 應用程式在使用者間共用應用程式資料'
        Name = '允許 Windows 應用程式在使用者間共用應用程式資料'
        Standard = '停用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 233
        ID = 'FCB-01-001-0048'
        Category = '系統管理範本\Windows 元件'
        GPO = '電腦設定\系統管理範本\Windows 元件\應用程式套件部署\允許安裝所有受信任的應用程式'
        Name = '允許安裝所有受信任的應用程式'
        Standard = '啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 234
        ID = 'FCB-01-001-0049'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\Windows 時間服務\時間提供者\啟用 Windows NTP 用戶端'
        Name = '啟用 Windows NTP 用戶端'
        Standard = '已啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 235
        ID = 'FCB-01-001-0050'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\Windows 時間服務\時間提供者\啟用 Windows NTP 伺服器'
        Name = '啟用 Windows NTP 伺服器'
        Standard = '已停用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 236
        ID = 'FCB-01-001-0051'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\地區設定服務\不允許將使用者輸入法複製到系統帳戶以便登入'
        Name = '不允許將使用者輸入法複製到系統帳戶以便登入'
        Standard = '已啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 237
        ID = 'FCB-01-001-0052'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\指定選用之元件安裝和元件修復的相關設定'
        Name = '指定選用之元件安裝和元件修復的相關設定'
        Standard = '啟用，從不嘗試從 Windows 更新下載有效負載'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 238
        ID = 'FCB-01-001-0053'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\登入\不要列舉加入網域電腦上的連線使用者'
        Name = '不要列舉加入網域電腦上的連線使用者'
        Standard = '啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 239
        ID = 'FCB-01-001-0054'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\登入\防止使用者在登入時顯示帳戶詳細資料'
        Name = '防止使用者在登入時顯示帳戶詳細資料'
        Standard = '啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 240
        ID = 'FCB-01-001-0055'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\登入\開啟方便的 PIN 登入'
        Name = '開啟方便的 PIN 登入'
        Standard = '停用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 241
        ID = 'FCB-01-001-0056'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\登入\關閉圖片密碼登錄'
        Name = '關閉圖片密碼登錄'
        Standard = '啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 242
        ID = 'FCB-01-001-0057'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\群組原則\設定登錄原則處理: 在定期的背景處理期間不要套用'
        Name = '設定登錄原則處理: 在定期的背景處理期間不要套用'
        Standard = '已啟用: FALSE'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 243
        ID = 'FCB-01-001-0058'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\群組原則\設定登錄原則處理: 即使群組原則物件尚未變更也進行處理'
        Name = '設定登錄原則處理: 即使群組原則物件尚未變更也進行處理'
        Standard = '啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 244
        ID = 'FCB-01-001-0059'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\群組原則\關閉群組原則背景更新'
        Name = '關閉群組原則背景更新'
        Standard = '已停用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 245
        ID = 'FCB-01-001-0060'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\裝置安裝\允許遠端存取隨插即用介面'
        Name = '允許遠端存取隨插即用介面'
        Standard = '停用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 246
        ID = 'FCB-01-001-0061'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\裝置安裝\在通常會提示建立系統還原點的裝置活動期間，防止建立系統還原點'
        Name = '在通常會提示建立系統還原點的裝置活動期間，防止建立系統還原點'
        Standard = '停用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 247
        ID = 'FCB-01-001-0062'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\裝置安裝\防止從網際網路擷取裝置中繼資料'
        Name = '防止從網際網路擷取裝置中繼資料'
        Standard = '啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 248
        ID = 'FCB-01-001-0063'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\裝置安裝\指定裝置驅動程式更新的搜尋伺服器'
        Name = '指定裝置驅動程式更新的搜尋伺服器'
        Standard = '啟用，選取更新伺服器：搜尋受管理伺服器'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 249
        ID = 'FCB-01-001-0064'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\裝置安裝\指定裝置驅動程式來源位置的搜尋順序'
        Name = '指定裝置驅動程式來源位置的搜尋順序'
        Standard = '啟用，選取搜尋順序：不搜尋 Windows Update'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 250
        ID = 'FCB-01-001-0065'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\裝置安裝\當裝置上已安裝標準驅動程式時，不要傳送 Windows 錯誤報告'
        Name = '當裝置上已安裝標準驅動程式時，不要傳送 Windows 錯誤報告'
        Standard = '啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 251
        ID = 'FCB-01-001-0066'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\裝置安裝\當裝置驅動程式在安裝期間要求其他軟體時，防止 Windows 傳送錯誤報告'
        Name = '當裝置驅動程式在安裝期間要求其他軟體時，防止 Windows 傳送錯誤報告'
        Standard = '啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 252
        ID = 'FCB-01-001-0067'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\網際網路通訊管理\網際網路通訊設定\關閉 Windows Update 裝置驅動程式搜尋'
        Name = '關閉 Windows Update 裝置驅動程式搜尋'
        Standard = '啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 253
        ID = 'FCB-01-001-0068'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\網際網路通訊管理\網際網路通訊設定\關閉 Windows 錯誤報告'
        Name = '關閉 Windows 錯誤報告'
        Standard = '啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 254
        ID = 'FCB-01-001-0069'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\網際網路通訊管理\網際網路通訊設定\關閉事件檢視器 "Events.asp" 連結'
        Name = '關閉事件檢視器 "Events.asp" 連結'
        Standard = '啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 255
        ID = 'FCB-01-001-0070'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\網際網路通訊管理\網際網路通訊設定\關閉個人化手寫資料共用'
        Name = '關閉個人化手寫資料共用'
        Standard = '啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 256
        ID = 'FCB-01-001-0071'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\網際網路通訊管理\網際網路通訊設定\關閉網際網路檔案關聯服務'
        Name = '關閉網際網路檔案關聯服務'
        Standard = '啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 257
        ID = 'FCB-01-001-0072'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\遠端協助\開啟工作階段記錄'
        Name = '開啟工作階段記錄'
        Standard = '啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 258
        ID = 'FCB-01-001-0073'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\遠端程序呼叫\限制未經驗證的 RPC 用戶端'
        Name = '限制未經驗證的 RPC 用戶端'
        Standard = '啟用 - 已驗證'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 259
        ID = 'FCB-01-001-0074'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\遠端程序呼叫\啟用 RPC 端點對應程式用戶端驗證'
        Name = '啟用 RPC 端點對應程式用戶端驗證'
        Standard = '啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 260
        ID = 'FCB-01-001-0075'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\驅動程式安裝\關閉 Windows Update 裝置驅動程式搜尋提示'
        Name = '關閉 Windows Update 裝置驅動程式搜尋提示'
        Standard = '啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 261
        ID = 'FCB-01-001-0076'
        Category = '系統管理範本\控制台'
        GPO = '電腦設定\系統管理範本\控制台\允許線上提示'
        Name = '允許線上提示'
        Standard = '已停用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 262
        ID = 'FCB-01-001-0077'
        Category = '系統管理範本\網路'
        GPO = '電腦設定\系統管理範本\網路\DNS用戶端\關閉多點傳送名稱解析'
        Name = '關閉多點傳送名稱解析'
        Standard = '啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 263
        ID = 'FCB-01-001-0078'
        Category = '系統管理範本\網路'
        GPO = '電腦設定\Windows設定 \系統管理範本\網路\ IPv6設定\IPv6設定規則\禁用 IPv6'
        Name = '禁用 IPv6'
        Standard = '0xff (255)'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 264
        ID = 'FCB-01-001-0079'
        Category = '系統管理範本\網路'
        GPO = '電腦設定\系統管理範本\網路\TCPIP 設定值\IPv6 轉換技術 \設定 IP-HTTPS 狀態'
        Name = '設定 IP-HTTPS 狀態'
        Standard = '已啟用， 輸入IPHTTPS URL：http://localhost 從下列選項中選取介面狀態：停用狀態'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 265
        ID = 'FCB-01-001-0080'
        Category = '系統管理範本\網路'
        GPO = '電腦設定\系統管理範本\網路\TCPIP 設定值\IPv6 轉換技術\設定 6to4 狀態'
        Name = '設定 6to4 狀態'
        Standard = '啟用：停用狀態'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 266
        ID = 'FCB-01-001-0081'
        Category = '系統管理範本\網路'
        GPO = '電腦設定\系統管理範本\網路\TCPIP 設定值\IPv6 轉換技術\設定 ISATAP 狀態'
        Name = '設定 ISATAP 狀態'
        Standard = '啟用：停用狀態'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 267
        ID = 'FCB-01-001-0082'
        Category = '系統管理範本\網路'
        GPO = '電腦設定\系統管理範本\網路\TCPIP 設定值\IPv6 轉換技術\設定 Teredo 狀態'
        Name = '設定 Teredo 狀態'
        Standard = '啟用：停用狀態'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 268
        ID = 'FCB-01-001-0083'
        Category = '系統管理範本\網路'
        GPO = '電腦設定\系統管理範本\網路\TCPIP 設定值\參數\設定 IP 無狀態自動設定限制狀態'
        Name = '設定 IP 無狀態自動設定限制狀態'
        Standard = '啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 269
        ID = 'FCB-01-001-0085'
        Category = '系統管理範本\網路'
        GPO = '電腦設定\系統管理範本\網路\字型\啟用字型提供者'
        Name = '啟用字型提供者'
        Standard = '已停用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 270
        ID = 'FCB-01-001-0087'
        Category = '系統管理範本\網路'
        GPO = '電腦設定\系統管理範本\網路\網路連線\禁止在您的 DNS 網域網路中使用網際網路連線共用'
        Name = '禁止在您的 DNS 網域網路中使用網際網路連線共用'
        Standard = '已啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 271
        ID = 'FCB-01-001-0089'
        Category = '系統管理範本\Windows 元件'
        GPO = '使用者設定\系統管理範本\Windows 元件\附件管理員\隱藏移除區域資訊的機制'
        Name = '隱藏移除區域資訊的機制'
        Standard = '啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 272
        ID = 'FCB-01-001-0090'
        Category = '系統管理範本\系統'
        GPO = '使用者設定\系統管理範本\系統\網際網路通訊管理\網際網路通訊設定\關閉說明分級'
        Name = '關閉說明分級'
        Standard = '啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 273
        ID = 'FCB-01-001-0091'
        Category = '系統管理範本\系統'
        GPO = '使用者設定\系統管理範本\系統\網際網路通訊管理\網際網路通訊設定\關閉說明使用經驗改進計畫'
        Name = '關閉說明使用經驗改進計畫'
        Standard = '已啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 274
        ID = 'FCB-01-001-0092'
        Category = '系統管理範本\控制台'
        GPO = '使用者設定\系統管理範本\控制台\個人化\螢幕保護裝置逾時'
        Name = '螢幕保護裝置逾時'
        Standard = '啟用: 900秒或以下，但不為0'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 275
        ID = 'FCB-01-001-0093 (TWGCB-01-007-0050)'
        Category = '本機原則\安全性選項'
        GPO = '電腦設定\Windows設定\安全性設定\本機原則\安全性選項\使用者帳戶控制：在管理員核准模式，系統管理員之提升權限提示的行為'
        Name = '使用者帳戶控制：在管理員核准模式，系統管理員之提升權限提示的行為'
        Standard = '在安全桌面提示要求同意'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 276
        ID = 'FCB-01-001-0098 (TWGCB-01-007-0232)'
        Category = '進階稽核原則'
        GPO = '電腦設定\Windows設定\安全性設定\進階稽核原則設定\稽核原則\登入/登出\稽核帳戶鎖定'
        Name = '稽核帳戶鎖定'
        Standard = '成功與失敗'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 277
        ID = 'FCB-01-001-0099 (TWGCB-01-007-0691)'
        Category = '系統管理範本\MS Security Guide'
        GPO = '電腦設定\系統管理範本\MS Security Guide\Enable Structured Exception Handling Overwrite Protection (SEHOP)'
        Name = 'Enable Structured Exception Handling Overwrite Protection (SEHOP)'
        Standard = '啟用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 278
        ID = 'FCB-01-001-0100 (TWGCB-01-007-0021)'
        Category = '系統管理範本\MSS (Legacy)'
        GPO = '電腦設定\系統管理範本\MSS (Legacy)\MSS：(EnableICMPRedirect) Allow ICMP redirects to override OSPF generated routes'
        Name = 'MSS：(EnableICMPRedirect) Allow ICMP redirects to override OSPF generated routes'
        Standard = '停用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 279
        ID = 'FCB-01-001-0101 (TWGCB-01-007-0027)'
        Category = '系統管理範本\MSS (Legacy)'
        GPO = '電腦設定\系統管理範本\MSS (Legacy)\MSS：(PerformRouterDiscovery) Allow IRDP to detect and configure Default Gateway addresses (could lead to DoS)'
        Name = 'MSS：(PerformRouterDiscovery) Allow IRDP to detect and configure Default Gateway addresses (could lead to DoS)'
        Standard = '已停用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 280
        ID = 'FCB-01-001-0105 (TWGCB-01-007-0696)'
        Category = '系統管理範本\系統'
        GPO = '電腦設定\系統管理範本\系統\認證委派\加密預示修復'
        Name = '加密預示修復'
        Standard = '啟用，保護層級：強制使用更新的用戶端'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 281
        ID = 'CSCB-01-2025-0001'
        Category = '系統服務'
        GPO = '電腦設定\系統服務\UPnPDevice Host'
        Name = '停用UPnPDevice Host'
        Standard = '停用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 282
        ID = 'CSCB-01-2025-0002'
        Category = '系統服務'
        GPO = '電腦設定\系統服務\Computer Browser'
        Name = '停用Computer Browser'
        Standard = '停用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 283
        ID = 'CSCB-01-2025-0004'
        Category = '系統服務'
        GPO = '電腦設定\系統服務\Smart Card'
        Name = '停用 Smart Card(智慧卡)'
        Standard = '停用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 284
        ID = 'CSCB-01-2025-0005'
        Category = '系統服務'
        GPO = '電腦設定\系統服務\Internet Connection Sharing (ics)'
        Name = 'Internet Connection Sharing(網際網路連線共用)'
        Standard = '停用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 285
        ID = 'CSCB-01-2025-0006'
        Category = '系統服務'
        GPO = '電腦設定\系統服務\Ssdp Discovery'
        Name = 'SSDP Discovery(Simple Service Discovery Protocol)'
        Standard = '停用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
    [PSCustomObject]@{
        Index = 286
        ID = 'CSCB-01-2025-0007'
        Category = '系統服務'
        GPO = '電腦設定\系統服務\裝置管理無線應用通訊協定(WAP)'
        Name = '停用通訊協定(WAP)'
        Standard = '停用'
        RegPath = '(略)'
        Result = '▓符合  □不符合'
        Note = ''
    }
)
# 每條的判定方式（由規則表逐條對應；T=sec/right/audit/svc/fw/reg/todo）
$Checks = @{
    1 = @{T = 'sec'; K = 'MinimumPasswordAge'; Op = 'ge'; V = 1}
    2 = @{T = 'sec'; K = 'MaximumPasswordAge'; Op = 'range'; V = @(1, 90)}
    3 = @{T = 'sec'; K = 'MinimumPasswordLength'; Op = 'ge'; V = 7}
    4 = @{T = 'sec'; K = 'PasswordComplexity'; Op = 'eq'; V = 1}
    5 = @{T = 'sec'; K = 'PasswordHistorySize'; Op = 'ge'; V = 3}
    6 = @{T = 'sec'; K = 'ClearTextPassword'; Op = 'eq'; V = 0}
    7 = @{T = 'sec'; K = 'LockoutBadCount'; Op = 'range'; V = @(1, 3)}
    8 = @{T = 'sec'; K = 'ResetLockoutCount'; Op = 'ge'; V = 15}
    9 = @{T = 'sec'; K = 'LockoutDuration'; Op = 'ge'; V = 15}
    10 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Services\LanmanWorkstation\Parameters'; N = 'EnablePlainTextPassword'; Op = 'eq'; V = 0})}
    11 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Services\LanmanWorkstation\Parameters'; N = 'EnableSecuritySignature'; Op = 'eq'; V = 1})}
    12 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Services\LanmanWorkstation\Parameters'; N = 'RequireSecuritySignature'; Op = 'eq'; V = 1})}
    13 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Services\LanManServer\Parameters'; N = 'SMBServerNameHardeningLevel'; Op = 'eq'; V = 0})}
    14 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Services\LanManServer\Parameters'; N = 'requiresecuritysignature'; Op = 'eq'; V = 1})}
    15 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Services\LanManServer\Parameters'; N = 'enablesecuritysignature'; Op = 'eq'; V = 1})}
    16 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Services\LanManServer\Parameters'; N = 'autodisconnect'; Op = 'range'; V = @(0, 15)})}
    17 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Services\LanManServer\Parameters'; N = 'enableforcedlogoff'; Op = 'eq'; V = 1})}
    18 = @{T = 'reg'; R = @(@{P = 'HKLM\Software\Microsoft\Windows NT\CurrentVersion\Winlogon'; N = 'passwordexpirywarning'; Op = 'ge'; V = 7})}
    19 = @{T = 'reg'; R = @(@{P = 'HKLM\Software\Microsoft\Windows NT\CurrentVersion\Winlogon'; N = 'cachedlogonscount'; Op = 'le'; V = 4})}
    20 = @{T = 'reg'; R = @(@{P = 'HKLM\Software\Microsoft\Windows NT\CurrentVersion\Winlogon'; N = 'scremoveoption'; Op = 'eq'; V = '1'})}
    21 = @{T = 'reg'; R = @(@{P = 'HKLM\Software\Microsoft\Windows\CurrentVersion\Policies\System'; N = 'scforceoption'; Op = 'eq'; V = 0})}
    22 = @{T = 'reg'; R = @(@{P = 'HKLM\Software\Microsoft\Windows\CurrentVersion\Policies\System'; N = 'DisableCAD'; Op = 'eq'; V = 0})}
    23 = @{T = 'reg'; R = @(@{P = 'HKLM\Software\Microsoft\Windows\CurrentVersion\Policies\System'; N = 'DontDisplayLastUserName'; Op = 'eq'; V = 1})}
    24 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Control\Session Manager'; N = 'ProtectionMode'; Op = 'eq'; V = 1})}
    25 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Control\Session Manager\Kernel'; N = 'ObCaseInsensitive'; Op = 'eq'; V = 1})}
    26 = @{T = 'reg'; R = @(@{P = 'HKLM\Software\Microsoft\Windows\CurrentVersion\Policies\System'; N = 'EnableUIADesktopToggle'; Op = 'eq'; V = 0})}
    27 = @{T = 'reg'; R = @(@{P = 'HKLM\Software\Microsoft\Windows\CurrentVersion\Policies\System'; N = 'ValidateAdminCodeSignatures'; Op = 'eq'; V = 0})}
    28 = @{T = 'reg'; R = @(@{P = 'HKLM\Software\Microsoft\Windows\CurrentVersion\Policies\System'; N = 'EnableLUA'; Op = 'eq'; V = 1})}
    29 = @{T = 'reg'; R = @(@{P = 'HKLM\Software\Microsoft\Windows\CurrentVersion\Policies\System'; N = 'EnableInstallerDetection'; Op = 'eq'; V = 1})}
    30 = @{T = 'reg'; R = @(@{P = 'HKLM\Software\Microsoft\Windows\CurrentVersion\Policies\System'; N = 'ConsentPromptBehaviorUser'; Op = 'eq'; V = 3})}
    31 = @{T = 'reg'; R = @(@{P = 'HKLM\Software\Microsoft\Windows\CurrentVersion\Policies\System'; N = 'EnableSecureUIAPaths'; Op = 'eq'; V = 1})}
    32 = @{T = 'reg'; R = @(@{P = 'HKLM\Software\Microsoft\Windows\CurrentVersion\Policies\System'; N = 'EnableVirtualization'; Op = 'eq'; V = 1})}
    33 = @{T = 'reg'; R = @(@{P = 'HKLM\Software\Microsoft\Windows NT\CurrentVersion\Setup\RecoveryConsole'; N = 'setcommand'; Op = 'eq'; V = 0})}
    34 = @{T = 'reg'; R = @(@{P = 'HKLM\Software\Microsoft\Windows NT\CurrentVersion\Setup\RecoveryConsole'; N = 'securitylevel'; Op = 'eq'; V = 0})}
    35 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Control\Lsa'; N = 'LimitBlankPasswordUse'; Op = 'eq'; V = 1})}
    36 = @{T = 'sec'; K = 'NewGuestName'; Op = 'ne'; V = 'Guest'}
    37 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Control\Print\Providers\LanMan Print Services\Servers'; N = 'AddPrinterDrivers'; Op = 'eq'; V = 1})}
    38 = @{T = 'reg'; R = @(@{P = 'HKLM\Software\Microsoft\Windows\CurrentVersion\Policies\System'; N = 'undockwithoutlogon'; Op = 'eq'; V = 0})}
    39 = @{T = 'reg'; R = @(@{P = 'HKLM\Software\Microsoft\Windows NT\CurrentVersion\Winlogon'; N = 'AllocateDASD'; Op = 'eq'; V = '0'})}
    40 = @{T = 'reg'; R = @(@{P = 'HKLM\Software\Microsoft\Windows NT\CurrentVersion\Winlogon'; N = 'AllocateCDRoms'; Op = 'eq'; V = '0'})}
    41 = @{T = 'reg'; R = @(@{P = 'HKLM\Software\Microsoft\Windows NT\CurrentVersion\Winlogon'; N = 'AllocateFloppies'; Op = 'eq'; V = '0'})}
    42 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Services\Netlogon\Parameters'; N = 'disablepasswordchange'; Op = 'eq'; V = 0})}
    43 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Services\Netlogon\Parameters'; N = 'maximumpasswordage'; Op = 'range'; V = @(1, 30)})}
    44 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Services\Netlogon\Parameters'; N = 'requiresignorseal'; Op = 'eq'; V = 1})}
    45 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Services\Netlogon\Parameters'; N = 'signsecurechannel'; Op = 'eq'; V = 1})}
    46 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Services\Netlogon\Parameters'; N = 'requirestrongkey'; Op = 'eq'; V = 1})}
    47 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Services\Netlogon\Parameters'; N = 'sealsecurechannel'; Op = 'eq'; V = 1})}
    48 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Control\Lsa'; N = 'EveryoneIncludesAnonymous'; Op = 'eq'; V = 0})}
    49 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Control\Lsa'; N = 'ForceGuest'; Op = 'eq'; V = 0})}
    50 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Control\Lsa'; N = 'RestrictAnonymousSAM'; Op = 'eq'; V = 1})}
    51 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Services\LanManServer\Parameters'; N = 'restrictnullsessaccess'; Op = 'eq'; V = 1})}
    52 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Control\SecurePipeServers\Winreg\AllowedPaths'; N = 'Machine'; Op = 'subset'; V = @('System\CurrentControlSet\Control\Print\Printers', 'System\CurrentControlSet\Services\Eventlog', 'Software\Microsoft\OLAP Server', 'Software\Microsoft\Windows NT\CurrentVersion\Print', 'Software\Microsoft\Windows NT\CurrentVersion\Windows', 'System\CurrentControlSet\Control\ContentIndex', 'System\CurrentControlSet\Control\Terminal Server', 'System\CurrentControlSet\Control\Terminal Server\UserConfig', 'System\CurrentControlSet\Control\Terminal Server\DefaultUserConfiguration', 'Software\Microsoft\Windows NT\CurrentVersion\Perflib', 'System\CurrentControlSet\Services\SysmonLog')})}
    53 = @{T = 'sec'; K = 'LSAAnonymousNameLookup'; Op = 'eq'; V = 0}
    54 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Services\LanManServer\Parameters'; N = 'NullSessionShares'; Op = 'empty'; V = $null})}
    55 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Services\LanManServer\Parameters'; N = 'NullSessionPipes'; Op = 'empty'; V = $null})}
    56 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Control\SecurePipeServers\Winreg\AllowedExactPaths'; N = 'Machine'; Op = 'subset'; V = @('System\CurrentControlSet\Control\ProductOptions', 'System\CurrentControlSet\Control\Server Applications', 'Software\Microsoft\Windows NT\CurrentVersion')})}
    57 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Control\Lsa'; N = 'DisableDomainCreds'; Op = 'eq'; V = 0})}
    58 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Control\Lsa'; N = 'NoLMHash'; Op = 'eq'; V = 1})}
    59 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Control\Lsa'; N = 'UseMachineId'; Op = 'eq'; V = 1})}
    60 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Control\Lsa'; N = 'LmCompatibilityLevel'; Op = 'eq'; V = 5})}
    61 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Control\Lsa\pku2u'; N = 'AllowOnlineID'; Op = 'eq'; V = 0})}
    62 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Control\Lsa\MSV1_0'; N = 'NTLMMinServerSec'; Op = 'eq'; V = 536870912})}
    63 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Control\Lsa\MSV1_0'; N = 'allownullsessionfallback'; Op = 'eq'; V = 0})}
    64 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Control\Lsa\MSV1_0'; N = 'NTLMMinClientSec'; Op = 'eq'; V = 537395200})}
    65 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Services\LDAP'; N = 'LDAPClientIntegrity'; Op = 'eq'; V = 1})}
    66 = @{T = 'sec'; K = 'ForceLogoffWhenHourExpire'; Op = 'eq'; V = 0}
    67 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Control\Lsa'; N = 'crashonauditfail'; Op = 'eq'; V = 0})}
    68 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Control\Lsa'; N = 'AuditBaseObjects'; Op = 'eq'; V = 0})}
    69 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Control\Lsa'; N = 'fullprivilegeauditing'; Op = 'zerobin'; V = $null})}
    70 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Control\Lsa'; N = 'scenoapplylegacyauditpolicy'; Op = 'eq'; V = 1})}
    71 = @{T = 'reg'; R = @(@{P = 'HKLM\Software\Microsoft\Windows\CurrentVersion\Policies\System'; N = 'ShutdownWithoutLogon'; Op = 'eq'; V = 0})}
    72 = @{T = 'reg'; R = @(@{P = 'HKLM\System\CurrentControlSet\Control\Session Manager\Memory Management'; N = 'ClearPageFileAtShutdown'; Op = 'eq'; V = 0})}
    73 = @{T = 'right'; K = 'SeShutdownPrivilege'; V = @('S-1-5-32-544'); IgnoreSvc = $false}
    74 = @{T = 'right'; K = 'SeIncreaseQuotaPrivilege'; V = @('S-1-5-32-544', 'S-1-5-19', 'S-1-5-20'); IgnoreSvc = $false}
    75 = @{T = 'right'; K = 'SeTakeOwnershipPrivilege'; V = @('S-1-5-32-544'); IgnoreSvc = $false}
    76 = @{T = 'right'; K = 'SeLoadDriverPrivilege'; V = @('S-1-5-32-544'); IgnoreSvc = $false}
    77 = @{T = 'right'; K = 'SeDenyServiceLogonRight'; V = @('S-1-5-32-546'); IgnoreSvc = $false}
    78 = @{T = 'right'; K = 'SeBackupPrivilege'; V = @('S-1-5-32-544'); IgnoreSvc = $false}
    79 = @{T = 'right'; K = 'SeSystemtimePrivilege'; V = @('S-1-5-19', 'S-1-5-32-544'); IgnoreSvc = $false}
    80 = @{T = 'right'; K = 'SeSystemEnvironmentPrivilege'; V = @('S-1-5-32-544'); IgnoreSvc = $false}
    81 = @{T = 'right'; K = 'SeCreateGlobalPrivilege'; V = @('S-1-5-32-544', 'S-1-5-6', 'S-1-5-19', 'S-1-5-20'); IgnoreSvc = $false}
    82 = @{T = 'right'; K = 'SeSystemProfilePrivilege'; V = @('S-1-5-32-544'); IgnoreSvc = $true}
    83 = @{T = 'right'; K = 'SeAuditPrivilege'; V = @('S-1-5-19', 'S-1-5-20'); IgnoreSvc = $false}
    84 = @{T = 'right'; K = 'SeTimeZonePrivilege'; V = @('S-1-5-19', 'S-1-5-32-544'); IgnoreSvc = $false}
    85 = @{T = 'right'; K = 'SeManageVolumePrivilege'; V = @('S-1-5-32-544'); IgnoreSvc = $false}
    86 = @{T = 'right'; K = 'SeRemoteShutdownPrivilege'; V = @('S-1-5-32-544'); IgnoreSvc = $false}
    87 = @{T = 'right'; K = 'SeCreateSymbolicLinkPrivilege'; V = @('S-1-5-32-544'); IgnoreSvc = $false}
    88 = @{T = 'right'; K = 'SeUndockPrivilege'; V = @('S-1-5-32-544'); IgnoreSvc = $false}
    89 = @{T = 'right'; K = 'SeCreatePagefilePrivilege'; V = @('S-1-5-32-544'); IgnoreSvc = $false}
    90 = @{T = 'right'; K = 'SeIncreaseBasePriorityPrivilege'; V = @('S-1-5-32-544'); IgnoreSvc = $false}
    91 = @{T = 'right'; K = 'SeSecurityPrivilege'; V = @('S-1-5-32-544'); IgnoreSvc = $false}
    92 = @{T = 'right'; K = 'SeProfileSingleProcessPrivilege'; V = @('S-1-5-32-544'); IgnoreSvc = $true}
    93 = @{T = 'right'; K = 'SeInteractiveLogonRight'; V = @('S-1-5-32-544', 'S-1-5-32-551', 'S-1-5-32-545'); IgnoreSvc = $false}
    94 = @{T = 'reg'; R = @(@{P = 'HKLM\Software\Policies\Microsoft\Windows\Installer'; N = 'AlwaysInstallElevated'; Op = 'eq'; V = 0})}
    95 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\Explorer'; N = 'NoAutoplayfornonVolume'; Op = 'eq'; V = 1})}
    96 = @{T = 'reg'; R = @(@{P = 'HKLM\Software\Policies\Microsoft\Windows NT\Terminal Services'; N = 'MinEncryptionLevel'; Op = 'ge'; V = 3})}
    97 = @{T = 'audit'; G = '{0CCE923E-69AE-11D9-BED3-505054503030}'; V = 0}
    98 = @{T = 'audit'; G = '{0CCE923D-69AE-11D9-BED3-505054503030}'; V = 0}
    99 = @{T = 'audit'; G = '{0CCE9212-69AE-11D9-BED3-505054503030}'; V = 3}
    100 = @{T = 'audit'; G = '{0CCE9213-69AE-11D9-BED3-505054503030}'; V = 3}
    101 = @{T = 'audit'; G = '{0CCE9211-69AE-11D9-BED3-505054503030}'; V = 3}
    102 = @{T = 'audit'; G = '{0CCE9214-69AE-11D9-BED3-505054503030}'; V = 3}
    103 = @{T = 'audit'; G = '{0CCE9210-69AE-11D9-BED3-505054503030}'; V = 3}
    104 = @{T = 'audit'; G = '{0CCE9221-69AE-11D9-BED3-505054503030}'; V = 3}
    105 = @{T = 'audit'; G = '{0CCE9224-69AE-11D9-BED3-505054503030}'; V = 0}
    106 = @{T = 'audit'; G = '{0CCE9227-69AE-11D9-BED3-505054503030}'; V = 0}
    107 = @{T = 'audit'; G = '{0CCE9222-69AE-11D9-BED3-505054503030}'; V = 0}
    108 = @{T = 'audit'; G = '{0CCE9220-69AE-11D9-BED3-505054503030}'; V = 0}
    109 = @{T = 'audit'; G = '{0CCE9226-69AE-11D9-BED3-505054503030}'; V = 0}
    110 = @{T = 'audit'; G = '{0CCE9223-69AE-11D9-BED3-505054503030}'; V = 0}
    111 = @{T = 'audit'; G = '{0CCE921E-69AE-11D9-BED3-505054503030}'; V = 3}
    112 = @{T = 'audit'; G = '{0CCE921F-69AE-11D9-BED3-505054503030}'; V = 0}
    113 = @{T = 'audit'; G = '{0CCE9233-69AE-11D9-BED3-505054503030}'; V = 0}
    114 = @{T = 'audit'; G = '{0CCE9230-69AE-11D9-BED3-505054503030}'; V = 1}
    115 = @{T = 'audit'; G = '{0CCE9231-69AE-11D9-BED3-505054503030}'; V = 1}
    116 = @{T = 'audit'; G = '{0CCE922F-69AE-11D9-BED3-505054503030}'; V = 3}
    117 = @{T = 'audit'; G = '{0CCE9232-69AE-11D9-BED3-505054503030}'; V = 3}
    118 = @{T = 'audit'; G = '{0CCE9234-69AE-11D9-BED3-505054503030}'; V = 2}
    119 = @{T = 'audit'; G = '{0CCE9228-69AE-11D9-BED3-505054503030}'; V = 3}
    120 = @{T = 'audit'; G = '{0CCE9229-69AE-11D9-BED3-505054503030}'; V = 0}
    121 = @{T = 'audit'; G = '{0CCE922A-69AE-11D9-BED3-505054503030}'; V = 0}
    122 = @{T = 'audit'; G = '{0CCE9242-69AE-11D9-BED3-505054503030}'; V = 3}
    123 = @{T = 'audit'; G = '{0CCE923F-69AE-11D9-BED3-505054503030}'; V = 3}
    124 = @{T = 'audit'; G = '{0CCE9240-69AE-11D9-BED3-505054503030}'; V = 3}
    125 = @{T = 'audit'; G = '{0CCE9241-69AE-11D9-BED3-505054503030}'; V = 3}
    126 = @{T = 'audit'; G = '{0CCE9239-69AE-11D9-BED3-505054503030}'; V = 0}
    127 = @{T = 'audit'; G = '{0CCE9236-69AE-11D9-BED3-505054503030}'; V = 3}
    128 = @{T = 'audit'; G = '{0CCE923A-69AE-11D9-BED3-505054503030}'; V = 3}
    129 = @{T = 'audit'; G = '{0CCE9237-69AE-11D9-BED3-505054503030}'; V = 3}
    130 = @{T = 'audit'; G = '{0CCE9238-69AE-11D9-BED3-505054503030}'; V = 1}
    131 = @{T = 'audit'; G = '{0CCE9235-69AE-11D9-BED3-505054503030}'; V = 3}
    132 = @{T = 'audit'; G = '{0CCE921A-69AE-11D9-BED3-505054503030}'; V = 0}
    133 = @{T = 'audit'; G = '{0CCE9243-69AE-11D9-BED3-505054503030}'; V = 1}
    134 = @{T = 'audit'; G = '{0CCE9218-69AE-11D9-BED3-505054503030}'; V = 0}
    135 = @{T = 'audit'; G = '{0CCE921C-69AE-11D9-BED3-505054503030}'; V = 3}
    136 = @{T = 'audit'; G = '{0CCE9216-69AE-11D9-BED3-505054503030}'; V = 3}
    137 = @{T = 'audit'; G = '{0CCE9215-69AE-11D9-BED3-505054503030}'; V = 3}
    138 = @{T = 'audit'; G = '{0CCE9219-69AE-11D9-BED3-505054503030}'; V = 0}
    139 = @{T = 'audit'; G = '{0CCE921B-69AE-11D9-BED3-505054503030}'; V = 1}
    140 = @{T = 'audit'; G = '{0CCE922E-69AE-11D9-BED3-505054503030}'; V = 0}
    141 = @{T = 'audit'; G = '{0CCE922D-69AE-11D9-BED3-505054503030}'; V = 0}
    142 = @{T = 'audit'; G = '{0CCE922B-69AE-11D9-BED3-505054503030}'; V = 1}
    143 = @{T = 'audit'; G = '{0CCE922C-69AE-11D9-BED3-505054503030}'; V = 3}
    144 = @{T = 'sec'; K = 'EnableGuestAccount'; Op = 'eq'; V = 0}
    145 = @{T = 'reg'; Any = $true; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\Skydrive'; N = 'DisableFileSync'; Op = 'eq'; V = 1}, @{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\OneDrive'; N = 'DisableFileSyncNGSC'; Op = 'eq'; V = 1})}
    146 = @{T = 'reg'; R = @(@{P = 'HKLM\Software\Policies\Microsoft\WindowsStore'; N = 'RemoveWindowsStore'; Op = 'eq'; V = 1})}
    147 = @{T = 'reg'; R = @(@{P = 'HKLM\Software\Policies\Microsoft\WindowsStore'; N = 'AutoDownload'; Op = 'eq'; V = 2})}
    148 = @{T = 'reg'; R = @(@{P = 'HKLM\Software\Policies\Microsoft\WindowsStore'; N = 'DisableOSUpgrade'; Op = 'eq'; V = 1})}
    149 = @{T = 'reg'; R = @(@{P = 'HKLM\Software\Microsoft\Windows\CurrentVersion\Policies\System\Audit'; N = 'ProcessCreationIncludeCmdLine_Enabled'; Op = 'eq'; V = 0})}
    150 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\Windows Search'; N = 'AllowIndexingEncryptedStoresOrItems'; Op = 'eq'; V = 0})}
    151 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\Windows Search'; N = 'ConnectedSearchPrivacy'; Op = 'eq'; V = 3})}
    152 = @{T = 'reg'; R = @(@{P = 'HKLM\Software\Microsoft\Windows\CurrentVersion\Policies\System'; N = 'MSAOptional'; Op = 'eq'; V = 1})}
    153 = @{T = 'reg'; R = @(@{P = 'HKLM\Software\Policies\Microsoft\Messenger\Client'; N = 'CEIP'; Op = 'eq'; V = 2})}
    154 = @{T = 'reg'; R = @(@{P = 'HKLM\Software\Policies\Microsoft\SearchCompanion'; N = 'DisableContentFileUpdates'; Op = 'eq'; V = 1})}
    155 = @{T = 'reg'; R = @(@{P = 'HKLM\Software\Policies\Microsoft\Windows\Personalization'; N = 'NoLockScreenSlideshow'; Op = 'eq'; V = 1})}
    156 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows Defender\SpyNet'; N = 'SpyNetReporting'; Op = 'eq'; V = 0})}
    157 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\Installer'; N = 'EnableUserControl'; Op = 'eq'; V = 0})}
    158 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\Installer'; N = 'SafeForScripting'; Op = 'eq'; V = 0})}
    159 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\WinRM\Client'; N = 'AllowUnencryptedTraffic'; Op = 'eq'; V = 0})}
    160 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\WinRM\Client'; N = 'AllowBasic'; Op = 'eq'; V = 0})}
    161 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\WinRM\Service'; N = 'AllowUnencryptedTraffic'; Op = 'eq'; V = 0})}
    162 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\WinRM\Service'; N = 'AllowBasic'; Op = 'eq'; V = 0})}
    163 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer'; N = 'NoAutorun'; Op = 'eq'; V = 1})}
    164 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\LocationAndSensors'; N = 'DisableLocation'; Op = 'eq'; V = 1})}
    165 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\CredUI'; N = 'DisablePasswordReveal'; Op = 'eq'; V = 1})}
    166 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\CredUI'; N = 'EnumerateAdministrators'; Op = 'eq'; V = 0})}
    167 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services'; N = 'fEncryptRPCTraffic'; Op = 'eq'; V = 1})}
    168 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services'; N = 'PerSessionTempDir'; Op = 'eq'; V = 1})}
    169 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services'; N = 'DeleteTempDirsOnExit'; Op = 'eq'; V = 1})}
    170 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services'; N = 'DisablePasswordSaving'; Op = 'eq'; V = 1})}
    171 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\Explorer'; N = 'NoHeapTerminationOnCorruption'; Op = 'eq'; V = 0})}
    172 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer'; N = 'PreXPSP2ShellProtocolBehavior'; Op = 'eq'; V = 0})}
    173 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\Explorer'; N = 'NoDataExecutionPrevention'; Op = 'eq'; V = 0})}
    174 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\System'; N = 'EnumerateLocalUsers'; Op = 'eq'; V = 0})}
    175 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\System'; N = 'DisableLockScreenAppNotifications'; Op = 'eq'; V = 1})}
    176 = @{T = 'reg'; R = @(@{P = 'HKLM\SYSTEM\CurrentControlSet\Policies\EarlyLaunch'; N = 'DriverLoadPolicy'; Op = 'eq'; V = 1})}
    177 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Power\PowerSettings\0e796bdb-100d-47d6-a2d5-f7d2daa51f51'; N = 'ACSettingIndex'; Op = 'eq'; V = 1})}
    178 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Power\PowerSettings\0e796bdb-100d-47d6-a2d5-f7d2daa51f51'; N = 'DCSettingIndex'; Op = 'eq'; V = 1})}
    179 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\ScriptedDiagnosticsProvider\Policy'; N = 'DisableQueryRemoteServer'; Op = 'eq'; V = 0})}
    180 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\WDI\{9c5a40da-b965-4fc3-8781-88dd50a6299d}'; N = 'ScenarioExecutionEnabled'; Op = 'eq'; V = 0})}
    181 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Printers'; N = 'DisableHTTPPrinting'; Op = 'eq'; V = 1})}
    182 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\SQMClient\Windows'; N = 'CEIPEnable'; Op = 'eq'; V = 0})}
    183 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\HandwritingErrorReports'; N = 'PreventHandwritingErrorReports'; Op = 'eq'; V = 1})}
    184 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\Explorer'; N = 'NoUseStoreOpenWith'; Op = 'eq'; V = 1})}
    185 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Printers'; N = 'DisableWebPnPDownload'; Op = 'eq'; V = 1})}
    186 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services'; N = 'fAllowUnsolicited'; Op = 'eq'; V = 0})}
    187 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services'; N = 'fAllowToGetHelp'; Op = 'eq'; V = 0})}
    188 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Peernet'; N = 'Disabled'; Op = 'eq'; V = 1})}
    189 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\WCN\Registrars'; N = 'EnableRegistrars'; Op = 'eq'; V = 0}, @{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\WCN\Registrars'; N = 'DisableUPnPRegistrar'; Op = 'eq'; V = 0}, @{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\WCN\Registrars'; N = 'DisableInBand802DOT11Registrar'; Op = 'eq'; V = 0}, @{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\WCN\Registrars'; N = 'DisableFlashConfigRegistrar'; Op = 'eq'; V = 0}, @{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\WCN\Registrars'; N = 'DisableWPDRegistrar'; Op = 'eq'; V = 0})}
    190 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\WCN\UI'; N = 'DisableWcnUi'; Op = 'eq'; V = 1})}
    191 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\Network Connections'; N = 'NC_StdDomainUserSetLocation'; Op = 'eq'; V = 1})}
    192 = @{T = 'reg'; R = @(@{P = 'HKLM\SYSTEM\CurrentControlSet\Services\Netlogon\Parameters'; N = 'VulnerableChannelAllowList'; Op = 'empty'; V = $null})}
    193 = @{T = 'audit'; G = '{0CCE9245-69AE-11D9-BED3-505054503030}'; V = 3}
    194 = @{T = 'audit'; G = '{0CCE9249-69AE-11D9-BED3-505054503030}'; V = 1}
    195 = @{T = 'audit'; G = '{0CCE9248-69AE-11D9-BED3-505054503030}'; V = 1}
    196 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'; N = 'NoConnectedUser'; Op = 'eq'; V = 3})}
    197 = @{T = 'fw'; P = 'Public'; K = 'LogMaxSizeKilobytes'; Op = 'ge'; V = 16384}
    198 = @{T = 'fw'; P = 'Public'; K = 'LogFileName'; Op = 'path'; V = '%SystemRoot%\System32\logfiles\firewall\publicfw.log'}
    199 = @{T = 'fw'; P = 'Public'; K = 'LogBlocked'; Op = 'true'}
    200 = @{T = 'fw'; P = 'Public'; K = 'LogAllowed'; Op = 'true'}
    201 = @{T = 'fw'; P = 'Private'; K = 'LogMaxSizeKilobytes'; Op = 'ge'; V = 16384}
    202 = @{T = 'fw'; P = 'Private'; K = 'LogFileName'; Op = 'path'; V = '%SystemRoot%\System32\logfiles\firewall\privatefw.log'}
    203 = @{T = 'fw'; P = 'Private'; K = 'LogBlocked'; Op = 'true'}
    204 = @{T = 'fw'; P = 'Private'; K = 'LogAllowed'; Op = 'true'}
    205 = @{T = 'fw'; P = 'Domain'; K = 'LogMaxSizeKilobytes'; Op = 'ge'; V = 16384}
    206 = @{T = 'fw'; P = 'Domain'; K = 'LogFileName'; Op = 'path'; V = '%SystemRoot%\System32\logfiles\firewall\domainfw.log'}
    207 = @{T = 'fw'; P = 'Domain'; K = 'LogBlocked'; Op = 'true'}
    208 = @{T = 'fw'; P = 'Domain'; K = 'LogAllowed'; Op = 'true'}
    209 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\CurrentVersion\PushNotifications'; N = 'NoCloudApplicationNotification'; Op = 'eq'; V = 1})}
    210 = @{T = 'reg'; R = @(@{P = 'HKLM\SYSTEM\CurrentControlSet\Services\NetBT\Parameters'; N = 'NodeType'; Op = 'eq'; V = 2})}
    211 = @{T = 'reg'; R = @(@{P = 'HKLM\SYSTEM\CurrentControlSet\Control\SecurityProviders\WDigest'; N = 'UseLogonCredential'; Op = 'eq'; V = 0})}
    212 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'; N = 'LocalAccountTokenFilterPolicy'; Op = 'eq'; V = 0})}
    213 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\Installer'; N = 'DisableLUAPatching'; Op = 'eq'; V = 1})}
    214 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\WindowsMediaPlayer'; N = 'DisableAutoUpdate'; Op = 'eq'; V = 1})}
    215 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\PowerShell\ScriptBlockLogging'; N = 'EnableScriptBlockLogging'; Op = 'eq'; V = 0})}
    216 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\PowerShell\Transcription'; N = 'EnableTranscripting'; Op = 'eq'; V = 0})}
    217 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU'; N = 'NoAutoRebootWithLoggedOnUsers'; Op = 'eq'; V = 0})}
    218 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate'; N = 'DeferQualityUpdates'; Op = 'eq'; V = 1}, @{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate'; N = 'DeferQualityUpdatesPeriodInDays'; Op = 'eq'; V = 0})}
    219 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate'; N = 'DeferFeatureUpdates'; Op = 'eq'; V = 1}, @{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate'; N = 'DeferFeatureUpdatesPeriodInDays'; Op = 'ge'; V = 180})}
    220 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows Defender Security Center\App and Browser protection'; N = 'DisallowExploitProtectionOverride'; Op = 'eq'; V = 1})}
    221 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\EventLog\Setup'; N = 'Retention'; Op = 'eq'; V = '0'})}
    222 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\EventLog\Setup'; N = 'MaxSize'; Op = 'ge'; V = 32768})}
    223 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows NT\CurrentVersion\Software Protection Platform'; N = 'NoGenTicket'; Op = 'eq'; V = 1})}
    224 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\DataCollection'; N = 'DoNotShowFeedbackNotifications'; Op = 'eq'; V = 1})}
    225 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\PreviewBuilds'; N = 'AllowBuildPreview'; Op = 'eq'; V = 0})}
    226 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\DataCollection'; N = 'DisableEnterpriseAuthProxy'; Op = 'eq'; V = 1})}
    227 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services'; N = 'fSingleSessionPerUser'; Op = 'eq'; V = 1})}
    228 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services'; N = 'fDisableCcm'; Op = 'eq'; V = 1})}
    229 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services'; N = 'fDisableLPT'; Op = 'eq'; V = 1})}
    230 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services'; N = 'fDisablePNPRedir'; Op = 'eq'; V = 1})}
    231 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services'; N = 'fEnableSmartCard'; Op = 'eq'; V = 1})}
    232 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\CurrentVersion\AppModel\StateManager'; N = 'AllowSharedLocalAppData'; Op = 'eq'; V = 0})}
    233 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\Appx'; N = 'AllowAllTrustedApps'; Op = 'eq'; V = 1})}
    234 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\W32Time\TimeProviders\NtpClient'; N = 'Enabled'; Op = 'eq'; V = 1})}
    235 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\W32Time\TimeProviders\NtpServer'; N = 'Enabled'; Op = 'eq'; V = 0})}
    236 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Control Panel\International'; N = 'BlockUserInputMethodsForSignIn'; Op = 'eq'; V = 1})}
    237 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Servicing'; N = 'UseWindowsUpdate'; Op = 'eq'; V = 2})}
    238 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\System'; N = 'DontEnumerateConnectedUsers'; Op = 'eq'; V = 1})}
    239 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\System'; N = 'BlockUserFromShowingAccountDetailsOnSignin'; Op = 'eq'; V = 1})}
    240 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\System'; N = 'AllowDomainPINLogon'; Op = 'eq'; V = 0})}
    241 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\System'; N = 'BlockDomainPicturePassword'; Op = 'eq'; V = 1})}
    242 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\Group Policy\{35378EAC-683F-11D2-A89A-00C04FBBCFA2}'; N = 'NoBackgroundPolicy'; Op = 'eq'; V = 0})}
    243 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\Group Policy\{35378EAC-683F-11D2-A89A-00C04FBBCFA2}'; N = 'NoGPOListChanges'; Op = 'eq'; V = 0})}
    244 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'; N = 'DisableBkGndGroupPolicy'; Op = 'absent0'; V = $null})}
    245 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\DeviceInstall\Settings'; N = 'AllowRemoteRPC'; Op = 'eq'; V = 0})}
    246 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\DeviceInstall\Settings'; N = 'DisableSystemRestore'; Op = 'eq'; V = 0})}
    247 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\Device Metadata'; N = 'PreventDeviceMetadataFromNetwork'; Op = 'eq'; V = 1})}
    248 = @{T = 'todo'; Why = '「搜尋受管理伺服器」對應的登錄值尚未核對'}
    249 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\DriverSearching'; N = 'SearchOrderConfig'; Op = 'eq'; V = 0})}
    250 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\DeviceInstall\Settings'; N = 'DisableSendGenericDriverNotFoundToWER'; Op = 'eq'; V = 1})}
    251 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\DeviceInstall\Settings'; N = 'DisableSendRequestAdditionalSoftwareToWER'; Op = 'eq'; V = 1})}
    252 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\DriverSearching'; N = 'DontSearchWindowsUpdate'; Op = 'eq'; V = 1})}
    253 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\Windows Error Reporting'; N = 'Disabled'; Op = 'eq'; V = 1})}
    254 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\EventViewer'; N = 'MicrosoftEventVwrDisableLinks'; Op = 'eq'; V = 1})}
    255 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\TabletPC'; N = 'PreventHandwritingDataSharing'; Op = 'eq'; V = 1})}
    256 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer'; N = 'NoInternetOpenWith'; Op = 'eq'; V = 1})}
    257 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services'; N = 'LoggingEnabled'; Op = 'eq'; V = 1})}
    258 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Rpc'; N = 'RestrictRemoteClients'; Op = 'eq'; V = 1})}
    259 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Rpc'; N = 'EnableAuthEpResolution'; Op = 'eq'; V = 1})}
    260 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\DriverSearching'; N = 'DontPromptForWindowsUpdate'; Op = 'eq'; V = 1})}
    261 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer'; N = 'AllowOnlineTips'; Op = 'eq'; V = 0})}
    262 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows NT\DNSClient'; N = 'EnableMulticast'; Op = 'eq'; V = 0})}
    263 = @{T = 'reg'; R = @(@{P = 'HKLM\SYSTEM\CurrentControlSet\Services\Tcpip6\Parameters'; N = 'DisabledComponents'; Op = 'eq'; V = 255})}
    264 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\TCPIP\v6Transition\IPHTTPS\IPHTTPSInterface'; N = 'IPHTTPS_ClientState'; Op = 'eq'; V = 3})}
    265 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\TCPIP\v6Transition'; N = '6to4_State'; Op = 'eq'; V = 'Disabled'})}
    266 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\TCPIP\v6Transition'; N = 'ISATAP_State'; Op = 'eq'; V = 'Disabled'})}
    267 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\TCPIP\v6Transition'; N = 'Teredo_State'; Op = 'eq'; V = 'Disabled'})}
    268 = @{T = 'todo'; Why = '「IP 無狀態自動設定限制狀態」對應的登錄值尚未核對'}
    269 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\System'; N = 'EnableFontProviders'; Op = 'eq'; V = 0})}
    270 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\Network Connections'; N = 'NC_ShowSharedAccessUI'; Op = 'eq'; V = 0})}
    271 = @{T = 'reg'; R = @(@{P = 'HKCU\Software\Microsoft\Windows\CurrentVersion\Policies\Attachments'; N = 'HideZoneInfoOnProperties'; Op = 'eq'; V = 1})}
    272 = @{T = 'reg'; R = @(@{P = 'HKCU\Software\Policies\Microsoft\Assistance\Client\1.0'; N = 'NoExplicitFeedback'; Op = 'eq'; V = 1})}
    273 = @{T = 'reg'; R = @(@{P = 'HKCU\Software\Policies\Microsoft\Assistance\Client\1.0'; N = 'NoImplicitFeedback'; Op = 'eq'; V = 1})}
    274 = @{T = 'reg'; R = @(@{P = 'HKCU\Software\Policies\Microsoft\Windows\Control Panel\Desktop'; N = 'ScreenSaveTimeOut'; Op = 'range'; V = @(1, 900)})}
    275 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'; N = 'ConsentPromptBehaviorAdmin'; Op = 'eq'; V = 2})}
    276 = @{T = 'audit'; G = '{0CCE9217-69AE-11D9-BED3-505054503030}'; V = 3}
    277 = @{T = 'reg'; R = @(@{P = 'HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\kernel'; N = 'DisableExceptionChainValidation'; Op = 'eq'; V = 0})}
    278 = @{T = 'reg'; R = @(@{P = 'HKLM\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters'; N = 'EnableICMPRedirect'; Op = 'eq'; V = 0})}
    279 = @{T = 'reg'; R = @(@{P = 'HKLM\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters'; N = 'PerformRouterDiscovery'; Op = 'eq'; V = 0})}
    280 = @{T = 'reg'; R = @(@{P = 'HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System\CredSSP\Parameters'; N = 'AllowEncryptionOracle'; Op = 'eq'; V = 0})}
    281 = @{T = 'svc'; K = 'upnphost'}
    282 = @{T = 'svc'; K = 'Browser'}
    283 = @{T = 'svc'; K = 'SCardSvr'}
    284 = @{T = 'svc'; K = 'SharedAccess'}
    285 = @{T = 'svc'; K = 'SSDPSRV'}
    286 = @{T = 'svc'; K = 'dmwappushservice'}
}

# ---------------------------------------------------------------------------
# 3. 判定用的共用函式
# ---------------------------------------------------------------------------
# 注意：PowerShell 變數不分大小寫，常數不可與函式內的 $ok 同名
$ST_OK = 'Compliant'; $ST_NG = 'Non-Compliant'; $ST_NA = 'Not-Applicable'; $ST_ER = 'Error'

function Format-Val($v) {
    if ($null -eq $v) { return '(無)' }
    if ($v -is [byte[]]) { return (($v | ForEach-Object { $_.ToString('X2') }) -join ' ') }
    if ($v -is [array]) { if ($v.Count -eq 0) { return '(空白)' }; return ($v -join ', ') }
    $s = [string]$v
    if ($s -eq '') { return '(空白)' }
    return $s
}

function Describe-Expect([string]$op, $v) {
    switch ($op) {
        'eq'      { return "= $(Format-Val $v)" }
        'ne'      { return "≠ $(Format-Val $v)" }
        'ge'      { return "≥ $v" }
        'le'      { return "≤ $v" }
        'range'   { return "介於 $($v[0])～$($v[1])" }
        'empty'   { return "空白或不存在" }
        'absent0' { return "不存在或 0" }
        'subset'  { return "不超出基準清單" }
        'zerobin' { return "全為 00" }
        'true'    { return "是" }
        'path'    { return "= $v" }
    }
    return "$op $v"
}

function Test-Num($a, [ref]$n) {
    $x = 0L
    if ([int64]::TryParse(([string]$a).Trim(), [ref]$x)) { $n.Value = $x; return $true }
    return $false
}

# 回傳 $true / $false；值的型別對不上（例如基準是數字、實際是文字）回 $null＝無法判定
function Test-Expect([string]$op, $actual, $exp) {
    $n = 0L
    switch ($op) {
        'eq' {
            if ($exp -is [int] -or $exp -is [long]) {
                if (-not (Test-Num $actual ([ref]$n))) { return $null }
                return ($n -eq [int64]$exp)
            }
            return (([string]$actual).Trim() -ieq ([string]$exp).Trim())
        }
        'ne'    { return (([string]$actual).Trim().Trim('"') -ine ([string]$exp)) }
        'ge'    { if (-not (Test-Num $actual ([ref]$n))) { return $null }; return ($n -ge [int64]$exp) }
        'le'    { if (-not (Test-Num $actual ([ref]$n))) { return $null }; return ($n -le [int64]$exp) }
        'range' { if (-not (Test-Num $actual ([ref]$n))) { return $null }; return ($n -ge [int64]$exp[0] -and $n -le [int64]$exp[1]) }
        'empty' {
            $items = @($actual | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
            return ($items.Count -eq 0)
        }
        'absent0' { if (-not (Test-Num $actual ([ref]$n))) { return $null }; return ($n -eq 0) }
        'subset' {
            $allowed = @($exp | ForEach-Object { $_.ToLower() })
            foreach ($a in @($actual)) {
                if ([string]::IsNullOrWhiteSpace([string]$a)) { continue }
                if ($allowed -notcontains ([string]$a).Trim().ToLower()) { return $false }
            }
            return $true
        }
        'zerobin' {
            if ($actual -is [array]) { return (@($actual | Where-Object { [int64]$_ -ne 0 }).Count -eq 0) }
            if (Test-Num $actual ([ref]$n)) { return ($n -eq 0) }
            return $null
        }
        'true'  { return ([string]$actual -ieq 'True') }
        'path'  {
            $x = [Environment]::ExpandEnvironmentVariables([string]$actual)
            $y = [Environment]::ExpandEnvironmentVariables([string]$exp)
            return ($x -ieq $y)
        }
    }
    return $null
}

# 讀登錄值：State = ok / absent / error
function Read-Reg([string]$path, [string]$name) {
    $psPath = $path -replace '^HKLM\\', 'HKLM:\' -replace '^HKCU\\', 'HKCU:\'
    try {
        if (-not (Test-Path -LiteralPath $psPath)) { return @{ State = 'absent' } }
        $key = Get-Item -LiteralPath $psPath -ErrorAction Stop
        if (@($key.GetValueNames()) -notcontains $name) { return @{ State = 'absent' } }
        $v = $key.GetValue($name, $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
        return @{ State = 'ok'; Value = $v }
    } catch {
        return @{ State = 'error'; Why = $_.Exception.Message }
    }
}

function Sid-Name([string]$sid) {
    try { return (New-Object System.Security.Principal.SecurityIdentifier($sid)).Translate([System.Security.Principal.NTAccount]).Value }
    catch { return $sid }
}

function Name-Sid([string]$name) {
    try { return (New-Object System.Security.Principal.NTAccount($name)).Translate([System.Security.Principal.SecurityIdentifier]).Value }
    catch { return $name }
}

function Audit-Flags([string]$t) {
    if ($t -match 'No Auditing|沒有稽核|無稽核|不稽核') { return 0 }
    $f = 0
    if ($t -match 'Success|成功') { $f = $f -bor 1 }
    if ($t -match 'Failure|失敗') { $f = $f -bor 2 }
    if ($f -eq 0) { return $null }
    return $f
}
$AuditText = @{ 0 = '沒有稽核'; 1 = '成功'; 2 = '失敗'; 3 = '成功與失敗' }

# 單一判定 → @{ S = 結果; C = 目前值文字 }
function New-Result([string]$s, [string]$c) { return @{ S = $s; C = $c } }

function Check-Sec($c) {
    if ($SecError) { return New-Result $ST_ER "查不到: ($SecError)" }
    if (-not $SecContent.ContainsKey($c.K)) { return New-Result $ST_NG "未符合: ($($c.K) 未設定；基準 $(Describe-Expect $c.Op $c.V))" }
    $v = $SecContent[$c.K]
    $ok = Test-Expect $c.Op $v $c.V
    if ($null -eq $ok) { return New-Result $ST_ER "查不到: ($($c.K) = $v，值無法判讀)" }
    if ($ok) { return New-Result $ST_OK "無異常: ($($c.K) = $v)" }
    return New-Result $ST_NG "未符合: ($($c.K) = $v；基準 $(Describe-Expect $c.Op $c.V))"
}

function Check-Right($c) {
    if ($SecError) { return New-Result $ST_ER "查不到: ($SecError)" }
    $raw = if ($SecContent.ContainsKey($c.K)) { $SecContent[$c.K] } else { '' }
    $have = @($raw -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ } | ForEach-Object {
        if ($_.StartsWith('*')) { $_.Substring(1) } else { Name-Sid $_ } })
    $cmp = $have
    if ($c.IgnoreSvc) { $cmp = @($have | Where-Object { $_ -notlike 'S-1-5-80-*' }) }
    $want = @($c.V)
    $missing = @($want | Where-Object { $cmp -notcontains $_ })
    $extra = @($cmp | Where-Object { $want -notcontains $_ })
    $shown = if ($have.Count) { (($have | ForEach-Object { Sid-Name $_ }) -join ', ') } else { '(無人)' }
    if ($missing.Count -eq 0 -and $extra.Count -eq 0) { return New-Result $ST_OK "無異常: ($($c.K) = $shown)" }
    $diff = @()
    if ($extra.Count) { $diff += "多出 " + (($extra | ForEach-Object { Sid-Name $_ }) -join ', ') }
    if ($missing.Count) { $diff += "缺少 " + (($missing | ForEach-Object { Sid-Name $_ }) -join ', ') }
    return New-Result $ST_NG "未符合: ($($c.K) = $shown；$($diff -join '；'))"
}

function Check-Audit($c) {
    if ($AuditError) { return New-Result $ST_ER "查不到: ($AuditError)" }
    $row = $AuditMap[$c.G.ToUpper()]
    if (-not $row) { return New-Result $ST_ER "查不到: (auditpol 沒有回傳子類別 $($c.G))" }
    $f = Audit-Flags $row.Inc
    if ($null -eq $f) { return New-Result $ST_ER "查不到: ($($row.Sub) = $($row.Inc)，無法判讀)" }
    $want = [int]$c.V
    $ok = if ($AuditCompare -eq 'AtLeast') { (($f -band $want) -eq $want) } else { ($f -eq $want) }
    $txt = "$($row.Sub) = $($AuditText[$f])"
    if ($ok) { return New-Result $ST_OK "無異常: ($txt)" }
    return New-Result $ST_NG "未符合: ($txt；基準 $($AuditText[$want]))"
}

function Check-Svc($c) {
    try {
        $s = Get-CimInstance Win32_Service -Filter "Name='$($c.K)'" -ErrorAction Stop
    } catch { return New-Result $ST_ER "查不到: (服務查詢失敗：$($_.Exception.Message))" }
    if (-not $s) { return New-Result $ST_NA "不適用: (服務 $($c.K) 未安裝)" }
    $txt = "服務: $($s.Name), 狀態: $($s.State), 啟動類型: $($s.StartMode)"
    if ($s.StartMode -eq 'Disabled' -and $s.State -ne 'Running') { return New-Result $ST_OK "無異常: ($txt)" }
    return New-Result $ST_NG "未符合: ($txt；基準 停用)"
}

function Check-Fw($c) {
    if ($FwError) { return New-Result $ST_ER "查不到: ($FwError)" }
    $p = $FwProfiles[$c.P]
    if (-not $p) { return New-Result $ST_ER "查不到: (找不到 $($c.P) 設定檔)" }
    $v = $p.($c.K)
    $ok = Test-Expect $c.Op $v $c.V
    $txt = "$($c.P) Profile: $($c.K) = $(Format-Val $v)"
    if ($null -eq $ok) { return New-Result $ST_ER "查不到: ($txt，值無法判讀)" }
    if ($ok) { return New-Result $ST_OK "無異常: ($txt)" }
    return New-Result $ST_NG "未符合: ($txt；基準 $(Describe-Expect $c.Op $c.V))"
}

function Check-Reg($c) {
    $parts = @(); $nOK = 0; $nNG = 0; $nER = 0
    foreach ($s in @($c.R)) {
        $r = Read-Reg $s.P $s.N
        if ($r.State -eq 'error') { $nER++; $parts += "$($s.N) 讀取失敗：$($r.Why)"; continue }
        if ($r.State -eq 'absent') {
            if ($s.Op -eq 'empty' -or $s.Op -eq 'absent0') { $nOK++; $parts += "$($s.N) 不存在"; continue }
            $nNG++; $parts += "$($s.N) 未設定；基準 $(Describe-Expect $s.Op $s.V)"; continue
        }
        $ok = Test-Expect $s.Op $r.Value $s.V
        $v = Format-Val $r.Value
        if ($null -eq $ok) { $nER++; $parts += "$($s.N) = $v，值無法判讀" }
        elseif ($ok) { $nOK++; $parts += "$($s.N) = $v" }
        else { $nNG++; $parts += "$($s.N) = $v；基準 $(Describe-Expect $s.Op $s.V)" }
    }
    $txt = $parts -join ' / '
    if (@($c.R | Where-Object { $_.P -like 'HKCU\*' }).Count) { $txt += "（以執行帳號 $($Identity.Name) 的使用者設定判定）" }
    if ($c.Any) {
        if ($nOK -gt 0) { return New-Result $ST_OK "無異常: ($txt)" }
        if ($nER -gt 0) { return New-Result $ST_ER "查不到: ($txt)" }
        return New-Result $ST_NG "未符合: ($txt)"
    }
    if ($nNG -gt 0) { return New-Result $ST_NG "未符合: ($txt)" }
    if ($nER -gt 0) { return New-Result $ST_ER "查不到: ($txt)" }
    return New-Result $ST_OK "無異常: ($txt)"
}

# ---------------------------------------------------------------------------
# 4. 逐條判定
# ---------------------------------------------------------------------------
Write-Host "`n[*] 開始逐條檢核 $($AllRules.Count) 條..." -ForegroundColor Yellow
$AuditResults = [System.Collections.Generic.List[PSObject]]::new()
$CurrentIndex = 0
foreach ($rule in $AllRules) {
    $CurrentIndex++
    if ($CurrentIndex % 25 -eq 0 -or $CurrentIndex -eq $AllRules.Count) {
        Write-Progress -Activity "Windows 組態安全配置檢核進行中" -Status "已檢核 $CurrentIndex / $($AllRules.Count)" `
            -PercentComplete (($CurrentIndex / $AllRules.Count) * 100)
    }
    $c = $Checks[[int]$rule.Index]
    try {
        if (-not $c) { $res = New-Result $ST_ER "查不到: (這一條沒有定義判定方式)" }
        else {
            switch ($c.T) {
                'sec'   { $res = Check-Sec $c }
                'right' { $res = Check-Right $c }
                'audit' { $res = Check-Audit $c }
                'svc'   { $res = Check-Svc $c }
                'fw'    { $res = Check-Fw $c }
                'reg'   { $res = Check-Reg $c }
                'todo'  { $res = New-Result $ST_ER "查不到: (判定方式待核對：$($c.Why))" }
                default { $res = New-Result $ST_ER "查不到: (未知的判定類型 $($c.T))" }
            }
        }
    } catch {
        $res = New-Result $ST_ER "查不到: (檢核時發生錯誤：$($_.Exception.Message))"
    }
    $AuditResults.Add([PSCustomObject]@{
        "項次"         = $rule.Index
        "TWGCB-ID"     = $rule.ID
        "檢查結果"     = $res.S
        "類別"         = $rule.Category
        "原則設定名稱" = $rule.Name
        "GPO設定路徑"  = $rule.GPO
        "標準設定值"   = $rule.Standard
        "Registry路徑" = $rule.RegPath
        "目前值"       = $res.C
        "備註"         = $rule.Note
    })
}
Write-Progress -Activity "Windows 組態安全配置檢核進行中" -Completed
$EndTime = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
$Cnt = @{}
foreach ($k in @($ST_OK, $ST_NG, $ST_NA, $ST_ER)) { $Cnt[$k] = @($AuditResults | Where-Object { $_."檢查結果" -eq $k }).Count }

# ---------------------------------------------------------------------------
# 5. 產出報告（FCB_主機名_IP.txt / .html）
# ---------------------------------------------------------------------------
$BaseFileName = "FCB_${ComputerName}_${IPAddress}"
$TxtPath = Join-Path $OutputDir "$BaseFileName.txt"
$HtmlPath = Join-Path $OutputDir "$BaseFileName.html"
$SummaryLine = "Check summary: 合計 $($AuditResults.Count) 項；Compliant $($Cnt[$ST_OK])；Non-Compliant $($Cnt[$ST_NG])；Not-Applicable $($Cnt[$ST_NA])；Error $($Cnt[$ST_ER])"

function Clean([string]$s) { return ($s -replace ';', '，' -replace '[\r\n]+', ' ').Trim() }

$txtSb = [System.Text.StringBuilder]::new()
[void]$txtSb.AppendLine("系統組態檢查")
[void]$txtSb.AppendLine("平台: Windows")
[void]$txtSb.AppendLine("主機名: $ComputerName")
[void]$txtSb.AppendLine("IP地址: $IPAddress")
[void]$txtSb.AppendLine("作業系統版本: $OSName ($OSVersion)")
[void]$txtSb.AppendLine("核心版本: $KernelVersion")
[void]$txtSb.AppendLine("腳本版本: $ScriptVersion")
[void]$txtSb.AppendLine("檢核基準: $BaselineName")
[void]$txtSb.AppendLine("Check stated at: $CheckTime")
[void]$txtSb.AppendLine("Check runas: $RunAs")
[void]$txtSb.AppendLine("TWGCB-ID;角色;檢查結果;類別;原則設定名稱;標準設定值;目前值")
foreach ($item in $AuditResults) {
    $id = ($item."TWGCB-ID" -replace '[\r\n]+', ' ').Trim()
    [void]$txtSb.AppendLine("$id;Common;$($item."檢查結果");$(Clean $item."類別");$(Clean $item."原則設定名稱");$(Clean $item."標準設定值");$(Clean $item."目前值")")
}
[void]$txtSb.AppendLine("Check ended at: $EndTime")
[void]$txtSb.AppendLine($SummaryLine)
# TXT 以 UTF-8（含 BOM）寫出：任何語系的 Windows 都不會把中文寫成問號；8-2 匯入認得 UTF-8。
[System.IO.File]::WriteAllText($TxtPath, $txtSb.ToString(), (New-Object System.Text.UTF8Encoding($true)))

$E = { param($s) [System.Security.SecurityElement]::Escape([string]$s) }
$cls = @{ $ST_OK = 'st-ok'; $ST_NG = 'st-ng'; $ST_NA = 'st-na'; $ST_ER = 'st-er' }
$label = @{ $ST_OK = '符合'; $ST_NG = '不符合'; $ST_NA = '不適用'; $ST_ER = '查不到' }
$denom = $Cnt[$ST_OK] + $Cnt[$ST_NG]
$rate = if ($denom -gt 0) { "{0:N1}%" -f ($Cnt[$ST_OK] * 100.0 / $denom) } else { '無法計算' }

$h = [System.Text.StringBuilder]::new()
[void]$h.AppendLine("<!DOCTYPE html><html><head><meta charset='utf-8'>")
[void]$h.AppendLine("<title>Windows 組態安全配置檢核報告 - $(& $E $ComputerName)</title><style>")
[void]$h.AppendLine("body{font-family:'Microsoft JhengHei','Segoe UI',Tahoma,sans-serif;margin:20px;background:#fcfcfc;color:#333}")
[void]$h.AppendLine("h1{color:#004d80}.info-card{background:#f8f9fa;border:1px solid #dee2e6;border-radius:6px;padding:15px 20px;margin-bottom:20px}")
[void]$h.AppendLine(".info-card p{margin:6px 0;font-size:14px}.sum span{display:inline-block;margin-right:18px;font-weight:bold}")
[void]$h.AppendLine("table{border-collapse:collapse;width:100%;font-size:13px;background:#fff}")
[void]$h.AppendLine("th,td{border:1px solid #d0d7de;padding:8px 10px;text-align:left;vertical-align:top;word-break:break-word}")
[void]$h.AppendLine("th{background:#004d80;color:#fff}tr:nth-child(even){background:#f8fafd}")
[void]$h.AppendLine(".st-ok{color:#28a745;font-weight:bold}.st-ng{color:#dc3545;font-weight:bold}.st-na{color:#6c757d;font-weight:bold}.st-er{color:#b8860b;font-weight:bold}")
[void]$h.AppendLine(".idx-col{text-align:center;font-weight:bold;color:#555}.gpo-path{font-family:Consolas,monospace;font-size:12px;color:#555}")
[void]$h.AppendLine(".reg-path{font-family:Consolas,monospace;font-size:11px;color:#666;word-break:break-all}.std-value{font-weight:600;color:#0056b3}.note-col{color:#888;font-size:12px}")
[void]$h.AppendLine("</style></head><body><h1>Windows 組態安全配置檢核報告</h1><div class='info-card'>")
foreach ($kv in @(@('平台', 'Windows'), @('主機名', $ComputerName), @('IP地址', $IPAddress),
        @('作業系統版本', "$OSName ($OSVersion)"), @('核心/內部版本', $KernelVersion),
        @('檢核基準', "$BaselineName (全量共 $($AuditResults.Count) 項)"), @('腳本版本', $ScriptVersion),
        @('執行身分', $RunAs), @('Check stated at', $CheckTime), @('Check ended at', $EndTime))) {
    [void]$h.AppendLine("<p><strong>$($kv[0]):</strong> $(& $E $kv[1])</p>")
}
[void]$h.AppendLine("<p class='sum'><span class='st-ok'>符合 $($Cnt[$ST_OK])</span><span class='st-ng'>不符合 $($Cnt[$ST_NG])</span>" +
    "<span class='st-na'>不適用 $($Cnt[$ST_NA])</span><span class='st-er'>查不到 $($Cnt[$ST_ER])</span></p>")
[void]$h.AppendLine("<p>合規率 $rate ＝ 符合 $($Cnt[$ST_OK]) ÷（總數 $($AuditResults.Count) − 不適用 $($Cnt[$ST_NA]) − 查不到 $($Cnt[$ST_ER]) ＝ $denom）</p></div>")
[void]$h.AppendLine("<table><tr><th style='width:45px'>項次</th><th style='width:140px'>Server TWGCB-ID</th><th style='width:80px'>檢查結果</th>" +
    "<th style='width:120px'>類別</th><th style='width:180px'>原則設定名稱</th><th style='width:220px'>GPO設定路徑</th>" +
    "<th style='width:130px'>設定值 (標準)</th><th style='width:200px'>Registry路徑</th><th style='width:160px'>目前值</th><th style='width:110px'>備註</th></tr>")
foreach ($item in $AuditResults) {
    $st = $item."檢查結果"; $k = $cls[$st]
    [void]$h.AppendLine("<tr><td class='idx-col'>$($item."項次")</td><td>$((& $E $item."TWGCB-ID") -replace '\r?\n', '<br>')</td>" +
        "<td class='$k'>$st<br>$($label[$st])</td><td>$(& $E $item."類別")</td><td><strong>$(& $E $item."原則設定名稱")</strong></td>" +
        "<td class='gpo-path'>$(& $E $item."GPO設定路徑")</td><td class='std-value'>$(& $E $item."標準設定值")</td>" +
        "<td class='reg-path'>$(& $E $item."Registry路徑")</td><td class='$k'>$(& $E $item."目前值")</td><td class='note-col'>$(& $E $item."備註")</td></tr>")
}
[void]$h.AppendLine("</table></body></html>")
[System.IO.File]::WriteAllText($HtmlPath, $h.ToString(), (New-Object System.Text.UTF8Encoding($true)))

Write-Host "`n=================================================================" -ForegroundColor Green
Write-Host "  檢核完成：共 $($AuditResults.Count) 條" -ForegroundColor Green
Write-Host "    符合 $($Cnt[$ST_OK])　不符合 $($Cnt[$ST_NG])　不適用 $($Cnt[$ST_NA])　查不到 $($Cnt[$ST_ER])" -ForegroundColor Green
Write-Host "    合規率 $rate（分母不含不適用與查不到）" -ForegroundColor Green
Write-Host "    - TXT : $TxtPath" -ForegroundColor Green
Write-Host "    - HTML: $HtmlPath" -ForegroundColor Green
Write-Host "=================================================================" -ForegroundColor Green
