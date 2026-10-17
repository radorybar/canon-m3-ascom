"""CHDK PTP extension (operation 0x9999), protocol 2.x as defined in CHDK core/ptp.h."""

import logging
import struct
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from .transport import PtpError, PtpResponseError, PtpSession

log = logging.getLogger(__name__)

OC_CHDK = 0x9999


class Cmd(IntEnum):
    VERSION = 0
    GET_MEMORY = 1
    SET_MEMORY = 2
    CALL_FUNCTION = 3
    TEMP_DATA = 4
    UPLOAD_FILE = 5
    DOWNLOAD_FILE = 6
    EXECUTE_SCRIPT = 7
    SCRIPT_STATUS = 8
    SCRIPT_SUPPORT = 9
    READ_SCRIPT_MSG = 10
    WRITE_SCRIPT_MSG = 11
    GET_DISPLAY_DATA = 12
    REMOTE_CAPTURE_IS_READY = 13
    REMOTE_CAPTURE_GET_DATA = 14


class MsgType(IntEnum):
    NONE = 0
    ERROR = 1
    RETURN = 2
    USER = 3


class DataType(IntEnum):
    UNSUPPORTED = 0
    NIL = 1
    BOOLEAN = 2
    INTEGER = 3
    STRING = 4
    TABLE = 5


LANG_LUA = 0
FLAG_FLUSH_CAM_MSGS = 0x200
FLAG_FLUSH_HOST_MSGS = 0x400
STATUS_RUN = 0x1
STATUS_MSG = 0x2
ERR_COMPILE = 1

CAPTURE_JPG = 0x1
CAPTURE_RAW = 0x2
CAPTURE_DNGHDR = 0x4
CAPTURE_NOTSET = 0x10000000


class ChdkError(PtpError):
    pass


class ScriptError(ChdkError):
    pass


@dataclass
class Message:
    type: MsgType
    subtype: int
    script_id: int
    value: Any


def decode_value(subtype: int, data: bytes) -> Any:
    if subtype == DataType.NIL:
        return None
    if subtype == DataType.BOOLEAN:
        return struct.unpack_from("<i", data)[0] != 0
    if subtype == DataType.INTEGER:
        return struct.unpack_from("<i", data)[0]
    return data.decode("utf-8", errors="replace")  # string, table (serialized), unsupported (type name)


class ChdkClient:
    def __init__(self, session: PtpSession):
        self.s = session
        self.version: tuple[int, int] = (0, 0)

    def _cmd(self, cmd: Cmd, *params: int, data: bytes | None = None, timeout_ms: int = 5000):
        return self.s.transaction(OC_CHDK, [int(cmd), *params], data_out=data, timeout_ms=timeout_ms)

    def get_version(self) -> tuple[int, int]:
        try:
            r = self._cmd(Cmd.VERSION)
        except PtpResponseError as e:
            raise ChdkError("the camera does not answer CHDK commands - is CHDK loaded? "
                            f"(response 0x{e.code:04X})") from e
        self.version = (r.param(0), r.param(1))
        if self.version[0] != 2:
            raise ChdkError(f"unsupported CHDK PTP protocol {self.version[0]}.{self.version[1]} (need 2.x)")
        return self.version

    # -- scripts ---------------------------------------------------------------------------------

    def execute(self, script: str) -> int:
        flags = LANG_LUA
        if self.version >= (2, 6):
            flags |= FLAG_FLUSH_CAM_MSGS | FLAG_FLUSH_HOST_MSGS
        r = self._cmd(Cmd.EXECUTE_SCRIPT, flags, data=script.encode() + b"\x00")
        script_id, status = r.param(0), r.param(1)
        if status == ERR_COMPILE:
            msg = self.read_message()
            raise ScriptError(f"Lua compile error: {msg.value if msg.type == MsgType.ERROR else '?'}")
        return script_id

    def script_status(self) -> tuple[bool, bool]:
        flags = self._cmd(Cmd.SCRIPT_STATUS).param(0)
        return bool(flags & STATUS_RUN), bool(flags & STATUS_MSG)

    def read_message(self) -> Message:
        r = self._cmd(Cmd.READ_SCRIPT_MSG)
        mtype, subtype, sid, length = (r.param(i) for i in range(4))
        data = (r.data or b"")[:length]
        value = data.decode("utf-8", errors="replace") if mtype == MsgType.ERROR else decode_value(subtype, data)
        return Message(MsgType(mtype), subtype, sid, value)

    def write_message(self, text: str, script_id: int = 0) -> int:
        return self._cmd(Cmd.WRITE_SCRIPT_MSG, script_id, data=text.encode()).param(0)

    def wait_script(self, timeout_s: float, poll_s: float = 0.02) -> list[Any]:
        """Wait for the running script to end; return its return values, raise on script errors."""
        values: list[Any] = []
        deadline = time.monotonic() + timeout_s
        while True:
            running, has_msg = self.script_status()
            while has_msg:
                msg = self.read_message()
                if msg.type == MsgType.ERROR:
                    raise ScriptError(f"Lua error: {msg.value}")
                if msg.type in (MsgType.RETURN, MsgType.USER):
                    values.append(msg.value)
                running, has_msg = self.script_status()
            if not running:
                return values
            if time.monotonic() > deadline:
                raise ChdkError(f"script still running after {timeout_s:.0f}s")
            time.sleep(poll_s)

    def execwait(self, script: str, timeout_s: float = 10.0) -> list[Any]:
        self.execute(script)
        return self.wait_script(timeout_s)

    # -- remote capture --------------------------------------------------------------------------

    def capture_ready(self) -> tuple[int, int]:
        r = self._cmd(Cmd.REMOTE_CAPTURE_IS_READY)
        return r.param(0), r.param(1)

    def capture_get_data(self, fmt: int, timeout_ms: int = 60000) -> bytes:
        """Fetch all chunks of one capture format and assemble them (chunks may request seeks)."""
        buf = bytearray()
        pos = 0
        for _ in range(1000):
            r = self._cmd(Cmd.REMOTE_CAPTURE_GET_DATA, fmt, timeout_ms=timeout_ms)
            data = r.data or b""
            more, seek = r.param(1), r.param(2)
            if seek != 0xFFFFFFFF:
                pos = seek
            end = pos + len(data)
            if end > len(buf):
                buf.extend(b"\x00" * (end - len(buf)))
            buf[pos:end] = data
            pos = end
            if not more:
                return bytes(buf)
        raise ChdkError("remote capture returned too many chunks")
