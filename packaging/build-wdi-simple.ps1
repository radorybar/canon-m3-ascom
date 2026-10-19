# Builds wdi-simple.exe (libwdi 1.5.1, WinUSB only) for the installer's "install WinUSB driver" option.
# Needs Visual Studio 2022 (C++ x64 tools). Downloads, into build\third_party:
#   - libwdi source (git, tag v1.5.1)
#   - Microsoft WDK 8.0 redistributable components (wdfcoinstaller.msi, 31 MB) - WinUSB co-installers
# Output: build\third_party\libwdi\x64\Release\examples\wdi-simple.exe

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$tp = Join-Path $root 'build\third_party'
New-Item -ItemType Directory -Force $tp | Out-Null

# 1. WDK 8.0 redistributable (administrative extraction, nothing is installed)
$wdk = Join-Path $tp 'wdk\Windows Kits\8.0'
if (-not (Test-Path "$wdk\redist\wdf\x64\winusbcoinstaller2.dll")) {
    New-Item -ItemType Directory -Force "$tp\wdk-msi" | Out-Null
    Invoke-WebRequest 'https://go.microsoft.com/fwlink/p/?LinkID=253170' -OutFile "$tp\wdk-msi\wdfcoinstaller.msi" -UseBasicParsing
    $sig = Get-AuthenticodeSignature "$tp\wdk-msi\wdfcoinstaller.msi"
    if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {
        throw "wdfcoinstaller.msi is not validly signed by Microsoft"
    }
    $p = Start-Process msiexec -ArgumentList '/a', "`"$tp\wdk-msi\wdfcoinstaller.msi`"", '/qn', "TARGETDIR=`"$tp\wdk`"" -Wait -PassThru
    if ($p.ExitCode) { throw "msiexec /a failed: $($p.ExitCode)" }
}

# 2. libwdi source
$lw = Join-Path $tp 'libwdi'
if (-not (Test-Path "$lw\libwdi.sln")) {
    git clone -q --depth 1 --branch v1.5.1 https://github.com/pbatard/libwdi.git $lw
    if ($LASTEXITCODE) { throw "git clone failed" }
}

# 3. Configure: WinUSB only (no libusb0/libusbK), no ARM64 (WDK 8.0 has no ARM64 WinUSB files)
$cfgPath = "$lw\msvc\config.h"
$cfg = Get-Content $cfgPath -Raw
$cfg = $cfg -replace '(?m)^#define WDK_DIR ".*"', ('#define WDK_DIR "' + ($wdk -replace '\\', '/') + '"')
$cfg = $cfg -replace '(?m)^#define LIBUSB0_DIR ', '// #define LIBUSB0_DIR '
$cfg = $cfg -replace '(?m)^#define LIBUSBK_DIR ', '// #define LIBUSBK_DIR '
$cfg = $cfg -replace '(?m)^#define OPT_ARM\s*$', '// #define OPT_ARM'
[IO.File]::WriteAllText($cfgPath, $cfg)

$proj = "$lw\libwdi\.msvc\libwdi_static.vcxproj"
$x = Get-Content $proj -Raw
$x = [regex]::Replace($x, '\s*<ProjectReference Include="installer_arm64\.vcxproj">.*?</ProjectReference>', '',
                      [Text.RegularExpressions.RegexOptions]::Singleline)
[IO.File]::WriteAllText($proj, $x)
$sln = Get-Content "$lw\libwdi.sln" | Where-Object { $_ -notmatch '\{6AC16F78-F266-4AE0-BD63-550A55F54C15\}\..*Build\.0' }
Set-Content "$lw\libwdi.sln" $sln

# 4. Build. The pre-build step runs "embedder" from the current directory, which cmd refuses when
#    NoDefaultCurrentDirectoryInExePath is set.
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$msbuild = & $vswhere -latest -requires Microsoft.Component.MSBuild -find 'MSBuild\**\Bin\MSBuild.exe' | Select-Object -First 1
if (-not $msbuild) { throw "MSBuild (Visual Studio 2022) not found" }
Remove-Item Env:NoDefaultCurrentDirectoryInExePath -ErrorAction SilentlyContinue
Push-Location $lw
try {
    & $msbuild libwdi.sln '/t:examples\wdi-simple' /p:Configuration=Release /p:Platform=x64 /m /v:minimal /nologo
    if ($LASTEXITCODE) { throw "MSBuild failed" }
} finally { Pop-Location }
Get-Item "$lw\x64\Release\examples\wdi-simple.exe" | Format-Table FullName, Length
