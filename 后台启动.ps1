$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$config = Get-Content -LiteralPath (Join-Path $root 'config.json') -Encoding UTF8 -Raw | ConvertFrom-Json
$port = $config.port
$existing = $null
try { $existing = Invoke-RestMethod -Uri ('http://127.0.0.1:' + $port + '/api/session') -TimeoutSec 2 } catch { }
if ($existing) {
    Write-Output "端口 $port 已被使用，请检查 http://127.0.0.1:$port"
    exit 0
}
$logDir = Join-Path $root 'data\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$process = Start-Process powershell.exe -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + (Join-Path $root 'run.ps1') + '"'),'serve') -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'server.log') -RedirectStandardError (Join-Path $logDir 'server-error.log') -PassThru
$process.Id | Set-Content -LiteralPath (Join-Path $logDir 'launcher.pid')
Write-Output "后台启动中：http://127.0.0.1:$port；日志位于 $logDir"
