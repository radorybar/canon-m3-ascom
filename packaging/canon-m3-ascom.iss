; Inno Setup script for Canon M3 ASCOM: driver application + USB access to the camera.
; Built by packaging\build-installer.ps1, which passes AppVersion, PackageDir and WdiSimple.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef PackageDir
  #define PackageDir "..\dist\canon-m3-ascom-" + AppVersion + "-windows"
#endif
#ifndef WdiSimple
  #define WdiSimple "..\build\third_party\libwdi\x64\Release\examples\wdi-simple.exe"
#endif

[Setup]
AppId={{6E3B2C5A-6A9C-4E8B-9F21-3C7A5D0E9B41}
AppName=Canon M3 ASCOM
AppVersion={#AppVersion}
AppVerName=Canon M3 ASCOM {#AppVersion}
AppPublisher=radorybar
AppPublisherURL=https://github.com/radorybar/canon-m3-ascom
AppSupportURL=https://github.com/radorybar/canon-m3-ascom/issues
DefaultDirName={autopf}\canon-m3-ascom
DefaultGroupName=Canon M3 ASCOM
UsePreviousAppDir=no
UsePreviousGroup=no
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=canon-m3-ascom-{#AppVersion}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\canon-m3-ascom.exe
UninstallDisplayName=Canon M3 ASCOM {#AppVersion} (Canon EOS M3 camera driver)

[Files]
Source: "{#PackageDir}\*"; DestDir: "{app}"; Excludes: "start-driver.cmd,diagnose-camera.cmd"; Flags: recursesubdirs ignoreversion
Source: "{#WdiSimple}"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "..\scripts\restore-camera-driver.ps1"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "..\build\third_party\libwdi\COPYING-LGPL"; DestDir: "{app}\licenses\libwdi"; DestName: "COPYING-LGPL.txt"
; WinUSB co-installers embedded in wdi-simple.exe come from the Microsoft WDK 8.0 redistributable components
Source: "..\build\third_party\wdk\Windows Kits\8.0\redist\wdf\License.rtf"; DestDir: "{app}\licenses\microsoft-wdf-redist"

[Dirs]
; users must be able to edit the configuration and write logs
Name: "{commonappdata}\canon-m3-ascom"; Permissions: users-modify
Name: "{commonappdata}\canon-m3-ascom\logs"; Permissions: users-modify

[InstallDelete]
; installations of the project under its former name "m3alpaca" (same AppId, so this setup replaces them)
Type: filesandordirs; Name: "{autopf}\m3alpaca"
Type: filesandordirs; Name: "{commonprograms}\m3alpaca"
Type: files; Name: "{userstartup}\m3alpaca driver.lnk"

[Tasks]
Name: "autostart"; Description: "Start the driver automatically when I sign in"; Flags: unchecked
Name: "firewall"; Description: "Allow the driver through Windows Firewall (private networks, needed for discovery)"

[Icons]
Name: "{group}\Canon M3 ASCOM driver"; Filename: "{app}\canon-m3-ascom.exe"; Parameters: "--tray --config ""{commonappdata}\canon-m3-ascom\config.toml"""; WorkingDir: "{commonappdata}\canon-m3-ascom"; Flags: runminimized
Name: "{group}\Canon M3 ASCOM driver (console window)"; Filename: "{app}\canon-m3-ascom.exe"; Parameters: "--config ""{commonappdata}\canon-m3-ascom\config.toml"""; WorkingDir: "{commonappdata}\canon-m3-ascom"
Name: "{group}\Log files"; Filename: "{commonappdata}\canon-m3-ascom\logs"
Name: "{group}\Camera diagnostics (quit the driver first)"; Filename: "{cmd}"; Parameters: "/k ""{app}\canon-m3-ascom.exe"" diag --shoot 1"; WorkingDir: "{commonappdata}\canon-m3-ascom"
Name: "{group}\Install WinUSB driver for the camera"; Filename: "{app}\tools\wdi-simple.exe"; Parameters: "--vid 0x04A9 --pid 0x3299 --type 0 --name ""Canon EOS M3 (CHDK)"" --progressbar"; Comment: "Connect the camera (on, CHDK loaded) first"
Name: "{group}\Restore standard camera driver"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\tools\restore-camera-driver.ps1"""
Name: "{group}\Edit configuration"; Filename: "{win}\notepad.exe"; Parameters: """{commonappdata}\canon-m3-ascom\config.toml"""
Name: "{group}\Uninstall Canon M3 ASCOM"; Filename: "{uninstallexe}"
Name: "{userstartup}\Canon M3 ASCOM driver"; Filename: "{app}\canon-m3-ascom.exe"; Parameters: "--tray --config ""{commonappdata}\canon-m3-ascom\config.toml"""; WorkingDir: "{commonappdata}\canon-m3-ascom"; Flags: runminimized; Tasks: autostart

[Run]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""m3alpaca"""; Flags: runhidden; StatusMsg: "Removing the old firewall rule..."
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""canon-m3-ascom"" dir=in action=allow program=""{app}\canon-m3-ascom.exe"" enable=yes profile=private,domain"; Flags: runhidden; Tasks: firewall; StatusMsg: "Adding the Windows Firewall rule..."
Filename: "{cmd}"; Parameters: "/k ""{app}\canon-m3-ascom.exe"" diag --shoot 1"; WorkingDir: "{commonappdata}\canon-m3-ascom"; Description: "Test the camera connection now (camera on, CHDK loaded, USB connected)"; Flags: postinstall skipifsilent nowait unchecked runasoriginaluser
Filename: "{app}\canon-m3-ascom.exe"; Parameters: "--tray --config ""{commonappdata}\canon-m3-ascom\config.toml"""; WorkingDir: "{commonappdata}\canon-m3-ascom"; Description: "Start the Canon M3 ASCOM driver (icon in the notification area)"; Flags: postinstall skipifsilent nowait runasoriginaluser runminimized

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""canon-m3-ascom"""; Flags: runhidden; RunOnceId: "firewall"

[Code]
var
  PreparePage: TOutputMsgWizardPage;
  AccessPage: TInputOptionWizardPage;
  CurrentDriver: String;

{ Which Windows driver does the camera (VID 04A9) currently use? Empty if it is not connected. }
function DetectCameraDriver(): String;
var
  TmpFile: String;
  Lines: TArrayOfString;
  Code: Integer;
begin
  Result := '';
  TmpFile := ExpandConstant('{tmp}\camera-driver.txt');
  if Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
          '-NoProfile -ExecutionPolicy Bypass -Command "Get-PnpDevice -PresentOnly | Where-Object InstanceId -match ''VID_04A9'' | ' +
          'ForEach-Object { (Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName DEVPKEY_Device_Service).Data } | ' +
          'Select-Object -First 1 | Out-File -Encoding ascii ''' + TmpFile + '''"',
          '', SW_HIDE, ewWaitUntilTerminated, Code) and LoadStringsFromFile(TmpFile, Lines) and (GetArrayLength(Lines) > 0) then
    Result := Trim(Lines[0]);
end;

procedure InitializeWizard();
begin
  PreparePage := CreateOutputMsgPage(wpSelectTasks,
    'Prepare the camera', 'Settings on the Canon EOS M3 for remote capture',
    'Before using the driver:' + #13#10 + #13#10 +
    '  - mode dial on M (manual)' + #13#10 +
    '  - Canon menu: Auto power off = Disable' + #13#10 +
    '  - Custom functions: Release shutter w/o lens = Enable (for a telescope adapter)' + #13#10 +
    '  - CHDK loaded; CHDK menu: USB Remote disabled, no autostart script' + #13#10 + #13#10 +
    'For the next step it helps to switch the camera on and connect it by USB now, but it is not required.');

  AccessPage := CreateInputOptionPage(PreparePage.ID,
    'USB access to the camera', 'How should canon-m3-ascom talk to the camera?',
    'Both work with N.I.N.A. the same way. You can change it later by running this setup again.',
    True, False);
  AccessPage.Add('Keep the standard Windows driver (recommended)' + #13#10 +
                 '     Nothing changes in Windows; the camera stays visible in Explorer. About 1.5 s transfer per frame.');
  AccessPage.Add('Install the WinUSB driver' + #13#10 +
                 '     Slightly faster (about 1.2 s per frame); Explorer no longer sees the camera. Undone on uninstall.');
  AccessPage.Values[StrToInt(GetPreviousData('UsbAccess', '0'))] := True;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = AccessPage.ID then
  begin
    CurrentDriver := DetectCameraDriver();
    if CurrentDriver = '' then
      AccessPage.SubCaptionLabel.Caption := 'Camera not connected right now - that is fine, the choice is applied anyway.'
    else if CompareText(CurrentDriver, 'WinUSB') = 0 then
      AccessPage.SubCaptionLabel.Caption := 'Camera found, it currently uses the WinUSB driver.'
    else
      AccessPage.SubCaptionLabel.Caption := 'Camera found, it currently uses the standard Windows driver (' + CurrentDriver + ').';
  end;
end;

procedure RegisterPreviousData(PreviousDataKey: Integer);
begin
  if AccessPage.Values[1] then
    SetPreviousData(PreviousDataKey, 'UsbAccess', '1')
  else
    SetPreviousData(PreviousDataKey, 'UsbAccess', '0');
end;

{ Create the configuration on first install; always set the chosen transport. }
procedure WriteConfig(Transport: String);
var
  ConfigFile, Text: String;
  Lines: TArrayOfString;
  I: Integer;
begin
  ConfigFile := ExpandConstant('{commonappdata}\canon-m3-ascom\config.toml');
  { keep the configuration of an installation under the former name m3alpaca }
  if not FileExists(ConfigFile) and FileExists(ExpandConstant('{commonappdata}\m3alpaca\config.toml')) then
    FileCopy(ExpandConstant('{commonappdata}\m3alpaca\config.toml'), ConfigFile, False);
  if not FileExists(ConfigFile) then
    FileCopy(ExpandConstant('{app}\config.example.toml'), ConfigFile, False);
  if LoadStringsFromFile(ConfigFile, Lines) then
  begin
    Text := '';
    for I := 0 to GetArrayLength(Lines) - 1 do
    begin
      if Pos('transport =', Trim(Lines[I])) = 1 then
        Lines[I] := 'transport = "' + Transport + '"        # set by the installer: wpd, winusb or auto';
      Text := Text + Lines[I] + #13#10;
    end;
    SaveStringToFile(ConfigFile, Text, False);
  end;
end;

procedure RunDriverStep();
var
  Code: Integer;
begin
  if AccessPage.Values[1] then
  begin
    { WinUSB is assigned only to a connected camera: the standard driver (signed by Microsoft) outranks
      a pre-installed libwdi driver when the camera is plugged in later. }
    if DetectCameraDriver() = '' then
      MsgBox('The camera is not connected, so the WinUSB driver cannot be assigned to it now.' + #13#10 + #13#10 +
             'Until then canon-m3-ascom uses the standard Windows driver. To switch later, connect the camera ' +
             '(on, CHDK loaded) and run "Install WinUSB driver for the camera" from the Start menu.',
             mbInformation, MB_OK)
    else
    begin
      WizardForm.StatusLabel.Caption := 'Installing the WinUSB driver for the camera...';
      if not Exec(ExpandConstant('{app}\tools\wdi-simple.exe'),
                  '--vid 0x04A9 --pid 0x3299 --type 0 --name "Canon EOS M3 (CHDK)" --dest "' +
                  ExpandConstant('{tmp}\winusb-driver') + '" --progressbar=' + IntToStr(WizardForm.Handle),
                  '', SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
        MsgBox('The WinUSB driver could not be installed (code ' + IntToStr(Code) + ').' + #13#10 +
               'canon-m3-ascom will use the standard Windows driver. You can try again from the Start menu.',
               mbError, MB_OK);
    end;
    { auto: WinUSB when the camera has it, otherwise the standard driver - works in every case }
    WriteConfig('auto');
    Exit;
  end
  else if CompareText(CurrentDriver, 'WinUSB') = 0 then
  begin
    WizardForm.StatusLabel.Caption := 'Restoring the standard Windows driver for the camera...';
    Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
         '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\tools\restore-camera-driver.ps1') + '" -NoPause',
         '', SW_HIDE, ewWaitUntilTerminated, Code);
  end;
  WriteConfig('wpd');
end;

{ A running driver locks its files; stop it before upgrading or uninstalling. }
procedure StopRunningDriver();
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM canon-m3-ascom.exe /F', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM m3alpaca.exe /F', '', SW_HIDE, ewWaitUntilTerminated, Code);  { former name }
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopRunningDriver();
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    RunDriverStep();
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
begin
  if CurUninstallStep = usUninstall then
    StopRunningDriver();
  if (CurUninstallStep = usUninstall) and
     (MsgBox('Restore the standard Windows driver for the camera (removes the WinUSB driver if this setup installed it)?',
             mbConfirmation, MB_YESNO) = IDYES) then
    Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
         '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\tools\restore-camera-driver.ps1') + '" -NoPause',
         '', SW_HIDE, ewWaitUntilTerminated, Code);
end;
