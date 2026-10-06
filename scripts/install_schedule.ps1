# Register (or update) the daily price-watch task in Windows Task Scheduler.
#   powershell -ExecutionPolicy Bypass -File scripts\install_schedule.ps1 [-Time 08:00] [-Uninstall]
# Runs as the current user while logged on (needed for toasts + user env vars like SERPAPI_API_KEY).
# StartWhenAvailable: if the PC was asleep/off at the scheduled time, it runs at next wake.
param(
    [string]$Time = "08:00",
    [string]$TaskName = "searchProduct-PriceWatch",
    [switch]$Uninstall
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "removed $TaskName"
    exit 0
}

$action = New-ScheduledTaskAction -Execute "$root\pricewatch.cmd" -Argument "run" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Daily price watch for searchProduct/watchlist.json (report: reports\price-watch\latest.md)" -Force | Out-Null
Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo | Select-Object TaskName, NextRunTime
