"""Dinner-module image unit: hero sizing, prompt, and the hero resolver.

The per-module logic of spec §7.5 for the §13 dinner hero. Unlike the
countdown hero (shown as-is on white), the dinner hero is a normal keyed
image: the prompt asks for the pure-green key background and the store
chroma-keys it to a transparent PNG (§7.2) - so "Dinner" must stay out of
``_UNKEYED_MODULES`` in :mod:`app.images.store`. The logical key is the
combined meal name (§7.1), so a dish repeating across days reuses its image.
"""

from flask import current_app, url_for

from app.config import get_settings
from app.dinner.view import HeroResolver
from app.images import ImageSpec, RenderedImage, ensure_image
from app.images.generate import DEFAULT_IMAGE_MODEL

DINNER_MODULE = "Dinner"

# Logical display box (px): the hero band of the dinner panel - the module
# grid cell minus the "Dinner" title above and the menu-name line(s) below.
# Drives the generation size (clamped into the API bounds, §7.2); the stored
# PNG keeps its native generation resolution and CSS aspect-fits it into the
# box (never upscaling). Keep in sync with static/css/dinner.css.
HERO_W = 400
HERO_H = 190

# The user-authored hero prompt: the calendar-icon template reworked for the
# dinner scene - same palette table and green-key tail (keyed module), with
# the combined-dish instruction replacing the icon-simplicity guidance.
_HERO_PROMPT_TEMPLATE = """\
Create an illustration of “{name}”, tonight’s dinner, for the Dinner panel of \
a children’s calendar.  The illustration should be in the style of comic book, \
with black outlines and colored fills, emulating a hand-drawn comic.  It \
should look tasty, fun, and engaging for a 7 year old kid.  If the name lists \
more than one dish, draw them together as one combined dinner scene, for \
example one plate or table setting with the sides beside the main.  The image \
will be displayed at medium size on a color e-ink device, so prefer simple \
color fills and shapes without too much fine detail.

There should be no text in the image.

This illustration will be presented on a color e-ink display with a limited \
palette.  You should strive to only use the following colors:
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

Finally, put the illustration on a pure green (i.e. #00FF00) background.  This \
will allow me to chroma-key out the background later.\
"""


def dinner_image_spec(name: str) -> ImageSpec:
    """The logical key of a meal's hero record (§7.1).

    Shared by the render-path resolver and the ``/admin/meals`` page so both
    always address the same record for a given meal name.
    """
    return ImageSpec(
        module=DINNER_MODULE, item_description=name, width=HERO_W, height=HERO_H
    )


def dinner_hero_prompt(name: str) -> str:
    """The generation prompt for a dinner hero image (spec §7.5, §13)."""
    return _HERO_PROMPT_TEMPLATE.format(name=name)


def make_dinner_hero_resolver(collected: list[RenderedImage]) -> HeroResolver:
    """Build the dinner hero resolver for the current request.

    Must be called (and the resolver used) inside a request context. A
    generation miss yields ``None`` (hero omitted, name remains, §7.3). Every
    successfully resolved image is appended to ``collected`` for the
    ``?debug_images=1`` listing (§3.5).
    """
    settings = get_settings()
    storage_root = current_app.config["APP_STORAGE_PATH"]
    generate = current_app.config["GENERATE_IMAGE_BYTES"]
    model = settings.module_model_tiers.get(DINNER_MODULE, DEFAULT_IMAGE_MODEL)

    def resolve(name: str) -> str | None:
        spec = dinner_image_spec(name)
        image_id = ensure_image(
            spec,
            dinner_hero_prompt(name),
            storage_root=storage_root,
            generate=generate,
            api_key=settings.openai_api_key,
            model=model,
        )
        if image_id is None:
            return None
        rendered = RenderedImage(id=image_id, spec=spec)
        if rendered not in collected:
            collected.append(rendered)
        return url_for("images.generated_image", image_id=image_id)

    return resolve
