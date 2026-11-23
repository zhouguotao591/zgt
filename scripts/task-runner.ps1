param([ValidateSet('watchdog','proxy')][string]$Mode = 'watchdog')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
if ($Mode -eq 'watchdog') {
    # Respect an intentional stop; the normal start command clears this marker.
    if (Test-Path -LiteralPath (Join-Path $root 'data\watchdog.stop')) { exit 0 }
    & (Join-Path $root 'run.ps1') watchdog
    exit $LASTEXITCODE
}
$exe = Join-Path $root 'deployment\caddy\caddy.exe'
$config = Join-Path $root 'deployment\Caddyfile'
$existing = Get-Process -Name caddy -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe }
if ($existing) { exit 0 }
$logs = Join-Path $root 'data\logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$process = Start-Process -FilePath $exe -ArgumentList @('run','--config',('"' + $config + '"'),'--adapter','caddyfile') -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs 'caddy-task.stdout.log') -RedirectStandardError (Join-Path $logs 'caddy-task.stderr.log') -PassThru
$process.WaitForExit()
exit $process.ExitCode
