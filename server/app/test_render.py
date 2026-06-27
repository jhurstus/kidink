from datetime import UTC, datetime

from flask import Flask

from app import create_app
from app.calendar import CalendarFetchError
from app.config import get_settings

DAY_NAMES = [
    "MONDAY",
    "TUESDAY",
    "WEDNESDAY",
    "THURSDAY",
    "FRIDAY",
    "SATURDAY",
    "SUNDAY",
]

# Minimal valid feed with no events — the strip renders with no event titles.
EMPTY_ICS = "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//test//EN\nEND:VCALENDAR\n"

# One event on Fri 2026-06-05; rendered against Wed 2026-06-03 it lands on a
# non-today cell (today's cell is a burst image with no title slot).
EVENT_ICS = (
    "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//test//EN\n"
    "BEGIN:VEVENT\nUID:soccer\nSUMMARY:Soccer practice\n"
    "DTSTART;TZID=America/Los_Angeles:20260605T120000\n"
    "DTEND;TZID=America/Los_Angeles:20260605T130000\n"
    "DESCRIPTION:interesting = 300\nEND:VEVENT\nEND:VCALENDAR\n"
)


def _app_with_ics(ics: str) -> Flask:
    """An app whose calendar fetch is faked to return ``ics`` (no network)."""
    app = create_app()
    app.config["FETCH_ICS"] = lambda url: ics
    return app


def test_render_route_ok() -> None:
    response = _app_with_ics(EMPTY_ICS).test_client().get("/render?date=2026-06-03")

    assert response.status_code == 200


def test_render_contains_all_seven_day_names() -> None:
    text = _app_with_ics(EMPTY_ICS).test_client().get("/render?date=2026-06-03").text

    for name in DAY_NAMES:
        assert name in text


def test_render_contains_formatted_corner_date() -> None:
    text = _app_with_ics(EMPTY_ICS).test_client().get("/render?date=2026-06-03").text

    assert "June 3, 2026" in text


def test_render_today_shows_burst_image() -> None:
    # 2026-06-22 is a Monday: today's cell is replaced by its burst image (the day
    # name is baked into the image), and the old is-today bold treatment is gone.
    text = _app_with_ics(EMPTY_ICS).test_client().get("/render?date=2026-06-22").text

    assert "day-burst-monday" in text
    assert "img/day_strip/monday_burst.png" in text
    assert "is-today" not in text


def test_render_emits_exact_outer_panel_params() -> None:
    text = _app_with_ics(EMPTY_ICS).test_client().get("/render?date=2026-06-03").text

    assert "--panel-w:1540px" in text
    assert "--panel-h:190px" in text
    assert "--panel-bg:#e1dcca" in text
    assert "#bbb4a2" in text  # outer halftone color
    assert "--origin-angle:330deg" in text  # the two outer halftone fields
    assert "--origin-angle:150deg" in text
    assert "--magnitude:21%" in text


def test_render_has_strip_structure() -> None:
    text = _app_with_ics(EMPTY_ICS).test_client().get("/render?date=2026-06-03").text

    assert "strip-groups" in text
    assert "day-row" in text
    assert "date-box" in text


def test_render_default_date_uses_injected_now() -> None:
    # No ?date= -> the resolved date comes from the injected clock, not the wall
    # clock. 2026-06-23 18:00 UTC is still Jun 23 in US/Pacific, a Tuesday.
    app = _app_with_ics(EMPTY_ICS)
    app.config["NOW"] = datetime(2026, 6, 23, 18, 0, tzinfo=UTC)
    text = app.test_client().get("/render").text

    assert "June 23, 2026" in text
    assert "day-burst-tuesday" in text


def test_render_is_deterministic() -> None:
    client = _app_with_ics(EVENT_ICS).test_client()

    first = client.get("/render?date=2026-06-03").text
    second = client.get("/render?date=2026-06-03").text

    assert first == second


def test_render_shows_most_interesting_event_title() -> None:
    text = _app_with_ics(EVENT_ICS).test_client().get("/render?date=2026-06-03").text

    assert "Soccer practice" in text
    assert "day-event" in text


def test_render_shows_event_title_on_today_burst_cell() -> None:
    # The event falls on the render date itself (today = the burst cell). Its title
    # must still render — now overlaid on the burst, using the shared .day-event.
    ics = (
        "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//test//EN\n"
        "BEGIN:VEVENT\nUID:t\nSUMMARY:Field trip\n"
        "DTSTART;TZID=America/Los_Angeles:20260622T120000\n"
        "DTEND;TZID=America/Los_Angeles:20260622T130000\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    # 2026-06-22 is a Monday → the burst cell.
    text = _app_with_ics(ics).test_client().get("/render?date=2026-06-22").text

    assert "day-burst-content" in text
    assert "Field trip" in text


def test_render_returns_500_when_fetch_fails() -> None:
    app = create_app()

    def boom(url: object) -> str:
        raise CalendarFetchError("fetch failed")

    app.config["FETCH_ICS"] = boom

    assert app.test_client().get("/render?date=2026-06-03").status_code == 500


def test_render_returns_500_on_unparseable_feed() -> None:
    app = _app_with_ics("this is not iCalendar data")

    assert app.test_client().get("/render?date=2026-06-03").status_code == 500


def test_render_does_not_leak_secret_url() -> None:
    secret = get_settings().family_calendar_ics_url.get_secret_value()
    text = _app_with_ics(EMPTY_ICS).test_client().get("/render?date=2026-06-03").text

    assert secret not in text
