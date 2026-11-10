param([Parameter(Mandatory=$true)][string]$PublicOrigin)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$uri=$null
if(-not [Uri]::TryCreate($PublicOrigin,[UriKind]::Absolute,[ref]$uri) -or $uri.Scheme -ne 'https' -or $uri.AbsolutePath -ne '/' -or $uri.Query -or $uri.Fragment -or $uri.UserInfo){throw '请填写真实HTTPS站点来源，例如https://pc28.example.com，不含路径、查询参数或账号密码。'}
$origin=$uri.GetLeftPart([UriPartial]::Authority)
if($uri.Host -match '(^|\.)invalid$|(^|\.)example\.(com|org|net)$'){throw '请替换为自己的真实域名，不要使用示例域名。'}
$configPath=Join-Path $root 'config.json'
$config=Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
$config.deployment.public_origin=$origin
$config | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $configPath -Encoding UTF8
Write-Output ('已设定站点来源：'+$origin+'。请确保反向代理使用该域名与HTTPS，并重启服务。')
