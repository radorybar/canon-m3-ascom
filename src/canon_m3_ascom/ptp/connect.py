"""Choose how to reach the camera: WinUSB (libusb bulk endpoints) or Windows Portable Devices."""

import logging

from .transport import PtpError, PtpSession, UsbTransport

log = logging.getLogger(__name__)

TRANSPORTS = ("auto", "winusb", "wpd")


def open_winusb(pid: int = 0) -> PtpSession:
    return PtpSession(UsbTransport(pid))


def open_wpd() -> "PtpSession":
    from .wpd import ComWpdDevice, WpdSession
    return WpdSession(ComWpdDevice())


def open_session(transport: str = "auto", pid: int = 0):
    """Return an unopened PtpSession/WpdSession. `auto`: WinUSB if the camera has that driver, else WPD."""
    if transport == "winusb":
        return open_winusb(pid)
    if transport == "wpd":
        return open_wpd()
    if transport != "auto":
        raise PtpError(f"unknown transport {transport!r} (use {', '.join(TRANSPORTS)})")
    try:
        return open_winusb(pid)
    except PtpError as usb_error:
        log.debug("WinUSB not available (%s), trying Windows Portable Devices", usb_error)
        try:
            return open_wpd()
        except Exception as wpd_error:  # noqa: BLE001 - report both reasons
            raise PtpError(f"camera not reachable. WinUSB: {usb_error} | WPD: {wpd_error}") from wpd_error
