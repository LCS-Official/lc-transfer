$ErrorActionPreference = 'Stop'
$lcOutput = Join-Path $PSScriptRoot '发布\LC互传'
$lcExe = Join-Path $lcOutput 'LC互传.exe'
if (-not (Test-Path -LiteralPath $lcExe) -or -not (Test-Path -LiteralPath (Join-Path $lcOutput '_internal'))) { throw 'Build the desktop client first.' }
$lcShortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $PSScriptRoot 'LC互传.lnk'))
$lcShortcut.TargetPath = $lcExe
$lcShortcut.WorkingDirectory = $lcOutput
$lcShortcut.IconLocation = "$lcExe,0"
$lcShortcut.Description = 'LC Transfer desktop client'
$lcShortcut.Save()
