# TOOLS-SYNC-SCRIPT v1 (2026-11-15): keep the relay-PC scripts current, no copying by hand.
# Reads manifest.txt from the relay "tools" branch (one line per script: <file name> <sha256>), downloads each script,
# checks its SHA256 and its "...-SCRIPT vN" header, and replaces the local copy only when the relay one is newer.
# The old copy is kept as <name>.bak. Nothing is run; it only replaces files. Log: tools_sync_log.txt next to this script.
# -Install: also registers the hourly task webit3_tools_sync (current user, runs only while logged on, no password stored).
param([switch]$Install, [string]$Repo = 'alienid4/CL_WEBITv3-relay')
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$dir = $PSScriptRoot
$log = Join-Path $dir 'tools_sync_log.txt'
function Say($m) { Write-Host $m; Add-Content -LiteralPath $log -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $m) }
function Ver($path) {
  if (-not (Test-Path -LiteralPath $path)) { return -1 }
  $first = Get-Content -LiteralPath $path -TotalCount 1 -Encoding UTF8
  if ($first -match '-SCRIPT v(\d+)') { return [int]$Matches[1] }
  return 0
}
$raw = "https://raw.githubusercontent.com/$Repo/tools/"

if ($Install) {
  $act = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -WindowStyle Hidden -File "' + (Join-Path $dir 'tools_sync.ps1') + '"') -WorkingDirectory $dir
  $trg = New-ScheduledTaskTrigger -Once -At ((Get-Date).Date.AddHours((Get-Date).Hour + 1).AddMinutes(50)) -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650)
  $usr = [Security.Principal.WindowsIdentity]::GetCurrent().Name
  $pri = New-ScheduledTaskPrincipal -UserId $usr -LogonType Interactive -RunLevel Limited
  Register-ScheduledTask -TaskName 'webit3_tools_sync' -Action $act -Trigger $trg -Principal $pri -Force | Out-Null
  Say ('INSTALL task webit3_tools_sync every hour at :50 as ' + $usr)
}

try { $man = (Invoke-WebRequest ($raw + 'manifest.txt') -UseBasicParsing -TimeoutSec 30).Content }
catch { Say ('FAIL manifest: ' + $_.Exception.Message); exit 1 }
if ($man -is [byte[]]) { $man = [Text.Encoding]::UTF8.GetString($man) }
$changed = 0
foreach ($line in ($man -split "`n")) {
  $p = $line.Trim() -split '\s+'
  if ($p.Count -lt 2 -or $p[0] -notmatch '^[A-Za-z0-9_.-]+\.(ps1|bat|txt)$' -or $p[1] -notmatch '^[0-9a-f]{64}$') { continue }
  $name = $p[0]; $want = $p[1]
  $dst = Join-Path $dir $name
  $tmp = $dst + '.new'
  try { Invoke-WebRequest ($raw + $name) -OutFile $tmp -UseBasicParsing -TimeoutSec 60 }
  catch { Say ('FAIL download ' + $name + ': ' + $_.Exception.Message); continue }
  $got = (Get-FileHash -LiteralPath $tmp -Algorithm SHA256).Hash.ToLower()
  if ($got -ne $want) { Say ('FAIL sha256 ' + $name + ' (kept the old copy)'); [IO.File]::Delete($tmp); continue }
  $nv = Ver $tmp; $ov = Ver $dst
  $same = (Test-Path -LiteralPath $dst) -and ((Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash.ToLower() -eq $got)
  if ($same -or ($ov -ge 0 -and $nv -le $ov -and $name -like '*.ps1')) { [IO.File]::Delete($tmp); continue }
  if (Test-Path -LiteralPath $dst) { Copy-Item -LiteralPath $dst -Destination ($dst + '.bak') -Force }
  Move-Item -LiteralPath $tmp -Destination $dst -Force
  $changed++
  Say ('UPDATED ' + $name + ' v' + $ov + ' -> v' + $nv)
}
Say ('OK checked, ' + $changed + ' updated')
exit 0
