"""Joke-module image unit: hero sizing, prompt, and the hero resolver.

The per-module logic of spec §7.5 for the §15 joke panel. The whole panel *is*
the generated image, with the joke/riddle text drawn inside it - so, unlike the
dinner hero (a keyed subject with the name as separate HTML), the joke hero is
displayed as-is on a solid background: "Joke" is in ``_UNKEYED_MODULES`` in
:mod:`app.images.store` (no chroma key, no crop), like the §12 countdown hero.
The logical key is the joke line itself (§7.1, §15), so the image is generated
once and reused each time the daily index cycles back to that joke.
"""

from flask import current_app, url_for

from app.config import get_settings
from app.images import ImageSpec, RenderedImage, ensure_image
from app.images.generate import DEFAULT_IMAGE_MODEL
from app.joke.view import HeroResolver

# Keep in sync with the per-module policy in app/images/store.py: this module
# string opts the hero out of keying (_UNKEYED_MODULES) - the joke is drawn on
# a solid background and shown as-is, not chroma-keyed to transparency.
JOKE_MODULE = "Joke"

# Logical display box (px): the joke grid cell. The hero fills the whole panel
# (object-fit: cover) under the comic frame. Drives the generation size
# (clamped into the API bounds, §7.2); the stored PNG keeps its native
# generation resolution. Keep in sync with static/css/joke.css and the
# comic_panel size in templates/modules/joke.html.
HERO_W = 449
HERO_H = 307

# The hero prompt: the dinner template reworked for the joke panel. Unlike
# dinner it (a) asks for the joke text to be lettered *inside* the image, (b)
# asks for a full-bleed scene with no internal frame (the comic border is added
# by CSS), and (c) ends on a solid background with no #00FF00 green-key tail -
# the joke is an unkeyed module, displayed as-is.
_HERO_PROMPT_TEMPLATE = """\
Create a single comic-book panel for the Joke panel of a children’s calendar \
that tells this joke: “{joke}”.  Draw a fun scene illustrating the joke, and \
letter the COMPLETE joke text legibly inside the image in a bold comic speech \
bubble or caption box.  If it is a riddle, show BOTH the question and its \
answer together (there is no interactivity to reveal it later). There should \
be no text in the image besides the previously quoted joke / riddle. 

The panel \
should be in the style of a comic book, with black outlines and colored fills, \
emulating a hand-drawn comic, and be fun and engaging for a 7 year old kid.  \
The image will be displayed at medium size on a color e-ink device, so use big, \
bold, clearly readable hand-lettering and simple color fills and shapes with \
no fine details.

The illustration must fill the whole frame edge to edge (full bleed).  Do NOT \
draw a panel border, outline, or margin around the scene - a frame is added \
separately.

Use the color #F4C293 for the skin color of any people in the image.

This panel will be presented on a color e-ink display with a limited palette.  \
You should strive to only use the following colors:
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

Finally, use a single flat, solid background color behind the scene (it fills \
the panel, so pick one that contrasts with the art and the lettering).\
"""


def joke_image_spec(text: str) -> ImageSpec:
    """The logical key of a joke's hero record (§7.1, §15).

    Shared by the render-path resolver and the ``/admin/jokes`` page so both
    always address the same record for a given joke line.
    """
    return ImageSpec(
        module=JOKE_MODULE, item_description=text, width=HERO_W, height=HERO_H
    )


def joke_hero_prompt(text: str) -> str:
    """The generation prompt for a joke hero image (spec §7.5, §15)."""
    return _HERO_PROMPT_TEMPLATE.format(joke=text)


def make_joke_hero_resolver(collected: list[RenderedImage]) -> HeroResolver:
    """Build the joke hero resolver for the current request.

    Must be called (and the resolver used) inside a request context. A
    generation miss yields ``None`` (the panel falls back to the HTML text
    bubble, §7.3). Every successfully resolved image is appended to
    ``collected`` for the ``?debug_images=1`` listing (§3.5).
    """
    settings = get_settings()
    storage_root = current_app.config["APP_STORAGE_PATH"]
    generate = current_app.config["GENERATE_IMAGE_BYTES"]
    model = settings.module_model_tiers.get(JOKE_MODULE, DEFAULT_IMAGE_MODEL)

    def resolve(text: str) -> str | None:
        spec = joke_image_spec(text)
        image_id = ensure_image(
            spec,
            joke_hero_prompt(text),
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
