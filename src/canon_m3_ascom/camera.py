"""ASCOM ICameraV3 behaviour on top of a Backend (exposure state machine, ROI, gain-as-ISO)."""

import logging
import threading
import time
from datetime import datetime, timezone
from enum import IntEnum

import numpy as np

from .alpaca.errors import (INVALID_OPERATION, INVALID_VALUE, NOT_CONNECTED, UNSPECIFIED, VALUE_NOT_SET,
                            AlpacaError, not_implemented)
from .backends import Backend, BackendError
from .config import CameraConfig
from .sensor import ISO_VALUES
from .starfield import e_per_adu

log = logging.getLogger(__name__)


class CameraState(IntEnum):
    IDLE = 0
    WAITING = 1
    EXPOSING = 2
    READING = 3
    DOWNLOAD = 4
    ERROR = 5


# ASCOM SensorType enum
_SENSOR_TYPES = {"RGGB": 2}


class Camera:
    def __init__(self, backend: Backend, cfg: CameraConfig):
        self.backend = backend
        self.spec = backend.spec
        self.cfg = cfg
        self._lock = threading.Lock()
        self._connected = False
        self._disconnecting: threading.Thread | None = None
        self._exposure_thread: threading.Thread | None = None
        self.startx = 0
        self.starty = 0
        self.numx = self.spec.width
        self.numy = self.spec.height
        self._iso = min(max(cfg.default_iso, ISO_VALUES[0]), ISO_VALUES[-1])
        self._busy = False
        self._exp_t0 = 0.0
        self._exp_duration = 0.0
        self._cancel = threading.Event()
        self._image: np.ndarray | None = None
        self._error: str | None = None
        self._last_start: str | None = None
        self._last_duration: float | None = None
        # timing of the client's loop, logged to see where the time between frames goes
        self._image_t = 0.0
        self._sent_t: float | None = None

    # -- connection ------------------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connected

    def set_connected(self, value: bool) -> None:
        if value == self._connected:
            return
        if value:
            self._finish_disconnect()
            try:
                self.backend.connect()
            except BackendError as e:
                raise AlpacaError(UNSPECIFIED, f"cannot connect to camera: {e}") from e
            self._connected = True
        else:
            # Answer at once: a running exposure cannot be interrupted on the camera, so the backend is
            # closed in the background when it has finished (its image is discarded).
            self._connected = False
            self._cancel.set()
            self._disconnecting = threading.Thread(target=self.backend.disconnect, name="disconnect", daemon=True)
            self._disconnecting.start()

    def _finish_disconnect(self) -> None:
        limit = self.cfg.exposure_max * 2 + 120
        for t in (self._disconnecting, self._exposure_thread):
            if t is not None and t.is_alive():
                log.info("waiting for the previous exposure to finish before reconnecting")
                t.join(limit)
        self._disconnecting = None

    def require_connected(self) -> None:
        if not self._connected:
            raise AlpacaError(NOT_CONNECTED, "camera is not connected")

    # -- gain (ISO) ------------------------------------------------------------------------------

    # Gain is the ISO value itself (Alpaca "GainMin/GainMax" mode), so clients show 100 ... 12800 instead of
    # list indices. CHDK sets any ISO in between, not only the full stops.

    @property
    def gain_min(self) -> int:
        return ISO_VALUES[0]

    @property
    def gain_max(self) -> int:
        return ISO_VALUES[-1]

    @property
    def gain(self) -> int:
        return self._iso

    def set_gain(self, iso: int) -> None:
        if not self.gain_min <= iso <= self.gain_max:
            raise AlpacaError(INVALID_VALUE, f"gain (ISO) {iso} outside {self.gain_min}..{self.gain_max}")
        self._iso = iso

    @property
    def iso(self) -> int:
        return self._iso

    @property
    def electrons_per_adu(self) -> float:
        return e_per_adu(self.iso)

    @property
    def full_well(self) -> float:
        return min(self.electrons_per_adu * (self.spec.max_adu - self.spec.black_level), 26000.0)

    # -- sensor ----------------------------------------------------------------------------------

    @property
    def sensor_type(self) -> int:
        return _SENSOR_TYPES[self.spec.cfa]

    @property
    def ccd_temperature(self) -> float:
        t = self.backend.sensor_temperature()
        if t is None:
            raise AlpacaError(VALUE_NOT_SET, "sensor temperature not read yet")
        return t

    # -- exposure --------------------------------------------------------------------------------

    @property
    def state(self) -> CameraState:
        with self._lock:
            if self._busy:
                elapsed = time.monotonic() - self._exp_t0
                return CameraState.EXPOSING if elapsed < self._exp_duration else CameraState.DOWNLOAD
            return CameraState.ERROR if self._error else CameraState.IDLE

    @property
    def percent_completed(self) -> int:
        with self._lock:
            if self._busy:
                elapsed = time.monotonic() - self._exp_t0
                return min(int(100 * elapsed / self._exp_duration), 99) if self._exp_duration else 99
            if self._image is not None or self._error:
                return 100
        raise AlpacaError(INVALID_OPERATION, "no exposure has been started")

    @property
    def image_ready(self) -> bool:
        # A failed exposure reports ImageReady = true and the error comes with ImageArray. N.I.N.A. (3.2)
        # ignores exceptions from ImageReady and would wait for its own timeout (exposure + 60 s).
        with self._lock:
            if self._error:
                return True
            return not self._busy and self._image is not None

    def image(self) -> np.ndarray:
        """Last image as uint16 (H x W), ROI already applied."""
        with self._lock:
            if self._error:
                # ValueNotSet on purpose: for any other error N.I.N.A. (3.2) returns the last image it got
                # and saves it again as a new frame. ValueNotSet makes it see no image, so the frame fails.
                raise AlpacaError(VALUE_NOT_SET, f"exposure failed: {self._error}")
            if self._image is None:
                raise AlpacaError(INVALID_OPERATION, "no image available")
            return self._image

    def image_sent(self, requested_t: float, encode_s: float, send_s: float) -> None:
        """Called by the server once a client has received the image (times from time.monotonic())."""
        log.info("image sent: requested %.1fs after it was ready, encoded in %.2fs, transferred in %.1fs",
                 requested_t - self._image_t, encode_s, send_s)
        self._sent_t = time.monotonic()

    @property
    def last_exposure_start(self) -> str:
        if self._last_start is None:
            raise AlpacaError(VALUE_NOT_SET, "no exposure taken yet")
        return self._last_start

    @property
    def last_exposure_duration(self) -> float:
        if self._last_duration is None:
            raise AlpacaError(VALUE_NOT_SET, "no exposure taken yet")
        return self._last_duration

    def start_exposure(self, duration: float, light: bool) -> None:
        self.require_connected()
        if duration < 0 or duration > self.cfg.exposure_max:
            raise AlpacaError(INVALID_VALUE, f"duration {duration} outside 0..{self.cfg.exposure_max}")
        duration = max(duration, self.cfg.exposure_min)  # bias frames: shortest shutter time
        if self.numx < 1 or self.numy < 1 or self.startx < 0 or self.starty < 0 \
                or self.startx + self.numx > self.spec.width or self.starty + self.numy > self.spec.height:
            raise AlpacaError(INVALID_VALUE, f"subframe {self.startx},{self.starty} {self.numx}x{self.numy} "
                                             f"outside {self.spec.width}x{self.spec.height}")
        with self._lock:
            if self._busy:
                raise AlpacaError(INVALID_OPERATION, "an exposure is already in progress")
            if self._sent_t is not None:
                log.info("StartExposure %.1fs after the last image was sent (client processing)",
                         time.monotonic() - self._sent_t)
                self._sent_t = None
            self._busy = True
            self._image = None
            self._error = None
            self._cancel = threading.Event()
            self._exp_t0 = time.monotonic()
            self._exp_duration = duration
            self._last_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
            self._last_duration = duration
        roi = (self.startx, self.starty, self.numx, self.numy)
        self._exposure_thread = threading.Thread(target=self._expose, args=(duration, self.iso, light, roi, self._cancel),
                                                 name="exposure", daemon=True)
        self._exposure_thread.start()

    def _expose(self, duration: float, iso: int, light: bool, roi, cancel: threading.Event) -> None:
        x, y, w, h = roi
        try:
            full = self.backend.capture(duration, iso, light, cancel)
            img = full[y:y + h, x:x + w] if (w, h) != (full.shape[1], full.shape[0]) else full
            with self._lock:
                if not cancel.is_set():
                    self._image = img
                    self._image_t = time.monotonic()
                    log.info("image ready %.1fs after StartExposure", self._image_t - self._exp_t0)
        except BackendError as e:
            if not cancel.is_set():
                log.error("exposure failed: %s", e)
                with self._lock:
                    self._error = str(e)
        except Exception as e:  # never leave the camera stuck in "busy"
            log.exception("unexpected exposure failure")
            with self._lock:
                self._error = f"{type(e).__name__}: {e}"
        finally:
            with self._lock:
                self._busy = False

    def abort_exposure(self) -> None:
        if not self.backend.can_abort:
            raise not_implemented("AbortExposure")
        self._cancel.set()
