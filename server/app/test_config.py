from datetime import date

import pytest
from pydantic import SecretStr, ValidationError

from app.config import CONFIG_PATH, Kid, Settings, get_settings


def _settings(**overrides: object) -> Settings:
    """Construct Settings with all required fields, applying ``overrides``."""
    kwargs: dict = {
        "timezone": "UTC",
        "family_calendar_ics_url": SecretStr("https://example.com/cal.ics"),
        "anylist_mealplan_ics_url": SecretStr("https://example.com/meals.ics"),
        "openai_api_key": SecretStr("sk-test-not-a-real-key"),
        "google_maps_api_key": SecretStr("maps-test-not-a-real-key"),
    }
    kwargs.update(overrides)
    return Settings(**kwargs)


def test_timezone_defaults_to_us_pacific() -> None:
    # With no config.toml and no env override, the model default applies; the
    # committed local config also pins US/Pacific, so this holds either way.
    assert get_settings().timezone == "US/Pacific"


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


def test_explicit_timezone_overrides_default() -> None:
    assert _settings(timezone="UTC").timezone == "UTC"


def test_unknown_timezone_is_rejected() -> None:
    # A bad value fails fast at load time (§18) rather than later in a render.
    with pytest.raises(ValidationError):
        _settings(timezone="Mars/Phobos")


def test_family_calendar_ics_url_is_required() -> None:
    # §18: the ICS URL is required config (no default), so it must fail fast if
    # absent rather than surfacing later as a render error.
    assert Settings.model_fields["family_calendar_ics_url"].is_required()


def test_family_calendar_ics_url_is_secret() -> None:
    # Held as SecretStr so it never leaks via repr/str (§18, CLAUDE.md).
    settings = _settings(
        family_calendar_ics_url=SecretStr("https://secret.example/feed.ics")
    )
    assert "secret.example" not in repr(settings)
    assert settings.family_calendar_ics_url.get_secret_value().endswith("feed.ics")


def test_anylist_mealplan_ics_url_is_required_and_secret() -> None:
    # §18: the meal-plan ICS URL is required config, and must never leak via
    # repr/str (it is a secret, unauthenticated URL).
    assert Settings.model_fields["anylist_mealplan_ics_url"].is_required()
    settings = _settings(
        anylist_mealplan_ics_url=SecretStr("https://meals-secret.example/feed.ics")
    )
    assert "meals-secret.example" not in repr(settings)
    assert settings.anylist_mealplan_ics_url.get_secret_value().endswith("feed.ics")


def test_openai_api_key_is_required_and_secret() -> None:
    # §18: the OpenAI key is required, and must never leak via repr/str.
    assert Settings.model_fields["openai_api_key"].is_required()
    settings = _settings(openai_api_key=SecretStr("sk-super-secret"))
    assert "sk-super-secret" not in repr(settings)
    assert settings.openai_api_key.get_secret_value() == "sk-super-secret"


def test_google_maps_api_key_is_required_and_secret() -> None:
    # § Weather / §18: the Maps key is required, and must never leak via
    # repr/str (it rides in the forecast request URL).
    assert Settings.model_fields["google_maps_api_key"].is_required()
    settings = _settings(google_maps_api_key=SecretStr("maps-super-secret"))
    assert "maps-super-secret" not in repr(settings)
    assert settings.google_maps_api_key.get_secret_value() == "maps-super-secret"


def test_weather_location_defaults_to_san_francisco() -> None:
    # § Weather / §18: lat/long have a plausible default so a fresh checkout
    # renders. Assert the model defaults, not the loaded values — the local
    # config.toml legitimately sets the home's real coordinates.
    assert Settings.model_fields["latitude"].default == pytest.approx(37.7749)
    assert Settings.model_fields["longitude"].default == pytest.approx(-122.4194)


def test_app_storage_path_defaults_relative_to_server_dir() -> None:
    # The default ".storage" resolves against server/ (where config.toml lives),
    # not the process CWD, so behavior is stable however the app is launched.
    settings = _settings()
    assert settings.app_storage_path == CONFIG_PATH.parent / ".storage"
    assert settings.app_storage_path.is_absolute()


def test_app_storage_path_relative_value_resolved() -> None:
    settings = _settings(app_storage_path="custom/store")
    assert settings.app_storage_path == CONFIG_PATH.parent / "custom" / "store"


def test_app_storage_path_absolute_value_kept() -> None:
    settings = _settings(app_storage_path="/tmp/kidink-store")
    assert str(settings.app_storage_path) == "/tmp/kidink-store"


def test_kids_defaults_empty() -> None:
    # Assert the model default, not the loaded value: the developer's local
    # config.toml legitimately configures kids (like timezone above).
    field = Settings.model_fields["kids"]
    assert not field.is_required()
    assert field.default_factory() == []


def test_kids_parsed_from_tables() -> None:
    # The TOML shape is a list of {name, label} tables ([[kids]]); order is
    # preserved because it fixes each kid's badge color (§8).
    settings = _settings(
        kids=[{"name": "Julia", "label": "J"}, {"name": "Sam", "label": "S"}]
    )
    assert settings.kids == [Kid(name="Julia", label="J"), Kid(name="Sam", label="S")]


def test_kid_label_is_required() -> None:
    with pytest.raises(ValidationError):
        _settings(kids=[{"name": "Julia"}])


def test_module_model_tiers_defaults_empty() -> None:
    assert _settings().module_model_tiers == {}
    assert (
        _settings(module_model_tiers={"Calendar": "gpt-image-2"}).module_model_tiers[
            "Calendar"
        ]
        == "gpt-image-2"
    )


def test_joke_start_date_default_and_override() -> None:
    # §15: the base date for the modulo joke index. Has a default so a fresh
    # checkout validates; TOML supplies a real date.
    assert Settings.model_fields["joke_start_date"].default == date(2026, 1, 1)
    assert _settings(joke_start_date="2026-07-15").joke_start_date == date(2026, 7, 15)


# --- Inkplate firmware keys (specs/firmware.md) ---------------------------


def test_device_defaults() -> None:
    # All device keys have defaults so a checkout that never flashes a board
    # still starts; `python -m app.firmware` is what requires the real values.
    settings = _settings()
    # No default base URL: it must be a LAN address the board can reach, and a
    # guess would fail only as a device that quietly stops updating.
    assert settings.device_server_base_url == ""
    assert settings.device_fetch_path == "/display"
    assert settings.device_time_path == "/time"
    assert settings.device_wake_cron == "0 5-21/2 * * *"
    assert settings.device_clock_sync_time == "03:15"
    assert settings.device_wifi_timeout_seconds == 60
    assert settings.device_http_timeout_seconds == 300
    assert settings.device_fallback_sleep_seconds == 900
    assert settings.device_repaint_on_button is True
    assert settings.device_posix_tz == ""


def test_device_wifi_credentials_are_secret() -> None:
    # Never leak via repr/str: the passphrase must not reach a log or traceback.
    settings = _settings(
        device_wifi_ssid=SecretStr("HomeNetwork"),
        device_wifi_password=SecretStr("super-secret-passphrase"),
    )
    assert "super-secret-passphrase" not in repr(settings)
    assert "HomeNetwork" not in repr(settings)
    assert settings.device_wifi_password.get_secret_value() == "super-secret-passphrase"


def test_device_wake_cron_validated_at_load() -> None:
    # A typo must fail here, not on a board already hung on a wall.
    with pytest.raises(ValidationError):
        _settings(device_wake_cron="0 99 * * *")
    with pytest.raises(ValidationError):
        _settings(device_wake_cron="not a schedule")
    assert _settings(device_wake_cron="@daily").device_wake_cron == "@daily"


def test_device_fetch_path_must_be_absolute() -> None:
    with pytest.raises(ValidationError):
        _settings(device_fetch_path="display")
    assert _settings(device_fetch_path="/display?raw=1").device_fetch_path.endswith(
        "raw=1"
    )


def test_device_time_path_must_be_absolute() -> None:
    with pytest.raises(ValidationError, match="device_time_path"):
        _settings(device_time_path="time")


def test_device_clock_sync_time_validated_at_load() -> None:
    # Like the wake cron: a typo must fail here, not on a board on a wall.
    with pytest.raises(ValidationError):
        _settings(device_clock_sync_time="25:00")
    with pytest.raises(ValidationError):
        _settings(device_clock_sync_time="0315")
    with pytest.raises(ValidationError):
        _settings(device_clock_sync_time="03:15:00")
    with pytest.raises(ValidationError):
        _settings(device_clock_sync_time="4:05")  # zero-padded HH:MM only
    assert _settings(device_clock_sync_time="04:05").device_clock_sync_time == "04:05"


def test_device_timeouts_are_bounded() -> None:
    # A zero timeout would make every fetch fail; an unbounded one would let a
    # stalled server hold the board awake until the battery died.
    with pytest.raises(ValidationError):
        _settings(device_wifi_timeout_seconds=0)
    with pytest.raises(ValidationError):
        _settings(device_http_timeout_seconds=99_999)
    assert _settings(device_http_timeout_seconds=120).device_http_timeout_seconds == 120


def test_kid_rejects_unknown_keys() -> None:
    # TOML folds bare `key = value` pairs after a [[kids]] header into that
    # table, so a setting appended to the end of config.toml silently becomes a
    # kid field. Forbidding extras turns that into a startup error instead of a
    # setting that is present in the file but never reaches Settings.
    with pytest.raises(ValidationError, match="device_wifi_ssid"):
        _settings(kids=[{"name": "Julia", "label": "J", "device_wifi_ssid": "net"}])
