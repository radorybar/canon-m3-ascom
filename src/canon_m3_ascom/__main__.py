"""Run the Alpaca camera server: `canon-m3-ascom --config config.toml` (or `python -m canon_m3_ascom`)."""

import argparse
import logging
import sys

import uvicorn

from .alpaca.discovery import DiscoveryResponder
from .alpaca.server import create_app
from .backends import create_backend
from .camera import Camera
from .config import BACKENDS, load_config
from .logsetup import default_log_dir, setup_logging

SUBCOMMANDS = {"diag": "ptpdiag", "detect": "detect"}  # `canon-m3-ascom diag ...` = python -m canon_m3_ascom.ptpdiag ...

log = logging.getLogger("canon_m3_ascom")


def _single_instance(port: int) -> bool:
    """False if another canon-m3-ascom server on this port is already running (Windows named mutex)."""
    if sys.platform != "win32":
        return True
    import ctypes
    k32 = ctypes.windll.kernel32
    _single_instance.handle = k32.CreateMutexW(None, False, f"Local\\canon-m3-ascom-{port}")  # kept for the process lifetime
    return k32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in SUBCOMMANDS:
        import importlib
        sub = sys.argv[1]
        sys.argv = [f"{sys.argv[0]} {sub}", *sys.argv[2:]]
        importlib.import_module(f".{SUBCOMMANDS[sub]}", __package__).main()
        return
    ap = argparse.ArgumentParser(prog="canon-m3-ascom", description=__doc__,
                                 epilog="Also: canon-m3-ascom diag [--shoot SECONDS], canon-m3-ascom detect [--json]")
    ap.add_argument("--config", help="TOML config file (see config.example.toml)")
    ap.add_argument("--tray", action="store_true", help="run in the notification area instead of a console window")
    ap.add_argument("--backend", choices=list(BACKENDS), help="override camera.backend")
    ap.add_argument("--fake-camera", action="store_true", help="ptp backend: emulate the camera on USB level")
    ap.add_argument("--host", help="override server.host")
    ap.add_argument("--port", type=int, help="override server.port")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.backend:
        cfg.camera.backend = args.backend
    if args.fake_camera:
        cfg.ptp.fake = True
    if args.host:
        cfg.server.host = args.host
    if args.port:
        cfg.server.port = args.port

    if args.tray:
        from .tray import hide_own_console, message_box
        hide_own_console()
    log_path = setup_logging(args.log_level, cfg.server.log_dir or default_log_dir(args.config), console=not args.tray)

    if not _single_instance(cfg.server.port):
        msg = f"canon-m3-ascom is already running on port {cfg.server.port}."
        log.error(msg)
        if args.tray:
            message_box(msg + "\nLook for its icon in the notification area.")
        sys.exit(1)

    camera = Camera(create_backend(cfg), cfg.camera)
    app = create_app(camera, cfg.server)
    if cfg.server.discovery:
        # bound to the HTTP address, so we never advertise an interface the API is not listening on
        try:
            DiscoveryResponder(cfg.server.port, cfg.server.discovery_port, bind=cfg.server.host).start()
        except OSError as e:
            log.warning("discovery disabled (UDP %d): %s - use N.I.N.A. 'Alpaca Direct' instead",
                        cfg.server.discovery_port, e)
    log.info("canon-m3-ascom on http://%s:%d (backend: %s), log: %s", cfg.server.host, cfg.server.port,
             cfg.camera.backend, log_path or "console only")

    if args.tray:
        from .tray import TrayApp
        TrayApp(cfg, camera, app, args.config, log_path).run()
    else:
        uvicorn.run(app, host=cfg.server.host, port=cfg.server.port, log_level="warning", log_config=None)


if __name__ == "__main__":
    main()
