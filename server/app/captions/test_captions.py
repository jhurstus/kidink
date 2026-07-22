from datetime import date
from pathlib import Path

from app.captions.captions import (
    Assignment,
    add_captions,
    clear_rotation,
    delete_caption,
    get_assignment,
    get_last_index,
    latest_assignment,
    list_captions,
    open_captions_db,
    record_assignment,
    update_caption,
)


def test_add_and_list_preserves_insertion_order(tmp_path: Path) -> None:
    conn = open_captions_db(tmp_path)
    try:
        add_captions(conn, ["caption A", "caption B"])
        add_captions(conn, ["caption C"])
        texts = [caption.text for caption in list_captions(conn)]
        assert texts == ["caption A", "caption B", "caption C"]
    finally:
        conn.close()


def test_add_skips_blank_and_comment_lines_and_strips(tmp_path: Path) -> None:
    conn = open_captions_db(tmp_path)
    try:
        added = add_captions(conn, ["  caption A  ", "", "   ", "# comment", "B"])
        assert added == 2
        assert [caption.text for caption in list_captions(conn)] == ["caption A", "B"]
    finally:
        conn.close()


def test_update_replaces_text_and_keeps_id(tmp_path: Path) -> None:
    conn = open_captions_db(tmp_path)
    try:
        add_captions(conn, ["caption A", "caption B"])
        first = list_captions(conn)[0]
        update_caption(conn, first.id, "caption A edited")
        listed = list_captions(conn)
        assert listed[0].text == "caption A edited"
        # The id (and thus list position) is stable across an edit.
        assert listed[0].id == first.id
    finally:
        conn.close()


def test_delete_removes_and_tolerates_absence(tmp_path: Path) -> None:
    conn = open_captions_db(tmp_path)
    try:
        add_captions(conn, ["caption A", "caption B"])
        first = list_captions(conn)[0]
        delete_caption(conn, first.id)
        assert [caption.text for caption in list_captions(conn)] == ["caption B"]
        delete_caption(conn, first.id)  # deleting an absent id is a no-op
    finally:
        conn.close()


def test_assignments_round_trip_and_advance_the_pointer(tmp_path: Path) -> None:
    conn = open_captions_db(tmp_path)
    try:
        assert get_assignment(conn, date(2026, 7, 22)) is None
        assert get_last_index(conn) is None
        assert latest_assignment(conn) is None

        record_assignment(conn, date(2026, 7, 22), 0)
        record_assignment(conn, date(2026, 7, 24), 1)
        assert get_assignment(conn, date(2026, 7, 22)) == 0
        assert get_assignment(conn, date(2026, 7, 24)) == 1
        assert get_assignment(conn, date(2026, 7, 23)) is None
        assert get_last_index(conn) == 1
        # Latest is by assignment order (rowid), not calendar order.
        record_assignment(conn, date(2026, 7, 23), 2)
        assert latest_assignment(conn) == Assignment(
            day=date(2026, 7, 23), caption_index=2
        )
        assert get_last_index(conn) == 2
    finally:
        conn.close()


def test_record_assignment_first_writer_wins(tmp_path: Path) -> None:
    # A concurrent render can race a day's first pin; the second write must
    # change nothing (neither the pin nor the pointer).
    conn = open_captions_db(tmp_path)
    try:
        record_assignment(conn, date(2026, 7, 22), 0)
        record_assignment(conn, date(2026, 7, 22), 5)
        assert get_assignment(conn, date(2026, 7, 22)) == 0
        assert get_last_index(conn) == 0
    finally:
        conn.close()


def test_clear_rotation_forgets_pins_and_pointer(tmp_path: Path) -> None:
    conn = open_captions_db(tmp_path)
    try:
        record_assignment(conn, date(2026, 7, 22), 0)
        clear_rotation(conn)
        assert get_assignment(conn, date(2026, 7, 22)) is None
        assert get_last_index(conn) is None
        assert latest_assignment(conn) is None
        clear_rotation(conn)  # clearing an empty rotation is a no-op
    finally:
        conn.close()
