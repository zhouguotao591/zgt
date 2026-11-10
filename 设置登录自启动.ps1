# 用户手动运行此脚本可注册当前用户登录启动；不需要管理员权限。
$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'run.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $script + '" watchdog') -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'PC28LocalResearch' -Action $action -Trigger $trigger -Settings $settings -Description 'PC28官方开奖采集与本地模拟研究平台' -Force | Out-Null
Write-Output '已注册当前用户登录启动。移除命令：Unregister-ScheduledTask -TaskName PC28LocalResearch -Confirm:$false'
