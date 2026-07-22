from app.captions.select import SelectedCaption, select_caption

_CAPTIONS = ["caption 0", "caption 1", "caption 2"]


def test_empty_list_shows_nothing() -> None:
    assert select_caption([], None, None) is None
    assert select_caption([], 1, 2) is None  # even a pinned date goes silent


def test_first_ever_render_starts_the_rotation() -> None:
    assert select_caption(_CAPTIONS, None, None) == SelectedCaption(
        0, "caption 0", fresh=True
    )


def test_unpinned_date_takes_the_caption_after_the_pointer() -> None:
    assert select_caption(_CAPTIONS, None, 0) == SelectedCaption(
        1, "caption 1", fresh=True
    )


def test_next_wraps_around_the_list() -> None:
    assert select_caption(_CAPTIONS, None, 2) == SelectedCaption(
        0, "caption 0", fresh=True
    )


def test_pinned_date_repeats_its_caption() -> None:
    # The pin wins regardless of where the pointer has moved since.
    assert select_caption(_CAPTIONS, 1, 2) == SelectedCaption(
        1, "caption 1", fresh=False
    )


def test_pin_survives_a_shrunken_list() -> None:
    # An admin delete can leave a pin past the end; the modulo keeps the
    # lookup in range instead of raising.
    assert select_caption(_CAPTIONS, 5, None) == SelectedCaption(
        2, "caption 2", fresh=False
    )


def test_pointer_survives_a_shrunken_list() -> None:
    assert select_caption(_CAPTIONS, None, 7) == SelectedCaption(
        2, "caption 2", fresh=True
    )
