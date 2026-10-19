"""Alpaca API end to end, against both the simulator and the real CHDK backend driving fake chdkptp."""

import time

import numpy as np

from canon_m3_ascom.alpaca import imagebytes

BASE = "/api/v1/camera/0"


def get(c, member, **params):
    r = c.get(f"{BASE}/{member}", params={"ClientTransactionID": 5, **params})
    assert r.status_code == 200, r.text
    return r.json()


def put(c, member, **data):
    r = c.put(f"{BASE}/{member}", data={"ClientID": 1, "ClientTransactionID": 6, **data})
    assert r.status_code == 200, r.text
    return r.json()


def connect(c):
    assert put(c, "connected", Connected="True")["ErrorNumber"] == 0
    assert get(c, "connected")["Value"] is True


def wait_ready(c, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = get(c, "imageready")
        assert r["ErrorNumber"] == 0, r
        if r["Value"]:
            return
        time.sleep(0.05)
    raise AssertionError("image never became ready")


def test_management(client):
    assert client.get("/management/apiversions").json()["Value"] == [1]
    devs = client.get("/management/v1/configureddevices").json()["Value"]
    assert devs[0]["DeviceType"] == "Camera" and devs[0]["DeviceNumber"] == 0
    assert client.get("/setup").status_code == 200


def test_not_connected(client):
    r = get(client, "cameraxsize")
    assert r["ErrorNumber"] == 0x407
    assert get(client, "interfaceversion")["Value"] == 3


def test_protocol_errors(client):
    assert client.get("/api/v1/camera/1/connected").status_code == 400
    assert client.get(f"{BASE}/nosuchmember").status_code == 400
    # PUT parameter names are case sensitive
    assert client.put(f"{BASE}/connected", data={"connected": "True"}).status_code == 400
    assert client.put(f"{BASE}/connected", data={"Connected": "maybe"}).status_code == 400
    # GET parameter names are not
    assert client.get(f"{BASE}/connected", params={"clienttransactionid": 42}).json()["ClientTransactionID"] == 42


def test_properties(client):
    connect(client)
    assert get(client, "cameraxsize")["Value"] == 6000
    assert get(client, "cameraysize")["Value"] == 4000
    assert get(client, "sensortype")["Value"] == 2  # RGGB
    assert get(client, "maxadu")["Value"] == 16383
    # gain is the ISO value (min/max mode), the list mode is not offered
    assert get(client, "gains")["ErrorNumber"] == 0x400
    assert get(client, "gainmin")["Value"] == 100
    assert get(client, "gainmax")["Value"] == 12800
    assert get(client, "gain")["Value"] == 800  # default_iso
    assert put(client, "gain", Gain="50")["ErrorNumber"] == 0x401
    assert put(client, "gain", Gain="25600")["ErrorNumber"] == 0x401
    assert put(client, "gain", Gain="1000")["ErrorNumber"] == 0  # in-between ISO values are fine
    assert get(client, "gain")["Value"] == 1000
    assert get(client, "ccdtemperature")["ErrorNumber"] == 0
    assert get(client, "camerastate")["Value"] == 0


def test_exposure_imagebytes(client):
    connect(client)
    assert put(client, "startexposure", Duration="5", Light="True")["ErrorNumber"] == 0
    wait_ready(client)
    assert get(client, "camerastate")["Value"] == 0
    assert get(client, "lastexposureduration")["Value"] == 5
    r = client.get(f"{BASE}/imagearray", headers={"Accept": "application/imagebytes"})
    assert r.headers["content-type"] == "application/imagebytes"
    meta, img = imagebytes.decode(r.content)
    assert meta["error"] == 0 and img.shape == (4000, 6000)
    # sky background sits a little above the black level, stars well above it
    assert 2047 <= np.median(img) < 3000
    assert img.max() > 4000


def test_subframe_and_json(client):
    connect(client)
    for k, v in {"StartX": 100, "StartY": 200, "NumX": 40, "NumY": 30}.items():
        assert put(client, k.lower(), **{k: str(v)})["ErrorNumber"] == 0
    put(client, "startexposure", Duration="0.1", Light="True")
    wait_ready(client)
    r = client.get(f"{BASE}/imagearray").json()
    assert r["Rank"] == 2 and r["Type"] == 2
    assert len(r["Value"]) == 40 and len(r["Value"][0]) == 30


def test_invalid_exposures(client):
    connect(client)
    assert put(client, "startexposure", Duration="-1", Light="True")["ErrorNumber"] == 0x401
    assert put(client, "startexposure", Duration="100000", Light="True")["ErrorNumber"] == 0x401
    put(client, "numx", NumX="7000")
    assert put(client, "startexposure", Duration="1", Light="True")["ErrorNumber"] == 0x401
    assert get(client, "imagearray")["ErrorNumber"] == 0x40B


def test_unexpected_errors_become_alpaca_errors(client, monkeypatch):
    connect(client)
    from canon_m3_ascom.camera import Camera

    def boom(self):
        raise RuntimeError("libusb exploded")

    monkeypatch.setattr(Camera, "ccd_temperature", property(boom))
    r = client.get(f"{BASE}/ccdtemperature")
    assert r.status_code == 200
    assert r.json()["ErrorNumber"] == 0x500 and "libusb exploded" in r.json()["ErrorMessage"]
    assert get(client, "cameraxsize")["Value"] == 6000  # the server keeps working


def test_abort(client):
    connect(client)
    if client.backend_name in ("chdk", "ptp"):
        assert get(client, "canabortexposure")["Value"] is False
        assert put(client, "abortexposure")["ErrorNumber"] == 0x400
        return
    put(client, "startexposure", Duration="30", Light="True")
    assert get(client, "camerastate")["Value"] == 2
    assert put(client, "abortexposure")["ErrorNumber"] == 0
    time.sleep(0.2)
    assert get(client, "camerastate")["Value"] == 0
    assert get(client, "imageready")["Value"] is False
