"""Canon EOS M3 sensor geometry as defined by the CHDK port (platform/m3/platform_camera.h).

Values marked TODO(spike) must be confirmed on the real camera (see docs/phase0-results.md).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SensorSpec:
    name: str
    raw_width: int  # full raw frame incl. masked borders
    raw_height: int
    active_left: int  # active area, [left, right) x [top, bottom)
    active_top: int
    active_right: int
    active_bottom: int
    bits: int
    black_level: int
    pixel_size_um: float
    cfa: str  # pattern of the active area's top-left 2x2 block, row-major

    @property
    def width(self) -> int:
        return self.active_right - self.active_left

    @property
    def height(self) -> int:
        return self.active_bottom - self.active_top

    @property
    def max_adu(self) -> int:
        return (1 << self.bits) - 1

    @property
    def raw_row_bytes(self) -> int:
        return self.raw_width * self.bits // 8

    @property
    def raw_size_bytes(self) -> int:
        return self.raw_row_bytes * self.raw_height


EOS_M3 = SensorSpec(
    name="Canon EOS M3 (CHDK)",
    raw_width=6112,
    raw_height=4060,
    active_left=84,
    active_top=46,
    active_right=6084,
    active_bottom=4046,
    bits=14,
    black_level=2047,  # CHDK port value; measured 2048 from the masked border on the real camera
    pixel_size_um=3.72,
    cfa="RGGB",  # confirmed on the real camera (natural colours with RGGB debayer)
)

# Market ISO values offered to N.I.N.A. as the Alpaca "Gains" list.
ISO_VALUES = (100, 200, 400, 800, 1600, 3200, 6400, 12800)
