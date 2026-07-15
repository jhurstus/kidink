from pathlib import Path

from app.joke.jokes import (
    add_jokes,
    count_jokes,
    delete_joke,
    get_joke,
    list_jokes,
    open_jokes_db,
    stored_jokes,
    update_joke,
)


def test_add_and_list_preserves_insertion_order(tmp_path: Path) -> None:
    conn = open_jokes_db(tmp_path)
    try:
        add_jokes(conn, ["joke A", "joke B"])
        add_jokes(conn, ["joke C"])
        texts = [joke.text for joke in list_jokes(conn)]
        assert texts == ["joke A", "joke B", "joke C"]
        assert count_jokes(conn) == 3
    finally:
        conn.close()


def test_add_skips_blank_and_comment_lines_and_strips(tmp_path: Path) -> None:
    conn = open_jokes_db(tmp_path)
    try:
        added = add_jokes(conn, ["  joke A  ", "", "   ", "# a comment", "joke B"])
        assert added == 2
        assert [joke.text for joke in list_jokes(conn)] == ["joke A", "joke B"]
    finally:
        conn.close()


def test_update_replaces_text_and_keeps_id(tmp_path: Path) -> None:
    conn = open_jokes_db(tmp_path)
    try:
        add_jokes(conn, ["joke A", "joke B"])
        first = list_jokes(conn)[0]
        update_joke(conn, first.id, "joke A edited")
        edited = get_joke(conn, first.id)
        assert edited is not None
        assert edited.text == "joke A edited"
        # The id (and thus list position) is stable across an edit.
        assert list_jokes(conn)[0].id == first.id
    finally:
        conn.close()


def test_delete_removes_and_tolerates_absence(tmp_path: Path) -> None:
    conn = open_jokes_db(tmp_path)
    try:
        add_jokes(conn, ["joke A", "joke B"])
        first = list_jokes(conn)[0]
        delete_joke(conn, first.id)
        assert [joke.text for joke in list_jokes(conn)] == ["joke B"]
        delete_joke(conn, first.id)  # deleting an absent id is a no-op
        assert get_joke(conn, first.id) is None
    finally:
        conn.close()


def test_stored_jokes_reads_saved_values(tmp_path: Path) -> None:
    conn = open_jokes_db(tmp_path)
    add_jokes(conn, ["joke A", "joke B"])
    conn.close()

    assert stored_jokes(tmp_path) == ["joke A", "joke B"]


def test_stored_jokes_without_a_db_creates_nothing(tmp_path: Path) -> None:
    # /render reads jokes on every request; a fresh (or poisoned, see
    # test_render._app_with_ics) storage root must stay untouched.
    assert stored_jokes(tmp_path) == []
    assert not (tmp_path / "sqlite.db").exists()

    missing = tmp_path / "never-created"
    assert stored_jokes(missing) == []
    assert not missing.exists()
