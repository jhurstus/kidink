import io
from collections.abc import Sequence
from pathlib import Path

from flask import Flask
from PIL import Image
from pydantic import SecretStr

from app import create_app
from app.images import ImageGenerationError, RenderedImage
from app.joke.hero import (
    HERO_H,
    HERO_W,
    joke_hero_prompt,
    joke_image_spec,
    make_joke_hero_resolver,
)

JOKE = "What do you call a bear with no teeth? A gummy bear!"


def _solid_png() -> bytes:
    """A plain blue PNG - the joke is unkeyed, so it is stored verbatim."""
    out = io.BytesIO()
    Image.new("RGB", (800, 480), (40, 90, 200)).save(out, format="PNG")
    return out.getvalue()


class _Generator:
    """Fake seam recording calls; returns a plain (non-key) PNG."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.prompts: list[str] = []
        self.last_png = _solid_png()

    def __call__(
        self,
        api_key: SecretStr,
        *,
        prompt: str,
        size: str,
        model: str,
        base_png: bytes | None = None,
        reference_images: Sequence[bytes] = (),
    ) -> bytes:
        self.prompts.append(prompt)
        if self.fail:
            raise ImageGenerationError("image generation failed: Boom")
        return self.last_png


def _app(tmp_path: Path, generate: _Generator) -> Flask:
    app = create_app()
    app.config["APP_STORAGE_PATH"] = tmp_path
    app.config["GENERATE_IMAGE_BYTES"] = generate
    return app


def _resolve(
    tmp_path: Path,
    generate: _Generator,
    collected: list[RenderedImage],
    text: str = JOKE,
) -> str | None:
    with _app(tmp_path, generate).test_request_context():
        return make_joke_hero_resolver(collected)(text)


def test_hero_prompt_draws_the_text_and_omits_the_chroma_key() -> None:
    # The joke is drawn inside the image (§15) on a solid background - unkeyed,
    # so no #00FF00 green-key tail - but keeps the e-ink palette table.
    prompt = joke_hero_prompt(JOKE)
    assert JOKE in prompt
    assert "#00FF00" not in prompt
    assert "Periwinkle" in prompt  # palette table retained
    assert "purple and brown" in prompt
    assert "full bleed" in prompt
    assert "question and its answer" in prompt.lower()


def test_resolver_generates_and_collects_the_hero(tmp_path: Path) -> None:
    generate = _Generator()
    collected: list[RenderedImage] = []

    url = _resolve(tmp_path, generate, collected)

    assert url == "/images/generated/1"
    assert generate.prompts == [joke_hero_prompt(JOKE)]
    assert [r.id for r in collected] == [1]
    spec = collected[0].spec
    assert spec == joke_image_spec(JOKE)
    assert spec.module == "Joke"
    assert spec.item_description == JOKE
    assert (spec.width, spec.height) == (HERO_W, HERO_H)
    assert spec.variant is None


def test_resolver_reuses_a_warm_image_without_regenerating(tmp_path: Path) -> None:
    # §15: the image is keyed by the joke line and reused each N-day cycle.
    generate = _Generator()
    _resolve(tmp_path, generate, [])

    url = _resolve(tmp_path, generate, [])

    assert url == "/images/generated/1"
    assert len(generate.prompts) == 1  # second resolve hit the cache


def test_resolver_stores_the_unkeyed_bytes_verbatim(tmp_path: Path) -> None:
    # Guard against "Joke" drifting out of store.py's _UNKEYED_MODULES: the
    # stored PNG must be the raw generation bytes, not a keyed/cropped RGBA.
    generate = _Generator()
    _resolve(tmp_path, generate, [])

    stored = (tmp_path / "gen_images" / "1.png").read_bytes()
    assert stored == generate.last_png


def test_generation_failure_yields_none(tmp_path: Path) -> None:
    collected: list[RenderedImage] = []

    url = _resolve(tmp_path, _Generator(fail=True), collected)

    assert url is None
    assert collected == []
