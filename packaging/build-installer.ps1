# Builds dist\canon-m3-ascom-<version>-setup.exe:
#   1. the standalone package (build-exe.ps1)
#   2. wdi-simple.exe for the WinUSB option (build-wdi-simple.ps1, only if missing)
#   3. the Inno Setup wizard (packaging\canon-m3-ascom.iss)
# Run from the project root:  powershell -ExecutionPolicy Bypass -File packaging\build-installer.ps1

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

& "$PSScriptRoot\build-exe.ps1"
$wdi = 'build\third_party\libwdi\x64\Release\examples\wdi-simple.exe'
if (-not (Test-Path $wdi)) { & "$PSScriptRoot\build-wdi-simple.ps1" }

$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup)" }

$version = & .venv\Scripts\python.exe -c "import canon_m3_ascom; print(canon_m3_ascom.__version__)"
& $iscc /Qp "/DAppVersion=$version" "/DPackageDir=$root\dist\canon-m3-ascom-$version-windows" "/DWdiSimple=$root\$wdi" packaging\canon-m3-ascom.iss
if ($LASTEXITCODE) { throw "Inno Setup compiler failed" }
Get-Item "dist\canon-m3-ascom-$version-setup.exe" | Format-Table Name, @{ n = 'MB'; e = { [math]::Round($_.Length / 1MB, 1) } }
