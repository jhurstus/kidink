"""Derive the burst frame's silhouette mask (a dev-time asset tool).

The starburst frame asset (``static/img/countdown/countdown_burst.png``) is
opaque white OUTSIDE its jagged outline, and the enlarged countdown panel
bleeds over its neighbors — overlaid verbatim, those white corners would
occlude them. The fix in ``static/css/countdown.css`` is a CSS mask on the
burst container built from the asset this tool writes
(``countdown_burst_mask.png``): opaque everywhere inside the frame's outer
outline *including the transparent center hole*, transparent outside. Masked
that way, the full-bleed hero, the body, and the frame itself all clip to the
burst shape, so the neighboring panels show through outside the outline.

The silhouette cannot be expressed in CSS from the frame alone (the center
hole and the exterior are both alpha 0 — only a flood fill from the image
edges can tell them apart), hence this derived asset. Regenerate it after
replacing the frame art:

    uv run python -m app.countdown.burst_mask
"""

from pathlib import Path

import numpy as np
from PIL import Image

from app.images.keying import _edge_connected_key_mask

_IMG_DIR = Path(__file__).resolve().parent.parent / "static" / "img" / "countdown"
FRAME_PATH = _IMG_DIR / "countdown_burst.png"
MASK_PATH = _IMG_DIR / "countdown_burst_mask.png"

# The frame's exterior is flat near-white; the tolerance absorbs the outline's
# anti-aliased near-white rim. The interior band's identical white is safe:
# the outline seals it off from the edge-connected flood.
_WHITE_TOLERANCE = 60.0


def derive_mask() -> float:
    """Write ``MASK_PATH``; return the opaque (inside-the-outline) fraction."""
    pixels = np.asarray(Image.open(FRAME_PATH).convert("RGBA"))
    rgb = pixels[..., :3].copy()
    # Transparent pixels (the center hole) must never read as exterior white,
    # whatever RGB they carry under their zero alpha.
    rgb[pixels[..., 3] < 128] = 0
    exterior = _edge_connected_key_mask(rgb, (255, 255, 255), _WHITE_TOLERANCE)

    mask = np.zeros_like(pixels)
    mask[..., 3] = np.where(exterior, 0, 255)
    Image.fromarray(mask, "RGBA").save(MASK_PATH)
    return float(1.0 - exterior.mean())


if __name__ == "__main__":
    inside = derive_mask()
    print(f"wrote {MASK_PATH} (inside-the-outline fraction: {inside:.3f})")
