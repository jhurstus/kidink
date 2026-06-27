import pytest
from pydantic import SecretStr, ValidationError

from app.config import Settings, get_settings


def test_timezone_defaults_to_us_pacific() -> None:
    # With no config.toml and no env override, the model default applies; the
    # committed local config also pins US/Pacific, so this holds either way.
    assert get_settings().timezone == "US/Pacific"


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


def test_explicit_timezone_overrides_default() -> None:
    settings = Settings(
        timezone="UTC",
        family_calendar_ics_url=SecretStr("https://example.com/cal.ics"),
    )
    assert settings.timezone == "UTC"


def test_unknown_timezone_is_rejected() -> None:
    # A bad value fails fast at load time (§18) rather than later in a render.
    with pytest.raises(ValidationError):
        Settings(
            timezone="Mars/Phobos",
            family_calendar_ics_url=SecretStr("https://example.com/cal.ics"),
        )


def test_family_calendar_ics_url_is_required() -> None:
    # §18: the ICS URL is required config (no default), so it must fail fast if
    # absent rather than surfacing later as a render error.
    assert Settings.model_fields["family_calendar_ics_url"].is_required()


def test_family_calendar_ics_url_is_secret() -> None:
    # Held as SecretStr so it never leaks via repr/str (§18, CLAUDE.md).
    settings = Settings(
        timezone="UTC",
        family_calendar_ics_url=SecretStr("https://secret.example/feed.ics"),
    )
    assert "secret.example" not in repr(settings)
    assert settings.family_calendar_ics_url.get_secret_value().endswith("feed.ics")
