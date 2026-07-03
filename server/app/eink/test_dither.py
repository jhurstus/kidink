import numpy as np
import pytest

from app.eink.dither import _c_div, quantize, saturate
from app.eink.palette import BLACK, GREEN, PALETTE_RGB, WHITE


def _luma(pixel) -> float:
    r, g, b = (float(v) for v in pixel)
    return 0.299 * r + 0.587 * g + 0.114 * b


def _gray_row(value: int, width: int) -> np.ndarray:
    return np.full((1, width, 3), value, dtype=np.uint8)


def test_c_div_truncates_toward_zero() -> None:
    # Python // floors; the firmware's / truncates. -635/26 = -24.42.
    assert _c_div(-635, 26) == -24
    assert (-635) // 26 == -25  # the trap _c_div exists to avoid
    assert _c_div(635, 26) == 24
    assert _c_div(-1, 26) == 0
    assert _c_div(0, 26) == 0


def test_reduced_parity_fixture_gray_row() -> None:
    # Hand-computed against the firmware arithmetic (rgb metric, strength 1):
    # 128 -> white, err -127, east tap trunc(5*-127/26) = -24
    # 104 -> black, err +104, east tap trunc(5*104/26) = +20
    # 148 -> white, err -107, east tap trunc(5*-107/26) = -20
    # 108 -> black
    out = quantize(_gray_row(128, 4), "reduced", "rgb")
    assert out.tolist() == [[WHITE, BLACK, WHITE, BLACK]]


def test_fs_fixture_gray_row() -> None:
    # 128 -> white (-127, east 7/16 -> -55), 73 -> black (+73 -> +31),
    # 159 -> white (-96 -> -42), 86 -> black.
    out = quantize(_gray_row(128, 4), "fs", "rgb")
    assert out.tolist() == [[WHITE, BLACK, WHITE, BLACK]]


def test_zero_strength_equals_no_dither() -> None:
    rng = np.random.default_rng(42)
    img = rng.integers(0, 256, size=(16, 16, 3), dtype=np.uint8)
    assert np.array_equal(
        quantize(img, "reduced", "ycc", strength=0.0),
        quantize(img, "none", "ycc"),
    )


def test_overdriven_strength_does_not_crash() -> None:
    # strength > 1 scales the error past +-255; it must clamp, not IndexError.
    rng = np.random.default_rng(3)
    img = rng.integers(0, 256, size=(8, 8, 3), dtype=np.uint8)
    out = quantize(img, "fs", "ycc", strength=2.0)
    assert out.shape == (8, 8)


def test_error_diffusion_spans_multiple_rows() -> None:
    # A 2x2 gray block: the south taps must carry error into row 1 (the two
    # rows must not quantize identically-independently).
    out = quantize(np.full((2, 2, 3), 128, dtype=np.uint8), "reduced", "rgb")
    independent = np.vstack(
        [quantize(_gray_row(128, 2), "reduced", "rgb") for _ in range(2)]
    )
    assert not np.array_equal(out, independent)


def test_single_pixel_image_does_not_crash() -> None:
    for mode in ("reduced", "fs", "ordered", "none"):
        out = quantize(np.full((1, 1, 3), 200, dtype=np.uint8), mode, "ycc")
        assert out.shape == (1, 1)


def test_pure_palette_colors_are_fixed_points_for_every_mode() -> None:
    for mode in ("reduced", "fs", "ordered", "none"):
        for idx, color in enumerate(PALETTE_RGB):
            img = np.full((8, 8, 3), color, dtype=np.uint8)
            assert np.all(quantize(img, mode, "ycc") == idx), mode


def test_ordered_gray_yields_black_white_mix() -> None:
    out = quantize(np.full((16, 16, 3), 128, dtype=np.uint8), "ordered")
    values = set(np.unique(out))
    assert values == {BLACK, WHITE}


def test_ordered_renders_pale_tint_as_chromatic_dots() -> None:
    # monday_burst.png's light-green background. Damped error diffusion
    # (reduced) drops the tint entirely; ordered must produce a white field
    # with sparse green dots at roughly the tint's coverage.
    img = np.full((32, 32, 3), (231, 246, 228), dtype=np.uint8)
    out = quantize(img, "ordered")
    values, counts = np.unique(out, return_counts=True)
    coverage = dict(zip(values.tolist(), counts.tolist(), strict=True))
    assert set(coverage) == {WHITE, GREEN}
    green_frac = coverage[GREEN] / out.size
    assert 0.03 < green_frac < 0.4


def test_bright_green_renders_green_dominant() -> None:
    # monday_burst.png's lettering color must not come out gray-green. Its
    # true ink decomposition is ~45% green + 32% white + 24% black, so green
    # must be the dominant ink but need not exceed half the pixels.
    img = np.full((32, 32, 3), (81, 195, 85), dtype=np.uint8)
    for mode in ("ordered", "fs", "reduced"):
        out = quantize(img, mode, "ycc")
        values, counts = np.unique(out, return_counts=True)
        coverage = dict(zip(values.tolist(), counts.tolist(), strict=True))
        assert coverage.get(GREEN, 0) == max(coverage.values()), mode
        assert coverage.get(GREEN, 0) / out.size > 0.35, mode


def _aa_edge_image() -> np.ndarray:
    # Black region | one mid-gray antialiasing column | white region.
    img = np.full((16, 16, 3), 255, dtype=np.uint8)
    img[:, :7] = 0
    img[:, 7] = 128
    return img


def test_edge_snap_thresholds_aa_ramp_uniformly() -> None:
    # The AA column sits on a strong luma step, so it must be committed to a
    # single ink instead of dithering along the edge. With default stem
    # darkening (gamma 1.5) the 50%-coverage column bolds to black; with
    # gamma 1.0 it thresholds at 50% luma and goes white.
    out = quantize(_aa_edge_image(), "ordered")
    assert np.all(out[:, 7] == BLACK)
    assert np.all(out[:, :7] == BLACK)
    assert np.all(out[:, 8:] == WHITE)

    unboosted = quantize(_aa_edge_image(), "ordered", edge_gamma=1.0)
    assert np.all(unboosted[:, 7] == WHITE)


def test_edge_snap_zero_disables_thresholding() -> None:
    out = quantize(_aa_edge_image(), "ordered", edge_snap=0)
    # Without the snap, the ordered dither scatters the AA column.
    assert set(np.unique(out[:, 7])) == {BLACK, WHITE}


def test_edge_snap_leaves_flat_regions_dithered() -> None:
    flat = np.full((16, 16, 3), 128, dtype=np.uint8)
    assert np.array_equal(
        quantize(flat, "ordered"), quantize(flat, "ordered", edge_snap=0)
    )


def test_ordered_suppresses_stray_dots_in_near_pure_fields() -> None:
    # A field this close to white would pick a 2/64 black sprinkle without
    # the minimum-ratio floor; it must render as solid white.
    field = np.full((16, 16, 3), 244, dtype=np.uint8)
    assert np.all(quantize(field, "ordered") == WHITE)


def test_saturate_boosts_chromatic_colors_and_preserves_luma() -> None:
    img = np.full((1, 1, 3), (81, 195, 85), dtype=np.uint8)
    out = saturate(img, 1.4)[0, 0]
    # Pushed toward pure green: more G, less R/B...
    assert out[1] > 205 and out[0] < 65 and out[2] < 70
    # ...but no lighter or darker.
    assert abs(_luma(out) - _luma((81, 195, 85))) <= 1.5


def test_saturate_leaves_near_neutrals_untouched() -> None:
    # Vibrance, not plain saturation: paper cream, grays, and text ink must
    # not acquire phantom color (a linear boost dirties the cream page).
    for color in [(225, 220, 202), (128, 128, 128), (20, 20, 20), (253, 253, 253)]:
        img = np.full((1, 1, 3), color, dtype=np.uint8)
        out = saturate(img, 1.4)[0, 0]
        assert np.max(np.abs(out.astype(int) - np.array(color))) <= 2, color


def test_saturate_factor_one_is_identity() -> None:
    rng = np.random.default_rng(11)
    img = rng.integers(0, 256, size=(8, 8, 3), dtype=np.uint8)
    assert np.array_equal(saturate(img, 1.0), img)


def test_saturated_bright_green_dithers_denser() -> None:
    img = np.full((16, 16, 3), (81, 195, 85), dtype=np.uint8)
    plain = quantize(img, "ordered")
    boosted = quantize(saturate(img, 1.4), "ordered")
    plain_frac = np.count_nonzero(plain == GREEN) / plain.size
    boosted_frac = np.count_nonzero(boosted == GREEN) / boosted.size
    assert boosted_frac > plain_frac
    assert boosted_frac > 0.55


def test_saturated_cream_field_stays_achromatic() -> None:
    img = np.full((16, 16, 3), (225, 220, 202), dtype=np.uint8)
    out = quantize(saturate(img, 1.4), "ordered")
    assert set(np.unique(out)) <= {BLACK, WHITE}


def test_ordered_is_deterministic() -> None:
    rng = np.random.default_rng(7)
    img = rng.integers(0, 256, size=(16, 16, 3), dtype=np.uint8)
    assert np.array_equal(quantize(img, "ordered"), quantize(img, "ordered"))


def test_rejects_bad_input_shape() -> None:
    with pytest.raises(ValueError, match="expected"):
        quantize(np.zeros((4, 4), dtype=np.uint8), "none", "rgb")
