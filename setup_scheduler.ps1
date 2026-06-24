# setup_scheduler.ps1
# Run this once from PowerShell to register the Task Scheduler task.
# Re-running it is safe: the old task is removed and replaced cleanly.
# If you get an "access denied" error, right-click PowerShell and run as Administrator.

$TaskName   = "FuelFinderSnapshot"
$scriptFile = Join-Path $PSScriptRoot "run_collection.ps1"

# Action: run the wrapper script silently in a hidden PowerShell window
$action = New-ScheduledTaskAction `
    -Execute          "powershell.exe" `
    -Argument         "-NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$scriptFile`"" `
    -WorkingDirectory $PSScriptRoot

# Four daily triggers, Monday to Friday only
$weekdays = "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"
$triggers = @("09:45", "11:30", "14:00", "16:30") | ForEach-Object {
    $time = [DateTime]::Today.Add([TimeSpan]::Parse($_))
    New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At $time
}

# StartWhenAvailable: if the PC was off at trigger time, fire as soon as you log in.
# ExecutionTimeLimit: kill the task if it somehow runs for more than 2 hours.
# MultipleInstances IgnoreNew: if one run is still going at the next trigger time, skip it.
$settings = New-ScheduledTaskSettingsSet `
    -RunOnlyIfNetworkAvailable `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew

# Run as the current logged-in user. No password stored, no admin required at run time.
$principal = New-ScheduledTaskPrincipal `
    -UserId    "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel  Limited

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed existing task '$TaskName'."
}

Register-ScheduledTask `
    -TaskName    $TaskName `
    -Description "Fuel Finder 4x/day weekday snapshot collector" `
    -Action      $action `
    -Trigger     $triggers `
    -Settings    $settings `
    -Principal   $principal

Write-Host ""
Write-Host "Task '$TaskName' registered."
Write-Host "Runs Mon-Fri at 09:45, 11:30, 14:00, 16:30."
Write-Host "Missed runs (PC was off) fire automatically on next login."
Write-Host ""
Write-Host "Next scheduled run:"
(Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo).NextRunTime
Write-Host ""
Write-Host "To check on it later:"
Write-Host "  Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
Write-Host "  Get-Content '$PSScriptRoot\logs\collection.log' -Tail 30"
