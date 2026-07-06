"""Calendar-module image unit: icon sizing, prompt construction, icon resolver.

The per-module logic of spec §7.5 for calendar-event icons (day strip now;
today/tomorrow panels later, which share the same icon size per §9.2). The
resolver is what view-model builders call: item description in, servable icon
URL (or ``None`` on failure) out — generation happens inline behind it (§3.6).
"""

from collections.abc import Callable
from dataclasses import dataclass

from flask import current_app, url_for

from app.config import get_settings
from app.images.db import ImageSpec
from app.images.generate import DEFAULT_IMAGE_MODEL
from app.images.store import ensure_image

CALENDAR_ICON_MODULE = "Calendar"

# Logical display box (px): drives the generation size (16×, §7.2) and the CSS
# max-width/max-height that aspect-fit the icon. The stored PNG keeps its
# native generation resolution.
CALENDAR_ICON_W = 60
CALENDAR_ICON_H = 60

type IconResolver = Callable[[str], str | None]
"""Maps an item description to a servable icon URL, or ``None`` on failure."""


@dataclass(frozen=True)
class RenderedImage:
    """An AI image referenced by the current render (for ``?debug_images=1``)."""

    id: int
    spec: ImageSpec


# The user-authored prompt template for day-strip calendar icons. The palette
# table mirrors the §5.3 halftone swatches so generations key cleanly and
# quantize well on the panel (§5.5).
_PROMPT_TEMPLATE = """\
Create an icon that will represent the concept of “{item_description}” on a children’s \
calendar.  The icon should be in the style of comic book, with black outlines and \
colored fills, emulating a hand-drawn comic.  The icon should be fun and engaging for \
a 7 year old kid.  It will be shown at fairly small resolution (60px wide by 60px \
tall), so keep it simple: very simple shapes, very simple colors, and no fine details.

There should be no text in the image.

This icon will be presented on a color e-ink display with a limited palette.  You \
should strive to only use the following colors:
| Swatch | Recipe |
|---|---|
| Black | 100% black |
| White | 100% white |
| Red | 100% red |
| Yellow | 100% yellow |
| Green | 100% green |
| Blue | 100% blue |
| Orange | 50% red 50% yellow |
| Lime | 50% green 50% yellow |
| Teal | 50% blue 50% green |
| Periwinkle | 35% red 65% blue |
| Steel blue | 85% blue 15% black |
| Pink | 35% red 65% white |
| Light-blue (sky) | 40% blue 60% white |
| Mint | 40% green 60% white |
| Cream / tan | 30% yellow 5% red 65% white |
| Light gray | 20% black 80% white |
| Mid gray | 50% black 50% white |
| Dark gray | 75% black 25% white |
| Navy | 70% blue 30% black |
| Maroon | 70% red 30% black |
| Forest green | 65% green 35% black |
| Amber | 30% red 70% yellow |
| Coral | 55% red 45% white |
| Butter | 40% yellow 60% white |

Avoid purple and brown hues.  They do not render well.

Finally, put the icon a pure green (i.e. #00FF00) background.  This will allow me to \
chroma-key out the background later.\
"""


def calendar_icon_prompt(item_description: str) -> str:
    """The generation prompt for a calendar-event icon (spec §7.5)."""
    return _PROMPT_TEMPLATE.format(item_description=item_description)


def make_calendar_icon_resolver(
    collected: list[RenderedImage],
) -> IconResolver:
    """Build the day-strip icon resolver for the current request.

    Must be called (and the resolver used) inside a request context. Every
    successfully resolved image is appended to ``collected``, which ``/render``
    hands to the ``?debug_images=1`` listing (§3.5).
    """
    settings = get_settings()
    storage_root = current_app.config["APP_STORAGE_PATH"]
    generate = current_app.config["GENERATE_IMAGE_BYTES"]
    model = settings.module_model_tiers.get(CALENDAR_ICON_MODULE, DEFAULT_IMAGE_MODEL)

    def resolve(item_description: str) -> str | None:
        spec = ImageSpec(
            module=CALENDAR_ICON_MODULE,
            item_description=item_description,
            width=CALENDAR_ICON_W,
            height=CALENDAR_ICON_H,
        )
        image_id = ensure_image(
            spec,
            calendar_icon_prompt(item_description),
            storage_root=storage_root,
            generate=generate,
            api_key=settings.openai_api_key,
            model=model,
        )
        if image_id is None:
            return None
        rendered = RenderedImage(id=image_id, spec=spec)
        if rendered not in collected:  # a recurring event resolves once per day
            collected.append(rendered)
        return url_for("images.generated_image", image_id=image_id)

    return resolve
