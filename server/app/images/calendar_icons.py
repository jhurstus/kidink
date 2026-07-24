"""Event-icon image unit: icon sizing, prompt construction, icon resolver.

The per-module logic of spec §7.5 for calendar-event icons (day strip and the
Today panel, which share the same icon size per §9.2) and the mechanically
identical Chores icons (§14). Both are the same comic-icon artwork at the same
60×60 size and share the prompt here; they differ only in the image-record
``module`` (their own cache namespace and example set), so the resolver factory
is parameterized by it. The resolver is what view-model builders call: a batch
of icon items (title + optional ``icon_description``) in, a logical-key →
servable-URL mapping (``None`` per failure) out — generation happens inline
behind it (§3.6), concurrently across the batch's missing images.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from flask import current_app, url_for

from app.config import get_settings
from app.images.db import ImageSpec
from app.images.generate import DEFAULT_IMAGE_MODEL
from app.images.store import ensure_images

CALENDAR_ICON_MODULE = "Calendar"

# The Chores module's icons (§14) are the same artwork at the same size as
# calendar icons; only the image-record module differs, so chore icons cache and
# seed their example set (prompt_images/defaults/chores/) independently.
CHORE_ICON_MODULE = "Chores"

# Logical display box (px): drives the generation size (16×, §7.2) and the CSS
# max-width/max-height that aspect-fit the icon. The stored PNG keeps its
# native generation resolution. Shared by both event-icon modules.
CALENDAR_ICON_W = 60
CALENDAR_ICON_H = 60

type IconItem = tuple[str, str | None]
"""One event's image inputs: ``(title, icon_description)`` (§6.4/§7.1).

Structural twin of ``app.event_rows.IconItem`` (the view-model side stays free
of images imports). The optional ``icon_description`` elaborates the title in
the generation prompt; the logical image key is ``icon_description or title``.
"""

type IconResolver = Callable[[Sequence[IconItem]], Mapping[str, str | None]]
"""Maps icon items to servable icon URLs (``None`` per failed item), keyed by
each item's logical key (``icon_description or title``, §7.1).

Batch-shaped so a render's missing icons can generate concurrently
(:func:`app.images.store.ensure_images`): a view-model builder hands over every
item it needs in one call.
"""


@dataclass(frozen=True)
class RenderedImage:
    """An AI image referenced by the current render (for ``?debug_images=1``)."""

    id: int
    spec: ImageSpec


# The user-authored prompt template for day-strip calendar icons. The palette
# table mirrors the §5.3 halftone swatches so generations key cleanly and
# quantize well on the panel (§5.4).
_PROMPT_TEMPLATE = """\
Create an icon that will represent the concept of “{title}” on a \
children’s calendar.  The icon should be in the style of comic book, with black \
outlines and colored fills, emulating a hand-drawn comic.  The icon should be fun \
and engaging for a 7 year old kid.  It will be shown at fairly small resolution \
(60px wide by 60px tall), so keep it simple: very simple shapes, very simple colors, \
and no fine details.

{elaboration}

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

Finally, put the icon on a pure green (i.e. #00FF00) background.  This will allow \
me to chroma-key out the background later.\
"""


def calendar_icon_prompt(title: str, icon_description: str | None = None) -> str:
    """The generation prompt for a calendar-event icon (spec §7.5).

    A set ``icon_description`` (§6.4) does not replace the title: it fills the
    template's elaboration paragraph — the concept stays the event while the
    description steers the artwork. ``app.countdown.hero.countdown_hero_prompt``
    fills its elaboration the same way, but carries it as the trailing paragraph
    instead of mid-template, so its empty-description cleanup differs.
    """
    prompt = _PROMPT_TEMPLATE.format(title=title, elaboration=icon_description or "")
    # An absent description leaves the elaboration paragraph empty; collapse
    # the doubled paragraph break it leaves behind.
    return prompt.replace("\n\n\n\n", "\n\n")


def _make_icon_resolver(
    module: str,
    collected: list[RenderedImage],
) -> IconResolver:
    """Build an event-icon resolver for ``module`` for the current request.

    Shared by the Calendar and Chores icon units (§7.5/§14): identical artwork
    and 60×60 size, keyed under distinct ``module`` cache namespaces. Must be
    called (and the resolver used) inside a request context. Each call resolves
    a whole batch: missing images generate concurrently inside
    :func:`app.images.store.ensure_images`. Every successfully resolved image
    is appended to ``collected``, which ``/render`` hands to the
    ``?debug_images=1`` listing (§3.5).
    """
    settings = get_settings()
    storage_root = current_app.config["APP_STORAGE_PATH"]
    generate = current_app.config["GENERATE_IMAGE_BYTES"]
    model = settings.module_model_tiers.get(module, DEFAULT_IMAGE_MODEL)

    def resolve(items: Sequence[IconItem]) -> dict[str, str | None]:
        # Dedupe preserving order, by logical key (§7.1): a repeated key (a
        # recurring event, or the strip and Today sharing an event) is one
        # logical image, and the first occurrence's prompt seeds the record
        # if it is new. The empty guard keeps event-less renders from ever
        # touching storage.
        unique: dict[str, IconItem] = {}
        for item in items:
            title, icon_description = item
            unique.setdefault(icon_description or title, item)
        if not unique:
            return {}
        requests = [
            (
                ImageSpec(
                    module=module,
                    item_description=key,
                    width=CALENDAR_ICON_W,
                    height=CALENDAR_ICON_H,
                ),
                calendar_icon_prompt(title, icon_description),
            )
            for key, (title, icon_description) in unique.items()
        ]
        image_ids = ensure_images(
            requests,
            storage_root=storage_root,
            generate=generate,
            api_key=settings.openai_api_key,
            model=model,
        )
        resolved: dict[str, str | None] = {}
        for (spec, _), image_id in zip(requests, image_ids, strict=True):
            if image_id is None:
                resolved[spec.item_description] = None
                continue
            rendered = RenderedImage(id=image_id, spec=spec)
            if rendered not in collected:  # resolves once across strip + Today
                collected.append(rendered)
            resolved[spec.item_description] = url_for(
                "images.generated_image", image_id=image_id
            )
        return resolved

    return resolve


def make_calendar_icon_resolver(collected: list[RenderedImage]) -> IconResolver:
    """Build the calendar-icon resolver (module ``Calendar``) for this request.

    Shared across the day strip and the Today/Tomorrow panels, so an event in
    more than one reuses a single image record (§7.5).
    """
    return _make_icon_resolver(CALENDAR_ICON_MODULE, collected)


def make_chore_icon_resolver(collected: list[RenderedImage]) -> IconResolver:
    """Build the chore-icon resolver (module ``Chores``) for this request.

    Same artwork/size as calendar icons but its own cache namespace and example
    set (§14), so a ``chore: Make bed`` icon is distinct from a like-named
    calendar event's.
    """
    return _make_icon_resolver(CHORE_ICON_MODULE, collected)
