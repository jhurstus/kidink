from datetime import date, timedelta
from pathlib import Path

from app.dinner.overrides import (
    clear_override,
    get_override,
    open_meals_db,
    overrides_for,
    set_override,
    stored_override,
)

DAY = date(2026, 6, 3)


def test_set_and_get_roundtrip(tmp_path: Path) -> None:
    conn = open_meals_db(tmp_path)
    try:
        assert get_override(conn, DAY) is None
        set_override(conn, DAY, "Pizza night")
        assert get_override(conn, DAY) == "Pizza night"
    finally:
        conn.close()


def test_set_replaces_an_existing_override(tmp_path: Path) -> None:
    conn = open_meals_db(tmp_path)
    try:
        set_override(conn, DAY, "Pizza night")
        set_override(conn, DAY, "Soup")
        assert get_override(conn, DAY) == "Soup"
    finally:
        conn.close()


def test_clear_deletes_and_tolerates_absence(tmp_path: Path) -> None:
    conn = open_meals_db(tmp_path)
    try:
        set_override(conn, DAY, "Pizza night")
        clear_override(conn, DAY)
        assert get_override(conn, DAY) is None
        clear_override(conn, DAY)  # clearing a clean day is a no-op
    finally:
        conn.close()


def test_overrides_for_returns_only_overridden_days(tmp_path: Path) -> None:
    days = [DAY + timedelta(days=i) for i in range(4)]
    conn = open_meals_db(tmp_path)
    try:
        set_override(conn, days[1], "Pizza night")
        set_override(conn, days[3], "Soup")
        assert overrides_for(conn, days) == {days[1]: "Pizza night", days[3]: "Soup"}
        assert overrides_for(conn, []) == {}
    finally:
        conn.close()


def test_stored_override_reads_a_saved_value(tmp_path: Path) -> None:
    conn = open_meals_db(tmp_path)
    set_override(conn, DAY, "Pizza night")
    conn.close()

    assert stored_override(tmp_path, DAY) == "Pizza night"
    assert stored_override(tmp_path, DAY + timedelta(days=1)) is None


def test_stored_override_without_a_db_creates_nothing(tmp_path: Path) -> None:
    # /render reads overrides on every request; a fresh (or poisoned, see
    # test_render._app_with_ics) storage root must stay untouched.
    assert stored_override(tmp_path, DAY) is None
    assert not (tmp_path / "sqlite.db").exists()

    missing = tmp_path / "never-created"
    assert stored_override(missing, DAY) is None
    assert not missing.exists()
