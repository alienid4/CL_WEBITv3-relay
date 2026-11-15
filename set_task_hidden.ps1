# SET-TASK-HIDDEN-SCRIPT v2 (2026-11-15): make the WEBIT3 collection tasks run without a window (user: the black window is annoying,
# and closing it by mistake kills the run - 19:50 run was lost that way, result 0xC000013A).
# How: the task action becomes  conhost.exe --headless <old program> <old arguments>  (same program, same arguments, same folder,
# same account and triggers; only no console window). Tasks already headless are left alone. Prints every action before and after.
# Undo:  powershell -nop -file .\set_task_hidden.ps1 -Undo   (puts the old program and arguments back).
param([string]$Like = 'webit3_wug_*', [switch]$Undo)
$ErrorActionPreference = 'Stop'
$hc = Join-Path $env:SystemRoot 'System32\conhost.exe'
# v2: a task with no working folder (webit3_wug_daily) made New-ScheduledTaskAction fail; only pass it when there is one.
function NewAct([string]$Exe, [string]$Arg, [string]$Wd) {
  if ($Wd) { return New-ScheduledTaskAction -Execute $Exe -Argument $Arg -WorkingDirectory $Wd }
  return New-ScheduledTaskAction -Execute $Exe -Argument $Arg
}
foreach ($t in @(Get-ScheduledTask -TaskName $Like)) {
  $acts = @()
  $changed = $false
  foreach ($a in $t.Actions) {
    Write-Host ('[' + $t.TaskName + '] BEFORE: ' + $a.Execute + ' ' + $a.Arguments)
    $isHc = ($a.Execute -like '*conhost.exe') -and ($a.Arguments -like '--headless *')
    if ($Undo -and $isHc) {
      $rest = $a.Arguments.Substring(11)
      if ($rest.StartsWith('"')) { $i = $rest.IndexOf('"', 1); $exe = $rest.Substring(1, $i - 1); $arg = $rest.Substring($i + 1).TrimStart() }
      else { $i = $rest.IndexOf(' '); if ($i -lt 0) { $exe = $rest; $arg = '' } else { $exe = $rest.Substring(0, $i); $arg = $rest.Substring($i + 1) } }
      $acts += (NewAct $exe $arg $a.WorkingDirectory)
      $changed = $true
    } elseif ((-not $Undo) -and (-not $isHc)) {
      $arg = '--headless "' + $a.Execute + '"'
      if ($a.Arguments) { $arg += ' ' + $a.Arguments }
      $acts += (NewAct $hc $arg $a.WorkingDirectory)
      $changed = $true
    } else { $acts += $a }
  }
  if ($changed) {
    Set-ScheduledTask -TaskName $t.TaskName -Action $acts | Out-Null
    foreach ($a in (Get-ScheduledTask -TaskName $t.TaskName).Actions) { Write-Host ('[' + $t.TaskName + '] AFTER:  ' + $a.Execute + ' ' + $a.Arguments) }
  } else { Write-Host ('[' + $t.TaskName + '] no change') }
}
