"""SQLite metadata store for AI-generated images (spec §7.1).

One row per generated image, uniquely keyed by the logical key ``(module,
item_description, width, height, variant)``. The rendered bytes live on disk as
``gen_images/<id>.png`` under the storage root — the database holds only
metadata. Connections are short-lived: open one per operation and close it, so
there is no shared connection to worry about across Flask request threads.

The ``prompt_attachments`` table tracks style-reference images (spec §7.1): one
row per file under ``prompt_images/`` (the ``path`` column is relative to that
directory), many-to-many with ``images`` through ``image_prompt_attachments``.
Attached images are passed to the generation API as reference inputs (§7.2).
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


@dataclass(frozen=True)
class PromptAttachment:
    """One row of the ``prompt_attachments`` table (spec §7.1).

    ``path`` is relative to the ``prompt_images/`` directory under the storage
    root, with POSIX separators (see :mod:`app.images.attachments`).
    """

    id: int
    path: str


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
) -> tuple[ImageRecord, bool]:
    """Return ``spec``'s record plus whether this call created it.

    ``prompt`` seeds the row only on first creation (§7.5); an existing row's
    (possibly admin-edited, §7.4) prompt always wins on subsequent calls. The
    ``created`` flag lets callers run first-creation-only work, like seeding
    the record's default prompt attachments (§7.5).
    """
    with conn:
        cursor = conn.execute(
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
    record = find_record(conn, spec)
    assert record is not None  # the insert above guarantees the row exists
    return record, cursor.rowcount == 1


def find_record(conn: sqlite3.Connection, spec: ImageSpec) -> ImageRecord | None:
    """Return the record for ``spec`` without creating it, or ``None``.

    The SELECT half of :func:`get_or_create_record` (same ``COALESCE`` variant
    matching); used to locate a variant's base row (the edit-from-base policy
    in :mod:`app.images.store`).
    """
    row = conn.execute(
        """
        SELECT * FROM images
        WHERE module = ? AND item_description = ? AND width = ? AND height = ?
          AND COALESCE(variant, '') = COALESCE(?, '')
        """,
        (spec.module, spec.item_description, spec.width, spec.height, spec.variant),
    ).fetchone()
    return _row_to_record(row) if row is not None else None


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


def _row_to_attachment(row: sqlite3.Row) -> PromptAttachment:
    return PromptAttachment(id=row["id"], path=row["path"])


def get_or_create_attachment(conn: sqlite3.Connection, path: str) -> PromptAttachment:
    """Return the attachment row for ``path``, creating it if absent.

    ``path`` must already be normalized (see
    :func:`app.images.attachments.normalize_attachment_path`), so one file
    never lands under two spellings.
    """
    with conn:
        conn.execute(
            "INSERT INTO prompt_attachments (path) VALUES (?)"
            " ON CONFLICT (path) DO NOTHING",
            (path,),
        )
    row = conn.execute(
        "SELECT * FROM prompt_attachments WHERE path = ?", (path,)
    ).fetchone()
    assert row is not None  # the insert above guarantees the row exists
    return _row_to_attachment(row)


def get_attachment(
    conn: sqlite3.Connection, attachment_id: int
) -> PromptAttachment | None:
    """Return the attachment with ``attachment_id``, or ``None``."""
    row = conn.execute(
        "SELECT * FROM prompt_attachments WHERE id = ?", (attachment_id,)
    ).fetchone()
    return _row_to_attachment(row) if row is not None else None


def list_image_attachments(
    conn: sqlite3.Connection, image_id: int
) -> list[PromptAttachment]:
    """The attachments of one image record, in attachment-creation order.

    The id ordering is what keeps generation deterministic and the ordinal
    "reference image N" prompt rewrites stable: seeded module defaults come
    first (they are created first, §7.5), later admin attaches append.
    """
    rows = conn.execute(
        """
        SELECT pa.* FROM prompt_attachments pa
        JOIN image_prompt_attachments ipa ON ipa.attachment_id = pa.id
        WHERE ipa.image_id = ?
        ORDER BY pa.id
        """,
        (image_id,),
    ).fetchall()
    return [_row_to_attachment(row) for row in rows]


def list_all_attachments(conn: sqlite3.Connection) -> list[PromptAttachment]:
    """Every known attachment, ordered by path (the admin attach picker, §7.4)."""
    rows = conn.execute("SELECT * FROM prompt_attachments ORDER BY path").fetchall()
    return [_row_to_attachment(row) for row in rows]


def attach_to_image(
    conn: sqlite3.Connection, image_id: int, attachment_id: int
) -> None:
    """Link an attachment to an image record (idempotent)."""
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO image_prompt_attachments (image_id, attachment_id)"
            " VALUES (?, ?)",
            (image_id, attachment_id),
        )


def detach_from_image(
    conn: sqlite3.Connection, image_id: int, attachment_id: int
) -> None:
    """Unlink an attachment from an image record.

    Removes the junction row only: the ``prompt_attachments`` row and its file
    survive, since either may be shared with other records.
    """
    with conn:
        conn.execute(
            "DELETE FROM image_prompt_attachments"
            " WHERE image_id = ? AND attachment_id = ?",
            (image_id, attachment_id),
        )
