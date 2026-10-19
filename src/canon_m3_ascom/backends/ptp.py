"""Real EOS M3 through our own CHDK PTP client (pyusb + WinUSB), no chdkptp needed.

One persistent USB session. Per frame: init remote capture (raw) -> run the shoot script ->
poll until the raw buffer is ready -> download it -> wait for the script -> uninit.
This is the same sequence chdkptp's `rs -raw` performs (lua/cli.lua, lua/rlibs.lua).
"""

import logging
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from .. import rawcodec
from ..config import Config
from ..ptp import chdk, scripts
from ..ptp.connect import open_session
from ..ptp.transport import PtpError, PtpSession
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
        self._keepalive_stop = threading.Event()
        self.info: dict[str, str] = {}

    def _default_factory(self):
        if self.cfg.fake:
            from ..ptp.fake import FakeCamera, FakeTransport
            return FakeTransport(FakeCamera(time_scale=self.cfg.fake_time_scale, download_s=self.cfg.fake_download_s,
                                            focuser_url=self.cfg.fake_focuser_url))
        return open_session(self.cfg.transport, self.cfg.pid)

    # -- connection ------------------------------------------------------------------------------

    def _open(self) -> None:
        try:
            made = self._factory()
        except PtpError as e:
            raise BackendError(str(e)) from e
        # factories return either a ready session (WinUSB/WPD) or a raw byte transport (tests, emulator)
        session = made if hasattr(made, "transaction") else PtpSession(made)
        is_wpd = type(session).__name__ == "WpdSession"
        try:
            session.open()
            dev = session.device_info()
            client = chdk.ChdkClient(session)
            major, minor = client.get_version()
            result = scripts.parse_result(client.execwait(
                scripts.setup(self.cfg.lcd_off, self.cfg.prevent_shutdown, self.cfg.dark_frame_off,
                              unlock=self.cfg.unlock), 15))
        except (PtpError, ValueError) as e:
            session.close()
            raise BackendError(str(e)) from e
        temperature, support = float(result[0]), int(result[1])
        if not support & chdk.CAPTURE_RAW:
            session.close()
            raise BackendError("this CHDK build does not support raw remote capture")
        self._session, self._chdk, self._temperature = session, client, temperature
        kind = "WPD" if is_wpd else "WinUSB"
        self.info = {"model": dev.model, "firmware": dev.version, "chdk_protocol": f"{major}.{minor}",
                     "transport": kind}
        log.info("connected via %s: %s fw %s, CHDK PTP %d.%d, sensor %.0f C", kind, dev.model, dev.version,
                 major, minor, temperature)

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
        self._start_keepalive()

    def disconnect(self) -> None:
        self._keepalive_stop.set()
        with self._lock:
            self._capture_done()
            self._drop()

    # -- keep-alive ------------------------------------------------------------------------------

    def _start_keepalive(self) -> None:
        if self.cfg.keepalive_s <= 0:
            return
        self._keepalive_stop.set()  # stop a previous thread
        stop = self._keepalive_stop = threading.Event()
        threading.Thread(target=self._keepalive, args=(stop,), name="ptp-keepalive", daemon=True).start()

    def _keepalive(self, stop: threading.Event) -> None:
        """While idle: read the sensor temperature; reconnect if the link was lost. Never blocks a capture."""
        link_ok = True
        while not stop.wait(self.cfg.keepalive_s):
            if not self._lock.acquire(blocking=False):
                continue  # a capture (or connect) is running - that is proof of life enough
            try:
                if stop.is_set():
                    return
                if self._chdk is None:
                    self._open()
                    log.info("keep-alive: camera reconnected")
                result = scripts.parse_result(self._chdk.execwait(scripts.TEMPERATURE, 10))
                if result:
                    self._temperature = float(result[0])
                link_ok = True
            except (PtpError, ValueError, BackendError) as e:
                if link_ok:  # log the change, not every minute of an outage
                    log.warning("keep-alive: camera not reachable (%s); retrying every %.0f s", e,
                                self.cfg.keepalive_s)
                link_ok = False
                self._drop()
            finally:
                self._lock.release()

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
            except BackendError:
                self._drop()  # e.g. no data delivered: reconnect (and unlock) for the next frame
                raise
            except (PtpError, ValueError) as e:
                self._drop()  # next frame reconnects from scratch
                raise BackendError(str(e)) from e
        if len(raw) != self.spec.raw_size_bytes:
            raise BackendError(f"raw size {len(raw)} bytes, expected {self.spec.raw_size_bytes} "
                               f"({self.spec.raw_width}x{self.spec.raw_height} {self.spec.bits}-bit)")
        if self.cfg.keep_raw:
            self.frames_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]  # ms: short frames come several per second
            (self.frames_dir / f"m3_{stamp}.raw").write_bytes(raw)
        t0 = time.monotonic()
        img = rawcodec.decode_chdk_raw(raw, self.spec)
        log.debug("raw decoded in %.2fs", time.monotonic() - t0)
        return img

    def _capture_raw(self, exposure_s: float, iso: int) -> bytes:
        c = self._chdk
        # Remote capture stays initialised between frames (one cheap PTP query instead of two Lua scripts
        # per frame); it is switched off on disconnect and after a failed frame.
        if c.capture_ready()[0] == chdk.CAPTURE_NOTSET:
            scripts.parse_result(c.execwait(scripts.capture_init(chdk.CAPTURE_RAW, 30000)))
        ok = False
        try:
            t0 = time.monotonic()
            c.execute(scripts.shoot(exposure_s, iso))
            deadline = t0 + 2 * exposure_s + 10 + self.cfg.timeout_extra_s  # 2x: possible dark frame
            # The raw buffer is handed over by the camera's capture task, independently of the script:
            # the shot script usually ends before the data is ready, so the script ending is not an error
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
                # poll gently while the shutter is open, quickly from ~1 s before the expected end
                time.sleep(1.0 if time.monotonic() - t0 < exposure_s - 1.0 else 0.05)
            t_ready = time.monotonic()
            raw = c.capture_get_data(chdk.CAPTURE_RAW)
            t_dl = time.monotonic()
            if result is None:
                result = scripts.parse_result(c.wait_script(30))
            if result:
                self._temperature = float(result[0])
            log.info("frame %.3fs ISO%d: exposure+processing %.1fs, download %.1fs (%.1f MB/s)",
                     exposure_s, iso, t_ready - t0, t_dl - t_ready, len(raw) / 1e6 / max(t_dl - t_ready, 1e-3))
            ok = True
            return raw
        finally:
            if not ok:
                self._capture_done()

    def _capture_done(self) -> None:
        """Switch remote capture off, so the camera saves to its card again (best effort)."""
        if self._chdk is None:
            return
        try:
            self._chdk.execwait(scripts.CAPTURE_DONE, 5)
        except (PtpError, ValueError) as e:
            log.warning("could not reset remote capture: %s", e)
