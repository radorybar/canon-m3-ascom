import struct

import numpy as np

from m3alpaca.alpaca import imagebytes


def test_header_and_order():
    img = np.arange(12, dtype=np.uint16).reshape(3, 4)  # height 3, width 4
    data = imagebytes.encode(img, client_tx=7, server_tx=9)
    fields = struct.unpack_from("<11i", data)
    assert fields == (1, 0, 7, 9, 44, imagebytes.INT32, imagebytes.UINT16, 2, 4, 3, 0)
    # [x, y] with y fastest: pixel (x=0,y=0), (x=0,y=1), (x=0,y=2), (x=1,y=0) ...
    px = np.frombuffer(data[44:], dtype="<u2")
    assert list(px[:4]) == [img[0, 0], img[1, 0], img[2, 0], img[0, 1]]


def test_roundtrip_int32():
    img = np.random.default_rng(0).integers(0, 16383, size=(10, 6), dtype=np.uint16)
    meta, out = imagebytes.decode(imagebytes.encode(img, 1, 2, transmit_uint16=False))
    assert meta["transmission_type"] == imagebytes.INT32
    assert np.array_equal(out, img)


def test_error():
    meta, msg = imagebytes.decode(imagebytes.encode_error(0x40B, "no image", 1, 2))
    assert meta["error"] == 0x40B and msg == "no image"
