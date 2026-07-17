from collections.abc import Sequence
from pathlib import Path

from flask import Flask
from pydantic import SecretStr

from app import create_app
from app.images import ImageGenerationError, RenderedImage
from app.images.strip_icons import (
    DAY_STRIP_MODULE,
    STRIP_ICON_H,
    STRIP_ICON_W,
    excited_strip_prompt,
    make_strip_icon_resolver,
    strip_icon_prompt,
)

type _Request = tuple[tuple[str, str | None], bool]


def test_prompt_contains_the_title() -> None:
    prompt = strip_icon_prompt("Soccer practice")
    assert "“Soccer practice”" in prompt


def test_prompt_adds_icon_description_as_its_own_paragraph() -> None:
    # §6.4: the description elaborates the title instead of replacing it.
    prompt = strip_icon_prompt("S's game", "kids playing a soccer match")
    assert "“S's game”" in prompt
    assert "\n\nkids playing a soccer match\n\n" in prompt


def test_prompt_without_description_has_no_empty_paragraph() -> None:
    assert "\n\n\n" not in strip_icon_prompt("Soccer practice")


def test_prompt_carries_the_key_instructions() -> None:
    # The load-bearing pieces of the user-authored template: the full-bleed
    # opaque scene, the bold flat style, and the e-ink palette guidance.
    prompt = strip_icon_prompt("Soccer")
    assert "full bleed" in prompt
    assert "Do not draw a panel border" in prompt
    assert "200px square" in prompt
    assert "pure saturated primary colors" in prompt
    assert "Avoid purple and brown hues" in prompt
    assert "| Periwinkle | 35% red 65% blue |" in prompt
    # Crop safety: the display cover-crops (hardest on torn panels), so the
    # subject must sit within the central region with a croppable background
    # margin reserved around it.
    assert "central 40% of the image" in prompt
    assert "cropped away" in prompt


def test_prompt_has_no_chroma_key_background() -> None:
    # Strip art is opaque (module "DayStrip" is unkeyed, §9.1): the calendar
    # template's green-key tail must not carry over.
    prompt = strip_icon_prompt("Soccer")
    assert "#00FF00" not in prompt
    assert "chroma" not in prompt.lower()


def test_excited_prompt_is_a_comic_excitement_edit() -> None:
    prompt = excited_strip_prompt()
    assert "excitement and anticipation" in prompt
    assert "comic" in prompt
    assert "composition" in prompt
    assert "text" in prompt
    assert "no fine details" in prompt  # busy edits turn to noise at panel size


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
    requests: Sequence[_Request],
) -> dict[tuple[str, bool], str | None]:
    with _app(tmp_path, generate).test_request_context():
        return dict(make_strip_icon_resolver(collected)(requests))


def test_base_request_records_under_daystrip_module(tmp_path: Path) -> None:
    generate = _Generator()
    collected: list[RenderedImage] = []

    resolved = _resolve(tmp_path, generate, collected, [(("Soccer", None), False)])

    assert resolved == {("Soccer", False): "/images/generated/1"}
    assert generate.base_pngs == [None]  # one plain generation, no edit
    assert generate.prompts == [strip_icon_prompt("Soccer")]
    spec = collected[0].spec
    assert spec.module == DAY_STRIP_MODULE
    assert spec.item_description == "Soccer"
    assert (spec.width, spec.height) == (STRIP_ICON_W, STRIP_ICON_H)
    assert spec.variant is None


def test_excited_edits_the_stored_base_in_a_second_call(tmp_path: Path) -> None:
    generate = _Generator()
    collected: list[RenderedImage] = []

    resolved = _resolve(tmp_path, generate, collected, [(("Soccer", None), True)])

    assert resolved == {("Soccer", True): "/images/generated/2"}
    # Sequential batches: the base generates first (no edit input), then the
    # variant edits the base's stored bytes.
    assert generate.base_pngs == [None, b"base-bytes"]
    assert generate.prompts == [strip_icon_prompt("Soccer"), excited_strip_prompt()]
    assert [(r.id, r.spec.variant) for r in collected] == [(1, None), (2, "excited")]


def test_same_key_resolves_base_and_excited_to_distinct_urls(tmp_path: Path) -> None:
    # A recurring event on today AND another day: one logical key, two URLs —
    # the base art for the other day, the excited edit for today (§9.1).
    generate = _Generator()

    resolved = _resolve(
        tmp_path,
        generate,
        [],
        [(("Soccer", None), False), (("Soccer", None), True)],
    )

    assert resolved == {
        ("Soccer", False): "/images/generated/1",
        ("Soccer", True): "/images/generated/2",
    }
    assert generate.base_pngs == [None, b"base-bytes"]  # one base, one edit


def test_resolver_dedupes_requests_by_logical_key(tmp_path: Path) -> None:
    generate = _Generator()

    resolved = _resolve(
        tmp_path,
        generate,
        [],
        [
            (("S's game", "kids soccer match"), False),
            (("Soccer", "kids soccer match"), False),
        ],
    )

    # One logical image (§7.1): generated once, seeded by the first request's
    # prompt.
    assert resolved == {("kids soccer match", False): "/images/generated/1"}
    assert generate.prompts == [strip_icon_prompt("S's game", "kids soccer match")]


def test_edit_failure_falls_back_to_the_base_url(tmp_path: Path) -> None:
    generate = _Generator(fail_edit=True)
    collected: list[RenderedImage] = []

    resolved = _resolve(tmp_path, generate, collected, [(("Soccer", None), True)])

    assert resolved == {("Soccer", True): "/images/generated/1"}
    assert [(r.id, r.spec.variant) for r in collected] == [(1, None)]


def test_base_failure_yields_none_and_skips_the_edit(tmp_path: Path) -> None:
    generate = _Generator(fail_base=True)
    collected: list[RenderedImage] = []

    resolved = _resolve(
        tmp_path,
        generate,
        collected,
        [(("Soccer", None), False), (("Soccer", None), True)],
    )

    assert resolved == {("Soccer", False): None, ("Soccer", True): None}
    assert generate.base_pngs == [None]  # the edit was never attempted
    assert collected == []


def test_empty_requests_resolve_to_nothing(tmp_path: Path) -> None:
    generate = _Generator()

    assert _resolve(tmp_path, generate, [], []) == {}
    assert generate.prompts == []
