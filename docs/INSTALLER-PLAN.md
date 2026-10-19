# Installer plan

Goal: one `canon-m3-ascom-setup.exe` wizard that installs the driver application **and** sets up USB access to
the camera, with a choice between two ways of talking to it:

- **A – Windows Portable Devices (no driver change).** The camera keeps its standard Windows driver
  (WpdMtp); CHDK commands go through the official WPD "MTP extensions" API. Explorer keeps working.
- **B – WinUSB driver.** The installer replaces the camera driver with WinUSB automatically (libwdi),
  no Zadig clicking. The proven path, used by the current `ptp` backend.

Status: **A works** (spike on the EOS M3, 2026-10-18, results below). Installer built
(`packaging/canon-m3-ascom.iss`, `build-installer.ps1`, `build-wdi-simple.ps1`); the packaged exe works over WPD.
Not yet tested: running the setup wizard end to end, option B through the wizard, uninstall.

## Spike A results (EOS M3, firmware 1.21A, CHDK 1.6.1-6315, Windows 11, standard driver WUDFWpdMtp)

- The camera advertises 68 Canon vendor operations (0x9001–0x9185); CHDK's **0x9999 is not listed**, but the
  WPD driver forwards it anyway: Version → protocol 2.9.
- Lua scripts work in both data directions (ExecuteScript = write, ReadScriptMsg = read).
- The shutter fires; raw remote capture delivers the full 43 425 760-byte buffer in **1.5–1.7 s (27–29 MB/s)**
  (WinUSB: 1.1–1.2 s). Black level 2048, same image data as over WinUSB.
- First attempt after switching the camera back to the standard driver: the shot fired but remote capture never
  offered data. After the DryOS logical event 4482 (CHDK wiki "unlock") it worked every time, so the setup
  script now sends it when the transport is WPD.
- Overhead per frame about 4.5 s vs ~3 s with WinUSB.

Decision: A is offered as the default ("no driver change"), B as the faster/most compatible alternative.

## 1. Common transport interface

The CHDK client only needs `transaction(op, params, data_out, timeout) -> Response` plus `open()`,
`close()` and `device_info()`. `PtpSession` (USB bulk) already provides this. A `WpdSession` provides
the same methods on top of WPD, so `ChdkClient` and the backend do not change.

Config: `[ptp] transport = "auto" | "wpd" | "winusb"`. `auto` uses WinUSB if the camera has that driver,
otherwise WPD.

## 2. Path A – WPD MTP extensions

API (`PortableDeviceApi`, COM; used from Python via `comtypes`):

| PTP step | WPD command |
|---|---|
| command without data | `WPD_COMMAND_MTP_EXT_EXECUTE_COMMAND_WITHOUT_DATA_PHASE` |
| command + data to device (ExecuteScript) | `…_EXECUTE_COMMAND_WITH_DATA_TO_WRITE`, `…_WRITE_DATA`, `…_END_DATA_TRANSFER` |
| command + data from device (ReadScriptMsg, RemoteCaptureGetData, GetDeviceInfo) | `…_EXECUTE_COMMAND_WITH_DATA_TO_READ`, `…_READ_DATA` (in chunks), `…_END_DATA_TRANSFER` |
| allowed vendor operations | `WPD_COMMAND_MTP_EXT_GET_SUPPORTED_VENDOR_OPCODES` |

Session handling belongs to the Windows driver (no OpenSession/CloseSession from us).

Risks, decided by the spike:

1. The WPD driver may allow only vendor operations the camera lists in DeviceInfo; CHDK's `0x9999`
   must be in that list.
2. Throughput of a 43 MB read through WPD (target: no worse than ~2× the 1.2 s of WinUSB).
3. Behaviour while Windows Explorer or the Photos app also uses the camera.

Spike (one evening, camera on its original driver):

1. List WPD devices, open the camera, print `GET_SUPPORTED_VENDOR_OPCODES`.
2. `0x9999` Version (no data phase) → expect protocol 2.x.
3. ExecuteScript `return get_temperature(1)` + ReadScriptMsg (both data directions).
4. Full raw capture, measure transfer time.

Go/no-go: all four pass → A becomes the default in the installer; otherwise the installer offers B only
and A stays experimental.

## 3. Path B – automatic WinUSB installation

- Build `wdi-simple.exe` from [libwdi](https://github.com/pbatard/libwdi) (LGPL-3.0) with Visual Studio 2022
  and the WDK 8.0 redistributable components (`wdfcoinstaller.msi`, 31 MB, Microsoft), as libwdi's own CI
  does. A GitHub Actions workflow in this repository will do the same so the binary is reproducible.
- The installer runs, elevated:
  `wdi-simple.exe --vid 0x04A9 --pid 0x3299 --type 0 --name "Canon EOS M3 (CHDK)"`.
  libwdi creates the INF, a self-signed catalog and installs it, exactly what Zadig does.
- The camera must be connected and on during this step; the wizard waits for it and can retry.
- Uninstall option: remove the WinUSB driver package again (`pnputil /delete-driver … /uninstall`) so
  Windows falls back to its standard driver.
- Licensing: wdi-simple is LGPL-3.0, shipped as a separate executable with its license and source link.

## 4. Inno Setup wizard

1. Welcome, license (GPL-2.0-or-later).
2. Install folder (default `%ProgramFiles%\canon-m3-ascom`).
3. **Camera preparation:** mode M, CHDK loaded, CHDK USB Remote off, Canon "Auto power off: Disable".
   Button *Check camera* (runs `canon-m3-ascom.exe detect`).
4. **USB access:** radio buttons
   - A – "Keep the standard Windows driver (recommended if supported)"
   - B – "Install the WinUSB driver (most compatible)"

   The page shows what `detect` found (current driver, whether CHDK answers through WPD).
5. Install files; for B install the driver; for A, if the camera currently has WinUSB, offer to restore
   the standard driver.
6. **Connection test:** `canon-m3-ascom.exe diag --shoot 0.1`, result shown in the wizard.
7. Finish: Start menu shortcuts (driver, diagnostics, config, log folder), optional start at logon,
   Windows Firewall rule for TCP 11112 / UDP 32227 (private networks), uninstaller.

Configuration lives in `%ProgramData%\canon-m3-ascom\config.toml` (kept on upgrade/uninstall unless the user
chooses otherwise); logs in `%ProgramData%\canon-m3-ascom\logs`.

## 5. New pieces of code

- `ptp/wpd.py` – `WpdSession` (comtypes).
- `canon-m3-ascom.exe detect` – machine-readable status for the wizard: camera present, current driver,
  CHDK reachable via WPD / WinUSB.
- `[ptp] transport` setting and selection logic.
- `packaging/canon-m3-ascom.iss` – Inno Setup script; `packaging/build-installer.ps1` builds the exe package,
  `wdi-simple.exe` and the setup.
- CI workflow (later): build the package, wdi-simple and the setup on tags and attach them to the release.

## 6. Order of work

1. `WpdSession` + transport selection + `detect` (no camera needed to write; tests with a fake WPD layer).
2. **Spike A on the camera** (requires restoring the original driver on the test PC).
3. Build `wdi-simple.exe` (needs the WDK 8.0 redistributable download).
4. Inno Setup script and build script (needs Inno Setup).
5. Full test on a clean PC: install with A, uninstall, install with B.

## 7. Downloads needed (each approved separately)

| What | Source | Size |
|---|---|---|
| Inno Setup 6.7.3 | github.com/jrsoftware/issrc releases (winget `JRSoftware.InnoSetup`) | ~7 MB |
| WDK 8.0 redistributable components (`wdfcoinstaller.msi`) | go.microsoft.com/fwlink/p/?LinkID=253170 | 31 MB |
| libwdi source | github.com/pbatard/libwdi (git clone) | small |
| comtypes | PyPI | small |

## 8. Code signing

An unsigned `setup.exe` triggers Windows SmartScreen ("Windows protected your PC") when downloaded.
Options: SignPath (free for open-source projects) or a paid certificate. Not needed for private use.
