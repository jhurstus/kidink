"""Tests for the weather subpanel view model (spec § Weather)."""

from datetime import date, timedelta

import pytest

from app.config import Kid
from app.weather.api import DayForecast
from app.weather.view import Condition, Outfit, build_weather, override_high

_TARGET = date(2026, 7, 9)
_KIDS = [Kid(name="Alice", label="A"), Kid(name="Bob", label="B")]


def _day(
    *,
    high: float = 66.0,
    condition: str = "PARTLY_CLOUDY",
    precip: int = 10,
    precip_type: str = "RAIN",
    thunder: int = 0,
    cloud: int = 45,
) -> DayForecast:
    return DayForecast(
        high_f=high,
        condition_type=condition,
        precip_percent=precip,
        precip_type=precip_type,
        thunderstorm_percent=thunder,
        cloud_cover_percent=cloud,
    )


def _condition(day: DayForecast) -> Condition:
    return build_weather(_TARGET, day).condition


# --- Condition bucketing (the working enum→bucket mapping; full audit §20) ---


def test_thunder_by_probability_wins_over_everything() -> None:
    day = _day(thunder=30, precip=90, precip_type="SNOW", condition="SNOW")
    assert _condition(day) == Condition.THUNDER


def test_thunder_by_condition_type() -> None:
    assert _condition(_day(condition="SCATTERED_THUNDERSTORMS")) == Condition.THUNDER


def test_snow_needs_wet_probability_and_snow_type() -> None:
    assert _condition(_day(precip=25, precip_type="SNOW")) == Condition.SNOW
    assert _condition(_day(precip=40, condition="LIGHT_SNOW")) == Condition.SNOW


def test_steady_rain_types_bucket_as_rain() -> None:
    assert _condition(_day(precip=60, condition="RAIN_SHOWERS")) == Condition.RAIN
    assert _condition(_day(precip=60, condition="HEAVY_RAIN")) == Condition.RAIN


def test_other_wet_days_bucket_as_light_rain() -> None:
    assert (
        _condition(_day(precip=30, condition="CHANCE_OF_SHOWERS"))
        == Condition.LIGHT_RAIN
    )
    # A plain sky type carrying a wet PoP is still (light) rain.
    assert (
        _condition(_day(precip=40, condition="PARTLY_CLOUDY")) == Condition.LIGHT_RAIN
    )


def test_dry_probability_is_not_rain() -> None:
    assert _condition(_day(precip=24, condition="CLEAR", cloud=10)) == Condition.SUNNY


def test_dry_sky_leans_on_stated_condition_type() -> None:
    # Observed live: Google calls a 76%-cover day "Sunny"/CLEAR — the stated
    # sky type wins over raw cloud cover.
    assert _condition(_day(condition="CLEAR", cloud=76)) == Condition.SUNNY
    assert _condition(_day(condition="MOSTLY_CLEAR", cloud=40)) == Condition.SUNNY
    assert _condition(_day(condition="PARTLY_CLOUDY")) == Condition.PARTLY_CLOUDY
    assert _condition(_day(condition="MOSTLY_CLOUDY")) == Condition.CLOUDY
    assert _condition(_day(condition="CLOUDY")) == Condition.CLOUDY


def test_unknown_dry_type_falls_back_to_cloud_cover() -> None:
    assert _condition(_day(condition="HAZE", cloud=30)) == Condition.SUNNY
    assert _condition(_day(condition="HAZE", cloud=70)) == Condition.PARTLY_CLOUDY
    assert _condition(_day(condition="HAZE", cloud=71)) == Condition.CLOUDY


# --- Outfit selection (§ Weather: PoP ≥ 25% → rain, else by the high) ---


@pytest.mark.parametrize(
    ("high", "precip", "outfit"),
    [
        (66.0, 25, Outfit.RAIN),  # rain overrides temperature
        (45.0, 24, Outfit.COLD),
        (59.9, 0, Outfit.COLD),
        (60.0, 0, Outfit.NORMAL),  # 60–72 inclusive is normal
        (72.0, 0, Outfit.NORMAL),
        (72.1, 0, Outfit.HOT),
        (95.0, 0, Outfit.HOT),
    ],
)
def test_outfit_cutoffs(high: float, precip: int, outfit: Outfit) -> None:
    assert build_weather(_TARGET, _day(high=high, precip=precip)).outfit == outfit


# --- Kid flip-flop (§ Weather: date-seeded, Today/Tomorrow offset) ---


def test_featured_kid_alternates_daily() -> None:
    first = build_weather(_TARGET, _day(), _KIDS).kid_name
    second = build_weather(_TARGET + timedelta(days=1), _day(), _KIDS).kid_name
    third = build_weather(_TARGET + timedelta(days=2), _day(), _KIDS).kid_name

    assert first != second
    assert first == third


def test_slot_offsets_the_flip_flop() -> None:
    # Today (slot 0) and Tomorrow (slot 1) feature different kids the same day.
    today = build_weather(_TARGET, _day(), _KIDS, slot=0)
    tomorrow = build_weather(_TARGET, _day(), _KIDS, slot=1)

    assert today.kid_name != tomorrow.kid_name


def test_figure_is_kid_index_and_outfit_asset_name() -> None:
    # The asset name carries the kid's config-order INDEX, never the name —
    # the committed image files must not leak the kids' names (§ Weather).
    panel = build_weather(_TARGET, _day(high=80.0), _KIDS)

    expected_index = _TARGET.toordinal() % len(_KIDS)
    assert panel.kid_name == _KIDS[expected_index].name
    assert panel.figure == f"kid{expected_index}_hot"


def test_no_kids_configured_yields_no_figure() -> None:
    panel = build_weather(_TARGET, _day())

    assert panel.kid_name is None
    assert panel.figure is None
    assert panel.outfit == Outfit.NORMAL  # the outfit itself is still computed


# --- Debug overrides (§3.5: weather_icon / weather_outfit / weather_temp) ---


def test_condition_and_outfit_overrides_replace_derivation() -> None:
    panel = build_weather(
        _TARGET,
        _day(),  # would derive partly_cloudy / normal
        _KIDS,
        condition_override=Condition.SNOW,
        outfit_override=Outfit.RAIN,
    )

    assert panel.condition == Condition.SNOW
    assert panel.outfit == Outfit.RAIN
    assert panel.figure is not None
    assert panel.figure.endswith("_rain")  # the figure asset follows the outfit
    assert panel.figure.startswith("kid")  # ...and stays index-keyed


def test_override_high_passes_through_without_a_debug_temp() -> None:
    day = _day(high=60.0)

    assert override_high(day, None) is day
    assert override_high(None, None) is None


def test_override_high_replaces_the_high_only() -> None:
    day = override_high(_day(high=60.0, precip=30), 95)

    assert day is not None
    assert day.high_f == 95.0
    assert day.precip_percent == 30  # the rest of the forecast is untouched


def test_override_high_synthesizes_a_day_when_forecast_is_missing() -> None:
    day = override_high(None, 80)

    assert day is not None
    assert day.high_f == 80.0
    # The synthesized day is clear and dry: hot outfit, sunny bucket.
    panel = build_weather(_TARGET, day, _KIDS)
    assert panel.outfit == Outfit.HOT
    assert panel.condition == Condition.SUNNY


# --- Temperature bar (§ Weather bands; arrow measured from the hot top) ---


def test_high_is_rounded_for_the_label() -> None:
    assert build_weather(_TARGET, _day(high=61.8)).bar.high_f == 62


@pytest.mark.parametrize(
    ("high", "percent"),
    [
        (30.0, 100.0),  # clamped at the cold end
        (42.5, 100.0),
        (50.5, 80.0),  # coldest/cold band edge
        (59.5, 60.0),  # cold/mild band edge
        (63.5, 50.0),  # mid-mild = mid-bar
        (67.5, 40.0),  # mild/warm band edge
        (75.5, 20.0),  # warm/hottest band edge
        (83.5, 0.0),
        (105.0, 0.0),  # clamped at the hot end
    ],
)
def test_arrow_position_at_band_edges(high: float, percent: float) -> None:
    assert build_weather(_TARGET, _day(high=high)).bar.arrow_percent == percent


def test_arrow_position_is_monotonic_in_the_high() -> None:
    highs = [float(h) for h in range(30, 106)]
    positions = [build_weather(_TARGET, _day(high=h)).bar.arrow_percent for h in highs]

    assert positions == sorted(positions, reverse=True)
