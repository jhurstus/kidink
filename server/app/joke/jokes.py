"""The joke store: the curated joke/riddle list, one row per joke (spec §15).

The spec's source is "a configurable UTF-8 text file, one joke/riddle per
line"; here the live source of truth is instead a ``jokes`` table so the
``/admin/jokes`` page can do robust row-level CRUD (edit/delete/add), with new
jokes pasted in through the admin textarea (:func:`add_jokes`).

Rows live in the same ``sqlite.db`` as the image metadata, but the table is
owned by this module: the images schema stays image-only, and
:func:`open_jokes_db` just layers the extra DDL on
:func:`app.images.db.open_db` (inheriting its pragmas and short-lived
connection convention) - the same pattern as :mod:`app.dinner.overrides`.

Ordering is by ``id`` (insertion order): the daily index (§15) counts into the
list in that order, and it is stable across edits (an edit keeps a row's id).
"""

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from app.images.db import open_db

_JOKES_SCHEMA = """
CREATE TABLE IF NOT EXISTS jokes (
    id   INTEGER PRIMARY KEY,
    text TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Joke:
    """One row of the ``jokes`` table."""

    id: int
    text: str


def open_jokes_db(storage_root: Path) -> sqlite3.Connection:
    """Open ``sqlite.db`` with the jokes table ensured.

    Delegates to :func:`app.images.db.open_db` (storage creation, pragmas,
    images schema) and adds this module's idempotent DDL. Callers own the
    connection and should close it promptly.
    """
    conn = open_db(storage_root)
    conn.executescript(_JOKES_SCHEMA)
    return conn


def _clean_lines(lines: Iterable[str]) -> list[str]:
    """Joke lines worth storing: stripped, non-blank, non-``#``-comment (§15)."""
    cleaned = []
    for line in lines:
        text = line.strip()
        if text and not text.startswith("#"):
            cleaned.append(text)
    return cleaned


def list_jokes(conn: sqlite3.Connection) -> list[Joke]:
    """Every joke in insertion order - the order the daily index counts into."""
    rows = conn.execute("SELECT id, text FROM jokes ORDER BY id").fetchall()
    return [Joke(id=row["id"], text=row["text"]) for row in rows]


def get_joke(conn: sqlite3.Connection, joke_id: int) -> Joke | None:
    """The joke with ``joke_id``, or ``None`` when absent."""
    row = conn.execute("SELECT id, text FROM jokes WHERE id = ?", (joke_id,)).fetchone()
    return Joke(id=row["id"], text=row["text"]) if row is not None else None


def count_jokes(conn: sqlite3.Connection) -> int:
    """The number of jokes (``N`` in the §15 modulo index)."""
    return conn.execute("SELECT COUNT(*) FROM jokes").fetchone()[0]


def add_jokes(conn: sqlite3.Connection, lines: Iterable[str]) -> int:
    """Append jokes (one per line), skipping blank/``#`` lines; return the count.

    The admin bulk-add textarea funnels through here for its blank/comment
    filtering (§15).
    """
    texts = _clean_lines(lines)
    with conn:
        conn.executemany(
            "INSERT INTO jokes (text) VALUES (?)", [(text,) for text in texts]
        )
    return len(texts)


def update_joke(conn: sqlite3.Connection, joke_id: int, text: str) -> None:
    """Replace one joke's text (keeps its id, so its list position is stable)."""
    with conn:
        conn.execute("UPDATE jokes SET text = ? WHERE id = ?", (text.strip(), joke_id))


def delete_joke(conn: sqlite3.Connection, joke_id: int) -> None:
    """Remove one joke (a no-op when the id is absent)."""
    with conn:
        conn.execute("DELETE FROM jokes WHERE id = ?", (joke_id,))


def stored_jokes(storage_root: Path) -> list[str]:
    """The joke texts for the render route, in list order (empty when unseeded).

    Never *creates* storage: a storage root without a ``sqlite.db`` (fresh
    install, storage-less test app) simply has no jokes, and ``/render`` must
    not conjure a database just to learn that (mirrors
    :func:`app.dinner.overrides.stored_override`).
    """
    if not (storage_root / "sqlite.db").exists():  # the open_db filename
        return []
    conn = open_jokes_db(storage_root)
    try:
        return [joke.text for joke in list_jokes(conn)]
    finally:
        conn.close()
