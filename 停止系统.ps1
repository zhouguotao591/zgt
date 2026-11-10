$ErrorActionPreference = 'Stop'
$config = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'config.json') -Encoding UTF8 -Raw | ConvertFrom-Json
$baseUrl = 'http://127.0.0.1:' + $config.port
New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot 'data') | Out-Null
'intentional stop' | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'data\watchdog.stop') -Encoding UTF8
try {
    $session = Invoke-RestMethod -Uri ($baseUrl + '/api/session') -TimeoutSec 5
    $headers = @{'X-PC28-Token'=$session.token}
    $controlFile = Join-Path $PSScriptRoot 'data\local-control-token.txt'
    if (Test-Path -LiteralPath $controlFile) { $headers['X-PC28-Control'] = (Get-Content -LiteralPath $controlFile -Raw -Encoding UTF8).Trim() }
    $result = Invoke-RestMethod -Uri ($baseUrl + '/api/shutdown') -Method Post -Headers $headers -ContentType 'application/json' -Body '{}' -TimeoutSec 5
    Write-Output '已请求本地PC28服务停止。'
} catch { Write-Output '已通知守护停止；服务当前未响应，将由守护完成停止。' }
