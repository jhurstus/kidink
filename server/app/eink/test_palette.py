import numpy as np

from app.eink.palette import (
    BLACK,
    BLUE,
    GREEN,
    PALETTE_RGB,
    RED,
    WHITE,
    YELLOW,
    indices_to_rgb,
    nearest_indices,
)


def _one(pixel: tuple[int, int, int], metric) -> int:
    rgb = np.array([[pixel]], dtype=np.uint8)
    return int(nearest_indices(rgb, metric)[0, 0])


def test_pure_palette_colors_map_to_themselves_under_both_metrics() -> None:
    for metric in ("rgb", "ycc"):
        for idx, color in enumerate(PALETTE_RGB):
            assert _one(tuple(color), metric) == idx


def test_rgb_tie_breaks_to_lowest_palette_index() -> None:
    # (128, 0, 128) is exactly equidistant to red and blue in plain RGB
    # (32513 each; black is 32768) — the firmware's strict < keeps red (3).
    assert _one((128, 0, 128), "rgb") == RED


def test_warm_gray_snaps_to_yellow_under_rgb_but_white_under_ycc() -> None:
    # The AA-edge speckle failure mode: a slightly warm mid-gray is nearer
    # yellow than black/white in plain RGB. Separating luma from chroma
    # keeps it achromatic.
    assert _one((140, 140, 100), "rgb") == YELLOW
    assert _one((140, 140, 100), "ycc") == WHITE


def test_bright_green_stays_green_under_ycc() -> None:
    # monday_burst.png's lettering color. The earlier 2x chroma up-weight put
    # this on the green/white boundary and it rendered gray-green on the
    # panel; plain YCbCr keeps it decisively green.
    assert _one((81, 195, 85), "ycc") == GREEN
    assert _one((81, 195, 85), "rgb") == GREEN


def test_pure_grays_stay_achromatic_under_ycc() -> None:
    ramp = np.arange(256, dtype=np.uint8)
    grays = np.stack([ramp, ramp, ramp], axis=-1)[None, :, :]
    indices = nearest_indices(grays, "ycc")
    assert set(np.unique(indices)) <= {BLACK, WHITE}


def test_primary_ink_neighborhoods_survive_ycc_metric() -> None:
    # Slightly-off saturated colors must still map to their ink.
    assert _one((230, 30, 40), "ycc") == RED
    assert _one((30, 40, 220), "ycc") == BLUE
    assert _one((40, 210, 30), "ycc") == GREEN
    assert _one((240, 230, 40), "ycc") == YELLOW


def test_indices_to_rgb_roundtrip() -> None:
    indices = np.array([[0, 1], [4, 5]], dtype=np.uint8)
    rgb = indices_to_rgb(indices)
    assert rgb.shape == (2, 2, 3)
    assert tuple(rgb[0, 0]) == (0, 0, 0)
    assert tuple(rgb[1, 0]) == (0, 0, 255)
