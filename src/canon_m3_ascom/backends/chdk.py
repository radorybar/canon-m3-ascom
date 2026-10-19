"""Real EOS M3 through chdkptp, in batch mode (one chdkptp process per operation).

Batch mode reconnects every frame (~1-2 s) but avoids driving chdkptp's interactive REPL over a
pipe, which is unverified (docs/phase0-results.md, C). A persistent session can replace this later.
"""

import itertools
import logging
import re
import subprocess
import threading
import time
from pathlib import Path

import numpy as np

from .. import rawcodec
from ..config import Config
from ..sensor import EOS_M3
from .base import BackendError

log = logging.getLogger(__name__)

_TEMP_RE = re.compile(r"return:(-?\d+(?:\.\d+)?)")
_ERROR_RE = re.compile(r"^\s*(error|ERROR)\b.*$", re.MULTILINE)


def format_tv(exposure_s: float) -> str:
    """Seconds as chdkptp accepts them: decimal for >= 1 s, X/Y below (e.g. 1/4000)."""
    if exposure_s >= 1:
        return f"{exposure_s:g}"
    return f"1/{round(1 / exposure_s)}"


class ChdkBackend:
    can_abort = False  # an exposure in progress on the camera cannot be interrupted over PTP
    description = "Canon EOS M3 via CHDK/chdkptp"

    def __init__(self, cfg: Config):
        self.cfg = cfg.chdk
        self.spec = EOS_M3
        self.frames_dir = Path(cfg.camera.frames_dir)
        self._seq = itertools.count(1)
        self._temperature: float | None = None
        self._connected = False

    # -- chdkptp process -------------------------------------------------------------------------

    def _run(self, commands: list[str], timeout: float) -> str:
        argv = [*self.cfg.command, "-c", *(f"-e{c}" for c in commands)]
        log.debug("run %s", argv)
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError as e:
            raise BackendError(f"chdkptp not found: {self.cfg.command[0]}") from e
        except subprocess.TimeoutExpired as e:
            raise BackendError(f"chdkptp timed out after {timeout:.0f}s") from e
        out = (proc.stdout or "") + (proc.stderr or "")
        log.debug("chdkptp exit=%s output=%s", proc.returncode, out.strip())
        err = _ERROR_RE.search(out)
        if proc.returncode != 0 or err:
            detail = err.group(0).strip() if err else (out.strip().splitlines() or ["no output"])[-1]
            raise BackendError(f"chdkptp failed (exit {proc.returncode}): {detail}")
        self._parse_temperature(out)
        return out

    def _parse_temperature(self, out: str) -> None:
        m = _TEMP_RE.findall(out)
        if m:
            self._temperature = float(m[-1])

    # -- Backend ---------------------------------------------------------------------------------

    def connect(self) -> None:
        lua = []
        if self.cfg.prevent_shutdown:
            lua.append("pcall(set_config_value,221,3)")  # alt_prevent_shutdown = always (id from conf.c)
        if self.cfg.lcd_off:
            lua.append("set_lcd_display(0)")
        lua.append("return get_temperature(1)")
        self._run(["rec", "=" + " ".join(lua)], timeout=30)
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        self._connected = True
        log.info("connected, sensor %.0f°C", self._temperature or float("nan"))

    def disconnect(self) -> None:
        self._connected = False

    def sensor_temperature(self) -> float | None:
        return self._temperature

    def capture(self, exposure_s: float, iso: int, light: bool, cancel: threading.Event) -> np.ndarray:
        if not self._connected:
            raise BackendError("not connected")
        # light=False (darks/bias): the camera has no way to block the telescope; the user caps it.
        base = self.frames_dir / f"m3_{int(time.time())}_{next(self._seq):05d}"
        fmt = "-raw" if self.cfg.format == "raw" else "-dng"
        rs = f"rs {base.as_posix()} {fmt} -tv={format_tv(exposure_s)} -svm={iso}"
        t0 = time.monotonic()
        self._run([rs, "=return get_temperature(1)"], timeout=2 * exposure_s + 10 + self.cfg.timeout_extra_s)
        files = sorted(self.frames_dir.glob(base.name + ".*"))
        if not files:
            raise BackendError(f"chdkptp produced no file for {base.name}")
        path = files[0]
        log.info("captured %s (%d bytes) in %.1fs", path.name, path.stat().st_size, time.monotonic() - t0)
        try:
            if path.suffix.lower() != ".raw":
                raise BackendError(f"unsupported frame format {path.suffix} (only .raw for now)")
            return rawcodec.decode_chdk_raw(path.read_bytes(), self.spec)
        finally:
            if not self.cfg.keep_files:
                path.unlink(missing_ok=True)
