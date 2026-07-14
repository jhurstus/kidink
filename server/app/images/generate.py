"""OpenAI image generation — the real implementation of the generation seam.

The only images module that touches the network. The Flask app exposes it as the
injectable ``app.config["GENERATE_IMAGE_BYTES"]`` seam (like ``FETCH_ICS``), so
tests swap in a fake and never open a socket. The API key is passed as
``SecretStr`` and never appears in a raised error message or log line — errors
carry only the underlying exception's *type name*, since SDK message text could
echo request details.
"""

import base64
import io
from collections.abc import Callable
from fractions import Fraction

from openai import OpenAIError
from pydantic import SecretStr

DEFAULT_IMAGE_MODEL = "gpt-image-2"

# gpt-image-2 accepts custom WIDTHxHEIGHT sizes where both dimensions are
# divisible by 16, within these bounds and at most _MAX_ASPECT:1 elongated
# (the API rejects e.g. 2560x832 with "maximum supported aspect ratio is 3:1").
_MAX_GEN_W, _MAX_GEN_H = 2560, 1440
_MAX_ASPECT = 3

# Records store small logical display sizes (e.g. 60×60); generation happens
# up to this many times larger (hero-sized records clamp to the API bounds
# instead). Keying runs at that full resolution and the PNG is stored at it
# too, so the browser always downscales — never upscales — when CSS sizes it
# to the logical box, at any device scale factor (§7.2).
_GEN_SCALE = 16

type GenerateImageBytes = Callable[..., bytes]
"""The generation seam: ``(api_key, *, prompt, size, model, base_png=None) ->
PNG bytes``. A non-``None`` ``base_png`` switches from text-to-image to an
image *edit* of those PNG bytes (the §12 excited hero variant)."""


class ImageGenerationError(Exception):
    """Raised when image generation fails.

    The message is deliberately secret-free: the exception type name only, never
    the SDK's message text (which could echo request details).
    """


def generation_size(width: int, height: int) -> str:
    """The API size string for a record's logical display size.

    The largest same-aspect size that is at most ``_GEN_SCALE``× the logical
    box and fits the API bounds, each dimension floored to a multiple of 16:
    small icons scale the full 16× (100×60 → ``"1600x960"``), hero-sized
    records clamp to the bounds instead (460×150 → ``"2560x832"``). Exact
    ``Fraction`` math keeps the result deterministic (§3.4) — no float
    truncation wobble. Raises ``ValueError`` when an extreme aspect ratio
    would collapse a dimension below 16.
    """
    scale = min(
        Fraction(_GEN_SCALE),
        Fraction(_MAX_GEN_W, width),
        Fraction(_MAX_GEN_H, height),
    )
    gen_w = int(width * scale) // 16 * 16
    gen_h = int(height * scale) // 16 * 16
    # The 16px flooring can nudge a near-3:1 box past the API's aspect cap;
    # pull the long dimension back to exactly the cap (a 16-multiple times
    # _MAX_ASPECT stays a 16-multiple).
    if gen_w > gen_h * _MAX_ASPECT:
        gen_w = gen_h * _MAX_ASPECT
    elif gen_h > gen_w * _MAX_ASPECT:
        gen_h = gen_w * _MAX_ASPECT
    if gen_w < 16 or gen_h < 16:
        raise ValueError(f"generation size {gen_w}x{gen_h} below API minimum")
    return f"{gen_w}x{gen_h}"


def generate_image_bytes(
    api_key: SecretStr,
    *,
    prompt: str,
    size: str,
    model: str,
    base_png: bytes | None = None,
) -> bytes:
    """Generate one image via the OpenAI API and return the raw PNG bytes.

    With ``base_png`` set, the API *edits* those PNG bytes per ``prompt``
    (the §12 excited hero) instead of generating from scratch. The result may
    still have a solid key-color background; whether to run it through
    :func:`app.images.keying.key_and_crop` is the store's per-module policy.
    Raises :class:`ImageGenerationError` on any SDK/decode failure.
    """
    from openai import OpenAI  # deferred: the SDK is never needed under test

    try:
        images = OpenAI(api_key=api_key.get_secret_value()).images
        if base_png is None:
            result = images.generate(model=model, prompt=prompt, size=size)
        else:
            result = images.edit(
                model=model,
                image=("base.png", io.BytesIO(base_png), "image/png"),
                prompt=prompt,
                size=size,
            )
        data = result.data or []
        if not data or not data[0].b64_json:
            raise ImageGenerationError("image generation returned no image data")
        return base64.b64decode(data[0].b64_json)
    except ImageGenerationError:
        raise
    except (OpenAIError, ValueError) as exc:
        # Only the exception type: SDK/base64 message text stays out of logs.
        raise ImageGenerationError(
            f"image generation failed: {type(exc).__name__}"
        ) from exc
