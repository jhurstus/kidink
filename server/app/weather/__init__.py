"""Weather module (spec § Weather): the Google daily forecast and the
Today/Tomorrow weather subpanel view models."""

from app.weather.api import DayForecast, WeatherFetchError, fetch_forecast
from app.weather.view import (
    Condition,
    Outfit,
    TempBar,
    WeatherPanel,
    build_weather,
    figure_name,
    override_high,
)

__all__ = [
    "Condition",
    "DayForecast",
    "Outfit",
    "TempBar",
    "WeatherFetchError",
    "WeatherPanel",
    "build_weather",
    "fetch_forecast",
    "figure_name",
    "override_high",
]
