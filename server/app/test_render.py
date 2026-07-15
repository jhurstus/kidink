import io
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pytest
from flask import Flask
from PIL import Image
from pydantic import SecretStr

from app import create_app
from app.calendar import CalendarFetchError
from app.config import get_settings
from app.dinner.overrides import open_meals_db, set_override
from app.images import ImageGenerationError
from app.joke.jokes import add_jokes, open_jokes_db
from app.weather import DayForecast, WeatherFetchError

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

# A meal plan in the Anylist shape (all-day date-only entries, §13): a main
# plus a side on Wed 2026-06-03, and the same dinner again two days later
# (same combined name -> same image record, §7.1).
MEAL_ICS = (
    "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//AnyList//\n"
    "BEGIN:VEVENT\nUID:meal-main\nSUMMARY:Tacos\n"
    "DTSTART;VALUE=DATE:20260603\nEND:VEVENT\n"
    "BEGIN:VEVENT\nUID:meal-side\nSUMMARY:Rice\n"
    "DTSTART;VALUE=DATE:20260603\nEND:VEVENT\n"
    "BEGIN:VEVENT\nUID:meal-main-again\nSUMMARY:Tacos\n"
    "DTSTART;VALUE=DATE:20260605\nEND:VEVENT\n"
    "BEGIN:VEVENT\nUID:meal-side-again\nSUMMARY:Rice\n"
    "DTSTART;VALUE=DATE:20260605\nEND:VEVENT\n"
    "END:VCALENDAR\n"
)


# A mild partly-cloudy day: 66°F feels-like high → normal outfit, arrow mid-bar.
_FAKE_DAY = DayForecast(
    feels_like_high_f=66.0,
    condition_type="PARTLY_CLOUDY",
    precip_percent=10,
    precip_type="RAIN",
    thunderstorm_percent=0,
    cloud_cover_percent=45,
)


class _EveryDayForecast(dict):
    """Fake forecast mapping serving _FAKE_DAY for any date, so tests don't
    couple their ?date= choices to a hand-built forecast horizon."""

    def get(self, key: object, default: object = None) -> DayForecast:
        return _FAKE_DAY


def _keyable_png() -> bytes:
    """A red rectangle on a pure-green key background — keys cleanly (§7.2)."""
    pixels = np.tile(np.array((0, 255, 0), np.uint8), (480, 800, 1))
    pixels[40:440, 40:760] = (220, 40, 40)
    out = io.BytesIO()
    Image.fromarray(pixels, "RGB").save(out, format="PNG")
    return out.getvalue()


def _generate_ok(
    api_key: SecretStr,
    *,
    prompt: str,
    size: str,
    model: str,
    base_png: bytes | None = None,
    reference_images: Sequence[bytes] = (),
) -> bytes:
    return _keyable_png()


def _generate_boom(
    api_key: SecretStr,
    *,
    prompt: str,
    size: str,
    model: str,
    base_png: bytes | None = None,
    reference_images: Sequence[bytes] = (),
) -> bytes:
    raise ImageGenerationError("image generation failed: Boom")


def _generate_unexpected(
    api_key: SecretStr,
    *,
    prompt: str,
    size: str,
    model: str,
    base_png: bytes | None = None,
    reference_images: Sequence[bytes] = (),
) -> bytes:
    raise AssertionError("image generation was invoked but no test seam was set")


# Storage default for storage-less tests: /render now reads meal overrides
# from storage on every request, so pointing at the developer's real .storage
# is no longer merely unused but actively wrong. A path under /dev/null can
# never exist (no overrides) and any accidental create/write fails loudly.
_POISON_STORAGE = Path("/dev/null/kidink-test-storage")


def _app_with_ics(
    ics: str,
    storage: Path | None = None,
    generate: object = None,
    mealplan_ics: str = EMPTY_ICS,
) -> Flask:
    """An app with the feed fetches, image generation, and storage faked.

    No test may touch the network or the developer's real storage: the
    generator defaults to a fail-loudly guard (event-less feeds never
    generate), storage defaults to the poison path above, and tests whose
    feeds have events must pass a ``tmp_path`` ``storage``. The meal plan
    defaults to no meals (the mystery card, §13).
    """
    app = create_app()
    app.config["FETCH_ICS"] = lambda url: ics
    app.config["FETCH_MEALPLAN_ICS"] = lambda url: mealplan_ics
    app.config["FETCH_FORECAST"] = lambda *args, **kwargs: _EveryDayForecast()
    app.config["GENERATE_IMAGE_BYTES"] = generate or _generate_unexpected
    app.config["APP_STORAGE_PATH"] = storage if storage is not None else _POISON_STORAGE
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

    def generate(
        api_key: SecretStr,
        *,
        prompt: str,
        size: str,
        model: str,
        base_png: bytes | None = None,
        reference_images: Sequence[bytes] = (),
    ) -> bytes:
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


def test_render_today_weather_subpanel() -> None:
    # The fake forecast (66°F, partly cloudy, dry) renders the §10.3 subpanel:
    # condition placeholder, clothing-kid placeholder, and the SVG temp bar
    # with the arrow's high label. 66°F is a normal-outfit day.
    text = _app_with_ics(EMPTY_ICS).test_client().get("/render?date=2026-06-03").text

    assert "weather-condition" in text
    assert "partly cloudy" in text
    assert "weather-kid" in text
    assert "normal outfit" in text
    assert "weather-bar" in text
    assert "66°" in text


def _weather_sections(text: str) -> tuple[str, str]:
    """Split a render into (today's, tomorrow's) weather-subpanel HTML.

    Document order is fixed: the Today pane precedes the right pane, so the
    today slot's markup runs from its class name to the tomorrow slot's.
    """
    today_on = text.split("today-weather-slot", 1)[1]
    today_section, tomorrow_section = today_on.split("tomorrow-weather-slot", 1)
    return today_section, tomorrow_section


def _featured_kid(section: str) -> str:
    # The clothing-kid placeholder renders "<Name>: <outfit> outfit".
    kids = get_settings().kids
    return next(k.name for k in kids if f"{k.name}:" in section)


def test_render_tomorrow_weather_subpanel() -> None:
    # §11: tomorrow's subpanel matches today's — condition icon + clothing kid
    # + temperature bar; its SVG defs carry the tomorrow uid so ids don't
    # collide with today's.
    text = _app_with_ics(EMPTY_ICS).test_client().get("/render?date=2026-06-03").text

    _, tomorrow_section = _weather_sections(text)
    assert "weather-condition" in tomorrow_section
    assert "weather-kid" in tomorrow_section
    assert "weather-bar" in tomorrow_section
    assert "wx-tomorrow-clip" in tomorrow_section
    assert "66°" in tomorrow_section


def test_render_weather_kid_flip_flops_daily() -> None:
    # The featured kid alternates with the date seed (§ Weather). The local
    # config's kids drive the names; assert Today's kid differs across two
    # consecutive days.
    client = _app_with_ics(EMPTY_ICS).test_client()
    if len(get_settings().kids) < 2:
        pytest.skip("flip-flop needs two configured kids")

    first, _ = _weather_sections(client.get("/render?date=2026-06-03").text)
    second, _ = _weather_sections(client.get("/render?date=2026-06-04").text)

    assert _featured_kid(first) != _featured_kid(second)


def test_render_weather_panels_feature_different_kids() -> None:
    # § Weather flip-flop: the same render features one kid in Today and the
    # other in Tomorrow.
    if len(get_settings().kids) < 2:
        pytest.skip("flip-flop needs two configured kids")
    text = _app_with_ics(EMPTY_ICS).test_client().get("/render?date=2026-06-03").text

    today_section, tomorrow_section = _weather_sections(text)
    assert _featured_kid(today_section) != _featured_kid(tomorrow_section)


def test_render_fetches_forecast_once() -> None:
    # One forecast fetch serves both panels — never one call per subpanel.
    app = _app_with_ics(EMPTY_ICS)
    calls: list[object] = []

    def counting_fetch(*args: object, **kwargs: object) -> dict:
        calls.append(args)
        return _EveryDayForecast()

    app.config["FETCH_FORECAST"] = counting_fetch
    app.test_client().get("/render?date=2026-06-03")

    assert len(calls) == 1


def test_render_weather_debug_overrides() -> None:
    # §3.5: each weather_* arg replaces its slice of the forecast in BOTH
    # panels; the fake forecast would otherwise say partly cloudy / 66°.
    text = (
        _app_with_ics(EMPTY_ICS)
        .test_client()
        .get(
            "/render?date=2026-06-03"
            "&weather_icon=snow&weather_outfit=rain&weather_temp=95"
        )
        .text
    )

    today_section, tomorrow_section = _weather_sections(text)
    assert "img/weather/snow.png" in today_section
    assert "rain outfit" in today_section
    assert "95°" in today_section
    assert "rain outfit" in tomorrow_section
    assert "95°" in tomorrow_section
    assert "partly cloudy" not in text
    assert "66°" not in text


def test_render_weather_temp_drives_outfit_derivation() -> None:
    # A lone weather_temp flows into the outfit cutoffs too: 95°F is a hot
    # day even though the fake forecast's 66° would be a normal one.
    text = (
        _app_with_ics(EMPTY_ICS)
        .test_client()
        .get("/render?date=2026-06-03&weather_temp=95")
        .text
    )

    assert "95°" in text
    assert "hot outfit" in text
    assert "normal outfit" not in text


def test_render_all_weather_overrides_skip_the_fetch() -> None:
    # §3.5: with every weather_* arg set there is nothing real left to show,
    # so the forecast fetch is skipped outright.
    app = _app_with_ics(EMPTY_ICS)
    calls: list[object] = []

    def counting_fetch(*args: object, **kwargs: object) -> dict:
        calls.append(args)
        return _EveryDayForecast()

    app.config["FETCH_FORECAST"] = counting_fetch
    text = (
        app.test_client()
        .get(
            "/render?date=2026-06-03"
            "&weather_icon=thunder&weather_outfit=cold&weather_temp=40"
        )
        .text
    )

    assert calls == []
    assert "img/weather/thunder.png" in text
    assert "cold outfit" in text
    assert "40°" in text


def test_render_weather_temp_renders_panel_without_forecast() -> None:
    # weather_temp alone previews the subpanels even when the forecast is
    # unavailable (fetch failure -> empty horizon -> synthesized day).
    app = _app_with_ics(EMPTY_ICS)

    def boom(*args: object, **kwargs: object) -> dict:
        raise WeatherFetchError("weather forecast fetch failed")

    app.config["FETCH_FORECAST"] = boom
    text = app.test_client().get("/render?date=2026-06-03&weather_temp=80").text

    assert "weather-bar" in text
    assert "80°" in text
    assert "hot outfit" in text


def test_render_invalid_weather_override_is_400() -> None:
    client = _app_with_ics(EMPTY_ICS).test_client()

    assert client.get("/render?date=2026-06-03&weather_icon=sunnny").status_code == 400
    assert client.get("/render?date=2026-06-03&weather_outfit=wet").status_code == 400
    assert client.get("/render?date=2026-06-03&weather_temp=warm").status_code == 400


def test_render_survives_weather_fetch_failure() -> None:
    # Weather degrades softly (unlike the calendar): the board still renders,
    # the slot keeps its footprint, and the subpanel is simply absent.
    app = _app_with_ics(EMPTY_ICS)

    def boom(*args: object, **kwargs: object) -> dict:
        raise WeatherFetchError("weather forecast fetch failed")

    app.config["FETCH_FORECAST"] = boom
    response = app.test_client().get("/render?date=2026-06-03")

    assert response.status_code == 200
    assert "today-weather-slot" in response.text
    assert "weather-bar" not in response.text


def test_render_today_rows_show_icons(tmp_path: Path) -> None:
    text = (
        _app_with_ics(TODAY_ICS, tmp_path, _generate_ok)
        .test_client()
        .get("/render?date=2026-06-03")
        .text
    )

    assert '<img class="event-icon"' in text
    assert "event-chip" not in text


def test_render_today_rows_fall_back_to_chip(tmp_path: Path) -> None:
    # §7.3: a failed generation leaves the row's title next to a blank chip.
    text = (
        _app_with_ics(TODAY_ICS, tmp_path, _generate_boom)
        .test_client()
        .get("/render?date=2026-06-03")
        .text
    )

    assert "event-chip" in text
    assert '<img class="event-icon"' not in text
    assert "Breakfast" in text


def test_render_tomorrow_shows_next_days_events(tmp_path: Path) -> None:
    # EVENT_ICS's event is on Fri 2026-06-05; rendered against Thu 2026-06-04
    # it is tomorrow's, so its row lands inside the Tomorrow panel.
    text = (
        _app_with_ics(EVENT_ICS, tmp_path, _generate_ok)
        .test_client()
        .get("/render?date=2026-06-04")
        .text
    )

    tomorrow_rows = text.split('class="tomorrow-rows"')[1].split("tomorrow-weather")[0]
    assert "Soccer practice" in tomorrow_rows
    assert '<img class="event-icon"' in tomorrow_rows


def test_render_tomorrow_has_tab_and_reserved_weather_slot() -> None:
    text = _app_with_ics(EMPTY_ICS).test_client().get("/render?date=2026-06-03").text

    assert "tomorrow-tab" in text
    assert "tomorrow-weather-slot" in text
    assert "event-row" not in text  # empty day: no rows at all


def test_render_tomorrow_crosses_into_next_week_on_sunday(tmp_path: Path) -> None:
    # Rendered on Sunday 2026-06-07, tomorrow is Monday of the NEXT week — the
    # event-expansion window must extend past the strip's Mon–Sun (§11).
    ics = (
        "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//test//EN\n"
        "BEGIN:VEVENT\nUID:library\nSUMMARY:Library visit\n"
        "DTSTART;TZID=America/Los_Angeles:20260608T100000\n"
        "DTEND;TZID=America/Los_Angeles:20260608T110000\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    text = (
        _app_with_ics(ics, tmp_path, _generate_ok)
        .test_client()
        .get("/render?date=2026-06-07")
        .text
    )

    assert "Library visit" in text


# One countdown-eligible event on Sat 2026-06-20 (mirrors the real "camping
# trip" test event): rendered against earlier dates it sweeps the §12 tiers —
# 17 sleeps calm, 5 excited, 1 hype, 0 peak.
COUNTDOWN_ICS = (
    "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//test//EN\n"
    "BEGIN:VEVENT\nUID:camping\nSUMMARY:Camping trip\n"
    "DTSTART;TZID=America/Los_Angeles:20260620T120000\n"
    "DTEND;TZID=America/Los_Angeles:20260620T130000\n"
    "DESCRIPTION:countdown_eligible = true\nEND:VEVENT\nEND:VCALENDAR\n"
)


def _countdown_render(tmp_path: Path, date: str, generate: object = None) -> str:
    return (
        _app_with_ics(COUNTDOWN_ICS, tmp_path, generate or _generate_ok)
        .test_client()
        .get(f"/render?date={date}")
        .text
    )


def test_render_countdown_calm_tier(tmp_path: Path) -> None:
    # 17 sleeps out: plain comic border, no burst, no SFX — just the title,
    # the hero, and the sleeps line (calm carries no exclamation point).
    text = _countdown_render(tmp_path, "2026-06-03")

    assert "countdown-calm" in text
    assert "Camping trip" in text
    assert "17 sleeps to go" in text
    assert "17 sleeps to go!" not in text
    assert '<img class="countdown-hero" src="/images/generated/' in text
    assert "countdown-burst" not in text
    assert "countdown-sfx" not in text


def test_render_countdown_excited_tier_shows_burst(tmp_path: Path) -> None:
    # 5 sleeps: the starburst frame and the exclamation-point copy, but none
    # of the hype kit yet.
    text = _countdown_render(tmp_path, "2026-06-15")

    assert "countdown-excited" in text
    assert "countdown-burst" in text
    assert "5 sleeps to go!" in text
    assert "countdown-sfx" not in text


def test_render_countdown_hype_tier_edits_the_hero(tmp_path: Path) -> None:
    # 1 sleep: burst and emphatic copy (no SFX yet — peak only), and the hero
    # swaps to the excited variant, generated by *editing* the base hero's
    # stored bytes (a second seam call carrying base_png).
    calls: list[bytes | None] = []

    def generate(
        api_key: SecretStr,
        *,
        prompt: str,
        size: str,
        model: str,
        base_png: bytes | None = None,
        reference_images: Sequence[bytes] = (),
    ) -> bytes:
        calls.append(base_png)
        return _keyable_png()

    text = _countdown_render(tmp_path, "2026-06-19", generate)

    assert "countdown-hype" in text
    assert "Just 1 more sleep!!" in text
    assert "countdown-sfx" not in text
    assert sum(1 for base in calls if base is not None) == 1


def test_render_countdown_peak_tier_keeps_title_and_hero(tmp_path: Path) -> None:
    # The event day: "It's today!" (escaped by Jinja), title + hero and the
    # SFX pair remain.
    text = _countdown_render(tmp_path, "2026-06-20")

    assert "countdown-peak" in text
    assert "It&#39;s today!" in text
    assert "Camping trip" in text
    assert '<img class="countdown-hero"' in text
    assert "countdown-sfx-1" in text
    assert "countdown-sfx-2" in text


def test_render_countdown_rolls_over_the_day_after(tmp_path: Path) -> None:
    # The day after the event there is no upcoming eligible event left — the
    # panel renders the blank card (§12), footprint preserved.
    text = _countdown_render(tmp_path, "2026-06-21")

    assert "countdown-calm" in text
    assert "countdown-body" not in text
    assert "sleeps to go" not in text


def test_render_countdown_blank_card_without_eligible_events(tmp_path: Path) -> None:
    # EVENT_ICS has an event but no countdown-eligible one; the blank card
    # still renders a bordered panel with no countdown content.
    text = (
        _app_with_ics(EVENT_ICS, tmp_path, _generate_ok)
        .test_client()
        .get("/render?date=2026-06-03")
        .text
    )

    assert "countdown-calm" in text
    assert "countdown-body" not in text


def test_render_countdown_hero_bytes_are_unkeyed(tmp_path: Path) -> None:
    # §12: the hero is displayed as-is — the served PNG must be byte-identical
    # to the generation output (no chroma keying, no crop). This also guards
    # the "Countdown" module-string agreement between store.py and hero.py.
    # On 2026-06-03 the event is outside the strip week, so the hero is the
    # render's only generated image (id 1).
    client = _app_with_ics(COUNTDOWN_ICS, tmp_path, _generate_ok).test_client()
    client.get("/render?date=2026-06-03")

    response = client.get("/images/generated/1")
    assert response.status_code == 200
    assert response.data == _keyable_png()


def test_render_countdown_edit_failure_falls_back_to_base_hero(tmp_path: Path) -> None:
    # The excited edit fails; the page shows the base hero instead (§7.3-style
    # soft failure, no 500, no missing image).
    def generate(
        api_key: SecretStr,
        *,
        prompt: str,
        size: str,
        model: str,
        base_png: bytes | None = None,
        reference_images: Sequence[bytes] = (),
    ) -> bytes:
        if base_png is not None:
            raise ImageGenerationError("image generation failed: Boom")
        return _keyable_png()

    text = _countdown_render(tmp_path, "2026-06-19", generate)

    assert "countdown-hype" in text
    hero_src = text.split('<img class="countdown-hero" src="')[1].split('"')[0]
    assert hero_src.startswith("/images/generated/")
    assert "Camping trip" in text


def test_render_dinner_shows_joined_name_and_hero(tmp_path: Path) -> None:
    # §13: the date's entries ARE the dinner - main + side join into one name
    # and one combined image.
    text = (
        _app_with_ics(EMPTY_ICS, tmp_path, _generate_ok, mealplan_ics=MEAL_ICS)
        .test_client()
        .get("/render?date=2026-06-03")
        .text
    )

    assert "tacos &amp; rice" in text
    assert '<img class="dinner-hero"' in text
    assert "Mystery dinner!" not in text


def test_render_dinner_mystery_when_no_meal_is_planned() -> None:
    text = _app_with_ics(EMPTY_ICS).test_client().get("/render?date=2026-06-03").text

    assert "Mystery dinner!" in text
    assert "dinner-hero" not in text


def test_render_dinner_mystery_when_meal_fetch_fails() -> None:
    # §13: a meal-plan fetch failure degrades to the same friendly card, never
    # a 500 (contrast the family calendar), and the rest of the board renders.
    app = _app_with_ics(EMPTY_ICS)

    def boom(url: object) -> str:
        raise CalendarFetchError("fetch failed")

    app.config["FETCH_MEALPLAN_ICS"] = boom
    response = app.test_client().get("/render?date=2026-06-03")

    assert response.status_code == 200
    assert "Mystery dinner!" in response.text
    assert "WEDNESDAY" in response.text


def test_render_dinner_mystery_on_unparseable_meal_feed() -> None:
    # An unparseable meal feed degrades like a fetch failure (§13); the same
    # bytes in the family feed are a 500 (test_render_returns_500_...).
    response = (
        _app_with_ics(EMPTY_ICS, mealplan_ics="this is not iCalendar data")
        .test_client()
        .get("/render?date=2026-06-03")
    )

    assert response.status_code == 200
    assert "Mystery dinner!" in response.text


def test_render_dinner_generation_failure_keeps_the_name(tmp_path: Path) -> None:
    # §7.3: a hero miss omits the image; "Dinner" + the menu name remain.
    text = (
        _app_with_ics(EMPTY_ICS, tmp_path, _generate_boom, mealplan_ics=MEAL_ICS)
        .test_client()
        .get("/render?date=2026-06-03")
        .text
    )

    assert "tacos &amp; rice" in text
    assert "dinner-hero" not in text
    assert "Mystery dinner!" not in text


def test_render_dinner_hero_is_keyed_transparent(tmp_path: Path) -> None:
    # §13: the dinner hero is a keyed transparent PNG - the served bytes must
    # be the keyed/cropped RGBA, not the raw green-background generation
    # (guards the "Dinner" module string against _UNKEYED_MODULES drift).
    client = _app_with_ics(
        EMPTY_ICS, tmp_path, _generate_ok, mealplan_ics=MEAL_ICS
    ).test_client()
    client.get("/render?date=2026-06-03")

    response = client.get("/images/generated/1")
    assert response.status_code == 200
    assert response.data != _keyable_png()
    assert Image.open(io.BytesIO(response.data)).mode == "RGBA"


def test_render_dinner_reuses_the_image_across_days(tmp_path: Path) -> None:
    # §13: the image is keyed by the dish name and reused across days - the
    # same combined dinner two days apart generates exactly once.
    calls: list[str] = []

    def generate(
        api_key: SecretStr,
        *,
        prompt: str,
        size: str,
        model: str,
        base_png: bytes | None = None,
        reference_images: Sequence[bytes] = (),
    ) -> bytes:
        calls.append(prompt)
        return _keyable_png()

    client = _app_with_ics(
        EMPTY_ICS, tmp_path, generate, mealplan_ics=MEAL_ICS
    ).test_client()
    client.get("/render?date=2026-06-03")
    client.get("/render?date=2026-06-05")

    assert len(calls) == 1


def test_render_dinner_override_wins_end_to_end(tmp_path: Path) -> None:
    # /admin/meals override semantics: the stored name replaces the feed's for
    # that date (display and image key both), even though the feed still
    # carries tacos & rice.
    conn = open_meals_db(tmp_path)
    set_override(conn, date(2026, 6, 3), "Pizza night")
    conn.close()
    calls: list[str] = []

    def generate(
        api_key: SecretStr,
        *,
        prompt: str,
        size: str,
        model: str,
        base_png: bytes | None = None,
        reference_images: Sequence[bytes] = (),
    ) -> bytes:
        calls.append(prompt)
        return _keyable_png()

    text = (
        _app_with_ics(EMPTY_ICS, tmp_path, generate, mealplan_ics=MEAL_ICS)
        .test_client()
        .get("/render?date=2026-06-03")
        .text
    )

    assert "Pizza night" in text
    assert "tacos &amp; rice" not in text
    assert len(calls) == 1
    assert "Pizza night" in calls[0]


# A chore on the render date Wed 2026-06-03 (the `chore:` prefix is stripped by
# the parser), plus a regular event the same day that must NOT leak into the
# chore panel.
CHORE_ICS = (
    "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//test//EN\n"
    "BEGIN:VEVENT\nUID:bed\nSUMMARY:chore: Make bed\n"
    "DTSTART;TZID=America/Los_Angeles:20260603T080000\n"
    "DTEND;TZID=America/Los_Angeles:20260603T083000\nEND:VEVENT\n"
    "BEGIN:VEVENT\nUID:soccer\nSUMMARY:Soccer practice\n"
    "DTSTART;TZID=America/Los_Angeles:20260603T160000\n"
    "DTEND;TZID=America/Los_Angeles:20260603T170000\nEND:VEVENT\n"
    "END:VCALENDAR\n"
)


def test_render_chore_shows_rows_with_tab_and_icon(tmp_path: Path) -> None:
    # §14: today's chore renders as an event row under the labeled "Chores" tab
    # (with its checkbox glyph); the same day's regular event stays out of it.
    text = (
        _app_with_ics(CHORE_ICS, tmp_path, _generate_ok)
        .test_client()
        .get("/render?date=2026-06-03")
        .text
    )

    # Scope to the chore panel (its wrapper up to the next grid cell) so the
    # same day's regular event, which lives in the Today panel, isn't counted.
    section = text.split('<div class="chore">')[1].split("grid-cell", 1)[0]
    assert "chore-tab" in section
    assert "chore-check" in section  # the checkbox glyph
    assert "Make bed" in section
    assert '<img class="event-icon"' in section
    assert "Soccer practice" not in section  # regular event excluded (§6.5)
    assert "no chores today" not in text


def test_render_chore_icon_uses_its_own_module(tmp_path: Path) -> None:
    # §14: the chore icon caches under module "Chores", distinct from Calendar.
    text = (
        _app_with_ics(CHORE_ICS, tmp_path, _generate_ok)
        .test_client()
        .get("/render?date=2026-06-03&debug_images=1")
        .text
    )

    assert "Chores / Make bed" in text


def _chore_event(uid: str, summary: str, hour: int) -> str:
    return (
        f"BEGIN:VEVENT\nUID:{uid}\nSUMMARY:chore: {summary}\n"
        f"DTSTART;TZID=America/Los_Angeles:20260603T{hour:02d}0000\n"
        f"DTEND;TZID=America/Los_Angeles:20260603T{hour:02d}3000\nEND:VEVENT\n"
    )


# Four chores on the render date: the two-column layout threshold (§14).
FOUR_CHORE_ICS = (
    "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//test//EN\n"
    + _chore_event("c1", "Make bed", 8)
    + _chore_event("c2", "Wash dishes", 9)
    + _chore_event("c3", "Feed cat", 10)
    + _chore_event("c4", "Water plants", 11)
    + "END:VCALENDAR\n"
)


def test_render_chore_two_column_when_four_or_more(tmp_path: Path) -> None:
    # §14: four or more chores spill from a single block into two columns.
    text = (
        _app_with_ics(FOUR_CHORE_ICS, tmp_path, _generate_ok)
        .test_client()
        .get("/render?date=2026-06-03")
        .text
    )

    section = text.split('<div class="chore">')[1].split("grid-cell", 1)[0]
    assert "chore-columns" in section
    assert "chore-column" in section
    assert "chore-rows" not in section  # not the single-block layout
    for title in ("Make bed", "Wash dishes", "Feed cat", "Water plants"):
        assert title in section


def test_render_chore_empty_state_is_plain(tmp_path: Path) -> None:
    # No chores on the date: a plain panel with centered text and no tab (§14
    # placeholder). EVENT_ICS's lone event is regular, so the chore list is empty.
    text = (
        _app_with_ics(EVENT_ICS, tmp_path, _generate_ok)
        .test_client()
        .get("/render?date=2026-06-03")
        .text
    )

    assert "no chores today" in text
    assert "chore-tab" not in text
    assert "chore-rows" not in text


def test_render_returns_500_when_fetch_fails() -> None:
    app = create_app()

    def boom(url: object) -> str:
        raise CalendarFetchError("fetch failed")

    app.config["FETCH_ICS"] = boom

    assert app.test_client().get("/render?date=2026-06-03").status_code == 500


def test_render_returns_500_on_unparseable_feed() -> None:
    app = _app_with_ics("this is not iCalendar data")

    assert app.test_client().get("/render?date=2026-06-03").status_code == 500


def test_render_joke_falls_back_when_the_store_is_empty() -> None:
    # No jokes seeded (poison storage has no DB): the panel shows the HTML
    # placeholder rather than failing (§15, §7.3).
    text = _app_with_ics(EMPTY_ICS).test_client().get("/render?date=2026-06-03").text

    assert "No joke today!" in text


def test_render_joke_shows_the_days_hero_image(tmp_path: Path) -> None:
    # Seed three jokes; with the default start date 2026-01-01, day offset 153
    # (to 2026-06-03) mod 3 == 0 selects the first joke, and its unkeyed hero is
    # the only image generated on this event-less board (id 1).
    conn = open_jokes_db(tmp_path)
    add_jokes(conn, ["joke A", "joke B", "joke C"])
    conn.close()

    text = (
        _app_with_ics(EMPTY_ICS, tmp_path, _generate_ok)
        .test_client()
        .get("/render?date=2026-06-03")
        .text
    )

    assert 'class="joke-hero"' in text
    assert "/images/generated/1" in text
    assert 'alt="joke A"' in text


def test_render_joke_is_deterministic_for_a_date(tmp_path: Path) -> None:
    conn = open_jokes_db(tmp_path)
    add_jokes(conn, ["joke A", "joke B", "joke C"])
    conn.close()
    client = _app_with_ics(EMPTY_ICS, tmp_path, _generate_ok).test_client()

    first = client.get("/render?date=2026-06-04").text
    second = client.get("/render?date=2026-06-04").text

    # Day offset 154 mod 3 == 1 -> the second joke, stable across renders.
    assert 'alt="joke B"' in first
    assert 'alt="joke B"' in second
    assert 'alt="joke A"' not in first


def test_render_does_not_leak_secrets(tmp_path: Path) -> None:
    ics_secret = get_settings().family_calendar_ics_url.get_secret_value()
    meals_secret = get_settings().anylist_mealplan_ics_url.get_secret_value()
    api_key = get_settings().openai_api_key.get_secret_value()
    maps_key = get_settings().google_maps_api_key.get_secret_value()
    client = _app_with_ics(
        EVENT_ICS, tmp_path, _generate_boom, mealplan_ics=MEAL_ICS
    ).test_client()
    text = client.get("/render?date=2026-06-03").text

    assert ics_secret not in text
    assert meals_secret not in text
    assert api_key not in text
    assert maps_key not in text
    # The on-disk failure log carries the item and prompt but never a secret.
    failure_log = (tmp_path / "gen_failures.log").read_text()
    assert api_key not in failure_log
    assert ics_secret not in failure_log
    assert meals_secret not in failure_log
