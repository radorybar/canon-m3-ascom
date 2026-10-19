# Builds a standalone Windows package (no Python needed on the target PC):
#   canon-m3-ascom.exe            the driver
#   canon-m3-ascom.exe diag ...   first-contact diagnostics
# Run from the project root:  powershell -ExecutionPolicy Bypass -File packaging\build-exe.ps1
# Output: dist\canon-m3-ascom-<version>-windows.zip
#
# --onedir rather than --onefile: one process (onefile runs a launcher plus a child that can be left
# orphaned), faster start-up, nothing unpacked into %TEMP% on every start.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = '.venv\Scripts\python.exe'
& $py -m pip install --quiet pyinstaller

$version = & $py -c "import canon_m3_ascom; print(canon_m3_ascom.__version__)"
# generate the WPD COM wrappers into comtypes.gen now, so they are bundled (Program Files is read-only at run time)
& $py -c "from canon_m3_ascom.ptp.wpd import _com; _com()"
$name = "canon-m3-ascom-$version-windows"
$out = "dist\$name"
Remove-Item -Recurse -Force $out, "$out.zip", build\pyinstaller -ErrorAction SilentlyContinue
if (Test-Path $out) { throw "cannot remove $out (is a shell or program using that folder?)" }

& $py -m PyInstaller --noconfirm --clean --onedir --console --name canon-m3-ascom `
    --distpath dist\pyinstaller --workpath build\pyinstaller --specpath build\pyinstaller `
    --collect-binaries libusb_package --collect-data libusb_package `
    --collect-submodules uvicorn `
    --collect-submodules canon_m3_ascom `
    --collect-submodules comtypes `
    --collect-submodules pystray `
    packaging\canon_m3_ascom_entry.py
if ($LASTEXITCODE) { throw "PyInstaller failed" }
Move-Item dist\pyinstaller\canon-m3-ascom $out
Remove-Item -Recurse -Force dist\pyinstaller

Copy-Item LICENSE, README.md $out
# the packaged default is the real camera, with the LCD off for imaging
$cfg = (Get-Content config.example.toml -Raw) -replace '(?m)^backend = "sim"', 'backend = "ptp"' `
    -replace '(?m)^lcd_off = false', 'lcd_off = true'
# UTF-8 without BOM: the installer reads and rewrites this file
[IO.File]::WriteAllText((Join-Path (Resolve-Path $out) 'config.example.toml'), $cfg, (New-Object Text.UTF8Encoding $false))
@'
@echo off
rem Starts the canon-m3-ascom camera driver. Close the window to stop it.
cd /d "%~dp0"
if not exist config.toml copy config.example.toml config.toml >nul
title canon-m3-ascom driver
canon-m3-ascom.exe --config config.toml
pause
'@ | Out-File -Encoding ascii "$out\start-driver.cmd"
@'
@echo off
rem Checks the camera connection step by step and takes one 1 s test frame.
cd /d "%~dp0"
canon-m3-ascom.exe diag --shoot 1
pause
'@ | Out-File -Encoding ascii "$out\diagnose-camera.cmd"

Compress-Archive -Force -Path $out -DestinationPath "$out.zip"
$size = (Get-ChildItem $out -Recurse | Measure-Object Length -Sum).Sum
Write-Host ("Built {0}.zip  ({1:N0} MB unpacked, {2:N0} MB zip)" -f $out, ($size / 1MB), ((Get-Item "$out.zip").Length / 1MB))
