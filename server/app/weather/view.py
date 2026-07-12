"""View model for the weather subpanel (spec § Weather).

Shared by Today (§10.3) and Tomorrow (§11), both condition icon → clothing
kid → temperature bar (Tomorrow packs the row horizontally compact). Reduces
one day's :class:`~app.weather.api.DayForecast` to the three UI decisions:

- the seven-bucket **condition icon** (Google's condition enum leaning on the
  thunderstorm probability and precip type, cloud cover as the fallback);
- the featured kid's **outfit** (rain overrides temperature, then the
  "feels like" high), with the kid flip-flopped between Today and Tomorrow
  off the date seed;
- the **temperature bar** arrow position for the day's "feels like" high —
  both systems dress and read for what the day feels like, not the dry-bulb
  high.

Everything here is a pure function of the target date and the forecast (§3.4):
the flip-flop is seeded off the date, never the wall clock.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
from itertools import pairwise

from app.config import Kid
from app.weather.api import DayForecast


class Condition(StrEnum):
    """The seven hand-made condition-icon buckets (§ Weather); each value is
    also the static asset name the icon image will be served under (§7.6)."""

    SUNNY = "sunny"
    PARTLY_CLOUDY = "partly_cloudy"
    CLOUDY = "cloudy"
    LIGHT_RAIN = "light_rain"
    RAIN = "rain"
    THUNDER = "thunder"
    SNOW = "snow"


class Outfit(StrEnum):
    """The four clothing-kid outfits (§ Weather)."""

    HOT = "hot"
    NORMAL = "normal"
    COLD = "cold"
    RAIN = "rain"


# Daytime PoP at or above which the day counts as precipitating — used both for
# the rain/snow condition buckets and for the rain-gear outfit override.
_PRECIP_PERCENT = 25
# Daytime thunderstorm probability at or above which the icon shows thunder.
_THUNDER_PERCENT = 30

# Precipitating condition types that read as *steady* rain; every other rainy
# type ("chance of showers", "scattered showers", the light variants, or a
# plain sky type with a wet PoP) stays in the gentler light-rain bucket.
_STEADY_RAIN_TYPES = frozenset(
    {
        "RAIN",
        "HEAVY_RAIN",
        "RAIN_SHOWERS",
        "HEAVY_RAIN_SHOWERS",
        "MODERATE_TO_HEAVY_RAIN",
        "RAIN_PERIODICALLY_HEAVY",
        "WIND_AND_RAIN",
        "RAIN_AND_SNOW",
    }
)

# Dry-sky condition types Google calls outright. Preferred over raw cloud cover
# (observed: a "Sunny"/CLEAR day carrying 76% average cover); cover thresholds
# below are the fallback for types outside this table. The full enum→bucket
# audit is still open (§20).
_SKY_TYPES: dict[str, Condition] = {
    "CLEAR": Condition.SUNNY,
    "MOSTLY_CLEAR": Condition.SUNNY,
    "PARTLY_CLOUDY": Condition.PARTLY_CLOUDY,
    "MOSTLY_CLOUDY": Condition.CLOUDY,
    "CLOUDY": Condition.CLOUDY,
}

# Outfit cutoffs on the "feels like" high (§ Weather): < 60 °F cold,
# 60–72 normal, > 72 hot.
_COLD_BELOW_F = 60
_HOT_ABOVE_F = 72

# Piecewise-linear anchors mapping the day's "feels like" high onto the bar as
# a fraction from the TOP (hot end). Interior anchors are the § Weather band boundaries,
# each sitting at the shared edge of two equal-height fifths; the outer anchors
# extend the end bands by the same ~8–9 °F pitch so extreme highs keep moving
# within them before clamping.
_ARROW_ANCHORS = [
    (42.5, 1.0),
    (50.5, 0.8),
    (59.5, 0.6),
    (67.5, 0.4),
    (75.5, 0.2),
    (83.5, 0.0),
]


@dataclass(frozen=True)
class TempBar:
    """The temperature bar's dynamic parts (the bands are template-authored)."""

    high_f: int
    """The day's "feels like" high, rounded for the arrow's label."""

    arrow_percent: float
    """Arrow position along the bar: 0 = top (hottest), 100 = bottom."""


@dataclass(frozen=True)
class WeatherPanel:
    """The weather subpanel view model (``templates/modules/weather.html``)."""

    condition: Condition
    """Condition icon bucket (§10.3/§11)."""

    kid_name: str | None
    """Featured kid's name, or ``None`` when no kids are configured."""

    outfit: Outfit

    figure: str | None
    """Static asset name for the clothing figure (§7.6), e.g. ``kid0_rain``
    (config-order index, never the kid's name); ``None`` when no kids are
    configured."""

    bar: TempBar


def figure_name(kid_index: int, outfit: Outfit) -> str:
    """Static asset name for a kid's clothing figure (§7.6), e.g. ``kid0_rain``.

    Keyed by the kid's position in the config order — never the kid's name,
    which must not land in the repo (the asset files are committed)."""
    return f"kid{kid_index}_{outfit}"


def build_weather(
    target: date,
    day: DayForecast,
    kids: Sequence[Kid] = (),
    slot: int = 0,
    *,
    condition_override: Condition | None = None,
    outfit_override: Outfit | None = None,
) -> WeatherPanel:
    """Build the subpanel view model for ``target``'s forecast ``day``.

    ``slot`` is the panel's day offset (0 = Today, 1 = Tomorrow): the featured
    kid alternates daily off the date seed, and the offset keeps the two panels
    on different kids the same day (§ Weather flip-flop).

    The ``*_override`` keywords back the ``?weather_icon`` / ``?weather_outfit``
    debug args (§3.5): a non-``None`` value replaces the derived condition
    bucket / outfit verbatim (the figure asset follows the outfit).
    """
    kid_index = (target.toordinal() + slot) % len(kids) if kids else None
    kid = kids[kid_index] if kid_index is not None else None
    outfit = outfit_override or _select_outfit(day)
    return WeatherPanel(
        condition=condition_override or _bucket_condition(day),
        kid_name=kid.name if kid else None,
        outfit=outfit,
        figure=figure_name(kid_index, outfit) if kid_index is not None else None,
        bar=TempBar(
            high_f=round(day.feels_like_high_f),
            arrow_percent=round(_arrow_fraction(day.feels_like_high_f) * 100, 1),
        ),
    )


def override_high(day: DayForecast | None, high_f: int | None) -> DayForecast | None:
    """Apply the ``?weather_temp`` debug override (§3.5).

    Replaces the day's "feels like" high — the outfit and temperature bar
    re-derive from it — and synthesizes a clear, dry day when no forecast is
    available at all, so the subpanel can be previewed without live weather.
    ``None`` for ``high_f`` passes ``day`` through untouched.
    """
    if high_f is None:
        return day
    if day is None:
        day = DayForecast(
            feels_like_high_f=0.0,
            condition_type="",
            precip_percent=0,
            precip_type="RAIN",
            thunderstorm_percent=0,
            cloud_cover_percent=0,
        )
    return replace(day, feels_like_high_f=float(high_f))


def _bucket_condition(day: DayForecast) -> Condition:
    """Map the forecast onto the seven icon buckets (§ Weather).

    Precedence: thunder, then precipitation (snow vs. steady vs. light rain),
    then the dry sky — leaning on the thunderstorm probability and the precip
    RAIN/SNOW type for the wet buckets and on the stated sky type / cloud cover
    for the dry ones, per the spec.
    """
    if day.thunderstorm_percent >= _THUNDER_PERCENT or "THUNDER" in day.condition_type:
        return Condition.THUNDER
    if day.precip_percent >= _PRECIP_PERCENT:
        if day.precip_type == "SNOW" or "SNOW" in day.condition_type:
            return Condition.SNOW
        if day.condition_type in _STEADY_RAIN_TYPES:
            return Condition.RAIN
        return Condition.LIGHT_RAIN
    sky = _SKY_TYPES.get(day.condition_type)
    if sky is not None:
        return sky
    if day.cloud_cover_percent <= 30:
        return Condition.SUNNY
    if day.cloud_cover_percent <= 70:
        return Condition.PARTLY_CLOUDY
    return Condition.CLOUDY


def _select_outfit(day: DayForecast) -> Outfit:
    """Pick the clothing-kid outfit (§ Weather): rain gear at PoP ≥ 25%
    overrides temperature; otherwise by the day's "feels like" high."""
    if day.precip_percent >= _PRECIP_PERCENT:
        return Outfit.RAIN
    if day.feels_like_high_f < _COLD_BELOW_F:
        return Outfit.COLD
    if day.feels_like_high_f > _HOT_ABOVE_F:
        return Outfit.HOT
    return Outfit.NORMAL


def _arrow_fraction(high_f: float) -> float:
    """The arrow's bar position for ``high_f`` as a fraction from the top,
    interpolated between the :data:`_ARROW_ANCHORS` and clamped at the ends."""
    if high_f <= _ARROW_ANCHORS[0][0]:
        return _ARROW_ANCHORS[0][1]
    for (lo, lo_frac), (hi, hi_frac) in pairwise(_ARROW_ANCHORS):
        if high_f <= hi:
            return lo_frac + (high_f - lo) / (hi - lo) * (hi_frac - lo_frac)
    return _ARROW_ANCHORS[-1][1]
