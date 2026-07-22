import threading
from datetime import date, timedelta
from pathlib import Path

from app.captions.captions import (
    add_captions,
    delete_caption,
    get_assignment,
    get_last_index,
    list_captions,
    open_captions_db,
)
from app.captions.provider import make_caption_provider

_DAY = date(2026, 7, 22)


def _seed(tmp_path: Path, texts: list[str]) -> None:
    conn = open_captions_db(tmp_path)
    try:
        add_captions(conn, texts)
    finally:
        conn.close()


def _stored(tmp_path: Path, day: date) -> tuple[int | None, int | None]:
    """(the day's pin, the rotation pointer) as stored."""
    conn = open_captions_db(tmp_path)
    try:
        return get_assignment(conn, day), get_last_index(conn)
    finally:
        conn.close()


def test_first_render_pins_the_first_caption(tmp_path: Path) -> None:
    _seed(tmp_path, ["caption A", "caption B"])

    assert make_caption_provider(tmp_path, _DAY)() == "caption A"
    assert _stored(tmp_path, _DAY) == (0, 0)


def test_repeat_renders_reuse_the_pin(tmp_path: Path) -> None:
    _seed(tmp_path, ["caption A", "caption B"])

    make_caption_provider(tmp_path, _DAY)()
    assert make_caption_provider(tmp_path, _DAY)() == "caption A"
    assert _stored(tmp_path, _DAY) == (0, 0)


def test_each_new_date_takes_the_next_caption(tmp_path: Path) -> None:
    _seed(tmp_path, ["caption A", "caption B", "caption C"])

    assert make_caption_provider(tmp_path, _DAY)() == "caption A"
    next_day = date(2026, 7, 23)
    assert make_caption_provider(tmp_path, next_day)() == "caption B"
    assert _stored(tmp_path, next_day) == (1, 1)


def test_out_of_order_renders_pin_in_assignment_order(tmp_path: Path) -> None:
    # The motivating scenario (§10.5): show on day X, preview X+2, then
    # preview X+1 - each unpinned date takes the next caption in the order it
    # is rendered, and every date keeps what it was first shown.
    _seed(tmp_path, ["caption A", "caption B", "caption C"])
    x, x1, x2 = _DAY, date(2026, 7, 23), date(2026, 7, 24)

    assert make_caption_provider(tmp_path, x)() == "caption A"
    assert make_caption_provider(tmp_path, x2)() == "caption B"
    assert make_caption_provider(tmp_path, x1)() == "caption C"
    # Re-renders in any order stick to the pins.
    assert make_caption_provider(tmp_path, x2)() == "caption B"
    assert make_caption_provider(tmp_path, x1)() == "caption C"
    assert make_caption_provider(tmp_path, x)() == "caption A"


def test_rotation_wraps_across_dates(tmp_path: Path) -> None:
    _seed(tmp_path, ["caption A", "caption B"])

    days = [date(2026, 7, 22 + i) for i in range(3)]
    assert [make_caption_provider(tmp_path, d)() for d in days] == [
        "caption A",
        "caption B",
        "caption A",
    ]


def test_without_a_db_returns_none_and_creates_nothing(tmp_path: Path) -> None:
    assert make_caption_provider(tmp_path, _DAY)() is None
    assert not (tmp_path / "sqlite.db").exists()


def test_empty_caption_table_pins_nothing(tmp_path: Path) -> None:
    _seed(tmp_path, [])

    assert make_caption_provider(tmp_path, _DAY)() is None
    assert _stored(tmp_path, _DAY) == (None, None)


def test_pinned_date_survives_list_shrinking(tmp_path: Path) -> None:
    # A pin past the end of a since-shrunken list reads modulo the current
    # length - no error, just a substitute line.
    _seed(tmp_path, ["caption A", "caption B"])
    make_caption_provider(tmp_path, _DAY)()
    day2 = date(2026, 7, 23)
    assert make_caption_provider(tmp_path, day2)() == "caption B"

    conn = open_captions_db(tmp_path)
    try:
        delete_caption(conn, list_captions(conn)[1].id)
    finally:
        conn.close()

    assert make_caption_provider(tmp_path, day2)() == "caption A"  # 1 % 1 == 0


def test_concurrent_first_renders_take_distinct_captions(tmp_path: Path) -> None:
    # Allocation must serialize: simultaneous first renders of *different*
    # dates (each on its own connection) may not read the same rotation
    # pointer and duplicate a slot - all eight must come away with distinct
    # consecutive captions, whatever order the transactions win in.
    _seed(tmp_path, [f"caption {i}" for i in range(8)])
    days = [date(2026, 7, 20) + timedelta(days=i) for i in range(8)]
    barrier = threading.Barrier(len(days))
    results: dict[date, str | None] = {}

    def render(day: date) -> None:
        barrier.wait()
        results[day] = make_caption_provider(tmp_path, day)()

    threads = [threading.Thread(target=render, args=(day,)) for day in days]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results.values()) == [f"caption {i}" for i in range(8)]


def test_concurrent_first_renders_of_one_day_share_its_pin(tmp_path: Path) -> None:
    # Simultaneous first renders of the *same* date must converge on a single
    # pin: one assignment row, one pointer advance, identical captions.
    _seed(tmp_path, ["caption A", "caption B"])
    barrier = threading.Barrier(6)
    results: list[str | None] = [None] * 6

    def render(i: int) -> None:
        barrier.wait()
        results[i] = make_caption_provider(tmp_path, _DAY)()

    threads = [threading.Thread(target=render, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == ["caption A"] * 6
    assert _stored(tmp_path, _DAY) == (0, 0)
    conn = open_captions_db(tmp_path)
    try:
        rows = conn.execute("SELECT COUNT(*) FROM caption_assignments").fetchone()[0]
        assert rows == 1
    finally:
        conn.close()


def test_emptied_list_goes_silent_even_for_pinned_dates(tmp_path: Path) -> None:
    _seed(tmp_path, ["caption A"])
    make_caption_provider(tmp_path, _DAY)()

    conn = open_captions_db(tmp_path)
    try:
        delete_caption(conn, list_captions(conn)[0].id)
    finally:
        conn.close()

    assert make_caption_provider(tmp_path, _DAY)() is None
