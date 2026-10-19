import json
import socket
import threading

import numpy as np
import pytest

from canon_m3_ascom import starfield
from canon_m3_ascom.alpaca.discovery import DiscoveryResponder
from canon_m3_ascom.backends import BackendError
from canon_m3_ascom.backends.chdk import ChdkBackend, format_tv
from canon_m3_ascom.sensor import EOS_M3

from .conftest import make_config


def test_format_tv():
    assert format_tv(60) == "60"
    assert format_tv(2.5) == "2.5"
    assert format_tv(1 / 4000) == "1/4000"


def test_chdk_backend_with_fake_chdkptp(tmp_path, fake_chdk_env, monkeypatch):
    monkeypatch.setenv("FAKE_CHDK_TEMP", "27")
    b = ChdkBackend(make_config("chdk", tmp_path))
    b.connect()
    assert b.sensor_temperature() == 27
    img = b.capture(1.0, 800, True, threading.Event())
    assert img.shape == (4000, 6000) and img.dtype == np.uint16
    assert not list((tmp_path / "frames").iterdir())  # .raw removed after decoding


def test_chdk_backend_no_camera(tmp_path, fake_chdk_env, monkeypatch):
    monkeypatch.setenv("FAKE_CHDK_FAIL", "connect")
    with pytest.raises(BackendError, match="no matching devices"):
        ChdkBackend(make_config("chdk", tmp_path)).connect()


def test_chdk_backend_missing_executable(tmp_path):
    cfg = make_config("chdk", tmp_path)
    cfg.chdk.command = [str(tmp_path / "nope.exe")]
    with pytest.raises(BackendError, match="not found"):
        ChdkBackend(cfg).connect()


def test_defocus_blurs_stars():
    p = starfield.StarFieldParams(stars=50, read_noise_e=0, sky_e_per_s=0)
    sharp = starfield.render(EOS_M3, 10, 800, True, p.best_focus, p, noise_seed=1)
    blurred = starfield.render(EOS_M3, 10, 800, True, p.best_focus + 2000, p, noise_seed=1)
    assert sharp.max() > 2 * blurred.max()
    # roughly the same total light, just spread out
    s, b = (float(np.sum(i.astype(np.int64) - EOS_M3.black_level)) for i in (sharp, blurred))
    assert abs(s - b) / s < 0.2


def test_discovery_survives_vanished_client():
    """Windows raises ConnectionResetError after replying to a client that already closed its socket."""
    d = DiscoveryResponder(alpaca_port=11112, discovery_port=0, bind="127.0.0.1").start()
    try:
        gone = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        gone.sendto(b"alpacadiscovery1", ("127.0.0.1", d.port))
        gone.close()
        import time
        time.sleep(0.3)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        s.sendto(b"alpacadiscovery1", ("127.0.0.1", d.port))
        assert json.loads(s.recvfrom(1024)[0]) == {"AlpacaPort": 11112}
        assert d.alive
    finally:
        d.close()


def test_discovery_ignores_other_messages_and_floods():
    d = DiscoveryResponder(alpaca_port=11112, discovery_port=0, bind="127.0.0.1").start()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.sendto(b"hello", ("127.0.0.1", d.port))
        with pytest.raises(socket.timeout):
            s.recvfrom(1024)
        for _ in range(5):  # a burst gets one answer
            s.sendto(b"alpacadiscovery1", ("127.0.0.1", d.port))
        s.recvfrom(1024)
        with pytest.raises(socket.timeout):
            s.recvfrom(1024)
    finally:
        d.close()


def test_discovery():
    d = DiscoveryResponder(alpaca_port=11112, discovery_port=0, bind="127.0.0.1").start()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        s.sendto(b"alpacadiscovery1", ("127.0.0.1", d.port))
        data, _ = s.recvfrom(1024)
        assert json.loads(data) == {"AlpacaPort": 11112}
    finally:
        d.close()
