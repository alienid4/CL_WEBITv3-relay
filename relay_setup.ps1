# WUG-RELAY-SETUP-SCRIPT v3 : one-click setup of a relay Windows PC (WhatsUp daily collect + SFTP upload schedule; -Alerts adds a per-minute alert task)
# Run once on the relay PC (Windows PowerShell 5.1) as the Windows account that will run the daily job.
# It only prepares this PC: folders, an SSH key for upload, run_daily.ps1, and one scheduled task.
# With -Alerts it also writes run_alert.ps1 and a second task that runs wug_alert_collect.ps1 (devkit D7) and the upload every minute, 24 hours a day.
# It checks that wug_account.txt, the private key and their folders are open only to you (plus SYSTEM and Administrators);
# it offers to tighten the two files (asks y/N first) and prints the command for existing folders, which you run yourself.
# A relay folder that this script creates itself is locked down that way from the start.
# It never connects to WhatsUp or the system host, never stores your password, and does not change registry, firewall, services or power settings.
# Put wug_collect.ps1 (devkit D4), relay_push.ps1 and this file in the same folder first (and wug_alert_collect.ps1 for -Alerts). Example:
#   powershell -File relay_setup.ps1 -WugBase https://wug.example.test:9644 -Target relayuser@sysrelay.example.test -Alerts
param(
  [string]$Root = (Join-Path $env:SystemDrive 'webit3_relay'),
  [Parameter(Mandatory=$true)][string]$WugBase,
  [Parameter(Mandatory=$true)][string]$Target,
  [string]$Time = '15:30',
  [string]$IpFile = '',
  [string]$TaskName = 'webit3_wug_daily',
  [string]$RemoteDir = '/opt/webit3/relay_in',
  [switch]$TestNow,
  [switch]$Alerts,
  [string]$AlertTaskName = 'webit3_wug_alert'
)
$ErrorActionPreference = 'Stop'
$script:Fails = 0
$skipTask = $false
$skipAlert = $false
function Ok([string]$m) { Write-Host ('OK    ' + $m) -ForegroundColor Green }
function Fail([string]$m) { Write-Host ('FAIL  ' + $m) -ForegroundColor Red; $script:Fails++ }
function Warn([string]$m) { Write-Host ('WARN  ' + $m) -ForegroundColor Yellow }
function Info([string]$m) { Write-Host ('      ' + $m) }
function Q([string]$s) { return "'" + $s.Replace("'", "''") + "'" }
# Who may read a path: only you, SYSTEM, Administrators (and CREATOR OWNER) are fine; anyone else is reported.
$me = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
function Get-OtherAccess([string]$Path) {
  $okSids = @($me, 'S-1-5-18', 'S-1-5-32-544', 'S-1-3-0')
  $names = @()
  foreach ($r in (Get-Acl -LiteralPath $Path).Access) {
    if ([string]$r.AccessControlType -ne 'Allow') { continue }
    $sid = [string]$r.IdentityReference
    try { $sid = $r.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value } catch { $sid = [string]$r.IdentityReference }
    if ($okSids -notcontains $sid) { $names += [string]$r.IdentityReference }
  }
  return @($names | Select-Object -Unique)
}
# A secret file (WhatsUp account file, private key): offer to restrict it to you; never changed without your y.
function Protect-SecretFile([string]$Path, [string]$Label) {
  $o = @(Get-OtherAccess $Path)
  if ($o.Count -eq 0) { Ok ($Label + ' is readable only by you (plus SYSTEM and Administrators)'); return }
  Warn ($Label + ' can also be read by: ' + ($o -join ', '))
  $fix = 'icacls "' + $Path + '" /inheritance:r /grant:r "*' + $me + ':F"'
  $ans = Read-Host ('Restrict ' + $Label + ' to your account only now? This runs: ' + $fix + '  (y/N)')
  if ($ans -match '^[yY]') {
    & icacls $Path /inheritance:r /grant:r ('*' + $me + ':F') | Out-Null
    if (($LASTEXITCODE -eq 0) -and (@(Get-OtherAccess $Path).Count -eq 0)) { Ok ($Label + ' is now readable only by you'); return }
  }
  Fail ($Label + ' is readable by other accounts. Fix it yourself with: ' + $fix)
}
# A folder holding secrets or the scripts the job runs: only reported; the fix command is printed for you to run.
function Test-PrivateFolder([string]$Path, [string]$Label) {
  $o = @(Get-OtherAccess $Path)
  if ($o.Count -eq 0) { Ok ($Label + ' ' + $Path + ' is open only to you (plus SYSTEM and Administrators)'); return }
  Fail ($Label + ' ' + $Path + ' is also open to: ' + ($o -join ', ') + '. Other accounts could read the secrets or change the scripts in it. Run this yourself, then run setup again:')
  Info ('icacls "' + $Path + '" /inheritance:r /grant:r "*' + $me + ':(OI)(CI)F" /grant:r "*S-1-5-18:(OI)(CI)F" /grant:r "*S-1-5-32-544:(OI)(CI)F"')
}
# The one place this script writes a file: the job scripts run_daily.ps1 / run_alert.ps1 (UTF-8 with BOM for PowerShell 5.1).
function Write-RunFile([string]$Path, [string[]]$Lines) {
  [IO.File]::WriteAllText($Path, (($Lines -join [Environment]::NewLine) + [Environment]::NewLine), (New-Object System.Text.UTF8Encoding($true)))
  Ok ('Written ' + $Path)
}
function Stop-IfFailed { if ($script:Fails -gt 0) { Write-Host ''; Write-Host ('Stopped: ' + $script:Fails + ' problem(s) above. Fix them and run this script again (it is safe to re-run).') -ForegroundColor Red; exit 1 } }

Write-Host '== Relay PC setup =='
# 0. Check the inputs before touching anything.
if ($WugBase -match '^https://[A-Za-z0-9][A-Za-z0-9.-]{0,251}(:[0-9]{1,5})?/?$') { Ok ('WhatsUp address ' + $WugBase) } else { Fail 'WugBase must be https://<host name>[:port] (plain http is refused: the password would travel unencrypted)' }
if ($Target -match '^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+$') { Ok ('Upload target ' + $Target) } else { Fail 'Target must look like user@host (the upload account on the system host)' }
if ($RemoteDir -match '^/[A-Za-z0-9._/-]+$') { Ok ('Remote relay folder ' + $RemoteDir) } else { Fail 'RemoteDir must be an absolute path like /opt/webit3/relay_in' }
$at = $null
if ($Time -match '^([01][0-9]|2[0-3]):[0-5][0-9]$') { $at = [datetime]::ParseExact($Time, 'HH:mm', $null); Ok ('Daily run time ' + $Time) } else { Fail 'Time must be HH:mm, for example 15:30' }
if ($at) { $mins = $at.Hour * 60 + $at.Minute; if (($mins -ge 510) -and ($mins -lt 900)) { Warn 'This time is inside weekday trading hours (08:30-15:00); the collector refuses to run then on weekdays. 15:30 or later is recommended.' } }
foreach ($c in 'ssh-keygen', 'sftp') { if (Get-Command $c -ErrorAction SilentlyContinue) { Ok ($c + ' found') } else { Fail ($c + ' not found: ask IT to enable the Windows optional feature OpenSSH Client') } }
$pol = [string](Get-ExecutionPolicy)
if (@('Restricted', 'AllSigned', 'Undefined') -contains $pol) { Fail ('PowerShell script policy on this PC is ' + $pol + ', so the daily job could not run scripts. Ask IT to allow local scripts (RemoteSigned) for this account; this setup does not change it.') } else { Ok ('PowerShell script policy ' + $pol) }
Stop-IfFailed

# a. Folders and files.
Write-Host ''
Write-Host '-- a. Folders and files'
$outDir = Join-Path $Root 'wug_out'
$sentDir = Join-Path $Root 'wug_sent'
$rootNew = -not (Test-Path -LiteralPath $Root)
New-Item -ItemType Directory -Force -Path $Root | Out-Null
if ($rootNew) {
  # A folder this script just created is locked down from the start (you, SYSTEM, Administrators); an existing folder is only checked.
  & icacls $Root /inheritance:r /grant:r ('*' + $me + ':(OI)(CI)F') /grant:r '*S-1-5-18:(OI)(CI)F' /grant:r '*S-1-5-32-544:(OI)(CI)F' | Out-Null
  if ($LASTEXITCODE -eq 0) { Ok ('Folder ' + $Root + ' created, open only to you (plus SYSTEM and Administrators)') } else { Fail ('Folder ' + $Root + ' created, but setting its permissions failed') }
} else { Ok ('Folder ' + $Root + ' already exists (permissions checked below)') }
$alertOut = Join-Path $Root 'wug_alert_out'
$alertSent = Join-Path $Root 'wug_alert_sent'
$dirs = @($outDir, $sentDir)
if ($Alerts) { $dirs += $alertOut; $dirs += $alertSent }
foreach ($d in $dirs) { New-Item -ItemType Directory -Force -Path $d | Out-Null; Ok ('Folder ' + $d) }
$need = @('wug_collect.ps1', 'relay_push.ps1')
if ($Alerts) { $need += 'wug_alert_collect.ps1' }
foreach ($f in $need) {
  $p = Join-Path $Root $f
  if (-not (Test-Path -LiteralPath $p)) { Fail ($f + ' is missing in ' + $Root + ' - download it from the devkit page (D4 = wug_collect.ps1, D6 = relay_push.ps1, D7 = wug_alert_collect.ps1) and save it there'); continue }
  if (Get-Item -LiteralPath $p -Stream 'Zone.Identifier' -ErrorAction SilentlyContinue) { Unblock-File -LiteralPath $p; Ok ($f + ' was marked as downloaded; unblocked this one file so the daily job may run it') }
  Ok ($f + ' present')
}
$pushFile = Join-Path $Root 'relay_push.ps1'
if ((Test-Path -LiteralPath $pushFile) -and -not (Select-String -LiteralPath $pushFile -Pattern 'RELAY-PUSH-SCRIPT v[2-9]' -Quiet)) { Fail 'relay_push.ps1 is an old version - download it again from the devkit page D6' }
$acct = Join-Path $Root 'wug_account.txt'
if ((Test-Path -LiteralPath $acct) -and ((Get-Item -LiteralPath $acct).Length -gt 0)) { Ok 'wug_account.txt present (contents not read here)'; Protect-SecretFile $acct 'wug_account.txt' } else { Fail ('wug_account.txt is missing or empty. Open Notepad, write two lines username=<WhatsUp read-only account> and password=<its password>, save as ' + $acct + ', and make it readable only by this Windows account.') }
if (-not $IpFile) { $IpFile = Join-Path $Root 'wug_ips.txt' }
if (Test-Path -LiteralPath $IpFile) { $ipCount = @(Get-Content -LiteralPath $IpFile | Where-Object { $_.Trim() -and -not $_.Trim().StartsWith('#') }).Count; Ok ('IP list ' + $IpFile + ' (' + $ipCount + ' lines)') } else { Fail ('IP list ' + $IpFile + ' is missing: one IPv4 address per line, lines starting with # are comments') }
Test-PrivateFolder $Root 'Relay folder'
Stop-IfFailed

# b. SSH key for upload (no passphrase, so the job can run unattended; the system host limits what it can do).
Write-Host ''
Write-Host '-- b. Upload key'
$sshDir = Join-Path $env:USERPROFILE '.ssh'
$key = Join-Path $sshDir 'webit3_relay'
New-Item -ItemType Directory -Force -Path $sshDir | Out-Null
if (Test-Path -LiteralPath $key) { Ok ('Key already exists, kept: ' + $key) } else {
  $empty = '""'
  if ($PSVersionTable.PSVersion.Major -ge 7) { $empty = '' }
  & ssh-keygen -q -t ed25519 -N $empty -C 'webit3-relay-push' -f $key
  if (($LASTEXITCODE -eq 0) -and (Test-Path -LiteralPath $key)) { Ok ('Key created: ' + $key) } else { Fail 'ssh-keygen failed'; Stop-IfFailed }
}
Test-PrivateFolder $sshDir 'Key folder'
Protect-SecretFile $key 'Private key webit3_relay'
Stop-IfFailed
$pub = (Get-Content -LiteralPath ($key + '.pub') -TotalCount 1).Trim()
$myIp = @(Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } | ForEach-Object { $_.IPv4Address.IPAddress })
if ($myIp.Count -eq 0) { $myIp = @('<this PC IPv4>'); Warn 'Could not find this PC IPv4 address; replace <this PC IPv4> below by hand.' }
if ($myIp.Count -gt 1) { Warn ('This PC has more than one IPv4 address (' + ($myIp -join ', ') + '); the first one is used below. Use the one the system host sees.') }
$line = 'from="' + $myIp[0] + '",command="internal-sftp -d ' + $RemoteDir + '",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding ' + $pub
Info 'Give this ONE line to the system host administrator, to add to authorized_keys of the upload account (it is a public key, not a secret):'
Write-Host ''
Write-Host $line -ForegroundColor Cyan
Write-Host ''

# c. run_daily.ps1 (collect yesterday, then upload; everything goes to run_log.txt).
Write-Host '-- c. Daily job script'
$runPath = Join-Path $Root 'run_daily.ps1'
$run = @(
  '# RELAY-DAILY-RUN v2 : written by relay_setup.ps1 - run relay_setup.ps1 again to change it. Collect yesterday from WhatsUp, then upload.',
  '$root = $PSScriptRoot',
  '$log = Join-Path $root ''run_log.txt''',
  'function Log([string]$m) { Add-Content -LiteralPath $log -Value ((Get-Date -Format s) + '' '' + $m) }',
  'Set-Location -LiteralPath $root',
  '$bad = 0',
  'Log ''start''',
  'try {',
  '  $global:LASTEXITCODE = 0',
  ('  & (Join-Path $root ''wug_collect.ps1'') -Base ' + (Q $WugBase) + ' -IpFile ' + (Q $IpFile) + ' -OutDir (Join-Path $root ''wug_out'') *>&1 | ForEach-Object { Log (''collect: '' + $_) }'),
  '  $rc = $global:LASTEXITCODE',
  '  Log (''collect exit '' + $rc)',
  '  if ($rc -ne 0) { $bad++ }',
  '} catch { Log (''collect error: '' + $_.Exception.Message); $bad++ }',
  'try {',
  '  $global:LASTEXITCODE = 0',
  ('  & (Join-Path $root ''relay_push.ps1'') -OutDir (Join-Path $root ''wug_out'') -SentDir (Join-Path $root ''wug_sent'') -Key ' + (Q $key) + ' -Target ' + (Q $Target) + ' *>&1 | ForEach-Object { Log (''push: '' + $_) }'),
  '  $rc = $global:LASTEXITCODE',
  '  Log (''push exit '' + $rc)',
  '  if ($rc -ne 0) { $bad++ }',
  '} catch { Log (''push error: '' + $_.Exception.Message); $bad++ }',
  'Log (''end, problems='' + $bad)',
  'if ($bad -gt 0) { exit 1 }',
  'exit 0'
)
Write-RunFile $runPath $run
if ($Alerts) {
  $alertRunPath = Join-Path $Root 'run_alert.ps1'
  $runA = @(
    '# RELAY-ALERT-RUN v1 : written by relay_setup.ps1 -Alerts - run relay_setup.ps1 again to change it. Read WhatsUp alert state, then upload. Runs every minute, also in trading hours (read-only, WhatsUp only).',
    '$root = $PSScriptRoot',
    '$log = Join-Path $root ''alert_log.txt''',
    'function Log([string]$m) { Add-Content -LiteralPath $log -Value ((Get-Date -Format s) + '' '' + $m) }',
    'Set-Location -LiteralPath $root',
    '$bad = 0',
    'try {',
    '  $global:LASTEXITCODE = 0',
    ('  & (Join-Path $root ''wug_alert_collect.ps1'') -Base ' + (Q $WugBase) + ' -OutDir (Join-Path $root ''wug_alert_out'') *>&1 | ForEach-Object { Log (''alert: '' + $_) }'),
    '  $rc = $global:LASTEXITCODE',
    '  if ($rc -ne 0) { $bad++; Log (''alert exit '' + $rc) }',
    '} catch { Log (''alert error: '' + $_.Exception.Message); $bad++ }',
    'try {',
    '  $global:LASTEXITCODE = 0',
    ('  & (Join-Path $root ''relay_push.ps1'') -OutDir (Join-Path $root ''wug_alert_out'') -SentDir (Join-Path $root ''wug_alert_sent'') -Key ' + (Q $key) + ' -Target ' + (Q $Target) + ' *>&1 | ForEach-Object { Log (''push: '' + $_) }'),
    '  $rc = $global:LASTEXITCODE',
    '  if ($rc -ne 0) { $bad++; Log (''push exit '' + $rc) }',
    '} catch { Log (''push error: '' + $_.Exception.Message); $bad++ }',
    'if ($bad -gt 0) { exit 1 }',
    'exit 0'
  )
  Write-RunFile $alertRunPath $runA
}

# d. Scheduled task(s) that run whether or not the user is logged on.
Write-Host ''
Write-Host '-- d. Scheduled task'
$who = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$old = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($old) {
  $ans = Read-Host ('A task named ' + $TaskName + ' already exists. Replace it? (y/N)')
  if ($ans -notmatch '^[yY]') { Warn 'Kept the existing task unchanged.'; $skipTask = $true }
}
if ($Alerts) {
  $oldA = Get-ScheduledTask -TaskName $AlertTaskName -ErrorAction SilentlyContinue
  if ($oldA) {
    $ans = Read-Host ('A task named ' + $AlertTaskName + ' already exists. Replace it? (y/N)')
    if ($ans -notmatch '^[yY]') { Warn 'Kept the existing alert task unchanged.'; $skipAlert = $true }
  }
} else { $skipAlert = $true }
# The one place a task is registered (asked first above when a task of that name exists).
function Register-Job([string]$Name, [string]$RunFile, $Trigger, $Limit, [string]$What) {
  $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -File "' + $RunFile + '"') -WorkingDirectory $Root
  $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit $Limit -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
  try {
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Trigger -Settings $settings -User $cred.UserName -Password $cred.GetNetworkCredential().Password -RunLevel Limited -Force | Out-Null
    $lt = (Get-ScheduledTask -TaskName $Name).Principal.LogonType
    Ok ('Task ' + $Name + ' registered: ' + $What + ', runs whether or not you are logged on (logon type ' + $lt + ')')
  } catch {
    Fail ('Task ' + $Name + ' not registered: ' + $_.Exception.Message + ' - check the password; if it says access denied, run PowerShell as administrator, or ask IT to give this account the right to log on as a batch job.')
  }
}
if ((-not $skipTask) -or (-not $skipAlert)) {
  Info ('Windows needs the password of ' + $who + ' so the task can run while nobody is logged on. It goes straight to Windows Task Scheduler and is not saved or logged by this script.')
  $cred = Get-Credential -UserName $who -Message 'Password of this Windows account (for the scheduled task)'
  if (-not $cred) { Fail 'No password given; task not registered.' } else {
    if (-not $skipTask) { Register-Job $TaskName $runPath (New-ScheduledTaskTrigger -Daily -At $at) (New-TimeSpan -Hours 3) ('daily at ' + $Time) }
    if (-not $skipAlert) { Register-Job $AlertTaskName $alertRunPath (New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 1)) (New-TimeSpan -Minutes 10) 'every minute, 24 hours a day' }
    $cred = $null
  }
}

# e. Sleep check (read only).
Write-Host ''
Write-Host '-- e. Sleep check'
$q = @(& powercfg -query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE | Select-String -Pattern '0x[0-9a-fA-F]+' | ForEach-Object { $_.Matches[0].Value })
if ($q.Count -ge 2) {
  $acSec = [Convert]::ToInt32($q[$q.Count - 2], 16)
  if ($acSec -eq 0) { Ok 'This PC never sleeps on AC power' } else { Warn ('This PC goes to sleep after ' + [int]($acSec / 60) + ' minutes on AC power, so the daily job may be missed. If that is allowed here, run this yourself: powercfg -change -standby-timeout-ac 0') }
} else { Warn 'Could not read the sleep setting; make sure this PC does not sleep at the daily run time.' }

# f. Optional test run now.
if ($TestNow -and -not $skipTask -and $script:Fails -eq 0) {
  Write-Host ''
  Write-Host '-- f. Test run now'
  Start-ScheduledTask -TaskName $TaskName
  Info 'Started; waiting for it to finish (up to 30 minutes)...'
  Start-Sleep -Seconds 5
  $waited = 0
  while (((Get-ScheduledTask -TaskName $TaskName).State -eq 'Running') -and ($waited -lt 1800)) { Start-Sleep -Seconds 10; $waited += 10 }
  $res = (Get-ScheduledTaskInfo -TaskName $TaskName).LastTaskResult
  Info ('Last result code ' + $res + ' (0 = finished)')
  $rl = Join-Path $Root 'run_log.txt'
  if (Test-Path -LiteralPath $rl) { Info 'Last lines of run_log.txt:'; Get-Content -LiteralPath $rl -Tail 15 | ForEach-Object { Info $_ } }
  $pl = Join-Path $sentDir 'push_log.txt'
  if (Test-Path -LiteralPath $pl) { Info 'Last lines of push_log.txt:'; Get-Content -LiteralPath $pl -Tail 5 | ForEach-Object { Info $_ } } else { Warn 'No push_log.txt yet: nothing was uploaded (normal on weekdays 08:30-15:00, or if the key line is not installed yet).' }
}

Write-Host ''
if ($script:Fails -gt 0) { Write-Host ('Finished with ' + $script:Fails + ' problem(s); fix them and run again.') -ForegroundColor Red; exit 1 }
Write-Host 'Done. Next: give the key line above to the system host administrator; after it is installed, run this again with -TestNow (after 15:00) to check one full run.' -ForegroundColor Green
if ($Alerts) { Write-Host 'Alert task: after the key is installed, alert_log.txt in the relay folder gets a line only when something goes wrong; wug_alert_sent fills with small batches.' }
