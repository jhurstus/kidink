"""OpenAI image generation — the real implementation of the generation seam.

The only images module that touches the network. The Flask app exposes it as the
injectable ``app.config["GENERATE_IMAGE_BYTES"]`` seam (like ``FETCH_ICS``), so
tests swap in a fake and never open a socket. The API key is passed as
``SecretStr`` and never appears in a raised error message or log line — errors
carry only the underlying exception's *type name*, since SDK message text could
echo request details.
"""

import base64
from collections.abc import Callable

from openai import OpenAIError
from pydantic import SecretStr

DEFAULT_IMAGE_MODEL = "gpt-image-2"

# gpt-image-2 accepts custom WIDTHxHEIGHT sizes where both dimensions are
# divisible by 16, within these bounds.
_MAX_GEN_W, _MAX_GEN_H = 2560, 1440

# Records store small logical display sizes (e.g. 60×60); generation happens
# this many times larger. Keying runs at that full resolution and the PNG is
# stored at it too, so the browser always downscales — never upscales — when
# CSS sizes it to the logical box, at any device scale factor (§7.2).
_GEN_SCALE = 16

type GenerateImageBytes = Callable[..., bytes]
"""The generation seam: ``(api_key, *, prompt, size, model) -> PNG bytes``."""


class ImageGenerationError(Exception):
    """Raised when image generation fails.

    The message is deliberately secret-free: the exception type name only, never
    the SDK's message text (which could echo request details).
    """


def generation_size(width: int, height: int) -> str:
    """The API size string for a record's logical display size: ``_GEN_SCALE``× larger.

    E.g. 100×60 → ``"1600x960"`` — same aspect ratio exactly, both dimensions
    divisible by 16. Raises ``ValueError`` if the scaled size exceeds the API
    bounds (future hero-image sizes will need a different rule).
    """
    gen_w, gen_h = width * _GEN_SCALE, height * _GEN_SCALE
    if gen_w > _MAX_GEN_W or gen_h > _MAX_GEN_H:
        raise ValueError(f"generation size {gen_w}x{gen_h} exceeds API bounds")
    return f"{gen_w}x{gen_h}"


def generate_image_bytes(
    api_key: SecretStr, *, prompt: str, size: str, model: str
) -> bytes:
    """Generate one image via the OpenAI API and return the raw PNG bytes.

    The result still has its solid key-color background; callers run it through
    :func:`app.images.keying.key_and_crop`. Raises
    :class:`ImageGenerationError` on any SDK/decode failure.
    """
    from openai import OpenAI  # deferred: the SDK is never needed under test

    try:
        result = OpenAI(api_key=api_key.get_secret_value()).images.generate(
            model=model, prompt=prompt, size=size
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
