"""PTP/CHDK stack against the USB-level camera emulator."""

import struct
import threading

import numpy as np
import pytest

from m3alpaca.backends.base import BackendError
from m3alpaca.backends.ptp import PtpBackend
from m3alpaca.ptp import chdk, scripts
from m3alpaca.ptp.fake import FakeCamera, FakeTransport
from m3alpaca.ptp.transport import HEADER, PtpResponseError, PtpSession

from .conftest import make_config


def session_for(camera: FakeCamera) -> tuple[PtpSession, FakeTransport]:
    t = FakeTransport(camera)
    s = PtpSession(t)
    s.open()
    return s, t


def test_apex_values_match_chdkptp():
    assert scripts.tv96(1) == 0
    assert scripts.tv96(60) == -567
    assert scripts.tv96(1 / 4000) == 1149
    assert scripts.sv96(100) == 480  # CHDK: ISO 100 = sv96 480
    assert scripts.sv96(800) == 768


def test_device_info_roundtrip():
    s, _ = session_for(FakeCamera())
    info = s.device_info()
    assert info.model == "Canon EOS M3" and chdk.OC_CHDK in info.operations


def test_zero_length_packet_handling():
    """A container that is an exact multiple of the packet size is followed by a ZLP."""
    cam = FakeCamera()
    s, t = session_for(cam)
    c = chdk.ChdkClient(s)
    c.get_version()
    payload_len = 512 * 4 - HEADER.size  # data container of exactly 4 packets
    cam.messages.append((chdk.MsgType.RETURN, chdk.DataType.STRING, b"x" * payload_len))
    cam.messages.append((chdk.MsgType.RETURN, chdk.DataType.STRING, b"ok"))
    assert c.read_message().value == "x" * payload_len
    assert c.read_message().value == "ok"  # stream still in sync after the ZLP


def test_script_values_and_errors():
    s, _ = session_for(FakeCamera())
    c = chdk.ChdkClient(s)
    c.get_version()
    assert c.execwait(scripts.TEMPERATURE) == ["ok|21"]
    with pytest.raises(chdk.ScriptError, match="compile"):
        c.execwait("this is not lua")
    with pytest.raises(ValueError, match="not in record mode"):
        scripts.parse_result(c.execwait(scripts.capture_init(chdk.CAPTURE_RAW, 1000)))


def test_chdk_not_loaded():
    s, _ = session_for(FakeCamera(chdk_loaded=False))
    with pytest.raises(chdk.ChdkError, match="is CHDK loaded"):
        chdk.ChdkClient(s).get_version()


def backend(tmp_path, camera: FakeCamera) -> PtpBackend:
    return PtpBackend(make_config("ptp", tmp_path), transport_factory=lambda: FakeTransport(camera))


def test_capture_sequence(tmp_path):
    cam = FakeCamera(time_scale=0, temperature=27)
    b = backend(tmp_path, cam)
    b.connect()
    assert b.info["model"] == "Canon EOS M3" and b.sensor_temperature() == 27
    img = b.capture(2.0, 1600, True, threading.Event())
    assert img.shape == (4000, 6000) and img.dtype == np.uint16
    assert cam.scripts_run == [scripts.TAG_SETUP, scripts.TAG_CAPINIT, scripts.TAG_SHOOT, scripts.TAG_CAPDONE]
    assert cam.capture_fmt == 0  # remote capture uninitialised again
    # a second frame on the same session
    b.capture(1.0, 800, True, threading.Event())
    assert cam.shots == 2


def test_no_raw_support(tmp_path):
    with pytest.raises(BackendError, match="raw remote capture"):
        backend(tmp_path, FakeCamera(raw_capture=False)).connect()


def test_reconnect_after_usb_failure(tmp_path):
    cam = FakeCamera(time_scale=0)
    b = backend(tmp_path, cam)
    b.connect()
    original = b._chdk.capture_ready

    def broken():
        raise PtpResponseError(chdk.OC_CHDK, 0x2002)

    b._chdk.capture_ready = broken
    with pytest.raises(BackendError):
        b.capture(1.0, 800, True, threading.Event())
    assert b._chdk is None  # session dropped
    assert original is not None
    img = b.capture(1.0, 800, True, threading.Event())  # reconnects transparently
    assert img.shape == (4000, 6000)


def test_multi_chunk_assembly():
    """RemoteCaptureGetData may deliver data in chunks with seeks."""
    s, t = session_for(FakeCamera())
    c = chdk.ChdkClient(s)
    replies = iter([(b"world", 1, 6), (b"hello ", 0, 0)])

    def handle(op, params, data):
        d, more, seek = next(replies)
        return 0x2001, [len(d), more, seek], d

    t.camera.handle = handle
    assert c.capture_get_data(chdk.CAPTURE_RAW) == b"hello world"


def test_response_params_parsing():
    s, t = session_for(FakeCamera())
    t.camera.handle = lambda op, params, data: (0x2001, [1, 2, 3, 0xFFFFFFFF], None)
    r = s.transaction(0x9999, [0])
    assert r.params == [1, 2, 3, 0xFFFFFFFF] and r.param(7) == 0
    assert struct.calcsize("<4I") == 16
