# Phase 0 findings

Legend: ✅ confirmed (source code, documentation or measurement) · 🟡 likely · ❓ open

## A. Tested environment

Windows 11, N.I.N.A. 3.2.0.9001, ASCOM Platform 7.1 Update 3, ASTAP, Python 3.12.

## B. CHDK on the EOS M3

- ✅ CHDK ports exist for firmware **1.01A, 1.20F (beta), 1.21A (alpha)**; tested with **1.21A** and
  **CHDK 1.6.1-6315**.
- ✅ `platform_camera.h`: native raw, CHDK PTP, 14-bit raw, FileWriteTask hook (remote capture without
  writing to the SD card), extended Tv range.
- ✅ Remote capture formats reported by the camera: **0xF** (jpg, raw, DNG header, Canon raw).
- ✅ Long Tv overrides are honoured (see E).

### Sensor

| | Value |
|---|---|
| Raw frame | 6112 × 4060 |
| Active area | (84,46)–(6084,4046) = **6000 × 4000** |
| Bit depth | 14 (MaxADU 16383) |
| CFA | **RGGB** ✅ (natural colours from a lit test frame) |
| Black level | **2048** measured from the masked border (CHDK port value: 2047) ✅ |
| Pixel size | ≈ 3.72 µm |
| Raw buffer | 43 425 760 bytes per frame ✅ |

## C. chdkptp

- ✅ Latest release chdkptp 1.0 rc1 (r1528, 03/2025). Windows builds are distributed in the Assembla
  files area, which requires an account, so this project uses its own CHDK PTP client instead
  (`ptp` backend). The `chdk` backend for an external chdkptp stays as a fallback.
- ✅ `rs` (remoteshoot) sequence, reproduced by the `ptp` backend: `init_usb_capture`, Tv/Sv overrides
  in the same script as `shoot()`, poll `RemoteCaptureIsReady`, `RemoteCaptureGetData`,
  `init_usb_capture(0)`.

### Lua API used

- `get_temperature(1)` – sensor temperature in °C
- `switch_mode_usb(1)` – switch to record mode while PTP is connected
- `set_tv96_direct`, `set_sv96`, `sv96_market_to_real` – exposure overrides
- `set_raw_nr(1)` – no automatic in-camera dark frame
- `set_lcd_display(0)` – LCD off
- `cnf_core.alt_prevent_shutdown` via `set_config_value` – prevent auto shutdown

## D. N.I.N.A. / Alpaca

- ✅ N.I.N.A. has no folder-watching camera; the Camera Simulator "Directory" source only replays
  existing files. A dedicated Alpaca driver is required.
- ✅ N.I.N.A. 3.x discovers Alpaca devices (UDP 32227); 3.2 also has "Alpaca Direct" (fixed address).
- ✅ The ASCOM client uses ImageBytes when offered; elements are reported as Int32.
- ✅ `SensorType` other than Monochrome → N.I.N.A. debayers. Bayer offsets are applied to previews only
  from 3.3 nightlies; with RGGB and offset 0 this does not matter.
- ✅ **N.I.N.A. takes the bit depth from the profile** (Camera settings → Bit depth), not from MaxADU.
- ✅ Gain: N.I.N.A. tries GainMin/Max, otherwise uses `Gains` as a list and `Gain` as an index.
- ✅ `LastExposureStartTime/Duration` go into the FITS header (3.2+).
- 🟡 The EOS M3 is not supported by Canon's EDSDK.

### Tools and references

- AlpycaDevice (ASCOM Python template): https://github.com/ASCOMInitiative/AlpycaDevice
- ImageBytes specification: https://github.com/ASCOMInitiative/ASCOMRemote/blob/main/Documentation/AlpacaImageBytes.pdf
- ConformU (Alpaca conformance checker): https://github.com/ASCOMInitiative/ConformU
- Similar approach for another brand: https://github.com/totoantibes/LumixCameraAscomDriver

## E. Measurements on the real camera

- **Driver:** WinUSB (Zadig) on the camera only (`04A9:3299`).
- **`ptpdiag`:** USB ✅, PTP session ✅ (DeviceVersion `3-14.0.1.0`), CHDK PTP **2.9** ✅, Lua ✅.
- **Dark 5 s frame at ISO 800:**
  - raw size matches 6112×4060×14 bit;
  - masked border median 2048, σ 11.7 ADU;
  - neighbouring same-colour pixels differ by ~11 ADU (read noise level), typical Canon horizontal banding,
    i.e. the decoding is correct.
- **Lit 1/20 s frame at ISO 200:** natural colours with RGGB, image not mirrored. Transfer 43 MB in
  1.1 s (38 MB/s); exposure + camera processing 1.7 s.
- **Long exposures at ISO 100:**

  | Requested | Exposure + processing | Transfer |
  |---|---|---|
  | 30 s | 31.8 s | 1.1 s |
  | 60 s | 61.5 s | 1.3 s |
  | 120 s | 121.7 s | 1.2 s |

  Brightness grows with time; no automatic dark frame.
- **Temperature:** the sensor warmed from 29 °C to 39 °C with the LCD and live view on → use
  `lcd_off = true` for imaging.
- **N.I.N.A. 3.2:** connected through discovery, 0.1 s exposure displayed; ~3 s overhead per frame.

## F. Open

- Stability over a long sequence (hundreds of frames), USB selective suspend, behaviour after a USB reset.
- Exposures beyond 120 s (CHDK allows up to 2048 s).
- Autofocus and plate solving on the sky.
