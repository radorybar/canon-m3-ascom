# Next steps

## Done

- Alpaca camera: discovery, ICameraV3, ImageBytes (a 48 MB frame over localhost in ~0.35 s), status page.
- Simulator verified with the ASCOM OmniSimulator focuser and ASTAP: the HFD V-curve has its minimum
  at the simulated best focus.
- N.I.N.A. 3.2 with the simulator: discovery, exposures with HFR, autofocus (start 25000, result 25599,
  target 25600, R² ≈ 0.98).
- Own CHDK PTP client (`ptp` backend), USB-level camera emulator, `ptpdiag` diagnostics.
- Real camera: raw capture over USB, RGGB, black level, 30/60/120 s exposures, first N.I.N.A. exposures.

## Next

1. **Telescope PC:** install with `scripts\install.ps1` ([INSTALL-MINIX.md](INSTALL-MINIX.md)),
   WinUSB via Zadig on that PC, `ptpdiag --shoot 1`.
2. **First night:** focus with the electronic focuser and N.I.N.A. autofocus (start with a step size
   that changes HFR clearly), plate solve with ASTAP, a short unguided sequence (subs of 15–30 s at
   1000 mm without guiding), `lcd_off = true`.
3. **Reliability:** a sequence of a few hundred frames; disable USB selective suspend if the camera
   drops off; check reconnect after unplugging.

## Later

- Guiding and dithering with PHD2 (the driver does not depend on it).
- ConformU validation.
- 2×2 binning for faster autofocus.
- DNG output (remote capture of the DNG header + raw).
- Exposures beyond 120 s.
