param([string]$Python = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not $Python) { $Python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python)) { $Python = (Get-Command python -ErrorAction Stop).Source }
$lcArgs = @('-m','PyInstaller','--noconfirm','--windowed','--onedir','--name','LC互传','--distpath','发布','--workpath','build','--specpath','build','--collect-all','paramiko','--exclude-module','matplotlib','--exclude-module','scipy','--exclude-module','pandas','--exclude-module','numpy','--exclude-module','PySide6.QtWebEngineWidgets','--exclude-module','PySide6.QtWebEngineCore')
$lcDecimal = & $Python -c 'import pathlib,sys; print(next((str(p) for p in (pathlib.Path(sys.base_prefix)/"Library/bin").glob("libmpdec*.dll")), ""))'
if ($lcDecimal) { $lcArgs += @('--add-binary', ($lcDecimal + ';.')) }
& $Python @lcArgs desktop.py
if ($LASTEXITCODE -ne 0) { throw 'Windows build failed' }
$lcOutput = Join-Path $PSScriptRoot '发布\LC互传'
foreach ($lcName in @('README.md','手机设置.html','THIRD_PARTY_NOTICES.md','LICENSE','NOTICE')) { Copy-Item -LiteralPath (Join-Path $PSScriptRoot $lcName) -Destination $lcOutput }
& $Python collect_licenses.py
if ($LASTEXITCODE -ne 0) { throw 'License collection failed' }
& (Join-Path $PSScriptRoot 'prepare_launchers.ps1')
Write-Output "Built $lcOutput\LC互传.exe"
