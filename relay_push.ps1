# RELAY-PUSH-SCRIPT v4 : upload finished batches from the relay PC to the system host relay folder by SFTP (SSH key only).
# The key is restricted on the system host side: only from this PC, sftp only, start dir = the relay folder.
# Each batch goes into a fresh hidden staging folder (.up_<batch>_<random>, which the importer skips because it starts with a dot):
# every file goes up as .tmp and is renamed, manifest.json goes last, then the whole folder is renamed to the batch name in one step.
# If a batch of that name is already on the system host, the final rename fails and the batch is reported FAIL; nothing is overwritten.
# A batch folder without manifest.json is still being written and is skipped. Sent batches move to SentDir; push_log.txt keeps one line per batch.
# v3 (2026-11-17): sftp could wait for ever (no connect / idle timeout), which froze the whole daily job. Now every sftp run has
# ConnectTimeout / ServerAlive options AND a hard per-batch limit (-SftpTimeoutSec, the sftp process tree is killed on expiry and
# the batch is reported FAIL with the reason), plus a watchdog (no progress for -StallMinutes, or the whole push longer than
# -MaxMinutes: log WATCHDOG line, kill this process tree, exit code 1). A failed batch stays in OutDir and is sent again next run.
# v4 (2026-11-17): the transport is now a setting. DEFAULT = unchanged: sftp over SSH, port 22 (no -P flag is added, no new file is needed).
# To change it, put a relay_transport.conf next to this script (key=value lines, # = comment) or pass the matching parameters:
#   transport=sftp|https     port=<n>  (sftp: used as -P when not 22; https: replaces the port in https_base only if it has none)
#   https_base=https://host[:port]/path   pin_sha256=<64 hex>   cert_file=<server certificate .cer/.crt>   token_file=<file with a bearer token>
# https mode always VERIFIES the server certificate: with pin_sha256 / cert_file only that exact certificate (self-signed is fine, not
# expired) is accepted; without them the normal Windows trust store must accept it. There is no "skip certificate check" mode.
# https upload contract (PROPOSED; no receiver exists in the system yet, so https cannot work until one is built and 443 is opened):
#   PUT <base>/<stage>/<file> for each file (manifest.json last), then POST <base>/<stage>/_commit?as=<batch>; any 2xx = OK.
# Example:  powershell -File relay_push.ps1 -Target relayuser@sysrelay.example.test
param(
  [string]$OutDir = (Join-Path $PSScriptRoot 'wug_out'),
  [string]$SentDir = (Join-Path $PSScriptRoot 'wug_sent'),
  [string]$Key = (Join-Path (Join-Path $env:USERPROFILE '.ssh') 'webit3_relay'),
  [Parameter(Mandatory=$true)][string]$Target,
  [int]$SftpTimeoutSec = 600,
  [int]$StallMinutes = 15,
  [int]$MaxMinutes = 60,
  [int]$WatchdogPollSec = 5,
  [string]$Transport = '',
  [int]$Port = 0,
  [string]$HttpsBase = '',
  [string]$PinSha256 = '',
  [string]$CertFile = '',
  [string]$TokenFile = '',
  [int]$HttpsTimeoutSec = 120
)
# ---- transport settings: parameter > relay_transport.conf > default (sftp, 22) ----
$cfg = @{}
$cfgFile = Join-Path $PSScriptRoot 'relay_transport.conf'
if (Test-Path -LiteralPath $cfgFile) {
  foreach ($l in (Get-Content -LiteralPath $cfgFile)) { $t = $l.Trim(); if ((-not $t) -or $t.StartsWith('#')) { continue }; $i = $t.IndexOf('='); if ($i -gt 0) { $cfg[$t.Substring(0, $i).Trim().ToLower()] = $t.Substring($i + 1).Trim() } }
}
function Pick([string]$ParamValue, [string]$CfgKey, [string]$Default) { if ($ParamValue) { return $ParamValue }; if ($cfg.ContainsKey($CfgKey) -and $cfg[$CfgKey]) { return [string]$cfg[$CfgKey] }; return $Default }
$transport = (Pick $Transport 'transport' 'sftp').ToLower()
$port = $Port; if ($port -le 0) { $port = 0; if ($cfg.ContainsKey('port') -and ($cfg['port'] -match '^[0-9]{1,5}$')) { $port = [int]$cfg['port'] } }
$httpsBase = Pick $HttpsBase 'https_base' ''
$pinText = Pick $PinSha256 'pin_sha256' ''
$certFile = Pick $CertFile 'cert_file' ''
$tokenFile = Pick $TokenFile 'token_file' ''
if (($transport -ne 'sftp') -and ($transport -ne 'https')) { Write-Host ('transport must be sftp or https (got: ' + $transport + ')'); exit 1 }
if ($port -gt 65535) { Write-Host 'port must be 1..65535'; exit 1 }
$script:tlsReady = $false
$script:token = ''
$pin = ''
if ($transport -eq 'https') {
  if ($httpsBase -notmatch '^https://[A-Za-z0-9][A-Za-z0-9.-]{0,251}(:[0-9]{1,5})?(/[A-Za-z0-9._/-]*)?$') { Write-Host 'https_base must look like https://host[:port]/path (plain http is refused)'; exit 1 }
  if (($port -gt 0) -and ($httpsBase -notmatch '^https://[^/]+:[0-9]+')) { $httpsBase = $httpsBase -replace '^(https://[^/]+)', ('$1:' + $port) }
  if ($pinText) {
    $pin = ($pinText -replace '[\s:]', '').ToLower()
    if ($pin -notmatch '^[0-9a-f]{64}$') { Write-Host 'pin_sha256 must be 64 hex characters (SHA-256 of the server certificate, colons allowed)'; exit 1 }
  } elseif ($certFile) {
    if (-not (Test-Path -LiteralPath $certFile)) { Write-Host ('cert_file not found: ' + $certFile); exit 1 }
    try { $c2 = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList $certFile; $sh = [Security.Cryptography.SHA256]::Create(); $pin = (($sh.ComputeHash($c2.RawData) | ForEach-Object { $_.ToString('x2') }) -join '') }
    catch { Write-Host ('cert_file could not be read as a certificate: ' + $_.Exception.Message); exit 1 }
  }
  if ($tokenFile) {
    if (-not (Test-Path -LiteralPath $tokenFile)) { Write-Host ('token_file not found: ' + $tokenFile); exit 1 }
    $script:token = ([string](Get-Content -LiteralPath $tokenFile -TotalCount 1)).Trim()
  }
}

if ($Target -notmatch '^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+$') { Write-Host 'Target must look like user@host'; exit 1 }
if (($transport -eq 'sftp') -and (-not (Test-Path -LiteralPath $Key))) { Write-Host ('Missing key file ' + $Key + ' - run relay_setup.ps1 first'); exit 1 }
if (-not (Test-Path -LiteralPath $OutDir)) { Write-Host ('Nothing to send: ' + $OutDir + ' does not exist'); exit 0 }
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

New-Item -ItemType Directory -Force -Path $SentDir | Out-Null
$wdLogs = @((Join-Path $PSScriptRoot 'run_log.txt'), (Join-Path $SentDir 'push_watchdog_log.txt'))
Start-Watchdog 'relay_push' ($StallMinutes * 60) ($MaxMinutes * 60) $WatchdogPollSec $wdLogs
try {
$log = Join-Path $SentDir 'push_log.txt'
$enc = New-Object System.Text.UTF8Encoding($false)
$safe = '^[A-Za-z0-9][A-Za-z0-9._-]*$'
$bad = 0
function Initialize-Tls {
  if ($script:tlsReady) { return }
  Add-Type -TypeDefinition @'
using System;
using System.Net.Security;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
public static class WebitPin {
  public static string Pin = "";
  public static readonly RemoteCertificateValidationCallback Callback = Validate;
  public static bool Validate(object sender, X509Certificate cert, X509Chain chain, SslPolicyErrors errors) {
    if (cert == null) return false;
    // no pin: only a certificate the normal Windows trust store accepts
    if (string.IsNullOrEmpty(Pin)) return errors == SslPolicyErrors.None;
    // pin: exactly this certificate (identity by SHA-256), still within its validity dates; chain/name errors of a self-signed certificate do not matter
    if ((errors & SslPolicyErrors.RemoteCertificateNotAvailable) != 0) return false;
    X509Certificate2 c2 = new X509Certificate2(cert);
    DateTime now = DateTime.Now;
    if (now < c2.NotBefore || now > c2.NotAfter) return false;
    byte[] h;
    using (SHA256 sha = SHA256.Create()) { h = sha.ComputeHash(c2.RawData); }
    return BitConverter.ToString(h).Replace("-", "").ToLowerInvariant() == Pin;
  }
}
'@
  [WebitPin]::Pin = $pin
  $script:tlsReady = $true
}
# One https request with a hard total timeout. Returns '' on success (any 2xx), else a short reason. The token is never printed.
function Send-Https([string]$Method, [string]$Url, [string]$File, [int]$TimeoutSec) {
  try {
    Initialize-Tls
    $req = [Net.HttpWebRequest]::Create($Url)
    $req.Method = $Method
    $req.UserAgent = 'webit3-relay-push'
    $req.Timeout = $TimeoutSec * 1000
    $req.ReadWriteTimeout = $TimeoutSec * 1000
    $req.ServerCertificateValidationCallback = [WebitPin]::Callback
    if ($script:token) { $req.Headers.Add('Authorization', 'Bearer ' + $script:token) }
    if ($File) {
      $bytes = [IO.File]::ReadAllBytes($File)
      $req.ContentType = 'application/octet-stream'
      $req.ContentLength = $bytes.Length
      $rs = $req.GetRequestStream()
      try { $rs.Write($bytes, 0, $bytes.Length) } finally { $rs.Dispose() }
    } else { $req.ContentLength = 0 }
    $task = $req.GetResponseAsync()
    $waited = $false
    try { $waited = $task.Wait($TimeoutSec * 1000) } catch { $waited = $true }
    if (-not $waited) { $req.Abort(); return ('timeout after ' + $TimeoutSec + ' s waiting for the server') }
    if ($task.IsFaulted) { throw $task.Exception.InnerException }
    $resp = $task.Result
    $code = [int]$resp.StatusCode
    $resp.Close()
    if (($code -ge 200) -and ($code -lt 300)) { return '' }
    return ('HTTP ' + $code)
  } catch {
    $e = $_.Exception
    $code = ''
    try { if ($e.Response) { $code = 'HTTP ' + [int]$e.Response.StatusCode + ' ' + [string]$e.Response.StatusDescription + ' - ' } } catch { }
    $m = [string]$e.Message
    if ($m -match 'trust|SSL|TLS|certificate|secure channel') { $m = $m + ' (server certificate was not accepted: check pin_sha256 / cert_file)' }
    return ($code + $m)
  }
}
foreach ($b in Get-ChildItem -LiteralPath $OutDir -Directory) {
  $m = Join-Path $b.FullName 'manifest.json'
  if (-not (Test-Path -LiteralPath $m)) { continue }
  $n = $b.Name
  $files = @(Get-ChildItem -LiteralPath $b.FullName -File | Where-Object { $_.Name -ne 'manifest.json' -and $_.Name -notlike '*.tmp' }) + @(Get-Item -LiteralPath $m)
  $r = 'FAIL'
  $why = ''
  $unsafe = @($files | Where-Object { $_.Name -notmatch $safe })
  if ($n -notmatch $safe) { $why = ' (batch name has characters other than letters, digits, dot, dash, underscore)' }
  elseif ($unsafe.Count -gt 0) { $why = ' (file name has characters other than letters, digits, dot, dash, underscore)' }
  else {
    $stage = '.up_' + $n + '_' + [guid]::NewGuid().ToString('N').Substring(0, 8)
    if ($transport -eq 'https') {
      $t0 = Get-Date
      $errTail = ''
      $sent = $true
      $baseUrl = $httpsBase.TrimEnd('/')
      foreach ($f in $files) {
        Set-Step ('https PUT ' + $n + '/' + $f.Name)
        $e = Send-Https 'PUT' ($baseUrl + '/' + $stage + '/' + $f.Name) $f.FullName $HttpsTimeoutSec
        if ($e) { $sent = $false; $errTail = $f.Name + ': ' + $e; break }
      }
      if ($sent) {
        Set-Step ('https commit ' + $n)
        $e = Send-Https 'POST' ($baseUrl + '/' + $stage + '/_commit?as=' + $n) '' $HttpsTimeoutSec
        if ($e) { $sent = $false; $errTail = 'commit: ' + $e }
      }
      if (-not $sent) { $why = ' (https upload failed after ' + [int]((Get-Date) - $t0).TotalSeconds + ' s: ' + $errTail + '; the batch stays and is sent again next run)' }
    } else {
      $lines = @('mkdir "' + $stage + '"')
      foreach ($f in $files) {
        $p = $f.FullName.Replace([char]92, [char]47)
        $lines += ('put "' + $p + '" "' + $stage + '/' + $f.Name + '.tmp"')
        $lines += ('rename "' + $stage + '/' + $f.Name + '.tmp" "' + $stage + '/' + $f.Name + '"')
      }
      $lines += ('rename "' + $stage + '" "' + $n + '"')
      $bf = Join-Path $env:TEMP ('relay_push_' + $n + '.txt')
      [IO.File]::WriteAllLines($bf, [string[]]$lines, $enc)
      Set-Step ('sftp ' + $n)
      $sent = $false
      $so = Join-Path $env:TEMP ('relay_push_' + $n + '.out')
      $se = Join-Path $env:TEMP ('relay_push_' + $n + '.err')
      $sa = '-i "' + $Key + '" -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=30 -o ServerAliveInterval=15 -o ServerAliveCountMax=4' + $(if (($port -gt 0) -and ($port -ne 22)) { ' -P ' + $port } else { '' }) + ' -b "' + $bf + '" ' + $Target
      $sp = Start-Process -FilePath 'sftp' -ArgumentList $sa -NoNewWindow -PassThru -RedirectStandardOutput $so -RedirectStandardError $se
      $t0 = Get-Date
      $null = $sp.Handle   # keep the handle so ExitCode is readable after the process ends (PowerShell 5.1 quirk with -PassThru)
      if ($sp.WaitForExit($SftpTimeoutSec * 1000)) { $sp.WaitForExit(); $sent = ($sp.ExitCode -eq 0) }
      else {
        & taskkill.exe /PID $sp.Id /T /F | Out-Null
        $why = ' (sftp did not finish within ' + $SftpTimeoutSec + ' s; killed it; the batch stays and is sent again next run)'
      }
      $errTail = ''
      if (Test-Path -LiteralPath $se) { $errTail = ((@(Get-Content -LiteralPath $se -Tail 2 -ErrorAction SilentlyContinue) -join ' | ') -replace '\s+', ' ').Trim() }
      Remove-Item -LiteralPath $bf, $so, $se -ErrorAction SilentlyContinue
    }
    if (-not $sent) {
      if (-not $why) { $why = ' (upload failed after ' + [int]((Get-Date) - $t0).TotalSeconds + ' s, or a batch of this name is already on the system host' + $(if ($errTail) { '; sftp said: ' + $errTail } else { '' }) + ')' }
    }
    else {
      try {
        Move-Item -LiteralPath $b.FullName -Destination (Join-Path $SentDir $n) -ErrorAction Stop
        $r = 'OK'
      } catch {
        $why = ' (uploaded, but could not move it to the sent folder: ' + $_.Exception.Message + ')'
      }
    }
  }
  if ($r -ne 'OK') { $bad++ }
  Add-Content -LiteralPath $log -Value ((Get-Date -Format s) + ' ' + $n + ' ' + $r + ' files=' + $files.Count + $why)
  Write-Host ($n + ' ' + $r + $why)
}
} finally { Stop-Watchdog }
if ($bad -gt 0) { exit 1 }
exit 0
