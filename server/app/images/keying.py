"""Background-keying and sizing for generated images (spec §7.2).

``gpt-image-2`` has no transparent-background mode, so images are generated on a
flat pure-green (#00FF00) key background and the alpha is derived here: starting
from the image edges, background-connected pixels near the key color are flood-
filled to transparent (so key-colored regions *inside* the subject stay opaque),
the green spill is removed at the subject's rim, the result is cropped to its
visible pixels, and finally downscaled — aspect ratio preserved — to fit within
the record's width×height bounding box. Generating large and downscaling turns
the hard keying edge into a smooth anti-aliased one.
"""

import io

import numpy as np
from PIL import Image

# How far (Euclidean RGB distance) a pixel may sit from the key color and still
# count as background. The model never emits perfectly flat green; 100 absorbs
# that wobble while leaving legitimate palette greens (e.g. #4ebc60, distance
# ~140 from pure green) opaque.
_DEFAULT_TOLERANCE = 100.0

# Width in pixels of the de-spill rim just inside the keyed edge.
_DESPILL_RIM_PX = 2


class KeyingError(Exception):
    """Raised when the keyed image has no visible pixels (bad generation)."""


def _edge_connected_key_mask(
    rgb: np.ndarray, key_rgb: tuple[int, int, int], tolerance: float
) -> np.ndarray:
    """Boolean (H, W) mask of background: key-colored pixels reachable from an edge.

    Candidates are pixels within ``tolerance`` of ``key_rgb``; the returned mask
    is the subset 4-connected to the image border, grown by iterative frontier
    dilation (vectorized shifts) until fixpoint.
    """
    distance = np.linalg.norm(
        rgb.astype(np.float32) - np.array(key_rgb, np.float32), axis=-1
    )
    candidates = distance < tolerance

    reached = np.zeros_like(candidates)
    reached[0, :] = candidates[0, :]
    reached[-1, :] = candidates[-1, :]
    reached[:, 0] = candidates[:, 0]
    reached[:, -1] = candidates[:, -1]
    while True:
        grown = _dilate4(reached) & candidates
        if (grown == reached).all():
            return reached
        reached = grown


def _dilate4(mask: np.ndarray) -> np.ndarray:
    """One 4-connectivity dilation of a boolean mask (self ∪ four neighbors)."""
    out = mask.copy()
    out[1:, :] |= mask[:-1, :]
    out[:-1, :] |= mask[1:, :]
    out[:, 1:] |= mask[:, :-1]
    out[:, :-1] |= mask[:, 1:]
    return out


def _despill_rim(rgb: np.ndarray, background: np.ndarray) -> np.ndarray:
    """Kill the green halo on the opaque rim bordering ``background``.

    On the rim (opaque pixels within ``_DESPILL_RIM_PX`` of the background) the
    classic despill ``g = min(g, max(r, b))`` clamps green below the other
    channels, removing key spill without touching interior color.
    """
    near_background = background
    for _ in range(_DESPILL_RIM_PX):
        near_background = _dilate4(near_background)
    rim = near_background & ~background

    out = rgb.copy()
    r, g, b = out[..., 0], out[..., 1], out[..., 2]
    g[rim] = np.minimum(g[rim], np.maximum(r[rim], b[rim]))
    return out


def key_crop_and_fit(
    png_bytes: bytes,
    *,
    max_size: tuple[int, int],
    key_rgb: tuple[int, int, int] = (0, 255, 0),
    tolerance: float = _DEFAULT_TOLERANCE,
) -> bytes:
    """Key out the green background, crop to content, fit within ``max_size``.

    Returns transparent-PNG bytes no larger than ``max_size`` (the record's
    width×height, a *maximum* bounding box): after keying, fully transparent
    borders are cropped away and the result is scaled — aspect ratio preserved —
    so the more constraining dimension matches the box exactly.

    Raises :class:`KeyingError` if keying leaves no visible pixels.
    """
    rgb = np.asarray(Image.open(io.BytesIO(png_bytes)).convert("RGB"))
    background = _edge_connected_key_mask(rgb, key_rgb, tolerance)
    rgb = _despill_rim(rgb, background)
    alpha = np.where(background, 0, 255).astype(np.uint8)

    # Crop to the bounding box of visible (non-transparent) pixels.
    visible_rows = np.flatnonzero(alpha.any(axis=1))
    visible_cols = np.flatnonzero(alpha.any(axis=0))
    if visible_rows.size == 0:
        raise KeyingError("keying removed every pixel")
    top, bottom = visible_rows[0], visible_rows[-1] + 1
    left, right = visible_cols[0], visible_cols[-1] + 1

    rgba = np.dstack([rgb, alpha])[top:bottom, left:right]
    image = Image.fromarray(rgba, "RGBA")

    # Aspect-preserving fit: the more constraining dimension lands exactly on the
    # box edge. Downscale through premultiplied alpha ("RGBa") so LANCZOS never
    # mixes the RGB of transparent pixels into visible edges (green/dark fringe).
    max_w, max_h = max_size
    scale = min(max_w / image.width, max_h / image.height)
    target = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    image = (
        image.convert("RGBa").resize(target, Image.Resampling.LANCZOS).convert("RGBA")
    )

    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()
