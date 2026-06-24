import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings


def test_timezone_defaults_to_us_pacific() -> None:
    # With no config.toml and no env override, the model default applies; the
    # committed local config also pins US/Pacific, so this holds either way.
    assert get_settings().timezone == "US/Pacific"


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


def test_explicit_timezone_overrides_default() -> None:
    assert Settings(timezone="UTC").timezone == "UTC"


def test_unknown_timezone_is_rejected() -> None:
    # A bad value fails fast at load time (§18) rather than later in a render.
    with pytest.raises(ValidationError):
        Settings(timezone="Mars/Phobos")
