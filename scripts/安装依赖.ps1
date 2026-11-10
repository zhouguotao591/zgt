param([string]$PythonExe='')
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
if(-not $PythonExe){if($env:PC28_PYTHON){$PythonExe=$env:PC28_PYTHON}else{$python=Get-Command python.exe -ErrorAction SilentlyContinue;if($python -and $python.Source -notmatch 'WindowsApps'){$PythonExe=$python.Source}}}
if(-not $PythonExe){throw '请先安装Python 3.11或以上（勾选Add Python to PATH），或通过-PythonExe指定python.exe。'}
& $PythonExe -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)"
if($LASTEXITCODE -ne 0){throw 'Python版本检查失败'}
if(-not(Test-Path -LiteralPath (Join-Path $root '.venv/Scripts/python.exe'))){& $PythonExe -m venv (Join-Path $root '.venv');if($LASTEXITCODE -ne 0){throw '创建独立虚拟环境失败'}}
$runtime=Join-Path $root '.venv/Scripts/python.exe'
& $runtime -m pip install -r (Join-Path $root 'requirements-windows.txt')
if($LASTEXITCODE -ne 0){throw '安装依赖失败，请检查服务器网络或pip镜像设置'}
& $runtime -X utf8 -c "import sqlite3,numpy,png; from cryptography.hazmat.primitives.ciphers.aead import AESGCM; from qrcode.image.pure import PyPNGImage; from zoneinfo import ZoneInfo; ZoneInfo('Asia/Shanghai'); ZoneInfo('America/Vancouver'); print('Windows release dependencies OK')"
if($LASTEXITCODE -ne 0){throw '依赖校验失败'}
Write-Output '安装完成。请配置HTTPS域名与反向代理，再运行后台启动.ps1。'
