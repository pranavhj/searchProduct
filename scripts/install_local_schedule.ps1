# Register (or update) the frequent local-pickup price-watch task in Windows Task Scheduler.
#   powershell -ExecutionPolicy Bypass -File scripts\install_local_schedule.ps1 [-Start 09:00] [-EveryHours 2] [-Hours 14] [-Uninstall]
# Runs `pricewatch run --local` (FB Marketplace / Craigslist / OfferUp only) every few hours during the day;
# Discord only gets a message when a new good deal shows up. Missed runs (PC asleep) are not caught up:
# the next run covers them. Runs as the current user while logged on (toasts + user env vars).
param(
    [string]$Start = "09:00",
    [int]$EveryHours = 2,
    [int]$Hours = 14,
    [string]$TaskName = "searchProduct-PriceWatch-Local",
    [string]$RunArgs = "run --local",
    [switch]$Uninstall
)
# Deep search (installed 2026-11-05, items with "deep": true):
#   -TaskName searchProduct-PriceWatch-Deep -RunArgs "run --deep" -Start 08:00 -EveryHours 4 -Hours 13
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "removed $TaskName"
    exit 0
}

$action = New-ScheduledTaskAction -Execute "$root\pricewatch.cmd" -Argument $RunArgs -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At $Start
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At $Start -RepetitionInterval (New-TimeSpan -Hours $EveryHours) `
    -RepetitionDuration (New-TimeSpan -Hours $Hours)).Repetition
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 90) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Price watch '$RunArgs' every $EveryHours h" -Force | Out-Null
Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo | Select-Object TaskName, NextRunTime
