"""Synthetic star field rendered onto an RGGB Bayer mosaic, in raw ADU.

Used by the simulated backend and the fake chdkptp so the whole pipeline (packing, decoding,
Alpaca transfer, N.I.N.A. HFR/autofocus) can be exercised without the camera.
"""

from dataclasses import dataclass

import numpy as np

from .sensor import SensorSpec

# Relative response of the R, G, B pixels to a white-ish star.
_CHANNEL_GAIN = {"R": 0.55, "G": 1.0, "B": 0.45}


@dataclass
class StarFieldParams:
    stars: int = 600
    seed: int = 42  # same star positions every frame, like a tracked mount
    sky_e_per_s: float = 3.0  # sky background, electrons/pixel/s at the green channel
    read_noise_e: float = 3.0
    best_focus: int = 5000  # simulated focuser position with the sharpest stars
    fwhm_best_px: float = 2.5
    fwhm_px_per_step: float = 0.004
    fwhm_max_px: float = 40.0


def fwhm_for_focus(params: StarFieldParams, focus_position: int | None) -> float:
    if focus_position is None:
        return params.fwhm_best_px
    fwhm = params.fwhm_best_px + abs(focus_position - params.best_focus) * params.fwhm_px_per_step
    return min(fwhm, params.fwhm_max_px)


def e_per_adu(iso: int) -> float:
    """Rough Canon APS-C conversion gain: ~unity gain around ISO 400. Approximate only."""
    return 4.0 * 100.0 / iso


def _cfa_mask(spec: SensorSpec, height: int, width: int) -> np.ndarray:
    """Per-pixel channel gain for the CFA pattern."""
    p = spec.cfa
    tile = np.array([[_CHANNEL_GAIN[p[0]], _CHANNEL_GAIN[p[1]]],
                     [_CHANNEL_GAIN[p[2]], _CHANNEL_GAIN[p[3]]]], dtype=np.float32)
    return np.tile(tile, (height // 2, width // 2))


def render(spec: SensorSpec, exposure_s: float, iso: int, light: bool = True,
           focus_position: int | None = None, params: StarFieldParams | None = None,
           noise_seed: int | None = None) -> np.ndarray:
    """Render the active area as uint16 raw ADU (black level included)."""
    params = params or StarFieldParams()
    h, w = spec.height, spec.width
    rng = np.random.default_rng(noise_seed)

    electrons = np.zeros((h, w), dtype=np.float32)
    if light:
        electrons += params.sky_e_per_s * exposure_s
        _add_stars(electrons, params, exposure_s, fwhm_for_focus(params, focus_position))
        electrons *= _cfa_mask(spec, h, w)
        # shot noise (normal approximation of Poisson)
        electrons += rng.standard_normal((h, w), dtype=np.float32) * np.sqrt(np.maximum(electrons, 0))
    electrons += rng.standard_normal((h, w), dtype=np.float32) * params.read_noise_e

    adu = electrons / e_per_adu(iso) + spec.black_level
    return np.clip(adu, 0, spec.max_adu).astype(np.uint16)


def _add_stars(img: np.ndarray, params: StarFieldParams, exposure_s: float, fwhm: float) -> None:
    h, w = img.shape
    rng = np.random.default_rng(params.seed)
    xs = rng.uniform(0, w, params.stars)
    ys = rng.uniform(0, h, params.stars)
    # power-law brightness: few bright stars, many faint ones (electrons/s)
    flux = 200.0 * (rng.pareto(1.3, params.stars) + 1.0)

    sigma = fwhm / 2.3548
    r = int(np.ceil(4 * sigma)) + 1
    off = np.arange(-r, r + 1, dtype=np.float32)
    for x, y, f in zip(xs, ys, flux):
        cx, cy = int(x), int(y)
        x0, x1 = max(cx - r, 0), min(cx + r + 1, w)
        y0, y1 = max(cy - r, 0), min(cy + r + 1, h)
        if x0 >= x1 or y0 >= y1:
            continue
        gx = np.exp(-0.5 * ((off[x0 - cx + r:x1 - cx + r] + cx - x + 0.5) / sigma) ** 2)
        gy = np.exp(-0.5 * ((off[y0 - cy + r:y1 - cy + r] + cy - y + 0.5) / sigma) ** 2)
        norm = f * exposure_s / (2 * np.pi * sigma * sigma)
        img[y0:y1, x0:x1] += norm * np.outer(gy, gx)
