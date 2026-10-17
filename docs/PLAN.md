# Design: Canon EOS M3 as an N.I.N.A. camera through CHDK PTP

## 1. Goal

N.I.N.A. sees the Canon EOS M3 as a regular **ASCOM Alpaca camera** over one USB cable, with:

- exposures and sequences,
- autofocus with an electronic focuser (N.I.N.A. measures HFR on the raw frame),
- plate solving (ASTAP),
- sensor temperature in the FITS header.

**Out of scope:** live view in N.I.N.A., cooling. Guiding and dithering (PHD2) are a later phase.

## 2. Options considered

| Option | Why not chosen |
|---|---|
| Canon EDSDK (N.I.N.A.'s Canon driver) | the EOS M3 has no tethering support in EDSDK |
| CHDK "USB remote" (5 V pulse on the USB port as a shutter release) | triggers the shutter, but images stay on the SD card, so no autofocus or plate solving in N.I.N.A.; cannot be used together with PTP on the same port |
| HDMI capture of the live view | live view only, no raw frames |
| N.I.N.A. "folder watcher" camera | does not exist: the Camera Simulator's "Directory" source only replays existing files |
| **CHDK PTP remote capture + own Alpaca driver** | chosen |

## 3. Architecture

```
┌──────────────── telescope PC (Windows) ──────────────────┐
│                                                          │
│  N.I.N.A. ──HTTP/Alpaca──► m3alpaca (localhost)          │
│     │                         │                          │
│     │ focuser, ASTAP          ├─ Alpaca API (ICameraV3)  │
│     │ (unchanged)             ├─ Camera (exposure state  │
│     │                         │   machine, ROI, ISO)     │
│     │                         ├─ Backend: ptp / sim /    │
│     │                         │   chdk                   │
│     │                         ├─ raw decoder (14-bit)    │
│     │                         └─ discovery (UDP 32227)   │
│                                   │                      │
└───────────────────────────────────┼──────────────────────┘
                                    │ USB (PTP + CHDK extension)
                          Canon EOS M3 + CHDK
```

Why Alpaca and not a COM driver: N.I.N.A. supports Alpaca natively including discovery, nothing to
register, any language, testable over plain HTTP (curl, ConformU).

## 4. Components

**a) PTP backend** (`backends/ptp.py`, `ptp/`)

- Own CHDK PTP client in Python (pyusb + libusb-1.0, WinUSB driver). chdkptp's Windows builds are only
  distributed through Assembla, so the driver does not depend on it.
- One persistent USB session. Per frame, the same sequence as chdkptp's `rs -raw`:
  1. Lua: `init_usb_capture(raw)`;
  2. Lua: `set_tv96_direct`, `set_sv96(sv96_market_to_real(...))`, `shoot()` (not waited for);
  3. poll `RemoteCaptureIsReady` until the raw buffer is available;
  4. `RemoteCaptureGetData`, then collect the script result (sensor temperature);
  5. Lua: `init_usb_capture(0)`.
- On the M3, `shoot()` returns before the raw data is handed over, so only the capture status decides.
- On any USB/PTP error the session is dropped and the next frame reconnects.
- `ptp/fake.py` emulates the camera at the USB byte-stream level for tests.

**b) Camera** (`camera.py`) – exposure state machine following Alpaca `CameraState`:
`Idle → Exposing → Download → Idle`, `Error` on failure. `StartExposure` returns immediately,
`ImageReady` becomes true after decoding.

**c) Raw decoder** (`rawcodec.py`)

- The CHDK raw buffer: 6112×4060, MSB-first 14-bit packed, bytes swapped within 16-bit words
  (verified against CHDK's `get_raw_pixel`).
- Cropped to the active area (84,46)–(6084,4046) = 6000×4000, uint16; the black level is not subtracted
  (that is what calibration frames are for).
- `SensorType = RGGB`, `BayerOffsetX/Y = 0`; sent as Int32 `[x, y]` (transmitted as UInt16 ImageBytes).

**d) Alpaca API** (ICameraV3)

| Alpaca | Source |
|---|---|
| `CameraXSize/YSize`, `PixelSizeX/Y` (≈3.72 µm) | EOS M3 constants |
| `ExposureMin/Max` | 1/4000 s … 300 s (configurable; verified to 120 s) |
| `Gains` / `Gain` | ISO list (100 … 12800) / index |
| `CCDTemperature` | `get_temperature(1)` after each frame |
| `CanAbortExposure` | false for the real camera |
| `BinX/Y` | 1 |
| `ImageArray` | ImageBytes (JSON fallback is streamed) |
| `MaxADU` | 16383 |

**e) Discovery** – answers on UDP 32227, bound to the same address as the HTTP server.

## 5. One exposure

1. N.I.N.A.: `PUT startexposure(Duration=60, Light=true)` with `Gain=3` (ISO 800).
2. Camera → `Exposing`; backend runs the capture sequence above.
3. The raw buffer (43 MB) arrives over USB in ~1.2 s; Camera → `Download`, decode, `ImageReady=true`.
4. N.I.N.A. downloads `imagearray` and saves the FITS.

Overhead per frame on the real camera: about 3 s (1.7 s camera processing + 1.2 s transfer).

## 6. Phase 0 checks

Research and prerequisites without the camera, then on the camera: driver and connection, raw remote
capture on the M3 (DIGIC 6), long Tv overrides, transfer speed, stability over many frames, raw layout
(CFA, active area, black level). Results: [phase0-results.md](phase0-results.md).

## 7. Implementation phases

1. MVP: Alpaca server, exposure, ImageBytes. ✅
2. Real camera over CHDK PTP, ISO as gain, temperature, discovery, reconnect. ✅
3. Reliability during a whole night: long sequences, USB power saving, logging.
4. Extras: ConformU validation, 2×2 binning for faster autofocus, DNG output.

## 8. Notes

- Hardware: a dummy battery (LP-E17 coupler) for whole nights, T2 → EF-M adapter; a coma corrector is
  recommended for APS-C on a fast Newtonian.
- The driver runs on the same PC as N.I.N.A.
