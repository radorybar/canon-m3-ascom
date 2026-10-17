"""Simulated EOS M3: renders a star field, optionally coupled to an Alpaca focuser."""

import logging
import threading
import time

import numpy as np

from .. import focuser, rawcodec, starfield
from ..config import Config
from ..sensor import EOS_M3
from .base import BackendError

log = logging.getLogger(__name__)


class SimBackend:
    can_abort = True
    description = "Simulated Canon EOS M3"

    def __init__(self, cfg: Config):
        self.cfg = cfg.sim
        self.spec = EOS_M3
        self.params = starfield.StarFieldParams(stars=self.cfg.stars, best_focus=self.cfg.best_focus)
        self._connected = False

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def sensor_temperature(self) -> float | None:
        return self.cfg.temperature_c

    def capture(self, exposure_s: float, iso: int, light: bool, cancel: threading.Event) -> np.ndarray:
        if not self._connected:
            raise BackendError("not connected")
        if cancel.wait(exposure_s):
            raise BackendError("exposure aborted")
        focus = focuser.read_position(self.cfg.focuser_url)
        t0 = time.monotonic()
        img = starfield.render(self.spec, exposure_s, iso, light, focus, self.params)
        if self.cfg.roundtrip_codec:
            img = rawcodec.decode_chdk_raw(rawcodec.encode_chdk_raw(img, self.spec), self.spec)
        spent = time.monotonic() - t0
        log.info("sim frame %.3fs ISO%d focus=%s rendered in %.2fs", exposure_s, iso, focus, spent)
        if cancel.wait(max(self.cfg.download_s - spent, 0)):
            raise BackendError("exposure aborted")
        return img
