# Installs canon-m3-ascom from the unpacked or cloned project folder (installation from source).
# Run:  powershell -ExecutionPolicy Bypass -File scripts\install.ps1
# Safe to run again (updates the installation).

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
Write-Host "canon-m3-ascom install in $root"

# 1. Python 3.12
$hasPy = $false
try { & py -3.12 --version 2>$null | Out-Null; $hasPy = ($LASTEXITCODE -eq 0) } catch {}
if (-not $hasPy) {
    Write-Host "Python 3.12 not found - installing with winget (user scope)..."
    winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine')
    & py -3.12 --version
}

# 2. virtual environment + dependencies
if (-not (Test-Path .venv\Scripts\python.exe)) { & py -3.12 -m venv .venv }
& .venv\Scripts\python.exe -m pip install --quiet --upgrade pip
& .venv\Scripts\python.exe -m pip install --quiet -e .
Write-Host "Python packages installed."

# 3. configuration (kept if it already exists)
if (-not (Test-Path config.toml)) {
    @'
# canon-m3-ascom - local configuration. Full documentation: config.example.toml

[server]
host = "0.0.0.0"
port = 11112

[camera]
backend = "ptp"
default_iso = 800
exposure_max = 300.0       # verified on the M3 up to 120 s (CHDK allows up to 2048 s)

[ptp]
lcd_off = true            # less sensor heat during the night
prevent_shutdown = true
dark_frame_off = true
'@ | Out-File -Encoding utf8 config.toml
    Write-Host "Created config.toml (backend ptp)."
} else {
    Write-Host "config.toml exists - left unchanged."
}

# 4. start-up shortcut (optional autostart at logon)
$startup = [Environment]::GetFolderPath('Startup')
$lnk = Join-Path $startup 'canon-m3-ascom driver.lnk'
$answer = Read-Host "Start the driver automatically at Windows logon? (y/N)"
if ($answer -match '^[yYaA]') {
    $ws = New-Object -ComObject WScript.Shell
    $s = $ws.CreateShortcut($lnk)
    $s.TargetPath = Join-Path $root 'scripts\start-driver.cmd'
    $s.WorkingDirectory = $root
    $s.WindowStyle = 7  # minimized
    $s.Save()
    Write-Host "Autostart shortcut created: $lnk"
}

Write-Host ""
Write-Host "Next:"
Write-Host "  1. Zadig: give the camera the WinUSB driver (once per PC), see docs\INSTALL.md"
Write-Host "  2. Test:  .venv\Scripts\python.exe -m canon_m3_ascom.ptpdiag --shoot 1"
Write-Host "  3. Start: scripts\start-driver.cmd, then connect 'Canon EOS M3 (CHDK)' in N.I.N.A."
