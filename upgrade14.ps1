# UPGRADE14-SCRIPT v6 (2026-11-17): v6 = real GitHub error (HTTP status + message, never a token), fallback to latest.txt when the API fails,
#   SHA256 file is required (missing / malformed / mismatch = stop). Still interactive (asks the host and the root password once).
# UPGRADE14-SCRIPT v5 (2026-11-15): one double-click upgrade of the system host from the public relay.
# First run: asks the host address and root password once, and registers a dedicated key on the host that
#   - only works from this PC (from=),
#   - can only run /usr/local/sbin/webit3_upgrade_recv (install one package, or report the version): no shell, no forwarding.
#   Remove it any time on the host: delete the authorized_keys line ending in webit3-upgrade14.
# Later runs: no questions. Fetch newest package, check SHA256, send it, the host re-checks SHA256 and runs patch.sh.
param([string]$Repo = 'alienid4/CL_WEBITv3-relay')
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$here = $PSScriptRoot
$log = Join-Path $here 'upgrade14_log.txt'
function Say($m) { Write-Host $m; Add-Content -LiteralPath $log -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $m) }
# What really went wrong with a web call: HTTP status + GitHub's own message, or "no HTTP response" + the network error.
# Only the status, its text and the first 300 characters of the reply body are shown; request headers are never printed.
function ErrInfo($e) {
  $code = ''; $desc = ''; $body = ''
  try { $r = $e.Exception.Response; if ($r) { $code = [string][int]$r.StatusCode; $desc = [string]$r.StatusDescription } } catch { }
  try { if ($e.ErrorDetails -and $e.ErrorDetails.Message) { $body = [string]$e.ErrorDetails.Message } } catch { }
  if (-not $body) {
    try { $r = $e.Exception.Response; if ($r) { $sr = New-Object IO.StreamReader($r.GetResponseStream()); $body = $sr.ReadToEnd(); $sr.Dispose() } } catch { }
  }
  $body = (($body -replace '\s+', ' ').Trim())
  if ($body.Length -gt 300) { $body = $body.Substring(0, 300) + '...' }
  if ($code) { return ('HTTP ' + $code + ' ' + $desc + $(if ($body) { ' - ' + $body } else { '' })) }
  return ('no HTTP response (network, DNS, proxy or certificate): ' + [string]$e.Exception.Message)
}

# 0) self-update (v4): newer copy of this script lives on the relay's "tools" branch (main gets force-pushed, tools does not).
#    Take it only if it is a newer UPGRADE14-SCRIPT; otherwise keep running this one. Never blocks the upgrade.
$MyVer = 6
try {
  $nu = Join-Path $here 'upgrade14.ps1.new'
  Invoke-WebRequest "https://raw.githubusercontent.com/$Repo/tools/upgrade14.ps1" -OutFile $nu -UseBasicParsing -TimeoutSec 20
  $first = (Get-Content -LiteralPath $nu -TotalCount 1 -Encoding UTF8)
  if ($first -match 'UPGRADE14-SCRIPT v(\d+)' -and [int]$Matches[1] -gt $MyVer) {
    Copy-Item -LiteralPath $PSCommandPath -Destination (Join-Path $here 'upgrade14.ps1.old') -Force
    Move-Item -LiteralPath $nu -Destination $PSCommandPath -Force
    Say ('更新升級程式到 v' + $Matches[1])
    & powershell -NoProfile -File $PSCommandPath -Repo $Repo
    exit $LASTEXITCODE
  }
  Remove-Item -LiteralPath $nu -Force
} catch { Write-Host ('（升級程式本身沒有新版可拿，照現有的跑：' + (ErrInfo $_) + '）') }

# 0b) v5：先把這台的其他程式（收集程式等）換成最新版，再升 .14。同步失敗不擋升級。
$ts = Join-Path $here 'tools_sync.ps1'
try {
  if (-not (Test-Path -LiteralPath $ts)) { Invoke-WebRequest "https://raw.githubusercontent.com/$Repo/tools/tools_sync.ps1" -OutFile $ts -UseBasicParsing -TimeoutSec 30 }
  Write-Host '更新這台電腦上的程式…'
  & powershell -NoProfile -File $ts -Repo $Repo
} catch { Write-Host ('（程式同步沒成功，照舊升級：' + $_.Exception.Message + '）') }

# 1) host and login (asked once, kept in upgrade14.conf; not secret)
$conf = Join-Path $here 'upgrade14.conf'
if (Test-Path -LiteralPath $conf) { $c = @(Get-Content -LiteralPath $conf); $Hst = $c[0]; $Usr = $c[1] }
else { $Hst = Read-Host '.14 的 IP'; $Usr = 'root' }
if ($Hst -notmatch '^[A-Za-z0-9.-]+$' -or $Usr -ne 'root') { Write-Host 'upgrade14.conf 不對（第二行要是 root）；刪掉它再跑一次重填'; exit 1 }
if (-not (Test-Path -LiteralPath $conf)) { Set-Content -LiteralPath $conf -Value @($Hst, $Usr) -Encoding ASCII }
$target = $Usr + '@' + $Hst
$work = Join-Path $here 'upgrade14_dl'
New-Item -ItemType Directory -Force -Path $work | Out-Null

# 2) dedicated key
$sshDir = Join-Path $env:USERPROFILE '.ssh'
$key = Join-Path $sshDir 'webit3_upgrade14'
New-Item -ItemType Directory -Force -Path $sshDir | Out-Null
if (-not (Test-Path -LiteralPath $key)) {
  $empty = '""'; if ($PSVersionTable.PSVersion.Major -ge 7) { $empty = '' }
  & ssh-keygen -q -t ed25519 -N $empty -C 'webit3-upgrade14' -f $key
  if ($LASTEXITCODE -ne 0) { Say 'FAIL 建不出專用鑰匙'; exit 1 }
}
$keyOpts = '-i "' + $key + '" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new'

# Start-Process hands a stdin file to ssh as-is (byte for byte); ssh still reads any password from the console.
function KeyRun([string]$cmd, [string]$inFile, [string]$outFile) {
  $a = @{ FilePath = 'ssh'; ArgumentList = ($keyOpts + ' ' + $target + ' ' + $cmd); NoNewWindow = $true; Wait = $true; PassThru = $true }
  if ($inFile) { $a.RedirectStandardInput = $inFile }
  if ($outFile) { $a.RedirectStandardOutput = $outFile }
  return (Start-Process @a).ExitCode
}

# 3) is the key accepted? If not, register it (the one and only password prompt)
$verFile = Join-Path $work 'version.txt'
$rc = KeyRun 'version' $null $verFile
if ($rc -ne 0) {
  Write-Host '第一次設定：登記專用鑰匙到 .14（請輸入 root 密碼，只有這一次）…'
  $pub = (Get-Content -LiteralPath ($key + '.pub') -TotalCount 1).Trim()
  if ($pub -notmatch '^ssh-ed25519 [A-Za-z0-9+/=]+ webit3-upgrade14$') { Say 'FAIL 專用鑰匙格式不對'; exit 1 }
  $recv = @'
#!/bin/bash
# Forced command of the webit3-upgrade14 key (from the relay PC). Allowed: "version", or "install <package> <sha256>"
# with the package on stdin. Anything else is refused. Installed by upgrade14.ps1; remove the key line to switch it off.
set -u; set -f; umask 077
set -- ${SSH_ORIGINAL_COMMAND:-}
case "${1:-}" in
  version) for pt in 8081 8000; do v=$(curl -sf --max-time 5 http://localhost:$pt/api/version) && { echo "$v"; exit 0; }; done; echo "{}"; exit 0 ;;
  install) ;;
  *) echo "refused: only version / install"; exit 2 ;;
esac
name="${2:-}"; sha="${3:-}"
[[ "$name" =~ ^patch_[A-Za-z0-9_]+_v[0-9]+\.[0-9]+\.[0-9]+\.tar\.gz$ ]] || { echo "refused: bad package name"; exit 2; }
[[ "$sha" =~ ^[0-9a-f]{64}$ ]] || { echo "refused: bad sha256"; exit 2; }
find /tmp -maxdepth 1 -name 'webit3_up.*' -mtime +7 -exec rm -rf {} + 2>/dev/null
# one install at a time; a package is never larger than 200 MB
exec 9>/var/lock/webit3_upgrade.lock; flock -n 9 || { echo "refused: another install is running"; exit 4; }
d=$(mktemp -d /tmp/webit3_up.XXXXXX) || exit 4
[ -n "$d" ] || exit 4
head -c 209715200 > "$d/$name"
echo "$sha  $d/$name" | sha256sum -c || exit 3
cd "$d" && tar xzf "$name" && cd "${name%.tar.gz}" && bash patch.sh
rc=$?
echo; for pt in 8081 8000; do v=$(curl -sf --max-time 5 http://localhost:$pt/api/version) && { echo "VERSION: $v"; break; }; done
exit $rc
'@
  $tail = @'
chmod 700 /usr/local/sbin/webit3_upgrade_recv.new
mv /usr/local/sbin/webit3_upgrade_recv.new /usr/local/sbin/webit3_upgrade_recv
ip=${SSH_CLIENT%% *}
ak="$HOME/.ssh/authorized_keys"
mkdir -p "$HOME/.ssh"; chmod 700 "$HOME/.ssh"; touch "$ak"; chmod 600 "$ak"
cp -p "$ak" "$ak.bak_webit3_$(date +%Y%m%d%H%M%S)"
grep -v 'webit3-upgrade14$' "$ak" > "$ak.w3" || true
echo "from=\"$ip\",command=\"/usr/local/sbin/webit3_upgrade_recv\",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-user-rc PUBKEY" >> "$ak.w3"
chmod 600 "$ak.w3"; mv "$ak.w3" "$ak"
command -v restorecon >/dev/null && restorecon -R "$HOME/.ssh" || true
echo "SETUP-OK key allowed from $ip"
'@
  $setup = "set -eu`numask 077`ncat > /usr/local/sbin/webit3_upgrade_recv.new <<'RECV'`n" + $recv + "`nRECV`n" + $tail.Replace('PUBKEY', $pub)
  $setupFile = Join-Path $work 'setup14.sh'
  [IO.File]::WriteAllText($setupFile, ($setup -replace "`r", ''))
  $pr = Start-Process ssh -ArgumentList ('-o PubkeyAuthentication=no -o StrictHostKeyChecking=accept-new ' + $target + ' bash -s') -RedirectStandardInput $setupFile -NoNewWindow -Wait -PassThru
  Remove-Item -LiteralPath $setupFile -Force
  if ($pr.ExitCode -ne 0) { Say ('FAIL 登記鑰匙沒成功 exit ' + $pr.ExitCode); exit 1 }
  $rc = KeyRun 'version' $null $verFile
  if ($rc -ne 0) { Say ('FAIL 鑰匙登記了但用不了 exit ' + $rc); exit 1 }
  Say 'OK 專用鑰匙已登記，之後不用再打密碼'
}
$cur = $null
try { $cur = ((Get-Content -LiteralPath $verFile -Raw) | ConvertFrom-Json).version } catch { $cur = $null }
# v4: the old receiver asked the wrong port; ask the backend directly (/api/version needs no login)
function WebVer {
  foreach ($pt in 8081, 8000) {
    try { $v = (Invoke-RestMethod ('http://' + $Hst + ':' + $pt + '/api/version') -TimeoutSec 8).version; if ($v -match '^\d+\.\d+\.\d+$') { return $v } } catch { }
  }
  return $null
}
if (-not ($cur -match '^\d+\.\d+\.\d+$')) { $cur = WebVer }

# 4) newest package on the relay (largest version in the last 3 date folders)
Write-Host '查最新版…'
$hd = @{ 'User-Agent' = 'webit3-upgrade14' }
$p = $null
$apiErr = ''
$ltErr = ''
$via = ''
# 4a) GitHub API. assign first, then loop: in PowerShell 5.1 Invoke-RestMethod hands a JSON array down the pipe as ONE object
try {
  $top = Invoke-RestMethod "https://api.github.com/repos/$Repo/contents/patches" -Headers $hd -TimeoutSec 30
  $days = @(foreach ($x in $top) { if ($x.type -eq 'dir' -and $x.name -match '^\d{8}$') { $x } }) | Sort-Object name | Select-Object -Last 3
  $cands = foreach ($d in $days) {
    $files = Invoke-RestMethod $d.url -Headers $hd -TimeoutSec 30
    foreach ($f in $files) {
      if ($f.name -match '^patch_[A-Za-z0-9_]+_v(\d+\.\d+\.\d+)\.tar\.gz$') {
        [pscustomobject]@{ Name = $f.name; Path = $f.path; Ver = [version]$Matches[1]; VerText = $Matches[1] } } } }
  $p = $cands | Sort-Object Ver | Select-Object -Last 1
  if ($p) { $via = 'GitHub API' } else { $apiErr = 'the API answered, but no patch_*_vX.Y.Z.tar.gz was in the newest 3 date folders' }
} catch { $apiErr = ErrInfo $_ }
if (-not $p) { Say ('WARN GitHub API 沒拿到更新包清單：' + $apiErr) }
# 4b) fallback: patches/latest.txt on the relay (raw file, no API rate limit). One line: the package path (patches/YYYYMMDD/patch_..tar.gz) or just its name.
if (-not $p) {
  Write-Host '改讀 latest.txt（退路）…'
  $ltRaw = ''
  try {
    $ltRaw = (Invoke-WebRequest "https://raw.githubusercontent.com/$Repo/main/patches/latest.txt" -UseBasicParsing -TimeoutSec 30).Content
    if ($ltRaw -is [byte[]]) { $ltRaw = [Text.Encoding]::UTF8.GetString($ltRaw) }
    $ltLine = ([string]$ltRaw -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ } | Select-Object -First 1)
    if ($ltLine -and ($ltLine -match '(patch_[A-Za-z0-9_]+_v(\d+\.\d+\.\d+)\.tar\.gz)$')) {
      $nm = $Matches[1]; $vt = $Matches[2]
      $pth = $ltLine
      if ($ltLine -notmatch '/') {
        if ($nm -match '^patch_[A-Za-z0-9]+_(\d{8})_') { $pth = 'patches/' + $Matches[1] + '/' + $nm } else { $pth = '' }
      }
      if ($pth -and ($pth -match '^patches/\d{8}/patch_[A-Za-z0-9_]+_v\d+\.\d+\.\d+\.tar\.gz$')) {
        $p = [pscustomobject]@{ Name = $nm; Path = $pth; Ver = [version]$vt; VerText = $vt }
        $via = 'latest.txt'
      } else { $ltErr = 'latest.txt text is not a package path I can use: ' + ([string]$ltLine) }
    } else { $ltErr = 'latest.txt text has no patch_*_vX.Y.Z.tar.gz in it: ' + (([string]$ltRaw -replace '\s+', ' ').Trim()) }
  } catch { $ltErr = ErrInfo $_ }
  if ($ltErr.Length -gt 300) { $ltErr = $ltErr.Substring(0, 300) + '...' }
}
if (-not $p) {
  Say ('FAIL 找不到更新包。GitHub API：' + $apiErr + '；latest.txt：' + $ltErr)
  Write-Host '（API 回 403 多半是這台 IP 被 GitHub 限流，過一小時再試；回 404 看網址或 repo 名稱；沒有 HTTP 回應就是網路／代理／憑證。）'
  exit 1
}
if ($via -eq 'latest.txt') { Say ('注意：GitHub API 失敗，改用 latest.txt 找到 ' + $p.Name) }
$curText = '查不到'; if ($cur) { $curText = 'v' + $cur }
Write-Host ('最新版 v' + $p.VerText + '；.14 目前 ' + $curText)
if ($cur -and ([version]$cur -ge $p.Ver)) { Say ('SKIP .14 已經是 v' + $cur + '，不用升'); exit 0 }

# 5) download and check SHA256
$tgz = Join-Path $work $p.Name
$shaF = Join-Path $work ($p.Name -replace '\.tar\.gz$', '.sha256')
$raw = "https://raw.githubusercontent.com/$Repo/main/"
if (Test-Path -LiteralPath $shaF) { Remove-Item -LiteralPath $shaF -Force }
Write-Host '下載中…'
try { Invoke-WebRequest ($raw + $p.Path) -OutFile $tgz -UseBasicParsing -TimeoutSec 900 }
catch { Say ('FAIL 下載更新包失敗 ' + $p.Name + '：' + (ErrInfo $_)); exit 1 }
try { Invoke-WebRequest ($raw + ($p.Path -replace '\.tar\.gz$', '.sha256')) -OutFile $shaF -UseBasicParsing -TimeoutSec 60 }
catch { Say ('FAIL 下載不到 .sha256，不能驗證就不安裝：' + (ErrInfo $_)); exit 1 }
$want = ((Get-Content -LiteralPath $shaF -Raw).Trim() -split '\s+')[0].ToLower()
if ($want -notmatch '^[0-9a-f]{64}$') { Say ('FAIL .sha256 檔內容不是 64 碼十六進位，不安裝：' + $p.Name); exit 1 }
$got = (Get-FileHash -LiteralPath $tgz -Algorithm SHA256).Hash.ToLower()
if ($want -ne $got) { Say ('FAIL SHA256 不符，不安裝 ' + $p.Name + '（官方 ' + $want.Substring(0, 12) + '…，下載 ' + $got.Substring(0, 12) + '…）'); exit 1 }
Say ('OK SHA256 相符 ' + $p.Name + '（取得方式：' + $via + '）')

# 6) send and install (the host checks SHA256 again before patch.sh)
Write-Host ('安裝 v' + $p.VerText + ' 到 .14…')
$rc = KeyRun ('install ' + $p.Name + ' ' + $want) $tgz $null
if ($rc -ne 0) { Say ('FAIL 安裝沒成功 exit ' + $rc + ' v' + $p.VerText); exit 1 }
$now = WebVer
if ($now -and ([version]$now -ge $p.Ver)) { Say ('OK 已裝好 v' + $p.VerText + '（.14 回報 v' + $now + '）') }
elseif ($now) { Say ('WARN 裝完 .14 回報 v' + $now + '，不是 v' + $p.VerText + '；服務可能還在啟動，等一分鐘重新整理網頁看右上角版號') }
else { Say ('OK 已裝好 v' + $p.VerText + '（.14 版號暫時查不到，服務可能還在啟動）') }
