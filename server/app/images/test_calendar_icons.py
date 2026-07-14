import io
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from PIL import Image
from pydantic import SecretStr

from app import create_app
from app.images import RenderedImage
from app.images.calendar_icons import (
    calendar_icon_prompt,
    make_calendar_icon_resolver,
)


def test_prompt_contains_the_title() -> None:
    prompt = calendar_icon_prompt("Soccer practice")
    assert "“Soccer practice”" in prompt


def test_prompt_adds_icon_description_as_its_own_paragraph() -> None:
    # §6.4: the description elaborates the title instead of replacing it.
    prompt = calendar_icon_prompt("S's game", "kids playing a soccer match")
    assert "“S's game”" in prompt
    assert "\n\nkids playing a soccer match\n\n" in prompt


def test_prompt_without_description_has_no_empty_paragraph() -> None:
    assert "\n\n\n" not in calendar_icon_prompt("Soccer practice")


def test_prompt_carries_the_key_instructions() -> None:
    # The load-bearing pieces of the user-authored template: display size,
    # e-ink palette guidance, and the chroma-key background.
    prompt = calendar_icon_prompt("Soccer")
    assert "60px wide by 60px tall" in prompt
    assert "#00FF00" in prompt
    assert "Avoid purple and brown hues" in prompt
    assert "| Periwinkle | 35% red 65% blue |" in prompt


def _keyable_png() -> bytes:
    """A red square on a pure-green background — keys cleanly."""
    pixels = np.tile(np.array((0, 255, 0), np.uint8), (480, 480, 1))
    pixels[40:440, 40:440] = (220, 40, 40)
    out = io.BytesIO()
    Image.fromarray(pixels, "RGB").save(out, format="PNG")
    return out.getvalue()


class _Generator:
    """Fake seam recording each generation's prompt."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

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
        return _keyable_png()


def _resolve(
    tmp_path: Path,
    generate: _Generator,
    collected: list[RenderedImage],
    items: Sequence[tuple[str, str | None]],
) -> dict[str, str | None]:
    app = create_app()
    app.config["APP_STORAGE_PATH"] = tmp_path
    app.config["GENERATE_IMAGE_BYTES"] = generate
    with app.test_request_context():
        return dict(make_calendar_icon_resolver(collected)(items))


def test_resolver_keys_by_description_and_prompts_with_title(tmp_path: Path) -> None:
    # §7.1: the logical key stays icon_description-or-title; the prompt keeps
    # the title and carries the description as its parenthesized elaboration.
    generate = _Generator()
    collected: list[RenderedImage] = []

    resolved = _resolve(
        tmp_path, generate, collected, [("S's game", "kids soccer match")]
    )

    assert resolved == {"kids soccer match": "/images/generated/1"}
    assert collected[0].spec.item_description == "kids soccer match"
    assert generate.prompts == [calendar_icon_prompt("S's game", "kids soccer match")]


def test_resolver_dedupes_items_by_logical_key(tmp_path: Path) -> None:
    generate = _Generator()

    resolved = _resolve(
        tmp_path,
        generate,
        [],
        [("S's game", "kids soccer match"), ("Soccer", "kids soccer match")],
    )

    # One logical image (§7.1): generated once, seeded by the first item's prompt.
    assert resolved == {"kids soccer match": "/images/generated/1"}
    assert generate.prompts == [calendar_icon_prompt("S's game", "kids soccer match")]
