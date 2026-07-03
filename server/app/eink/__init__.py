"""Six-color e-ink quantization, dithering, and Inkplate buffer packing.

The dither/palette core here is shared between the demo push CLI
(``uv run python -m app.eink``) and the future ``/display?quantize=1``
preview pass (spec §5.2).
"""

from app.eink.dither import DitherMode, quantize
from app.eink.pack import emit_header, pack_pixels
from app.eink.palette import PALETTE_RGB, Metric, indices_to_rgb, nearest_indices

__all__ = [
    "PALETTE_RGB",
    "DitherMode",
    "Metric",
    "emit_header",
    "indices_to_rgb",
    "nearest_indices",
    "pack_pixels",
    "quantize",
]
