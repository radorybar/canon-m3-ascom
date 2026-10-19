"""PTP vendor operations through Windows Portable Devices ("MTP extensions"): no driver change needed.

The camera keeps its standard Windows driver (WpdMtp). Vendor operations such as CHDK's 0x9999 are sent
with the WPD_COMMAND_MTP_EXT_* commands (WpdMtpExtensions.h). Windows owns the PTP session.

`WpdSession` offers the same methods as `PtpSession` (open, close, transaction, device_info), so the
CHDK client and the backend do not care which transport is used. The COM calls live in `ComWpdDevice`;
`WpdSession` only needs the small `WpdDevice` interface, which tests implement without COM.
"""

import ctypes
import logging
import threading
from typing import Protocol

from .transport import RC_OK, DeviceInfo, PtpError, PtpResponseError, Response

log = logging.getLogger(__name__)

CANON_VID_TAG = "vid_04a9"
DEFAULT_CHUNK = 1 << 20

# GUIDs and property ids from WpdMtpExtensions.h / PortableDevice.h (Windows SDK 10.0.22000)
_MTP_EXT = "{4D545058-1A2E-4106-A357-771E0819FC56}"
_COMMON = "{F0422A9C-5DC8-4440-B5BD-5DF28835658A}"
_DEVICE = "{26D4979A-E643-4626-9E2B-736DC0C92FDC}"
_CLIENT = "{204D9F0C-2292-4080-9F42-40664E70F859}"

CMD_GET_SUPPORTED_VENDOR_OPCODES = (_MTP_EXT, 11)
CMD_EXECUTE_WITHOUT_DATA = (_MTP_EXT, 12)
CMD_EXECUTE_WITH_DATA_TO_READ = (_MTP_EXT, 13)
CMD_EXECUTE_WITH_DATA_TO_WRITE = (_MTP_EXT, 14)
CMD_READ_DATA = (_MTP_EXT, 15)
CMD_WRITE_DATA = (_MTP_EXT, 16)
CMD_END_DATA_TRANSFER = (_MTP_EXT, 17)

P_OPERATION_CODE = (_MTP_EXT, 1001)
P_OPERATION_PARAMS = (_MTP_EXT, 1002)
P_RESPONSE_CODE = (_MTP_EXT, 1003)
P_RESPONSE_PARAMS = (_MTP_EXT, 1004)
P_VENDOR_OPERATION_CODES = (_MTP_EXT, 1005)
P_TRANSFER_CONTEXT = (_MTP_EXT, 1006)
P_TRANSFER_TOTAL_DATA_SIZE = (_MTP_EXT, 1007)
P_TRANSFER_NUM_BYTES_TO_READ = (_MTP_EXT, 1008)
P_TRANSFER_NUM_BYTES_READ = (_MTP_EXT, 1009)
P_TRANSFER_NUM_BYTES_TO_WRITE = (_MTP_EXT, 1010)
P_TRANSFER_DATA = (_MTP_EXT, 1012)
P_OPTIMAL_TRANSFER_BUFFER_SIZE = (_MTP_EXT, 1013)

P_COMMAND_CATEGORY = (_COMMON, 1001)
P_COMMAND_ID = (_COMMON, 1002)
P_HRESULT = (_COMMON, 1003)

P_DEVICE_FIRMWARE_VERSION = (_DEVICE, 3)
P_DEVICE_MANUFACTURER = (_DEVICE, 7)
P_DEVICE_MODEL = (_DEVICE, 8)
P_DEVICE_SERIAL_NUMBER = (_DEVICE, 9)

P_CLIENT_NAME = (_CLIENT, 2)
P_CLIENT_MAJOR_VERSION = (_CLIENT, 3)
P_CLIENT_MINOR_VERSION = (_CLIENT, 4)
P_CLIENT_REVISION = (_CLIENT, 5)
P_CLIENT_SECURITY_QOS = (_CLIENT, 8)
P_CLIENT_DESIRED_ACCESS = (_CLIENT, 9)

GENERIC_READ_WRITE = 0x80000000 | 0x40000000
SECURITY_IMPERSONATION = 0x00020000  # SECURITY_IMPERSONATION_LEVEL(SecurityImpersonation)
VT_UI4 = 19


class WpdError(PtpError):
    pass


class WpdDevice(Protocol):
    """What WpdSession needs from a WPD device (COM in production, a fake in tests)."""

    def vendor_opcodes(self) -> set[int]: ...

    def execute(self, op: int, params: list[int]) -> tuple[int, list[int]]: ...

    def execute_read(self, op: int, params: list[int]) -> tuple[int, list[int], bytes]: ...

    def execute_write(self, op: int, params: list[int], data: bytes) -> tuple[int, list[int]]: ...

    def properties(self) -> dict[str, str]: ...

    def close(self) -> None: ...


class WpdSession:
    """PtpSession-compatible facade over a WPD device."""

    def __init__(self, device: WpdDevice):
        self.device = device
        self._vendor_ops: set[int] | None = None
        self._warned = False

    def open(self, session_id: int = 1) -> None:  # Windows already holds the PTP session
        self._vendor_ops = self.device.vendor_opcodes()
        log.debug("WPD vendor operations: %s", ", ".join(f"0x{o:04X}" for o in sorted(self._vendor_ops)))

    def close(self) -> None:
        try:
            self.device.close()
        except Exception as e:  # noqa: BLE001 - closing must never fail
            log.debug("closing WPD device: %s", e)

    def transaction(self, op: int, params=(), data_out: bytes | None = None, timeout_ms: int = 5000,
                    check: bool = True, tid: int | None = None, data_in: bool = False) -> Response:
        # The list comes from the camera's DeviceInfo. CHDK does not add 0x9999 to it, but the WPD driver
        # still forwards the operation (verified on an EOS M3, Windows 11), so it is only informative.
        if self._vendor_ops is not None and op not in self._vendor_ops and not self._warned:
            log.debug("vendor operation 0x%04X is not advertised by the camera; sending it anyway", op)
            self._warned = True
        params = list(params)
        data = None
        if data_out is not None:
            code, rparams = self.device.execute_write(op, params, data_out)
        elif data_in:
            code, rparams, data = self.device.execute_read(op, params)
        else:
            code, rparams = self.device.execute(op, params)
        resp = Response(code, rparams, data)
        if check and code != RC_OK:
            raise PtpResponseError(op, code)
        return resp

    def device_info(self) -> DeviceInfo:
        p = self.device.properties()
        return DeviceInfo("", p.get("manufacturer", ""), p.get("model", ""), p.get("firmware", ""),
                          p.get("serial", ""), sorted(self._vendor_ops or []))


# -- COM implementation ---------------------------------------------------------------------------------


_com_thread = threading.local()


def _ensure_com() -> None:
    """Initialise COM for the calling thread (multithreaded apartment).

    The driver touches the camera from several threads (HTTP requests, exposures, keep-alive). In the
    MTA, COM objects created in one thread can be used from the others; each thread must still call
    CoInitializeEx once, otherwise every WPD call fails with CO_E_NOTINITIALIZED.
    """
    if getattr(_com_thread, "ready", False):
        return
    import sys
    if "comtypes" not in sys.modules:
        sys.coinit_flags = 0  # COINIT_MULTITHREADED for the importing thread as well
    import comtypes
    try:
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    except OSError as e:  # RPC_E_CHANGED_MODE: this thread already chose another apartment
        log.debug("CoInitializeEx: %s", e)
    _com_thread.ready = True


def _com():
    _ensure_com()
    import comtypes.client as cc
    cc.GetModule("portabledeviceapi.dll")
    cc.GetModule("portabledevicetypes.dll")
    from comtypes.gen import PortableDeviceApiLib as api
    from comtypes.gen import PortableDeviceTypesLib as types
    return cc, api, types


def list_canon_devices() -> list[str]:
    """PnP ids of Canon devices that Windows exposes through WPD (i.e. with the standard driver)."""
    cc, api, _ = _com()
    mgr = cc.CreateObject(api.PortableDeviceManager, interface=api.IPortableDeviceManager)
    mgr.RefreshDeviceList()
    _, count = mgr.GetDevices(ctypes.POINTER(ctypes.c_wchar_p)(), 0)
    if not count:
        return []
    ids = (ctypes.c_wchar_p * count)()
    mgr.GetDevices(ctypes.cast(ids, ctypes.POINTER(ctypes.c_wchar_p)), count)
    return [i for i in ids if i and CANON_VID_TAG in i.lower()]


class ComWpdDevice:
    def __init__(self, pnp_id: str | None = None):
        import comtypes
        from comtypes import GUID
        self._comtypes = comtypes
        self._GUID = GUID
        self.cc, self.api, self.types = _com()
        if pnp_id is None:
            found = list_canon_devices()
            if not found:
                raise WpdError("no Canon camera found through Windows Portable Devices "
                               "(is it on, and does it still have the standard Windows driver?)")
            pnp_id = found[0]
        self.pnp_id = pnp_id
        client = self._values()
        client.SetStringValue(self._key(P_CLIENT_NAME), "canon-m3-ascom")
        client.SetUnsignedIntegerValue(self._key(P_CLIENT_MAJOR_VERSION), 0)
        client.SetUnsignedIntegerValue(self._key(P_CLIENT_MINOR_VERSION), 1)
        client.SetUnsignedIntegerValue(self._key(P_CLIENT_REVISION), 0)
        client.SetUnsignedIntegerValue(self._key(P_CLIENT_SECURITY_QOS), SECURITY_IMPERSONATION)
        client.SetUnsignedIntegerValue(self._key(P_CLIENT_DESIRED_ACCESS), GENERIC_READ_WRITE)
        self.dev = self.cc.CreateObject(self.api.PortableDevice, interface=self.api.IPortableDevice)
        try:
            self.dev.Open(pnp_id, client)
        except self._comtypes.COMError as e:
            raise WpdError(f"cannot open the camera through WPD: {e}") from e

    # -- helpers --------------------------------------------------------------------------------

    def _key(self, k: tuple[str, int]):
        key = self.types._tagpropertykey()
        key.fmtid = self._GUID(k[0])
        key.pid = k[1]
        return key

    def _values(self):
        _ensure_com()
        return self.cc.CreateObject(self.types.PortableDeviceValues, interface=self.types.IPortableDeviceValues)

    def _command(self, cmd: tuple[str, int]):
        v = self._values()
        v.SetGuidValue(self._key(P_COMMAND_CATEGORY), self._GUID(cmd[0]))
        v.SetUnsignedIntegerValue(self._key(P_COMMAND_ID), cmd[1])
        return v

    def _params(self, params: list[int]):
        _ensure_com()
        coll = self.cc.CreateObject(self.types.PortableDevicePropVariantCollection,
                                    interface=self.types.IPortableDevicePropVariantCollection)
        for p in params:
            pv = self.types.tag_inner_PROPVARIANT()
            raw = (ctypes.c_ubyte * ctypes.sizeof(pv)).from_buffer(pv)
            ctypes.memset(raw, 0, ctypes.sizeof(pv))
            ctypes.c_ushort.from_buffer(pv, 0).value = VT_UI4
            ctypes.c_uint32.from_buffer(pv, 8).value = p & 0xFFFFFFFF
            coll.Add(ctypes.byref(pv))
        return coll

    def _uint_list(self, coll) -> list[int]:
        """VT_UI4 values of an IPortableDevicePropVariantCollection."""
        count = ctypes.c_ulong(0)
        coll.GetCount(ctypes.byref(count))  # the type library declares the count as an [in] pointer
        out = []
        for i in range(count.value):
            pv = self.types.tag_inner_PROPVARIANT()
            coll.GetAt(i, ctypes.byref(pv))
            out.append(ctypes.c_uint32.from_buffer(pv, 8).value)
        return out

    def _read_params(self, results) -> list[int]:
        return self._uint_list(results.GetIPortableDevicePropVariantCollectionValue(self._key(P_RESPONSE_PARAMS)))

    def _send(self, values):
        _ensure_com()
        try:
            # results come typed from PortableDeviceApiLib; use the PortableDeviceTypesLib interface so the
            # property keys (defined in both type libraries) match
            results = self.dev.SendCommand(0, values).QueryInterface(self.types.IPortableDeviceValues)
        except self._comtypes.COMError as e:
            raise WpdError(f"WPD command failed: {e}") from e
        hr = results.GetErrorValue(self._key(P_HRESULT))
        if hr and hr < 0:
            raise WpdError(f"WPD command failed with HRESULT 0x{hr & 0xFFFFFFFF:08X}")
        return results

    def _response(self, results) -> tuple[int, list[int]]:
        return results.GetUnsignedIntegerValue(self._key(P_RESPONSE_CODE)), self._read_params(results)

    def _operation(self, cmd, op: int, params: list[int]):
        v = self._command(cmd)
        v.SetUnsignedIntegerValue(self._key(P_OPERATION_CODE), op)
        v.SetIPortableDevicePropVariantCollectionValue(self._key(P_OPERATION_PARAMS), self._params(params))
        return v

    def _end(self, context: str) -> tuple[int, list[int]]:
        v = self._command(CMD_END_DATA_TRANSFER)
        v.SetStringValue(self._key(P_TRANSFER_CONTEXT), context)
        return self._response(self._send(v))

    # -- WpdDevice ------------------------------------------------------------------------------

    def vendor_opcodes(self) -> set[int]:
        _ensure_com()
        results = self._send(self._command(CMD_GET_SUPPORTED_VENDOR_OPCODES))
        return set(self._uint_list(
            results.GetIPortableDevicePropVariantCollectionValue(self._key(P_VENDOR_OPERATION_CODES))))

    def execute(self, op: int, params: list[int]) -> tuple[int, list[int]]:
        return self._response(self._send(self._operation(CMD_EXECUTE_WITHOUT_DATA, op, params)))

    def execute_read(self, op: int, params: list[int]) -> tuple[int, list[int], bytes]:
        results = self._send(self._operation(CMD_EXECUTE_WITH_DATA_TO_READ, op, params))
        context = results.GetStringValue(self._key(P_TRANSFER_CONTEXT))
        total = results.GetUnsignedLargeIntegerValue(self._key(P_TRANSFER_TOTAL_DATA_SIZE))
        try:
            chunk = results.GetUnsignedIntegerValue(self._key(P_OPTIMAL_TRANSFER_BUFFER_SIZE)) or DEFAULT_CHUNK
        except self._comtypes.COMError:
            chunk = DEFAULT_CHUNK
        chunk = max(chunk, DEFAULT_CHUNK)
        parts, got = [], 0
        try:
            while got < total:
                want = min(chunk, total - got)
                v = self._command(CMD_READ_DATA)
                v.SetStringValue(self._key(P_TRANSFER_CONTEXT), context)
                v.SetUnsignedIntegerValue(self._key(P_TRANSFER_NUM_BYTES_TO_READ), want)
                buf = (ctypes.c_ubyte * want)()
                v.SetBufferValue(self._key(P_TRANSFER_DATA), buf, want)
                r = self._send(v)
                n = r.GetUnsignedIntegerValue(self._key(P_TRANSFER_NUM_BYTES_READ))
                ptr, size = r.GetBufferValue(self._key(P_TRANSFER_DATA))
                try:
                    parts.append(ctypes.string_at(ptr, min(n, size)))
                finally:
                    ctypes.windll.ole32.CoTaskMemFree(ptr)
                if n == 0:
                    break
                got += n
        finally:
            code, rparams = self._end(context)
        if got < total:
            raise WpdError(f"WPD read ended early ({got}/{total} bytes)")
        return code, rparams, b"".join(parts)

    def execute_write(self, op: int, params: list[int], data: bytes) -> tuple[int, list[int]]:
        v = self._operation(CMD_EXECUTE_WITH_DATA_TO_WRITE, op, params)
        v.SetUnsignedLargeIntegerValue(self._key(P_TRANSFER_TOTAL_DATA_SIZE), len(data))
        context = self._send(v).GetStringValue(self._key(P_TRANSFER_CONTEXT))
        try:
            for pos in range(0, len(data), DEFAULT_CHUNK):
                piece = data[pos:pos + DEFAULT_CHUNK]
                w = self._command(CMD_WRITE_DATA)
                w.SetStringValue(self._key(P_TRANSFER_CONTEXT), context)
                w.SetUnsignedIntegerValue(self._key(P_TRANSFER_NUM_BYTES_TO_WRITE), len(piece))
                buf = (ctypes.c_ubyte * len(piece)).from_buffer_copy(piece)
                w.SetBufferValue(self._key(P_TRANSFER_DATA), buf, len(piece))
                self._send(w)
        finally:
            code, rparams = self._end(context)
        return code, rparams

    def properties(self) -> dict[str, str]:
        _ensure_com()
        props = self.dev.Content().Properties()
        keys = self.cc.CreateObject(self.types.PortableDeviceKeyCollection,
                                    interface=self.types.IPortableDeviceKeyCollection)
        wanted = {"manufacturer": P_DEVICE_MANUFACTURER, "model": P_DEVICE_MODEL,
                  "firmware": P_DEVICE_FIRMWARE_VERSION, "serial": P_DEVICE_SERIAL_NUMBER}
        for k in wanted.values():
            keys.Add(self._key(k))
        values = props.GetValues("DEVICE", keys).QueryInterface(self.types.IPortableDeviceValues)
        out = {}
        for name, k in wanted.items():
            try:
                out[name] = values.GetStringValue(self._key(k))
            except self._comtypes.COMError:
                pass
        return out

    def close(self) -> None:
        _ensure_com()
        try:
            self.dev.Close()
        except self._comtypes.COMError:
            pass
