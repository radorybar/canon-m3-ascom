# Installing on the telescope PC

The driver runs on the same PC as N.I.N.A. (for example a mini PC at the telescope, operated over
Remote Desktop). The camera is connected to that PC by USB.

## 1. Transfer

Download the source zip of the latest release from
[Releases](https://github.com/radorybar/canon-m3-ascom/releases), clone the repository on the telescope
PC, or build a zip on a development PC (`git archive --format=zip -o dist\m3alpaca.zip HEAD`).

Over Remote Desktop: copy the zip with **Ctrl+C** on the local PC and **Ctrl+V** in the remote session
(or enable drive redirection: Remote Desktop → Show Options → Local Resources → More… → Drives), then
extract it, e.g. to `C:\astro\m3alpaca`.

## 2. Install

In PowerShell on the telescope PC:

```
cd C:\astro\m3alpaca
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

The script installs Python 3.12 if needed (winget, user scope), creates `.venv`, installs the package and
writes a `config.toml` with the `ptp` backend and the LCD switched off. It can also add a shortcut that
starts the driver at logon. Running it again updates the installation and keeps `config.toml`.

## 3. USB driver (once per PC)

The WinUSB driver is installed per PC, so repeat it on the telescope PC:

1. Get Zadig from https://zadig.akeo.ie.
2. Switch the camera on with CHDK loaded and connect it.
3. Zadig → Options → **List All Devices** → **Canon EOS M3** (USB ID `04A9 3299`, driver `WUDFWpdMtp`) →
   **WinUSB** → **Replace Driver**.
4. Power-cycle the camera.

## 4. Test

```
.venv\Scripts\python.exe -m m3alpaca.ptpdiag
.venv\Scripts\python.exe -m m3alpaca.ptpdiag --shoot 1
```

## 5. N.I.N.A.

1. Run `scripts\start-driver.cmd` (or let the logon shortcut start it).
2. N.I.N.A. → Equipment → Camera → **Canon EOS M3 (CHDK)** → Connect.
   If it is not listed: Options → Equipment → ASCOM Alpaca → Alpaca Direct `127.0.0.1:11112`.
3. Options → Camera → **Bit depth 14**.

## Remote Desktop and overnight imaging

- End the Remote Desktop session by **closing the window (disconnect)**, not by signing out: signing out
  stops the driver and N.I.N.A.
- If the camera drops off the USB bus while unattended: Control Panel → Power Options → change plan
  settings → advanced → USB settings → **USB selective suspend: Disabled**.

## Updating

Extract the new version over the old folder (`config.toml` is kept) and run `scripts\install.ps1` again.
