# WUG-LASTPOLL-COLLECT-SCRIPT v8 : WhatsUp Gold read-only "last polled" values per group, for the WEBIT3 daily check (GET only, plus one token login)
# What it does: asks WhatsUp the same thing the morning checker reads on screen with time = "last polled":
#   CPU, memory and disk utilization of every device in a few WhatsUp groups (sub groups included), plus the member list of each group.
# One run = 1 login + 1 group list + per group (members + cpu + memory + disk), every list read page by page until there is no next page.
# Writes one batch folder OutDir/wuglp_<time>/ with snap.json, then manifest.json last (the system only ever sees a finished batch).
# v2 (first real run): every group has its own call budget, sized from its member count (a big group can no longer use up the calls of the
#   others); a report that hits its page cap keeps the pages it read and is marked partial (pages read, rows kept), not failed;
#   reports are sorted by percent, highest first (WhatsUp default), so a partial report still has the highest values.
#   Members: when the one-shot member list fails (HTTP 500 on very big groups), it pages with a smaller page size, then reads every sub group
#   one by one (no hierarchy) and joins them. Group names are compared after removing spaces and turning full-width brackets into
#   half-width ones; configured names that match nothing are listed with similar WhatsUp names.
# v3 (second real run): the group CPU report stops at about 100 devices (150 rows) with no next page, whatever the group size.
#   When one CPU query returns -CpuCapDevices devices or more, it is split: the group's own devices (no hierarchy) plus every
#   child group (again split when needed), joined by device; a leaf that still hits the cap is PARTIAL "WhatsUp report cap", never
#   complete. Any report covering fewer than half of the members (no next page) gets a low coverage note.
#   Swap probe: the old monitor list call used view=details, which WhatsUp refuses (HTTP 400); now view=basic, plus the device
#   memory report (lastPolled) to see whether swap shows up there.
# Anything that fails (group not found, a name that matches two groups, a report call that errors before the first page) is written
# into that group / metric as ok = false with the reason; the other groups still run. The system marks that batch not complete
# and the daily check shows "no data" for the rows that need it, never "normal".
# Group names: -Groups, or -GroupsFile (one name per line, UTF-8, lines starting with # are skipped), or wug_lp_groups.txt next to this
#   script, or the built-in default. A name is the group's own name (must match exactly one group) or its full path with ' > ' between levels.
# -Base: the WhatsUp address with the host name that is in its certificate (an IP gives an SSL trust error). When -Base is not given,
#   the line base=https://<host>:<port> in wug_account.txt is used.
# -SaveRaw     also writes every raw reply to RawDir (for the first run: compare with the WhatsUp screen). Off by default.
# -ProbeSwap   also reads the performance monitor list and the memory report of one device (-ProbeDeviceId, default: a device with a
#              swap memory row, else the first member) into RawDir, to see whether Linux_SWAP has a value over REST. Not parsed. Off by default.
# v8 (11-15 20:20, first v7 run): even 400 pages gave the same 1189 devices - WhatsUp keeps handing out new page ids whose
#   rows repeat what was already read. A page that adds no new row twice in a row now ends the list as read to the end
#   ("pages repeat"); the CPU device-cap split and the one-by-one fill then run as designed.
#   Only the CPU report (the split one) uses this rule, because a too-early stop there is made good by the one-by-one fill;
#   group lists, members, memory and disk read pages exactly as before. Row fingerprint = name=value of every field, ordinal.
# v7 (11-15 evening, first v6 run): the CPU report of 1-xServer (1375 members) stopped at "page cap 33" - the cap was sized from the
#   member count, but that report reads child groups too (a device shows up under several of them), so it has more rows than members.
#   The split (CPU) report now gets MaxPagesPerReport pages per call; the group call budget still bounds the whole group.
# v6 (11-15): a big leaf group (1375 members) left more devices to fill than CpuFillMax 400, so CPU stayed PARTIAL every hour.
#   CpuFillMax now 2000; per-device calls are paced (FillPauseMs, default 100 ms before each call, so well under 10 calls a second);
#   a call with no HTTP status (time out, connection or TLS error) or a 5xx is retried once after 2 s (4xx and our own caps are not);
#   the run time box is 1500 s (the task runs every 30 minutes); the PARTIAL message says the real reason and how many were filled.
#   Retries get a quarter of the fill budget on top: when more than a quarter of the devices need a retry, WhatsUp itself is unwell,
#   so the run stops and reports instead of pushing harder.
# v4: optional device status check for a short list of devices (the 1.5 Banqiao rows): wug_lp_devices.txt next to this script
#   (or -DevicesFile), one IP or WhatsUp device id per line, optional ',name'. Per device: find it (IP: device search, exact address;
#   id: device overview), its status (worst / best state, down monitors) and its last poll time (device ping report, lastPolled).
#   Written as devices.json in the same batch; a device that cannot be found or read is marked ok = false with the reason.
# Runs every 30 minutes on the relay Windows host (PowerShell 5.1). It only reads WhatsUp; it never connects to any host.
# Example:  powershell -NoProfile -File wug_lastpoll_collect.ps1 -Base https://wug-host.example.test:9644 -OutDir wug_lp_out
param(
  [string]$Base = '',
  [string]$OutDir = 'wug_lp_out',
  [string[]]$Groups = @(),
  [string]$GroupsFile = '',
  [string]$RawDir = 'wug_lp_raw',
  [switch]$SaveRaw,
  [switch]$ProbeSwap,
  [string]$ProbeDeviceId = '',
  [int]$TimeBoxSeconds = 1500,
  [int]$MaxCalls = 3000,
  [int]$GroupMaxCalls = 0,
  [int]$MaxPagesPerReport = 400,
  [int]$MemberPageSize = 200,
  [int]$CpuCapDevices = 100,
  [int]$CpuFillMax = 2000,
  [int]$FillPauseMs = 100,
  [string]$DevicesFile = ''
)
$ErrorActionPreference = 'Stop'
$ScriptVersion = 'wug_lastpoll_collect 8'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Sep = ' > '
$RowsPerPage = 50

# Default group names, built from code points so this file stays plain ASCII (PowerShell 5.1 reads BOM-less files as ANSI).
function U([int[]]$c) { return (-join ([char[]]$c)) }
if ((@($Groups).Count -eq 0) -and (-not $GroupsFile)) {
  $gf = Join-Path $PSScriptRoot 'wug_lp_groups.txt'
  if (Test-Path -LiteralPath $gf) { $GroupsFile = $gf }
}
if ($GroupsFile) {
  $Groups = @([IO.File]::ReadAllLines((Resolve-Path -LiteralPath $GroupsFile).Path, (New-Object System.Text.UTF8Encoding($false))) | ForEach-Object { $_.Trim() } | Where-Object { $_ -and (-not $_.StartsWith('#')) })
}
if (@($Groups).Count -eq 0) {
  $Groups = @(
    ('1-xServer (' + (U 0x91CD,0x8981,0x7CFB,0x7D71) + '/' + (U 0x4EA4,0x6613) + ')'),
    'Server',
    ('5-Infra(' + (U 0x4E3B,0x6A5F,0x985E) + '_Windows/Linux)')
  )
}

# Credentials: the same wug_account.txt as wug_collect.ps1 (username=... / password=..., optional base=...), readable only by this account.
# Nothing secret is written out.
$cfg = Join-Path $PSScriptRoot 'wug_account.txt'
if (-not (Test-Path -LiteralPath $cfg)) { Write-Host ('Missing ' + $cfg + ' - create it with Notepad: two lines username=... and password=..., readable only by this account; then run again.'); exit 1 }
$kv = @{}
foreach ($l in Get-Content -LiteralPath $cfg) { $i = $l.IndexOf('='); if ($i -gt 0) { $kv[$l.Substring(0, $i).Trim()] = $l.Substring($i + 1) } }
$user = $kv['username']; $pass = $kv['password']
if ((-not $Base) -and $kv['base']) { $Base = $kv['base'].Trim() }
$kv = $null
if (-not $user -or -not $pass) { Write-Host ('username or password is empty in ' + $cfg); exit 1 }
if (-not $Base) { Write-Host 'No WhatsUp address: pass -Base https://<WhatsUp host name>:<port> or add a line base=https://<host name>:<port> to wug_account.txt.'; exit 1 }
if ($Base -notmatch '^https://[A-Za-z0-9][A-Za-z0-9.-]{0,251}(:[0-9]{1,5})?/?$') { throw 'Base must be an https:// URL with a host name (plain http is refused because the password and token would travel unencrypted).' }
$root = $Base.TrimEnd('/')
if ($root -match '^https://[0-9.]+(:[0-9]+)?$') { Write-Host 'WARNING: -Base is an IP address; the WhatsUp certificate is issued to its host name, so use the host name if the login fails with an SSL / trust error.' }

function Write-Atomic([string]$Path, [string]$Text) {
  $tmp = $Path + '.tmp'
  [IO.File]::WriteAllText($tmp, $Text, (New-Object System.Text.UTF8Encoding($false)))
  Move-Item -LiteralPath $tmp -Destination $Path -Force
}
function Short([string]$s) { if ($s.Length -gt 300) { return $s.Substring(0, 300) } return $s }
# Group name compare key: no spaces (half-width, full-width, no-break, tab), full-width brackets / slash / underscore as half-width, lower case.
function Norm([string]$s) {
  if ($null -eq $s) { return '' }
  $t = $s.Replace([string][char]0x3000, ' ').Replace([string][char]0x00A0, ' ').Replace([string][char]9, ' ')
  $t = $t.Replace([string][char]0xFF08, '(').Replace([string][char]0xFF09, ')').Replace([string][char]0xFF0F, '/').Replace([string][char]0xFF3F, '_')
  return ($t -replace '[ ]+', '').ToLowerInvariant()
}

$body = @{ grant_type = 'password'; username = $user; password = $pass }
try {
  $tok = Invoke-RestMethod -Method Post -Uri ($root + '/api/v1/token') -Body $body -TimeoutSec 30
} catch {
  Write-Host ('Login failed: ' + $_.Exception.Message)
  exit 1
}
$body = $null
$pass = $null
$headers = @{ Authorization = ('Bearer ' + $tok.access_token) }
$tok = $null

$startedAt = Get-Date
$sw = [Diagnostics.Stopwatch]::StartNew()
$stamp = $startedAt.ToString('yyyyMMddTHHmmss')
$script:Calls = 0
$script:GCalls = 0
$script:GBudget = 0
$script:RawN = 0
if ($SaveRaw -or $ProbeSwap) { New-Item -ItemType Directory -Force -Path $RawDir | Out-Null }
function Save-Raw([string]$Label, $Obj) {
  $script:RawN++
  $f = Join-Path $RawDir ('raw_' + $stamp + '_' + $script:RawN.ToString('0000') + '_' + $Label + '.json')
  Write-Atomic $f ($Obj | ConvertTo-Json -Depth 12 -Compress)
}
# One GET. Three limits: the whole run (MaxCalls), the current group (its budget), and the time box.
function Get-Wug([string]$Path, [string]$Label) {
  if ($script:Calls -ge $MaxCalls) { throw ('run call cap ' + $MaxCalls + ' reached') }
  if (($script:GBudget -gt 0) -and ($script:GCalls -ge $script:GBudget)) { throw ('group call budget ' + $script:GBudget + ' reached') }
  if ($sw.Elapsed.TotalSeconds -gt $TimeBoxSeconds) { throw ('time box ' + $TimeBoxSeconds + ' s reached') }
  $script:Calls++
  $script:GCalls++
  $x = Invoke-RestMethod -Uri ($root + $Path) -Headers $headers -TimeoutSec 120
  if ($SaveRaw) { Save-Raw $Label $x }
  return $x
}
function Http-Code($e) {
  # The HTTP status of a failed call, 0 when there is none (time out, name error); only used for the message.
  $code = 0
  try { if ($e.Exception.Response) { $code = [int]$e.Exception.Response.StatusCode } } catch { $code = 0 }
  return $code
}
# Pages of one list until there is no next page, at most MaxP pages. Never throws: returns what it read.
#   complete = every page read; partial = some pages read, then a cap / error stopped it (rows kept); pages = 0 with error = nothing read.
# Inner = '' when data itself is the list (reports), else the property under data (devices, groups).
function Get-Pages([string]$Path, [string]$Inner, [string]$Label, [int]$MaxP, [switch]$StopOnRepeat) {
  $res = @{ rows = (New-Object System.Collections.ArrayList); pages = 0; complete = $false; error = ''; code = 0 }
  $pg = ''
  $seen = @{}
  $rowSeen = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
  $dry = 0
  while ($true) {
    if ($res.pages -ge $MaxP) { $res.error = 'page cap ' + $MaxP + ' reached, more pages left'; break }
    $q = $Path
    if ($pg) { $q += '&pageId=' + [Uri]::EscapeDataString($pg) }
    try { $x = Get-Wug $q $Label } catch { $res.error = Short $_.Exception.Message; $res.code = Http-Code $_; break }
    $res.pages++
    if (-not $x.PSObject.Properties['data']) { $res.error = 'response has no data'; break }
    if ($Inner) {
      if ((-not $x.data) -or (-not $x.data.PSObject.Properties[$Inner])) { $res.error = 'response has no data.' + $Inner; break }
      $list = $x.data.$Inner
    } else { $list = $x.data }
    $fresh = 0
    foreach ($r in @($list)) {
      if (-not $r) { continue }
      if ($StopOnRepeat) {
        $rk = (@($r.PSObject.Properties | ForEach-Object { $_.Name + '=' + [string]$_.Value }) -join [char]31)
        if (-not $rowSeen.Add($rk)) { continue }
      }
      $fresh++
      [void]$res.rows.Add($r)
    }
    $nx = ''
    if ($x.PSObject.Properties['paging'] -and $x.paging) { $nx = [string]$x.paging.nextPageId }
    if (-not $nx) { $res.complete = $true; break }
    # v8: WhatsUp can keep giving new page ids for pages that only repeat rows already read; two such pages in a row = the end.
    if ($StopOnRepeat -and ($fresh -eq 0) -and ($res.pages -gt 1)) { $dry++ } else { $dry = 0 }
    if ($dry -ge 2) { $res.complete = $true; $res.note = 'pages repeat after ' + ($res.pages - 2) + ' pages'; break }
    if ($seen.ContainsKey($nx)) { $res.error = 'next page id repeated: ' + $nx; break }
    $seen[$nx] = 1
    $pg = $nx
  }
  return $res
}

# 1) all groups -> full path of each (parent chain), to turn a name into exactly one id.
$groupsOk = $true
$groupsErr = ''
$byId = @{}
$kids = @{}
$gl = Get-Pages '/api/v1/device-groups/-?limit=0' 'groups' 'groups' $MaxPagesPerReport
if (-not $gl.complete) { $groupsOk = $false; $groupsErr = $gl.error }
else {
  foreach ($g in $gl.rows) {
    $byId[[string]$g.id] = @{ name = [string]$g.name; parent = [string]$g.parentGroupId }
    $p = [string]$g.parentGroupId
    if (-not $kids.ContainsKey($p)) { $kids[$p] = New-Object System.Collections.ArrayList }
    [void]$kids[$p].Add([string]$g.id)
  }
  if ($byId.Count -eq 0) { $groupsOk = $false; $groupsErr = 'group list was empty' }
}
function Path-Of([string]$Id) {
  $parts = @()
  $cur = $Id
  $guard = 0
  while ($cur -and $byId.ContainsKey($cur) -and ($guard -lt 50)) {
    $parts = @($byId[$cur].name) + $parts
    $cur = $byId[$cur].parent
    $guard++
  }
  return ($parts -join $Sep)
}
function Tree-Of([string]$Id) {
  $ids = New-Object System.Collections.ArrayList
  $todo = New-Object System.Collections.Queue
  $todo.Enqueue($Id)
  while (($todo.Count -gt 0) -and ($ids.Count -lt 5000)) {
    $c = [string]$todo.Dequeue()
    if ($ids.Contains($c)) { continue }
    [void]$ids.Add($c)
    if ($kids.ContainsKey($c)) { foreach ($k in $kids[$c]) { $todo.Enqueue($k) } }
  }
  return ,$ids.ToArray()
}
$paths = @{}
foreach ($k in $byId.Keys) { $paths[$k] = Path-Of $k }

# Every id-like field the reply has is kept (id, deviceId when present, the cpu/memory/disk item id, monitorId when present).
# avg / max are written exactly as WhatsUp sends them (avgPercent may be a 0-1 fraction); used / size too when present, so the
# system can tell fraction from percent.
function Row([object]$r, [string]$ItemProp) {
  $item = ''
  if ($ItemProp -and $r.PSObject.Properties[$ItemProp]) { $item = [string]$r.$ItemProp }
  $o = [ordered]@{ id = [string]$r.id; name = [string]$r.deviceName; item = $item; poll = [string]$r.pollTimeUtc
                   age = $r.timeFromLastPollSeconds; avg = $r.avgPercent; max = $r.maxPercent }
  if ($r.PSObject.Properties['deviceId']) { $o.device_id = [string]$r.deviceId }
  if ($ItemProp -and $r.PSObject.Properties[$ItemProp + 'Id']) { $o.item_id = [string]$r.($ItemProp + 'Id') }
  if ($r.PSObject.Properties['monitorId']) { $o.monitor_id = [string]$r.monitorId }
  if ($r.PSObject.Properties['avgUsed']) { $o.used = $r.avgUsed }
  if ($r.PSObject.Properties['size']) { $o.size = $r.size }
  return $o
}
# Page caps per report from the member count: cpu one row per device, memory about 2, disk about 6 (first run: memory 1974 rows
# for 1379 members, disk more than 50 pages of 50).
$Reports = @(
  @{ key = 'cpu'; path = 'cpu-utilization'; extra = '&rollupByDevice=true'; item = 'cpu'; per = 1; split = $true },
  @{ key = 'mem'; path = 'memory-utilization'; extra = ''; item = 'memory'; per = 2 },
  @{ key = 'disk'; path = 'disk-utilization'; extra = ''; item = 'disk'; per = 6 }
)
function Page-Cap([int]$Members, [int]$Per) {
  if ($Members -le 0) { return $MaxPagesPerReport }
  $n = [int][Math]::Ceiling(($Members * $Per) / $RowsPerPage) + 5
  if ($n -gt $MaxPagesPerReport) { $n = $MaxPagesPerReport }
  return $n
}
function Dev-Count($Rows) { return @($Rows | ForEach-Object { [string]$_.id } | Sort-Object -Unique).Count }
function Add-Rows($Acc, $Rows, [string]$ItemProp) {
  foreach ($r in $Rows) {
    # Only an exact repeat (same device, item, poll time and value: the same row seen through a parent and a child group) is dropped.
    $k = [string]$r.id + '|' + [string]$r.$ItemProp + '|' + [string]$r.($ItemProp + 'Id') + '|' + [string]$r.pollTimeUtc + '|' + [string]$r.avgPercent
    if (-not $Acc.ContainsKey($k)) { $Acc[$k] = $r }
  }
}
# One report for one group, split when WhatsUp stops at its device cap (CPU): the group's own devices plus each child group.
# St: pages, capped (group paths still at the cap), error (first error), broken (a call stopped early).
function Read-Split([string]$Gid, $Rp, [string]$Label, [int]$MaxP, $Acc, $St, [int]$Depth) {
  $base = '/api/v1/device-groups/' + $Gid + '/devices/reports/' + $Rp.path + '?range=lastPolled&returnHierarchy='
  $r = Get-Pages ($base + 'true' + $Rp.extra + '&limit=0') '' $Label $MaxP -StopOnRepeat:([bool]$Rp.split)
  $St.pages += $r.pages
  Add-Rows $Acc $r.rows $Rp.item
  if (-not $r.complete) {
    $St.broken = $true
    if (-not $St.error) { $St.error = $r.error; $St.code = $r.code }
    return
  }
  if ((-not $Rp.split) -or ((Dev-Count $r.rows) -lt $CpuCapDevices)) { return }
  $ch = @()
  if ($kids.ContainsKey($Gid)) { $ch = @($kids[$Gid]) }
  if (($ch.Count -eq 0) -or ($Depth -ge 12)) { [void]$St.capped.Add($paths[$Gid]); return }
  $d = Get-Pages ($base + 'false' + $Rp.extra + '&limit=0') '' ($Label + '_own') $MaxP -StopOnRepeat:([bool]$Rp.split)
  $St.pages += $d.pages
  Add-Rows $Acc $d.rows $Rp.item
  if (-not $d.complete) { $St.broken = $true; if (-not $St.error) { $St.error = $d.error; $St.code = $d.code } }
  elseif ((Dev-Count $d.rows) -ge $CpuCapDevices) { [void]$St.capped.Add($paths[$Gid] + ' (own devices)') }
  foreach ($c in $ch) { Read-Split $c $Rp ($Label + '_' + $c) $MaxP $Acc $St ($Depth + 1) }
}
function Add-Members($Acc, $Rows) {
  foreach ($d in $Rows) {
    $did = [string]$d.id
    if ((-not $did) -or $Acc.ContainsKey($did)) { continue }
    $mo = [ordered]@{ id = $did; name = [string]$d.name; ip = [string]$d.networkAddress }
    if ($d.PSObject.Properties['hostName']) { $mo.host = [string]$d.hostName }
    $Acc[$did] = $mo
  }
}

$out = New-Object System.Collections.ArrayList
$counts = New-Object System.Collections.ArrayList
$unmatched = New-Object System.Collections.ArrayList
$gi = 0
foreach ($want in $Groups) {
  $gi++
  $script:GCalls = 0
  $script:GBudget = 0
  $grp = [ordered]@{ query = $want; path = $want; id = $null; ok = $false; error = ''; members_via = ''; members = @()
                     metrics = [ordered]@{ cpu = [ordered]@{ ok = $false; partial = $false; pages = 0; error = 'not read'; rows = @() }
                                           mem = [ordered]@{ ok = $false; partial = $false; pages = 0; error = 'not read'; rows = @() }
                                           disk = [ordered]@{ ok = $false; partial = $false; pages = 0; error = 'not read'; rows = @() } } }
  $cnt = [ordered]@{ path = $want; members = 0; cpu = 0; mem = 0; disk = 0; calls = 0; budget = 0 }
  if (-not $groupsOk) { $grp.error = 'group list failed: ' + $groupsErr }
  else {
    $nw = Norm $want
    if ($want.Contains($Sep.Trim())) {
      $hits = @($paths.Keys | Where-Object { $paths[$_] -eq $want })
      if ($hits.Count -eq 0) { $hits = @($paths.Keys | Where-Object { (Norm $paths[$_]) -eq $nw }) }
    } else {
      $hits = @($byId.Keys | Where-Object { $byId[$_].name -eq $want })
      if ($hits.Count -eq 0) { $hits = @($byId.Keys | Where-Object { (Norm $byId[$_].name) -eq $nw }) }
    }
    if ($hits.Count -eq 0) {
      $key = $nw
      $cut = $key.IndexOf('(')
      if ($cut -gt 2) { $key = $key.Substring(0, $cut) }
      $near = @($byId.Keys | Where-Object { (Norm $byId[$_].name).Contains($key) } | ForEach-Object { $byId[$_].name } | Sort-Object -Unique | Select-Object -First 8)
      $grp.error = 'group not found'
      if ($near.Count -gt 0) { $grp.error += ' (similar WhatsUp names: ' + (Short ($near -join ' | ')) + ')' }
      [void]$unmatched.Add($want + ' -> ' + $grp.error)
    }
    elseif ($hits.Count -gt 1) { $grp.error = 'name matches ' + $hits.Count + ' groups, use the full path: ' + (Short ((@($hits | ForEach-Object { $paths[$_] }) | Sort-Object) -join ' | ')) }
    else {
      $id = [string]$hits[0]
      $grp.id = $id
      $grp.path = $paths[$id]
      $cnt.path = $grp.path
      # Members: one shot; if that fails, smaller pages; if that fails too, every sub group on its own (no hierarchy), joined by device id.
      $acc = @{}
      $mr = Get-Pages ('/api/v1/device-groups/' + $id + '/devices?returnHierarchy=true&view=overview&limit=0') 'devices' ('members' + $gi) $MaxPagesPerReport
      $via = 'one list'
      if (-not $mr.complete) {
        $first = $mr.error
        $mr = Get-Pages ('/api/v1/device-groups/' + $id + '/devices?returnHierarchy=true&view=overview&limit=' + $MemberPageSize) 'devices' ('members' + $gi) $MaxPagesPerReport
        $via = 'pages of ' + $MemberPageSize + ' (one list failed: ' + (Short $first) + ')'
      }
      if ($mr.complete) { Add-Members $acc $mr.rows }
      else {
        $tree = Tree-Of $id
        $via = 'sub groups one by one (' + $tree.Count + ' groups; the list with sub groups failed: ' + (Short $mr.error) + ')'
        $subErr = ''
        foreach ($sg in $tree) {
          $sr = Get-Pages ('/api/v1/device-groups/' + $sg + '/devices?returnHierarchy=false&view=overview&limit=' + $MemberPageSize) 'devices' ('members' + $gi + '_' + $sg) $MaxPagesPerReport
          if (-not $sr.complete) { $subErr = 'sub group ' + $paths[$sg] + ': ' + $sr.error; break }
          Add-Members $acc $sr.rows
        }
        if ($subErr) { $grp.error = 'members: ' + (Short $subErr) }
      }
      $grp.members_via = $via
      if (-not $grp.error) {
        $grp.members = @($acc.Values)
        $grp.ok = $true
        $cnt.members = $acc.Count
      }
      # Members failed: the reports are not read (the system cannot use a group without its members); the calls stay for the other groups.
      if (-not $grp.ok) { foreach ($k in @('cpu', 'mem', 'disk')) { $grp.metrics[$k].error = 'not read: members failed' } }
      else {
        # Report budget for this group: the page caps from the member count (or GroupMaxCalls when given).
        $caps = @{}
        $sum = 0
        foreach ($rp in $Reports) {
          if ($rp.split) { $caps[$rp.key] = $MaxPagesPerReport } else { $caps[$rp.key] = Page-Cap $acc.Count $rp.per }
          $sum += $caps[$rp.key]
        }
        $sum += 3 * (Tree-Of $id).Count                                   # CPU split: own devices + each child group
        if ($GroupMaxCalls -gt 0) { $script:GBudget = $GroupMaxCalls } else { $script:GBudget = $script:GCalls + $sum }
        $cnt.budget = $script:GBudget
        foreach ($rp in $Reports) {
          $m = $grp.metrics[$rp.key]
          $racc = @{}
          $st = @{ pages = 0; capped = (New-Object System.Collections.ArrayList); error = ''; code = 0; broken = $false }
          Read-Split $id $rp ($rp.key + $gi) $caps[$rp.key] $racc $st 0
          # v5 (11-14): a leaf group that still hits the CPU cap (e.g. more than 100 devices with no child group) is filled device
          # by device: every member the group reports did not return is asked with its own CPU report (lastPolled). Only when every
          # one of those calls answered does the cap mark go away; any failure keeps the metric PARTIAL. A member with no CPU row
          # (no CPU monitor) stays without a value, the same as in a group report.
          if ($rp.split -and ($st.capped.Count -gt 0) -and (-not $st.broken) -and ($CpuFillMax -gt 0)) {
            $have = @{}
            foreach ($r in $racc.Values) { $have[[string]$r.id] = 1 }
            $miss = @($acc.Keys | Where-Object { -not $have.ContainsKey([string]$_) })
            if ($miss.Count -gt $CpuFillMax) { $st.error = 'report cap: ' + $miss.Count + ' devices to fill, more than CpuFillMax ' + $CpuFillMax }
            if ($miss.Count -le $CpuFillMax) {
              # budget: one call per device plus room for retries (a quarter), unless the caller fixed GroupMaxCalls
              if ($GroupMaxCalls -le 0) { $script:GBudget += $miss.Count + [int][math]::Ceiling($miss.Count / 4) }
              $fillOk = $true
              $filled = 0
              foreach ($did in $miss) {
                $u = '/api/v1/devices/' + $did + '/reports/cpu-utilization?range=lastPolled&rollupByDevice=true&limit=0'
                $x = $null
                if ($FillPauseMs -gt 0) { Start-Sleep -Milliseconds $FillPauseMs }
                try { $x = Get-Wug $u ($rp.key + $gi + '_dev_' + $did) }
                catch {
                  $first = Short $_.Exception.Message
                  $code = Http-Code $_
                  $again = ($first -notmatch 'cap|budget|time box') -and (($code -eq 0) -or ($code -ge 500))
                  if (-not $again) { $fillOk = $false; $st.error = 'per-device cpu: ' + $first; if ($code) { $st.error = 'per-device cpu: HTTP ' + $code + ' ' + $first }; break }
                  Start-Sleep -Seconds 2
                  try { $x = Get-Wug $u ($rp.key + $gi + '_dev_' + $did + '_retry') }
                  catch { $fillOk = $false; $st.error = 'per-device cpu (after retry): ' + (Short $_.Exception.Message); break }
                }
                $st.pages++
                $list = @()
                if ($x.PSObject.Properties['data'] -and $x.data) { $list = @($x.data) }
                $got = 0
                $matched = 0
                foreach ($r in $list) {
                  if (-not $r) { continue }
                  $got++
                  if ((-not $r.PSObject.Properties['id']) -or (-not $r.id)) { $r | Add-Member -NotePropertyName id -NotePropertyValue $did -Force }
                  if ([string]$r.id -ne [string]$did) { continue }
                  $matched++
                  Add-Rows $racc @($r) $rp.item
                }
                # Review 11-14 P2: rows came back but none is this device = the reply is not understood; keep the metric PARTIAL.
                if (($got -gt 0) -and ($matched -eq 0)) { $fillOk = $false; $st.error = 'per-device cpu: reply rows are not for device ' + $did; break }
                $filled++
              }
              $cnt.budget = $script:GBudget
              if ($fillOk) { $st.filled = $miss.Count; $st.capped.Clear(); $st.error = '' }
              elseif ($st.error) { $st.error = $st.error + ' (filled ' + $filled + ' of ' + $miss.Count + ')' }
              $m.filled_one_by_one = $filled
            }
          }
          $rows = New-Object System.Collections.ArrayList
          foreach ($r in $racc.Values) { [void]$rows.Add((Row $r $rp.item)) }
          $m.pages = $st.pages
          $m.rows = @($rows.ToArray())
          $cnt[$rp.key] = $rows.Count
          $devs = Dev-Count $m.rows
          if ((-not $st.broken) -and ($st.capped.Count -eq 0)) {
            $m.ok = $true
            $m.error = ''
            if (($acc.Count -gt 0) -and ($devs -lt ($acc.Count * 0.5))) { $m.note = 'low coverage: ' + $devs + ' devices of ' + $acc.Count + ' members (no next page)' }
          }
          elseif ($rows.Count -gt 0) {
            $m.ok = $false
            $m.partial = $true
            if ($st.error) { $m.error = 'partial: ' + $st.error }
            elseif ($st.capped.Count -gt 0) { $m.error = 'partial: WhatsUp report cap (' + $CpuCapDevices + ' devices per query) in ' + (Short ((@($st.capped) | Sort-Object) -join ' | ')) }
            else { $m.error = 'partial: ' + $st.pages + ' pages read; ' + $st.error }
          }
          else {
            $m.ok = $false
            $m.error = $st.error
            if ($st.code) { $m.error = 'HTTP ' + $st.code + ': ' + $st.error }
          }
        }
      }
    }
  }
  $cnt.calls = $script:GCalls
  [void]$out.Add($grp)
  [void]$counts.Add($cnt)
}
$script:GBudget = 0

# Optional: the monitor list of one device, to see if Linux_SWAP (a custom performance monitor) has a value over REST. Saved raw, not parsed.
$probe = ''
if ($ProbeSwap) {
  $pid2 = $ProbeDeviceId
  if (-not $pid2) {
    foreach ($g in $out) { foreach ($r in @($g.metrics.mem.rows)) { if ((-not $pid2) -and ([string]$r.item -like '*swap*')) { $pid2 = [string]$r.id } } }
  }
  if (-not $pid2) {
    foreach ($g in $out) { if ((-not $pid2) -and (@($g.members).Count -gt 0)) { $pid2 = [string]@($g.members)[0].id } }
  }
  if ($pid2) {
    # view must be id / minimum / basic / status (view=details gave HTTP 400 on the second real run).
    $probe = 'device ' + $pid2 + ':'
    foreach ($pp in @(@{ n = 'monitors'; q = ('/api/v1/devices/' + $pid2 + '/monitors/-?type=performance&view=basic&limit=0') },
                      @{ n = 'memory'; q = ('/api/v1/devices/' + $pid2 + '/reports/memory-utilization?range=lastPolled&limit=0') })) {
      try {
        $x = Get-Wug $pp.q ('probe_' + $pp.n)
        $f = Join-Path $RawDir ('probe_swap_' + $stamp + '_' + $pid2 + '_' + $pp.n + '.json')
        Write-Atomic $f ($x | ConvertTo-Json -Depth 12 -Compress)
        $probe += ' ' + $pp.n + ' saved ' + $f + ';'
      } catch {
        $code = Http-Code $_
        $probe += ' ' + $pp.n + ' failed (' + $(if ($code) { 'HTTP ' + $code } else { 'no HTTP status' }) + ', ' + $pp.q + '): ' + (Short $_.Exception.Message) + ';'
      }
    }
  } else { $probe = 'probe skipped: no device id (no group has members)' }
}

# Optional: status of a few listed devices (1.5 Banqiao rows). Own budget: 4 calls per device + 5.
$devOut = New-Object System.Collections.ArrayList
$devList = @()
if (-not $DevicesFile) {
  $df = Join-Path $PSScriptRoot 'wug_lp_devices.txt'
  if (Test-Path -LiteralPath $df) { $DevicesFile = $df }
}
if ($DevicesFile) {
  $devList = @([IO.File]::ReadAllLines((Resolve-Path -LiteralPath $DevicesFile).Path, (New-Object System.Text.UTF8Encoding($false))) | ForEach-Object { $_.Trim() } | Where-Object { $_ -and (-not $_.StartsWith('#')) })
}
if ($devList.Count -gt 0) {
  $script:GCalls = 0
  $script:GBudget = 4 * $devList.Count + 5
  foreach ($line in $devList) {
    $parts = $line.Split(',')
    $key = $parts[0].Trim()
    $label = ''
    if ($parts.Count -gt 1) { $label = $parts[1].Trim() }
    $o = [ordered]@{ query = $key; label = $label; ip = ''; id = ''; name = ''; ok = $false; error = ''; worst_state = ''; best_state = ''
                     monitors_down = $null; down_monitors = @(); last_change = ''; poll = ''; age = $null; collected_at = '' }
    try {
      $addr = $null
      if ($key.Contains('.') -and [Net.IPAddress]::TryParse($key, [ref]$addr)) {
        $o.ip = $key
        $r = Get-Wug ('/api/v1/device-groups/-1/devices?search=' + $key + '&view=overview&limit=50') 'dev_search'
        $hits = @(@($r.data.devices) | Where-Object { $_ -and ([string]$_.networkAddress -eq $key) })
        if ($hits.Count -eq 0) { throw 'device not found by IP (device search, exact network address)' }
        if ($hits.Count -gt 1) { throw ('IP matches ' + $hits.Count + ' devices: ' + ((@($hits | ForEach-Object { [string]$_.id }) | Sort-Object) -join ' ')) }
        $d = $hits[0]
      } else {
        $x = Get-Wug ('/api/v1/devices/' + [Uri]::EscapeDataString($key) + '?view=overview') 'dev_get'
        $d = $x.data
        if (-not $d) { throw 'device not found by id' }
        $o.ip = [string]$d.networkAddress
      }
      $o.id = [string]$d.id
      $o.name = [string]$d.name
      if ($d.PSObject.Properties['totalActiveMonitorsDown']) { $o.monitors_down = [int]$d.totalActiveMonitorsDown }
      if ($d.PSObject.Properties['downActiveMonitors']) { $o.down_monitors = @(@($d.downActiveMonitors) | Where-Object { $_ } | ForEach-Object { [string]$_.monitorTypeName } | Select-Object -First 10) }
      $st = Get-Wug ('/api/v1/devices/' + $o.id + '/status') 'dev_status'
      $o.worst_state = [string]$st.data.worstState
      $o.best_state = [string]$st.data.bestState
      $o.last_change = [string]$st.data.lastChangeUtc
      $o.collected_at = (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz')
      $o.ok = $true
      # Last poll: the device ping response time report at lastPolled (pollTimeUtc, timeFromLastPollSeconds); the newest row wins.
      try {
        $pr = Get-Wug ('/api/v1/devices/' + $o.id + '/reports/ping-response-time?range=lastPolled&limit=0') 'dev_ping'
        $best = $null
        foreach ($row in @($pr.data)) {
          if ($row -and $row.PSObject.Properties['timeFromLastPollSeconds'] -and ($null -ne $row.timeFromLastPollSeconds)) {
            if (($null -eq $best) -or ($row.timeFromLastPollSeconds -lt $best.timeFromLastPollSeconds)) { $best = $row }
          }
        }
        if ($best) { $o.poll = [string]$best.pollTimeUtc; $o.age = $best.timeFromLastPollSeconds }
        else { $o.error = 'no last poll time in the ping report' }
      } catch { $o.error = 'last poll: ' + (Short $_.Exception.Message) }
    } catch { $o.ok = $false; $o.error = Short $_.Exception.Message }
    [void]$devOut.Add($o)
  }
  $script:GBudget = 0
}
$headers = $null

$batchId = 'wuglp_' + $stamp
$dir = Join-Path $OutDir $batchId
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$snap = [ordered]@{ groups = @($out.ToArray()) }
$fname = 'snap.json'
$final = Join-Path $dir $fname
Write-Atomic $final ($snap | ConvertTo-Json -Depth 8 -Compress)
$item = [ordered]@{ name = $fname; sha256 = (Get-FileHash -LiteralPath $final -Algorithm SHA256).Hash.ToLower(); size = (Get-Item -LiteralPath $final).Length }
$items = @($item)
if ($devOut.Count -gt 0) {
  $dfin = Join-Path $dir 'devices.json'
  Write-Atomic $dfin ([ordered]@{ devices = @($devOut.ToArray()) } | ConvertTo-Json -Depth 6 -Compress)
  $items += [ordered]@{ name = 'devices.json'; sha256 = (Get-FileHash -LiteralPath $dfin -Algorithm SHA256).Hash.ToLower(); size = (Get-Item -LiteralPath $dfin).Length }
}
$day = $startedAt.ToString('yyyy-MM-dd')
$manifest = [ordered]@{
  source = 'whatsup_lastpoll'
  batch_id = $batchId
  schema_version = 1
  collector_version = $ScriptVersion
  collected_at = $startedAt.ToString('yyyy-MM-ddTHH:mm:sszzz')
  time_zone = 'UTC'
  range = [ordered]@{ from = $day; to = $day }
  count = $items.Count
  files = $items
  counts = @($counts.ToArray())
  devices = $devOut.Count
  calls = $script:Calls
  elapsed_s = [int]$sw.Elapsed.TotalSeconds
}
# manifest.json is written last, so the system only ever sees a finished batch.
Write-Atomic (Join-Path $dir 'manifest.json') ($manifest | ConvertTo-Json -Depth 6 -Compress)

# Summary: per group members, rows / pages per report (complete, PARTIAL or FAILED), calls used of its budget.
function State($m) { if ($m.ok) { return 'complete' } if ($m.partial) { return 'PARTIAL' } return 'FAILED' }
$bad = @($out | Where-Object { (-not $_.ok) -or (-not $_.metrics.cpu.ok) -or (-not $_.metrics.mem.ok) -or (-not $_.metrics.disk.ok) }).Count
Write-Host ('BATCH ' + $batchId + ' : groups ' + $out.Count + ', with problems ' + $bad + ', calls ' + $script:Calls + ', seconds ' + [int]$sw.Elapsed.TotalSeconds)
for ($n = 0; $n -lt $out.Count; $n++) {
  $g = $out[$n]
  $c = $counts[$n]
  Write-Host ('  [' + ($n + 1) + '] ' + $g.query + '  =>  ' + $g.path)
  if (-not $g.id) { Write-Host ('      PROBLEM : ' + $g.error); continue }
  $ml = '      members ' + $c.members
  if ($g.members_via) { $ml += ' (via ' + $g.members_via + ')' }
  if (-not $g.ok) { $ml += '  PROBLEM ' + $g.error }
  Write-Host $ml
  foreach ($k in @('cpu', 'mem', 'disk')) {
    $m = $g.metrics[$k]
    $l = '      ' + $k + ' : rows ' + @($m.rows).Count + ', pages ' + $m.pages + ', ' + (State $m)
    if ($m.error) { $l += ' - ' + $m.error }
    if ($m.Contains('note')) { $l += ' - ' + $m.note }
    if ($c.members -gt 0) {
      $devs = @($m.rows | ForEach-Object { $_.id } | Sort-Object -Unique).Count
      $l += ' (devices ' + $devs + ' of ' + $c.members + ' members)'
    }
    Write-Host $l
  }
  Write-Host ('      calls ' + $c.calls + ' of budget ' + $c.budget)
}
if ($unmatched.Count -gt 0) {
  Write-Host ('  Configured groups that match no WhatsUp group (' + $unmatched.Count + '):')
  foreach ($u in $unmatched) { Write-Host ('    - ' + $u) }
}
if ($probe) { Write-Host ('  swap probe: ' + $probe) }
if ($devOut.Count -gt 0) {
  Write-Host ('  Device status (' + $devOut.Count + '):')
  foreach ($o in $devOut) {
    $l = '    - ' + $o.query + ' : '
    if ($o.ok) { $l += 'id ' + $o.id + ' ' + $o.name + ', worst ' + $o.worst_state + ', monitors down ' + $o.monitors_down + ', last poll ' + $o.age + ' s' }
    else { $l += 'FAILED' }
    if ($o.error) { $l += ' - ' + $o.error }
    Write-Host $l
  }
}
exit 0
