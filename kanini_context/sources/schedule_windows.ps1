# Registers a nightly Windows task that checks the sources due today (schedule.json) and rebuilds the release.
#   powershell -ExecutionPolicy Bypass -File kanini_context\sources\schedule_windows.ps1            # at 02:00
#   powershell -ExecutionPolicy Bypass -File kanini_context\sources\schedule_windows.ps1 -At 23:30
#   Unregister-ScheduledTask -TaskName "Kanini Context source check"                              # to remove it
# Docker Desktop must be running at that time, since the rebuild needs the database. Each run's report is appended
# to kanini_context\sources\check_log.txt.
param([string]$At = "02:00")
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$py = Join-Path $repo ".venv\Scripts\python.exe"
$action = New-ScheduledTaskAction -Execute $py -Argument "-m kanini_context.sources.check_sources --due --then-build" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable
Register-ScheduledTask -TaskName "Kanini Context source check" -Action $action -Trigger $trigger -Settings $settings `
  -Description "Checks each Kanini Context source for a new edition on its schedule (schedule.json) and rebuilds the release." -Force
Write-Host "Registered: every day at $At, $py -m kanini_context.sources.check_sources --due --then-build (in $repo)"
