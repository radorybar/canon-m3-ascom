import sys

import pytest
from fastapi.testclient import TestClient

from canon_m3_ascom.alpaca.server import create_app
from canon_m3_ascom.backends import create_backend
from canon_m3_ascom.camera import Camera
from canon_m3_ascom.config import Config

FAKE_CHDKPTP = [sys.executable, "-m", "canon_m3_ascom.fakechdk"]


def make_config(backend: str, tmp_path) -> Config:
    cfg = Config()
    cfg.camera.backend = backend
    cfg.camera.frames_dir = str(tmp_path / "frames")
    cfg.sim.download_s = 0
    cfg.sim.roundtrip_codec = False
    cfg.sim.stars = 100
    cfg.chdk.command = FAKE_CHDKPTP
    cfg.ptp.fake = True
    cfg.ptp.fake_download_s = 0
    cfg.ptp.fake_time_scale = 0
    return cfg


@pytest.fixture
def fake_chdk_env(monkeypatch):
    monkeypatch.setenv("FAKE_CHDK_TIME_SCALE", "0")
    monkeypatch.delenv("FAKE_CHDK_FAIL", raising=False)


@pytest.fixture(params=["sim", "chdk", "ptp"])
def client(request, tmp_path, fake_chdk_env):
    cfg = make_config(request.param, tmp_path)
    cam = Camera(create_backend(cfg), cfg.camera)
    with TestClient(create_app(cam, cfg.server)) as c:
        c.backend_name = request.param
        yield c
