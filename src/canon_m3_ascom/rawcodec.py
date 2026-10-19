"""CHDK 14-bit raw buffer packing.

CHDK's raw framebuffer (what `chdkptp rs -raw` saves) is a MSB-first 14-bit packed bit stream
whose bytes are swapped within each 16-bit word (little-endian words). CHDK's DNG writer swaps
the bytes back, so the DNG strip is the plain MSB-first packed stream.

Derived from CHDK core/raw.c get_raw_pixel() for CAM_SENSOR_BITS_PER_PIXEL == 14.
TODO(spike): verify against a real M3 .raw / .dng pair.
"""

import numpy as np

from .sensor import SensorSpec


def _swap_words(buf: np.ndarray) -> np.ndarray:
    return buf.reshape(-1, 2)[:, ::-1].reshape(-1)


def unpack14(data: bytes | np.ndarray, width: int, height: int, word_swapped: bool) -> np.ndarray:
    """Unpack a 14-bit packed buffer into a uint16 (height, width) array."""
    if width % 4:
        raise ValueError("width must be a multiple of 4")
    expected = width * height * 14 // 8
    buf = np.frombuffer(data, dtype=np.uint8) if isinstance(data, (bytes, bytearray, memoryview)) else data
    if buf.size < expected:
        raise ValueError(f"raw buffer too small: {buf.size} < {expected} bytes")
    buf = buf[:expected]
    if word_swapped:
        buf = _swap_words(buf)
    # 4 pixels = 7 bytes = 56 bits, MSB first
    g = buf.reshape(-1, 7).astype(np.uint64)
    v = np.zeros(g.shape[0], dtype=np.uint64)
    for i in range(7):
        v = (v << np.uint64(8)) | g[:, i]
    out = np.empty((g.shape[0], 4), dtype=np.uint16)
    mask = np.uint64(0x3FFF)
    for i in range(4):
        out[:, i] = (v >> np.uint64(42 - 14 * i)) & mask
    return out.reshape(height, width)


def pack14(img: np.ndarray, word_swapped: bool) -> bytes:
    """Inverse of unpack14 (used by the simulator and fake chdkptp)."""
    h, w = img.shape
    if w % 4:
        raise ValueError("width must be a multiple of 4")
    px = (img.astype(np.uint64) & np.uint64(0x3FFF)).reshape(-1, 4)
    v = np.zeros(px.shape[0], dtype=np.uint64)
    for i in range(4):
        v = (v << np.uint64(14)) | px[:, i]
    out = np.empty((px.shape[0], 7), dtype=np.uint8)
    for i in range(7):
        out[:, i] = (v >> np.uint64(48 - 8 * i)) & np.uint64(0xFF)
    buf = out.reshape(-1)
    if word_swapped:
        buf = _swap_words(buf)
    return buf.tobytes()


def decode_chdk_raw(data: bytes, spec: SensorSpec) -> np.ndarray:
    """Decode a CHDK .raw framebuffer dump and crop it to the active area (uint16, H x W).

    Fast path for the word-swapped 14-bit layout: read as little-endian 16-bit words, those *are* the
    MSB-first stream, and 7 words hold 8 pixels. Only the active rows are decoded and every shift stays
    within 16 bits, so no 64-bit temporaries: ~5x faster and ~6x less memory than unpack14 on the M3.
    """
    if spec.bits != 14 or spec.raw_width % 8:
        full = unpack14(data, spec.raw_width, spec.raw_height, word_swapped=True)
        return full[spec.active_top:spec.active_bottom, spec.active_left:spec.active_right].copy()
    if len(data) < spec.raw_size_bytes:
        raise ValueError(f"raw buffer too small: {len(data)} < {spec.raw_size_bytes} bytes")
    words = np.frombuffer(data, dtype="<u2", count=spec.raw_size_bytes // 2)
    rows = spec.active_bottom - spec.active_top
    w = words.reshape(spec.raw_height, -1)[spec.active_top:spec.active_bottom].reshape(rows, -1, 7)
    w0, w1, w2, w3, w4, w5, w6 = (w[:, :, i] for i in range(7))
    px = np.empty((rows, w.shape[1], 8), dtype=np.uint16)
    px[:, :, 0] = w0 >> 2
    px[:, :, 1] = ((w0 & 0x3) << 12) | (w1 >> 4)
    px[:, :, 2] = ((w1 & 0xF) << 10) | (w2 >> 6)
    px[:, :, 3] = ((w2 & 0x3F) << 8) | (w3 >> 8)
    px[:, :, 4] = ((w3 & 0xFF) << 6) | (w4 >> 10)
    px[:, :, 5] = ((w4 & 0x3FF) << 4) | (w5 >> 12)
    px[:, :, 6] = ((w5 & 0xFFF) << 2) | (w6 >> 14)
    px[:, :, 7] = w6 & 0x3FFF
    return px.reshape(rows, spec.raw_width)[:, spec.active_left:spec.active_right].copy()


def encode_chdk_raw(active: np.ndarray, spec: SensorSpec, border_value: int | None = None) -> bytes:
    """Embed an active-area image into a full raw frame and pack it like CHDK does."""
    full = np.full((spec.raw_height, spec.raw_width), spec.black_level if border_value is None else border_value,
                   dtype=np.uint16)
    full[spec.active_top:spec.active_bottom, spec.active_left:spec.active_right] = active
    return pack14(full, word_swapped=True)
