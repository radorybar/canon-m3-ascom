import threading
from typing import Protocol

import numpy as np

from ..sensor import SensorSpec


class BackendError(Exception):
    pass


class Backend(Protocol):
    spec: SensorSpec
    can_abort: bool
    description: str

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def capture(self, exposure_s: float, iso: int, light: bool, cancel: threading.Event) -> np.ndarray:
        """Take one frame and return the active area as uint16 (H x W) raw ADU.

        Blocks for the exposure plus download. Raises BackendError on failure.
        Backends with can_abort=True return early (raising BackendError) once cancel is set.
        """
        ...

    def sensor_temperature(self) -> float | None:
        """Last known sensor temperature in °C (cached; must not block on USB)."""
        ...
