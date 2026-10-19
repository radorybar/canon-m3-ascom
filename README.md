# Canon M3 ASCOM

Use a **Canon EOS M3 running CHDK** as an astro camera in [N.I.N.A.](https://nighttime-imaging.eu/) (or any
ASCOM Alpaca client): exposures and sequences, autofocus (HFR), plate solving, ISO as gain, sensor temperature
in the FITS header — over a single USB cable, without a remote release or tethering software.

```
N.I.N.A. ──Alpaca (HTTP)──► Canon M3 ASCOM ──USB (PTP + CHDK)──► Canon EOS M3 + CHDK
                            notification-area app
```

The EOS M3 has no tethering support in Canon's EDSDK and no remote-release socket. [CHDK](https://chdk.fandom.com/wiki/CHDK)
adds a PTP extension that runs Lua on the camera and hands the raw sensor buffer over USB. Canon M3 ASCOM
contains its own CHDK PTP client, decodes the 14-bit raw on the PC and serves it as an RGGB Bayer image
through the ASCOM Alpaca protocol.

## Features

- Raw 6000×4000 14-bit frames (RGGB), exposures from 1/4000 s to 300 s (verified to 120 s), ISO 100–12800
- Two ways to reach the camera, chosen in the setup wizard:
  - **standard Windows driver** (Windows Portable Devices) — nothing changes in Windows, the camera stays
    visible in Explorer; about 1.5 s USB transfer per frame
  - **WinUSB** — installed automatically by the wizard; exclusive access, about 1.1 s per frame;
    recommended for unattended nights
- Setup wizard, icon in the notification area, rotating log file, start at sign-in, Alpaca discovery
- Recovers on its own from a pulled USB cable or a camera switched off and on; keeps the link alive between
  frames and refreshes the sensor temperature
- About 4.8 s from start to image in the client for a 1 s exposure (camera processing and USB included)

## Requirements

- Windows 10/11 (64-bit), N.I.N.A. 3.x or another ASCOM Alpaca client
- Canon EOS M3 with CHDK 1.6 (tested: firmware 1.21A, CHDK 1.6.1-6315)

## Installation

1. **Prepare the camera**
   - mode dial on **M**
   - Canon menu: **Auto power off: Disable**; custom function **Release shutter w/o lens: Enable**
     (needed on a telescope adapter)
   - CHDK loaded; CHDK menu: **USB Remote disabled** (otherwise CHDK treats the USB cable as a shutter
     release), no autostart script
2. **Switch the camera on and connect it by USB** before starting the setup (needed for the WinUSB option).
3. **Run `canon-m3-ascom-<version>-setup.exe`** from [Releases](https://github.com/radorybar/canon-m3-ascom/releases)
   and choose the USB access:
   - *Keep the standard Windows driver* — simplest, nothing changes in Windows
   - *Install the WinUSB driver* — faster and exclusive; uninstalling offers to restore the standard driver
4. At the end, *Test the camera connection* runs a diagnostic and takes a 1 s frame.

The setup installs to `C:\Program Files\canon-m3-ascom`, keeps the configuration in
`%ProgramData%\canon-m3-ascom\config.toml` and the logs in `%ProgramData%\canon-m3-ascom\logs`. Running it
again upgrades the installation and keeps the configuration; it also replaces an installation under the
project's former name *m3alpaca*.

Start menu → **Canon M3 ASCOM**:

| Shortcut | |
|---|---|
| Canon M3 ASCOM driver | starts the driver in the notification area |
| Canon M3 ASCOM driver (console window) | the same with a console, for troubleshooting |
| Camera diagnostics | step-by-step check and a 1 s test frame (quit the driver first) |
| Install WinUSB driver for the camera / Restore standard camera driver | switch the USB access later |
| Edit configuration, Log files, Uninstall | |

Operated over Remote Desktop? End the session by closing the window (disconnect), not
by signing out — signing out stops the driver. More in [docs/INSTALL.md](docs/INSTALL.md).

## Using it in N.I.N.A.

1. Start **Canon M3 ASCOM driver** (or let it start at sign-in). The icon colour shows the state:
   grey = waiting for N.I.N.A., green = connected, blue = exposing, red = error.
2. Equipment → Camera → **Canon EOS M3 (CHDK)** → Connect (found by Alpaca discovery).
   If it is not listed: Options → Equipment → ASCOM Alpaca → add Alpaca Direct `127.0.0.1:11112`.
3. Options → Camera: **Bit depth = 14** (N.I.N.A. takes it from the profile, not from the driver).
4. **Gain is the ISO value** (100 … 12800). ISO 400–1600 suits the M3 for astrophotography.

Raw frames have no white balance: a green cast is normal (enable *Unlinked stretch* in N.I.N.A.'s image
options); magenta highlights mean the green channel is saturated. Keep `[ptp] lcd_off = true`: with the LCD
on, the sensor warms by about 10 °C. The mechanical shutter causes a short vibration at the start of each
exposure — use 4–5 s frames for autofocus.

## Configuration

`%ProgramData%\canon-m3-ascom\config.toml` (all options with comments: [config.example.toml](config.example.toml)).
The most useful ones:

| Setting | Default | |
|---|---|---|
| `[ptp] transport` | set by the setup | `wpd` (standard driver), `winusb`, or `auto` (WinUSB if installed, else standard) |
| `[ptp] lcd_off` | `true` | switch the camera LCD off after connecting |
| `[ptp] keepalive_s` | `60` | link check and temperature refresh while idle, `0` = off |
| `[camera] default_iso`, `exposure_max` | `800`, `300` | |
| `[server] port`, `host` | `11112`, `0.0.0.0` | `127.0.0.1` = local only (then use Alpaca Direct) |

## Troubleshooting

- **Camera diagnostics** (Start menu) checks USB, PTP, CHDK, Lua and remote capture step by step and says
  which layer fails.
- `canon-m3-ascom.exe detect` shows the camera, its current driver and which access path works.
- The log (`Log files` in the Start menu) has per-frame timings, reconnects and full error details.
- "No image from the camera" right after a driver change: reconnect once; the driver sends CHDK's unlock
  event on every connect.

## Status

Verified on a real EOS M3 and in N.I.N.A. 3.2: raw capture over both USB paths, ISO (exactly ×2 per stop),
30/60/120 s exposures, FITS headers (RGGB, temperature), the setup wizard with both options, and recovery from
a pulled cable, a power cycle and a disconnect during an exposure. **Not yet tested:** a whole night under the
sky (autofocus with a real focuser, plate solving, long sequences).

Details: [design](docs/PLAN.md) · [findings](docs/phase0-results.md) · [installer](docs/INSTALLER-PLAN.md) ·
[stability review](docs/STABILITY-PLAN.md) · [next steps](docs/NEXT-STEPS.md)

## Development

```bash
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest
.venv\Scripts\canon-m3-ascom.exe --backend sim          # simulated camera, no hardware needed
.venv\Scripts\canon-m3-ascom.exe diag --shoot 1         # real camera
```

Tests need no camera: the `ptp` backend runs against an emulator that speaks the USB byte stream, the WPD
path against a fake WPD device, and the `sim` backend renders a star field (optionally coupled to an Alpaca
focuser such as the ASCOM OmniSimulator, to try autofocus indoors).

Building: `packaging\build-exe.ps1` (standalone folder, PyInstaller) and `packaging\build-installer.ps1`
(setup wizard; needs Inno Setup 6 and the Visual Studio 2022 C++ tools for libwdi's `wdi-simple`).

```
src/canon_m3_ascom/
  alpaca/        Alpaca HTTP API, ImageBytes, discovery
  ptp/           PTP over USB, WPD transport, CHDK extension, camera-side Lua, camera emulator
  backends/      ptp (real camera), sim, chdk (external chdkptp.exe)
  camera.py      ICameraV3 behaviour (exposure state machine, ROI, ISO as gain)
  rawcodec.py    CHDK 14-bit raw decoding
  tray.py        notification-area app;  detect.py / ptpdiag.py  diagnostics
packaging/       PyInstaller and Inno Setup builds
scripts/         install from source, restore the standard camera driver
```

## Contributing

Issues and pull requests are welcome, see [CONTRIBUTING.md](CONTRIBUTING.md). Changes reach `main` only
through pull requests approved by the maintainer. Security issues: [SECURITY.md](SECURITY.md).

## Acknowledgements

[CHDK](https://chdk.fandom.com/wiki/CHDK) and [chdkptp](https://app.assembla.com/spaces/chdkptp/wiki)
(the CHDK PTP protocol, remote capture and the camera-side Lua approach), [libwdi](https://github.com/pbatard/libwdi)
(WinUSB installation), the [ASCOM Initiative](https://ascom-standards.org/) (Alpaca, ImageBytes) and N.I.N.A.

## License

Copyright © 2026 radorybar.

This program is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 2 of the License, or
(at your option) any later version. See [LICENSE](LICENSE). The bundled `wdi-simple.exe` is LGPL-3.0
(libwdi).

GPL was chosen because the CHDK remote-capture approach follows chdkptp and CHDK (both GPL-2.0), and a
test transcribes CHDK's `get_raw_pixel`.

This project is not affiliated with Canon, CHDK, the ASCOM Initiative or N.I.N.A.
