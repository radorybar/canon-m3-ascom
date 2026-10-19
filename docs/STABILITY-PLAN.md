# Stability plan

Findings of a static review focused on running unattended for a whole night, and the planned fixes.
Status (2026-10-18): all items implemented and verified on the EOS M3 (see *Verification* at the end).

## Findings

| # | Finding | Severity | How found | Status |
|---|---|---|---|---|
| 1 | The discovery thread stops after a client disappears before its answer arrives (`ConnectionResetError` on Windows); the camera is no longer discoverable until the driver restarts | high | reproduced | done |
| 2 | A stale PTP container left in the USB pipe (e.g. after an aborted transfer) is accepted as the response to the next command; every later response is then off by one | high | reproduced with the USB-level emulator | done |
| 3 | Some failures bypass the reconnect logic: a capture timeout keeps the session; raw USB library errors are not mapped to PTP errors, `UsbTransport.close()` can raise, and unexpected exceptions reach N.I.N.A. as HTTP 500 | high | code review | done |
| 4 | `Connected=false` during an exposure blocks the HTTP request until the exposure ends (up to 300 s) | medium | code review | done |
| 5 | No keep-alive between frames: a disconnected or switched-off camera is only noticed at the next exposure, and the sensor temperature is not refreshed while idle | medium | code review | done |
| 6 | Two USB transactions every 50 ms during the whole exposure (~12 000 for 300 s) | low | code review | done |
| 7 | Logs go to the console only; nothing to analyse after an unattended night | medium | code review | done: rotating log file, `[server] log_dir` |
| – | Python decode/encode costs ~1.5 s and ~590 MB peak per frame | performance | measured; separate topic | done: ~150 ms / ~100 MB, ImageBytes ~20 ms |
| – | `keep_raw` file names have one-second resolution and can overwrite each other | low | code review | done |

## Phase A – failures that can ruin a night

### A1. Discovery survives vanished clients (`alpaca/discovery.py`)

- Log and continue on `ConnectionResetError` and other transient errors from `recvfrom`/`sendto`; leave
  the loop only when the socket is closed.
- On Windows, disable `SIO_UDP_CONNRESET` on the socket so the error is not raised at all.
- Answer only the exact discovery message, at most once per ~50 ms per sender.
- Test: a client sends and closes before the reply; a second client must still get an answer.

### A2. PTP stream cannot desynchronise (`ptp/transport.py`)

- Skip (and log) containers whose transaction id does not match the current transaction, with a limit.
- When opening the transport: clear halt on the endpoints, drain pending IN data with short reads, and
  send the PTP class request *Device Reset* (0x66). If the M3 rejects 0x66, keep the first two steps.
- Tests (emulator): a stale response in the pipe; the remainder of an aborted 43 MB transfer; a second
  reconnect after that must succeed.
- On the camera: unplug the cable during a transfer; the next frame must recover without restarting
  the camera.

### A3. Every failure leads to a clean reconnect (`backends/ptp.py`, `ptp/transport.py`, `alpaca/server.py`)

- `UsbTransport` maps every USB library error to `PtpError`; `close()` never raises.
- The backend drops the session on any capture failure, including timeouts, so the next frame starts clean.
- The server turns unexpected exceptions into an Alpaca `UnspecifiedError` with the message instead of
  HTTP 500.
- Tests: USB error while closing, capture timeout, unexpected exception in a handler.

### A4. Disconnecting during an exposure does not block (`camera.py`, `backends/ptp.py`)

- `Connected=false` returns immediately: the camera is marked disconnected and the USB session is closed
  once the running capture has finished; its image is discarded.
- Connecting again while that capture is still finishing waits for it.
- Test: a 30 s exposure in the emulator; disconnect must answer within 1 s.

## Phase B – robustness

### B5. Keep-alive between frames (`backends/ptp.py`)

- While idle, every 60 s (configurable) run a short temperature query.
- Benefits: current sensor temperature in N.I.N.A., early detection of a lost connection with automatic
  reconnect, the camera does not fall asleep.
- On failure only log and reconnect; never interrupts a running exposure (shares the capture lock).
- Test: emulator with a short interval; temperature changes and a simulated outage recovers.

### B6. Less USB traffic during exposures (`backends/ptp.py`)

- During the exposure poll slowly (e.g. once per second, still surfacing script errors); switch to
  50 ms polling about 1 s before the expected end.
- For a 300 s exposure: ~300 transactions instead of ~12 000.
- Test: count transactions in the emulator.

### B7. Log file (`__main__.py`, `config.py`)

- Rotating log `logs/canon-m3-ascom.log` next to the driver (5 × 5 MB), concise console output.
- Logged: per-frame timings, reconnects, temperatures, full tracebacks of failures.
- New setting `[server] log_dir`.

## Procedure

1. A feature branch from `main`.
2. A1–A4 with tests first, then B5–B7; one commit per item so each can be reverted on its own.
3. Verification on the camera: a sequence of ~50 frames; pulling the USB cable during a transfer;
   switching the camera off and on during a sequence; restarting N.I.N.A. while the driver runs.
4. Then squash-merge into `main`.

Estimate: A1–A4 one evening, B5–B7 another half evening.

## Verification on the camera (2026-10-18, EOS M3 over WinUSB)

| Test | Result |
|---|---|
| 20 frames of 2 s in a row | 20/20 ok, ~5.8 s per frame |
| `Connected=false` during a 10 s exposure | answered in 0.00 s; reconnect waited for the exposure, next frame ok |
| USB cable pulled during a sequence, plugged in again | errors while unplugged, reconnected ~2 s after plugging in, sequence continued |
| Camera switched off during a sequence, on again with CHDK | errors while off, reconnected (with unlock) after CHDK loaded, sequence continued |
| Keep-alive during outages | logged once, not every interval |

Found and fixed during these tests: the WPD fallback failed in worker threads (`CO_E_NOTINITIALIZED`);
COM is now initialised per thread in the multithreaded apartment.
