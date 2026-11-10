param()
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$manifest=Get-Content -LiteralPath (Join-Path $root 'RELEASE-MANIFEST.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$count=0
foreach($entry in $manifest.files.PSObject.Properties){$file=Join-Path $root $entry.Name;if(-not(Test-Path -LiteralPath $file -PathType Leaf)){throw ('缺少文件：'+$entry.Name)};$sha=(Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant();if($sha -ne $entry.Value){throw ('文件已变化：'+$entry.Name)};$count++}
Write-Output ('文件校验通过：'+$count+'个；预置2026开奖'+$manifest.history.count+'期；截止期号'+$manifest.history.last_id)
