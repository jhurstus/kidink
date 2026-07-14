import pytest

from app.images.generate import generation_size


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
