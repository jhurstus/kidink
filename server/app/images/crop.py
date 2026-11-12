"""Tidy and crop a transparent generated image (spec §7.2).

Transparent-module images are requested from the API with
``background="transparent"``, so they arrive with real alpha. The model's alpha
is slightly noisy at both ends: the backdrop carries faint specks (alpha up to
~10) that would otherwise widen the crop box and could dither into stray dots
on the e-ink panel, and the subject sits at 253-254 rather than 255. Both ends
are snapped clean, then the image is cropped to its visible pixels. The PNG is
stored at that native generation resolution - the record's width×height is a
*display* size only, applied by CSS - so the browser always downscales (never
upscales) at any device scale factor.
"""

import io

import numpy as np
from PIL import Image

# Alpha at or below this snaps to fully transparent; at or above
# 255 - _ALPHA_SNAP to fully opaque. Comfortably above the backdrop noise the
# model emits, and far below anything a viewer could see.
_ALPHA_SNAP = 15


class EmptyImageError(Exception):
    """Raised when a generated image has no visible pixels (bad generation)."""


def crop_to_visible(png_bytes: bytes) -> bytes:
    """Snap near-0/near-255 alpha clean and crop to the visible pixels.

    Returns PNG bytes at the generation's own resolution: nothing is resized -
    display scaling is the browser's job (CSS sizes to the record's logical
    width×height). Raises :class:`EmptyImageError` if nothing is visible.
    """
    rgba = np.array(Image.open(io.BytesIO(png_bytes)).convert("RGBA"))
    alpha = rgba[..., 3]
    alpha[alpha <= _ALPHA_SNAP] = 0
    alpha[alpha >= 255 - _ALPHA_SNAP] = 255

    visible_rows = np.flatnonzero(alpha.any(axis=1))
    visible_cols = np.flatnonzero(alpha.any(axis=0))
    if visible_rows.size == 0:
        raise EmptyImageError("generated image has no visible pixels")
    top, bottom = visible_rows[0], visible_rows[-1] + 1
    left, right = visible_cols[0], visible_cols[-1] + 1

    out = io.BytesIO()
    Image.fromarray(rgba[top:bottom, left:right], "RGBA").save(out, format="PNG")
    return out.getvalue()
