import numpy as np

from app.eink.palette import PALETTE_RGB
from app.eink.testcard import make_test_card


def test_test_card_is_panel_sized() -> None:
    assert make_test_card().size == (1600, 1200)


def test_test_card_contains_all_six_pure_inks() -> None:
    pixels = np.asarray(make_test_card())
    flat = {tuple(px) for px in pixels.reshape(-1, 3)[::97]}  # sample for speed
    for color in PALETTE_RGB:
        assert tuple(color) in flat, f"missing pure {tuple(color)}"


def test_test_card_is_deterministic() -> None:
    assert make_test_card().tobytes() == make_test_card().tobytes()
