# m3alpaca

An ASCOM Alpaca camera driver that makes a **Canon EOS M3 running CHDK** usable in
[N.I.N.A.](https://nighttime-imaging.eu/) like a regular astro camera: exposures, sequences,
autofocus (HFR), plate solving, sensor temperature — over a single USB cable.

The EOS M3 has no tethering support in Canon's EDSDK and no remote-release socket. CHDK adds a PTP
extension that can run Lua on the camera and stream the raw sensor buffer over USB. m3alpaca includes
its own CHDK PTP client (Python, pyusb + WinUSB), decodes the 14-bit raw on the PC and serves it to
N.I.N.A. as an RGGB Bayer image through the Alpaca protocol.

```
N.I.N.A. ──Alpaca/HTTP──► m3alpaca ──USB (PTP + CHDK)──► Canon EOS M3 + CHDK
                          (Python)
```

## Status

| Part | Status |
|---|---|
| Alpaca server (ICameraV3, ImageBytes, discovery, status page) | done, tested |
| `ptp` backend: built-in CHDK PTP client, raw remote capture | **works with the real camera** |
| Verified on the camera | 6112×4060 14-bit raw, RGGB, black level 2048, exposures up to 120 s, ~1.2 s transfer |
| Verified in N.I.N.A. 3.2 | connect, single exposures; autofocus with the simulator |
| `sim` backend: synthetic star field coupled to an Alpaca focuser | done (autofocus V-curve in N.I.N.A.) |
| `chdk` backend: external `chdkptp.exe` | fallback, only tested against an emulator |
| Not yet tested | a night on the sky (real autofocus, plate solving), long sequences |

Details: [design](docs/PLAN.md) · [findings](docs/phase0-results.md) · [next steps](docs/NEXT-STEPS.md) ·
[installing on the telescope PC](docs/INSTALL-MINIX.md)

## Requirements

- Windows 10/11, Python 3.12, N.I.N.A. 3.x
- Canon EOS M3 with [CHDK](https://chdk.fandom.com/wiki/CHDK) 1.6 (tested: firmware 1.21A, CHDK 1.6.1-6315)
- The **WinUSB** driver for the camera, installed with [Zadig](https://zadig.akeo.ie/) (once per PC)

### Camera settings

- Mode dial on **M**; Canon menu: **Auto power off: Disable**; custom function
  **Release shutter w/o lens: Enable** (needed on a telescope adapter)
- CHDK menu: **USB Remote disabled** (otherwise CHDK treats the USB cable as a shutter release),
  no autostart script

### USB driver (Zadig)

With the camera on (CHDK loaded) and connected: Zadig → Options → **List All Devices** → select the
camera (**USB ID `04A9 3299`**, current driver `WUDFWpdMtp`) → **WinUSB** → **Replace Driver**, then
power-cycle the camera. Windows Explorer no longer sees the camera afterwards; to undo, uninstall the
device in Device Manager with "delete the driver" and reconnect.

## Install

```bash
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
copy config.example.toml config.toml
```

For a telescope PC there is `scripts\install.ps1`, see [docs/INSTALL-MINIX.md](docs/INSTALL-MINIX.md).

## First contact with the camera

```bash
.venv\Scripts\python.exe -m m3alpaca.ptpdiag
.venv\Scripts\python.exe -m m3alpaca.ptpdiag --shoot 5
```

The first command only reads (USB → PTP → CHDK → Lua → remote-capture support). The second takes one
raw frame over USB and reports the black level from the masked border, channel medians and timings.

## Run

```bash
.venv\Scripts\m3alpaca.exe --config config.toml
```

The status page is at http://127.0.0.1:11112/setup.

Backends (`[camera] backend`):

- `ptp` – the real camera through the built-in CHDK PTP client (`src/m3alpaca/ptp/`). It performs the
  same steps as chdkptp's `rs -raw`: a Lua script sets Tv/ISO overrides and shoots, the raw buffer
  comes over USB. `--fake-camera` emulates the camera at the USB/PTP level.
- `sim` – simulated camera. With `[sim] focuser_url` star sharpness follows an Alpaca focuser (e.g. the
  ASCOM OmniSimulator on port 32323), so N.I.N.A. autofocus can be tried indoors.
- `chdk` – the real camera through an external `chdkptp.exe` (chdkptp Windows builds are distributed on
  Assembla). Emulator: `command = [".venv/Scripts/python.exe", "-m", "m3alpaca.fakechdk"]`.

## N.I.N.A.

1. Equipment → Camera → **Canon EOS M3 (CHDK)** (found by Alpaca discovery).
   If missing: Options → Equipment → ASCOM Alpaca → add Alpaca Direct `127.0.0.1:11112`.
2. Options → Camera: **Bit depth = 14** (N.I.N.A. takes it from the profile, not from the driver).
3. Gain is an index into the ISO list (100 … 12800).
4. On first start Windows Firewall asks about Python; allow at least private networks for discovery.

Keep `[ptp] lcd_off = true` for imaging: with the LCD/live view on, the sensor warmed from 29 °C to
39 °C within minutes.

## Tests

```bash
.venv\Scripts\python.exe -m pytest
```

The API tests run against all three backends: `ptp` against a camera emulator that speaks the USB
byte stream, `chdk` against a fake chdkptp process, and the simulator.

## Project layout

```
src/m3alpaca/
  alpaca/        Alpaca HTTP API, ImageBytes, discovery
  ptp/           PTP over USB, CHDK extension, camera-side Lua, USB-level camera emulator
  backends/      ptp, sim, chdk
  camera.py      ICameraV3 behaviour (exposure state machine, ROI, ISO as gain)
  rawcodec.py    CHDK 14-bit raw packing
  starfield.py   synthetic star field for the simulator
  ptpdiag.py     first-contact diagnostics
scripts/         install.ps1, start-driver.cmd
```

## Acknowledgements

[CHDK](https://chdk.fandom.com/wiki/CHDK) and [chdkptp](https://app.assembla.com/spaces/chdkptp/wiki)
(the CHDK PTP protocol, remote capture and the camera-side Lua approach), the
[ASCOM Initiative](https://ascom-standards.org/) (Alpaca, ImageBytes) and N.I.N.A.

## License

Copyright © 2026 radorybar.

This program is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 2 of the License, or
(at your option) any later version. See [LICENSE](LICENSE).

GPL was chosen because the CHDK remote-capture approach follows chdkptp and CHDK (both GPL-2.0), and a
test transcribes CHDK's `get_raw_pixel`.

This project is not affiliated with Canon, CHDK, the ASCOM Initiative or N.I.N.A.
