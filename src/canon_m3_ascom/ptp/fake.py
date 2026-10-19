"""Emulated CHDK camera at the USB/PTP level.

FakeTransport speaks the same bulk-endpoint byte stream as a real camera (containers, data phases,
zero-length packets, reads that stop at container boundaries), so the whole PTP/CHDK stack and the
backend run unmodified. Scripts are recognised by their tag line (ptp/scripts.py).
"""

import re
import struct
import threading
import time
from collections import deque

from .. import focuser, rawcodec, starfield
from ..sensor import EOS_M3
from . import chdk, scripts
from .transport import (COMMAND, DATA, HEADER, OC_CLOSE_SESSION, OC_GET_DEVICE_INFO, OC_OPEN_SESSION, RC_OK,
                        RESPONSE, DeviceInfo, PtpTimeout)

RC_OPERATION_NOT_SUPPORTED = 0x2005
RC_GENERAL_ERROR = 0x2002


class FakeCamera:
    def __init__(self, chdk_loaded: bool = True, raw_capture: bool = True, temperature: int = 21,
                 time_scale: float = 1.0, download_s: float = 0.0, focuser_url: str = ""):
        self.chdk_loaded = chdk_loaded
        self.raw_capture = raw_capture
        self.temperature = temperature
        self.time_scale = time_scale
        self.download_s = download_s
        self.focuser_url = focuser_url
        self.rec_mode = False
        self.session_open = False
        self.capture_fmt = 0
        self.lock = threading.Lock()
        self.messages: deque[tuple[int, int, bytes]] = deque()  # (type, subtype, data)
        self.script_running = False
        self.script_id = 0
        self.generation = 0
        self.ready_raw: bytes | None = None
        self.shots = 0
        self.scripts_run: list[str] = []
        self.raw_capture_stuck = False

    # -- script engine (pattern based) -------------------------------------------------------------

    def _ret(self, text: str) -> None:
        self.messages.append((chdk.MsgType.RETURN, chdk.DataType.STRING, text.encode()))

    def run_script(self, src: str) -> tuple[int, int]:
        self.script_id += 1
        # like CHDK without PTP_CHDK_SCRIPT_FL_NOKILL: a new script kills the running one
        self.script_running = False
        self.ready_raw = None
        self.generation += 1
        self.messages.clear()
        tag = src.split("\n", 1)[0].strip()
        self.scripts_run.append(tag)
        if tag == scripts.TAG_SETUP:
            self.rec_mode = True
            self._ret(f"ok|{self.temperature}|{chdk.CAPTURE_RAW | chdk.CAPTURE_DNGHDR if self.raw_capture else 0}")
        elif tag == scripts.TAG_TEMP:
            self._ret(f"ok|{self.temperature}")
        elif tag == scripts.TAG_INFO:
            support = chdk.CAPTURE_RAW | chdk.CAPTURE_DNGHDR if self.raw_capture else 0
            self._ret(f"ok|1.6.1-6315|m3-121a|{self.temperature}|{support}|{'record' if self.rec_mode else 'playback'}")
        elif tag == scripts.TAG_CAPINIT:
            if not self.rec_mode:
                self._ret("err|camera is not in record mode")
            elif not self.raw_capture:
                self._ret("err|raw remote capture not supported")
            else:
                self.capture_fmt = int(re.search(r"init_usb_capture\((\d+)", src).group(1))
                self._ret("ok")
        elif tag == scripts.TAG_CAPDONE:
            self.capture_fmt = 0
            self._ret("ok")
        elif tag == scripts.TAG_SHOOT:
            tv = int(re.search(r"set_tv96_direct\((-?\d+)\)", src).group(1))
            sv = int(re.search(r"sv96_market_to_real\((-?\d+)\)", src).group(1))
            self.script_running = True
            threading.Thread(target=self._shoot, args=(2 ** (-tv / 96), round(3.125 * 2 ** (sv / 96)),
                                                       self.generation), daemon=True).start()
        elif src.strip().startswith("return"):
            self._ret("ok")
        else:  # like CHDK on a syntax error: status in the response, text as an error message
            self.messages.append((chdk.MsgType.ERROR, chdk.ERR_COMPILE, b"fake camera: unknown script"))
            return self.script_id, chdk.ERR_COMPILE
        return self.script_id, 0

    def _shoot(self, exposure_s: float, iso: int, generation: int) -> None:
        time.sleep(exposure_s * self.time_scale)
        if self.raw_capture_stuck:  # like a camera in the "connected to PC" state: shutter fires, no data
            if generation == self.generation:
                self._finish_shot()
            return
        if self.capture_fmt & chdk.CAPTURE_RAW:
            focus = focuser.read_position(self.focuser_url)
            img = starfield.render(EOS_M3, exposure_s, iso, True, focus)
            with self.lock:
                if generation != self.generation:  # script killed meanwhile
                    return
                self.ready_raw = rawcodec.encode_chdk_raw(img, EOS_M3)
        if generation == self.generation:
            # like the real M3: the shot script ends while the raw data still waits for the host
            self._finish_shot()

    def _finish_shot(self) -> None:
        with self.lock:
            self.shots += 1
            self._ret(f"ok|{self.temperature}")
            self.script_running = False

    # -- PTP operations ----------------------------------------------------------------------------

    def handle(self, op: int, params: list[int], data: bytes | None) -> tuple[int, list[int], bytes | None]:
        if op == OC_OPEN_SESSION:
            self.session_open = True
            return RC_OK, [], None
        if op == OC_CLOSE_SESSION:
            self.session_open = False
            return RC_OK, [], None
        if op == OC_GET_DEVICE_INFO:
            info = DeviceInfo("Canon PTP Extensions (emulated)", "Canon Inc.", "Canon EOS M3", "3-1.2.1",
                              "FAKE0001", [0x1001, 0x1002, 0x1003, chdk.OC_CHDK])
            return RC_OK, [], info.build()
        if op != chdk.OC_CHDK or not self.chdk_loaded:
            return RC_OPERATION_NOT_SUPPORTED, [], None
        cmd = params[0]
        with self.lock:
            if cmd == chdk.Cmd.VERSION:
                return RC_OK, [2, 9], None
            if cmd == chdk.Cmd.EXECUTE_SCRIPT:
                src = (data or b"").rstrip(b"\x00").decode()
                return RC_OK, list(self.run_script(src)), None
            if cmd == chdk.Cmd.SCRIPT_STATUS:
                return RC_OK, [(chdk.STATUS_RUN if self.script_running else 0)
                               | (chdk.STATUS_MSG if self.messages else 0)], None
            if cmd == chdk.Cmd.READ_SCRIPT_MSG:
                if not self.messages:
                    return RC_OK, [chdk.MsgType.NONE, 0, 0, 0], b"\x00"
                mtype, sub, payload = self.messages.popleft()
                return RC_OK, [mtype, sub, self.script_id, len(payload)], payload or b"\x00"
            if cmd == chdk.Cmd.REMOTE_CAPTURE_IS_READY:
                if not self.capture_fmt:
                    return RC_OK, [chdk.CAPTURE_NOTSET, 0], None
                return RC_OK, [chdk.CAPTURE_RAW if self.ready_raw else 0, self.shots + 1], None
            if cmd == chdk.Cmd.REMOTE_CAPTURE_GET_DATA:
                if params[1] != chdk.CAPTURE_RAW or self.ready_raw is None:
                    return RC_GENERAL_ERROR, [], None
                raw, self.ready_raw = self.ready_raw, None
        time.sleep(self.download_s)
        return RC_OK, [len(raw), 0, 0xFFFFFFFF], raw


class FakeTransport:
    max_packet = 512

    def __init__(self, camera: FakeCamera | None = None):
        self.camera = camera or FakeCamera()
        self._out = bytearray()
        self._pending: tuple[int, int, list[int]] | None = None  # command waiting for its data phase
        self._in: deque[memoryview] = deque()  # one entry per container (or zero-length packet)

    def _queue(self, ctype: int, code: int, tid: int, payload: bytes) -> None:
        packet = HEADER.pack(HEADER.size + len(payload), ctype, code, tid) + payload
        self._in.append(memoryview(packet))
        if len(packet) % self.max_packet == 0:
            self._in.append(memoryview(b""))

    def _respond(self, op: int, tid: int, params: list[int], data: bytes | None) -> None:
        code, rparams, rdata = self.camera.handle(op, params, data)
        if rdata is not None:
            self._queue(DATA, op, tid, rdata)
        self._queue(RESPONSE, code, tid, struct.pack(f"<{len(rparams)}I", *rparams))

    def write(self, data: bytes, timeout_ms: int) -> None:
        if not data:
            return  # zero-length packet
        self._out += data
        while len(self._out) >= HEADER.size:
            length, ctype, code, tid = HEADER.unpack_from(self._out)
            if len(self._out) < length:
                return
            payload = bytes(self._out[HEADER.size:length])
            del self._out[:length]
            if ctype == COMMAND:
                params = list(struct.unpack(f"<{len(payload) // 4}I", payload))
                if code == chdk.OC_CHDK and params and params[0] in (chdk.Cmd.EXECUTE_SCRIPT,
                                                                      chdk.Cmd.WRITE_SCRIPT_MSG):
                    self._pending = (code, tid, params)
                else:
                    self._respond(code, tid, params, None)
            elif ctype == DATA and self._pending:
                op, ptid, params = self._pending
                self._pending = None
                self._respond(op, ptid, params, payload)

    def read(self, size: int, timeout_ms: int) -> bytes:
        if not self._in:
            raise PtpTimeout("emulated camera: nothing to read")
        head = self._in[0]
        chunk, rest = head[:size], head[size:]
        if len(rest):
            self._in[0] = rest
        else:
            self._in.popleft()
        return bytes(chunk)

    def close(self) -> None:
        pass
