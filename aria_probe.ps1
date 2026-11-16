# ARIA-HOST-PROBE-SCRIPT v8 : Aria Operations (vCOps) read-only probe for ESXi hosts (GET only, plus one token login).
# Runs on the jump host (the only one that reaches vCOps), PowerShell 5.1, after the market closes (15:05 or later).
# 2026-11-04 synced with the version running on the jump host: resourceKind=HostSystem&pageSize=300, start = today 09:00 (+08),
# also fetches the swap statKey, file prefix ariahost_, and dot-sources aria_host_report.ps1 at the end (console Top10 + CSV).
# New in v2: also writes one relay batch folder = manifest.json + one JSON per host (same contract as wug_collect.ps1;
# manifest.json is written last, so the system only ever sees a finished batch). The system (IT war room) only reads these files.
# v3 (review fixes): missing values stay null (never 0); the host list is paged (page=0,1,...) and checked against totalCount;
# the transcript does not record the account, auth source or server error body (login failure = HTTP status only);
# the batch-writing part runs with ErrorActionPreference Stop and any failure (or any host that could not be read) means no manifest.
# vCOps keeps 5-minute points for 28 days, so a run after the close (or a later day with -Day) gets the full 09:00-09:30 window.
# Credentials: aria_account.txt next to this script (line 1 user, line 2 password, line 3 auth source optional). Not in git.
# PS 5.1 trap: strings from Get-Content carry PSPath etc.; always cast to plain string ("$($x)") or vCOps answers 400 Invalid input format.
# Run:  powershell -NoProfile -File aria_probe.ps1 -AriaHost <vCOps address>            (no execution-policy override, no encoded command)
# Self-signed certificate is accepted only for $AriaHost, only inside this PowerShell process.
param(
  [Parameter(Mandatory=$true)][string]$AriaHost,
  [string]$NameLike = 'esxi169-*',
  [string]$Day = '',
  [string]$OutDir = '',
  [int]$PageSize = 300,
  [ValidateSet('Open', '24h')][string]$Mode = 'Open',
  [int]$DelayMs = 1500
)
$ScriptVersion = 'aria_probe 12'
# v12 (2026-11-17): an import once received 44 host files whose name was null ({"name":null,...}). Cause: progress lines
# ("VMs <host> : ...", "FAILED ...") inside the per-host loop were returned into $out together with the host records,
# and every text line became a "host" with no name. Progress lines now use Write-Host, and the batch writer skips any record
# that is not a host with a name: nothing is written for it; it is listed in <log>_skipped.txt (reason + vCOps object id).
# v11 (11-11, NB measured on the jump host: v10 always broke at call #101, then every retry failed too): vCOps closes a
# keep-alive connection after 100 requests and PS 5.1 keeps reusing the dead one. Every call now opens its own connection
# (-DisableKeepAlive), and before a retry the pooled connections to vCOps are closed, so a retry never reuses a broken one.
# v10 (11-10, NB measured on the jump host): GET /resources/{host}/relationships drops the connection on the FIRST call,
# also when slowed down (not a rate limit); v7 never used it and always worked. So v10 does not call relationships at all:
# it lists the VMs (paged) and reads their properties in bulk (GET /resources/properties?resourceId=..), then puts each VM
# under the host named in its summary|parentHost. Host stats are read exactly as in v7. If the VM part fails, the host
# numbers still go out and every host gets vms_error (the system shows the VM section as unknown).
# v9 (11-10, user: "big collections can take 24h, do not pull too much at once"): every vCOps call waits -DelayMs first
# (default 1500 ms) and is retried up to 3 times (3 / 6 / 9 s). The log prints the call number of the first failure, so we
# can tell "blocked after N calls" (rate limit / IPS) from "failed from the start" (connection).
# v8 (11-10): second part of the opening report = "VMs using more than 10% of the host's physical CPU" (team's manual report).
# Per host: GET its CHILD relationships (VirtualMachine), then GET /resources/stats for those VMs in groups of 40
# (cpu|usagemhz_average, rollUpType=MAX, 5 min). Written into the host file as "vms": [{name, resource_id, t, v}].
# The system divides by the host's cpu|totalCapacity_average (already collected). A VM read failure does NOT stop the
# batch (the host numbers still go out); that host gets "vms_error" instead of "vms", and the system shows it as unknown.
# v7 (11-09): -AriaHost is required (no address in the file: it is published in the devkit); the log, the raw JSON and
# the relay batches are written next to this script (user 11-09: all WEBIT3 tools live in one folder, WEBIT3_DevTools).
# v6 (11-09): the team's manual opening report uses the vCOps "CPU Usage (Max)" column = rollUpType=MAX.
# 5-minute AVG flattens short peaks (esxi169-214 on 11-09: AVG 80.66%, MAX 87.48% = manual report), so each host is also
# read once with rollUpType=MAX for cpu|usage_average and mem|usage_average, written as extra keys "<key>:max".
# Older systems ignore keys they do not know, so the batch still imports there (just without the max).
# v5 (11-04): -Mode Open (default) = only $Day 09:00-09:30; -Mode 24h = last 24 hours (see v4 below).
# v4 (11-04 evening): range = last 24 hours up to now (never later than $Day 09:00), so one run after the close feeds every
# report in aria_host_report.ps1: opening peak, hosts >=90% in 24h, per-host 24h resources, cluster-wide CPU, Top10.
# Extra keys cpu|usagemhz_average and cpu|totalCapacity_average (MHz) give the cluster-wide CPU total; missing stays null.
$ts = Get-Date -f yyyyMMdd_HHmmss
$L = Join-Path $PSScriptRoot ('ariahost_' + $ts)
Start-Transcript "$L.log" | Out-Null
if (-not $OutDir) { $OutDir = Join-Path $PSScriptRoot 'aria_relay' }
$H = $AriaHost
[Net.ServicePointManager]::SecurityProtocol = 'Tls12'
[Net.ServicePointManager]::Expect100Continue = $false
# Certificate check as a compiled .NET delegate built in memory with System.Linq.Expressions (2026-11-15): a PowerShell script block here
# runs on a TLS thread with no runspace and fails calls with "an unexpected error occurred on a send" (seen on the jump host 2026-11-14,
# 37 of 43 hosts failed). No Add-Type, so the C# compiler is never started. Accepts a valid chain, or a chain error alone (self-signed)
# for exactly AriaHost; a name mismatch or any other error is refused (the old block accepted any error when the host matched).
$E = [Linq.Expressions.Expression]
$pS = $E::Parameter([object], 's'); $pC = $E::Parameter([Security.Cryptography.X509Certificates.X509Certificate], 'c')
$pH = $E::Parameter([Security.Cryptography.X509Certificates.X509Chain], 'h'); $pE = $E::Parameter([Net.Security.SslPolicyErrors], 'e')
$eqI = [string].GetMethod('Equals', [type[]]@([string], [string], [StringComparison]))
$req = $E::TypeAs($pS, [Net.HttpWebRequest])
$sameHost = $E::Call($eqI, $E::Property($E::Property($req, 'RequestUri'), 'Host'), $E::Constant([string](($H -split ':')[0])), $E::Constant([StringComparison]::OrdinalIgnoreCase))
$isChain = $E::Equal($pE, $E::Constant([Net.Security.SslPolicyErrors]::RemoteCertificateChainErrors))
$body = $E::OrElse($E::Equal($pE, $E::Constant([Net.Security.SslPolicyErrors]::None)), $E::AndAlso($isChain, $E::AndAlso($E::NotEqual($req, $E::Constant($null, [Net.HttpWebRequest])), $sameHost)))
[Net.ServicePointManager]::ServerCertificateValidationCallback = $E::Lambda([Net.Security.RemoteCertificateValidationCallback], $body, [Linq.Expressions.ParameterExpression[]]@($pS, $pC, $pH, $pE)).Compile()
$B = "https://$H/suite-api/api"

# ---- login (the only POST) ----
$acc = Get-Content (Join-Path $PSScriptRoot 'aria_account.txt')
$u = "$($acc[0])".Trim(); $pw = "$($acc[1])"; $src = if ($acc.Count -gt 2) { "$($acc[2])".Trim() } else { '' }
# The transcript never records the account name, the auth source, the password, the token or the server's error body.
$t = $null
foreach ($s2 in @($src, 'LOCAL')) {
  $bd = @{ username = $u; password = $pw }; if ($s2) { $bd.authSource = $s2 }
  try {
    $t = Invoke-RestMethod -Method Post -Uri "$B/auth/token/acquire" -ContentType 'application/json' -Headers @{ Accept = 'application/json' } -Body ($bd | ConvertTo-Json) -DisableKeepAlive -ErrorAction Stop
    'login ok'; break
  } catch {
    $code = $null; $r = $_.Exception.Response; if ($r) { try { $code = [int]$r.StatusCode } catch { $code = $null } }
    'login failed, HTTP status=' + $(if ($code) { $code } else { 'none' })
  }
}
$pw = $null; $bd = $null; $acc = $null
if (-not $t) { Stop-Transcript; return }
$hd = @{ Accept = 'application/json'; Authorization = "vRealizeOpsToken $($t.token)" }
$t = $null

# ---- time range: $Day 09:00 (+08) to now (or end of that day for a past day) ----
$tz8 = New-TimeSpan -Hours 8
if (-not $Day) { $Day = [DateTimeOffset]::UtcNow.ToOffset($tz8).ToString('yyyy-MM-dd') }
$dd = [DateTime]::ParseExact($Day, 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
$d0 = New-Object DateTimeOffset($dd.Year, $dd.Month, $dd.Day, 9, 0, 0, $tz8)
$open0 = $d0.ToUnixTimeMilliseconds()                    # 09:00 of $Day (opening window start, used by the report)
$dayEnd = $d0.AddHours(15).ToUnixTimeMilliseconds()     # 24:00 of $Day
$e2 = [Math]::Min([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds(), $dayEnd)
$b2 = [Math]::Min($open0, $e2 - 86400000)                # last 24h, never later than 09:00 so the opening window is always inside
if ($Mode -eq 'Open') {   # opening only: 09:00-09:30
  if ($e2 -le $open0) { "$Day 09:00 (+08) has not come yet; nothing to fetch in -Mode Open."; Stop-Transcript; return }
  $b2 = $open0; $e2 = [Math]::Min($e2, $open0 + 1800000)
}
if ($e2 -le $open0) { "Note: $Day 09:00 (+08) has not come yet; the opening report will be empty, the 24h reports still run." }

# ---- hosts + stats (GET only) ----
$script:calls = 0; $script:firstFail = $null
function Call([string]$Uri) {
  for ($t = 0; $t -le 3; $t++) {
    Start-Sleep -Milliseconds $DelayMs
    $script:calls++
    try { return Invoke-RestMethod -Uri $Uri -Headers $hd -DisableKeepAlive }
    catch {
      if ($null -eq $script:firstFail) { $script:firstFail = $script:calls; Write-Host ("first failure at call #" + $script:calls + ": " + $_.Exception.Message) }
      if ($t -eq 3) { throw }
      try { [void][Net.ServicePointManager]::FindServicePoint([Uri]$Uri).CloseConnectionGroup('') } catch { Write-Host ('could not reset the connection: ' + $_.Exception.Message) }
      Write-Host ("retry " + ($t + 1) + " of 3 (call #" + $script:calls + "): " + $_.Exception.Message); Start-Sleep -Seconds (3 * ($t + 1))
    }
  }
}
$keys = @('cpu|usage_average', 'mem|usage_average', 'mem|swapped_average', 'cpu|usagemhz_average', 'cpu|totalCapacity_average')
# Page through the whole list (page=0,1,...) so a large inventory is never cut at one page.
$hosts = @(); $pg = 0; $listedTotal = $null
do {
  $rr = Call "$B/resources?resourceKind=HostSystem&page=$pg&pageSize=$PageSize"
  $chunk = @($rr.resourceList)
  $hosts += $chunk
  if ($rr.pageInfo -and ($null -ne $rr.pageInfo.totalCount)) { $listedTotal = [int]$rr.pageInfo.totalCount }
  $pg++
} while (($chunk.Count -ge $PageSize) -and ($pg -lt 100) -and (($null -eq $listedTotal) -or ($hosts.Count -lt $listedTotal)))
"HostSystem listed=$($hosts.Count) total=$listedTotal pages=$pg"
if (($null -ne $listedTotal) -and ($hosts.Count -lt $listedTotal)) { "Stopped: vCOps says $listedTotal hosts but only $($hosts.Count) came back."; Stop-Transcript; return }
$hosts = @($hosts | Where-Object { $_.resourceKey.name -like $NameLike })
"in scope ($NameLike)=$($hosts.Count)"
$sk = ($keys | ForEach-Object { 'statKey=' + [Uri]::EscapeDataString($_) }) -join '&'
$maxKeys = @('cpu|usage_average', 'mem|usage_average')
$skMax = ($maxKeys | ForEach-Object { 'statKey=' + [Uri]::EscapeDataString($_) }) -join '&'
$skVm = 'statKey=' + [Uri]::EscapeDataString('cpu|usagemhz_average')
$script:fetchFailed = 0

# ---- VMs per host from the VM properties (v10: no relationships call) ----
$short = { param($n) ("$n".Trim().ToLower() -split '[.]')[0] }
$inScope = @{}; foreach ($v in $hosts) { $inScope[(& $short $v.resourceKey.name)] = @() }
$vmMapErr = $null
try {
  $vmAll = @(); $pg = 0; $vmTotal = $null
  do {
    $rr = Call "$B/resources?resourceKind=VirtualMachine&page=$pg&pageSize=500"
    $chunk = @($rr.resourceList); $vmAll += $chunk
    if ($rr.pageInfo -and ($null -ne $rr.pageInfo.totalCount)) { $vmTotal = [int]$rr.pageInfo.totalCount }
    $pg++
  # keep paging while vCOps says there are more (a server that caps pageSize lower must not stop us at page 1)
  } while (($chunk.Count -gt 0) -and ($pg -lt 100) -and ((($null -eq $vmTotal) -and ($chunk.Count -ge 500)) -or (($null -ne $vmTotal) -and ($vmAll.Count -lt $vmTotal))))
  if (($null -ne $vmTotal) -and ($vmAll.Count -lt $vmTotal)) { throw "vCOps says $vmTotal VMs but only $($vmAll.Count) came back" }
  $vmName = @{}; foreach ($x in $vmAll) { $vmName[[string]$x.identifier] = [string]$x.resourceKey.name }
  $ids = @($vmAll | ForEach-Object { [string]$_.identifier })
  $placed = 0
  for ($k = 0; $k -lt $ids.Count; $k += 50) {
    $q = ($ids[$k..([Math]::Min($k + 50, $ids.Count) - 1)] | ForEach-Object { 'resourceId=' + $_ }) -join '&'
    $res = Call "$B/resources/properties?$q"
    if (-not ($res -and $res.resourcePropertiesList)) { throw 'bulk properties call has no resourcePropertiesList' }
    foreach ($item in @($res.resourcePropertiesList)) {
      $ph = $null
      foreach ($pp in @($item.property)) { if ($pp -and ([string]$pp.name -eq 'summary|parentHost')) { $ph = [string]$pp.value; break } }
      $hk = & $short $ph
      if ($ph -and $inScope.ContainsKey($hk)) { $inScope[$hk] += [string]$item.resourceId; $placed++ }
    }
  }
  "VMs listed=$($vmAll.Count) placed under in-scope hosts=$placed"
} catch { $vmMapErr = 'VM list failed'; "FAILED (vm list) : $($_.Exception.Message)" }

$out = foreach ($v in $hosts) {
  Start-Sleep 1
  $q = "resourceId=$($v.identifier)&$sk&begin=$b2&end=$e2&rollUpType=AVG&intervalType=MINUTES&intervalQuantifier=5"
  $st = $null
  try { $st = Call "$B/resources/stats?$q" } catch { $script:fetchFailed++; Write-Host "FAILED $($v.resourceKey.name) : $($_.Exception.Message)" }
  $qm = "resourceId=$($v.identifier)&$skMax&begin=$b2&end=$e2&rollUpType=MAX&intervalType=MINUTES&intervalQuantifier=5"
  $stm = $null
  try { $stm = Call "$B/resources/stats?$qm" } catch { $script:fetchFailed++; Write-Host "FAILED (max) $($v.resourceKey.name) : $($_.Exception.Message)" }
  $vmStats = $null; $vmErr = $null
  if ($vmMapErr) { $vmErr = $vmMapErr }
  else {
    try {
      $vmIds = @($inScope[(& $short $v.resourceKey.name)])
      $vmStats = New-Object System.Collections.ArrayList
      for ($k = 0; $k -lt $vmIds.Count; $k += 40) {
        $part = @($vmIds[$k..([Math]::Min($k + 39, $vmIds.Count - 1))])
        $idq = ($part | ForEach-Object { 'resourceId=' + $_ }) -join '&'
        $sv = Call "$B/resources/stats?$idq&$skVm&begin=$b2&end=$e2&rollUpType=MAX&intervalType=MINUTES&intervalQuantifier=5"
        foreach ($val in @($sv.values)) {
          $rid = [string]$val.resourceId
          [void]$vmStats.Add(@{ name = $vmName[$rid]; id = $rid; stat = @($val.'stat-list'.stat)[0] })
        }
      }
      Write-Host "VMs $($v.resourceKey.name) : $($vmIds.Count) placed, $($vmStats.Count) with stats"
    } catch { $vmStats = $null; $vmErr = 'VM read failed'; Write-Host "FAILED (vms) $($v.resourceKey.name) : $($_.Exception.Message)" }
  }
  @{ name = [string]$v.resourceKey.name; id = [string]$v.identifier; stats = $st; statsMax = $stm; vms = $vmStats; vmErr = $vmErr }
}
"vCOps calls=$($script:calls) first failure at call #$(if ($null -eq $script:firstFail) { 'none' } else { $script:firstFail }) (delay $DelayMs ms)"
$hd = $null
$f = $L + '.json'
$out | ConvertTo-Json -Depth 12 | Set-Content $f -Encoding UTF8
"saved $f hosts=$($hosts.Count)"

# ---- relay batch: one JSON per host + manifest.json last ----
# Any failure in this section (or any host whose stats could not be read) means no manifest: the system never sees a half batch.
# vCOps keeps 28 days, so just run it again later (or with -Day for a past day).
$ErrorActionPreference = 'Stop'
$batchOk = $true
function Write-Atomic([string]$Path, [string]$Text) {
  $tmp = $Path + '.tmp'
  [IO.File]::WriteAllText($tmp, $Text, (New-Object System.Text.UTF8Encoding($false)))
  Move-Item -LiteralPath $tmp -Destination $Path -Force
}
$batchId = 'ariahost_' + $Day + '_' + (Get-Date -f yyyyMMddTHHmmss)
$bdir = Join-Path $OutDir $batchId
$files = New-Object System.Collections.ArrayList
$used = @{}
$skipLog = $L + '_skipped.txt'
$skipped = 0
function Skip-Record($rec, [string]$why) {
  $script:skipped++
  $rid = ''; $kind = ''
  if ($rec -is [System.Collections.IDictionary]) { $rid = [string]$rec['id']; $kind = 'host record' }
  else { $kind = 'stray text line (not a host): ' + ([string]$rec).Substring(0, [Math]::Min(80, ([string]$rec).Length)) }
  Add-Content -LiteralPath $skipLog -Value ((Get-Date -Format s) + ' SKIP ' + $kind + ' | vCOps object id=' + $(if ($rid) { $rid } else { '(none)' }) + ' | reason=' + $why + ' | nothing written for it')
}
try {
New-Item -ItemType Directory -Force -Path $bdir | Out-Null
foreach ($o in @($out)) {
  if ($o -isnot [System.Collections.IDictionary]) { Skip-Record $o 'result item is not a host record'; continue }
  if ([string]::IsNullOrWhiteSpace([string]$o['name'])) { Skip-Record $o 'host name is empty or missing'; continue }
  $metrics = [ordered]@{}
  $list = @()
  if ($o.stats -and $o.stats.values) { $list = @($o.stats.values[0].'stat-list'.stat) }
  if ($o.statsMax -and $o.statsMax.values) {
    # MAX series get the suffix ':max' so they never overwrite the AVG series of the same key.
    foreach ($s in @($o.statsMax.values[0].'stat-list'.stat)) { if ($maxKeys -contains [string]$s.statKey.key) { $list += [pscustomobject]@{ statKey = [pscustomobject]@{ key = ([string]$s.statKey.key + ':max') }; timestamps = $s.timestamps; data = $s.data } } }
  }
  foreach ($s in $list) {
    $k = [string]$s.statKey.key
    if (($keys -notcontains $k) -and (($maxKeys | ForEach-Object { $_ + ':max' }) -notcontains $k)) { continue }
    # Missing values stay null (never 0): casting $null to a number would turn a gap into a real 0 point.
    $tt = New-Object System.Collections.ArrayList; $vv = New-Object System.Collections.ArrayList
    $ta = @($s.timestamps); $da = @($s.data)
    if ($ta.Count -ne $da.Count) { throw ('timestamps and data differ in length for ' + $o.name) }
    for ($j = 0; $j -lt $ta.Count; $j++) {
      [void]$tt.Add([int64]$ta[$j])
      $x = $da[$j]
      if (($null -eq $x) -or ("$x" -eq '') -or ("$x" -eq 'NaN')) { [void]$vv.Add($null) } else { [void]$vv.Add([double]$x) }
    }
    $metrics[$k] = [ordered]@{ t = $tt.ToArray(); v = $vv.ToArray() }
  }
  $dev = [ordered]@{ name = $o.name; resource_id = $o.id; metrics = $metrics }
  if ($null -ne $o.vms) {
    $vl = New-Object System.Collections.ArrayList
    foreach ($w in @($o.vms)) {
      if (-not $w.name) { continue }
      $tt = New-Object System.Collections.ArrayList; $vv = New-Object System.Collections.ArrayList
      $ta = @(); $da = @(); if ($w.stat) { $ta = @($w.stat.timestamps); $da = @($w.stat.data) }
      if ($ta.Count -ne $da.Count) { throw ('VM timestamps and data differ in length for ' + $w.name) }
      for ($j = 0; $j -lt $ta.Count; $j++) {
        [void]$tt.Add([int64]$ta[$j])
        $x = $da[$j]
        if (($null -eq $x) -or ("$x" -eq '') -or ("$x" -eq 'NaN')) { [void]$vv.Add($null) } else { [void]$vv.Add([double]$x) }
      }
      [void]$vl.Add([ordered]@{ name = $w.name; resource_id = $w.id; t = $tt.ToArray(); v = $vv.ToArray() })
    }
    $dev.vms = $vl.ToArray()
  } elseif ($o.vmErr) {
    $dev.vms_error = $o.vmErr
  }
  $base = ($o.name -replace '[^A-Za-z0-9._-]', '_').TrimStart('.', '_', '-')
  if (-not $base) { $base = 'host' }
  if ($base.Length -gt 100) { $base = $base.Substring(0, 100) }
  $fname = $base + '.json'; $i = 1
  while ($used.ContainsKey($fname.ToLower())) { $i++; $fname = $base + '_' + $i + '.json' }
  $used[$fname.ToLower()] = 1
  $final = Join-Path $bdir $fname
  Write-Atomic $final (ConvertTo-Json -InputObject $dev -Depth 8 -Compress)
  [void]$files.Add([ordered]@{ name = $fname; sha256 = (Get-FileHash -LiteralPath $final -Algorithm SHA256).Hash.ToLower(); size = (Get-Item -LiteralPath $final).Length })
}
} catch {
  $batchOk = $false
  'Stopped writing the batch: ' + $_.Exception.Message + ' (no manifest was written; run it again).'
}
$ErrorActionPreference = 'Continue'
if ($skipped -gt 0) { "WARN skipped $skipped record(s) that have no usable host name; see $skipLog (nothing was written for them)" }
if (-not $batchOk) {
  'Batch not finished, so no manifest.'
} elseif ($script:fetchFailed -gt 0) {
  "No manifest: $($script:fetchFailed) host(s) failed to read; run it again later (vCOps keeps 28 days)."
} elseif ($files.Count -eq 0) {
  'No host in scope, so no manifest was written (the system will ignore this empty folder).'
} else {
  $manifest = [ordered]@{
    source = 'aria_host'
    batch_id = $batchId
    schema_version = 1
    collector_version = $ScriptVersion
    collected_at = [DateTimeOffset]::UtcNow.ToOffset($tz8).ToString('yyyy-MM-ddTHH:mm:sszzz')
    time_zone = 'Asia/Taipei'
    range = [ordered]@{ from = $Day; to = $Day }
    scope = $NameLike
    listed_total = $listedTotal
    data_until = [DateTimeOffset]::FromUnixTimeMilliseconds($e2).ToOffset($tz8).ToString('yyyy-MM-ddTHH:mm:sszzz')
    count = $files.Count
    files = $files.ToArray()
  }
  try {
    $ErrorActionPreference = 'Stop'
    Write-Atomic (Join-Path $bdir 'manifest.json') (ConvertTo-Json -InputObject $manifest -Depth 8 -Compress)
    "BATCH $batchId : $($files.Count) hosts -> $bdir"
  } catch { 'Stopped: manifest could not be written: ' + $_.Exception.Message }
  $ErrorActionPreference = 'Continue'
}

# ---- console report (Top10 + all + CSV), same as before ----
$rep = Join-Path $PSScriptRoot 'aria_host_report.ps1'
if (Test-Path -LiteralPath $rep) { . $rep }
Stop-Transcript
