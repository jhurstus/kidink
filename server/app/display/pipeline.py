"""Screenshot to device-buffer stages for `/display` (§3.3 steps 5-7)."""

import io

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from app.eink import dither, palette


def screenshot_to_indices(png_bytes: bytes) -> NDArray[np.uint8]:
    """Run the single page-wide six-color pass (§5.2) on a screenshot PNG.

    `quantize`'s defaults are the decided configuration (ordered mixing-plan
    dither, ycc metric, edge snap 48, edge gamma 1.5, eink-demo §4), so the
    bare call is deliberate.
    """
    with Image.open(io.BytesIO(png_bytes)) as img:
        rgb = np.asarray(img.convert("RGB"), dtype=np.uint8)
    return dither.quantize(dither.saturate(rgb, dither.DEFAULT_SATURATE))


def indices_to_png(indices: NDArray[np.uint8]) -> bytes:
    """Encode palette indices as a viewable PNG (the `?quantize=1` preview)."""
    out = io.BytesIO()
    Image.fromarray(palette.indices_to_rgb(indices)).save(out, format="PNG")
    return out.getvalue()
