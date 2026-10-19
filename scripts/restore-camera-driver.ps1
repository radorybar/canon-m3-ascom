# Removes the WinUSB driver that Zadig/libwdi installed for Canon cameras (USB VID 04A9), so Windows uses
# its standard camera driver again (Explorer access, WPD transport).
# Run:  right-click > Run with PowerShell, or  powershell -ExecutionPolicy Bypass -File scripts\restore-camera-driver.ps1
# Needs administrator rights; the script asks for them. -NoPause: no "press Enter" (used by the installer).
param([switch]$NoPause)

$ErrorActionPreference = 'Stop'
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
    if ($NoPause) { $argList += '-NoPause' }
    Start-Process powershell -Verb RunAs -Wait -ArgumentList $argList
    exit
}

# pnputil /enum-drivers prints one block per driver package, separated by blank lines
$blocks = ((pnputil /enum-drivers) -join "`n") -split "`n\s*`n"
$targets = foreach ($b in $blocks) {
    if ($b -match 'Provider Name:\s+libwdi' -and $b -match 'VID_04A9' -and $b -match 'Published Name:\s+(oem\d+\.inf)') {
        $Matches[1]
    }
}

if (-not $targets) {
    Write-Host "No Zadig/libwdi driver for a Canon camera found - nothing to do."
} else {
    foreach ($inf in $targets) {
        Write-Host "Removing $inf ..."
        pnputil /delete-driver $inf /uninstall /force
    }
    pnputil /scan-devices | Out-Null
    Write-Host ""
    Write-Host "Done. Unplug the camera and plug it in again; Windows installs its standard driver."
}
if (-not $NoPause) { Read-Host "Press Enter to close" }
