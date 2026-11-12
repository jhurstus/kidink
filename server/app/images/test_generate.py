import base64
from types import SimpleNamespace
from typing import Any

import openai
import pytest
from pydantic import SecretStr

from app.images.generate import generate_image_bytes, generation_size


def test_generation_size_scales_16x() -> None:
    # Exact-aspect large generation, both dimensions divisible by 16.
    assert generation_size(100, 60) == "1600x960"
    assert generation_size(60, 60) == "960x960"  # the day-strip icon box


def test_generation_size_clamps_hero_sizes_to_api_bounds() -> None:
    # Hero-sized records can't scale the full 16× — the scale clamps so the
    # size fits the API bounds, dimensions floored to multiples of 16, and the
    # aspect ratio capped at the API's 3:1 (2560x832 would be 3.08:1).
    assert generation_size(554, 366) == "2176x1440"  # the countdown hero box
    assert generation_size(460, 150) == "2496x832"  # aspect-cap case
    assert generation_size(400, 300) == "1920x1440"


def test_generation_size_rejects_degenerate_aspect() -> None:
    # An extreme aspect ratio would collapse the short dimension below the
    # API minimum once the long one is clamped.
    with pytest.raises(ValueError):
        generation_size(5000, 10)


class _FakeImages:
    """Records SDK ``images`` calls; returns one base64 PNG payload."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _result(self) -> SimpleNamespace:
        b64 = base64.b64encode(b"png-bytes").decode()
        return SimpleNamespace(data=[SimpleNamespace(b64_json=b64)])

    def generate(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(("generate", kwargs))
        return self._result()

    def edit(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(("edit", kwargs))
        return self._result()


@pytest.mark.parametrize(
    ("transparent", "background"), [(True, "transparent"), (False, "opaque")]
)
@pytest.mark.parametrize("base_png", [None, b"base"])
def test_seam_requests_the_background_as_png(
    monkeypatch: pytest.MonkeyPatch,
    transparent: bool,
    background: str,
    base_png: bytes | None,
) -> None:
    # §7.2: the background is always explicit (never the API's "auto"), on
    # both the generate and the edit endpoint, and the output is PNG.
    images = _FakeImages()
    monkeypatch.setattr(
        openai, "OpenAI", lambda api_key: SimpleNamespace(images=images)
    )

    png = generate_image_bytes(
        SecretStr("sk-test"),
        prompt="p",
        size="960x960",
        model="m",
        transparent=transparent,
        base_png=base_png,
    )

    assert png == b"png-bytes"
    [(endpoint, kwargs)] = images.calls
    assert endpoint == ("generate" if base_png is None else "edit")
    assert kwargs["background"] == background
    assert kwargs["output_format"] == "png"
