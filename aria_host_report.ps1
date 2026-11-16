# ARIA-HOST-REPORT-SCRIPT v3 : console + CSV reports, dot-sourced at the end of aria_probe.ps1 (uses $out, $open0, $e2, $NameLike, $L).
# All numbers come from the same 5-minute points aria_probe just read; missing points are skipped (never counted as 0).
# Reports (user 11-04):
#   R1 opening peak 09:00-09:30 : CPU Max/Avg/Min per host, sorted by Max (Top10 + all)
#   R2 hosts that reached >= 90% CPU or memory in the last 24 hours (when and how high)
#   R3 per-host 24h resources : CPU Max/Avg, memory Max/Avg, swap Max
#   R4 cluster-wide CPU (all hosts in scope) : average of host CPU%, and used/total MHz when vCOps returns them
#   R5 Top10 by 24h CPU Max and Top10 by 24h memory Max
# A Teams-ready summary text is written to <log>_teams.txt (the system sends it after the batch is imported on .14).
$TH = 90
$w1 = $open0 + 1800000
function Get-Series($o, [string]$key) {
  $s = $null
  if ($o.stats -and $o.stats.values) { $s = @($o.stats.values[0].'stat-list'.stat) | Where-Object { $_.statKey.key -eq $key } | Select-Object -First 1 }
  $pts = New-Object System.Collections.ArrayList
  if ($s) {
    $ta = @($s.timestamps); $da = @($s.data)
    for ($i = 0; $i -lt $ta.Count; $i++) {
      $x = $da[$i]
      if (($null -ne $x) -and ("$x" -ne '') -and ("$x" -ne 'NaN')) { [void]$pts.Add([pscustomobject]@{ t = [int64]$ta[$i]; v = [double]$x }) }
    }
  }
  return ,$pts.ToArray()
}
function Stat3($pts) {
  if (-not $pts -or $pts.Count -eq 0) { return $null }
  $m = $pts | Measure-Object -Property v -Average -Maximum -Minimum
  $top = $pts | Sort-Object v -Descending | Select-Object -First 1
  return [pscustomobject]@{ Max = [math]::Round($m.Maximum, 2); Avg = [math]::Round($m.Average, 2); Min = [math]::Round($m.Minimum, 2); N = $pts.Count; At = $top.t }
}
function T8([int64]$ms) { [DateTimeOffset]::FromUnixTimeMilliseconds($ms).ToOffset((New-TimeSpan -Hours 8)).ToString('MM-dd HH:mm') }

$rows = foreach ($o in @($out)) {
  if (($o -isnot [System.Collections.IDictionary]) -or [string]::IsNullOrWhiteSpace([string]$o['name'])) { continue }   # v3: never report a record without a host name
  if ($o.name -notlike $NameLike) { continue }
  $cpu = Get-Series $o 'cpu|usage_average'; $mem = Get-Series $o 'mem|usage_average'; $swp = Get-Series $o 'mem|swapped_average'
  $mhz = Get-Series $o 'cpu|usagemhz_average'; $cap = Get-Series $o 'cpu|totalCapacity_average'
  $op = Stat3 @($cpu | Where-Object { $_.t -ge $open0 -and $_.t -lt $w1 })
  $c24 = Stat3 $cpu; $m24 = Stat3 $mem; $s24 = Stat3 $swp
  [pscustomobject]@{
    Name = $o.name
    OpenMax = $(if ($op) { $op.Max } else { $null }); OpenAvg = $(if ($op) { $op.Avg } else { $null }); OpenMin = $(if ($op) { $op.Min } else { $null }); OpenN = $(if ($op) { $op.N } else { 0 })
    Cpu24Max = $(if ($c24) { $c24.Max } else { $null }); Cpu24Avg = $(if ($c24) { $c24.Avg } else { $null }); Cpu24MaxAt = $(if ($c24) { T8 $c24.At } else { '' })
    Mem24Max = $(if ($m24) { $m24.Max } else { $null }); Mem24Avg = $(if ($m24) { $m24.Avg } else { $null }); Mem24MaxAt = $(if ($m24) { T8 $m24.At } else { '' })
    Swap24Max = $(if ($s24) { $s24.Max } else { $null })
    CpuMhzLast = $(if ($mhz.Count) { [math]::Round($mhz[-1].v, 0) } else { $null }); CapMhzLast = $(if ($cap.Count) { [math]::Round($cap[-1].v, 0) } else { $null })
    Points24 = $(if ($c24) { $c24.N } else { 0 })
  }
}
$rows = @($rows)
$noData = @($rows | Where-Object { $_.Points24 -eq 0 })
"=== scope $NameLike : $($rows.Count) hosts, $($noData.Count) without CPU data ==="

# R1 opening peak
$r1 = @($rows | Where-Object { $_.OpenN -gt 0 } | Sort-Object OpenMax -Descending)
"=== R1 opening peak 09:00-09:30 CPU (Max/Avg/Min) : $($r1.Count) hosts ==="
$r1 | Select-Object -First 10 Name, OpenMax, OpenAvg, OpenMin, OpenN | Format-Table -AutoSize | Out-Host
# R2 >= 90% in 24h
$r2 = @($rows | Where-Object { ($_.Cpu24Max -ge $TH) -or ($_.Mem24Max -ge $TH) } | Sort-Object { [math]::Max([double]$_.Cpu24Max, [double]$_.Mem24Max) } -Descending)
"=== R2 hosts >= $TH% CPU or memory in the last 24h : $($r2.Count) ==="
$r2 | Select-Object Name, Cpu24Max, Cpu24MaxAt, Mem24Max, Mem24MaxAt | Format-Table -AutoSize | Out-Host
# R4 cluster-wide CPU
$withCpu = @($rows | Where-Object { $null -ne $_.Cpu24Avg })
$avgCpu = if ($withCpu.Count) { [math]::Round(($withCpu | Measure-Object Cpu24Avg -Average).Average, 2) } else { $null }
$openAvgAll = if ($r1.Count) { [math]::Round(($r1 | Measure-Object OpenAvg -Average).Average, 2) } else { $null }
$mhzRows = @($rows | Where-Object { ($null -ne $_.CpuMhzLast) -and ($null -ne $_.CapMhzLast) -and ($_.CapMhzLast -gt 0) })
$usedMhz = ($mhzRows | Measure-Object CpuMhzLast -Sum).Sum; $capMhz = ($mhzRows | Measure-Object CapMhzLast -Sum).Sum
$mhzPct = if ($capMhz) { [math]::Round(100 * $usedMhz / $capMhz, 2) } else { $null }
"=== R4 cluster-wide CPU : 24h average of host CPU% = $avgCpu ; opening 09:00-09:30 average = $openAvgAll ; latest used/total = $usedMhz / $capMhz MHz ($mhzPct%) over $($mhzRows.Count) hosts ==="
# R5 Top10
"=== R5 Top10 by 24h CPU Max ==="
$rows | Where-Object { $null -ne $_.Cpu24Max } | Sort-Object Cpu24Max -Descending | Select-Object -First 10 Name, Cpu24Max, Cpu24MaxAt, Cpu24Avg | Format-Table -AutoSize | Out-Host
"=== R5 Top10 by 24h memory Max ==="
$rows | Where-Object { $null -ne $_.Mem24Max } | Sort-Object Mem24Max -Descending | Select-Object -First 10 Name, Mem24Max, Mem24MaxAt, Mem24Avg, Swap24Max | Format-Table -AutoSize | Out-Host

# CSV (R3 = all columns, one row per host) + Teams-ready summary
$rows | Sort-Object Cpu24Max -Descending | Export-Csv "${L}_hosts_24h.csv" -NoTypeInformation -Encoding UTF8
"csv=${L}_hosts_24h.csv"
$lines = New-Object System.Collections.ArrayList
[void]$lines.Add("[vCOps] $NameLike  data until $(T8 $e2)  hosts=$($rows.Count) (no data: $($noData.Count))")
[void]$lines.Add("Hosts >= $TH% in 24h: $($r2.Count)")
foreach ($h in ($r2 | Select-Object -First 10)) { [void]$lines.Add("  $($h.Name)  CPU max $($h.Cpu24Max)% @$($h.Cpu24MaxAt)  MEM max $($h.Mem24Max)% @$($h.Mem24MaxAt)") }
[void]$lines.Add("Opening 09:00-09:30 CPU Top3: " + (($r1 | Select-Object -First 3 | ForEach-Object { "$($_.Name) $($_.OpenMax)%" }) -join ', '))
[void]$lines.Add("Cluster CPU: 24h avg $avgCpu%, opening avg $openAvgAll%" + $(if ($null -ne $mhzPct) { ", latest $mhzPct% of capacity" } else { '' }))
[IO.File]::WriteAllText("${L}_teams.txt", ($lines -join "`r`n"), (New-Object System.Text.UTF8Encoding($false)))
"teams text=${L}_teams.txt"
$lines | Out-Host
