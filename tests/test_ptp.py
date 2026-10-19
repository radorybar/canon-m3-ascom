"""PTP/CHDK stack against the USB-level camera emulator."""

import struct
import threading

import numpy as np
import pytest

from canon_m3_ascom.backends.base import BackendError
from canon_m3_ascom.backends.ptp import PtpBackend
from canon_m3_ascom.ptp import chdk, scripts
from canon_m3_ascom.ptp.fake import FakeCamera, FakeTransport
from canon_m3_ascom.ptp.transport import HEADER, PtpResponseError, PtpSession

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
    assert cam.scripts_run == [scripts.TAG_SETUP, scripts.TAG_CAPINIT, scripts.TAG_SHOOT]
    assert cam.capture_fmt == chdk.CAPTURE_RAW  # stays initialised between frames
    # a second frame on the same session needs no re-initialisation
    b.capture(1.0, 800, True, threading.Event())
    assert cam.shots == 2
    assert cam.scripts_run.count(scripts.TAG_CAPINIT) == 1
    b.disconnect()
    assert cam.scripts_run[-1] == scripts.TAG_CAPDONE and cam.capture_fmt == 0


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


def test_capture_timeout_drops_session_and_next_frame_recovers(tmp_path):
    cam = FakeCamera(time_scale=0)
    cfg = make_config("ptp", tmp_path)
    cfg.ptp.timeout_extra_s = -11  # deadline = 2*exposure - 1 s: already passed
    b = PtpBackend(cfg, transport_factory=lambda: FakeTransport(cam))
    b.connect()
    cam.raw_capture_stuck = True
    with pytest.raises(BackendError, match="no image"):
        b.capture(0.001, 800, True, threading.Event())
    assert b._chdk is None  # dropped, so the next frame reconnects and unlocks
    cam.raw_capture_stuck = False
    cfg.ptp.timeout_extra_s = 30
    assert b.capture(1.0, 800, True, threading.Event()).shape == (4000, 6000)
    assert cam.scripts_run.count(scripts.TAG_SETUP) == 2


def test_disconnect_during_exposure_returns_at_once(tmp_path):
    """The camera cannot abort an exposure; disconnecting must still answer immediately."""
    import time
    from canon_m3_ascom.camera import Camera
    cam = FakeCamera(time_scale=1.0)
    cfg = make_config("ptp", tmp_path)
    camera = Camera(PtpBackend(cfg, transport_factory=lambda: FakeTransport(cam)), cfg.camera)
    camera.set_connected(True)
    camera.start_exposure(2.0, True)
    time.sleep(0.3)
    t0 = time.monotonic()
    camera.set_connected(False)
    assert time.monotonic() - t0 < 0.5
    assert not camera.connected
    # reconnecting waits for the running exposure, then works normally
    camera.set_connected(True)
    camera.start_exposure(0.5, True)
    for _ in range(100):
        if camera.image_ready:
            break
        time.sleep(0.05)
    assert camera.image().shape == (4000, 6000)


def test_keepalive_refreshes_temperature_and_recovers(tmp_path):
    import time
    cam = FakeCamera(time_scale=0, temperature=20)
    cfg = make_config("ptp", tmp_path)
    cfg.ptp.keepalive_s = 0.1
    b = PtpBackend(cfg, transport_factory=lambda: FakeTransport(cam))
    b.connect()
    cam.temperature = 33
    for _ in range(50):
        if b.sensor_temperature() == 33:
            break
        time.sleep(0.05)
    assert b.sensor_temperature() == 33
    b._drop()  # simulate a lost link
    for _ in range(50):
        if b._chdk is not None:
            break
        time.sleep(0.05)
    assert b._chdk is not None  # reconnected by the keep-alive
    b.disconnect()
    setups = cam.scripts_run.count(scripts.TAG_SETUP)
    time.sleep(0.4)
    assert cam.scripts_run.count(scripts.TAG_SETUP) == setups  # stopped after disconnect


def test_gentle_polling_during_long_exposure(tmp_path):
    cam = FakeCamera(time_scale=1.0)
    calls = []
    original = cam.handle
    cam.handle = lambda op, params, data: (calls.append(params[0] if params else op), original(op, params, data))[1]
    cfg = make_config("ptp", tmp_path)
    cfg.ptp.keepalive_s = 0
    b = PtpBackend(cfg, transport_factory=lambda: FakeTransport(cam))
    b.connect()
    calls.clear()
    import time
    t0 = time.monotonic()
    b.capture(4.0, 800, True, threading.Event())
    duration = time.monotonic() - t0
    polls = calls.count(chdk.Cmd.REMOTE_CAPTURE_IS_READY)
    every_50ms = duration / 0.05  # the old behaviour: fast polling from the first moment
    assert polls < 0.7 * every_50ms, (polls, every_50ms)


def test_keep_raw_names_do_not_collide(tmp_path):
    cfg = make_config("ptp", tmp_path)
    cfg.ptp.keep_raw = True
    cfg.ptp.keepalive_s = 0
    b = PtpBackend(cfg, transport_factory=lambda: FakeTransport(FakeCamera(time_scale=0)))
    b.connect()
    for _ in range(3):
        b.capture(0.001, 800, True, threading.Event())
    assert len(list((tmp_path / "frames").glob("m3_*.raw"))) == 3


def test_failed_exposure_is_reported_the_way_nina_sees_it(tmp_path):
    """N.I.N.A. ignores errors from ImageReady and answers any ImageArray error except ValueNotSet with the
    previous image (saved again as a duplicate): a failed frame flips ImageReady and fails with ValueNotSet."""
    import time
    from fastapi.testclient import TestClient
    from canon_m3_ascom.alpaca import imagebytes
    from canon_m3_ascom.alpaca.server import create_app
    from canon_m3_ascom.camera import Camera
    cam = FakeCamera(time_scale=0)
    cfg = make_config("ptp", tmp_path)
    cfg.ptp.timeout_extra_s = -11  # deadline already passed: "no image from the camera"
    cfg.ptp.keepalive_s = 0
    camera = Camera(PtpBackend(cfg, transport_factory=lambda: FakeTransport(cam)), cfg.camera)
    client = TestClient(create_app(camera, cfg.server))
    base = "/api/v1/camera/0"
    client.put(f"{base}/connected", data={"Connected": "True"})
    cam.raw_capture_stuck = True
    assert client.put(f"{base}/startexposure", data={"Duration": "0.001", "Light": "True"}).json()["ErrorNumber"] == 0
    t0 = time.monotonic()
    while not client.get(f"{base}/imageready").json()["Value"]:
        assert time.monotonic() - t0 < 10, "ImageReady never became true for a failed exposure"
        time.sleep(0.05)
    assert client.get(f"{base}/imageready").json()["ErrorNumber"] == 0  # no exception for N.I.N.A. to swallow
    assert client.get(f"{base}/camerastate").json()["Value"] == 5  # cameraError
    r = client.get(f"{base}/imagearray", headers={"Accept": "application/imagebytes"})
    # the ASCOM client reads errors only from JSON, also when it asked for ImageBytes
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["ErrorNumber"] == 0x402 and "no image from the camera" in r.json()["ErrorMessage"]  # ValueNotSet
    assert "no image from the camera" in client.get(f"{base}/imagearray").json()["ErrorMessage"]
    # the next exposure works normally again
    cam.raw_capture_stuck = False
    cfg.ptp.timeout_extra_s = 30
    client.put(f"{base}/startexposure", data={"Duration": "0.5", "Light": "True"})
    while not client.get(f"{base}/imageready").json()["Value"]:
        time.sleep(0.05)
    meta, img = imagebytes.decode(client.get(f"{base}/imagearray", headers={"Accept": "application/imagebytes"}).content)
    assert meta["error"] == 0 and img.shape == (4000, 6000)


def test_setup_sends_unlock_by_default():
    assert "post_levent_to_ui(4482)" in scripts.setup(False, True, True, unlock=True)
    assert "if true and type(post_levent_to_ui)" in scripts.setup(False, True, True, unlock=True)
    assert "if false and type(post_levent_to_ui)" in scripts.setup(False, True, True, unlock=False)


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


def stale_response(tid: int) -> memoryview:
    return memoryview(HEADER.pack(HEADER.size + 8, 3, 0x2001, tid) + struct.pack("<2I", 7, 7))


def test_stale_container_mid_session_is_skipped():
    """Reproduces the stability review finding: an old response must not be taken as the new answer."""
    s, t = session_for(FakeCamera())
    c = chdk.ChdkClient(s)
    c.get_version()
    t._in.appendleft(stale_response(99))
    assert c.get_version() == (2, 9)
    assert c.get_version() == (2, 9)  # and the stream stays in sync afterwards


def test_stale_data_is_drained_on_open():
    cam = FakeCamera()
    t = FakeTransport(cam)
    t._in.append(stale_response(5))
    t._in.append(memoryview(b"\x00" * 1000))  # remainder of an aborted bulk transfer
    resets = []
    t.reset = lambda: resets.append(True)
    s = PtpSession(t)
    s.open()
    assert resets == [True]
    assert chdk.ChdkClient(s).get_version() == (2, 9)


def test_session_close_never_raises():
    s, t = session_for(FakeCamera())

    def broken(*_):
        raise OSError("device gone")

    t.write = broken
    t.close = broken
    s.close()  # must not raise


def test_response_params_parsing():
    s, t = session_for(FakeCamera())
    t.camera.handle = lambda op, params, data: (0x2001, [1, 2, 3, 0xFFFFFFFF], None)
    r = s.transaction(0x9999, [0])
    assert r.params == [1, 2, 3, 0xFFFFFFFF] and r.param(7) == 0
    assert struct.calcsize("<4I") == 16
