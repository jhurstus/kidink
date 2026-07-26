"""Tests for the /display request log (app.display.monitoring)."""

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from app.display.monitoring import list_requests, log_request, open_monitoring_db


def test_open_monitoring_db_is_idempotent(tmp_path: Path) -> None:
    open_monitoring_db(tmp_path).close()
    conn = open_monitoring_db(tmp_path)  # re-running the DDL must be harmless
    try:
        assert list_requests(conn) == []
    finally:
        conn.close()


def test_log_request_roundtrip_in_insertion_order(tmp_path: Path) -> None:
    first = datetime(2026, 7, 25, 6, 30, 0, 123456, tzinfo=UTC)
    log_request(tmp_path, first, 200)
    log_request(tmp_path, first + timedelta(minutes=20), 304)

    conn = open_monitoring_db(tmp_path)
    try:
        rows = list_requests(conn)
    finally:
        conn.close()
    assert [(row.requested_at, row.status) for row in rows] == [
        (first, 200),
        (first + timedelta(minutes=20), 304),
    ]
    assert rows[0].id < rows[1].id


def test_log_request_normalizes_timestamp_to_utc(tmp_path: Path) -> None:
    pacific = timezone(timedelta(hours=-7))
    log_request(tmp_path, datetime(2026, 7, 25, 6, 30, tzinfo=pacific), 500)

    conn = open_monitoring_db(tmp_path)
    try:
        [row] = list_requests(conn)
    finally:
        conn.close()
    assert row.requested_at == datetime(2026, 7, 25, 13, 30, tzinfo=UTC)
    assert row.requested_at.tzinfo == UTC


def test_shares_sqlite_db_with_image_metadata(tmp_path: Path) -> None:
    log_request(tmp_path, datetime(2026, 7, 25, tzinfo=UTC), 200)
    db_files = {path.name for path in tmp_path.iterdir() if path.suffix == ".db"}
    assert db_files == {"sqlite.db"}  # one shared database, no second file
