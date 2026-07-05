"""AI-generated image pipeline (spec §7).

Public API:

- :class:`ImageSpec` / :class:`ImageRecord` — the logical key and its DB row.
- :func:`ensure_image` — get-or-generate an image inline during a render.
- :func:`make_calendar_icon_resolver` / :data:`IconResolver` /
  :class:`RenderedImage` — the calendar-module unit consumed by the day strip.
- :func:`generate_image_bytes` — the real OpenAI seam implementation
  (``app.config["GENERATE_IMAGE_BYTES"]``).
- :data:`images_bp` — blueprint serving ``/images/generated/<id>`` (§7.6).
- :data:`admin_bp` — the image admin endpoint (§7.4).
"""

from app.images.admin import admin_bp
from app.images.calendar_icons import (
    IconResolver,
    RenderedImage,
    make_calendar_icon_resolver,
)
from app.images.db import ImageRecord, ImageSpec
from app.images.generate import ImageGenerationError, generate_image_bytes
from app.images.routes import images_bp
from app.images.store import ensure_image

__all__ = [
    "IconResolver",
    "ImageGenerationError",
    "ImageRecord",
    "ImageSpec",
    "RenderedImage",
    "admin_bp",
    "ensure_image",
    "generate_image_bytes",
    "images_bp",
    "make_calendar_icon_resolver",
]
