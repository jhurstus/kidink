"""Quantize full-color images to the six-ink palette, with optional dithering.

``reduced`` and ``fs`` are ports of the Inkplate Arduino library's error
diffusion (``src/graphics/ImageColor/ImageDitherColor.cpp``): raster order,
per-channel error carried into the next pixels, accumulated value clamped to
0..255 *before* palette matching, error diffused as C truncating integer
division ``(weight * err) / coef``, off-edge taps discarded. With
``metric="rgb"`` and ``strength=1.0`` the ``reduced`` mode is bit-exact with
the device's default ReducedDiffusion kernel.

The error-diffusion port and its kernel tables are derived from the Inkplate
Arduino library, Copyright (c) Soldered Electronics, LGPL-3.0 — those
portions remain under LGPL-3.0; see THIRD_PARTY_NOTICES.md at the repo root.
The rest of this module (the ordered mixing-plan dither, edge snapping, stem
darkening, and the vibrance boost) is original to this project.

``ordered`` is a palette-mixing ordered dither (after Yliluoma): each color
resolves to a two-ink mixing plan (inks A, B and a ratio) and an 8x8 Bayer
matrix decides which ink each pixel gets. Unlike error diffusion it is
spatially stable (no "worms", regular dot lattices, clean edges), and unlike
naive threshold-bias dithering it renders pale tints as sparse chromatic dots
— a plain Bayer bias moves colors only along the gray axis and can never
cross a chroma boundary, so pastels would lose their tint entirely. This is
the mode closest to the comic-halftone look spec §5.2 wants for the real
device pipeline.
"""

import functools
from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from app.eink.palette import (
    PALETTE_RGB,
    Metric,
    nearest_indices,
    nearest_lut_bytes,
    ycbcr,
)

DitherMode = Literal["reduced", "fs", "ordered", "none"]


@dataclass(frozen=True)
class Kernel:
    """An error-diffusion kernel, as laid out in ImageDitherColorKernels.h.

    ``weights`` is row-major, ``width`` wide and ``height`` tall; ``x`` is the
    column of the current pixel within row 0. ``coef`` is the divisor — it may
    exceed the weight sum, in which case only part of the error is diffused
    (that damping is the whole point of ReducedDiffusion).
    """

    width: int
    height: int
    x: int
    coef: int
    weights: tuple[int, ...]


REDUCED_DIFFUSION = Kernel(3, 2, 1, 26, (0, 0, 5, 2, 3, 1))
FLOYD_STEINBERG = Kernel(3, 2, 1, 16, (0, 0, 7, 3, 5, 1))

# 8x8 Bayer threshold matrix.
_BAYER_8 = np.array(
    [
        [0, 32, 8, 40, 2, 34, 10, 42],
        [48, 16, 56, 24, 50, 18, 58, 26],
        [12, 44, 4, 36, 14, 46, 6, 38],
        [60, 28, 52, 20, 62, 30, 54, 22],
        [3, 35, 11, 43, 1, 33, 9, 41],
        [51, 19, 59, 27, 49, 17, 57, 25],
        [15, 47, 7, 39, 13, 45, 5, 37],
        [63, 31, 55, 23, 61, 29, 53, 21],
    ],
    dtype=np.float32,
)
# Mixing-plan search: ratio granularity matches the 64-level Bayer matrix,
# the plan LUT quantizes each RGB channel to 6 bits, and the contrast penalty
# discourages plans that mix two very different inks. Its main job is keeping
# grays as black/white mixes rather than equally-averaging yellow+blue
# checkerboards; it must stay small enough that pale tints still prefer a
# sparse chromatic mix over pure white (0.15 was too strong and killed them).
_RATIO_LEVELS = 64
_PLAN_BITS = 6  # per-channel LUT resolution
_CONTRAST_PENALTY = 0.05
# Plans mixing less than this ratio collapse to the pure ink: a 1-2/64 dot
# sprinkle reads as dirt (stray dots in near-pure fields and along the outer
# tails of AA ramps), not as a tint. Real pale tints land at ratio >= 3.
_MIN_RATIO = 3


def _c_div(n: int, d: int) -> int:
    """C integer division: truncates toward zero (Python ``//`` floors)."""
    return -(-n // d) if n < 0 else n // d


# Luma-gradient threshold above which a pixel counts as lying on an edge.
# Antialiasing ramps on text/line art step ~100+ per pixel; soft image
# gradients stay well below.
DEFAULT_EDGE_SNAP = 48
# Vibrance: chroma magnitude at/above which --saturate applies its full
# factor. The quadratic ramp below it means near-neutrals (paper, text ink,
# gray washes) are essentially untouched — a plain linear boost gives
# near-neutrals phantom color that dithers as yellow/red dirt on cream.
_SATURATE_C_REF = 80.0
DEFAULT_SATURATE = 1.4

# Stem darkening: gamma applied to edge pixels before the nearest-ink snap.
# Thresholding an AA ramp at 50% systematically erodes strokes (thin, broken
# glyphs); 1.5 moves the black/white cutoff from luma ~128 to ~160, so ramp
# pixels with >=~37% ink coverage commit to the dark side and strokes render
# at their designed weight. The comic style wants heavy outlines anyway.
DEFAULT_EDGE_GAMMA = 1.5


def quantize(
    rgb: NDArray[np.uint8],
    mode: DitherMode = "ordered",
    metric: Metric = "ycc",
    strength: float = 1.0,
    edge_snap: int = DEFAULT_EDGE_SNAP,
    edge_gamma: float = DEFAULT_EDGE_GAMMA,
) -> NDArray[np.uint8]:
    """Quantize an (h, w, 3) uint8 image to (h, w) palette indices.

    ``strength`` scales the diffused error in the error-diffusion modes; it
    has no effect on ``ordered``/``none``. ``metric`` picks the nearest-ink
    distance for the error-diffusion modes and ``none``; ``ordered`` always
    selects its mixing plans in YCbCr space.

    ``edge_snap`` is a hybrid-screening pass (0 disables): pixels whose luma
    gradient exceeds the threshold — the antialiasing ramps along glyphs and
    line art — are thresholded to their nearest ink instead of dithered.
    Dithering an AA ramp scatters its mid-gray pixels pseudo-randomly along
    the edge (spiky, noisy text); thresholding commits each ramp pixel to the
    dominant side for the cleanest possible stairstep, while flat regions
    keep dithering for tints. ``edge_gamma`` darkens the snapped pixels first
    (stem darkening, 1.0 disables). (For the error-diffusion modes the snap
    is applied after diffusion, so conservation is slightly off along edges.)
    """
    if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8:
        raise ValueError(f"expected (h, w, 3) uint8 image, got {rgb.shape} {rgb.dtype}")
    match mode:
        case "reduced":
            out = _error_diffuse(rgb, REDUCED_DIFFUSION, metric, strength)
        case "fs":
            out = _error_diffuse(rgb, FLOYD_STEINBERG, metric, strength)
        case "ordered":
            out = _ordered(rgb)
        case "none":
            out = nearest_indices(rgb, metric)
    if edge_snap > 0:
        mask = _edge_mask(rgb, edge_snap)
        snapped = nearest_indices(_gamma_lut(edge_gamma)[rgb], metric)
        out = np.where(mask, snapped, out)
    return out


def saturate(rgb: NDArray[np.uint8], factor: float) -> NDArray[np.uint8]:
    """Vibrance-style chroma boost, applied before quantizing.

    A faithful dither can only give a color its true ink coverage — e.g.
    (81,195,85) "bright green" genuinely contains ~32% white and ~24% black,
    so it dithers at only ~45% green. Boosting chroma toward the pure inks
    raises that coverage and the panel reads more saturated.

    Two properties are load-bearing (tests pin both): the boost scales with
    each pixel's own chroma (quadratic ramp up to ``_SATURATE_C_REF``), so
    near-neutrals stay put; and it is exactly luma-preserving — only dither
    coverage shifts from white/black toward the chromatic inks, nothing
    lightens or darkens.
    """
    if factor == 1.0:
        return rgb
    r = rgb[..., 0].astype(np.float32)
    g = rgb[..., 1].astype(np.float32)
    b = rgb[..., 2].astype(np.float32)
    y, cb, cr = ycbcr(r, g, b)
    chroma = np.sqrt(cb * cb + cr * cr)
    ramp = np.minimum(chroma / _SATURATE_C_REF, 1.0)
    scale = 1.0 + (factor - 1.0) * ramp * ramp
    cb *= scale
    cr *= scale
    new_r = y + cr
    new_b = y + cb
    new_g = (y - 0.299 * new_r - 0.114 * new_b) / 0.587
    out = np.stack([new_r, new_g, new_b], axis=-1)
    return np.clip(np.round(out), 0.0, 255.0).astype(np.uint8)


@functools.cache
def _gamma_lut(gamma: float) -> NDArray[np.uint8]:
    values = 255.0 * (np.arange(256, dtype=np.float64) / 255.0) ** gamma
    return np.round(values).astype(np.uint8)


def _edge_mask(rgb: NDArray[np.uint8], threshold: int) -> NDArray[np.bool_]:
    """Pixels adjacent to a luma step of at least ``threshold``."""
    y = (
        0.299 * rgb[..., 0].astype(np.float32)
        + 0.587 * rgb[..., 1].astype(np.float32)
        + 0.114 * rgb[..., 2].astype(np.float32)
    )
    mag = np.zeros_like(y)
    dx = np.abs(y[:, 1:] - y[:, :-1])
    np.maximum(mag[:, 1:], dx, out=mag[:, 1:])
    np.maximum(mag[:, :-1], dx, out=mag[:, :-1])
    dy = np.abs(y[1:, :] - y[:-1, :])
    np.maximum(mag[1:, :], dy, out=mag[1:, :])
    np.maximum(mag[:-1, :], dy, out=mag[:-1, :])
    return mag >= threshold


def _error_diffuse(
    rgb: NDArray[np.uint8], kernel: Kernel, metric: Metric, strength: float
) -> NDArray[np.uint8]:
    # The serial loop is specialized to the 3-wide, 2-tall, x=1 kernel shape
    # shared by ReducedDiffusion and Floyd-Steinberg (taps E, SW, S, SE).
    if (kernel.width, kernel.height, kernel.x) != (3, 2, 1):
        raise ValueError(f"unsupported kernel shape: {kernel}")
    w_e, w_sw, w_s, w_se = kernel.weights[2:]

    h, w = rgb.shape[:2]
    lut = nearest_lut_bytes(metric)
    pal_r, pal_g, pal_b = (PALETTE_RGB[:, c].tolist() for c in range(3))

    # Post-clamp error is always in [-255, 255]; precompute the truncating
    # division for every (weight, error) pair so the hot loop only indexes.
    def div_table(weight: int) -> list[int]:
        return [_c_div(weight * e, kernel.coef) for e in range(-255, 256)]

    tbl_e, tbl_sw, tbl_s, tbl_se = (div_table(t) for t in (w_e, w_sw, w_s, w_se))
    scale = None if strength == 1.0 else strength

    out = np.empty((h, w), dtype=np.uint8)
    cur_r, cur_g, cur_b = [0] * w, [0] * w, [0] * w
    nxt_r, nxt_g, nxt_b = [0] * w, [0] * w, [0] * w
    for y in range(h):
        flat = rgb[y].ravel().tolist()
        row_out = bytearray(w)
        for x in range(w):
            i3 = 3 * x
            r = flat[i3] + cur_r[x]
            g = flat[i3 + 1] + cur_g[x]
            b = flat[i3 + 2] + cur_b[x]
            if r < 0:
                r = 0
            elif r > 255:
                r = 255
            if g < 0:
                g = 0
            elif g > 255:
                g = 255
            if b < 0:
                b = 0
            elif b > 255:
                b = 255
            idx = lut[r << 16 | g << 8 | b]
            row_out[x] = idx
            er = r - pal_r[idx]
            eg = g - pal_g[idx]
            eb = b - pal_b[idx]
            if scale is not None:
                # Clamp back into the division tables' domain (strength > 1
                # can push the scaled error past +-255).
                er = min(255, max(-255, int(er * scale)))
                eg = min(255, max(-255, int(eg * scale)))
                eb = min(255, max(-255, int(eb * scale)))
            if er or eg or eb:
                if x + 1 < w:
                    cur_r[x + 1] += tbl_e[er + 255]
                    cur_g[x + 1] += tbl_e[eg + 255]
                    cur_b[x + 1] += tbl_e[eb + 255]
                    nxt_r[x + 1] += tbl_se[er + 255]
                    nxt_g[x + 1] += tbl_se[eg + 255]
                    nxt_b[x + 1] += tbl_se[eb + 255]
                if x > 0:
                    nxt_r[x - 1] += tbl_sw[er + 255]
                    nxt_g[x - 1] += tbl_sw[eg + 255]
                    nxt_b[x - 1] += tbl_sw[eb + 255]
                nxt_r[x] += tbl_s[er + 255]
                nxt_g[x] += tbl_s[eg + 255]
                nxt_b[x] += tbl_s[eb + 255]
        out[y] = np.frombuffer(bytes(row_out), dtype=np.uint8)
        cur_r, cur_g, cur_b = nxt_r, nxt_g, nxt_b
        nxt_r, nxt_g, nxt_b = [0] * w, [0] * w, [0] * w
    return out


@functools.cache
def _mixing_plan_lut() -> NDArray[np.uint16]:
    """Best two-ink mixing plan for every (quantized) RGB color.

    Flat table over 6-bit-per-channel RGB; each entry packs
    ``A << 9 | B << 6 | ratio`` where the rendered pixel is ink B when the
    Bayer threshold is below ``ratio`` (of 64), else ink A. Plans are chosen
    by YCbCr distance between the target color and the plan's average color,
    plus a contrast penalty that scales with how often the mix alternates.
    """
    pal = PALETTE_RGB.astype(np.float32)
    n = len(pal)

    # All candidate plans: every ink pair (including A==B at ratio 0).
    plans: list[tuple[int, int, int]] = [(i, i, 0) for i in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j:
                plans.extend(
                    (i, j, r) for r in range(_MIN_RATIO, _RATIO_LEVELS // 2 + 1)
                )
    plan_arr = np.array(plans, dtype=np.int32)
    a_idx, b_idx, ratio = plan_arr[:, 0], plan_arr[:, 1], plan_arr[:, 2]
    frac = ratio.astype(np.float32) / _RATIO_LEVELS
    mix_rgb = pal[a_idx] * (1.0 - frac[:, None]) + pal[b_idx] * frac[:, None]
    mix_y, mix_cb, mix_cr = ycbcr(mix_rgb[:, 0], mix_rgb[:, 1], mix_rgb[:, 2])

    pair_delta = pal[a_idx] - pal[b_idx]
    dy, dcb, dcr = ycbcr(pair_delta[:, 0], pair_delta[:, 1], pair_delta[:, 2])
    pair_dist = dy**2 + dcb**2 + dcr**2
    penalty = _CONTRAST_PENALTY * frac * (1.0 - frac) * pair_dist

    # Grid of quantized target colors (bin centers).
    steps = 1 << _PLAN_BITS
    axis = (np.arange(steps, dtype=np.float32) * (256 // steps)) + (256 // steps) / 2.0
    grid_r, grid_g, grid_b = np.meshgrid(axis, axis, axis, indexing="ij")
    gy, gcb, gcr = ycbcr(grid_r.ravel(), grid_g.ravel(), grid_b.ravel())

    best_cost = np.full(gy.shape, np.inf, dtype=np.float32)
    best_plan = np.zeros(gy.shape, dtype=np.uint16)
    for p in range(len(plans)):
        cost = (
            (gy - mix_y[p]) ** 2
            + (gcb - mix_cb[p]) ** 2
            + (gcr - mix_cr[p]) ** 2
            + penalty[p]
        )
        better = cost < best_cost
        best_cost[better] = cost[better]
        best_plan[better] = (a_idx[p] << 9) | (b_idx[p] << 6) | ratio[p]
    return best_plan


def _ordered(rgb: NDArray[np.uint8]) -> NDArray[np.uint8]:
    h, w = rgb.shape[:2]
    lut = _mixing_plan_lut()
    shift = 8 - _PLAN_BITS
    flat = (
        (rgb[..., 0].astype(np.int64) >> shift) << (2 * _PLAN_BITS)
        | (rgb[..., 1].astype(np.int64) >> shift) << _PLAN_BITS
        | (rgb[..., 2].astype(np.int64) >> shift)
    )
    plan = lut[flat]
    a = ((plan >> 9) & 7).astype(np.uint8)
    b = ((plan >> 6) & 7).astype(np.uint8)
    ratio = plan & 63
    bayer = np.tile(_BAYER_8, (-(-h // 8), -(-w // 8)))[:h, :w]
    return np.where(bayer < ratio, b, a)
