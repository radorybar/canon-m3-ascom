"""Run the Alpaca camera server: `m3alpaca --config config.toml` (or `python -m m3alpaca`)."""

import argparse
import logging

import uvicorn

from .alpaca.discovery import DiscoveryResponder
from .alpaca.server import create_app
from .backends import create_backend
from .camera import Camera
from .config import BACKENDS, load_config


def main() -> None:
    ap = argparse.ArgumentParser(prog="m3alpaca", description=__doc__)
    ap.add_argument("--config", help="TOML config file (see config.example.toml)")
    ap.add_argument("--backend", choices=list(BACKENDS), help="override camera.backend")
    ap.add_argument("--fake-camera", action="store_true", help="ptp backend: emulate the camera on USB level")
    ap.add_argument("--host", help="override server.host")
    ap.add_argument("--port", type=int, help="override server.port")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)
    if args.backend:
        cfg.camera.backend = args.backend
    if args.fake_camera:
        cfg.ptp.fake = True
    if args.host:
        cfg.server.host = args.host
    if args.port:
        cfg.server.port = args.port

    camera = Camera(create_backend(cfg), cfg.camera)
    app = create_app(camera, cfg.server)
    if cfg.server.discovery:
        # bound to the HTTP address, so we never advertise an interface the API is not listening on
        try:
            DiscoveryResponder(cfg.server.port, cfg.server.discovery_port, bind=cfg.server.host).start()
        except OSError as e:
            logging.warning("discovery disabled (UDP %d): %s — use N.I.N.A. 'Alpaca Direct' instead",
                            cfg.server.discovery_port, e)
    logging.info("m3alpaca on http://%s:%d (backend: %s)", cfg.server.host, cfg.server.port, cfg.camera.backend)
    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port, log_level="warning")


if __name__ == "__main__":
    main()
