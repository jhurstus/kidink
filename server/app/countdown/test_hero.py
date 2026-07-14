from collections.abc import Sequence
from pathlib import Path

from flask import Flask
from pydantic import SecretStr

from app import create_app
from app.countdown.hero import (
    HERO_H,
    HERO_W,
    countdown_hero_prompt,
    excited_hero_prompt,
    make_countdown_hero_resolver,
)
from app.images import ImageGenerationError, RenderedImage


def test_hero_prompt_contains_title_and_white_background() -> None:
    prompt = countdown_hero_prompt("camping trip")
    assert "“camping trip”" in prompt
    assert "pure white background" in prompt


def test_hero_prompt_adds_icon_description_as_its_own_paragraph() -> None:
    # §6.4: the description elaborates the title instead of replacing it.
    prompt = countdown_hero_prompt("Camping!!", "a family pitching a red tent")
    assert "“Camping!!”" in prompt
    assert "\n\na family pitching a red tent\n\n" in prompt


def test_hero_prompt_without_description_has_no_empty_paragraph() -> None:
    assert "\n\n\n" not in countdown_hero_prompt("camping trip")


def test_hero_prompt_drops_the_palette_and_chroma_key_guidance() -> None:
    # The calendar-icon template's e-ink palette table, purple/brown warning,
    # and green-key background must not carry over (§12: shown as-is).
    prompt = countdown_hero_prompt("camping trip")
    assert "#00FF00" not in prompt
    assert "chroma" not in prompt.lower()
    assert "purple" not in prompt.lower()
    assert "Periwinkle" not in prompt
    assert "palette" not in prompt.lower()


def test_excited_prompt_is_a_comic_excitement_edit() -> None:
    prompt = excited_hero_prompt()
    assert "excitement and anticipation" in prompt
    assert "comic" in prompt
    assert "white" in prompt
    assert "text" in prompt


class _Generator:
    """Fake seam recording calls; distinct bytes for generate vs edit."""

    def __init__(self, *, fail_base: bool = False, fail_edit: bool = False) -> None:
        self.fail_base = fail_base
        self.fail_edit = fail_edit
        self.prompts: list[str] = []
        self.base_pngs: list[bytes | None] = []

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
        self.base_pngs.append(base_png)
        if self.fail_base if base_png is None else self.fail_edit:
            raise ImageGenerationError("image generation failed: Boom")
        return b"base-bytes" if base_png is None else b"excited-bytes"


def _app(tmp_path: Path, generate: _Generator) -> Flask:
    app = create_app()
    app.config["APP_STORAGE_PATH"] = tmp_path
    app.config["GENERATE_IMAGE_BYTES"] = generate
    return app


def _resolve(
    tmp_path: Path,
    generate: _Generator,
    collected: list[RenderedImage],
    *,
    excited: bool,
    item: tuple[str, str | None] = ("camping trip", None),
) -> str | None:
    with _app(tmp_path, generate).test_request_context():
        return make_countdown_hero_resolver(collected)(item, excited)


def test_calm_resolves_the_base_hero_only(tmp_path: Path) -> None:
    generate = _Generator()
    collected: list[RenderedImage] = []

    url = _resolve(tmp_path, generate, collected, excited=False)

    assert url == "/images/generated/1"
    assert generate.base_pngs == [None]  # one plain generation, no edit
    assert generate.prompts == [countdown_hero_prompt("camping trip")]
    assert [(r.id, r.spec.variant) for r in collected] == [(1, None)]
    spec = collected[0].spec
    assert spec.module == "Countdown"
    assert spec.item_description == "camping trip"
    assert (spec.width, spec.height) == (HERO_W, HERO_H)


def test_described_hero_keys_by_description_and_prompts_with_both(
    tmp_path: Path,
) -> None:
    # §7.1: the logical key stays icon_description-or-title; the prompt keeps
    # the title and carries the description as its parenthesized elaboration.
    generate = _Generator()
    collected: list[RenderedImage] = []

    url = _resolve(
        tmp_path,
        generate,
        collected,
        excited=False,
        item=("Camping!!", "a family camping trip"),
    )

    assert url == "/images/generated/1"
    assert collected[0].spec.item_description == "a family camping trip"
    assert generate.prompts == [
        countdown_hero_prompt("Camping!!", "a family camping trip")
    ]


def test_excited_edits_the_stored_base_in_a_second_call(tmp_path: Path) -> None:
    generate = _Generator()
    collected: list[RenderedImage] = []

    url = _resolve(tmp_path, generate, collected, excited=True)

    assert url == "/images/generated/2"
    # Sequential: the base generates first (no edit input), then the variant
    # edits the base's stored bytes.
    assert generate.base_pngs == [None, b"base-bytes"]
    assert generate.prompts == [
        countdown_hero_prompt("camping trip"),
        excited_hero_prompt(),
    ]
    assert [(r.id, r.spec.variant) for r in collected] == [(1, None), (2, "excited")]


def test_excited_reuses_a_warm_base_without_regenerating(tmp_path: Path) -> None:
    generate = _Generator()
    collected: list[RenderedImage] = []
    _resolve(tmp_path, generate, collected, excited=False)

    url = _resolve(tmp_path, generate, [], excited=True)

    assert url == "/images/generated/2"
    assert generate.base_pngs == [None, b"base-bytes"]  # base not regenerated


def test_edit_failure_falls_back_to_the_base_hero(tmp_path: Path) -> None:
    generate = _Generator(fail_edit=True)
    collected: list[RenderedImage] = []

    url = _resolve(tmp_path, generate, collected, excited=True)

    assert url == "/images/generated/1"
    assert [(r.id, r.spec.variant) for r in collected] == [(1, None)]


def test_base_failure_yields_none_and_skips_the_edit(tmp_path: Path) -> None:
    generate = _Generator(fail_base=True)
    collected: list[RenderedImage] = []

    url = _resolve(tmp_path, generate, collected, excited=True)

    assert url is None
    assert generate.base_pngs == [None]  # the edit was never attempted
    assert collected == []
