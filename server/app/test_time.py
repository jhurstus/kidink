"""The device-facing `/time` endpoint (specs/firmware.md §5).

The body is what the firmware's daily clock-sync wake writes into the RTC via
setDate()/setTime(), so the format and timezone conversion are load-bearing:
a wrong stamp here becomes a board that wakes at the wrong hour.
"""

from datetime import UTC, datetime

from flask.testing import FlaskClient

from app import create_app


def _client(now: datetime) -> FlaskClient:
    app = create_app()
    app.config["NOW"] = now
    return app.test_client()


def test_time_is_local_to_the_configured_timezone() -> None:
    # Conftest leaves the default timezone (US/Pacific): 18:02 UTC in July is
    # 11:02 PDT.
    response = _client(datetime(2026, 7, 25, 18, 2, 38, tzinfo=UTC)).get("/time")

    assert response.status_code == 200
    assert response.mimetype == "text/plain"
    assert response.get_data(as_text=True) == "2026-07-25 11:02:38\n"


def test_time_applies_standard_time_in_winter() -> None:
    # The same UTC hour lands an hour earlier under PST than under PDT.
    response = _client(datetime(2026, 1, 10, 18, 2, 38, tzinfo=UTC)).get("/time")

    assert response.get_data(as_text=True) == "2026-01-10 10:02:38\n"


def test_time_crosses_the_date_line_when_converting() -> None:
    # Early-UTC hours are still the previous local evening: the *date* part
    # must convert along with the time.
    response = _client(datetime(2026, 7, 26, 3, 30, 0, tzinfo=UTC)).get("/time")

    assert response.get_data(as_text=True) == "2026-07-25 20:30:00\n"


def test_time_is_never_cached() -> None:
    # A cached timestamp is a wrong one; no-store keeps any future proxy or
    # client cache from replaying a stale clock at the device.
    response = _client(datetime(2026, 7, 25, 18, 2, 38, tzinfo=UTC)).get("/time")

    assert response.headers["Cache-Control"] == "no-store"
