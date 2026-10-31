#Requires -Version 4.0
<#
.SYNOPSIS
    Windows 磁碟 Housekeeping 唯讀盤點：找出最佔空間的目錄、整包很久沒動的目錄、
    使用中目錄裡的舊檔、疑似整包複製的備份、單一大檔，產出可直接貼進 email 的 summary.txt。

.DESCRIPTION
    設計原則（給資安 / 主管審閱）：
      1. 純查詢：掃描期間對任何磁碟零寫入；結束時才把報告寫到 -OutDir（-NoReport 則完全不寫檔）。
         報告寫入前會先確認該磁碟剩餘空間，不夠就只印在畫面上。
      2. 不提權、不繞過：無權限的目錄直接跳過並計數；不使用特殊權限、不取得擁有權、
         不呼叫外部程式、不動態執行字串、不編譯程式碼、不連網路、不做遠端連線。
      3. 不打擾營運：開跑前取樣 CPU / 記憶體 / 磁碟忙碌度，超過門檻就不跑；
         掃描中每 10 秒再確認一次，超標先暫停，持續超標就中止並輸出已完成部分；
         本身行程優先權降為 BelowNormal（只影響這支 script 自己）。
      4. 有時限：總時限（預設 5 分鐘，含開跑前檢查）到了就停，未完成的數字標「≥」。
      5. 不鑽太深：只統計到第 -MaxDepth 層目錄，更深的檔案大小直接歸到該層母目錄。
      6. 不跟隨 junction / symbolic link，避免重複計算與無窮迴圈。
      7. 「多久沒動」看檔案最後修改時間（LastWriteTime）。整包複製備份時修改時間會保留原檔的，
         所以複製過來的舊 log 一樣會被抓到。

    結束代碼：0 = 完成；1 = 部分完成（時間到或負載中止）；2 = 負載過高未執行；3 = 執行錯誤。

.EXAMPLE
    .\Get-DiskHousekeeping.ps1
    掃描使用率 >= 80% 的本機磁碟，報告寫到 script 所在目錄的 Reports\。

.EXAMPLE
    .\Get-DiskHousekeeping.ps1 -Drives C,D -TopN 3 -MaxMinutes 10

.EXAMPLE
    .\Get-DiskHousekeeping.ps1 -Path 'D:\APData' -NoReport
    只掃單一目錄，結果只印在畫面上，不寫任何檔案。
#>
[CmdletBinding()]
param(
    # 要掃的磁碟代號（C、D:、E:\ 都可）。不給則自動掃使用率 >= AlertPercent 的本機磁碟
    [string[]]$Drives,
    # 改成只掃指定目錄（給了就忽略 -Drives）
    [string[]]$Path,
    [ValidateRange(0, 100)][int]$AlertPercent = 80,
    [ValidateRange(1, 50)][int]$TopN = 5,
    # 總時限（分鐘），含開跑前約 6 秒的負載取樣
    [ValidateRange(0.01, 120)][double]$MaxMinutes = 5,
    # 目錄統計深度：磁碟根目錄下第一層 = 1
    [ValidateRange(1, 8)][int]$MaxDepth = 4,
    [ValidateRange(1, 100)][int]$OldYears = 5,
    [ValidateRange(1, 100)][int]$AncientYears = 10,
    # 小於這個大小的項目不列進 summary（CSV 仍完整）
    [ValidateRange(0, 100000)][double]$MinReportGB = 1,
    [ValidateRange(0, 100)][int]$CpuMax = 80,
    [ValidateRange(0, 100)][int]$MemMax = 90,
    [ValidateRange(0, 100)][int]$DiskBusyMax = 90,
    [string[]]$ExcludePath = @(),
    [string]$OutDir,
    [int]$MinFreeMBForReport = 500,
    [switch]$NoReport,
    # WMI/CIM 讀不到負載時預設不跑；確定要跑才加這個
    [switch]$AllowUnknownLoad,
    # 回傳結構化結果（測試用）
    [switch]$PassThru
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ScriptVersion = '1.0.0'

# =====================================================================
#  共用小工具
# =====================================================================
$script:LogLines = New-Object 'System.Collections.Generic.List[string]'

function Write-Log {
    param([string]$Message, [string]$Level = 'INFO', [switch]$LogOnly)
    $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    $script:LogLines.Add($line)
    if ($LogOnly) { return }
    $color = switch ($Level) { 'WARN' { 'Yellow' } 'ERROR' { 'Red' } default { 'Gray' } }
    Write-Host $line -ForegroundColor $color
}

function Format-Size {
    param([double]$Bytes)
    if ($Bytes -ge 1TB) { return ('{0:N2} TB' -f ($Bytes / 1TB)) }
    if ($Bytes -ge 1GB) { return ('{0:N1} GB' -f ($Bytes / 1GB)) }
    if ($Bytes -ge 1MB) { return ('{0:N0} MB' -f ($Bytes / 1MB)) }
    return ('{0:N0} KB' -f ($Bytes / 1KB))
}

function Format-Date {
    param([long]$Ticks)
    if ($Ticks -le 0) { return '-' }
    return ([datetime]$Ticks).ToLocalTime().ToString('yyyy-MM-dd')
}

function Get-AgeText {
    param([long]$Ticks)
    if ($Ticks -le 0) { return '' }
    $years = [math]::Floor(((Get-Date).ToUniversalTime().Ticks - $Ticks) / [TimeSpan]::TicksPerDay / 365.25)
    if ($years -ge 1) { return "（$years 年前）" }
    return ''
}

# 讓 AP 看得懂的系統目錄名稱
function Get-FriendlyName {
    param([string]$Name)
    switch ($Name) {
        '$Recycle.Bin' { return '（資源回收筒：使用者已刪除但未清空的檔案）' }
        'Temp'         { return '（暫存目錄）' }
        default        { return '' }
    }
}

function Format-NodeSize {
    param($Node)
    $prefix = ''
    if ($Node.Partial) { $prefix = '≥' }
    return $prefix + (Format-Size $Node.TotBytes)
}

# =====================================================================
#  系統負載（只讀 WMI/CIM 效能計數器；類別名稱不受中文系統語系影響）
# =====================================================================
function Get-SystemLoad {
    $r = @{ Cpu = $null; Mem = $null; Disk = $null }
    try {
        $p = Get-CimInstance -ClassName Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'" -ErrorAction Stop
        $r.Cpu = [int]$p.PercentProcessorTime
    } catch {
        try {
            $r.Cpu = [int](Get-CimInstance -ClassName Win32_Processor -ErrorAction Stop |
                Measure-Object -Property LoadPercentage -Average).Average
        } catch { }
    }
    try {
        $os = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
        $r.Mem = [int][math]::Round(100 - ($os.FreePhysicalMemory * 100.0 / $os.TotalVisibleMemorySize))
    } catch { }
    try {
        $d = Get-CimInstance -ClassName Win32_PerfFormattedData_PerfDisk_PhysicalDisk -Filter "Name='_Total'" -ErrorAction Stop
        $r.Disk = [int][math]::Max(0, [math]::Min(100, 100 - [int]$d.PercentIdleTime))
    } catch { }
    return $r
}

function Get-AverageLoad {
    $null = Get-SystemLoad   # 效能計數器第一次讀常是 0，先暖身
    $acc = @{ Cpu = @(); Mem = @(); Disk = @() }
    for ($i = 0; $i -lt 3; $i++) {
        Start-Sleep -Seconds 2
        $l = Get-SystemLoad
        foreach ($k in 'Cpu', 'Mem', 'Disk') { if ($null -ne $l[$k]) { $acc[$k] += $l[$k] } }
    }
    $r = @{}
    foreach ($k in 'Cpu', 'Mem', 'Disk') {
        if ($acc[$k].Count -gt 0) { $r[$k] = [int][math]::Round(($acc[$k] | Measure-Object -Average).Average) }
        else { $r[$k] = $null }
    }
    return $r
}

function Get-BusyReason {
    param($Load)
    $reasons = @()
    if ($null -ne $Load.Cpu  -and $Load.Cpu  -ge $CpuMax)      { $reasons += "CPU $($Load.Cpu)%（門檻 $CpuMax%）" }
    if ($null -ne $Load.Mem  -and $Load.Mem  -ge $MemMax)      { $reasons += "記憶體 $($Load.Mem)%（門檻 $MemMax%）" }
    if ($null -ne $Load.Disk -and $Load.Disk -ge $DiskBusyMax) { $reasons += "磁碟忙碌 $($Load.Disk)%（門檻 $DiskBusyMax%）" }
    if ($null -eq $Load.Cpu -and $null -eq $Load.Mem -and $null -eq $Load.Disk -and -not $AllowUnknownLoad) {
        $reasons += '讀不到系統負載（WMI/CIM 無回應）'
    }
    return ($reasons -join '、')
}

function Format-Load {
    param($Load)
    $f = { param($v) if ($null -eq $v) { '?' } else { "$v" } }
    return ('CPU {0}% / 記憶體 {1}% / 磁碟忙碌 {2}%' -f (& $f $Load.Cpu), (& $f $Load.Mem), (& $f $Load.Disk))
}

# 掃描中：忙就暫停 15 秒再看，最多 4 次；仍然忙就回傳原因（呼叫端會中止）
function Wait-WhileBusy {
    param([System.Diagnostics.Stopwatch]$Clock, [double]$DeadlineSec)
    for ($try = 0; $try -le 4; $try++) {
        $reason = Get-BusyReason (Get-SystemLoad)
        if (-not $reason) { return '' }
        if ($try -eq 4 -or ($Clock.Elapsed.TotalSeconds + 15) -ge $DeadlineSec) { return $reason }
        Write-Log "系統忙碌（$reason），暫停 15 秒後再確認（$($try + 1)/4）" 'WARN'
        Start-Sleep -Seconds 15
    }
    return ''
}

# =====================================================================
#  掃描（只讀目錄清單與檔案屬性，不開啟任何檔案內容）
# =====================================================================
function New-ScanNode {
    param([string]$NodePath, [string]$Name, [int]$Depth, $Parent)
    return @{
        Path = $NodePath; Name = $Name; Depth = $Depth; Parent = $Parent
        Bytes = [long]0; Files = [long]0; OldBytes = [long]0; AncBytes = [long]0; OldFiles = [long]0
        Newest = [long]0; Partial = $false; Denied = 0
    }
}

function Set-PartialChain {
    param($Nodes, $Key)
    while ($null -ne $Key) {
        $n = $Nodes[$Key]
        if ($null -eq $n -or $n.Partial) { break }
        $n.Partial = $true
        $Key = $n.Parent
    }
}

function Invoke-TreeScan {
    param([string]$RootPath, [System.Diagnostics.Stopwatch]$Clock, [double]$DeadlineSec, $ExcludeSet)

    $DIR = [int][System.IO.FileAttributes]::Directory
    $RP  = [int][System.IO.FileAttributes]::ReparsePoint
    $nowUtc   = (Get-Date).ToUniversalTime()
    $oldTicks = $nowUtc.AddYears(-$OldYears).Ticks
    $ancTicks = $nowUtc.AddYears(-$AncientYears).Ticks
    $topKeep  = [math]::Max(50, $TopN)

    $rootInfo = New-Object System.IO.DirectoryInfo $RootPath
    $rootKey  = $rootInfo.FullName
    $nodes = @{}
    $nodes[$rootKey] = New-ScanNode $rootKey $rootKey 0 $null

    $topFiles = New-Object 'System.Collections.Generic.List[object]'
    $topMin   = [long]0
    $bySizeDesc = [System.Comparison[object]] { param($a, $b) ([long]$b[0]).CompareTo([long]$a[0]) }
    $oldExt   = @{}
    $stack    = New-Object 'System.Collections.Generic.Stack[object]'
    $stack.Push(@($rootInfo, 0, $rootKey))

    $nFiles = [long]0; $nDirs = [long]0; $nLinks = 0; $nDenied = 0; $nLong = 0; $nErrors = 0; $entries = [long]0
    $problemSamples = New-Object 'System.Collections.Generic.List[string]'
    $excludedHit    = New-Object 'System.Collections.Generic.List[string]'
    $stopped = ''; $stopReason = ''
    $nextLoadCheck = $Clock.Elapsed.TotalSeconds + 10
    $nextProgress  = 0

    while ($stack.Count -gt 0) {
        $el = $Clock.Elapsed.TotalSeconds
        if ($el -ge $DeadlineSec) { $stopped = 'timeout'; break }
        if ($el -ge $nextLoadCheck) {
            $stopReason = Wait-WhileBusy -Clock $Clock -DeadlineSec $DeadlineSec
            if ($stopReason) { $stopped = 'load'; break }
            $nextLoadCheck = $Clock.Elapsed.TotalSeconds + 10
        }
        if ($el -ge $nextProgress) {
            Write-Progress -Activity "掃描 $RootPath（唯讀）" -Status ('已看 {0:N0} 個檔案、{1:N0} 個目錄，已用 {2:N0} 秒' -f $nFiles, $nDirs, $el)
            $nextProgress = $el + 2
        }

        $item   = $stack.Pop()
        $di     = $item[0]; $depth = $item[1]; $bucket = $item[2]
        $sum    = [long]0; $cnt = [long]0; $ob = [long]0; $ab = [long]0; $oc = [long]0; $newest = [long]0
        $interrupted = $false
        try {
            foreach ($e in $di.EnumerateFileSystemInfos()) {
                $attr = [int]$e.Attributes
                if ($attr -band $DIR) {
                    if ($attr -band $RP) { $nLinks++; continue }
                    $full = $e.FullName
                    if ($ExcludeSet.Contains($full)) { $excludedHit.Add($full); continue }
                    $nDirs++
                    $cd = $depth + 1
                    if ($cd -le $MaxDepth) {
                        $nodes[$full] = New-ScanNode $full $e.Name $cd $bucket
                        $stack.Push(@($e, $cd, $full))
                    } else {
                        $stack.Push(@($e, $cd, $bucket))
                    }
                } else {
                    $len = $e.Length
                    $wt  = $e.LastWriteTimeUtc.Ticks
                    $sum += $len; $cnt++
                    if ($wt -gt $newest) { $newest = $wt }
                    if ($wt -lt $oldTicks) {
                        $ob += $len; $oc++
                        if ($wt -lt $ancTicks) { $ab += $len }
                        $ext = $e.Extension.ToLowerInvariant()
                        if (-not $ext) { $ext = '(無副檔名)' }
                        $x = $oldExt[$ext]
                        if ($null -ne $x) { $x[0] += $len; $x[1]++ } else { $oldExt[$ext] = @([long]$len, [long]1) }
                    }
                    if ($len -gt $topMin) {
                        $topFiles.Add(@($len, $e.FullName, $wt))
                        if ($topFiles.Count -ge ($topKeep * 4)) {
                            $topFiles.Sort($bySizeDesc)
                            $topFiles.RemoveRange($topKeep, $topFiles.Count - $topKeep)
                            $topMin = $topFiles[$topKeep - 1][0]
                        }
                    }
                }
                $entries++
                if (($entries % 5000) -eq 0 -and $Clock.Elapsed.TotalSeconds -ge $DeadlineSec) {
                    $stopped = 'timeout'; $interrupted = $true; break
                }
            }
        } catch {
            $ex = $_.Exception
            while ($null -ne $ex.InnerException -and -not ($ex -is [System.UnauthorizedAccessException])) { $ex = $ex.InnerException }
            if ($ex -is [System.UnauthorizedAccessException] -or $ex -is [System.Security.SecurityException]) {
                $nDenied++; $nodes[$bucket].Denied++
                if ($problemSamples.Count -lt 30) { $problemSamples.Add("無權限：$($di.FullName)") }
            } elseif ($ex -is [System.IO.PathTooLongException] -or $ex -is [System.IO.DirectoryNotFoundException]) {
                # 路徑超過 Windows 傳統 260 字元上限（或掃描當下剛好被刪掉），略過
                $nLong++
                if ($problemSamples.Count -lt 30) { $problemSamples.Add("路徑過長或已不存在：$($di.FullName)") }
            } else {
                $nErrors++
                if ($problemSamples.Count -lt 30) { $problemSamples.Add("讀取錯誤：$($di.FullName)：$($ex.Message)") }
            }
        }

        $n = $nodes[$bucket]
        $n.Bytes += $sum; $n.Files += $cnt; $n.OldBytes += $ob; $n.AncBytes += $ab; $n.OldFiles += $oc
        if ($newest -gt $n.Newest) { $n.Newest = $newest }
        $nFiles += $cnt
        if ($interrupted) { $stack.Push($item); break }
    }
    Write-Progress -Activity "掃描 $RootPath（唯讀）" -Completed

    # 沒掃完的目錄：自己和所有上層都標「未掃完」
    if ($stopped) { foreach ($it in $stack) { Set-PartialChain $nodes $it[2] } }

    # 由深到淺把大小往上加總
    foreach ($n in $nodes.Values) {
        $n.TotBytes = $n.Bytes; $n.TotFiles = $n.Files; $n.TotOld = $n.OldBytes
        $n.TotAnc = $n.AncBytes; $n.TotOldFiles = $n.OldFiles; $n.TotNewest = $n.Newest
    }
    foreach ($n in @($nodes.Values | Sort-Object -Property @{ Expression = { $_.Depth } } -Descending)) {
        if ($null -eq $n.Parent) { continue }
        $p = $nodes[$n.Parent]
        $p.TotBytes += $n.TotBytes; $p.TotFiles += $n.TotFiles; $p.TotOld += $n.TotOld
        $p.TotAnc += $n.TotAnc; $p.TotOldFiles += $n.TotOldFiles
        if ($n.TotNewest -gt $p.TotNewest) { $p.TotNewest = $n.TotNewest }
    }

    $topFiles.Sort($bySizeDesc)
    if ($topFiles.Count -gt $topKeep) { $topFiles.RemoveRange($topKeep, $topFiles.Count - $topKeep) }

    return @{
        Root = $rootKey; Nodes = $nodes; TopFiles = $topFiles; OldExt = $oldExt
        Files = $nFiles; Dirs = $nDirs; Links = $nLinks; Denied = $nDenied; LongPath = $nLong; Errors = $nErrors
        Problems = $problemSamples; Excluded = $excludedHit
        Stopped = $stopped; StopReason = $stopReason; OldTicks = $oldTicks; Scanned = $true
    }
}

# =====================================================================
#  分析：把掃描結果整理成「給 AP 看的建議清單」
# =====================================================================
$BackupNamePattern = '(?i)(back\s?up|(^|[^a-z])(bak|old|copy)([^a-z]|$)|複製|備份|副本|舊)|^[a-z\u4e00-\u9fff].*[_\-. ](19|20)\d{2}[-_.]?\d{2}[-_.]?\d{2}$'

function Get-RootAnalysis {
    param($Scan)
    $nodes = $Scan.Nodes
    $minBytes = [long]($MinReportGB * 1GB)
    $oldTicks = $Scan.OldTicks

    $children = @{}
    foreach ($n in $nodes.Values) {
        if ($null -eq $n.Parent) { continue }
        if (-not $children.ContainsKey($n.Parent)) { $children[$n.Parent] = New-Object 'System.Collections.Generic.List[object]' }
        $children[$n.Parent].Add($n)
    }

    # 由淺到深算：整包沒動 / 是否在整包沒動的目錄底下 / 名稱像備份
    foreach ($n in @($nodes.Values | Sort-Object -Property @{ Expression = { $_.Depth } })) {
        $n.Stale = ($n.Depth -ge 1 -and $n.TotFiles -gt 0 -and $n.TotNewest -lt $oldTicks -and -not $n.Partial)
        $n.BackupName = ($n.Depth -ge 1 -and $n.Name -match $BackupNamePattern)
        $p = $null
        if ($null -ne $n.Parent) { $p = $nodes[$n.Parent] }
        $n.InStale    = $n.Stale -or ($null -ne $p -and $p.InStale)
        $n.InBackup   = $n.BackupName -or ($null -ne $p -and $p.InBackup)
        $n.StaleTop   = $n.Stale -and ($null -eq $p -or -not $p.InStale)
        $n.BackupTop  = $n.BackupName -and ($null -eq $p -or -not $p.InBackup)
    }

    # 1. 第一層最大目錄 + 熱點（一路往下跟著「佔上層一半以上」的子目錄走）
    $topDirs = @()
    $rootKids = @()
    if ($children.ContainsKey($Scan.Root)) { $rootKids = @($children[$Scan.Root] | Sort-Object -Property @{ Expression = { $_.TotBytes } } -Descending) }
    foreach ($d in @($rootKids | Select-Object -First $TopN)) {
        $cur = $d
        while ($children.ContainsKey($cur.Path)) {
            $big = @($children[$cur.Path] | Sort-Object -Property @{ Expression = { $_.TotBytes } } -Descending)[0]
            if ($big.TotBytes -gt 0 -and $big.TotBytes -ge ($cur.TotBytes * 0.5)) { $cur = $big } else { break }
        }
        $hot = $null
        if ($cur.Path -ne $d.Path) { $hot = $cur }
        $topDirs += [pscustomobject]@{ Node = $d; Name = $d.Name; Path = $d.Path; Bytes = $d.TotBytes; HotNode = $hot; Hotspot = $(if ($hot) { $hot.Path } else { $null }) }
    }

    # 2. 整包沒動：同一個母目錄底下有多個就合併成一筆
    $staleTops = @($nodes.Values | Where-Object { $_.StaleTop })
    $stale = @()
    foreach ($g in @($staleTops | Group-Object -Property { $_.Parent })) {
        $items = @($g.Group | Sort-Object -Property @{ Expression = { $_.TotBytes } } -Descending)
        $bytes = [long]0; $files = [long]0; $newest = [long]0
        foreach ($i in $items) { $bytes += $i.TotBytes; $files += $i.TotFiles; if ($i.TotNewest -gt $newest) { $newest = $i.TotNewest } }
        if ($items.Count -ge 2) {
            $stale += [pscustomobject]@{ Path = $g.Name; Bytes = $bytes; Files = $files; Newest = $newest; Count = $items.Count; Names = @($items | ForEach-Object { $_.Name }) }
        } else {
            $stale += [pscustomobject]@{ Path = $items[0].Path; Bytes = $bytes; Files = $files; Newest = $newest; Count = 1; Names = @() }
        }
    }
    $stale = @($stale | Where-Object { $_.Bytes -ge $minBytes -and $_.Bytes -gt 0 } | Sort-Object -Property Bytes -Descending | Select-Object -First $TopN)

    # 3. 使用中目錄裡的舊檔（不含已列在「整包沒動」的）
    $mixed = @($nodes.Values | Where-Object { -not $_.InStale -and $_.OldBytes -gt 0 -and $_.OldBytes -ge $minBytes } |
        Sort-Object -Property @{ Expression = { $_.OldBytes } } -Descending | Select-Object -First $TopN |
        ForEach-Object { [pscustomobject]@{ Path = $_.Path; OldBytes = $_.OldBytes; AncBytes = $_.AncBytes; OldFiles = $_.OldFiles; Deep = ($_.Depth -eq $MaxDepth) } })

    # 4. 名稱像整包複製的備份
    $backup = @($nodes.Values | Where-Object { $_.BackupTop -and $_.TotBytes -gt 0 -and $_.TotBytes -ge $minBytes } |
        Sort-Object -Property @{ Expression = { $_.TotBytes } } -Descending | Select-Object -First $TopN |
        ForEach-Object { [pscustomobject]@{ Path = $_.Path; Bytes = $_.TotBytes; Newest = $_.TotNewest; Partial = $_.Partial } })

    # 5. 單一大檔
    $files = @($Scan.TopFiles | Select-Object -First $TopN | ForEach-Object { [pscustomobject]@{ Path = $_[1]; Bytes = $_[0]; Modified = $_[2] } })

    return [pscustomobject]@{
        Root = $Scan.Root; Scan = $Scan; TopDirs = $topDirs; Stale = $stale; MixedOld = $mixed
        BackupLike = $backup; TopFiles = $files; TotalBytes = $nodes[$Scan.Root].TotBytes
        Links = $Scan.Links; Stopped = $Scan.Stopped
        Nodes = @($nodes.Values | ForEach-Object { [pscustomobject]@{ Path = $_.Path; Depth = $_.Depth; Bytes = $_.TotBytes } })
    }
}

# =====================================================================
#  summary.txt（條列格式，貼進 Outlook 不會跑版）
# =====================================================================
function Add-RootSummary {
    param($Lines, $A, $DriveRow)
    $label = $A.Root.TrimEnd('\')
    $scan  = $A.Scan

    $Lines.Add('')
    $Lines.Add("■ $label 最佔空間的目錄 Top $TopN")
    if (-not $scan.Scanned) {
        $Lines.Add('  （時間已用完，未掃描）')
        return
    }
    if ($A.TopDirs.Count -eq 0) { $Lines.Add('  （無）') }
    $i = 0
    foreach ($t in $A.TopDirs) {
        $i++
        $n = $t.Node
        $oldPart = ''
        if ($n.TotOld -gt 0) { $oldPart = "，其中 $OldYears 年以上沒動 $(Format-Size $n.TotOld)" }
        $Lines.Add("  $i. $($n.Path)$(Get-FriendlyName $n.Name)　$(Format-NodeSize $n)$oldPart　最後異動 $(Format-Date $n.TotNewest)")
        if ($t.HotNode) { $Lines.Add("     主要集中在：$($t.HotNode.Path)（$(Format-NodeSize $t.HotNode)）") }
        if ($n.Partial) { $Lines.Add('     ※ 此目錄未掃完，實際大小只會更大') }
    }

    $Lines.Add('')
    $Lines.Add("■ $label 整個目錄 $OldYears 年以上沒有異動（可評估整包刪除或移出）")
    if ($A.Stale.Count -eq 0) { $Lines.Add('  （無）') }
    $i = 0
    foreach ($s in $A.Stale) {
        $i++
        if ($s.Count -ge 2) {
            $names = ($s.Names | Select-Object -First 4) -join '、'
            if ($s.Count -gt 4) { $names += ' 等' }
            $Lines.Add("  $i. $($s.Path) 底下 $($s.Count) 個子目錄（$names）　共 $(Format-Size $s.Bytes)、$('{0:N0}' -f $s.Files) 個檔　最後異動 $(Format-Date $s.Newest)$(Get-AgeText $s.Newest)")
        } else {
            $Lines.Add("  $i. $($s.Path)　$(Format-Size $s.Bytes)、$('{0:N0}' -f $s.Files) 個檔　最後異動 $(Format-Date $s.Newest)$(Get-AgeText $s.Newest)")
        }
    }

    $Lines.Add('')
    $Lines.Add("■ $label 使用中目錄裡的舊檔（建議訂保留期限定期清理）")
    if ($A.MixedOld.Count -eq 0) { $Lines.Add('  （無）') }
    $i = 0
    foreach ($m in $A.MixedOld) {
        $i++
        $where = $m.Path
        if ($m.Deep) { $where += '（含子目錄）' }
        $anc = ''
        if ($m.AncBytes -gt 0) { $anc = "，其中 $AncientYears 年以上 $(Format-Size $m.AncBytes)" }
        $Lines.Add("  $i. $where　$OldYears 年以上的舊檔 $(Format-Size $m.OldBytes)（$('{0:N0}' -f $m.OldFiles) 個$anc）")
    }

    $Lines.Add('')
    $Lines.Add("■ $label 疑似整包複製的備份目錄（名稱含 bak / backup / old / copy / 日期）")
    if ($A.BackupLike.Count -eq 0) { $Lines.Add('  （無）') }
    $i = 0
    foreach ($b in $A.BackupLike) {
        $i++
        $sz = Format-Size $b.Bytes
        if ($b.Partial) { $sz = '≥' + $sz }
        $Lines.Add("  $i. $($b.Path)　$sz　最後異動 $(Format-Date $b.Newest)$(Get-AgeText $b.Newest)")
    }

    $Lines.Add('')
    $Lines.Add("■ $label 單一大檔 Top $TopN")
    if ($A.TopFiles.Count -eq 0) { $Lines.Add('  （無）') }
    $i = 0
    foreach ($f in $A.TopFiles) {
        $i++
        $Lines.Add("  $i. $($f.Path)　$(Format-Size $f.Bytes)　最後異動 $(Format-Date $f.Modified)")
    }

    $ext = @($scan.OldExt.GetEnumerator() | Sort-Object -Property @{ Expression = { $_.Value[0] } } -Descending | Select-Object -First 6 |
        ForEach-Object { "$($_.Key) $(Format-Size $_.Value[0])" })
    if ($ext.Count -gt 0) {
        $Lines.Add('')
        $Lines.Add("■ $label $OldYears 年以上舊檔的類型")
        $Lines.Add('  ' + ($ext -join '、'))
    }

    $Lines.Add('')
    $Lines.Add("■ $label 掃描涵蓋")
    $cover = "  已掃 $(Format-Size $A.TotalBytes)"
    if ($DriveRow) { $cover += " / 磁碟已用 $(Format-Size $DriveRow.Used)" }
    $cover += '（{0:N0} 個檔、{1:N0} 個目錄）' -f $scan.Files, $scan.Dirs
    $Lines.Add($cover)
    $notes = @()
    if ($scan.Excluded.Count -gt 0) { $notes += "系統目錄未掃 $($scan.Excluded.Count) 個" }
    if ($scan.Denied -gt 0) { $notes += "無權限略過 $($scan.Denied) 個目錄" }
    if ($scan.LongPath -gt 0) { $notes += "路徑過長略過 $($scan.LongPath) 個目錄" }
    if ($scan.Errors -gt 0) { $notes += "其他讀取錯誤 $($scan.Errors) 個目錄" }
    if ($scan.Links -gt 0)  { $notes += "捷徑型目錄（junction）略過 $($scan.Links) 個" }
    if ($scan.Stopped -eq 'timeout') { $notes += '時間到，部分目錄未掃完（標 ≥ 者）' }
    if ($scan.Stopped -eq 'load')    { $notes += "系統持續忙碌中止（$($scan.StopReason)），部分目錄未掃完" }
    if ($notes.Count -gt 0) { $Lines.Add('  ' + ($notes -join '；')) }
}

# =====================================================================
#  主流程
# =====================================================================
$clock     = [System.Diagnostics.Stopwatch]::StartNew()
$startTime = Get-Date
$hostName  = $env:COMPUTERNAME
$exitCode  = 0
$reportDir = $null
$utf8Bom   = $(if ($PSVersionTable.PSVersion.Major -ge 6) { 'utf8BOM' } else { 'utf8' })   # 記事本 / Outlook / Excel 開中文不亂碼
$analyses  = @()
$summaryText = ''

try {
    Write-Log "Disk Housekeeping 盤點 v$ScriptVersion 開始（主機 $hostName，唯讀模式）"

    # ---- 1. 開跑前負載檢查 ----
    Write-Log '檢查系統負載（取樣 3 次，約 6 秒）...'
    $preLoad = Get-AverageLoad
    Write-Log ('目前負載：' + (Format-Load $preLoad))
    $busy = Get-BusyReason $preLoad
    if ($busy) {
        Write-Log "系統負載過高，本次不執行：$busy" 'WARN'
        Write-Host '請等負載下降或改在離峰時段再執行。本次未讀取任何目錄、未寫任何檔案。' -ForegroundColor Yellow
        if ($PassThru) { [pscustomobject]@{ ExitCode = 2; Roots = @(); ReportDir = $null; SummaryText = ''; OldExt = @{} } }
        exit 2
    }

    try {
        [System.Diagnostics.Process]::GetCurrentProcess().PriorityClass = [System.Diagnostics.ProcessPriorityClass]::BelowNormal
        Write-Log '本 script 行程優先權已降為 BelowNormal'
    } catch {
        Write-Log "無法調低本身優先權（不影響掃描）：$($_.Exception.Message)" 'WARN'
    }

    # ---- 2. 磁碟現況與掃描目標 ----
    $driveRows = @([System.IO.DriveInfo]::GetDrives() | Where-Object { $_.DriveType -eq [System.IO.DriveType]::Fixed -and $_.IsReady } | ForEach-Object {
        $used = $_.TotalSize - $_.TotalFreeSpace
        [pscustomobject]@{
            Name = $_.Name.TrimEnd('\'); Root = $_.RootDirectory.FullName
            Total = $_.TotalSize; Used = $used; Free = $_.TotalFreeSpace
            Pct = [math]::Round($used * 100.0 / $_.TotalSize, 1)
        }
    })

    $targets = @()
    if ($Path) {
        foreach ($p in $Path) {
            if (Test-Path -LiteralPath $p -PathType Container) { $targets += (Resolve-Path -LiteralPath $p).ProviderPath }
            else { Write-Log "找不到目錄，略過：$p" 'WARN' }
        }
    } elseif ($Drives) {
        foreach ($d in $Drives) {
            $letter = $d.Trim().TrimEnd('\').TrimEnd(':').ToUpper()
            $row = $driveRows | Where-Object { $_.Name -eq "${letter}:" } | Select-Object -First 1
            if ($row) { $targets += $row.Root } else { Write-Log "找不到本機固定磁碟 ${letter}:，略過" 'WARN' }
        }
    } else {
        $targets = @($driveRows | Where-Object { $_.Pct -ge $AlertPercent } | ForEach-Object { $_.Root })
        if ($targets.Count -eq 0) { Write-Log "沒有磁碟使用率超過 $AlertPercent%，只輸出磁碟現況" }
    }

    # 系統目錄不掃（不是 AP 能清的，而且量大、權限複雜）
    $excludeSet = New-Object 'System.Collections.Generic.HashSet[string]' -ArgumentList ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($row in $driveRows) {
        foreach ($name in 'System Volume Information', 'Config.Msi', 'Recovery', '$WinREAgent', '$Windows.~BT', '$Windows.~WS') {
            [void]$excludeSet.Add((Join-Path $row.Root $name))
        }
    }
    if ($env:SystemRoot) { [void]$excludeSet.Add($env:SystemRoot.TrimEnd('\')) }
    foreach ($x in $ExcludePath) { if ($x) { [void]$excludeSet.Add($x.TrimEnd('\')) } }

    # ---- 3. 掃描（剩餘時間平均分給還沒掃的目標）----
    $totalSec = $MaxMinutes * 60
    $abortAll = $false
    for ($i = 0; $i -lt $targets.Count; $i++) {
        $t = $targets[$i]
        $remain = $totalSec - $clock.Elapsed.TotalSeconds
        if ($abortAll -or $remain -le 0) {
            Write-Log "未掃描：$t（$(if ($abortAll) { '系統忙碌中止' } else { '時間已用完' })）" 'WARN'
            $empty = @{ Root = (New-Object System.IO.DirectoryInfo $t).FullName; Scanned = $false; Stopped = $(if ($abortAll) { 'load' } else { 'timeout' }) }
            $analyses += [pscustomobject]@{ Root = $empty.Root; Scan = $empty; TopDirs = @(); Stale = @(); MixedOld = @(); BackupLike = @(); TopFiles = @(); TotalBytes = [long]0; Links = 0; Stopped = $empty.Stopped; Nodes = @() }
            $exitCode = 1
            continue
        }
        $share = $remain / ($targets.Count - $i)
        Write-Log ('開始掃描 {0}（時間額度約 {1:N0} 秒）' -f $t, $share)
        $scan = Invoke-TreeScan -RootPath $t -Clock $clock -DeadlineSec ($clock.Elapsed.TotalSeconds + $share) -ExcludeSet $excludeSet
        Write-Log ('完成 {0}：{1:N0} 個檔、{2:N0} 個目錄、{3}，無權限 {4}、路徑過長 {5}、其他錯誤 {6}、junction {7}{8}' -f $t, $scan.Files, $scan.Dirs,
            (Format-Size $scan.Nodes[$scan.Root].TotBytes), $scan.Denied, $scan.LongPath, $scan.Errors, $scan.Links,
            $(if ($scan.Stopped) { "，中止原因：$($scan.Stopped) $($scan.StopReason)" } else { '' }))
        $k = 0
        foreach ($p in $scan.Problems) {
            $k++
            if ($k -le 5) { Write-Log $p 'WARN' } else { Write-Log $p 'WARN' -LogOnly }
        }
        if ($scan.Problems.Count -gt 5) { Write-Host "  （其餘 $($scan.Problems.Count - 5) 筆略過明細寫在 run.log）" -ForegroundColor Yellow }
        if ($scan.Stopped) { $exitCode = 1 }
        if ($scan.Stopped -eq 'load') { $abortAll = $true }
        $analyses += Get-RootAnalysis $scan
    }

    # ---- 4. 組 summary ----
    $lines = New-Object 'System.Collections.Generic.List[string]'
    $elapsed = $clock.Elapsed
    $lines.Add('【磁碟空間 Housekeeping 盤點】')
    $lines.Add("主機：$hostName　執行時間：$($startTime.ToString('yyyy-MM-dd HH:mm'))　耗時 $([math]::Floor($elapsed.TotalMinutes)) 分 $($elapsed.Seconds) 秒")
    $lines.Add('本報告為唯讀查詢結果，未刪除或變更任何檔案。')
    $lines.Add("「沒有異動」以檔案最後修改時間判斷；$OldYears 年以上視為舊檔，$AncientYears 年以上另外標出。")
    $lines.Add('')
    $lines.Add('■ 磁碟使用率')
    foreach ($row in $driveRows) {
        $flag = ''
        if ($row.Pct -ge $AlertPercent) { $flag = "　← 超過 $AlertPercent%" }
        $lines.Add("  $($row.Name)　$($row.Pct)%　已用 $(Format-Size $row.Used) / $(Format-Size $row.Total)，剩 $(Format-Size $row.Free)$flag")
    }
    foreach ($a in $analyses) {
        $row = $driveRows | Where-Object { $a.Root.StartsWith($_.Root, [System.StringComparison]::OrdinalIgnoreCase) } | Select-Object -First 1
        if ($Path) { $row = $null }   # 指定目錄時不跟整顆磁碟比
        Add-RootSummary -Lines $lines -A $a -DriveRow $row
    }
    $lines.Add('')
    $lines.Add('以上項目請 AP 確認是否可刪除、壓縮或移出，處理後請回覆，謝謝。')
    $lines.Add("（執行前負載：$(Format-Load $preLoad)；工具版本 v$ScriptVersion）")
    $summaryText = $lines -join "`r`n"

    Write-Host ''
    Write-Host '==================== summary（可直接複製貼到 email）====================' -ForegroundColor Cyan
    Write-Host $summaryText
    Write-Host '=========================================================================' -ForegroundColor Cyan

    # ---- 5. 寫報告（掃描已結束，這是本 script 唯一的寫入動作）----
    if ($NoReport) {
        Write-Log '已指定 -NoReport，不寫任何檔案'
    } else {
        if (-not $OutDir) {
            $base = $PSScriptRoot
            if (-not $base) { $base = (Get-Location).ProviderPath }
            $OutDir = Join-Path $base 'Reports'
        }
        $outFull = [System.IO.Path]::GetFullPath($OutDir)
        $outDrive = New-Object System.IO.DriveInfo ([System.IO.Path]::GetPathRoot($outFull))
        if ($outDrive.AvailableFreeSpace -lt ([long]$MinFreeMBForReport * 1MB)) {
            Write-Log "報告位置 $($outDrive.Name) 剩餘 $(Format-Size $outDrive.AvailableFreeSpace)，低於 $MinFreeMBForReport MB，不寫檔（請直接複製上面畫面內容）" 'WARN'
        } else {
            $reportDir = Join-Path $outFull ('{0}_{1}' -f $hostName, $startTime.ToString('yyyyMMdd_HHmmss'))
            New-Item -ItemType Directory -Path $reportDir -Force | Out-Null
            $summaryText | Out-File -FilePath (Join-Path $reportDir 'summary.txt') -Encoding $utf8Bom

            $dirRows = foreach ($a in $analyses) {
                if (-not $a.Scan.Scanned) { continue }
                foreach ($n in @($a.Scan.Nodes.Values | Sort-Object -Property @{ Expression = { $_.Path } })) {
                    [pscustomobject][ordered]@{
                        '掃描範圍'                 = $a.Root
                        '目錄'                     = $n.Path
                        '層級'                     = $n.Depth
                        '大小(GB)'                 = [math]::Round($n.TotBytes / 1GB, 2)
                        '檔案數'                   = $n.TotFiles
                        "${OldYears}年以上沒動(GB)"     = [math]::Round($n.TotOld / 1GB, 2)
                        "${AncientYears}年以上沒動(GB)" = [math]::Round($n.TotAnc / 1GB, 2)
                        '最後異動日'               = Format-Date $n.TotNewest
                        '整包沒動'                 = $(if ($n.Stale) { 'Y' } else { '' })
                        '名稱像備份'               = $(if ($n.BackupName) { 'Y' } else { '' })
                        '未掃完'                   = $(if ($n.Partial) { 'Y' } else { '' })
                        '無權限目錄數'             = $n.Denied
                    }
                }
            }
            @($dirRows) | Export-Csv -LiteralPath (Join-Path $reportDir 'dirs.csv') -NoTypeInformation -Encoding UTF8

            $fileRows = foreach ($a in $analyses) {
                if (-not $a.Scan.Scanned) { continue }
                foreach ($f in $a.Scan.TopFiles) {
                    [pscustomobject][ordered]@{
                        '掃描範圍' = $a.Root; '檔案' = $f[1]; '大小(GB)' = [math]::Round($f[0] / 1GB, 2); '最後異動日' = Format-Date $f[2]
                    }
                }
            }
            @($fileRows) | Export-Csv -LiteralPath (Join-Path $reportDir 'large_files.csv') -NoTypeInformation -Encoding UTF8
            Write-Log "報告已寫到 $reportDir"
        }
    }
} catch {
    $exitCode = 3
    Write-Log "執行錯誤：$($_.Exception.Message)（第 $($_.InvocationInfo.ScriptLineNumber) 行）" 'ERROR'
} finally {
    Write-Log ('結束，耗時 {0:N0} 秒，結束代碼 {1}' -f $clock.Elapsed.TotalSeconds, $exitCode)
    if ($reportDir -and (Test-Path -LiteralPath $reportDir)) {
        $script:LogLines | Out-File -FilePath (Join-Path $reportDir 'run.log') -Encoding $utf8Bom
    }
}

if ($PassThru) {
    $mergedExt = @{}
    foreach ($a in $analyses) {
        if (-not $a.Scan.Scanned) { continue }
        foreach ($kv in $a.Scan.OldExt.GetEnumerator()) { $mergedExt[$kv.Key] = $kv.Value }
    }
    [pscustomobject]@{ ExitCode = $exitCode; Roots = $analyses; ReportDir = $reportDir; SummaryText = $summaryText; OldExt = $mergedExt }
}
exit $exitCode
