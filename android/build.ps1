param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$lcRoot = Split-Path -Parent $PSScriptRoot
if (-not $env:JAVA_HOME) {
    $lcJavac = (Get-Command javac -ErrorAction Stop).Source
    $env:JAVA_HOME = Split-Path -Parent (Split-Path -Parent $lcJavac)
}
if (-not $env:GRADLE_USER_HOME) { $env:GRADLE_USER_HOME = Join-Path $PSScriptRoot '.gradle' }
$lcSdk = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } elseif ($env:ANDROID_HOME) { $env:ANDROID_HOME } else { Join-Path $PSScriptRoot 'sdk' }
$lcSource = Join-Path $PSScriptRoot 'upstream\wolpi-prim-ftpd-f0aa774'
[IO.File]::WriteAllText((Join-Path $lcSource 'local.properties'), ('sdk.dir=' + $lcSdk.Replace('\','/')), [Text.UTF8Encoding]::new($false))
Set-Location -LiteralPath $lcSource
$lcGradle = Join-Path $PSScriptRoot 'tools\gradle-9.3.0\bin\gradle.bat'
if (-not (Test-Path -LiteralPath $lcGradle) -and $env:GRADLE_HOME) { $lcGradle = Join-Path $env:GRADLE_HOME 'bin\gradle.bat' }
if (-not (Test-Path -LiteralPath $lcGradle)) { $lcGradle = Join-Path $lcSource 'gradlew.bat' }
& $lcGradle ':primitiveFTPd:assembleRelease' '--no-daemon' '--console=plain'
if ($LASTEXITCODE -ne 0) { throw 'Android build failed' }
Set-Location -LiteralPath $lcRoot
& $Python (Join-Path $PSScriptRoot 'sign_apk.py')
if ($LASTEXITCODE -ne 0) { throw 'APK signing failed' }
