"""Windows 的 OS 重建資料（2026-10-20）。

跟 Linux 的 `os_rebuild_collector` **同一套項目模型**（group／id／必要|建議／state／label／
text／note／denied），資產頁「OS 重建」分頁不用分平台就畫得出來。存同一張 `host_rebuild`。

## 傳輸與 SOC 規則（同 winrm_collector）

* 只走 PSRP 在記憶體跑**一段**唯讀腳本（`REBUILD_PS`），一次往返收齊，用 `@@SEC <名稱>` 切段。
* 不寫 .ps1、不帶 File 參數、不改執行原則、不編碼、不 IEX、不 Add-Type、不另起行程。
  `test_agent_scripts_security` 自動掃本模組所有 `_PS` 常數。
* 不收祕密：憑證只收 Subject／指紋／到期日／「有沒有私鑰」這個布林值，**不碰私鑰本體**；
  不讀產品金鑰（啟用狀態只收授權狀態碼）；環境變數、hosts、服務路徑走 `_mask` 遮密碼。

## 權限（決策 WINRM-ADMIN1：收集帳號可以是管理員；不是也要能降級）

每一段各自 try／catch，拿不到印 `ERR` 行（同 `winrm_collector._PS_ERR_FN`）；主要查詢被拒就改走
CIM／登錄檔替代來源。替代來源也沒有 → 該項 state=denied（deny_kind="admin"：需系統管理員），
不會變成「此機未使用」。非 Server 版沒有角色與功能 → `NA` → 此機未使用。
"""
from __future__ import annotations

import re

import winrm_collector

# ---------------------------------------------------------------------
# 項目（id／group／title／nec）——跟 Linux 的 ITEMS 同結構
# ---------------------------------------------------------------------
ITEMS: list[dict] = [
    {"id": "win.sys.os", "group": "系統基本", "title": "作業系統版本／組建／版別", "nec": "A"},
    {"id": "win.sys.domain", "group": "系統基本", "title": "網域／工作群組", "nec": "A"},
    {"id": "win.sys.timezone", "group": "系統基本", "title": "時區", "nec": "A"},
    {"id": "win.sys.locale", "group": "系統基本", "title": "地區設定／語系", "nec": "B"},
    {"id": "win.sys.activation", "group": "系統基本", "title": "授權啟用狀態（不含金鑰）", "nec": "B"},
    {"id": "win.storage.disks", "group": "儲存", "title": "實體磁碟", "nec": "A"},
    {"id": "win.storage.partitions", "group": "儲存", "title": "分割區", "nec": "A"},
    {"id": "win.storage.volumes", "group": "儲存", "title": "磁碟區／磁碟機代號／掛載點", "nec": "A"},
    {"id": "win.storage.pagefile", "group": "儲存", "title": "分頁檔", "nec": "B"},
    {"id": "win.net.adapters", "group": "網路", "title": "網路卡", "nec": "A"},
    {"id": "win.net.ip", "group": "網路", "title": "IP／前置長度／預設閘道", "nec": "A"},
    {"id": "win.net.dns", "group": "網路", "title": "DNS 伺服器／尾碼", "nec": "A"},
    {"id": "win.net.routes", "group": "網路", "title": "靜態路由（非預設）", "nec": "A"},
    {"id": "win.net.hosts", "group": "網路", "title": "hosts 檔", "nec": "B"},
    {"id": "win.net.teaming", "group": "網路", "title": "NIC Teaming", "nec": "B"},
    {"id": "win.net.firewall", "group": "網路", "title": "防火牆設定檔狀態", "nec": "B"},
    {"id": "win.role.features", "group": "角色與功能", "title": "已安裝的角色與功能", "nec": "A"},
    {"id": "win.svc.auto", "group": "服務啟動設定", "title": "非預設的自動啟動服務＋執行帳號", "nec": "A"},
    {"id": "win.acct.groups", "group": "本機群組", "title": "本機群組與成員", "nec": "A"},
    {"id": "win.patch.hotfix", "group": "已安裝修補", "title": "已安裝 KB", "nec": "B"},
    {"id": "win.cert.my", "group": "憑證", "title": "LocalMachine\\My 憑證（不含私鑰）", "nec": "B"},
    {"id": "win.env.system", "group": "環境變數", "title": "系統環境變數（PATH 等）", "nec": "B"},
    {"id": "win.share.smb", "group": "共用資料夾", "title": "共用資料夾（不含系統 $ 共用）", "nec": "B"},
    {"id": "win.time.w32time", "group": "時間同步", "title": "W32Time NTP 來源", "nec": "A"},
]

#: 項目 → (段落名, 欄位標題)。一項可以吃多段（主要＋替代來源）。
_SECTIONS: dict[str, list[tuple[str, list[str]]]] = {
    "win.sys.os": [("os", ["項目", "值"])],
    "win.sys.domain": [("domain", ["項目", "值"])],
    "win.sys.timezone": [("tz", ["項目", "值"])],
    "win.sys.locale": [("locale", ["項目", "值"])],
    "win.sys.activation": [("activation", ["產品", "授權狀態", "說明"])],
    "win.storage.disks": [("disks", ["#", "型號", "序號", "大小", "分割樣式", "狀態", "匯流排", "開機碟", "系統碟"])],
    "win.storage.partitions": [("partitions", ["磁碟", "分割", "代號", "大小", "起始位移", "類型", "存取路徑"])],
    "win.storage.volumes": [("volumes", ["代號", "標籤", "檔案系統", "大小", "剩餘", "配置單位", "類型"]),
                            ("mountpoints", ["掛載路徑", "代號", "標籤", "檔案系統", "大小", "區塊大小"])],
    "win.storage.pagefile": [("pagefile", ["類別", "名稱", "初始(MB)", "最大(MB)／已用"])],
    "win.net.adapters": [("adapters", ["名稱", "說明", "MAC", "狀態", "速度", "ifIndex"])],
    "win.net.ip": [("ipaddr", ["介面", "家族", "IP", "前置長度", "來源"]),
                   ("ipconf", ["網卡", "MAC", "IP", "子網路遮罩", "預設閘道", "DHCP"])],
    "win.net.dns": [("dns", ["網卡／全域", "DNS 伺服器", "連線尾碼", "尾碼搜尋清單"])],
    "win.net.routes": [("routes", ["類別", "目的", "下一跳", "介面／遮罩", "metric"])],
    "win.net.hosts": [("hosts", ["內容"])],
    "win.net.teaming": [("teaming", ["Team", "模式", "負載平衡", "成員", "狀態"])],
    "win.net.firewall": [("firewall", ["設定檔", "啟用", "預設輸入", "預設輸出"])],
    "win.role.features": [("features", ["名稱", "顯示名稱", "類型"])],
    "win.svc.auto": [("services", ["服務", "顯示名稱", "狀態", "執行帳號", "執行檔", "延遲啟動"])],
    "win.acct.groups": [("groups", ["群組", "SID", "成員", "成員查詢錯誤", "說明"])],
    "win.patch.hotfix": [("hotfix", ["KB", "類別", "安裝日", "安裝者"])],
    "win.cert.my": [("certs", ["Subject", "指紋", "到期", "簽發者", "有私鑰", "易記名稱"])],
    "win.env.system": [("env", ["名稱", "值"])],
    "win.share.smb": [("shares", ["共用名", "路徑", "說明", "權限"])],
    "win.time.w32time": [("w32time", ["項目", "值"])],
}

#: 會含祕密的段落，輸出前一律 `_mask`
_MASK_SECTIONS = {"env", "hosts", "services"}

STATE_LABEL_ADMIN = "權限不足（需系統管理員）"

_TEXT_CAP = 131072
_ROWS_CAP = 3000          # 單段最多保留幾列（KB、hosts 偶爾很大）


# ---------------------------------------------------------------------
# PowerShell（唯讀；SOC 規則見模組說明）
# ---------------------------------------------------------------------
_PS_HELPERS = r"""
function W3S($s) { ("" + $s) -replace '[\t\r\n]+', ' ' }
function W3T($d) { if ($d) { ([datetime]$d).ToString('yyyy-MM-dd HH:mm:ss') } else { '' } }
function W3R { $o = foreach ($a in $args) { W3S $a }; "R`t" + ($o -join "`t") }
function W3J($x) { (@($x) | Where-Object { $_ -ne $null -and "$_" -ne '' }) -join ',' }
"""

REBUILD_PS = r"""
$ErrorActionPreference='SilentlyContinue'
""" + winrm_collector._PS_ERR_FN + _PS_HELPERS + r"""
"SRC=win_rebuild"
$os = $null
"@@SEC os"
try {
  $os = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
  W3R 'caption' $os.Caption
  W3R 'version' $os.Version
  W3R 'build' $os.BuildNumber
  W3R 'architecture' $os.OSArchitecture
  W3R 'sku' $os.OperatingSystemSKU
  W3R 'product_type' $os.ProductType
  W3R 'install_date' (W3T $os.InstallDate)
  W3R 'last_boot' (W3T $os.LastBootUpTime)
  W3R 'system_directory' $os.SystemDirectory
  W3R 'hostname' $env:COMPUTERNAME
} catch { W3Err 'os' $_ }
try {
  $cv = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -ErrorAction Stop
  W3R 'edition' $cv.EditionID
  W3R 'display_version' $(if ($cv.DisplayVersion) { $cv.DisplayVersion } else { $cv.ReleaseId })
  W3R 'ubr' $cv.UBR
  W3R 'installation_type' $cv.InstallationType
} catch { W3Err 'os_registry' $_ }

"@@SEC domain"
try {
  $cs = Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop
  W3R 'domain' $cs.Domain
  W3R 'part_of_domain' $cs.PartOfDomain
  W3R 'workgroup' $cs.Workgroup
  W3R 'domain_role' $cs.DomainRole
} catch { W3Err 'domain' $_ }

"@@SEC tz"
try {
  $tz = Get-TimeZone -ErrorAction Stop
  W3R 'id' $tz.Id
  W3R 'display_name' $tz.DisplayName
} catch {
  try {
    $t = Get-CimInstance -ClassName Win32_TimeZone -ErrorAction Stop
    W3R 'caption' $t.Caption
    W3R 'standard_name' $t.StandardName
  } catch { W3Err 'tz' $_ }
}

"@@SEC locale"
try { W3R 'system_locale' (Get-WinSystemLocale -ErrorAction Stop).Name } catch { W3Err 'locale' $_ }
W3R 'culture' (Get-Culture).Name
W3R 'ui_culture' (Get-UICulture).Name
if ($os) { W3R 'os_language' $os.OSLanguage }

"@@SEC activation"
try {
  $lic = @(Get-CimInstance -ClassName SoftwareLicensingProduct -Filter "ApplicationID='55c92734-d682-4d71-983e-d6ec3f16059f' AND PartialProductKey IS NOT NULL" -ErrorAction Stop)
  foreach ($l in $lic) { W3R $l.Name $l.LicenseStatus $l.Description }
  if ($lic.Count -eq 0) { "ERR`tactivation`tEMPTY`tno licensed product returned" }
} catch { W3Err 'activation' $_ }

"@@SEC disks"
$rows = @()
try { $rows = @(Get-Disk -ErrorAction Stop) } catch { W3Err 'disks' $_ }
if ($rows.Count -gt 0) {
  foreach ($d in $rows) { W3R $d.Number $d.FriendlyName $d.SerialNumber $d.Size $d.PartitionStyle $d.OperationalStatus $d.BusType $d.IsBoot $d.IsSystem }
} else {
  try { Get-CimInstance -ClassName Win32_DiskDrive -ErrorAction Stop | ForEach-Object { W3R $_.Index $_.Model $_.SerialNumber $_.Size '' $_.Status $_.InterfaceType '' '' } } catch { W3Err 'disks_cim' $_ }
}

"@@SEC partitions"
$rows = @()
try { $rows = @(Get-Partition -ErrorAction Stop) } catch { W3Err 'partitions' $_ }
if ($rows.Count -gt 0) {
  foreach ($p in $rows) { W3R $p.DiskNumber $p.PartitionNumber $p.DriveLetter $p.Size $p.Offset $p.Type (W3J $p.AccessPaths) }
} else {
  try { Get-CimInstance -ClassName Win32_DiskPartition -ErrorAction Stop | ForEach-Object { W3R $_.DiskIndex $_.Index '' $_.Size $_.StartingOffset $_.Type '' } } catch { W3Err 'partitions_cim' $_ }
}

"@@SEC volumes"
$rows = @()
try { $rows = @(Get-Volume -ErrorAction Stop) } catch { W3Err 'volumes' $_ }
if ($rows.Count -gt 0) {
  foreach ($v in $rows) { W3R $v.DriveLetter $v.FileSystemLabel $v.FileSystem $v.Size $v.SizeRemaining $v.AllocationUnitSize $v.DriveType }
} else {
  try { Get-CimInstance -ClassName Win32_LogicalDisk -ErrorAction Stop | ForEach-Object { W3R $_.DeviceID $_.VolumeName $_.FileSystem $_.Size $_.FreeSpace '' $_.DriveType } } catch { W3Err 'volumes_cim' $_ }
}
"@@SEC mountpoints"
try { Get-CimInstance -ClassName Win32_Volume -ErrorAction Stop | ForEach-Object { W3R $_.Name $_.DriveLetter $_.Label $_.FileSystem $_.Capacity $_.BlockSize } } catch { W3Err 'mountpoints' $_ }

"@@SEC pagefile"
try { $c = Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop; W3R 'auto_managed' '' $c.AutomaticManagedPagefile '' } catch { W3Err 'pagefile' $_ }
try { Get-CimInstance -ClassName Win32_PageFileSetting -ErrorAction Stop | ForEach-Object { W3R 'setting' $_.Name $_.InitialSize $_.MaximumSize } } catch { W3Err 'pagefile_setting' $_ }
try { Get-CimInstance -ClassName Win32_PageFileUsage -ErrorAction Stop | ForEach-Object { W3R 'usage' $_.Name $_.AllocatedBaseSize $_.CurrentUsage } } catch { W3Err 'pagefile_usage' $_ }

"@@SEC adapters"
$rows = @()
try { $rows = @(Get-NetAdapter -ErrorAction Stop) } catch { W3Err 'adapters' $_ }
if ($rows.Count -gt 0) {
  foreach ($a in $rows) { W3R $a.Name $a.InterfaceDescription $a.MacAddress $a.Status $a.LinkSpeed $a.ifIndex }
} else {
  try { Get-CimInstance -ClassName Win32_NetworkAdapter -Filter "NetConnectionID IS NOT NULL" -ErrorAction Stop | ForEach-Object { W3R $_.NetConnectionID $_.Name $_.MACAddress $_.NetConnectionStatus $_.Speed $_.InterfaceIndex } } catch { W3Err 'adapters_cim' $_ }
}

"@@SEC ipaddr"
try { Get-NetIPAddress -ErrorAction Stop | Where-Object { $_.InterfaceAlias -notmatch 'Loopback' } | ForEach-Object { W3R $_.InterfaceAlias $_.AddressFamily $_.IPAddress $_.PrefixLength $_.PrefixOrigin } } catch { W3Err 'ipaddr' $_ }
"@@SEC ipconf"
$nics = @()
try { $nics = @(Get-CimInstance -ClassName Win32_NetworkAdapterConfiguration -Filter "IPEnabled=True" -ErrorAction Stop) } catch { W3Err 'ipconf' $_ }
foreach ($n in $nics) { W3R $n.Description $n.MACAddress (W3J $n.IPAddress) (W3J $n.IPSubnet) (W3J $n.DefaultIPGateway) $n.DHCPEnabled }

"@@SEC dns"
foreach ($n in $nics) { W3R $n.Description (W3J $n.DNSServerSearchOrder) $n.DNSDomain (W3J $n.DNSDomainSuffixSearchOrder) }
try {
  $tp = Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters' -ErrorAction Stop
  W3R '(global)' '' $(if ($tp.'NV Domain') { $tp.'NV Domain' } else { $tp.Domain }) $tp.SearchList
} catch { W3Err 'dns_global' $_ }

"@@SEC routes"
try {
  Get-NetRoute -ErrorAction Stop | Where-Object {
    $_.DestinationPrefix -ne '0.0.0.0/0' -and $_.DestinationPrefix -ne '::/0' -and
    $_.NextHop -ne '0.0.0.0' -and $_.NextHop -ne '::'
  } | ForEach-Object { W3R 'live' $_.DestinationPrefix $_.NextHop $_.InterfaceAlias $_.RouteMetric }
} catch { W3Err 'routes' $_ }
if (Test-Path -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\PersistentRoutes') {
  try {
    foreach ($r in (Get-Item -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\PersistentRoutes' -ErrorAction Stop).Property) {
      $f = ("" + $r).Split(',')
      W3R 'persistent' $f[0] $f[2] $f[1] $f[3]
    }
  } catch { W3Err 'routes_persistent' $_ }
}

"@@SEC hosts"
try { Get-Content -LiteralPath (Join-Path $env:SystemRoot 'System32\drivers\etc\hosts') -TotalCount 3000 -ErrorAction Stop | ForEach-Object { W3R $_ } } catch { W3Err 'hosts' $_ }

"@@SEC teaming"
if (Get-Command Get-NetLbfoTeam -ErrorAction SilentlyContinue) {
  try { Get-NetLbfoTeam -ErrorAction Stop | ForEach-Object { W3R $_.Name $_.TeamingMode $_.LoadBalancingAlgorithm (W3J $_.Members) $_.Status } } catch { W3Err 'teaming' $_ }
} else { "NA" }

"@@SEC firewall"
try { Get-NetFirewallProfile -ErrorAction Stop | ForEach-Object { W3R $_.Name $_.Enabled $_.DefaultInboundAction $_.DefaultOutboundAction } } catch { W3Err 'firewall' $_ }

"@@SEC features"
if ($os -and [int]$os.ProductType -eq 1) { "NA" } else {
  $done = $false
  if (Get-Command Get-WindowsFeature -ErrorAction SilentlyContinue) {
    try { Get-WindowsFeature -ErrorAction Stop | Where-Object { $_.Installed } | ForEach-Object { W3R $_.Name $_.DisplayName $_.FeatureType }; $done = $true } catch { W3Err 'features' $_ }
  }
  if (-not $done) {
    try { Get-CimInstance -ClassName Win32_ServerFeature -ErrorAction Stop | ForEach-Object { W3R $_.ID $_.Name '' } } catch {
      if (("" + $_.FullyQualifiedErrorId + " " + $_.Exception.Message) -match '0x80041010|Invalid class') { "NA" } else { W3Err 'features_cim' $_ }
    }
  }
}

"@@SEC services"
$svcs = @()
$svcErr = $false
try { $svcs = @(Get-CimInstance -ClassName Win32_Service -Filter "StartMode='Auto'" -ErrorAction Stop) } catch { W3Err 'services' $_; $svcErr = $true }
if ($svcs.Count -gt 0) {
  foreach ($s in $svcs) { W3R $s.Name $s.DisplayName $s.State $s.StartName $s.PathName $s.DelayedAutoStart }
} else {
  if (-not $svcErr) { "ERR`tservices`tEMPTY`tWin32_Service returned no rows" }
  try {
    Get-ChildItem -Path 'HKLM:\SYSTEM\CurrentControlSet\Services' -ErrorAction Stop | ForEach-Object {
      $p = Get-ItemProperty -LiteralPath $_.PSPath
      if ($p.ImagePath -and ([int]$p.Type -band 0x30) -and [int]$p.Start -eq 2) {
        $dn = "" + $p.DisplayName
        if ($dn.StartsWith('@')) { $dn = '' }
        W3R $_.PSChildName $dn '' $p.ObjectName $p.ImagePath $(if ([int]$p.DelayedAutostart -eq 1) { 'True' } else { 'False' })
      }
    }
  } catch { W3Err 'services_registry' $_ }
}

"@@SEC groups"
$done = $false
if (Get-Command Get-LocalGroup -ErrorAction SilentlyContinue) {
  try {
    foreach ($g in @(Get-LocalGroup -ErrorAction Stop)) {
      $m = ''
      $me = ''
      try { $m = (@(Get-LocalGroupMember -Group $g -ErrorAction Stop) | ForEach-Object { $_.Name }) -join ', ' } catch { $me = W3S $_.Exception.Message }
      W3R $g.Name $g.SID $m $me $g.Description
    }
    $done = $true
  } catch { W3Err 'groups' $_ }
}
if (-not $done) {
  try {
    Get-CimInstance -ClassName Win32_Group -Filter "LocalAccount=True" -ErrorAction Stop | ForEach-Object {
      $mm = (@(Get-CimAssociatedInstance -InputObject $_ -ResultClassName Win32_Account -ErrorAction SilentlyContinue) | ForEach-Object { $_.Domain + '\' + $_.Name }) -join ', '
      W3R $_.Name $_.SID $mm '' $_.Description
    }
  } catch { W3Err 'groups_cim' $_ }
}

"@@SEC hotfix"
try { Get-CimInstance -ClassName Win32_QuickFixEngineering -ErrorAction Stop | ForEach-Object { W3R $_.HotFixID $_.Description $_.InstalledOn $_.InstalledBy } } catch { W3Err 'hotfix' $_ }

"@@SEC certs"
try { Get-ChildItem -LiteralPath 'Cert:\LocalMachine\My' -ErrorAction Stop | ForEach-Object { W3R $_.Subject $_.Thumbprint (W3T $_.NotAfter) $_.Issuer $_.HasPrivateKey $_.FriendlyName } } catch { W3Err 'certs' $_ }

"@@SEC env"
try {
  $ek = Get-Item -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment' -ErrorAction Stop
  foreach ($n in $ek.Property) { W3R $n $ek.GetValue($n, '', 'DoNotExpandEnvironmentNames') }
} catch { W3Err 'env' $_ }

"@@SEC shares"
$done = $false
if (Get-Command Get-SmbShare -ErrorAction SilentlyContinue) {
  try {
    foreach ($s in @(Get-SmbShare -ErrorAction Stop | Where-Object { -not $_.Special -and -not ("" + $_.Name).EndsWith('$') })) {
      $acc = ''
      try { $acc = (@(Get-SmbShareAccess -Name $s.Name -ErrorAction Stop) | ForEach-Object { "$($_.AccountName):$($_.AccessControlType):$($_.AccessRight)" }) -join '; ' } catch { $acc = '(access query failed)' }
      W3R $s.Name $s.Path $s.Description $acc
    }
    $done = $true
  } catch { W3Err 'shares' $_ }
}
if (-not $done) {
  try { Get-CimInstance -ClassName Win32_Share -Filter "Type=0" -ErrorAction Stop | Where-Object { -not ("" + $_.Name).EndsWith('$') } | ForEach-Object { W3R $_.Name $_.Path $_.Description '' } } catch { W3Err 'shares_cim' $_ }
}

"@@SEC w32time"
try {
  $wp = Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\Parameters' -ErrorAction Stop
  W3R 'NtpServer' $wp.NtpServer
  W3R 'Type' $wp.Type
} catch { W3Err 'w32time' $_ }
if (Test-Path -LiteralPath 'HKLM:\SOFTWARE\Policies\Microsoft\W32Time\Parameters') {
  try {
    $gp = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Policies\Microsoft\W32Time\Parameters' -ErrorAction Stop
    W3R 'GPO.NtpServer' $gp.NtpServer
    W3R 'GPO.Type' $gp.Type
  } catch { W3Err 'w32time_gpo' $_ }
}
"@@END"
"""


# ---------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------
def parse_output(text: str) -> dict:
    """腳本輸出 → {"sections": {名稱: {"rows": [[...]], "errors": [...], "na": bool}}, "complete": bool}。"""
    sections: dict[str, dict] = {}
    cur: dict | None = None
    complete = False
    for raw in (text or "").splitlines():
        line = raw.rstrip("\r\n")
        if line.startswith("@@SEC "):
            cur = sections.setdefault(line[6:].strip(), {"rows": [], "errors": [], "na": False})
            continue
        if line.strip() == "@@END":
            complete = True
            cur = None
            continue
        if cur is None:
            continue
        if line.startswith("ERR\t"):
            cur["errors"].extend(winrm_collector.parse_errors(line))
        elif line.strip() == "NA":
            cur["na"] = True
        elif line.startswith("R\t"):
            if len(cur["rows"]) < _ROWS_CAP:
                cur["rows"].append(line[2:].split("\t"))
            else:
                cur["capped"] = True
    return {"sections": sections, "complete": complete}


def _gb(v) -> str:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v or "")
    if n <= 0:
        return "0"
    return f"{n / 1024 ** 3:.1f} GB" if n >= 1024 ** 3 else f"{n / 1024 ** 2:.0f} MB"


#: 這幾欄是 bytes，顯示成 GB/MB
_BYTE_COLS = {"disks": {3}, "partitions": {3}, "volumes": {3, 4}, "mountpoints": {4}}

#: 系統內建服務的執行檔位置：在這底下＝Windows 預設服務，不算「非預設」
_SECRET_NAME = re.compile(r"(?i)pass(?:word|wd|phrase)?|pwd|secret|token|api[-_]?key|credential|"
                          r"private[-_]?key|access[-_]?key|(?:^|_)key$|auth|sas|conn(?:ection)?[-_]?str")
#: 服務執行檔參數裡「空白分隔」的密碼旗標（-password Foo、--pass Foo、/pw Foo、-p Foo）
_SECRET_FLAG = re.compile(r"(?i)(\s(?:--?|/)(?:p|pw|pwd|pass|passwd|password|secret|token|key|apikey)"
                          r"(?:\s+|[:=]))(\"[^\"]*\"|\S+)")
#: 這些段落不可能真的「沒有」（每台都有磁碟、分割、磁碟區、網卡、IP）：沒報錯卻 0 筆＝查不到，不是此機未使用
_NEVER_EMPTY = {"disks", "partitions", "volumes", "adapters", "ipaddr", "ipconf"}
_SYS_PATH = re.compile(r'(?i)^\s*"?[a-z]:\\windows\\')


def _rows_for(sec: str, rows: list[list[str]]) -> list[list[str]]:
    cols = _BYTE_COLS.get(sec)
    out = []
    for r in rows:
        r = list(r)
        if cols:
            r = [(_gb(v) if i in cols and v not in ("", None) else v) for i, v in enumerate(r)]
        out.append(r)
    if sec == "env":
        # 表格是「名稱 | 值」，_mask 認不到 key=value——依變數名遮（名稱留著，值不留）
        out = [[r[0], "[已遮蔽]"] if _SECRET_NAME.search(r[0] if r else "") else r for r in out]
    if sec == "services":
        # 非預設＝執行檔不在 C:\Windows 底下，或用非內建帳號跑（重建時要重新設定帳密的那些）
        builtin = ("localsystem", "nt authority\\localservice", "nt authority\\networkservice",
                   "nt authority\\local service", "nt authority\\network service", "")
        out = [r[:4] + [_SECRET_FLAG.sub(lambda m: m.group(1) + "[已遮蔽]", r[4])] + r[5:]
               if len(r) > 4 else r for r in out]
        out = [r for r in out if not _SYS_PATH.match(r[4] if len(r) > 4 else "")
               or (r[3] if len(r) > 3 else "").strip().lower() not in builtin]
    return out


def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return ""
    if len(headers) == 1:
        return "\n".join(r[0] if r else "" for r in rows)
    lines = [" | ".join(headers)]
    lines += [" | ".join(c if c != "" else "—" for c in (r + [""] * (len(headers) - len(r)))[:len(headers)])
              for r in rows]
    return "\n".join(lines)


def _cap(text: str) -> tuple[str, bool]:
    b = (text or "").encode("utf-8", "replace")
    if len(b) <= _TEXT_CAP:
        return text or "", False
    return b[:_TEXT_CAP].decode("utf-8", "ignore") + f"\n…[已截斷：原始超過 {_TEXT_CAP} bytes]", True


def _mask(text: str) -> str:
    import os_rebuild_collector
    return os_rebuild_collector._mask(text)


def _build_item(item: dict, parsed: dict) -> dict:
    secs = parsed.get("sections") or {}
    specs = _SECTIONS[item["id"]]
    present = [s for s, _h in specs if s in secs]
    if not present:
        return {"state": "not_run"}
    parts, errors, na, capped, had_rows = [], [], False, False, False
    for sec, headers in specs:
        d = secs.get(sec)
        if not d:
            continue
        had_rows = had_rows or bool(d["rows"])
        errors += d["errors"]
        na = na or d["na"]
        capped = capped or bool(d.get("capped"))
        rows = _rows_for(sec, d["rows"])
        body = _table(headers, rows)
        if body:
            if sec in _MASK_SECTIONS:
                body = _mask(body)
            parts.append(body if len(specs) == 1 else f"# {sec}\n{body}")
    text, trunc = _cap("\n\n".join(parts))
    notes = winrm_collector.explain_errors(errors)
    denied = [e for e in errors if e.get("kind") in ("DENIED", "EMPTY")]
    out: dict = {}
    if not parsed.get("complete", True) and text.strip():
        notes = notes + ["腳本沒有跑完（輸出被截斷或逾時），這一項可能不完整"]
    if text.strip():
        out.update(state="collected", text=text, truncated=trunc or capped)
        if notes:
            out["note"] = "部分查詢沒拿到（已改用替代來源或缺部分欄位）：" + "；".join(notes)[:400]
    elif had_rows:
        out["state"] = "empty"                       # 有資料但全被濾掉（例如服務全是內建）
    elif denied or (not errors and not na and all(s in _NEVER_EMPTY for s in present)):
        if not notes:
            notes = ["沒有報錯，但一筆都沒回（非管理員常見）"]
        out.update(state="denied", deny_kind="admin", note="；".join(notes)[:400])
    elif errors:
        out.update(state="unknown", note="；".join(notes)[:400])
    elif na:
        out.update(state="empty", note="此機沒有這一項（非 Server 版或沒裝此功能）")
    else:
        out["state"] = "empty"
    if item["id"] == "win.svc.auto" and out["state"] == "empty" and secs.get("services", {}).get("rows"):
        out["note"] = "自動啟動的服務全是 Windows 內建（執行檔在 C:\\Windows、用內建帳號），沒有非預設的"
    return out


def build_rebuild_data(parsed: dict) -> dict:
    """跟 os_rebuild_collector.build_rebuild_data 同形狀（資產頁同一套畫法）。"""
    import os_rebuild_collector as lin

    by_group: dict[str, list[dict]] = {}
    counts: dict[str, int] = {}
    deny_kinds: dict[str, int] = {}
    a_collected = 0
    for item in ITEMS:
        built = _build_item(item, parsed or {})
        if built["state"] == "denied":
            deny_kinds[built["deny_kind"]] = deny_kinds.get(built["deny_kind"], 0) + 1
        built.update({"id": item["id"], "group": item["group"], "title": item["title"],
                      "necessity": item["nec"],
                      "label": (STATE_LABEL_ADMIN if built["state"] == "denied"
                                else lin.STATE_LABEL.get(built["state"], built["state"]))})
        counts[built["state"]] = counts.get(built["state"], 0) + 1
        if item["nec"] == "A" and built["state"] == "collected":
            a_collected += 1
        by_group.setdefault(item["group"], []).append(built)
    return {"groups": [{"group": g, "items": its} for g, its in by_group.items()],
            "summary": {"total": len(ITEMS),
                        "total_a": sum(1 for i in ITEMS if i["nec"] == "A"),
                        "total_b": sum(1 for i in ITEMS if i["nec"] == "B"),
                        "a_collected": a_collected, "by_state": counts,
                        "sudo_list": None,
                        "collected": counts.get("collected", 0),
                        "denied": counts.get("denied", 0),
                        "denied_by_kind": deny_kinds}}


# ---------------------------------------------------------------------
# 收集（憑證流程同 account_inventory／service_inventory 的 Windows 分支）
# ---------------------------------------------------------------------
def collect_rebuild(host: str, username: str, password: str, runner=None, timeout: int = 120) -> dict:
    """跑腳本 → {"platform": "windows", "data": ..., "raw": {...}}。password 用完即丟。"""
    text = winrm_collector._run(host, username, password, REBUILD_PS, timeout, runner)
    parsed = parse_output(text)
    # raw 只留診斷用的「每段幾列＋錯誤」，不留原文：原文是 tab 分隔，_mask 認不到其中的祕密
    diag = {k: {"rows": len(v["rows"]), "errors": v["errors"], "na": v["na"]}
            for k, v in parsed["sections"].items()}
    return {"platform": "windows", "data": build_rebuild_data(parsed),
            "raw": {"complete": parsed["complete"], "sections": diag}}


def collect_for(conn, asset_serial: str, ip: str, runner=None) -> tuple[dict | None, str | None]:
    """對一台 Windows 收。回 (res, None) 或 (None, 能行動的原因)。"""
    import credential_store

    if runner is not None:
        return collect_rebuild(ip, "", "", runner=runner), None
    import win_onboard

    # Windows 勾選才收（2026-10-22）：沒納管的不登入（第二道；第一道是只挑 collect_ok=1）
    blocked = win_onboard.collect_block(conn, ip, asset_serial)
    if blocked:
        return None, blocked
    cred_name = credential_store.pick_for_host(conn, ip, kind="winrm")
    if not cred_name:
        return None, "沒有可用的收集憑證——請先在系統設定新增 WinRM 服務帳號"
    got = credential_store.get_for_use(conn, cred_name)
    if got is None:
        return None, f"憑證「{cred_name}」解不開（加密金鑰可能已更換），請重新設定"
    username, password = got
    try:
        res = collect_rebuild(ip, username, password)
    except Exception as exc:  # noqa: BLE001 - 原因如實回報（含開通指引），不吞
        msg = str(exc)[:200]
        if "actively refused" in msg or "timed out" in msg or "Max retries" in msg:
            msg = f"{msg}｜{winrm_collector.enable_hint()}"
        credential_store.audit_use(conn, cred_name, ip, False, msg)
        return None, msg
    credential_store.audit_use(conn, cred_name, ip, True, "OS 重建資料收集成功")
    return res, None
