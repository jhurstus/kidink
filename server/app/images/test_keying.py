import io

import numpy as np
import pytest
from PIL import Image

from app.images.keying import KeyingError, key_crop_and_fit

KEY = (0, 255, 0)
RED = (220, 40, 40)
# Monday's palette dot green — a legitimate in-subject green that must survive
# keying (distance ~140 from pure green, outside the tolerance).
PALETTE_GREEN = (78, 188, 96)


def _png(pixels: np.ndarray) -> bytes:
    out = io.BytesIO()
    Image.fromarray(pixels.astype(np.uint8), "RGB").save(out, format="PNG")
    return out.getvalue()


def _decode(png: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))


def _canvas(
    w: int = 800, h: int = 480, color: tuple[int, int, int] = KEY
) -> np.ndarray:
    return np.tile(np.array(color, np.uint8), (h, w, 1))


def test_background_keyed_but_interior_key_green_kept() -> None:
    # Red rectangle filling most of the canvas, with a pure-green hole inside it:
    # the surrounding background goes transparent, the hole stays opaque because
    # it is not edge-connected (§7.2).
    pixels = _canvas()
    pixels[40:440, 40:760] = RED
    pixels[200:240, 380:420] = KEY  # interior hole
    result = _decode(key_crop_and_fit(_png(pixels), max_size=(100, 60)))

    alpha = result[..., 3]
    # The crop snapped to the red rectangle, so its corners are opaque subject —
    # and the interior hole (center) stayed opaque too.
    assert alpha[0, 0] == 255 and alpha[-1, -1] == 255
    assert alpha[result.shape[0] // 2, result.shape[1] // 2] == 255


def test_legitimate_palette_green_not_keyed() -> None:
    pixels = _canvas()
    pixels[40:440, 40:760] = PALETTE_GREEN
    result = _decode(key_crop_and_fit(_png(pixels), max_size=(100, 60)))
    assert result[..., 3].max() == 255  # the subject survived


def test_near_key_wobble_is_keyed() -> None:
    # The generated background is never perfectly flat; near-key pixels on the
    # border must still key out.
    pixels = _canvas()
    pixels[0:480, 0:20] = (30, 235, 25)  # wobbly green strip on the left edge
    pixels[40:440, 40:760] = RED
    result = _decode(key_crop_and_fit(_png(pixels), max_size=(100, 60)))
    # Crop snapped to the red subject: wobbly strip and background both gone.
    assert (
        result[..., 3][:, 0].max() > 0
    )  # first column has visible pixels (crop is tight)
    opaque = result[..., 3] == 255
    assert not np.any(np.all(result[..., :3][opaque] == (30, 235, 25), axis=-1))


def test_despill_clamps_green_at_rim() -> None:
    pixels = _canvas()
    pixels[40:440, 40:760] = RED
    # Green-spilled fringe just inside the subject edge.
    pixels[40:440, 40:44] = (180, 250, 60)
    result = _decode(key_crop_and_fit(_png(pixels), max_size=(100, 60)))
    rgb = result[..., :3].astype(int)
    opaque = result[..., 3] == 255
    left_edge = opaque[:, :2]
    r, g, b = rgb[..., 0][:, :2], rgb[..., 1][:, :2], rgb[..., 2][:, :2]
    assert np.all(g[left_edge] <= np.maximum(r[left_edge], b[left_edge]) + 1)


def test_crop_snaps_to_visible_pixels_and_fits_box() -> None:
    # Small off-center subject: output must snap to it (no transparent borders)
    # and scale aspect-preserved so the constraining dimension hits the box.
    pixels = _canvas(1600, 960)
    pixels[100:340, 200:1000] = RED  # 800×240 subject → aspect 10:3
    result = _decode(key_crop_and_fit(_png(pixels), max_size=(100, 60)))

    h, w = result.shape[:2]
    assert (w, h) == (100, 30)  # width-constrained; aspect preserved
    alpha = result[..., 3]
    # No fully-transparent border row/column — the crop snapped to content.
    assert alpha[0, :].any() and alpha[-1, :].any()
    assert alpha[:, 0].any() and alpha[:, -1].any()


def test_height_constrained_subject() -> None:
    pixels = _canvas(1600, 960)
    pixels[80:880, 400:800] = RED  # 400×800 subject → taller than the box aspect
    result = _decode(key_crop_and_fit(_png(pixels), max_size=(100, 60)))
    h, w = result.shape[:2]
    assert (w, h) == (30, 60)


def test_all_key_image_raises() -> None:
    with pytest.raises(KeyingError):
        key_crop_and_fit(_png(_canvas()), max_size=(100, 60))
