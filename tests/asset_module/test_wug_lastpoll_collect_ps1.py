"""收集端 wug_lastpoll_collect.ps1（v3）實際跑一次：用假的 Invoke-RestMethod（PowerShell 函式蓋過 cmdlet）模擬 WhatsUp。

釘住 11-14 第二次實跑看到的事：群組 CPU 報表一次最多約 100 台、沒有下一頁 → 要拆成「自己的設備＋每個子群組」再合併；
拆到底（沒有子群組）還是滿 100 台 → PARTIAL「WhatsUp report cap」，不可以是 complete。成員一次讀 500 → 逐子群組。
SWAP 探測不可以用 view=details（WhatsUp 回 400）。只在有 Windows PowerShell 的機器跑（221 沒有就跳過）。
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "AI" / "tools_relay" / "wug_lastpoll_collect.ps1"
PS = shutil.which("powershell")

pytestmark = pytest.mark.skipif(PS is None or not SRC.exists(), reason="需要 Windows PowerShell 與 AI/ 正本")

MOCK = r"""
$ErrorActionPreference = 'Stop'
$global:G = @(
  @{ id = '0'; name = 'My Network'; parentGroupId = '' },
  @{ id = '1'; name = 'Big (A)'; parentGroupId = '0' },
  @{ id = '11'; name = 'Child A'; parentGroupId = '1' },
  @{ id = '12'; name = 'Child B'; parentGroupId = '1' },
  @{ id = '2'; name = 'Server'; parentGroupId = '0' },
  @{ id = '21'; name = 'Sub A'; parentGroupId = '2' },
  @{ id = '3'; name = 'Leaf'; parentGroupId = '0' }
)
$global:Own = @{ '0' = @(); '1' = @('d1','d2','d3'); '11' = @(1..80 | ForEach-Object { "a$_" }); '12' = @(1..70 | ForEach-Object { "b$_" });
                 '2' = @(); '21' = @('d4','d5'); '3' = @(1..120 | ForEach-Object { "i$_" }) }
function global:Devs([string]$gid, [bool]$h) {
  $out = @($global:Own[$gid])
  if ($h) { foreach ($g in $global:G) { if ($g.parentGroupId -eq $gid) { $out += Devs $g.id $true } } }
  return @($out | Sort-Object -Unique)
}
$global:Log = New-Object System.Collections.ArrayList
function global:Invoke-RestMethod {
  param([string]$Method, [string]$Uri, $Body, $Headers, [int]$TimeoutSec)
  [void]$global:Log.Add($Uri)
  if ($Uri -like '*/api/v1/token') { return [pscustomobject]@{ access_token = 'T' } }
  $pg = 0
  if ($Uri -match 'pageId=([0-9]+)') { $pg = [int]$Matches[1] }
  if ($Uri -like '*/device-groups/-?limit=0*') { return [pscustomobject]@{ data = [pscustomobject]@{ groups = @($global:G | ForEach-Object { [pscustomobject]$_ }) } } }
  if ($Uri -match '/device-groups/([0-9]+)/devices[?]returnHierarchy=(true|false)') {
    $gid = $Matches[1]; $h = ($Matches[2] -eq 'true')
    if ($gid -eq '2' -and $h) { throw (New-Object System.Net.WebException 'The remote server returned an error: (500) Internal Server Error.') }
    return [pscustomobject]@{ data = [pscustomobject]@{ devices = @(Devs $gid $h | ForEach-Object { [pscustomobject]@{ id = $_; name = "dev$_"; networkAddress = '10.99.1.1' } }) } }
  }
  if ($Uri -match '/device-groups/([0-9]+)/devices/reports/([a-z]+)-utilization[?]range=lastPolled&returnHierarchy=(true|false)') {
    $gid = $Matches[1]; $kind = $Matches[2]; $all = @(Devs $gid ($Matches[3] -eq 'true'))
    if ($kind -eq 'cpu') {
      $rows = @($all | Select-Object -First 100 | ForEach-Object { [pscustomobject]@{ id = $_; deviceName = 'x'; cpu = 'c'; pollTimeUtc = 't'; timeFromLastPollSeconds = 60; avgPercent = 0.2; maxPercent = 0.3 } })
      return [pscustomobject]@{ data = $rows; paging = [pscustomobject]@{ pageId = '1'; size = $rows.Count } }
    }
    $rows = @($all | Select-Object -Skip ($pg * 50) -First 50 | ForEach-Object { [pscustomobject]@{ id = $_; deviceName = 'x'; memory = 'm'; disk = 'C:'; pollTimeUtc = 't'; timeFromLastPollSeconds = 60; avgPercent = 0.1; maxPercent = 0.2; avgUsed = 10.0; size = 100.0 } })
    $p = [pscustomobject]@{ pageId = "$pg"; size = $rows.Count }
    if ((($pg + 1) * 50) -lt $all.Count) { $p | Add-Member -NotePropertyName nextPageId -NotePropertyValue ("" + ($pg + 1)) }
    return [pscustomobject]@{ data = $rows; paging = $p }
  }
  if ($Uri -like '*/monitors/-?*') {
    if ($Uri -like '*view=details*') { throw (New-Object System.Net.WebException 'The remote server returned an error: (400) Bad Request.') }
    return [pscustomobject]@{ data = 'monitors' }
  }
  if ($Uri -like '*/devices/*/reports/memory-utilization*') { return [pscustomobject]@{ data = 'mem' } }
  throw ('unexpected ' + $Uri)
}
& (Join-Path $PSScriptRoot 'wug_lastpoll_collect.ps1') -Base https://wug-host.example.test:9644 -Groups @('Big(A)', 'Server', 'Leaf') -OutDir (Join-Path $PSScriptRoot 'out') -ProbeSwap -RawDir (Join-Path $PSScriptRoot 'raw')
"""


def test_CPU報表上限_拆子群組合併_拆不動標PARTIAL_成員500逐子群組_SWAP探測不用details(tmp_path):
    shutil.copy(SRC, tmp_path / "wug_lastpoll_collect.ps1")
    (tmp_path / "wug_account.txt").write_text("username=u\npassword=p\n", encoding="utf-8")
    (tmp_path / "mock.ps1").write_text(MOCK, encoding="utf-8")
    r = subprocess.run([PS, "-NoProfile", "-File", str(tmp_path / "mock.ps1")], capture_output=True, timeout=180)
    text = r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")
    assert r.returncode == 0, text
    batch = next((tmp_path / "out").iterdir())
    snap = json.loads((batch / "snap.json").read_text(encoding="utf-8"))
    man = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
    g = {x["query"]: x for x in snap["groups"]}
    big = g["Big(A)"]
    assert big["ok"] and len(big["members"]) == 153                                  # 名稱寬鬆比對：Big(A) 對到 Big (A)
    cpu = big["metrics"]["cpu"]
    assert cpu["ok"] is True and cpu["partial"] is False and len({x["id"] for x in cpu["rows"]}) == 153   # 拆開後全拿到
    leaf = g["Leaf"]["metrics"]["cpu"]
    assert leaf["ok"] is False and leaf["partial"] is True and "WhatsUp report cap" in leaf["error"] and len(leaf["rows"]) == 100
    assert g["Leaf"]["metrics"]["mem"]["ok"] is True and len(g["Leaf"]["metrics"]["mem"]["rows"]) == 120   # 記憶體分頁照讀完
    srv = g["Server"]
    assert srv["ok"] and len(srv["members"]) == 2 and "sub groups one by one" in srv["members_via"]
    assert [c["cpu"] for c in man["counts"]] == [153, 2, 100]
    assert "view=details" not in text and "monitors saved" in text and "memory saved" in text
    assert "PARTIAL" in text and "calls" in text
