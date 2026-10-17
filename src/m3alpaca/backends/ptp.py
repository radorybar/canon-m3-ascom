"""Real EOS M3 through our own CHDK PTP client (pyusb + WinUSB), no chdkptp needed.

One persistent USB session. Per frame: init remote capture (raw) -> run the shoot script ->
poll until the raw buffer is ready -> download it -> wait for the script -> uninit.
This is the same sequence chdkptp's `rs -raw` performs (lua/cli.lua, lua/rlibs.lua).
"""

import logging
import threading
import time
from pathlib import Path

import numpy as np

from .. import rawcodec
from ..config import Config
from ..ptp import chdk, scripts
from ..ptp.transport import PtpError, PtpSession, UsbTransport
from ..sensor import EOS_M3
from .base import BackendError

log = logging.getLogger(__name__)


class PtpBackend:
    can_abort = False  # the camera finishes an exposure once started
    description = "Canon EOS M3 via CHDK PTP (USB)"

    def __init__(self, cfg: Config, transport_factory=None):
        self.cfg = cfg.ptp
        self.spec = EOS_M3
        self.frames_dir = Path(cfg.camera.frames_dir)
        self._factory = transport_factory or self._default_factory
        self._lock = threading.Lock()  # one USB conversation at a time
        self._session: PtpSession | None = None
        self._chdk: chdk.ChdkClient | None = None
        self._temperature: float | None = None
        self.info: dict[str, str] = {}

    def _default_factory(self):
        if self.cfg.fake:
            from ..ptp.fake import FakeCamera, FakeTransport
            return FakeTransport(FakeCamera(time_scale=self.cfg.fake_time_scale, download_s=self.cfg.fake_download_s,
                                            focuser_url=self.cfg.fake_focuser_url))
        return UsbTransport(self.cfg.pid)

    # -- connection ------------------------------------------------------------------------------

    def _open(self) -> None:
        transport = self._factory()
        session = PtpSession(transport)
        try:
            session.open()
            dev = session.device_info()
            client = chdk.ChdkClient(session)
            major, minor = client.get_version()
            result = scripts.parse_result(client.execwait(
                scripts.setup(self.cfg.lcd_off, self.cfg.prevent_shutdown, self.cfg.dark_frame_off), 15))
        except (PtpError, ValueError) as e:
            session.close()
            raise BackendError(str(e)) from e
        temperature, support = float(result[0]), int(result[1])
        if not support & chdk.CAPTURE_RAW:
            session.close()
            raise BackendError("this CHDK build does not support raw remote capture")
        self._session, self._chdk, self._temperature = session, client, temperature
        self.info = {"model": dev.model, "firmware": dev.version, "chdk_protocol": f"{major}.{minor}"}
        log.info("connected: %s fw %s, CHDK PTP %d.%d, sensor %.0f°C", dev.model, dev.version, major, minor,
                 temperature)

    def _drop(self) -> None:
        if self._session:
            try:
                self._session.close()
            except Exception:  # the USB link may already be gone
                pass
        self._session = self._chdk = None

    def connect(self) -> None:
        with self._lock:
            self._drop()
            self._open()

    def disconnect(self) -> None:
        with self._lock:
            self._drop()

    def sensor_temperature(self) -> float | None:
        return self._temperature

    # -- capture ---------------------------------------------------------------------------------

    def capture(self, exposure_s: float, iso: int, light: bool, cancel: threading.Event) -> np.ndarray:
        with self._lock:
            if self._chdk is None:
                log.info("reconnecting to the camera")
                self._open()
            try:
                raw = self._capture_raw(exposure_s, iso)
            except (PtpError, ValueError) as e:
                self._drop()  # next frame reconnects from scratch
                raise BackendError(str(e)) from e
        if len(raw) != self.spec.raw_size_bytes:
            raise BackendError(f"raw size {len(raw)} bytes, expected {self.spec.raw_size_bytes} "
                               f"({self.spec.raw_width}x{self.spec.raw_height} {self.spec.bits}-bit)")
        if self.cfg.keep_raw:
            self.frames_dir.mkdir(parents=True, exist_ok=True)
            (self.frames_dir / f"m3_{time.strftime('%Y%m%d_%H%M%S')}.raw").write_bytes(raw)
        return rawcodec.decode_chdk_raw(raw, self.spec)

    def _capture_raw(self, exposure_s: float, iso: int) -> bytes:
        c = self._chdk
        scripts.parse_result(c.execwait(scripts.capture_init(chdk.CAPTURE_RAW, 30000)))
        try:
            t0 = time.monotonic()
            c.execute(scripts.shoot(exposure_s, iso))
            deadline = t0 + 2 * exposure_s + 10 + self.cfg.timeout_extra_s  # 2x: possible dark frame
            # The raw buffer is handed over by the camera's capture task, independently of the script:
            # shoot() usually returns before the data is ready, so the script ending is not an error
            # (chdkptp also waits on the capture status only).
            result = None
            while True:
                status, _ = c.capture_ready()
                if status == chdk.CAPTURE_NOTSET:
                    raise BackendError("remote capture was not initialised on the camera")
                if status & chdk.CAPTURE_RAW:
                    break
                if result is None and not c.script_status()[0]:
                    result = scripts.parse_result(c.wait_script(1))  # raises the camera's Lua error, if any
                    log.debug("shoot script done after %.1fs, waiting for raw data", time.monotonic() - t0)
                if time.monotonic() > deadline:
                    raise BackendError(f"no image from the camera after {time.monotonic() - t0:.0f}s"
                                       + (" (the shot script finished - did the shutter fire?)" if result is not None
                                          else ""))
                time.sleep(0.05)
            t_ready = time.monotonic()
            raw = c.capture_get_data(chdk.CAPTURE_RAW)
            t_dl = time.monotonic()
            if result is None:
                result = scripts.parse_result(c.wait_script(30))
            if result:
                self._temperature = float(result[0])
            log.info("frame %.3fs ISO%d: exposure+processing %.1fs, download %.1fs (%.1f MB/s)",
                     exposure_s, iso, t_ready - t0, t_dl - t_ready, len(raw) / 1e6 / max(t_dl - t_ready, 1e-3))
            return raw
        finally:
            try:
                c.execwait(scripts.CAPTURE_DONE)
            except (PtpError, ValueError) as e:
                log.warning("could not reset remote capture: %s", e)
