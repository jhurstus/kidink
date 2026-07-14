"""Per-date meal-name overrides (the ``/admin/meals`` page, spec §13 adjunct).

One row per overridden date. An override replaces the feed-derived combined
meal name for that date wholesale and keeps winning even if the feed's name
later changes (or the feed has no entry at all) - the meal analog of an
admin-edited image prompt (§7.5). Rows live in the same ``sqlite.db`` as the
image metadata, but the table is owned by this module: the images schema
stays image-only, and :func:`open_meals_db` just layers the extra DDL on
:func:`app.images.db.open_db` (inheriting its pragmas and short-lived
connection convention).
"""

import sqlite3
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from app.images.db import open_db

_MEALS_SCHEMA = """
CREATE TABLE IF NOT EXISTS meal_overrides (
    day  TEXT PRIMARY KEY,  -- ISO date, YYYY-MM-DD
    name TEXT NOT NULL
);
"""


def open_meals_db(storage_root: Path) -> sqlite3.Connection:
    """Open ``sqlite.db`` with the meal-overrides table ensured.

    Delegates to :func:`app.images.db.open_db` (storage creation, pragmas,
    images schema) and adds this module's idempotent DDL. Callers own the
    connection and should close it promptly.
    """
    conn = open_db(storage_root)
    conn.executescript(_MEALS_SCHEMA)
    return conn


def stored_override(storage_root: Path, day: date) -> str | None:
    """One day's override for the render route.

    Never *creates* storage: a storage root without a ``sqlite.db`` (fresh
    install, storage-less test app) simply has no overrides, and ``/render``
    must not conjure a database just to learn that.
    """
    if not (storage_root / "sqlite.db").exists():  # the open_db filename
        return None
    conn = open_meals_db(storage_root)
    try:
        return get_override(conn, day)
    finally:
        conn.close()


def get_override(conn: sqlite3.Connection, day: date) -> str | None:
    """The override name for ``day``, or ``None`` when not overridden."""
    row = conn.execute(
        "SELECT name FROM meal_overrides WHERE day = ?", (day.isoformat(),)
    ).fetchone()
    return row["name"] if row is not None else None


def overrides_for(conn: sqlite3.Connection, days: Sequence[date]) -> dict[date, str]:
    """The overrides of ``days``, keyed by date (absent = not overridden)."""
    if not days:
        return {}
    marks = ",".join("?" * len(days))
    rows = conn.execute(
        f"SELECT day, name FROM meal_overrides WHERE day IN ({marks})",  # noqa: S608
        [d.isoformat() for d in days],
    ).fetchall()
    return {date.fromisoformat(row["day"]): row["name"] for row in rows}


def set_override(conn: sqlite3.Connection, day: date, name: str) -> None:
    """Set (or replace) ``day``'s override."""
    with conn:
        conn.execute(
            "INSERT INTO meal_overrides (day, name) VALUES (?, ?) "
            "ON CONFLICT(day) DO UPDATE SET name = excluded.name",
            (day.isoformat(), name),
        )


def clear_override(conn: sqlite3.Connection, day: date) -> None:
    """Remove ``day``'s override (a no-op when none exists)."""
    with conn:
        conn.execute("DELETE FROM meal_overrides WHERE day = ?", (day.isoformat(),))
