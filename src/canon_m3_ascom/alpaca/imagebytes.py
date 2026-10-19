"""Alpaca ImageBytes encoding (AlpacaImageBytes.pdf, metadata version 1).

Header: 11 little-endian int32 fields (44 bytes), then the array data. A rank-2 ASCOM array is
[x, y] with y varying fastest, i.e. the transposed (width, height) array in C order.
"""

import struct

import numpy as np

# ImageArrayElementTypes
INT32 = 2
UINT16 = 8

_HEADER = struct.Struct("<11i")
HEADER_SIZE = _HEADER.size  # 44
_TILE = 256


def encode(img: np.ndarray, client_tx: int, server_tx: int, transmit_uint16: bool = True) -> bytes:
    """img is (height, width); reported to the client as Int32 [x, y]."""
    h, w = img.shape
    tx_type = UINT16 if transmit_uint16 else INT32
    dtype = np.dtype("<u2" if transmit_uint16 else "<i4")
    # write the transposed pixels straight into the response buffer (one pass, no intermediate copies)
    buf = bytearray(HEADER_SIZE + w * h * dtype.itemsize)
    _HEADER.pack_into(buf, 0, 1, 0, client_tx, server_tx, HEADER_SIZE, INT32, tx_type, 2, w, h, 0)
    out = np.frombuffer(buf, dtype=dtype, offset=HEADER_SIZE).reshape(w, h)
    # transpose in tiles: a plain img.T copy walks memory column by column (~10x slower on 24 Mpx)
    for r in range(0, h, _TILE):
        for c in range(0, w, _TILE):
            out[c:c + _TILE, r:r + _TILE] = img[r:r + _TILE, c:c + _TILE].T
    return bytes(buf)


def encode_error(number: int, message: str, client_tx: int, server_tx: int) -> bytes:
    header = _HEADER.pack(1, number, client_tx, server_tx, HEADER_SIZE, 0, 0, 0, 0, 0, 0)
    return header + message.encode("utf-8")


def decode(data: bytes) -> tuple[dict, np.ndarray | str]:
    """Client-side decoder, used by tests and tools."""
    f = _HEADER.unpack_from(data)
    meta = dict(zip(("version", "error", "client_tx", "server_tx", "data_start", "element_type",
                     "transmission_type", "rank", "dim1", "dim2", "dim3"), f))
    body = data[meta["data_start"]:]
    if meta["error"]:
        return meta, body.decode("utf-8")
    dtype = {UINT16: "<u2", INT32: "<i4"}[meta["transmission_type"]]
    arr = np.frombuffer(body, dtype=dtype).reshape(meta["dim1"], meta["dim2"])  # [x, y]
    return meta, arr.T  # back to (height, width)
