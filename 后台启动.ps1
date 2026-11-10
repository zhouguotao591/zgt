$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$config = Get-Content -LiteralPath (Join-Path $root 'config.json') -Encoding UTF8 -Raw | ConvertFrom-Json
$port = $config.port
$logDir = Join-Path $root 'data\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$proxyExe = Join-Path $root 'deployment\caddy\caddy.exe'
$proxyConfig = Join-Path $root 'deployment\Caddyfile'
if ((Test-Path -LiteralPath $proxyExe) -and (Test-Path -LiteralPath $proxyConfig)) {
    $existingProxy = Get-Process -Name caddy -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $proxyExe }
    if (-not $existingProxy) {
        Start-Process -FilePath $proxyExe -ArgumentList @('run','--config',('"' + $proxyConfig + '"'),'--adapter','caddyfile') -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'caddy.stdout.log') -RedirectStandardError (Join-Path $logDir 'caddy.stderr.log') | Out-Null
    }
}
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$process = Start-Process powershell.exe -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + (Join-Path $root 'run.ps1') + '"'),'watchdog') -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir ('watchdog-launcher-' + $stamp + '.log')) -RedirectStandardError (Join-Path $logDir ('watchdog-launcher-' + $stamp + '.error.log')) -PassThru
$process.Id | Set-Content -LiteralPath (Join-Path $logDir 'launcher.pid')
Write-Output "守护启动中：http://127.0.0.1:$port；已运行的守护不会重复启动；日志位于 $logDir"
