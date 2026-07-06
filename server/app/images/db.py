"""SQLite metadata store for AI-generated images (spec §7.1).

One row per generated image, uniquely keyed by the logical key ``(module,
item_description, width, height, variant)``. The rendered bytes live on disk as
``gen_images/<id>.png`` under the storage root — the database holds only
metadata. Connections are short-lived: open one per operation and close it, so
there is no shared connection to worry about across Flask request threads.

The ``prompt_attachments`` / ``image_prompt_attachments`` tables are created now
so no migration is needed later, but nothing reads them yet — prompt-attachment
support (style-reference images, §7.5) is deferred.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    id               INTEGER PRIMARY KEY,
    module           TEXT NOT NULL,
    item_description TEXT NOT NULL,
    width            INTEGER NOT NULL,
    height           INTEGER NOT NULL,
    variant          TEXT,
    prompt           TEXT NOT NULL,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
-- SQLite unique indexes treat NULLs as distinct, which would let duplicate
-- variant-less rows pile up; indexing COALESCE(variant, '') makes variant=NULL
-- collide as the spec's logical key requires (§7.1).
CREATE UNIQUE INDEX IF NOT EXISTS images_logical_key
    ON images (module, item_description, width, height, COALESCE(variant, ''));

CREATE TABLE IF NOT EXISTS prompt_attachments (
    id   INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS image_prompt_attachments (
    image_id      INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    attachment_id INTEGER NOT NULL REFERENCES prompt_attachments(id) ON DELETE CASCADE,
    PRIMARY KEY (image_id, attachment_id)
);
"""


@dataclass(frozen=True)
class ImageSpec:
    """The logical key of an image record (spec §7.1).

    ``width``/``height`` are the *logical display size* in px (what CSS sizes
    the image to); the stored PNG keeps its native generation resolution.
    """

    module: str
    item_description: str
    width: int
    height: int
    variant: str | None = None


@dataclass(frozen=True)
class ImageRecord:
    """One row of the ``images`` table."""

    id: int
    spec: ImageSpec
    prompt: str


def open_db(storage_root: Path) -> sqlite3.Connection:
    """Open (creating as needed) ``sqlite.db`` under ``storage_root``.

    Creates the storage root, applies pragmas, and runs the idempotent schema
    DDL. Callers own the connection and should close it promptly (one short-lived
    connection per operation).
    """
    storage_root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(storage_root / "sqlite.db")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(_SCHEMA)
    return conn


def _row_to_record(row: sqlite3.Row) -> ImageRecord:
    return ImageRecord(
        id=row["id"],
        spec=ImageSpec(
            module=row["module"],
            item_description=row["item_description"],
            width=row["width"],
            height=row["height"],
            variant=row["variant"],
        ),
        prompt=row["prompt"],
    )


def get_or_create_record(
    conn: sqlite3.Connection, spec: ImageSpec, prompt: str
) -> ImageRecord:
    """Return the record for ``spec``, creating it with ``prompt`` if absent.

    ``prompt`` seeds the row only on first creation (§7.5); an existing row's
    (possibly admin-edited, §7.4) prompt always wins on subsequent calls.
    """
    with conn:
        conn.execute(
            """
            INSERT INTO images
                (module, item_description, width, height, variant, prompt)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (module, item_description, width, height, COALESCE(variant, ''))
            DO NOTHING
            """,
            (
                spec.module,
                spec.item_description,
                spec.width,
                spec.height,
                spec.variant,
                prompt,
            ),
        )
    row = conn.execute(
        """
        SELECT * FROM images
        WHERE module = ? AND item_description = ? AND width = ? AND height = ?
          AND COALESCE(variant, '') = COALESCE(?, '')
        """,
        (spec.module, spec.item_description, spec.width, spec.height, spec.variant),
    ).fetchone()
    return _row_to_record(row)


def get_record(conn: sqlite3.Connection, image_id: int) -> ImageRecord | None:
    """Return the record with ``image_id``, or ``None``."""
    row = conn.execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone()
    return _row_to_record(row) if row is not None else None


def list_records(conn: sqlite3.Connection) -> list[ImageRecord]:
    """All image records, ordered by logical key (for the admin list view, §7.4)."""
    rows = conn.execute(
        "SELECT * FROM images ORDER BY module, item_description, width, height, variant"
    ).fetchall()
    return [_row_to_record(row) for row in rows]


def update_prompt(conn: sqlite3.Connection, image_id: int, prompt: str) -> None:
    """Persist an edited ``prompt`` on the record (admin endpoint, §7.4)."""
    with conn:
        conn.execute("UPDATE images SET prompt = ? WHERE id = ?", (prompt, image_id))
