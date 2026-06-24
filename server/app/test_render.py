from datetime import UTC, datetime

from app import create_app

DAY_NAMES = [
    "MONDAY",
    "TUESDAY",
    "WEDNESDAY",
    "THURSDAY",
    "FRIDAY",
    "SATURDAY",
    "SUNDAY",
]


def test_render_route_ok() -> None:
    response = create_app().test_client().get("/render?date=2026-06-03")

    assert response.status_code == 200


def test_render_contains_all_seven_day_names() -> None:
    text = create_app().test_client().get("/render?date=2026-06-03").text

    for name in DAY_NAMES:
        assert name in text


def test_render_contains_formatted_corner_date() -> None:
    text = create_app().test_client().get("/render?date=2026-06-03").text

    assert "June 3, 2026" in text


def test_render_bolds_only_today() -> None:
    # 2026-06-03 is a Wednesday.
    text = create_app().test_client().get("/render?date=2026-06-03").text

    assert 'class="day-name is-today">WEDNESDAY</div>' in text
    assert text.count("is-today") == 1


def test_render_emits_exact_outer_panel_params() -> None:
    text = create_app().test_client().get("/render?date=2026-06-03").text

    assert "--panel-w:1540px" in text
    assert "--panel-h:190px" in text
    assert "--panel-bg:#e1dcca" in text
    assert "#bbb4a2" in text  # outer halftone color
    assert "--origin-angle:330deg" in text  # the two outer halftone fields
    assert "--origin-angle:150deg" in text
    assert "--magnitude:21%" in text


def test_render_has_strip_structure() -> None:
    text = create_app().test_client().get("/render?date=2026-06-03").text

    assert "strip-groups" in text
    assert "day-row" in text
    assert "date-box" in text


def test_render_default_date_uses_injected_now() -> None:
    # No ?date= -> the resolved date comes from the injected clock, not the wall
    # clock. 2026-06-23 18:00 UTC is still Jun 23 in US/Pacific, a Tuesday.
    app = create_app()
    app.config["NOW"] = datetime(2026, 6, 23, 18, 0, tzinfo=UTC)
    text = app.test_client().get("/render").text

    assert "June 23, 2026" in text
    assert 'class="day-name is-today">TUESDAY</div>' in text


def test_render_is_deterministic() -> None:
    client = create_app().test_client()

    first = client.get("/render?date=2026-06-03").text
    second = client.get("/render?date=2026-06-03").text

    assert first == second
