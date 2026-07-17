"""AI-generated image pipeline (spec §7).

Public API:

- :class:`ImageSpec` / :class:`ImageRecord` — the logical key and its DB row.
- :func:`ensure_images` / :func:`ensure_image` — get-or-generate images inline
  during a render (a batch's missing images generate concurrently).
- :func:`make_calendar_icon_resolver` / :func:`make_chore_icon_resolver` /
  :data:`IconResolver` / :class:`RenderedImage` — the event-icon unit consumed
  by the Today/Tomorrow panels and the Chores panel (§14).
- :func:`make_strip_icon_resolver` / :data:`StripIconResolver` — the day-strip
  panel-art unit (§9.1): full-bleed opaque art plus the excited today variant.
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
    make_chore_icon_resolver,
)
from app.images.db import ImageRecord, ImageSpec
from app.images.generate import ImageGenerationError, generate_image_bytes
from app.images.routes import images_bp
from app.images.store import ensure_image, ensure_images
from app.images.strip_icons import StripIconResolver, make_strip_icon_resolver

__all__ = [
    "IconResolver",
    "ImageGenerationError",
    "ImageRecord",
    "ImageSpec",
    "RenderedImage",
    "StripIconResolver",
    "admin_bp",
    "ensure_image",
    "ensure_images",
    "generate_image_bytes",
    "images_bp",
    "make_calendar_icon_resolver",
    "make_chore_icon_resolver",
    "make_strip_icon_resolver",
]
