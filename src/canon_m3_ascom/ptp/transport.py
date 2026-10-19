"""PTP over USB (PIMA 15740, USB Still Image class): containers, transactions, the USB endpoint layer.

Only what CHDK needs: command -> optional data phase (either direction) -> response.
"""

import logging
import struct
from dataclasses import dataclass, field
from typing import Protocol

log = logging.getLogger(__name__)

CANON_VID = 0x04A9

# container types
COMMAND = 1
DATA = 2
RESPONSE = 3
EVENT = 4

# operation / response codes
OC_GET_DEVICE_INFO = 0x1001
OC_OPEN_SESSION = 0x1002
OC_CLOSE_SESSION = 0x1003
RC_OK = 0x2001
RC_SESSION_ALREADY_OPEN = 0x201E

HEADER = struct.Struct("<IHHI")  # length, type, code, transaction id
READ_CHUNK = 1 << 20
MAX_STALE_CONTAINERS = 16

ZADIG_HINT = ("The camera does not have the WinUSB driver. Use transport \"wpd\" (standard driver), or install "
              "WinUSB with the canon-m3-ascom setup (option 'Install the WinUSB driver') or with Zadig.")


class PtpError(Exception):
    pass


class PtpTimeout(PtpError):
    pass


class PtpResponseError(PtpError):
    def __init__(self, op: int, code: int):
        super().__init__(f"PTP operation 0x{op:04X} failed with response 0x{code:04X}")
        self.op = op
        self.code = code


class Transport(Protocol):
    max_packet: int

    def write(self, data: bytes, timeout_ms: int) -> None: ...

    def read(self, size: int, timeout_ms: int) -> bytes: ...

    def close(self) -> None: ...


@dataclass
class Response:
    code: int
    params: list[int] = field(default_factory=list)
    data: bytes | None = None

    def param(self, i: int, default: int = 0) -> int:
        return self.params[i] if i < len(self.params) else default


class PtpSession:
    def __init__(self, transport: Transport):
        self.t = transport
        self._tid = 0

    def _next_tid(self) -> int:
        self._tid = (self._tid + 1) & 0xFFFFFFFF or 1
        return self._tid

    def _send(self, ctype: int, code: int, tid: int, payload: bytes, timeout_ms: int) -> None:
        packet = HEADER.pack(HEADER.size + len(payload), ctype, code, tid) + payload
        self.t.write(packet, timeout_ms)
        if len(packet) % self.t.max_packet == 0:
            self.t.write(b"", timeout_ms)  # zero-length packet terminates a full-packet transfer

    def _read_container(self, timeout_ms: int) -> tuple[int, int, int, bytes]:
        first = self.t.read(READ_CHUNK, timeout_ms)
        if len(first) < HEADER.size:
            raise PtpError(f"short PTP container ({len(first)} bytes)")
        length, ctype, code, tid = HEADER.unpack_from(first)
        chunks = [first]
        got = len(first)
        while got < length:
            want = min(length - got, READ_CHUNK)
            want += -want % self.t.max_packet  # ask in whole packets to avoid libusb overflow errors
            chunk = self.t.read(want, timeout_ms)
            if not chunk:
                raise PtpError(f"PTP data ended early ({got}/{length} bytes)")
            chunks.append(chunk)
            got += len(chunk)
        if length % self.t.max_packet == 0:
            try:  # the device terminates a full-packet transfer with a zero-length packet
                self.t.read(self.t.max_packet, 200)
            except PtpTimeout:
                pass
        data = b"".join(chunks)
        return ctype, code, tid, data[HEADER.size:length]

    def transaction(self, op: int, params=(), data_out: bytes | None = None, timeout_ms: int = 5000,
                    check: bool = True, tid: int | None = None, data_in: bool = False) -> Response:
        # data_in (the operation returns a data phase) matters only for WPD; on USB the device says so itself
        tid = self._next_tid() if tid is None else tid
        params = list(params)
        self._send(COMMAND, op, tid, struct.pack(f"<{len(params)}I", *params), timeout_ms)
        if data_out is not None:
            self._send(DATA, op, tid, data_out, timeout_ms)
        data_in = None
        stale = 0
        while True:
            ctype, code, rtid, payload = self._read_container(timeout_ms)
            if ctype in (DATA, RESPONSE) and rtid != tid:
                # left over from an earlier, aborted transaction: taking it as our answer would shift every
                # later response by one
                stale += 1
                log.warning("skipping stale PTP container (type %d, transaction %d, expected %d)", ctype, rtid, tid)
                if stale > MAX_STALE_CONTAINERS:
                    raise PtpError("PTP stream out of sync (too many stale containers)")
                continue
            if ctype == DATA:
                data_in = payload
                continue
            if ctype == RESPONSE:
                break
            log.debug("ignoring PTP container type %d code 0x%04X", ctype, code)
        resp = Response(code, list(struct.unpack(f"<{len(payload) // 4}I", payload[:len(payload) // 4 * 4])),
                        data_in)
        if check and code != RC_OK:
            raise PtpResponseError(op, code)
        return resp

    def drain(self) -> int:
        """Read and discard whatever is still waiting in the IN pipe (e.g. after an aborted transfer)."""
        dropped = 0
        for _ in range(256):  # bounded: at most 256 reads of up to 1 MB
            try:
                chunk = self.t.read(READ_CHUNK, 100)
            except PtpTimeout:
                break
            dropped += len(chunk)
        return dropped

    def open(self, session_id: int = 1) -> None:
        dropped = self.drain()
        if dropped:
            log.warning("discarded %d stale bytes from the camera before opening the session", dropped)
            reset = getattr(self.t, "reset", None)
            if reset:
                reset()
        self._tid = 0  # OpenSession uses transaction id 0, the first real transaction is 1
        resp = self.transaction(OC_OPEN_SESSION, [session_id], check=False, tid=0)
        if resp.code == RC_SESSION_ALREADY_OPEN:
            log.info("PTP session already open, reusing it")
        elif resp.code != RC_OK:
            raise PtpResponseError(OC_OPEN_SESSION, resp.code)

    def close(self) -> None:
        try:
            self.transaction(OC_CLOSE_SESSION, check=False, timeout_ms=2000)
        except Exception:  # noqa: BLE001 - the link may already be broken; closing must not fail
            pass
        try:
            self.t.close()
        except Exception as e:  # noqa: BLE001
            log.debug("closing transport: %s", e)

    def device_info(self) -> "DeviceInfo":
        return DeviceInfo.parse(self.transaction(OC_GET_DEVICE_INFO).data or b"")


@dataclass
class DeviceInfo:
    vendor_extension: str
    manufacturer: str
    model: str
    version: str
    serial: str
    operations: list[int]

    @staticmethod
    def parse(d: bytes) -> "DeviceInfo":
        pos = 0

        def u16():
            nonlocal pos
            v = struct.unpack_from("<H", d, pos)[0]
            pos += 2
            return v

        def u32():
            nonlocal pos
            v = struct.unpack_from("<I", d, pos)[0]
            pos += 4
            return v

        def string():
            nonlocal pos
            n = d[pos]
            pos += 1
            s = d[pos:pos + 2 * n].decode("utf-16-le").rstrip("\x00")
            pos += 2 * n
            return s

        def array16():
            return [u16() for _ in range(u32())]

        u16(), u32(), u16()  # standard version, vendor ext id, vendor ext version
        ext = string()
        u16()  # functional mode
        ops = array16()
        array16(), array16(), array16(), array16()  # events, properties, capture/image formats
        return DeviceInfo(ext, string(), string(), string(), string(), ops)

    def build(self) -> bytes:
        """Serialize (used by the emulated camera)."""
        def string(s: str) -> bytes:
            if not s:
                return b"\x00"
            enc = (s + "\x00").encode("utf-16-le")
            return bytes([len(enc) // 2]) + enc

        def array16(v: list[int]) -> bytes:
            return struct.pack(f"<I{len(v)}H", len(v), *v)

        return (struct.pack("<HIH", 100, 11, 100) + string(self.vendor_extension) + struct.pack("<H", 0)
                + array16(self.operations) + array16([]) * 4 + string(self.manufacturer) + string(self.model)
                + string(self.version) + string(self.serial))


class UsbTransport:
    """Bulk endpoints of the camera's Still Image interface, via pyusb + libusb-1.0 (WinUSB driver)."""

    def __init__(self, pid: int = 0):
        import libusb_package
        import usb.core
        import usb.util

        self._usb = usb
        try:
            backend = libusb_package.get_libusb1_backend()
            devices = list(usb.core.find(find_all=True, idVendor=CANON_VID, backend=backend))
        except (usb.core.USBError, usb.core.NoBackendError, OSError, ValueError) as e:
            raise PtpError(f"USB enumeration failed: {e}") from e
        if pid:
            devices = [d for d in devices if d.idProduct == pid]
        if not devices:
            raise PtpError("no Canon camera on USB (VID 04A9) - is it connected and switched on?")
        self.dev = dev = devices[0]
        self.pid = dev.idProduct
        try:
            cfg = dev.get_active_configuration()
            intf = usb.util.find_descriptor(cfg, bInterfaceClass=6) or cfg[(0, 0)]
            usb.util.claim_interface(dev, intf.bInterfaceNumber)
        except (usb.core.USBError, NotImplementedError) as e:
            raise PtpError(f"cannot open the camera over USB ({e}). {ZADIG_HINT}") from e
        self.interface = intf.bInterfaceNumber

        def endpoint(direction, ep_type):
            return usb.util.find_descriptor(
                intf, custom_match=lambda ep: usb.util.endpoint_direction(ep.bEndpointAddress) == direction
                and usb.util.endpoint_type(ep.bmAttributes) == ep_type)

        self.ep_out = endpoint(usb.util.ENDPOINT_OUT, usb.util.ENDPOINT_TYPE_BULK)
        self.ep_in = endpoint(usb.util.ENDPOINT_IN, usb.util.ENDPOINT_TYPE_BULK)
        if self.ep_out is None or self.ep_in is None:
            raise PtpError("camera USB interface has no bulk endpoints")
        self.max_packet = self.ep_in.wMaxPacketSize

    def write(self, data: bytes, timeout_ms: int) -> None:
        try:
            self.dev.write(self.ep_out, data, timeout_ms)
        except self._usb.core.USBTimeoutError as e:
            raise PtpTimeout("USB write timed out") from e
        except (self._usb.core.USBError, OSError, ValueError) as e:  # e.g. cable pulled, device gone
            raise PtpError(f"USB write failed: {e}") from e

    def read(self, size: int, timeout_ms: int) -> bytes:
        try:
            return bytes(self.dev.read(self.ep_in, size, timeout_ms))
        except self._usb.core.USBTimeoutError as e:
            raise PtpTimeout("USB read timed out") from e
        except (self._usb.core.USBError, OSError, ValueError) as e:
            raise PtpError(f"USB read failed: {e}") from e

    def reset(self) -> None:
        """PTP class 'Device Reset' and clear both bulk pipes (after stale data was found)."""
        for step in (lambda: self.dev.ctrl_transfer(0x21, 0x66, 0, self.interface, None, 2000),
                     lambda: self.dev.clear_halt(self.ep_in),
                     lambda: self.dev.clear_halt(self.ep_out)):
            try:
                step()
            except Exception as e:  # noqa: BLE001 - best effort, some cameras reject the class request
                log.debug("USB reset step failed: %s", e)

    def close(self) -> None:
        """Never raises: the device may already be gone."""
        for step in (lambda: self._usb.util.release_interface(self.dev, self.interface),
                     lambda: self._usb.util.dispose_resources(self.dev)):
            try:
                step()
            except Exception as e:  # noqa: BLE001
                log.debug("closing USB device: %s", e)
