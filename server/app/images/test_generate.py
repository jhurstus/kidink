import pytest

from app.images.generate import generation_size


def test_generation_size_scales_16x() -> None:
    # 100×60 → exact-aspect large generation, both dimensions divisible by 16.
    assert generation_size(100, 60) == "1600x960"


def test_generation_size_rejects_out_of_bounds() -> None:
    # Hero-sized records will need a different rule (§2.2 of the plan); until
    # then the helper fails loudly rather than sending an invalid size.
    with pytest.raises(ValueError):
        generation_size(400, 300)
