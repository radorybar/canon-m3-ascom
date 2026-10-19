import numpy as np

from canon_m3_ascom import rawcodec
from canon_m3_ascom.sensor import EOS_M3


def chdk_get_raw_pixel(buf: bytes, row_len: int, x: int, y: int) -> int:
    """Literal transcription of CHDK core/raw.c get_raw_pixel() for 14-bit sensors."""
    a = buf[y * row_len + (x // 8) * 14:]
    return {
        0: (a[1] << 6) | (a[0] >> 2),
        1: ((a[0] & 0x03) << 12) | (a[3] << 4) | (a[2] >> 4),
        2: ((a[2] & 0x0F) << 10) | (a[5] << 2) | (a[4] >> 6),
        3: ((a[4] & 0x3F) << 8) | a[7],
        4: (a[6] << 6) | (a[9] >> 2),
        5: ((a[9] & 0x03) << 12) | (a[8] << 4) | (a[11] >> 4),
        6: ((a[11] & 0x0F) << 10) | (a[10] << 2) | (a[13] >> 6),
        7: ((a[13] & 0x3F) << 8) | a[12],
    }[x % 8]


def test_roundtrip_small():
    rng = np.random.default_rng(1)
    img = rng.integers(0, 1 << 14, size=(6, 16), dtype=np.uint16)
    for swapped in (False, True):
        assert np.array_equal(rawcodec.unpack14(rawcodec.pack14(img, swapped), 16, 6, swapped), img)


def test_matches_chdk_reference():
    rng = np.random.default_rng(2)
    w, h = 32, 4
    img = rng.integers(0, 1 << 14, size=(h, w), dtype=np.uint16)
    buf = rawcodec.pack14(img, word_swapped=True)
    row_len = w * 14 // 8
    for y in range(h):
        for x in range(w):
            assert chdk_get_raw_pixel(buf, row_len, x, y) == img[y, x], (x, y)


def test_fast_decoder_matches_generic_unpacker():
    rng = np.random.default_rng(4)
    full = rng.integers(0, 1 << 14, size=(EOS_M3.raw_height, EOS_M3.raw_width), dtype=np.uint16)
    data = rawcodec.pack14(full, word_swapped=True)
    expected = full[EOS_M3.active_top:EOS_M3.active_bottom, EOS_M3.active_left:EOS_M3.active_right]
    assert np.array_equal(rawcodec.decode_chdk_raw(data, EOS_M3), expected)


def test_full_m3_frame():
    rng = np.random.default_rng(3)
    active = rng.integers(0, 1 << 14, size=(EOS_M3.height, EOS_M3.width), dtype=np.uint16)
    data = rawcodec.encode_chdk_raw(active, EOS_M3)
    assert len(data) == EOS_M3.raw_size_bytes == 43_425_760
    assert np.array_equal(rawcodec.decode_chdk_raw(data, EOS_M3), active)
    # masked border carries the black level
    full = rawcodec.unpack14(data, EOS_M3.raw_width, EOS_M3.raw_height, word_swapped=True)
    assert full[0, 0] == EOS_M3.black_level
