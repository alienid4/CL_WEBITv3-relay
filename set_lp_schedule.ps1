# SET-LP-SCHEDULE-SCRIPT v1 (2026-11-15): change when the WhatsUp "last polled" collection task runs (user agreed 11-15 evening).
# 06:00-09:00 every 30 minutes (fresh data before the morning daily check), 09:00-18:00 every hour.
# About 15 runs a day instead of 26, because each run now also asks WhatsUp about ~190 devices one by one.
# Only the triggers change; the task's action and account stay as they are. Prints the triggers before and after.
param([string]$Task = 'webit3_wug_lastpoll')
$ErrorActionPreference = 'Stop'
$t = Get-ScheduledTask -TaskName $Task
Write-Host 'BEFORE:'
foreach ($x in $t.Triggers) { Write-Host ('  start ' + $x.StartBoundary + '  every ' + $x.Repetition.Interval + '  for ' + $x.Repetition.Duration) }
$a = New-ScheduledTaskTrigger -Daily -At 06:00
$a.Repetition = (New-ScheduledTaskTrigger -Once -At 06:00 -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Hours 3)).Repetition
$b = New-ScheduledTaskTrigger -Daily -At 09:00
$b.Repetition = (New-ScheduledTaskTrigger -Once -At 09:00 -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Hours 9)).Repetition
Set-ScheduledTask -TaskName $Task -Trigger $a, $b | Out-Null
$t = Get-ScheduledTask -TaskName $Task
Write-Host 'AFTER:'
foreach ($x in $t.Triggers) { Write-Host ('  start ' + $x.StartBoundary + '  every ' + $x.Repetition.Interval + '  for ' + $x.Repetition.Duration) }
Write-Host ('NEXT RUN: ' + (Get-ScheduledTaskInfo -TaskName $Task).NextRunTime)
