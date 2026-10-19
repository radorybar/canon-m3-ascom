# Installation

Canon M3 ASCOM runs on the Windows PC that runs N.I.N.A. (or another Alpaca client); the camera is
connected to that PC by USB. Any Windows 10/11 64-bit PC works — a laptop next to the telescope, a mini PC
on the mount operated over Remote Desktop, or a desktop.

## Recommended: setup wizard

1. Copy `canon-m3-ascom-<version>-setup.exe` from [Releases](https://github.com/radorybar/canon-m3-ascom/releases)
   to the PC.
2. Switch the camera on with CHDK loaded and connect it by USB **before** running the setup.
3. Run the setup and choose the USB access:
   - **Keep the standard Windows driver** — nothing changes in Windows, the camera stays visible in Explorer.
   - **Install the WinUSB driver** — exclusive access and faster transfers, best for unattended nights.
     The page shows "Camera found …" when the camera is detected; WinUSB is only assigned to a connected
     camera (otherwise use *Install WinUSB driver for the camera* from the Start menu later).
4. Tick *Test the camera connection* at the end; then start **Canon M3 ASCOM driver** from the Start menu and
   connect **Canon EOS M3 (CHDK)** in N.I.N.A.

No Python is needed. Running a newer setup upgrades the installation and keeps the configuration
(`%ProgramData%\canon-m3-ascom\config.toml`).

## Alternative: standalone package

Extract `canon-m3-ascom-<version>-windows.zip` anywhere, run `diagnose-camera.cmd`, then `start-driver.cmd`.
It uses the standard Windows driver unless the camera has WinUSB (`[ptp] transport = "auto"`); for WinUSB
without the setup, see *USB driver* below.

## Alternative: from source

Needs Python 3.12 (the script installs it with winget if missing).

```
git clone https://github.com/radorybar/canon-m3-ascom
cd canon-m3-ascom
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

The script creates `.venv`, installs the package and writes `config.toml` (real camera, LCD off); it can
add a shortcut that starts the driver at sign-in. Run it again to update; `config.toml` is kept.

Test and start:

```
.venv\Scripts\canon-m3-ascom.exe diag --shoot 1
scripts\start-driver.cmd
```

## USB driver (WinUSB) without the setup

The driver is installed per PC:

1. Get Zadig from https://zadig.akeo.ie.
2. Switch the camera on with CHDK loaded and connect it.
3. Zadig → Options → **List All Devices** → **Canon EOS M3** (USB ID `04A9 3299`, driver `WUDFWpdMtp`) →
   **WinUSB** → **Replace Driver**.
4. Power-cycle the camera.

To go back to the standard driver run `scripts\restore-camera-driver.ps1` (or *Restore standard camera
driver* from the Start menu of an installed version).

## N.I.N.A.

1. Equipment → Camera → **Canon EOS M3 (CHDK)** → Connect.
   If it is not listed: Options → Equipment → ASCOM Alpaca → Alpaca Direct `127.0.0.1:11112`.
2. Options → Camera → **Bit depth 14**.
3. Gain is the ISO value (100 … 12800).

## Unattended nights

- Power the camera with a DC coupler (dummy battery) instead of the battery.
- Disable Windows USB power saving if the camera drops off the bus: Control Panel → Power Options → change
  plan settings → advanced → USB settings → **USB selective suspend: Disabled**.
- Over Remote Desktop, end the session by **closing the window (disconnect)**, not by signing out: signing
  out stops the driver and N.I.N.A.
