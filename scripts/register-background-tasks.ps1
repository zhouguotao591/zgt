$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $PSScriptRoot 'task-runner.ps1'
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
# Use the same logged-in identity so DPAPI robot credentials and TLS storage remain available.
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$login = New-ScheduledTaskTrigger -AtLogOn -User $user
$retry = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
foreach ($entry in @(@('PC28LocalResearch','watchdog'),@('PC28HttpsProxy','proxy'))) {
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $runner + '" -Mode ' + $entry[1]) -WorkingDirectory $root
    Register-ScheduledTask -TaskName $entry[0] -Action $action -Trigger @($login,$retry) -Settings $settings -Principal $principal -Description 'PC28 background process managed independently by Windows Task Scheduler' -Force | Out-Null
}
Write-Output 'Registered PC28LocalResearch and PC28HttpsProxy for the current user; logon startup and one-minute recovery enabled.'
