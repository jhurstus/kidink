import numpy as np
import pytest

from app.eink.pack import emit_header, pack_pixels


def test_pack_two_rows_high_nibble_is_left_pixel() -> None:
    indices = np.array([[0, 1, 2, 3], [4, 5, 0, 0]], dtype=np.uint8)
    # nibble = index << 1: (0,2) (4,6) / (8,A) (0,0)
    assert pack_pixels(indices) == bytes([0x02, 0x46, 0x8A, 0x00])


def test_pack_odd_width_pads_with_white() -> None:
    indices = np.array([[3]], dtype=np.uint8)
    # left nibble red (3<<1=6), right nibble padded white (1<<1=2)
    assert pack_pixels(indices) == bytes([0x62])


def test_pack_length_is_row_stride_times_height() -> None:
    indices = np.zeros((3, 5), dtype=np.uint8)
    assert len(pack_pixels(indices)) == 3 * 3  # ceil(5/2) == 3


def test_pack_full_panel_size() -> None:
    indices = np.ones((1200, 1600), dtype=np.uint8)
    assert len(pack_pixels(indices)) == 960_000


def test_pack_rejects_out_of_range_indices() -> None:
    with pytest.raises(ValueError, match="0..5"):
        pack_pixels(np.array([[6]], dtype=np.uint8))


def test_emit_header_shape() -> None:
    packed = pack_pixels(np.array([[0, 1], [2, 3]], dtype=np.uint8))
    header = emit_header(packed, 2, 2, name="mockup")
    assert "const uint8_t mockup[] PROGMEM = {" in header
    assert "const uint16_t mockup_w = 2;" in header
    assert "const uint16_t mockup_h = 2;" in header
    assert "#pragma once" in header
    assert "0x02," in header and "0x46," in header


def test_emit_header_rejects_wrong_length() -> None:
    with pytest.raises(ValueError, match="expected"):
        emit_header(b"\x00\x00\x00", 2, 2)
