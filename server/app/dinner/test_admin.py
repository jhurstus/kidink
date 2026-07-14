import io
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
from flask import Flask
from PIL import Image
from pydantic import SecretStr

from app import create_app
from app.calendar import CalendarFetchError
from app.config import get_settings
from app.dinner.hero import dinner_image_spec
from app.dinner.overrides import get_override, open_meals_db, set_override
from app.images import ImageGenerationError
from app.images.db import get_or_create_record

# 2026-06-03 13:00 in US/Pacific (the developer config's timezone) - "today"
# for every test, away from midnight so the local date is unambiguous.
NOW = datetime(2026, 6, 3, 20, 0, tzinfo=UTC)
TODAY = date(2026, 6, 3)

# Meals inside the 14-day window: a main + side today, one meal five days out.
MEAL_ICS = (
    "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//AnyList//\n"
    "BEGIN:VEVENT\nUID:meal-main\nSUMMARY:Tacos\n"
    "DTSTART;VALUE=DATE:20260603\nEND:VEVENT\n"
    "BEGIN:VEVENT\nUID:meal-side\nSUMMARY:Rice\n"
    "DTSTART;VALUE=DATE:20260603\nEND:VEVENT\n"
    "BEGIN:VEVENT\nUID:meal-later\nSUMMARY:Soup\n"
    "DTSTART;VALUE=DATE:20260608\nEND:VEVENT\n"
    "END:VCALENDAR\n"
)


def _keyable_png() -> bytes:
    """A red rectangle on a pure-green key background - keys cleanly (§7.2)."""
    pixels = np.tile(np.array((0, 255, 0), np.uint8), (480, 800, 1))
    pixels[40:440, 40:760] = (220, 40, 40)
    out = io.BytesIO()
    Image.fromarray(pixels, "RGB").save(out, format="PNG")
    return out.getvalue()


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


def _app(
    tmp_path: Path, generate: object = None, mealplan_ics: str = MEAL_ICS
) -> Flask:
    app = create_app()
    app.config["NOW"] = NOW
    app.config["FETCH_MEALPLAN_ICS"] = lambda url: mealplan_ics
    app.config["GENERATE_IMAGE_BYTES"] = generate or _generate_unexpected
    app.config["APP_STORAGE_PATH"] = tmp_path
    return app


def test_lists_fourteen_days_from_today(tmp_path: Path) -> None:
    text = _app(tmp_path).test_client().get("/admin/meals").text

    assert text.count('class="day"') == 14
    assert "2026-06-03" in text  # today, first row
    assert "2026-06-16" in text  # today + 13, last row
    assert "2026-06-17" not in text
    # Meal-less dates still get a row (with the override form).
    assert text.count("no meal planned") == 12


def test_shows_feed_names_on_their_dates(tmp_path: Path) -> None:
    text = _app(tmp_path).test_client().get("/admin/meals").text

    assert "tacos &amp; rice" in text
    assert "soup" in text


def test_override_roundtrip_beats_the_feed_name(tmp_path: Path) -> None:
    client = _app(tmp_path).test_client()

    response = client.post(
        "/admin/meals/2026-06-03/override", data={"name": "Pizza night"}
    )
    assert response.status_code == 302

    text = client.get("/admin/meals").text
    assert "Pizza night" in text
    assert '<span class="overridden">tacos &amp; rice</span>' in text
    conn = open_meals_db(tmp_path)
    try:
        assert get_override(conn, TODAY) == "Pizza night"
    finally:
        conn.close()


def test_saving_an_empty_override_clears_it(tmp_path: Path) -> None:
    client = _app(tmp_path).test_client()
    client.post("/admin/meals/2026-06-03/override", data={"name": "Pizza night"})

    client.post("/admin/meals/2026-06-03/override", data={"name": "   "})

    conn = open_meals_db(tmp_path)
    try:
        assert get_override(conn, TODAY) is None
    finally:
        conn.close()


def test_malformed_date_is_a_400(tmp_path: Path) -> None:
    client = _app(tmp_path).test_client()

    assert client.post("/admin/meals/garbage/override").status_code == 400
    assert client.post("/admin/meals/garbage/generate").status_code == 400


def test_links_to_the_image_admin_when_the_record_exists(tmp_path: Path) -> None:
    conn = open_meals_db(tmp_path)
    record, _ = get_or_create_record(conn, dinner_image_spec("tacos & rice"), "p")
    conn.close()

    text = _app(tmp_path).test_client().get("/admin/meals").text

    assert f"/admin/images?img={record.id}" in text
    # The dates without a record offer the generate action instead.
    assert "Generate image" in text


def test_generate_creates_the_record_and_redirects_to_it(tmp_path: Path) -> None:
    client = _app(tmp_path, _generate_ok).test_client()

    response = client.post("/admin/meals/2026-06-03/generate")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/images?img=1")
    stored = Image.open(tmp_path / "gen_images" / "1.png")
    assert stored.mode == "RGBA"
    # The record carries the meal's logical key, so the render path reuses it.
    text = client.get("/admin/meals").text
    assert "/admin/images?img=1" in text


def test_generate_respects_the_override(tmp_path: Path) -> None:
    conn = open_meals_db(tmp_path)
    set_override(conn, TODAY, "Pizza night")
    conn.close()
    prompts: list[str] = []

    def generate(
        api_key: SecretStr,
        *,
        prompt: str,
        size: str,
        model: str,
        base_png: bytes | None = None,
        reference_images: Sequence[bytes] = (),
    ) -> bytes:
        prompts.append(prompt)
        return _keyable_png()

    _app(tmp_path, generate).test_client().post("/admin/meals/2026-06-03/generate")

    assert len(prompts) == 1
    assert "Pizza night" in prompts[0]


def test_generate_without_a_meal_redirects_with_an_error(tmp_path: Path) -> None:
    client = _app(tmp_path).test_client()

    response = client.post("/admin/meals/2026-06-04/generate", follow_redirects=True)

    assert "no meal to generate an image for" in response.text


def test_generate_failure_redirects_with_an_error(tmp_path: Path) -> None:
    client = _app(tmp_path, _generate_boom).test_client()

    response = client.post("/admin/meals/2026-06-03/generate", follow_redirects=True)

    assert "generation failed" in response.text


def test_feed_failure_still_renders_the_page(tmp_path: Path) -> None:
    # Overrides must stay editable while Anylist is down: the page serves with
    # an error note, nameless rows, and the override forms intact.
    app = _app(tmp_path)

    def boom(url: object) -> str:
        raise CalendarFetchError("fetch failed")

    app.config["FETCH_MEALPLAN_ICS"] = boom
    response = app.test_client().get("/admin/meals")

    assert response.status_code == 200
    assert "meal plan fetch failed" in response.text
    assert response.text.count("/override") == 14


def test_page_does_not_leak_the_feed_secret(tmp_path: Path) -> None:
    secret = get_settings().anylist_mealplan_ics_url.get_secret_value()

    text = _app(tmp_path).test_client().get("/admin/meals").text

    assert secret not in text
