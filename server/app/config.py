"""Application configuration (spec §18).

A Pydantic ``BaseSettings`` model loaded once at startup from an optional
``config.toml`` (in the ``server/`` directory, next to ``pyproject.toml``) and/or
environment variables, and validated up front — so a bad value fails fast with a
clear error rather than surfacing later as a render bug.
"""

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, SecretStr, field_validator
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


class Kid(BaseModel):
    """One child shown on the board (spec §8, §18)."""

    name: str
    """Full name; the future pose/figure mapping (§18) will key off this."""

    label: str
    """Initials rendered next to the kid's events (§8), matched case-insensitively
    against events' ``kids`` override values (§6.4)."""


class Settings(BaseSettings):
    """Validated application configuration (spec §18)."""

    model_config = SettingsConfigDict(
        toml_file=CONFIG_PATH,
        env_prefix="KIDINK_",
        extra="ignore",
    )

    timezone: str = "US/Pacific"
    """Display timezone (e.g. ``US/Pacific``); drives date resolution."""

    family_calendar_ics_url: SecretStr
    """Private Google Calendar ICS feed — events + chores.

    Required: a missing value fails fast at startup. Held as ``SecretStr`` so
    the unauthenticated URL never lands in a repr, log, or traceback.  Supplied
    via ``config.toml`` or ``KIDINK_FAMILY_CALENDAR_ICS_URL``.
    """

    openai_api_key: SecretStr
    """OpenAI API key for AI image generation (§7.2).

    Required: a missing value fails fast at startup. Held as ``SecretStr`` so the
    key never lands in a repr, log, or traceback. Supplied via ``config.toml`` or
    ``KIDINK_OPENAI_API_KEY``.
    """

    google_maps_api_key: SecretStr
    """Google Maps Platform API key for the Weather API (§ Weather, §18).

    Required: a missing value fails fast at startup. Held as ``SecretStr`` so the
    key never lands in a repr, log, or traceback (it rides in the request URL's
    query string, so the URL is a secret too). Supplied via ``config.toml`` or
    ``KIDINK_GOOGLE_MAPS_API_KEY``.
    """

    latitude: float = 37.7749
    longitude: float = -122.4194
    """Weather location (§ Weather, §18). Defaults to downtown San Francisco so
    a fresh checkout renders plausible weather; set the home's real coordinates
    in ``config.toml``."""

    app_storage_path: Path = Path(".storage")
    """Root for all app-managed storage (§18): ``sqlite.db``, ``gen_images/``,
    ``prompt_images/``. A relative path is resolved against the ``server/``
    directory (where ``config.toml`` lives). Created lazily on first write, not
    at config load."""

    module_model_tiers: dict[str, str] = Field(default_factory=dict)
    """Per-module image-model overrides (§18), e.g. ``{"Calendar": "gpt-image-2"}``.
    Modules absent from the map use the default model."""

    kids: list[Kid] = Field(default_factory=list)
    """The children shown on the board (§8, §18), in display order — the order
    fixes each kid's badge color and label position on event rows."""

    @field_validator("app_storage_path")
    @classmethod
    def _resolve_storage_path(cls, value: Path) -> Path:
        value = value.expanduser()
        if not value.is_absolute():
            value = CONFIG_PATH.parent / value
        return value

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
    # Required fields are populated from the TOML file / env by pydantic-settings at
    # runtime, which the type checker can't see — hence the suppression.
    return Settings()  # ty: ignore[missing-argument]
