# UPGRADE14-SCRIPT v4 (2026-11-15): one double-click upgrade of the system host from the public relay.
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

# 0) self-update (v4): newer copy of this script lives on the relay's "tools" branch (main gets force-pushed, tools does not).
#    Take it only if it is a newer UPGRADE14-SCRIPT; otherwise keep running this one. Never blocks the upgrade.
$MyVer = 4
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
} catch { Write-Host '（升級程式本身沒有新版可拿，照現有的跑）' }

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
# assign first, then loop: in PowerShell 5.1 Invoke-RestMethod hands a JSON array down the pipe as ONE object
$top = Invoke-RestMethod "https://api.github.com/repos/$Repo/contents/patches" -Headers $hd
$days = @(foreach ($x in $top) { if ($x.type -eq 'dir' -and $x.name -match '^\d{8}$') { $x } }) | Sort-Object name | Select-Object -Last 3
$cands = foreach ($d in $days) {
  $files = Invoke-RestMethod $d.url -Headers $hd
  foreach ($f in $files) {
    if ($f.name -match '^patch_[A-Za-z0-9_]+_v(\d+\.\d+\.\d+)\.tar\.gz$') {
      [pscustomobject]@{ Name = $f.name; Path = $f.path; Ver = [version]$Matches[1]; VerText = $Matches[1] } } } }
$p = $cands | Sort-Object Ver | Select-Object -Last 1
if (-not $p) { Say 'FAIL relay 上找不到更新包'; exit 1 }
$curText = '查不到'; if ($cur) { $curText = 'v' + $cur }
Write-Host ('最新版 v' + $p.VerText + '；.14 目前 ' + $curText)
if ($cur -and ([version]$cur -ge $p.Ver)) { Say ('SKIP .14 已經是 v' + $cur + '，不用升'); exit 0 }

# 5) download and check SHA256
$tgz = Join-Path $work $p.Name
$shaF = Join-Path $work ($p.Name -replace '\.tar\.gz$', '.sha256')
$raw = "https://raw.githubusercontent.com/$Repo/main/"
Write-Host '下載中…'
Invoke-WebRequest ($raw + $p.Path) -OutFile $tgz -UseBasicParsing
Invoke-WebRequest ($raw + ($p.Path -replace '\.tar\.gz$', '.sha256')) -OutFile $shaF -UseBasicParsing
$want = ((Get-Content -LiteralPath $shaF -Raw).Trim() -split '\s+')[0].ToLower()
$got = (Get-FileHash -LiteralPath $tgz -Algorithm SHA256).Hash.ToLower()
if ($want -ne $got) { Say ('FAIL SHA256 不符 ' + $p.Name); exit 1 }

# 6) send and install (the host checks SHA256 again before patch.sh)
Write-Host ('安裝 v' + $p.VerText + ' 到 .14…')
$rc = KeyRun ('install ' + $p.Name + ' ' + $want) $tgz $null
if ($rc -ne 0) { Say ('FAIL 安裝沒成功 exit ' + $rc + ' v' + $p.VerText); exit 1 }
$now = WebVer
if ($now -and ([version]$now -ge $p.Ver)) { Say ('OK 已裝好 v' + $p.VerText + '（.14 回報 v' + $now + '）') }
elseif ($now) { Say ('WARN 裝完 .14 回報 v' + $now + '，不是 v' + $p.VerText + '；服務可能還在啟動，等一分鐘重新整理網頁看右上角版號') }
else { Say ('OK 已裝好 v' + $p.VerText + '（.14 版號暫時查不到，服務可能還在啟動）') }
