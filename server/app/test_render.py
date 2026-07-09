import io
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from flask import Flask
from PIL import Image
from pydantic import SecretStr

from app import create_app
from app.calendar import CalendarFetchError
from app.config import get_settings
from app.images import ImageGenerationError

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


def _keyable_png() -> bytes:
    """A red rectangle on a pure-green key background — keys cleanly (§7.2)."""
    pixels = np.tile(np.array((0, 255, 0), np.uint8), (480, 800, 1))
    pixels[40:440, 40:760] = (220, 40, 40)
    out = io.BytesIO()
    Image.fromarray(pixels, "RGB").save(out, format="PNG")
    return out.getvalue()


def _generate_ok(api_key: SecretStr, *, prompt: str, size: str, model: str) -> bytes:
    return _keyable_png()


def _generate_boom(api_key: SecretStr, *, prompt: str, size: str, model: str) -> bytes:
    raise ImageGenerationError("image generation failed: Boom")


def _generate_unexpected(
    api_key: SecretStr, *, prompt: str, size: str, model: str
) -> bytes:
    raise AssertionError("image generation was invoked but no test seam was set")


def _app_with_ics(
    ics: str, storage: Path | None = None, generate: object = None
) -> Flask:
    """An app with the calendar fetch, image generation, and storage faked.

    No test may touch the network or the developer's real image storage:
    the generator defaults to a fail-loudly guard (event-less feeds never
    generate), and tests whose feed has events must pass a ``tmp_path``
    ``storage``.
    """
    app = create_app()
    app.config["FETCH_ICS"] = lambda url: ics
    app.config["GENERATE_IMAGE_BYTES"] = generate or _generate_unexpected
    if storage is not None:
        app.config["APP_STORAGE_PATH"] = storage
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


def test_render_has_no_outer_strip_panel() -> None:
    # The strip's groups sit directly on .day-strip — the old full-width enclosing
    # comic panel (its tan fill and double halftone) must not come back.
    text = _app_with_ics(EMPTY_ICS).test_client().get("/render?date=2026-06-03").text

    assert "--panel-w:1540px" not in text
    assert "#e1dcca" not in text  # outer panel fill
    assert "#bbb4a2" not in text  # outer halftone color


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


def test_render_is_deterministic(tmp_path: Path) -> None:
    client = _app_with_ics(EVENT_ICS, tmp_path, _generate_ok).test_client()

    first = client.get("/render?date=2026-06-03").text
    second = client.get("/render?date=2026-06-03").text

    assert first == second


def test_render_shows_event_icon(tmp_path: Path) -> None:
    # The event's cell shows its generated icon, served by the image route; the
    # title remains as the img alt text.
    text = (
        _app_with_ics(EVENT_ICS, tmp_path, _generate_ok)
        .test_client()
        .get("/render?date=2026-06-03")
        .text
    )

    # Match the img tag, not the bare class name — "day-icon" is a substring
    # of the always-present-with-events "day-icons" row wrapper.
    assert '<img class="day-icon"' in text
    assert "/images/generated/1" in text
    assert "Soccer practice" in text  # alt text
    assert "day-event-chip" not in text


def test_render_generates_once_across_renders(tmp_path: Path) -> None:
    # The §7.1 warm path: the second render reuses gen_images/<id>.png with no
    # further generator calls, so the board stays cheap and deterministic.
    calls: list[str] = []

    def generate(api_key: SecretStr, *, prompt: str, size: str, model: str) -> bytes:
        calls.append(prompt)
        return _keyable_png()

    client = _app_with_ics(EVENT_ICS, tmp_path, generate).test_client()
    client.get("/render?date=2026-06-03")
    client.get("/render?date=2026-06-03")

    assert len(calls) == 1
    assert "Soccer practice" in calls[0]  # prompt built from the event title


def test_render_serves_generated_icon_bytes(tmp_path: Path) -> None:
    client = _app_with_ics(EVENT_ICS, tmp_path, _generate_ok).test_client()
    client.get("/render?date=2026-06-03")

    response = client.get("/images/generated/1")
    assert response.status_code == 200
    assert response.mimetype == "image/png"
    image = Image.open(io.BytesIO(response.data))
    assert image.mode == "RGBA"
    # Native-resolution crop of the fake generation's 720×400 subject — the
    # record's logical 60×60 box no longer bounds the stored PNG.
    assert (image.width, image.height) == (720, 400)


def test_render_falls_back_to_chip_on_generation_failure(tmp_path: Path) -> None:
    # §7.3: a failed generation never breaks the render — the cell falls back to
    # the comic chip carrying the title text.
    response = (
        _app_with_ics(EVENT_ICS, tmp_path, _generate_boom)
        .test_client()
        .get("/render?date=2026-06-03")
    )

    assert response.status_code == 200
    assert "day-event-chip" in response.text
    assert "Soccer practice" in response.text
    assert '<img class="day-icon"' not in response.text


def test_render_shows_event_icon_on_today_burst_cell(tmp_path: Path) -> None:
    # The event falls on the render date itself (today = the burst cell). Its
    # icon must still render, overlaid on the burst body.
    ics = (
        "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//test//EN\n"
        "BEGIN:VEVENT\nUID:t\nSUMMARY:Field trip\n"
        "DTSTART;TZID=America/Los_Angeles:20260622T120000\n"
        "DTEND;TZID=America/Los_Angeles:20260622T130000\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    # 2026-06-22 is a Monday → the burst cell.
    text = (
        _app_with_ics(ics, tmp_path, _generate_ok)
        .test_client()
        .get("/render?date=2026-06-22")
        .text
    )

    assert "day-burst-content" in text
    assert "day-icon" in text
    assert "Field trip" in text


def test_render_debug_images_lists_render_images(tmp_path: Path) -> None:
    client = _app_with_ics(EVENT_ICS, tmp_path, _generate_ok).test_client()

    plain = client.get("/render?date=2026-06-03").text
    debug = client.get("/render?date=2026-06-03&debug_images=1").text

    assert "debug-images" not in plain
    assert "debug-images" in debug
    assert "Soccer practice" in debug
    assert "/admin/images?img=1" in debug


# Two events on the render date Wed 2026-06-03: an 08:00 one (morning) and a
# 19:00 one (evening) — so the Today panel shows those two buckets and no Day.
TODAY_ICS = (
    "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//test//EN\n"
    "BEGIN:VEVENT\nUID:breakfast\nSUMMARY:Breakfast\n"
    "DTSTART;TZID=America/Los_Angeles:20260603T080000\n"
    "DTEND;TZID=America/Los_Angeles:20260603T090000\nEND:VEVENT\n"
    "BEGIN:VEVENT\nUID:movie\nSUMMARY:Movie night\n"
    "DTSTART;TZID=America/Los_Angeles:20260603T190000\n"
    "DTEND;TZID=America/Los_Angeles:20260603T200000\nEND:VEVENT\n"
    "END:VCALENDAR\n"
)


def test_render_today_buckets_only_nonempty(tmp_path: Path) -> None:
    # Assert on the bucket CSS class names, not the header words ("DAY" is a
    # substring of every weekday name in the strip).
    text = (
        _app_with_ics(TODAY_ICS, tmp_path, _generate_ok)
        .test_client()
        .get("/render?date=2026-06-03")
        .text
    )

    assert "today-bucket-morning" in text
    assert "today-bucket-evening" in text
    assert "today-bucket-day" not in text
    assert "Breakfast" in text
    assert "Movie night" in text


def test_render_today_has_tab_and_reserved_weather_slot() -> None:
    text = _app_with_ics(EMPTY_ICS).test_client().get("/render?date=2026-06-03").text

    assert "today-tab" in text
    assert "corner-tab" in text
    assert "today-weather-slot" in text
    assert "today-bucket-" not in text  # empty day: no buckets at all


def test_render_today_rows_show_icons(tmp_path: Path) -> None:
    text = (
        _app_with_ics(TODAY_ICS, tmp_path, _generate_ok)
        .test_client()
        .get("/render?date=2026-06-03")
        .text
    )

    assert '<img class="today-icon"' in text
    assert "today-chip" not in text


def test_render_today_rows_fall_back_to_chip(tmp_path: Path) -> None:
    # §7.3: a failed generation leaves the row's title next to a blank chip.
    text = (
        _app_with_ics(TODAY_ICS, tmp_path, _generate_boom)
        .test_client()
        .get("/render?date=2026-06-03")
        .text
    )

    assert "today-chip" in text
    assert '<img class="today-icon"' not in text
    assert "Breakfast" in text


def test_render_returns_500_when_fetch_fails() -> None:
    app = create_app()

    def boom(url: object) -> str:
        raise CalendarFetchError("fetch failed")

    app.config["FETCH_ICS"] = boom

    assert app.test_client().get("/render?date=2026-06-03").status_code == 500


def test_render_returns_500_on_unparseable_feed() -> None:
    app = _app_with_ics("this is not iCalendar data")

    assert app.test_client().get("/render?date=2026-06-03").status_code == 500


def test_render_does_not_leak_secrets(tmp_path: Path) -> None:
    ics_secret = get_settings().family_calendar_ics_url.get_secret_value()
    api_key = get_settings().openai_api_key.get_secret_value()
    client = _app_with_ics(EVENT_ICS, tmp_path, _generate_boom).test_client()
    text = client.get("/render?date=2026-06-03").text

    assert ics_secret not in text
    assert api_key not in text
    # The on-disk failure log carries the item and prompt but never a secret.
    failure_log = (tmp_path / "gen_failures.log").read_text()
    assert api_key not in failure_log
    assert ics_secret not in failure_log
