"""WPD transport (standard Windows driver) and transport selection, without COM or a camera."""

import sys
import threading

import pytest

from canon_m3_ascom import detect
from canon_m3_ascom.backends.base import BackendError
from canon_m3_ascom.backends.ptp import PtpBackend
from canon_m3_ascom.ptp import chdk, connect
from canon_m3_ascom.ptp.fake import FakeCamera
from canon_m3_ascom.ptp.transport import PtpError
from canon_m3_ascom.ptp.wpd import WpdError, WpdSession

from .conftest import make_config


class FakeWpdDevice:
    """WpdDevice on top of the emulated camera: one call per PTP operation, like WPD."""

    def __init__(self, camera: FakeCamera, vendor_ops=(chdk.OC_CHDK,)):
        self.camera = camera
        self.ops = set(vendor_ops)
        self.calls: list[tuple[str, int]] = []
        self.closed = False

    def vendor_opcodes(self):
        return set(self.ops)

    def execute(self, op, params):
        self.calls.append(("none", params[0]))
        code, rparams, data = self.camera.handle(op, params, None)
        assert data is None, f"command {params[0]} has a data phase but was sent without one"
        return code, rparams

    def execute_read(self, op, params):
        self.calls.append(("read", params[0]))
        code, rparams, data = self.camera.handle(op, params, None)
        return code, rparams, data or b""

    def execute_write(self, op, params, data):
        self.calls.append(("write", params[0]))
        code, rparams, _ = self.camera.handle(op, params, data)
        return code, rparams

    def properties(self):
        return {"manufacturer": "Canon Inc.", "model": "Canon EOS M3", "firmware": "3-14.0.1.0", "serial": "X"}

    def close(self):
        self.closed = True


def wpd_backend(tmp_path, device) -> PtpBackend:
    return PtpBackend(make_config("ptp", tmp_path), transport_factory=lambda: WpdSession(device))


def test_capture_over_wpd(tmp_path):
    dev = FakeWpdDevice(FakeCamera(time_scale=0))
    b = wpd_backend(tmp_path, dev)
    b.connect()
    assert b.info["transport"] == "WPD" and b.info["model"] == "Canon EOS M3"
    img = b.capture(1.0, 800, True, threading.Event())
    assert img.shape == (4000, 6000)
    kinds = {cmd: kind for kind, cmd in dev.calls}
    assert kinds[chdk.Cmd.EXECUTE_SCRIPT] == "write"
    assert kinds[chdk.Cmd.READ_SCRIPT_MSG] == "read"
    assert kinds[chdk.Cmd.REMOTE_CAPTURE_GET_DATA] == "read"
    assert kinds[chdk.Cmd.VERSION] == "none"
    b.disconnect()
    assert dev.closed


def test_wpd_unlisted_vendor_op_is_sent(tmp_path):
    """Like the real EOS M3: 0x9999 is not in the camera's operation list, Windows forwards it anyway."""
    dev = FakeWpdDevice(FakeCamera(time_scale=0), vendor_ops=(0x9001, 0x9002))
    b = wpd_backend(tmp_path, dev)
    b.connect()
    assert b.info["chdk_protocol"] == "2.9"
    assert chdk.OC_CHDK not in b._session.device_info().operations


def test_wpd_device_errors_become_backend_errors(tmp_path):
    class Broken(FakeWpdDevice):
        def execute(self, op, params):
            raise WpdError("WPD command failed with HRESULT 0x80070005")

    with pytest.raises(BackendError, match="0x80070005"):
        wpd_backend(tmp_path, Broken(FakeCamera())).connect()


def test_auto_prefers_winusb_then_falls_back_to_wpd(monkeypatch):
    sentinel = object()

    def no_usb(pid=0):
        raise PtpError("cannot open the camera over USB")

    monkeypatch.setattr(connect, "open_winusb", no_usb)
    monkeypatch.setattr(connect, "open_wpd", lambda: sentinel)
    assert connect.open_session("auto") is sentinel
    monkeypatch.setattr(connect, "open_winusb", lambda pid=0: "usb")
    assert connect.open_session("auto") == "usb"


def test_auto_reports_both_failures(monkeypatch):
    monkeypatch.setattr(connect, "open_winusb", lambda pid=0: (_ for _ in ()).throw(PtpError("usb says no")))
    monkeypatch.setattr(connect, "open_wpd", lambda: (_ for _ in ()).throw(WpdError("wpd says no")))
    with pytest.raises(PtpError, match="usb says no.*wpd says no"):
        connect.open_session("auto")


def test_detect_recommends_working_transport(monkeypatch):
    cam = FakeCamera()
    monkeypatch.setattr(detect, "_usb_devices", lambda: ["04A9:3299"])
    monkeypatch.setattr(connect, "open_winusb", lambda pid=0: (_ for _ in ()).throw(PtpError("no WinUSB")))
    monkeypatch.setattr(connect, "open_wpd", lambda: WpdSession(FakeWpdDevice(cam)))
    import canon_m3_ascom.ptp.wpd as wpd
    monkeypatch.setattr(wpd, "list_canon_devices", lambda: ["\\\\?\\usb#vid_04a9&pid_3299#x"])
    r = detect.detect()
    assert r["driver"] == "standard" and r["recommended"] == "wpd"
    assert r["wpd"]["ok"] and r["wpd"]["chdk_protocol"] == "2.9"
    assert not r["winusb"]["ok"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Portable Devices only exist on Windows")
def test_com_enumeration_smoke():
    from canon_m3_ascom.ptp.wpd import list_canon_devices
    assert isinstance(list_canon_devices(), list)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Portable Devices only exist on Windows")
def test_com_works_from_worker_threads():
    """Exposure and keep-alive threads must be able to use WPD (CoInitialize per thread)."""
    from canon_m3_ascom.ptp.wpd import list_canon_devices
    errors = []

    def worker():
        try:
            list_canon_devices()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert errors == []
