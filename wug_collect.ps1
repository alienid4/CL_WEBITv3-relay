# WUG-RELAY-COLLECT-SCRIPT v4 : WhatsUp Gold read-only collector for the relay folder (GET only, plus token login / refresh)
# Run on the relay Windows host (PowerShell 5.1) as the account that owns the saved credential file.
# Output: one folder per batch = manifest.json + one JSON file per device. The system only reads these files; it never connects to WhatsUp.
# v4 (2026-11-17): the daily task once hung for 1 h 48 min (no success since 11-14 22:50). Every WhatsUp call now goes through
# Invoke-WugRest (HttpWebRequest with a hard TOTAL timeout: headers AND body, aborted on expiry), and a watchdog (Start-Watchdog)
# kills this PowerShell process tree when no call has finished for -StallMinutes, or the whole run exceeds -MaxMinutes.
# The watchdog writes WATCHDOG lines (which step, how long) to run_log.txt and wug_watchdog_log.txt next to this script.
# Example:  powershell -File wug_collect.ps1 -Base https://wug.example.test:9644 -IpFile wug_ips.txt -OutDir wug_out
param(
  [Parameter(Mandatory=$true)][string]$Base,
  [string]$IpFile = 'wug_ips.txt',
  [string]$OutDir = 'wug_out',
  [int]$DaysAgo = 1,
  [int]$PageLimit = 100,
  [int]$MaxPages = 20,
  [int]$MaxCalls = 20000,
  [int]$BatchSize = 100,
  [int]$ListMaxPages = 100,
  [int]$RefreshMinutes = 25,
  [int]$CallTimeoutSec = 60,
  [int]$StallMinutes = 15,
  [int]$MaxMinutes = 150,
  [int]$WatchdogPollSec = 5,
  [switch]$AllowMarketHours
)
$ErrorActionPreference = 'Stop'
$ScriptVersion = 'wug_collect 4'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# ---- watchdog (same text lives in relay_push.ps1) -------------------------------------------------------------------
# A background job (separate process) watches this PowerShell process. Progress = Set-Step touching a small file.
# It kills the whole process tree (taskkill /T /F, so a stuck sftp child dies too) when there is no progress for
# StallSec seconds, or the run is older than TotalSec seconds; it logs WHICH step and HOW LONG before killing.
# The kill gives exit code 1, so Task Scheduler shows a failed run instead of a run that "never ended".
$script:WdJob = $null
$script:WdStepFile = $null
function Set-Step([string]$Text) {
  if (-not $script:WdStepFile) { return }
  try { [IO.File]::WriteAllText($script:WdStepFile, ((Get-Date -Format s) + ' ' + $Text)) } catch { }
}
function Start-Watchdog([string]$Name, [int]$StallSec, [int]$TotalSec, [int]$PollSec, [string[]]$LogFiles) {
  if (($StallSec -le 0) -and ($TotalSec -le 0)) { return }
  $script:WdStepFile = Join-Path ([IO.Path]::GetTempPath()) ('webit3_wd_' + $Name + '_' + $PID + '.step')
  Set-Step 'start'
  try {
    $script:WdJob = Start-Job -ArgumentList $PID, $Name, $script:WdStepFile, $StallSec, $TotalSec, $PollSec, $LogFiles -ScriptBlock {
      param($ppid, $name, $stepFile, $stallSec, $totalSec, $pollSec, $logs)
      $t0 = Get-Date
      while ($true) {
        Start-Sleep -Seconds $pollSec
        if (-not (Get-Process -Id $ppid -ErrorAction SilentlyContinue)) { return }
        $why = ''
        $step = ''
        $last = $t0
        if (Test-Path -LiteralPath $stepFile) {
          $last = (Get-Item -LiteralPath $stepFile).LastWriteTime
          $step = [string](Get-Content -LiteralPath $stepFile -TotalCount 1 -ErrorAction SilentlyContinue)
        }
        $idle = [int]((Get-Date) - $last).TotalSeconds
        $age = [int]((Get-Date) - $t0).TotalSeconds
        if (($stallSec -gt 0) -and ($idle -ge $stallSec)) { $why = 'no progress for ' + $idle + ' s (limit ' + $stallSec + ' s)' }
        elseif (($totalSec -gt 0) -and ($age -ge $totalSec)) { $why = 'run longer than ' + $age + ' s (limit ' + $totalSec + ' s)' }
        if ($why) {
          $msg = (Get-Date -Format s) + ' WATCHDOG ' + $name + ': ' + $why + '; last step: ' + $step + '; killing process ' + $ppid + ' and its children (exit code 1)'
          foreach ($lf in $logs) { try { Add-Content -LiteralPath $lf -Value $msg } catch { } }
          & taskkill.exe /PID $ppid /T /F | Out-Null
          return
        }
      }
    }
  } catch {
    $script:WdJob = $null
    Write-Host ('WARN watchdog could not start (' + $_.Exception.Message + '); continuing without it')
  }
}
function Stop-Watchdog {
  if ($script:WdJob) { try { Stop-Job -Job $script:WdJob -ErrorAction SilentlyContinue; Remove-Job -Job $script:WdJob -Force -ErrorAction SilentlyContinue } catch { }; $script:WdJob = $null }
  if ($script:WdStepFile) { try { [IO.File]::Delete($script:WdStepFile) } catch { }; $script:WdStepFile = $null }
}

# ---- one WhatsUp call with a hard total timeout ---------------------------------------------------------------------
# Invoke-RestMethod -TimeoutSec does not bound reading the response body (a server that sends slowly can hold it for ever),
# so this uses HttpWebRequest: wait for the headers at most N s, then read the body within the SAME N s, abort on expiry.
# Errors keep the WebException shape ($err.Exception.Response.StatusCode), so the 401 handling below works unchanged.
function Invoke-WugRest([string]$Uri, [string]$Method = 'GET', [hashtable]$Headers = $null, [hashtable]$Form = $null, [int]$TimeoutSec = 60) {
  $req = [Net.HttpWebRequest]::Create($Uri)
  $req.Method = $Method
  $req.Accept = 'application/json'
  $req.UserAgent = 'webit3-relay'
  $req.Timeout = $TimeoutSec * 1000
  $req.ReadWriteTimeout = $TimeoutSec * 1000
  if ($Headers) { foreach ($k in $Headers.Keys) { $req.Headers.Add([string]$k, [string]$Headers[$k]) } }
  $t0 = Get-Date
  if ($Form) {
    $pairs = foreach ($k in $Form.Keys) { [Uri]::EscapeDataString([string]$k) + '=' + [Uri]::EscapeDataString([string]$Form[$k]) }
    $bytes = [Text.Encoding]::UTF8.GetBytes(($pairs -join '&'))
    $req.ContentType = 'application/x-www-form-urlencoded'
    $req.ContentLength = $bytes.Length
    $rs = $req.GetRequestStream()
    try { $rs.Write($bytes, 0, $bytes.Length) } finally { $rs.Dispose() }
  }
  $task = $req.GetResponseAsync()
  $waited = $false
  try { $waited = $task.Wait($TimeoutSec * 1000) } catch { $waited = $true }   # a failed request ends the wait with an error: handled just below
  if (-not $waited) { $req.Abort(); throw ('timeout: no response headers within ' + $TimeoutSec + ' s (' + $Method + ' ' + ($Uri -replace '\?.*$', '') + ')') }
  if ($task.IsFaulted) { throw $task.Exception.InnerException }   # the original WebException: $err.Exception.Response.StatusCode stays readable
  $resp = $task.Result
  try {
    $sr = New-Object IO.StreamReader($resp.GetResponseStream(), [Text.Encoding]::UTF8)
    $left = [int]($TimeoutSec * 1000 - ((Get-Date) - $t0).TotalMilliseconds)
    if ($left -lt 1000) { $left = 1000 }
    $rt = $sr.ReadToEndAsync()
    if (-not $rt.Wait($left)) { $req.Abort(); throw ('timeout: response body not complete within ' + $TimeoutSec + ' s (' + $Method + ' ' + ($Uri -replace '\?.*$', '') + ')') }
    $text = $rt.Result
  } finally { $resp.Close() }
  if (-not $text) { return $null }
  return ($text | ConvertFrom-Json)
}

# Do not run during trading hours (weekdays 08:30-15:00 local time) unless you explicitly allow it.
$now = Get-Date
$mins = $now.Hour * 60 + $now.Minute
if (($now.DayOfWeek -ne 'Saturday') -and ($now.DayOfWeek -ne 'Sunday') -and ($mins -ge 510) -and ($mins -lt 900) -and (-not $AllowMarketHours)) {
  Write-Host 'Stopped: this is weekday trading hours (08:30-15:00). Run it after 15:00, or add -AllowMarketHours if a person really needs it now.'
  exit 1
}

# Credentials: plain text file next to this script (username=... / password=...), edited by the operator; only the owner may read it. Nothing secret is ever written to the output.
$cfg = Join-Path $PSScriptRoot 'wug_account.txt'
if (-not (Test-Path -LiteralPath $cfg)) { Write-Host ('Missing ' + $cfg + ' - create it with Notepad: two lines username=... and password=..., readable only by this account; then run again.'); exit 1 }
$kv = @{}
foreach ($l in Get-Content -LiteralPath $cfg) { $i = $l.IndexOf('='); if ($i -gt 0) { $kv[$l.Substring(0, $i).Trim()] = $l.Substring($i + 1) } }
$user = $kv['username']; $pass = $kv['password']; $kv = $null
if (-not $user -or -not $pass) { Write-Host ('username or password is empty in ' + $cfg); exit 1 }
if ($Base -notmatch '^https://[A-Za-z0-9][A-Za-z0-9.-]{0,251}(:[0-9]{1,5})?/?$') { throw 'Base must be an https:// URL with a host name (plain http is refused because the password and token would travel unencrypted).' }
$root = $Base.TrimEnd('/')
$wdLogs = @((Join-Path $PSScriptRoot 'run_log.txt'), (Join-Path $PSScriptRoot 'wug_watchdog_log.txt'))
Start-Watchdog 'wug_collect' ($StallMinutes * 60) ($MaxMinutes * 60) $WatchdogPollSec $wdLogs
try {
# WhatsUp tokens expire after 30 minutes (expires_in=1799, measured 2026-11-09). v2 logged in once, so after ~73 devices every
# later call got 401 and only ~73 of 4684 devices arrived each day. v3 refreshes before 25 minutes, retries once on 401
# (refresh_token first, then the password again), and stops cleanly if it cannot log back in.
$script:refresh = $null
$script:tokAt = $null
$script:headers = $null
function Get-Token([bool]$UseRefresh) {
  if ($UseRefresh -and $script:refresh) { $b = @{ grant_type = 'refresh_token'; refresh_token = $script:refresh } }
  else { $b = @{ grant_type = 'password'; username = $user; password = $pass } }
  Set-Step 'login'
  $t = Invoke-WugRest -Uri ($root + '/api/v1/token') -Method 'POST' -Form $b -TimeoutSec 30
  $b = $null
  if (-not $t.access_token) { throw 'token response has no access_token' }
  $script:headers = @{ Authorization = ('Bearer ' + $t.access_token) }
  $script:refresh = [string]$t.refresh_token
  $script:tokAt = Get-Date
  $t = $null
}
function Renew-Token {
  try { Get-Token $true; Write-Host 'TOKEN refreshed'; return } catch { Write-Host ('TOKEN refresh failed: ' + $_.Exception.Message) }
  Get-Token $false
  Write-Host 'TOKEN renewed with password'
}
try {
  Get-Token $false
} catch {
  Write-Host ('Login failed: ' + $_.Exception.Message)
  exit 1
}

$script:Calls = 0
$script:authLost = $false
function Test-Unauthorized($err) {
  try { return ([int]$err.Exception.Response.StatusCode -eq 401) } catch { return $false }
}
function Get-Wug([string]$Path, [int]$Timeout = 0) {
  if ($Timeout -le 0) { $Timeout = $CallTimeoutSec }
  if ($script:authLost) { throw 'login lost earlier in this run; not calling WhatsUp again' }
  if (((Get-Date) - $script:tokAt).TotalMinutes -ge $RefreshMinutes) {
    try { Renew-Token } catch { $script:authLost = $true; throw ('cannot log back in: ' + $_.Exception.Message) }
  }
  for ($try = 1; $try -le 2; $try++) {
    $script:Calls++
    if ($script:Calls -gt $MaxCalls) { throw 'Call limit reached; stopping.' }
    Start-Sleep -Seconds 1
    Set-Step ('call ' + $script:Calls + ' ' + ($Path -replace '\?.*$', ''))
    try {
      return Invoke-WugRest -Uri ($root + $Path) -Headers $script:headers -TimeoutSec $Timeout
    } catch {
      if (($try -eq 1) -and (Test-Unauthorized $_)) {
        Write-Host 'TOKEN got 401, logging in again'
        try { Renew-Token } catch { $script:authLost = $true; throw ('cannot log back in: ' + $_.Exception.Message) }
        continue
      }
      throw
    }
  }
}

# WhatsUp returns one summary row per CPU core / memory / disk; the real time points (3 or 10 min apart) are in that row's 'series'.
# Emit one row per point (carrying the core / disk id), so the system sees every point; the system averages cores at the same time.
$ids = @('cpuId','memoryId','diskId','disk')
$vals = @('pollTimeUtc','avgPercent','maxPercent','minPercent')
function Convert-One($src, $idRow) {
  $o = [ordered]@{}
  foreach ($f in $ids) { if ($idRow.PSObject.Properties[$f]) { $o[$f] = $idRow.$f } }
  foreach ($f in $vals) {
    if ($src.PSObject.Properties[$f]) {
      $v = $src.$f
      if ($v -is [DateTime]) { $v = $v.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ') }
      $o[$f] = $v
    }
  }
  return $o
}
function Convert-Rows($rows) {
  $out = New-Object System.Collections.ArrayList
  foreach ($r in @($rows)) {
    $pts = @()
    if ($r.PSObject.Properties['series']) { $pts = @($r.series) }
    if ($pts.Count -gt 0) {
      foreach ($p in $pts) { $o = Convert-One $p $r; if ($o.Contains('pollTimeUtc')) { [void]$out.Add($o) } }
    } else {
      $o = Convert-One $r $r
      if ($o.Contains('pollTimeUtc')) { [void]$out.Add($o) }
    }
  }
  return ,$out.ToArray()
}
function Write-Atomic([string]$Path, [string]$Text) {
  $tmp = $Path + '.tmp'
  [IO.File]::WriteAllText($tmp, $Text, (New-Object System.Text.UTF8Encoding($false)))
  Move-Item -LiteralPath $tmp -Destination $Path -Force
}

# The day to collect: whole local day (default yesterday), asked from WhatsUp in UTC.
$d0 = (Get-Date).Date.AddDays(-$DaysAgo)
$day = $d0.ToString('yyyy-MM-dd')
$startUtc = $d0.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
$endUtc = $d0.AddDays(1).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
$stamp = (Get-Date).ToString('yyyyMMddTHHmmss')
$script:part = 0
$script:batchDir = $null
$script:batchId = $null
$files = New-Object System.Collections.ArrayList
$script:total = 0
# Each batch (at most $BatchSize devices) gets its own folder + manifest, so a crash only loses the batch in progress.
function Open-Batch {
  $script:part++
  $script:batchId = 'whatsup_' + $day + '_' + $stamp + '_p' + $script:part.ToString('000')
  $script:batchDir = Join-Path $OutDir $script:batchId
  New-Item -ItemType Directory -Force -Path $script:batchDir | Out-Null
  $files.Clear()
}
# manifest.json is written last, so the system only ever sees a finished batch.
$script:ok = 0
$script:failed = 0
$script:notfound = 0
$script:nIps = 0
function Close-Batch([bool]$Final) {
  if ($files.Count -eq 0) { return }
  $manifest = [ordered]@{
    source = 'whatsup'
    batch_id = $script:batchId
    schema_version = 1
    collector_version = $ScriptVersion
    collected_at = (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz')
    time_zone = 'UTC'
    range = [ordered]@{ from = $day; to = $day }
    count = $files.Count
    files = $files.ToArray()
    run = [ordered]@{ final = $Final; ips = $script:nIps; ok = $script:ok; failed = $script:failed; notfound = $script:notfound; calls = $script:Calls }
  }
  Write-Atomic (Join-Path $script:batchDir 'manifest.json') ($manifest | ConvertTo-Json -Depth 8 -Compress)
  $script:total += $files.Count
  Write-Host ('BATCH ' + $script:batchId + ' : ' + $files.Count + ' devices')
}

$ips = @(Get-Content -LiteralPath $IpFile | ForEach-Object { $_.Trim() } | Where-Object { $_ -and (-not $_.StartsWith('#')) })
$script:nIps = $ips.Count

# One pass over the whole device list (200 per page, ~30 s a page) builds IP -> device, instead of one search per IP.
# If the list cannot be read, fall back to searching each IP (slower, same result).
$byIp = @{}
$listOk = $false
try {
  $pg = '0'
  $n = 0
  do {
    $x = Get-Wug ('/api/v1/device-groups/-1/devices?view=overview&limit=200&pageId=' + $pg) 180
    foreach ($dv in @($x.data.devices)) { $a = [string]$dv.networkAddress; if ($a -and (-not $byIp.ContainsKey($a))) { $byIp[$a] = $dv } }
    $pg = [string]$x.paging.nextPageId
    $n++
  } while ($pg -and ($n -lt $ListMaxPages))
  $listOk = $true
  Write-Host ('LIST ' + $byIp.Count + ' devices in ' + $n + ' page(s)')
} catch {
  Write-Host ('LIST failed, searching each IP instead: ' + $_.Exception.Message)
}

Open-Batch
foreach ($ip in $ips) {
  $addr = $null
  if ((-not [Net.IPAddress]::TryParse($ip, [ref]$addr)) -or ($addr.AddressFamily -ne 'InterNetwork')) { Write-Host 'SKIP: a line in the IP file is not an IPv4 address'; $script:failed++; continue }
  if ($script:authLost) { $script:failed++; continue }
  try {
    if ($listOk) {
      $dv = $byIp[$ip]
    } else {
      $r = Get-Wug ('/api/v1/device-groups/-1/devices?search=' + $ip + '&view=overview&limit=10')
      $dv = @($r.data.devices | Where-Object { $_.networkAddress -eq $ip })[0]
    }
    if (-not $dv) { Write-Host ('NOT FOUND ' + $ip); $script:notfound++; continue }
    $reports = [ordered]@{}
    foreach ($k in 'cpu-utilization','memory-utilization','disk-utilization') {
      $rows = @()
      $pg = ''
      $n = 0
      do {
        $q = '/api/v1/devices/' + $dv.id + '/reports/' + $k + '?range=custom&rangeStartUtc=' + $startUtc + '&rangeEndUtc=' + $endUtc + '&limit=' + $PageLimit
        if ($pg) { $q += '&pageId=' + $pg }
        $x = Get-Wug $q
        $rows += @($x.data)
        $pg = [string]$x.paging.nextPageId
        $n++
      } while ($pg -and ($n -lt $MaxPages))
      $reports[$k] = Convert-Rows $rows
    }
    # A full batch is closed only when the next device is ready, so the last batch is never empty and always carries the final counts.
    if ($files.Count -ge $BatchSize) { Close-Batch $false; Open-Batch }
    $dev = [ordered]@{ ip = $ip; id = $dv.id; name = [string]$dv.name; reports = $reports }
    $fname = $ip + '.json'
    $final = Join-Path $script:batchDir $fname
    Write-Atomic $final ($dev | ConvertTo-Json -Depth 8 -Compress)
    $item = [ordered]@{ name = $fname; sha256 = (Get-FileHash -LiteralPath $final -Algorithm SHA256).Hash.ToLower(); size = (Get-Item -LiteralPath $final).Length }
    [void]$files.Add($item)
    $script:ok++
    Write-Host ('OK ' + $ip)
  } catch {
    $script:failed++
    Write-Host ('FAILED ' + $ip + ' : ' + $_.Exception.Message)
  }
}
$headers = $null
$script:headers = $null
$script:refresh = $null
$pass = $null
Close-Batch $true
$summary = 'ok=' + $script:ok + ' failed=' + $script:failed + ' notfound=' + $script:notfound + ' of ' + $script:nIps + ' IPs'
if ($script:total -eq 0) {
  Write-Host ('No device was collected (' + $summary + '), so no manifest was written (the system will show the source as stale).')
  exit 1
}
if ($script:authLost) { Write-Host 'WARN: lost the WhatsUp login part way; the rest were counted as failed.' }
if ((($script:ok + $script:failed) -gt 0) -and (($script:failed / ($script:ok + $script:failed)) -gt 0.1)) { Write-Host ('WARN: more than 10% failed (' + $summary + '); the system will show the source as incomplete.') }
Write-Host ('DONE ' + $summary + ', ' + $script:total + ' devices in ' + $script:part + ' batch(es), ' + $script:Calls + ' calls')
} finally { Stop-Watchdog }
