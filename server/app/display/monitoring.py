"""The `/display` request log: one row per device-facing request.

A monitoring aid for the polling loop (§3.1): each `/display` request appends
its UTC arrival time and the HTTP status it was answered with, so battery and
reliability questions ("is the device still polling?", "how many 304s vs full
downloads?", "did captures start failing overnight?") can be answered from the
server alone.

Rows live in the same ``sqlite.db`` as the image metadata, but the table is
owned by this module: :func:`open_monitoring_db` just layers the extra DDL on
:func:`app.images.db.open_db` (inheriting its pragmas and short-lived
connection convention) - the same pattern as :mod:`app.joke.jokes`.

The log is write-only telemetry: it never feeds a render, so the determinism
invariant (§3.4) is untouched by the wall-clock timestamp.
"""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.images.db import open_db

_MONITORING_SCHEMA = """
CREATE TABLE IF NOT EXISTS display_requests (
    id           INTEGER PRIMARY KEY,
    requested_at TEXT NOT NULL,
    status       INTEGER NOT NULL
);
"""


@dataclass(frozen=True)
class DisplayRequest:
    """One row of the ``display_requests`` table."""

    id: int
    requested_at: datetime
    status: int


def open_monitoring_db(storage_root: Path) -> sqlite3.Connection:
    """Open ``sqlite.db`` with the ``display_requests`` table ensured.

    Delegates to :func:`app.images.db.open_db` (storage creation, pragmas,
    images schema) and adds this module's idempotent DDL. Callers own the
    connection and should close it promptly.
    """
    conn = open_db(storage_root)
    conn.executescript(_MONITORING_SCHEMA)
    return conn


def log_request(storage_root: Path, requested_at: datetime, status: int) -> None:
    """Append one request to the log.

    ``requested_at`` must be timezone-aware; it is stored normalized to UTC in
    ISO 8601 form so rows compare and parse unambiguously.
    """
    conn = open_monitoring_db(storage_root)
    try:
        with conn:
            conn.execute(
                "INSERT INTO display_requests (requested_at, status) VALUES (?, ?)",
                (requested_at.astimezone(UTC).isoformat(), status),
            )
    finally:
        conn.close()


def list_requests(conn: sqlite3.Connection) -> list[DisplayRequest]:
    """Every logged request, oldest first (insertion order)."""
    rows = conn.execute("SELECT * FROM display_requests ORDER BY id").fetchall()
    return [
        DisplayRequest(
            id=row["id"],
            requested_at=datetime.fromisoformat(row["requested_at"]),
            status=row["status"],
        )
        for row in rows
    ]
