"""The Inkplate 13 SPECTRA six-ink palette and nearest-color lookup.

Palette order matches ``pallete[]`` in the Inkplate Arduino library
(``src/boards/Inkplate13SPECTRA/pins.h``). The order is load-bearing twice
over: the packed nibble in the device buffer is ``index << 1``, and
nearest-color ties break to the lowest index, matching the library's strict
``<`` comparison.

Two distance metrics select the nearest ink:

- ``rgb`` — unweighted squared euclidean in RGB, exactly what the device
  firmware does.
- ``ycc`` (default) — squared euclidean in YCbCr. Near-neutral pixels
  (antialiased edges, gray fills) sit almost as close to the saturated inks
  as to black/white in plain RGB, so tiny channel imbalances snap them to
  yellow/red/etc., spraying colored speckle along edges; separating luma from
  chroma makes saturated inks distant from near-grays. The weight on the
  chroma terms is deliberately 1.0: an earlier 2x chroma up-weight muted
  legitimate mid-saturation colors (a bright green like (81,195,85) landed on
  the green/white decision boundary and rendered gray-green). Plain YCbCr
  already separates the cases; no up-weight is needed.
"""

import functools
from typing import Final, Literal

import numpy as np
from numpy.typing import NDArray

Metric = Literal["ycc", "rgb"]

# black, white, yellow, red, blue, green — SPECTRA library order.
PALETTE_RGB: Final = np.array(
    [
        [0x00, 0x00, 0x00],
        [0xFF, 0xFF, 0xFF],
        [0xFF, 0xFF, 0x00],
        [0xFF, 0x00, 0x00],
        [0x00, 0x00, 0xFF],
        [0x00, 0xFF, 0x00],
    ],
    dtype=np.uint8,
)

BLACK: Final = 0
WHITE: Final = 1
YELLOW: Final = 2
RED: Final = 3
BLUE: Final = 4
GREEN: Final = 5


def ycbcr(r: NDArray, g: NDArray, b: NDArray) -> tuple[NDArray, NDArray, NDArray]:
    """BT.601 luma plus simple chroma axes, the ``ycc`` metric's space."""
    y = 0.299 * r + 0.587 * g + 0.114 * b
    return y, b - y, r - y


@functools.cache
def _nearest_lut(metric: Metric) -> NDArray[np.uint8]:
    """Flat 256**3 table mapping (r<<16 | g<<8 | b) to nearest palette index."""
    axis = np.arange(256)
    best_idx = np.zeros((256, 256, 256), dtype=np.uint8)
    if metric == "rgb":
        # Exact integer math, matching the firmware's findClosestPalette.
        best_dist = np.full((256, 256, 256), np.iinfo(np.int64).max, dtype=np.int64)
        for idx, (pr, pg, pb) in enumerate(PALETTE_RGB.astype(np.int64)):
            dist = (
                ((axis - pr) ** 2)[:, None, None]
                + ((axis - pg) ** 2)[None, :, None]
                + ((axis - pb) ** 2)[None, None, :]
            )
            better = dist < best_dist  # strict: first (lowest) index wins ties
            best_dist[better] = dist[better]
            best_idx[better] = idx
        return best_idx.reshape(-1)

    # ycc: YCbCr euclidean. Y/Cb/Cr are all linear in r,g,b, so build them
    # as broadcast sums of per-axis terms.
    axis_f = axis.astype(np.float32)
    zero = np.zeros_like(axis_f)
    yr, cbr, crr = ycbcr(axis_f, zero, zero)
    yg, cbg, crg = ycbcr(zero, axis_f, zero)
    yb, cbb, crb = ycbcr(zero, zero, axis_f)
    best_dist = np.full((256, 256, 256), np.inf, dtype=np.float32)
    pal = PALETTE_RGB.astype(np.float32)
    for idx in range(len(pal)):
        py, pcb, pcr = ycbcr(pal[idx, 0], pal[idx, 1], pal[idx, 2])
        dy = yr[:, None, None] + yg[None, :, None] + yb[None, None, :] - py
        dcb = cbr[:, None, None] + cbg[None, :, None] + cbb[None, None, :] - pcb
        dcr = crr[:, None, None] + crg[None, :, None] + crb[None, None, :] - pcr
        dist = dy**2 + dcb**2 + dcr**2
        better = dist < best_dist
        best_dist[better] = dist[better]
        best_idx[better] = idx
    return best_idx.reshape(-1)


@functools.cache
def nearest_lut_bytes(metric: Metric) -> bytes:
    """The nearest-palette LUT as bytes, for fast scalar indexing."""
    return _nearest_lut(metric).tobytes()


def nearest_indices(rgb: NDArray[np.uint8], metric: Metric) -> NDArray[np.uint8]:
    """Map an (h, w, 3) uint8 image to (h, w) palette indices, vectorized."""
    lut = _nearest_lut(metric)
    flat = (
        rgb[..., 0].astype(np.int64) << 16
        | rgb[..., 1].astype(np.int64) << 8
        | rgb[..., 2].astype(np.int64)
    )
    return lut[flat]


def indices_to_rgb(indices: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """Expand (h, w) palette indices back to (h, w, 3) RGB, for previews."""
    return PALETTE_RGB[indices]
