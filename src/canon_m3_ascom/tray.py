"""Run the driver in the Windows notification area (tray) instead of a console window.

The HTTP server runs in a background thread; the tray icon (pystray) owns the main thread. The icon colour
shows the state: grey = waiting for N.I.N.A., green = camera connected, blue = exposing / downloading,
red = error or server not running. The tooltip and the first menu line show details.
"""

import logging
import os
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from .camera import Camera, CameraState

log = logging.getLogger(__name__)

COLORS = {"waiting": (128, 128, 128), "ready": (40, 167, 69), "busy": (0, 120, 215), "error": (220, 53, 69)}


def make_icon(state: str, size: int = 64):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, size - 3, size - 3), fill=COLORS[state] + (255,), outline=(255, 255, 255, 255), width=3)
    # a small camera body, so the icon is recognisable next to the clock
    w, h = size * 0.5, size * 0.32
    x0, y0 = (size - w) / 2, (size - h) / 2 + size * 0.04
    d.rounded_rectangle((x0, y0, x0 + w, y0 + h), radius=size * 0.05, fill=(255, 255, 255, 255))
    d.rectangle((x0 + w * 0.3, y0 - size * 0.07, x0 + w * 0.6, y0), fill=(255, 255, 255, 255))
    r = h * 0.3
    cx, cy = size / 2, y0 + h / 2
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=COLORS[state] + (255,))
    return img


class TrayApp:
    def __init__(self, cfg, camera: Camera, app, config_path: str | None, log_path: Path | None):
        self.cfg = cfg
        self.camera = camera
        self.config_path = config_path
        self.log_path = log_path
        self.server = uvicorn.Server(uvicorn.Config(app, host=cfg.server.host, port=cfg.server.port,
                                                    log_level="warning", log_config=None))
        self.server_thread = threading.Thread(target=self._serve, name="http-server", daemon=True)
        self.server_failed: str | None = None
        self.icon = None
        self._state = None
        self._stop = threading.Event()

    # -- server ----------------------------------------------------------------------------------

    def _serve(self) -> None:
        try:
            self.server.run()
        except BaseException as e:  # noqa: BLE001 - uvicorn exits via SystemExit when it cannot bind
            log.error("HTTP server stopped: %r", e)
        if not self._stop.is_set():
            self.server_failed = f"server on port {self.cfg.server.port} not running (port in use?)"
            log.error(self.server_failed)

    # -- status ----------------------------------------------------------------------------------

    def status(self) -> tuple[str, str]:
        """(state key, human text)"""
        if self.server_failed:
            return "error", self.server_failed
        if not self.server.started:
            return "waiting", "starting..."
        cam = self.camera
        if not cam.connected:
            return "waiting", f"waiting for N.I.N.A. (port {self.cfg.server.port})"
        transport = getattr(cam.backend, "info", {}).get("transport", "")
        temp = cam.backend.sensor_temperature()
        temp_s = f", sensor {temp:.0f} C" if temp is not None else ""
        state = cam.state
        if state == CameraState.ERROR:
            return "error", f"last exposure failed{temp_s}"
        if state in (CameraState.EXPOSING, CameraState.DOWNLOAD):
            return "busy", f"{state.name.lower()}{temp_s}"
        return "ready", f"connected{' via ' + transport if transport else ''}{temp_s}"

    def _watch(self) -> None:
        while not self._stop.wait(1.0):
            key, text = self.status()
            if self.icon is None:
                continue
            if key != self._state:
                self.icon.icon = make_icon(key)
                self._state = key
            self.icon.title = f"Canon M3 ASCOM - {text}"[:127]  # Windows tooltip limit

    # -- menu actions ----------------------------------------------------------------------------

    def _open(self, path) -> None:
        if path and Path(path).exists():
            os.startfile(path)  # noqa: S606 - user-initiated, local file

    def open_status(self, *_):
        webbrowser.open(f"http://127.0.0.1:{self.cfg.server.port}/setup")

    def open_log(self, *_):
        self._open(self.log_path)

    def open_log_folder(self, *_):
        self._open(self.log_path.parent if self.log_path else None)

    def edit_config(self, *_):
        if self.config_path:
            subprocess.Popen(["notepad.exe", str(self.config_path)])  # noqa: S603,S607

    def quit(self, *_):
        log.info("quitting from the tray menu")
        self._stop.set()
        self.server.should_exit = True
        try:
            self.camera.set_connected(False)
        except Exception as e:  # noqa: BLE001
            log.warning("disconnecting the camera: %s", e)
        self.server_thread.join(timeout=5)
        self.icon.stop()

    # -- run -------------------------------------------------------------------------------------

    def run(self) -> None:
        import pystray
        from pystray import Menu, MenuItem as Item

        menu = Menu(
            Item(lambda _: self.status()[1], None, enabled=False),
            Menu.SEPARATOR,
            Item("Open status page", self.open_status, default=True),
            Item("Open log", self.open_log, enabled=lambda _: self.log_path is not None),
            Item("Open log folder", self.open_log_folder, enabled=lambda _: self.log_path is not None),
            Item("Edit configuration", self.edit_config, enabled=lambda _: self.config_path is not None),
            Menu.SEPARATOR,
            Item("Quit Canon M3 ASCOM", self.quit),
        )
        self.icon = pystray.Icon("canon-m3-ascom", make_icon("waiting"), "Canon M3 ASCOM - starting...", menu)
        self.server_thread.start()
        threading.Thread(target=self._watch, name="tray-status", daemon=True).start()
        log.info("running in the notification area")
        self.icon.run()
        # give the HTTP thread a moment if the icon was closed some other way
        self._stop.set()
        self.server.should_exit = True
        time.sleep(0.2)


def hide_own_console() -> None:
    """Hide the console window if this process owns it alone (started from a shortcut, not from a shell)."""
    import ctypes
    k32 = ctypes.windll.kernel32
    hwnd = k32.GetConsoleWindow()
    if not hwnd:
        return
    pids = (ctypes.c_uint32 * 4)()
    if k32.GetConsoleProcessList(pids, 4) <= 1:  # only us -> nobody else needs the window
        ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE


def message_box(text: str, title: str = "Canon M3 ASCOM") -> None:
    import ctypes
    ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)  # MB_ICONINFORMATION
