"""Google Maps Platform Weather API client (spec § Weather).

The only weather module that touches the network. The API key rides in the
request URL's query string, so — like the calendar feed URL (§6.1) — it is
passed as ``SecretStr`` and no raised error message may echo the request URL.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any, cast

import httpx
from pydantic import SecretStr

_FORECAST_URL = "https://weather.googleapis.com/v1/forecast/days:lookup"
# Forecast horizon to request. The board only renders today and tomorrow, but
# the ?date= debug arg (§3.5) can preview any day — 10 days is the API maximum,
# so overrides within that horizon still get real weather.
_DAYS = 10


class WeatherFetchError(Exception):
    """Raised when the daily forecast can't be retrieved or parsed.

    The message is deliberately URL-free (the URL carries the API key); the
    render route degrades to a weather-less board rather than failing.
    """


@dataclass(frozen=True)
class DayForecast:
    """One day's daytime forecast, reduced to the fields the board uses."""

    high_f: float
    """The day's actual high, °F (the forecast is requested in imperial
    units); drives the outfit cutoffs and the temperature bar. Falls back to
    the "feels like" high when the API omits a usable actual value."""

    condition_type: str
    """Google's raw condition enum for the daytime, e.g. ``PARTLY_CLOUDY``."""

    precip_percent: int
    """Daytime probability of precipitation, 0–100."""

    precip_type: str
    """The precipitation probability's type, e.g. ``RAIN`` / ``SNOW``."""

    thunderstorm_percent: int
    """Daytime thunderstorm probability, 0–100."""

    cloud_cover_percent: int
    """Daytime average cloud cover, 0–100."""


def fetch_forecast(
    api_key: SecretStr,
    latitude: float,
    longitude: float,
    *,
    timeout: float = 10.0,
) -> dict[date, DayForecast]:
    """GET the daily forecast for ``latitude``/``longitude`` (spec § Weather).

    Requests imperial units (the API defaults to Celsius) and returns each
    forecast day's daytime fields keyed by its display date. Days the API
    returns in an unusable shape are skipped leniently; transport/HTTP errors
    and a non-JSON-object body raise :class:`WeatherFetchError`.
    """
    params: dict[str, str | float | int] = {
        "key": api_key.get_secret_value(),
        "location.latitude": latitude,
        "location.longitude": longitude,
        "days": _DAYS,
        # The endpoint paginates at 5 days by default; ask for the whole
        # horizon in one page so no nextPageToken loop is needed.
        "pageSize": _DAYS,
        "unitsSystem": "IMPERIAL",
    }
    try:
        response = httpx.get(_FORECAST_URL, params=params, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        # Never include the URL or the underlying exception text (both carry
        # the API key in the query string).
        raise WeatherFetchError("weather forecast fetch failed") from exc
    if not isinstance(payload, dict):
        raise WeatherFetchError("weather forecast response was not a JSON object")
    days: dict[date, DayForecast] = {}
    for entry in payload.get("forecastDays", []):
        parsed = _parse_day(entry)
        if parsed is not None:
            days[parsed[0]] = parsed[1]
    return days


def _parse_day(entry: object) -> tuple[date, DayForecast] | None:
    """Parse one ``forecastDays`` element, or ``None`` if it is unusable.

    The display date and a Fahrenheit high are essential (clothing and the
    temperature bar hang off it — misread units would dress the kids wrong),
    though the "feels like" high serves as its fallback; the daytime condition
    fields default benignly when absent, as they legitimately are for today's
    entry once the daytime window has passed.
    """
    if not isinstance(entry, dict):
        return None
    # ty narrows the isinstance to dict[Never, Never]; re-widen the value shape.
    fields = cast("dict[str, Any]", entry)
    display = fields.get("displayDate") or {}
    try:
        day = date(display["year"], display["month"], display["day"])
    except TypeError, KeyError, ValueError:
        return None
    high_f = _fahrenheit(fields.get("maxTemperature"))
    if high_f is None:
        high_f = _fahrenheit(fields.get("feelsLikeMaxTemperature"))
    if high_f is None:
        return None
    daytime = fields.get("daytimeForecast") or {}
    condition = (daytime.get("weatherCondition") or {}).get("type")
    probability = (daytime.get("precipitation") or {}).get("probability") or {}
    return day, DayForecast(
        high_f=high_f,
        condition_type=str(condition or ""),
        precip_percent=_as_percent(probability.get("percent")),
        precip_type=str(probability.get("type") or "RAIN"),
        thunderstorm_percent=_as_percent(daytime.get("thunderstormProbability")),
        cloud_cover_percent=_as_percent(daytime.get("cloudCover")),
    )


def _fahrenheit(value: object) -> float | None:
    """A temperature object's °F reading, or ``None`` if it is unusable."""
    if not isinstance(value, dict):
        return None
    # ty narrows the isinstance to dict[Never, Never]; re-widen the value shape.
    fields = cast("dict[str, Any]", value)
    if fields.get("unit") != "FAHRENHEIT":
        return None
    try:
        return float(fields["degrees"])
    except TypeError, KeyError, ValueError:
        return None


def _as_percent(value: object) -> int:
    """Coerce a probability/coverage field to a clamped 0–100 int."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return min(100, max(0, int(value)))
