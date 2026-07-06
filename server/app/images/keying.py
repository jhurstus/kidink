"""Background-keying for generated images (spec §7.2).

``gpt-image-2`` has no transparent-background mode, so images are generated on a
flat pure-green (#00FF00) key background and the alpha is derived here: starting
from the image edges, background-connected pixels near the key color are flood-
filled to transparent (so key-colored regions *inside* the subject stay opaque),
the green spill is removed at the subject's rim, and the result is cropped to
its visible pixels. The PNG is stored at that native generation resolution —
the record's width×height is a *display* size only, applied by CSS — so the
browser always downscales (never upscales) at any device scale factor, and that
downscale anti-aliases the hard keying edge at render time.
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


def key_and_crop(
    png_bytes: bytes,
    *,
    key_rgb: tuple[int, int, int] = (0, 255, 0),
    tolerance: float = _DEFAULT_TOLERANCE,
) -> bytes:
    """Key out the green background and crop to content, at native resolution.

    Returns transparent-PNG bytes at the generation's own resolution: after
    keying, fully transparent borders are cropped away and nothing is resized —
    display scaling is the browser's job (CSS sizes to the record's logical
    width×height).

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

    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()
