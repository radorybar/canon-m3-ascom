"""Log file setup and tray status logic (no window is shown)."""

import logging
import threading
import time

import pytest

from canon_m3_ascom import logsetup
from canon_m3_ascom.alpaca.server import create_app
from canon_m3_ascom.backends import create_backend
from canon_m3_ascom.camera import Camera

from .conftest import make_config


@pytest.fixture
def restore_logging():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    for h in handlers:
        root.addHandler(h)
    root.setLevel(level)


def test_log_file_next_to_config(tmp_path, restore_logging):
    cfg_file = tmp_path / "config.toml"
    assert logsetup.default_log_dir(str(cfg_file)) == tmp_path / "logs"
    path = logsetup.setup_logging("INFO", logsetup.default_log_dir(str(cfg_file)), console=False)
    logging.getLogger("canon_m3_ascom.test").info("hello night")
    for h in logging.getLogger().handlers:
        h.flush()
    assert path == tmp_path / "logs" / "canon-m3-ascom.log"
    assert "hello night" in path.read_text(encoding="utf-8")


def test_unwritable_log_dir_does_not_crash(tmp_path, restore_logging):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert logsetup.setup_logging("INFO", blocker / "logs", console=False) is None


def test_icon_images():
    pytest.importorskip("PIL")
    from canon_m3_ascom.tray import COLORS, make_icon
    for state in COLORS:
        img = make_icon(state)
        assert img.size == (64, 64)


def test_tray_status_follows_camera(tmp_path):
    pytest.importorskip("pystray")
    from canon_m3_ascom.tray import TrayApp
    cfg = make_config("sim", tmp_path)
    cfg.server.port = 0
    cam = Camera(create_backend(cfg), cfg.camera)
    tray = TrayApp(cfg, cam, create_app(cam, cfg.server), None, None)
    assert tray.status()[0] == "waiting"  # server not started yet
    tray.server.started = True
    assert tray.status() == ("waiting", "waiting for N.I.N.A. (port 0)")
    cam.set_connected(True)
    assert tray.status()[0] == "ready" and "sensor 18 C" in tray.status()[1]
    cam.start_exposure(0.5, True)
    assert tray.status()[0] == "busy"
    for _ in range(100):
        if not cam._busy:
            break
        time.sleep(0.05)
    assert tray.status()[0] == "ready"
    tray.server_failed = "server on port 0 not running (port in use?)"
    assert tray.status()[0] == "error"


def test_server_thread_failure_is_reported(tmp_path):
    pytest.importorskip("pystray")
    from canon_m3_ascom.tray import TrayApp
    cfg = make_config("sim", tmp_path)
    cam = Camera(create_backend(cfg), cfg.camera)
    tray = TrayApp(cfg, cam, create_app(cam, cfg.server), None, None)

    def boom():
        raise SystemExit(1)  # what uvicorn does when the port is taken

    tray.server.run = boom
    t = threading.Thread(target=tray._serve)
    t.start()
    t.join(5)
    assert tray.status()[0] == "error" and "port" in tray.status()[1]
