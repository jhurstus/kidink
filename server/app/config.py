"""Application configuration (spec §18).

A Pydantic ``BaseSettings`` model loaded once at startup from an optional
``config.toml`` (in the ``server/`` directory, next to ``pyproject.toml``) and/or
environment variables, and validated up front — so a bad value fails fast with a
clear error rather than surfacing later as a render bug.
"""

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

# config.toml lives in server/ (one level up from this package). It is gitignored;
# see config.example.toml for its shape. A missing file is fine — the model
# defaults apply.
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"


class Settings(BaseSettings):
    """Validated application configuration (spec §18)."""

    model_config = SettingsConfigDict(
        toml_file=CONFIG_PATH,
        env_prefix="KIDINK_",
        extra="ignore",
    )

    timezone: str = "US/Pacific"
    """Display timezone (e.g. ``US/Pacific``); drives date resolution (§3.4)."""

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {value!r}") from exc
        return value

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Precedence: explicit init args > environment > config.toml.
        return (init_settings, env_settings, TomlConfigSettingsSource(settings_cls))


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, loaded and validated once."""
    return Settings()
