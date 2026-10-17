"""Alpaca HTTP API: management endpoints, setup page and the Camera device (number 0)."""

import html
import itertools
import logging
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse

from .. import __version__
from ..camera import Camera
from . import imagebytes
from .errors import ACTION_NOT_IMPLEMENTED, INVALID_VALUE, AlpacaError, not_implemented

log = logging.getLogger(__name__)

INTERFACE_VERSION = 3
DEVICE_NAME = "Canon EOS M3 (CHDK)"


class BadRequest(Exception):
    """HTTP 400 (missing/malformed parameters), as opposed to an Alpaca error in a 200 response."""


class Params:
    """GET query keys are case-insensitive; PUT form keys must match exactly (Alpaca API spec)."""

    def __init__(self, values: dict[str, str], case_sensitive: bool):
        self.case_sensitive = case_sensitive
        self.values = values if case_sensitive else {k.lower(): v for k, v in values.items()}

    def raw(self, name: str) -> str:
        key = name if self.case_sensitive else name.lower()
        if key not in self.values:
            raise BadRequest(f"missing parameter {name}")
        return self.values[key]

    def get_int(self, name: str) -> int:
        try:
            return int(self.raw(name))
        except ValueError:
            raise BadRequest(f"parameter {name} must be an integer") from None

    def get_float(self, name: str) -> float:
        try:
            return float(self.raw(name))
        except ValueError:
            raise BadRequest(f"parameter {name} must be a number") from None

    def get_bool(self, name: str) -> bool:
        v = self.raw(name).lower()
        if v not in ("true", "false"):
            raise BadRequest(f"parameter {name} must be True or False")
        return v == "true"

    def transaction_id(self) -> int:
        try:
            v = int(self.raw("ClientTransactionID"))
            return v if 0 <= v <= 0xFFFFFFFF else 0
        except (BadRequest, ValueError):
            return 0


def _json_image(img, client_tx: int, server_tx: int):
    """JSON ImageArray ([x][y], Int32) streamed column by column; a 24 Mpx list would not fit in memory."""
    yield (f'{{"Type":2,"Rank":2,"ClientTransactionID":{client_tx},"ServerTransactionID":{server_tx},'
           f'"ErrorNumber":0,"ErrorMessage":"","Value":[')
    for i, col in enumerate(img.T):
        yield ("," if i else "") + "[" + ",".join(map(str, col.tolist())) + "]"
    yield "]}"


Getter = Callable[[], Any]
Setter = Callable[[Params], None]


def _ni(member: str) -> Callable:
    def fail(*_):
        raise not_implemented(member)
    return fail


def build_members(cam: Camera, cfg_server) -> dict[str, tuple[Getter | None, Setter | None]]:
    spec = cam.spec

    def connected_get():
        return cam.connected

    def connected_put(p: Params):
        cam.set_connected(p.get_bool("Connected"))

    def connected(fn: Getter) -> Getter:
        def wrapped():
            cam.require_connected()
            return fn()
        return wrapped

    def roi_setter(attr: str, minimum: int):
        def put(p: Params):
            name = {"startx": "StartX", "starty": "StartY", "numx": "NumX", "numy": "NumY"}[attr]
            v = p.get_int(name)
            if v < minimum:
                raise AlpacaError(INVALID_VALUE, f"{name} must be >= {minimum}")
            setattr(cam, attr, v)
        return put

    def bin_put(name: str):
        def put(p: Params):
            if p.get_int(name) != 1:
                raise AlpacaError(INVALID_VALUE, "only 1x1 binning is supported")
        return put

    def gain_put(p: Params):
        cam.set_gain(p.get_int("Gain"))

    def readout_put(p: Params):
        if p.get_int("ReadoutMode") != 0:
            raise AlpacaError(INVALID_VALUE, "only readout mode 0 exists")

    def start_exposure(p: Params):
        cam.start_exposure(p.get_float("Duration"), p.get_bool("Light"))

    def abort(p: Params):
        cam.abort_exposure()

    def action(p: Params):
        raise AlpacaError(ACTION_NOT_IMPLEMENTED, f"action {p.raw('Action')} is not supported")

    m: dict[str, tuple[Getter | None, Setter | None]] = {
        # common device members
        "connected": (connected_get, connected_put),
        "description": (lambda: cam.backend.description, None),
        "driverinfo": (lambda: f"m3alpaca {__version__}: Canon EOS M3 + CHDK via chdkptp", None),
        "driverversion": (lambda: __version__, None),
        "interfaceversion": (lambda: INTERFACE_VERSION, None),
        "name": (lambda: DEVICE_NAME, None),
        "supportedactions": (lambda: [], None),
        "action": (None, action),
        "commandblind": (None, _ni("CommandBlind")),
        "commandbool": (None, _ni("CommandBool")),
        "commandstring": (None, _ni("CommandString")),
        # geometry
        "cameraxsize": (connected(lambda: spec.width), None),
        "cameraysize": (connected(lambda: spec.height), None),
        "pixelsizex": (connected(lambda: spec.pixel_size_um), None),
        "pixelsizey": (connected(lambda: spec.pixel_size_um), None),
        "startx": (connected(lambda: cam.startx), roi_setter("startx", 0)),
        "starty": (connected(lambda: cam.starty), roi_setter("starty", 0)),
        "numx": (connected(lambda: cam.numx), roi_setter("numx", 1)),
        "numy": (connected(lambda: cam.numy), roi_setter("numy", 1)),
        "binx": (connected(lambda: 1), bin_put("BinX")),
        "biny": (connected(lambda: 1), bin_put("BinY")),
        "maxbinx": (connected(lambda: 1), None),
        "maxbiny": (connected(lambda: 1), None),
        "canasymmetricbin": (connected(lambda: False), None),
        # sensor
        "sensorname": (connected(lambda: "Canon EOS M3 APS-C CMOS"), None),
        "sensortype": (connected(lambda: cam.sensor_type), None),
        "bayeroffsetx": (connected(lambda: 0), None),
        "bayeroffsety": (connected(lambda: 0), None),
        "maxadu": (connected(lambda: spec.max_adu), None),
        "electronsperadu": (connected(lambda: cam.electrons_per_adu), None),
        "fullwellcapacity": (connected(lambda: cam.full_well), None),
        "hasshutter": (connected(lambda: False), None),
        # gain = ISO (list mode)
        "gains": (connected(lambda: cam.gains), None),
        "gain": (connected(lambda: cam.gain), gain_put),
        "gainmin": (_ni("GainMin"), None),
        "gainmax": (_ni("GainMax"), None),
        "offset": (_ni("Offset"), _ni("Offset")),
        "offsetmin": (_ni("OffsetMin"), None),
        "offsetmax": (_ni("OffsetMax"), None),
        "offsets": (_ni("Offsets"), None),
        # readout
        "readoutmodes": (connected(lambda: ["Normal"]), None),
        "readoutmode": (connected(lambda: 0), readout_put),
        "canfastreadout": (connected(lambda: False), None),
        "fastreadout": (_ni("FastReadout"), _ni("FastReadout")),
        # temperature / cooling
        "ccdtemperature": (connected(lambda: cam.ccd_temperature), None),
        "cansetccdtemperature": (connected(lambda: False), None),
        "setccdtemperature": (_ni("SetCCDTemperature"), _ni("SetCCDTemperature")),
        "cangetcoolerpower": (connected(lambda: False), None),
        "cooleron": (_ni("CoolerOn"), _ni("CoolerOn")),
        "coolerpower": (_ni("CoolerPower"), None),
        "heatsinktemperature": (_ni("HeatSinkTemperature"), None),
        # guiding
        "canpulseguide": (connected(lambda: False), None),
        "ispulseguiding": (_ni("IsPulseGuiding"), None),
        "pulseguide": (None, _ni("PulseGuide")),
        # exposure
        "exposuremin": (connected(lambda: cam.cfg.exposure_min), None),
        "exposuremax": (connected(lambda: cam.cfg.exposure_max), None),
        "exposureresolution": (connected(lambda: 0.0), None),
        "canabortexposure": (connected(lambda: cam.backend.can_abort), None),
        "canstopexposure": (connected(lambda: False), None),
        "camerastate": (connected(lambda: int(cam.state)), None),
        "percentcompleted": (connected(lambda: cam.percent_completed), None),
        "imageready": (connected(lambda: cam.image_ready), None),
        # served by the camera route itself (binary / streamed), listed here so they are routable
        "imagearray": (connected(cam.image), None),
        "imagearrayvariant": (connected(cam.image), None),
        "lastexposurestarttime": (connected(lambda: cam.last_exposure_start), None),
        "lastexposureduration": (connected(lambda: cam.last_exposure_duration), None),
        "subexposureduration": (_ni("SubExposureDuration"), _ni("SubExposureDuration")),
        "startexposure": (None, start_exposure),
        "abortexposure": (None, abort),
        "stopexposure": (None, _ni("StopExposure")),
    }
    return m


def create_app(cam: Camera, cfg_server) -> FastAPI:
    app = FastAPI(title="m3alpaca", docs_url=None, redoc_url=None, openapi_url=None)
    members = build_members(cam, cfg_server)
    server_tx = itertools.count(1)
    unique_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "m3alpaca/camera/0"))

    def reply(value: Any, client_tx: int, error: AlpacaError | None = None) -> JSONResponse:
        body: dict[str, Any] = {
            "ClientTransactionID": client_tx,
            "ServerTransactionID": next(server_tx),
            "ErrorNumber": error.number if error else 0,
            "ErrorMessage": error.message if error else "",
        }
        if value is not None and error is None:
            body["Value"] = value
        return JSONResponse(body)

    async def read_params(request: Request) -> Params:
        if request.method == "PUT":
            form = await request.form()
            return Params({k: str(v) for k, v in form.items()}, case_sensitive=True)
        return Params(dict(request.query_params), case_sensitive=False)

    # -- management API --------------------------------------------------------------------------

    @app.get("/management/apiversions")
    async def apiversions(request: Request):
        return reply([1], (await read_params(request)).transaction_id())

    @app.get("/management/v1/description")
    async def description(request: Request):
        return reply({"ServerName": "m3alpaca", "Manufacturer": "m3alpaca",
                      "ManufacturerVersion": __version__, "Location": "localhost"},
                     (await read_params(request)).transaction_id())

    @app.get("/management/v1/configureddevices")
    async def configureddevices(request: Request):
        return reply([{"DeviceName": DEVICE_NAME, "DeviceType": "Camera", "DeviceNumber": 0,
                       "UniqueID": unique_id}], (await read_params(request)).transaction_id())

    # -- setup / status page ---------------------------------------------------------------------

    @app.get("/setup", response_class=HTMLResponse)
    @app.get("/setup/v1/camera/0/setup", response_class=HTMLResponse)
    async def setup():
        rows = {
            "Backend": cam.backend.description,
            "Connected": cam.connected,
            "State": cam.state.name if cam.connected else "-",
            "ISO": cam.iso,
            "Sensor temperature": cam.backend.sensor_temperature(),
            "Exposure range": f"{cam.cfg.exposure_min:g} – {cam.cfg.exposure_max:g} s",
        }
        trs = "".join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(str(v))}</td></tr>" for k, v in rows.items())
        return f"""<!doctype html><html><head><meta charset="utf-8"><title>m3alpaca</title>
<style>body{{font-family:system-ui;margin:2rem}}th{{text-align:left;padding-right:1rem}}</style></head>
<body><h1>{html.escape(DEVICE_NAME)}</h1><table>{trs}</table>
<p>Configuration is in the TOML file passed with <code>--config</code>.</p></body></html>"""

    # -- camera device ---------------------------------------------------------------------------

    @app.api_route("/api/v1/camera/{device_number}/{member}", methods=["GET", "PUT"])
    async def camera(device_number: str, member: str, request: Request):
        if device_number != "0":
            return PlainTextResponse(f"device number {device_number} is not configured", status_code=400)
        member = member.lower()
        try:
            params = await read_params(request)
        except Exception:
            return PlainTextResponse("malformed request body", status_code=400)
        client_tx = params.transaction_id()
        entry = members.get(member)
        if entry is None:
            return PlainTextResponse(f"unknown member {member}", status_code=400)
        getter, setter = entry
        handler = getter if request.method == "GET" else setter
        if handler is None:
            return PlainTextResponse(f"{request.method} is not valid for {member}", status_code=400)

        wants_bytes = member == "imagearray" and "application/imagebytes" in request.headers.get("accept", "")
        try:
            if member in ("imagearray", "imagearrayvariant"):
                cam.require_connected()
                img = cam.image()
                if wants_bytes:
                    data = await run_in_threadpool(imagebytes.encode, img, client_tx, next(server_tx),
                                                   cfg_server.imagebytes_uint16)
                    return Response(data, media_type="application/imagebytes")
                return StreamingResponse(_json_image(img, client_tx, next(server_tx)),
                                         media_type="application/json")
            if request.method == "GET":
                return reply(await run_in_threadpool(handler), client_tx)
            await run_in_threadpool(handler, params)
            return reply(None, client_tx)
        except BadRequest as e:
            return PlainTextResponse(str(e), status_code=400)
        except AlpacaError as e:
            if wants_bytes:
                return Response(imagebytes.encode_error(e.number, e.message, client_tx, next(server_tx)),
                                media_type="application/imagebytes")
            return reply(None, client_tx, e)

    # legacy / fallback page for the Alpaca "setup" link on the root
    app.add_api_route("/", lambda: HTMLResponse('<a href="/setup">m3alpaca setup</a>'), methods=["GET"])
    return app
