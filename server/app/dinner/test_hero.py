import io
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from flask import Flask
from PIL import Image
from pydantic import SecretStr

from app import create_app
from app.dinner.hero import (
    HERO_H,
    HERO_W,
    dinner_hero_prompt,
    dinner_image_spec,
    make_dinner_hero_resolver,
)
from app.images import ImageGenerationError, RenderedImage


def _transparent_png() -> bytes:
    """A red rectangle on a transparent background - crops cleanly (§7.2)."""
    pixels = np.zeros((480, 800, 4), np.uint8)
    pixels[40:440, 40:760] = (220, 40, 40, 255)
    out = io.BytesIO()
    Image.fromarray(pixels, "RGBA").save(out, format="PNG")
    return out.getvalue()


class _Generator:
    """Fake seam recording calls; returns transparent bytes (Dinner is cropped)."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.prompts: list[str] = []

    def __call__(
        self,
        api_key: SecretStr,
        *,
        prompt: str,
        size: str,
        model: str,
        transparent: bool = True,
        base_png: bytes | None = None,
        reference_images: Sequence[bytes] = (),
    ) -> bytes:
        self.prompts.append(prompt)
        if self.fail:
            raise ImageGenerationError("image generation failed: Boom")
        return _transparent_png()


def _app(tmp_path: Path, generate: _Generator) -> Flask:
    app = create_app()
    app.config["APP_STORAGE_PATH"] = tmp_path
    app.config["GENERATE_IMAGE_BYTES"] = generate
    return app


def _resolve(
    tmp_path: Path,
    generate: _Generator,
    collected: list[RenderedImage],
    name: str = "Tacos & Rice",
) -> str | None:
    with _app(tmp_path, generate).test_request_context():
        return make_dinner_hero_resolver(collected)(name)


def test_hero_prompt_carries_name_palette_and_transparent_tail() -> None:
    # Dinner is a transparent module (§13): the prompt keeps the calendar-icon
    # template's e-ink palette table, purple/brown warning, and isolated-subject
    # transparent-background tail.
    prompt = dinner_hero_prompt("Tacos & Rice")
    assert "“Tacos & Rice”" in prompt
    assert "no text" in prompt
    assert "Periwinkle" in prompt
    assert "purple and brown" in prompt
    assert "isolated on a fully transparent background" in prompt
    assert "one combined dinner scene" in prompt


def test_resolver_generates_and_collects_the_hero(tmp_path: Path) -> None:
    generate = _Generator()
    collected: list[RenderedImage] = []

    url = _resolve(tmp_path, generate, collected)

    assert url == "/images/generated/1"
    assert generate.prompts == [dinner_hero_prompt("Tacos & Rice")]
    assert [r.id for r in collected] == [1]
    assert collected[0].spec == dinner_image_spec("Tacos & Rice")
    spec = collected[0].spec
    assert spec.module == "Dinner"
    assert spec.item_description == "Tacos & Rice"
    assert (spec.width, spec.height) == (HERO_W, HERO_H)
    assert spec.variant is None


def test_resolver_reuses_a_warm_image_without_regenerating(tmp_path: Path) -> None:
    # §13: the image is keyed by the dish name and reused across days.
    generate = _Generator()
    _resolve(tmp_path, generate, [])

    url = _resolve(tmp_path, generate, [])

    assert url == "/images/generated/1"
    assert len(generate.prompts) == 1  # second resolve hit the cache


def test_resolver_stores_a_transparent_cropped_png(tmp_path: Path) -> None:
    # Guard against "Dinner" drifting into store.py's _OPAQUE_MODULES: the
    # stored PNG must be the cropped RGBA, not the raw generation bytes.
    _resolve(tmp_path, generate := _Generator(), [])

    stored = Image.open(tmp_path / "gen_images" / "1.png")
    assert stored.mode == "RGBA"
    corner_alpha = np.asarray(stored)[0, 0, 3]
    assert corner_alpha == 255  # cropped to the opaque subject
    assert generate.prompts  # sanity: the fake actually generated it


def test_generation_failure_yields_none(tmp_path: Path) -> None:
    collected: list[RenderedImage] = []

    url = _resolve(tmp_path, _Generator(fail=True), collected)

    assert url is None
    assert collected == []
