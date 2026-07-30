"""Tests for the Weather API fetch (spec § Weather). No real sockets — httpx is
faked, mirroring the calendar feed tests."""

from datetime import date

import httpx
import pytest
from pydantic import SecretStr

from app.weather.api import DayForecast, WeatherFetchError, fetch_forecast

_KEY = SecretStr("maps-key-abc123")


def _day_payload(
    year: int = 2026,
    month: int = 7,
    day: int = 9,
    *,
    high: float | None = 61.8,
    unit: str = "FAHRENHEIT",
    feels_like: float | None = 58.4,
    feels_like_unit: str = "FAHRENHEIT",
    daytime: dict | None = None,
) -> dict:
    """One forecastDays element in the API's real shape (trimmed)."""
    entry: dict = {
        "displayDate": {"year": year, "month": month, "day": day},
    }
    if high is not None:
        entry["maxTemperature"] = {"unit": unit, "degrees": high}
    if feels_like is not None:
        entry["feelsLikeMaxTemperature"] = {
            "unit": feels_like_unit,
            "degrees": feels_like,
        }
    if daytime is not None:
        entry["daytimeForecast"] = daytime
    return entry


_DAYTIME = {
    "weatherCondition": {"type": "CLEAR", "description": {"text": "Sunny"}},
    "precipitation": {"probability": {"type": "RAIN", "percent": 10}},
    "thunderstormProbability": 5,
    "cloudCover": 76,
}


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


def test_fetch_parses_days(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"forecastDays": [_day_payload(daytime=_DAYTIME)]}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(payload))

    forecast = fetch_forecast(_KEY, 37.0, -122.0)

    assert forecast == {
        date(2026, 7, 9): DayForecast(
            high_f=61.8,
            condition_type="CLEAR",
            precip_percent=10,
            precip_type="RAIN",
            thunderstorm_percent=5,
            cloud_cover_percent=76,
        )
    }


def test_fetch_requests_imperial_and_passes_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict = {}

    def fake_get(url: str, *, params: dict, timeout: float) -> _FakeResponse:
        seen["url"] = url
        seen["params"] = params
        return _FakeResponse({"forecastDays": []})

    monkeypatch.setattr(httpx, "get", fake_get)
    fetch_forecast(_KEY, 37.5, -122.25)

    assert seen["params"]["unitsSystem"] == "IMPERIAL"
    assert seen["params"]["key"] == _KEY.get_secret_value()
    assert seen["params"]["location.latitude"] == 37.5
    assert seen["params"]["location.longitude"] == -122.25
    # The endpoint paginates at 5 days by default — the whole horizon must be
    # requested as one page.
    assert seen["params"]["pageSize"] == seen["params"]["days"]


def test_missing_daytime_forecast_defaults_benignly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Today's entry legitimately drops daytimeForecast fields once the daytime
    # window has passed; the day must still parse (high drives the bar/outfit).
    payload = {"forecastDays": [_day_payload()]}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(payload))

    forecast = fetch_forecast(_KEY, 37.0, -122.0)

    day = forecast[date(2026, 7, 9)]
    assert day.high_f == 61.8
    assert day.condition_type == ""
    assert day.precip_percent == 0
    assert day.thunderstorm_percent == 0


def test_high_falls_back_to_the_feels_like_high(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A missing or non-Fahrenheit plain high falls back to the "feels like"
    # high rather than dropping the day; and either alone is enough — a day
    # carrying only a usable plain high still parses.
    payload = {
        "forecastDays": [
            _day_payload(day=9, high=None),
            _day_payload(day=10, high=17.2, unit="CELSIUS"),
            {
                "displayDate": {"year": 2026, "month": 7, "day": 11},
                "maxTemperature": {"unit": "FAHRENHEIT", "degrees": 71.3},
            },
        ]
    }
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(payload))

    forecast = fetch_forecast(_KEY, 37.0, -122.0)

    assert forecast[date(2026, 7, 9)].high_f == 58.4
    assert forecast[date(2026, 7, 10)].high_f == 58.4
    assert forecast[date(2026, 7, 11)].high_f == 71.3


def test_unusable_days_are_skipped_leniently(monkeypatch: pytest.MonkeyPatch) -> None:
    # A day missing both highs, a non-Fahrenheit day (misread units would dress
    # the kids wrong), and a non-dict entry are each dropped; the rest survive.
    payload = {
        "forecastDays": [
            {"displayDate": {"year": 2026, "month": 7, "day": 8}},
            _day_payload(day=9, unit="CELSIUS", high=16.5, feels_like=None),
            "not-a-dict",
            _day_payload(day=10, high=70.0),
        ]
    }
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(payload))

    forecast = fetch_forecast(_KEY, 37.0, -122.0)

    assert set(forecast) == {date(2026, 7, 10)}


def test_percent_fields_are_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    daytime = {
        "precipitation": {"probability": {"type": "RAIN", "percent": 140}},
        "thunderstormProbability": -3,
        "cloudCover": "not-a-number",
    }
    payload = {"forecastDays": [_day_payload(daytime=daytime)]}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(payload))

    day = fetch_forecast(_KEY, 37.0, -122.0)[date(2026, 7, 9)]

    assert day.precip_percent == 100
    assert day.thunderstorm_percent == 0
    assert day.cloud_cover_percent == 0


def test_transport_error_raises_without_leaking_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*a: object, **k: object) -> _FakeResponse:
        raise httpx.ConnectError(
            f"failed connecting with key={_KEY.get_secret_value()}"
        )

    monkeypatch.setattr(httpx, "get", boom)
    with pytest.raises(WeatherFetchError) as exc_info:
        fetch_forecast(_KEY, 37.0, -122.0)
    # The raised error must not echo the API key (CLAUDE.md, §18).
    assert _KEY.get_secret_value() not in str(exc_info.value)


def test_http_status_error_raises_weather_fetch_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ErrorResponse:
        def raise_for_status(self) -> None:
            request = httpx.Request("GET", "https://weather.example/?key=secret")
            httpx.Response(403, request=request).raise_for_status()

        def json(self) -> object:
            return {}

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _ErrorResponse())
    with pytest.raises(WeatherFetchError):
        fetch_forecast(_KEY, 37.0, -122.0)


def test_non_object_body_raises_weather_fetch_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _FakeResponse(["not", "a", "dict"])
    )
    with pytest.raises(WeatherFetchError):
        fetch_forecast(_KEY, 37.0, -122.0)
