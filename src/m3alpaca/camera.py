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
        self.startx = 0
        self.starty = 0
        self.numx = self.spec.width
        self.numy = self.spec.height
        self._gain_index = ISO_VALUES.index(cfg.default_iso) if cfg.default_iso in ISO_VALUES else 3
        self._busy = False
        self._exp_t0 = 0.0
        self._exp_duration = 0.0
        self._cancel = threading.Event()
        self._image: np.ndarray | None = None
        self._error: str | None = None
        self._last_start: str | None = None
        self._last_duration: float | None = None

    # -- connection ------------------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connected

    def set_connected(self, value: bool) -> None:
        if value == self._connected:
            return
        if value:
            try:
                self.backend.connect()
            except BackendError as e:
                raise AlpacaError(UNSPECIFIED, f"cannot connect to camera: {e}") from e
            self._connected = True
        else:
            self._cancel.set()
            self.backend.disconnect()
            self._connected = False

    def require_connected(self) -> None:
        if not self._connected:
            raise AlpacaError(NOT_CONNECTED, "camera is not connected")

    # -- gain (ISO) ------------------------------------------------------------------------------

    @property
    def gains(self) -> list[str]:
        return [str(i) for i in ISO_VALUES]

    @property
    def gain(self) -> int:
        return self._gain_index

    def set_gain(self, index: int) -> None:
        if not 0 <= index < len(ISO_VALUES):
            raise AlpacaError(INVALID_VALUE, f"gain index {index} outside 0..{len(ISO_VALUES) - 1}")
        self._gain_index = index

    @property
    def iso(self) -> int:
        return ISO_VALUES[self._gain_index]

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
        with self._lock:
            if self._error:
                raise AlpacaError(UNSPECIFIED, f"exposure failed: {self._error}")
            return not self._busy and self._image is not None

    def image(self) -> np.ndarray:
        """Last image as uint16 (H x W), ROI already applied."""
        with self._lock:
            if self._image is None:
                raise AlpacaError(INVALID_OPERATION, "no image available")
            return self._image

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
            self._busy = True
            self._image = None
            self._error = None
            self._cancel = threading.Event()
            self._exp_t0 = time.monotonic()
            self._exp_duration = duration
            self._last_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
            self._last_duration = duration
        roi = (self.startx, self.starty, self.numx, self.numy)
        threading.Thread(target=self._expose, args=(duration, self.iso, light, roi, self._cancel),
                         name="exposure", daemon=True).start()

    def _expose(self, duration: float, iso: int, light: bool, roi, cancel: threading.Event) -> None:
        x, y, w, h = roi
        try:
            full = self.backend.capture(duration, iso, light, cancel)
            img = full[y:y + h, x:x + w] if (w, h) != (full.shape[1], full.shape[0]) else full
            with self._lock:
                if not cancel.is_set():
                    self._image = img
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
