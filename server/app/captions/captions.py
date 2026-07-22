"""The caption store: the weather kid's speech-bubble lines (spec §10.5).

Three tables, all owned by this module. ``captions`` is the curated caption
list, one row per line, managed on ``/admin/captions`` with new captions
pasted in through the admin textarea (:func:`add_captions`) - the same shape
as the joke store (:mod:`app.joke.jokes`). ``caption_assignments`` pins each
rendered date to the caption it shows (§10.5): the first caption-eligible
render of a date takes the next caption in rotation and records it here, so
every later render of that date repeats it. ``caption_rotation`` is the
rotation pointer - the index of the most recently assigned caption - which is
what makes "next" well-defined even when dates are rendered out of calendar
order.

Rows live in the same ``sqlite.db`` as the image metadata, but the tables are
owned by this module: the images schema stays image-only, and
:func:`open_captions_db` just layers the extra DDL on
:func:`app.images.db.open_db` (inheriting its pragmas and short-lived
connection convention) - the same pattern as :mod:`app.dinner.overrides`.

Ordering is by ``id`` (insertion order): the rotation index (§10.5) counts
into the list in that order, and it is stable across edits (an edit keeps a
row's id).
"""

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.images.db import open_db

_CAPTIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS captions (
    id   INTEGER PRIMARY KEY,
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS caption_assignments (
    day           TEXT PRIMARY KEY,  -- ISO date, YYYY-MM-DD
    caption_index INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS caption_rotation (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    last_index INTEGER NOT NULL
);
"""


@dataclass(frozen=True)
class Caption:
    """One row of the ``captions`` table."""

    id: int
    text: str


@dataclass(frozen=True)
class Assignment:
    """One pinned date: the caption index it shows (§10.5)."""

    day: date
    caption_index: int


def open_captions_db(storage_root: Path) -> sqlite3.Connection:
    """Open ``sqlite.db`` with the captions tables ensured.

    Delegates to :func:`app.images.db.open_db` (storage creation, pragmas,
    images schema) and adds this module's idempotent DDL. Callers own the
    connection and should close it promptly.
    """
    conn = open_db(storage_root)
    conn.executescript(_CAPTIONS_SCHEMA)
    return conn


def _clean_lines(lines: Iterable[str]) -> list[str]:
    """Caption lines worth storing: stripped, non-blank, non-``#``-comment."""
    cleaned = []
    for line in lines:
        text = line.strip()
        if text and not text.startswith("#"):
            cleaned.append(text)
    return cleaned


def list_captions(conn: sqlite3.Connection) -> list[Caption]:
    """Every caption in insertion order - the order the rotation counts into."""
    rows = conn.execute("SELECT id, text FROM captions ORDER BY id").fetchall()
    return [Caption(id=row["id"], text=row["text"]) for row in rows]


def add_captions(conn: sqlite3.Connection, lines: Iterable[str]) -> int:
    """Append captions (one per line), skipping blank/``#`` lines; return the count.

    The admin bulk-add textarea funnels through here for its blank/comment
    filtering.
    """
    texts = _clean_lines(lines)
    with conn:
        conn.executemany(
            "INSERT INTO captions (text) VALUES (?)", [(text,) for text in texts]
        )
    return len(texts)


def update_caption(conn: sqlite3.Connection, caption_id: int, text: str) -> None:
    """Replace one caption's text (keeps its id, so its list position is stable)."""
    with conn:
        conn.execute(
            "UPDATE captions SET text = ? WHERE id = ?", (text.strip(), caption_id)
        )


def delete_caption(conn: sqlite3.Connection, caption_id: int) -> None:
    """Remove one caption (a no-op when the id is absent)."""
    with conn:
        conn.execute("DELETE FROM captions WHERE id = ?", (caption_id,))


def get_assignment(conn: sqlite3.Connection, day: date) -> int | None:
    """The caption index pinned to ``day``, or ``None`` when not yet pinned."""
    row = conn.execute(
        "SELECT caption_index FROM caption_assignments WHERE day = ?",
        (day.isoformat(),),
    ).fetchone()
    return row["caption_index"] if row is not None else None


def record_assignment(conn: sqlite3.Connection, day: date, index: int) -> None:
    """Pin ``day`` to ``index`` and advance the rotation pointer to it.

    First writer wins: if the day is already pinned (a concurrent render got
    there first), neither the pin nor the pointer changes - concurrent racers
    compute the same index anyway, this just keeps the invariant airtight.
    """
    with conn:
        inserted = conn.execute(
            "INSERT INTO caption_assignments (day, caption_index) VALUES (?, ?) "
            "ON CONFLICT(day) DO NOTHING",
            (day.isoformat(), index),
        ).rowcount
        if inserted:
            conn.execute(
                "INSERT INTO caption_rotation (id, last_index) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET last_index = excluded.last_index",
                (index,),
            )


def latest_assignment(conn: sqlite3.Connection) -> Assignment | None:
    """The most recently *recorded* pin (assignment order, not calendar order),
    or ``None`` before any caption has ever been assigned."""
    row = conn.execute(
        "SELECT day, caption_index FROM caption_assignments ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return Assignment(
        day=date.fromisoformat(row["day"]), caption_index=row["caption_index"]
    )


def get_last_index(conn: sqlite3.Connection) -> int | None:
    """The rotation pointer: the last assigned caption index, or ``None``."""
    row = conn.execute(
        "SELECT last_index FROM caption_rotation WHERE id = 1"
    ).fetchone()
    return row["last_index"] if row is not None else None


def clear_rotation(conn: sqlite3.Connection) -> None:
    """Forget every pin and the pointer (a no-op when already empty) - the
    next rendered day then restarts the rotation at the first caption."""
    with conn:
        conn.execute("DELETE FROM caption_assignments")
        conn.execute("DELETE FROM caption_rotation WHERE id = 1")
