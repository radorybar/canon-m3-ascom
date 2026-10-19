# Next steps

## Done

- Alpaca camera: discovery, ICameraV3, ImageBytes, status page; gain = ISO (100–12800).
- Simulator verified with the ASCOM OmniSimulator focuser and ASTAP: the HFD V-curve has its minimum
  at the simulated best focus; N.I.N.A. autofocus with the simulator (result 25599, target 25600).
- Own CHDK PTP client (`ptp` backend) over WinUSB **and** over the standard Windows driver (WPD MTP
  extensions), USB-level camera emulator, `diag` and `detect` commands.
- Real camera: raw capture over USB, RGGB, black level 2048, ISO ×2 per stop, 30/60/120 s exposures,
  exposures and FITS files from N.I.N.A. 3.2; ~4.8 s per 1 s frame end to end.
- Setup wizard (Inno Setup) with a choice between the standard driver and WinUSB (libwdi), tray icon,
  rotating log file, standalone package without Python.

## Next

1. **Imaging PC:** run the setup wizard with the camera connected and on ([INSTALL.md](INSTALL.md)),
   option B (WinUSB) for unattended nights; then *Camera diagnostics*.
2. **First night:** focus with an electronic focuser and N.I.N.A. autofocus (4–5 s exposures, a step size
   that changes HFR clearly), plate solve with ASTAP, a short unguided sequence (15–30 s subs).
3. **Long run:** a sequence of a few hundred frames under the sky (the [stability review](STABILITY-PLAN.md)
   items are done and verified indoors).

## Later

- Guiding and dithering with PHD2 (the driver does not depend on it).
- ConformU validation.
- 2×2 binning for faster autofocus.
- DNG output (remote capture of the DNG header + raw).
- Exposures beyond 120 s.
- Code signing for the setup (SmartScreen).
