"""Tests for the weather image-inventory admin page (§ Weather inventory)."""

from app import create_app
from app.config import get_settings
from app.weather.view import Condition, Outfit


def test_admin_weather_lists_the_whole_inventory() -> None:
    # Every condition icon and every configured kid's outfit figure appears,
    # each referencing its static asset path (broken until the file lands).
    text = create_app().test_client().get("/admin/weather").text

    for condition in Condition:
        assert f"img/weather/{condition}.png" in text
    for i in range(len(get_settings().kids)):
        for outfit in Outfit:
            assert f"img/weather/kid{i}_{outfit}.png" in text


def test_admin_weather_uses_pinned_display_sizes() -> None:
    # The grid previews assets at the exact board sizes from weather.css.
    text = create_app().test_client().get("/admin/weather").text

    assert 'width="180" height="180"' in text
    assert 'width="250" height="250"' in text
