param([Parameter(ValueFromRemainingArguments=$true)][string[]]$AppArgs)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$candidates = @()
if ($env:PC28_PYTHON) { $candidates += $env:PC28_PYTHON }
$candidates += (Join-Path $PSScriptRoot '.venv\Scripts\python.exe')
$candidates += (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe')
$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if ($pythonCommand -and $pythonCommand.Source -notmatch 'WindowsApps') { $candidates += $pythonCommand.Source }
$pythonExe = $null
foreach ($candidate in $candidates) {
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        & $candidate -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { $pythonExe = $candidate; break }
    }
}
if (-not $pythonExe) { throw '需要Python 3.11及以上。安装后设置PC28_PYTHON，或在项目内创建.venv。' }
if (-not $AppArgs -or $AppArgs.Count -eq 0) { $AppArgs = @('serve') }
if ($AppArgs[0] -eq 'serve') { $AppArgs[0] = 'watchdog' }
& $pythonExe -X utf8 -m pc28 --root $PSScriptRoot @AppArgs
exit $LASTEXITCODE
