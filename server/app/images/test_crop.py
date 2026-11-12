import io

import numpy as np
import pytest
from PIL import Image

from app.images.crop import EmptyImageError, crop_to_visible

RED = (220, 40, 40)


def _png(rgba: np.ndarray) -> bytes:
    out = io.BytesIO()
    Image.fromarray(rgba.astype(np.uint8), "RGBA").save(out, format="PNG")
    return out.getvalue()


def _decode(png: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))


def _canvas(w: int = 1600, h: int = 960) -> np.ndarray:
    return np.zeros((h, w, 4), np.uint8)


def test_crop_snaps_to_visible_pixels_at_native_resolution() -> None:
    # Small off-center subject: output must snap to it (no transparent borders)
    # and keep its native resolution - no resize.
    rgba = _canvas()
    rgba[100:340, 200:1000] = (*RED, 255)  # 800×240 subject
    result = _decode(crop_to_visible(_png(rgba)))

    assert result.shape[:2] == (240, 800)
    alpha = result[..., 3]
    assert alpha[0, :].any() and alpha[-1, :].any()
    assert alpha[:, 0].any() and alpha[:, -1].any()


def test_faint_backdrop_noise_is_cleared_and_ignored_by_the_crop() -> None:
    # The model's backdrop carries faint alpha specks (observed up to ~10);
    # they must neither widen the crop box nor survive as faint pixels.
    rgba = _canvas()
    rgba[100:340, 200:1000] = (*RED, 255)  # 800×240 subject...
    rgba[100:120, 200:220] = (0, 0, 0, 0)  # ...with a notch at its top-left
    rgba[10, 10] = (0, 0, 0, 10)  # speck outside the subject's box
    rgba[105, 205] = (0, 0, 0, 10)  # speck inside the box, in the notch
    result = _decode(crop_to_visible(_png(rgba)))

    assert result.shape[:2] == (240, 800)
    assert result[5, 5, 3] == 0  # the in-box speck was cleared, not kept faint


def test_near_opaque_subject_snaps_to_fully_opaque() -> None:
    # The model's "opaque" subject sits at alpha 253-254; it is stored at 255,
    # while real anti-aliased edge alpha is kept.
    rgba = _canvas(100, 100)
    rgba[10:90, 10:90] = (*RED, 254)
    rgba[10:90, 9] = (*RED, 128)  # soft left edge
    result = _decode(crop_to_visible(_png(rgba)))

    assert result[40, 40, 3] == 255
    assert result[40, 0, 3] == 128


def test_fully_transparent_image_raises() -> None:
    rgba = _canvas()
    rgba[5, 5] = (0, 0, 0, 10)  # noise only
    with pytest.raises(EmptyImageError):
        crop_to_visible(_png(rgba))
