"""Application configuration (spec §18).

A Pydantic ``BaseSettings`` model loaded once at startup from an optional
``config.toml`` (in the ``server/`` directory, next to ``pyproject.toml``) and/or
environment variables, and validated up front — so a bad value fails fast with a
clear error rather than surfacing later as a render bug.
"""

from datetime import date
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
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

    # Reject unknown keys. TOML folds every bare `key = value` after a `[[kids]]`
    # header into that table, so a setting appended to the end of config.toml
    # lands here instead of at the top level. Ignoring it (pydantic's default)
    # makes that a silent no-op - the value simply never reaches Settings, and
    # the symptom is a "missing" setting that is visibly present in the file.
    model_config = ConfigDict(extra="forbid")

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

    anylist_mealplan_ics_url: SecretStr
    """Anylist meal-plan ICS feed - dinner (§6.1, §13).

    Required: a missing value fails fast at startup. Held as ``SecretStr`` so
    the unauthenticated URL never lands in a repr, log, or traceback.  Supplied
    via ``config.toml`` or ``KIDINK_ANYLIST_MEALPLAN_ICS_URL``.
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

    joke_start_date: date = date(2026, 1, 1)
    """Base date for the daily joke index (§15): the joke shown on a date is
    ``jokes[(date - joke_start_date).days % N]``, so the curated list loops. The
    joke list itself lives in the DB (the ``jokes`` table), managed on
    ``/admin/jokes``."""

    # --- Inkplate firmware (specs/firmware.md) -------------------------------
    # Consumed only by `python -m app.firmware`, which bakes them into the
    # gitignored arduino/kidink/config.h. All are optional here so the server
    # still starts on a checkout that never flashes a device; the CLI is what
    # fails fast on a missing one.

    device_wifi_ssid: SecretStr = SecretStr("")
    """Wi-Fi network the panel joins. ``SecretStr`` so it cannot leak through a
    repr or traceback; the CLI requires a non-empty value."""

    device_wifi_password: SecretStr = SecretStr("")
    """Wi-Fi passphrase. Never logged, not even its length. An open network is
    the only reason to leave this empty."""

    device_server_base_url: str = ""
    """Origin the device fetches from, e.g. ``192.168.1.20:5051``. A bare
    ``host:port`` gains an ``http://`` scheme; ``https://`` is rejected, since the
    firmware carries no TLS stack. No default: it has to be an address the board
    can reach over the LAN, and any guess (``localhost``) would be wrong in a way
    that only shows up as a device that silently never updates."""

    device_fetch_path: str = "/display"
    """Path (and any query) the device requests. Kept configurable so the
    firmware never hard-codes the endpoint's spelling."""

    device_wake_cron: str = "0 5-21/2 * * *"
    """Wake schedule as a 5-field crontab expression, evaluated on-device against
    local wall-clock time. Validated at load, so a typo fails fast here rather
    than on a board that has already been sealed to a wall."""

    device_wifi_timeout_seconds: int = Field(default=60, ge=5, le=600)
    """Deadline for Wi-Fi association. On expiry the device sleeps without
    painting; there are no retries."""

    device_http_timeout_seconds: int = Field(default=300, ge=5, le=600)
    """Deadline for the whole ``/display`` fetch. Generous because a cold image
    cache makes the server screenshot and quantize a fresh board inline (§3.6)."""

    device_fallback_sleep_seconds: int = Field(default=900, ge=60, le=86_400)
    """Sleep length when the schedule cannot be computed - i.e. the RTC has never
    been set *and* the fetch that would have carried a ``Date`` header failed."""

    device_repaint_on_button: bool = True
    """A WAKE press omits ``If-None-Match``, so it always produces a visible
    refresh instead of a silent 304."""

    device_posix_tz: str = ""
    """Override for the device's POSIX ``TZ`` string (e.g.
    ``PST8PDT,M3.2.0,M11.1.0``). Empty derives it from :attr:`timezone`."""

    @field_validator("app_storage_path")
    @classmethod
    def _resolve_storage_path(cls, value: Path) -> Path:
        value = value.expanduser()
        if not value.is_absolute():
            value = CONFIG_PATH.parent / value
        return value

    @field_validator("device_wake_cron")
    @classmethod
    def _check_wake_cron(cls, value: str) -> str:
        # Imported here rather than at module scope: app.firmware pulls in the
        # header emitter and the deploy CLI, none of which the server needs.
        from app.firmware.cron import parse_cron

        parse_cron(value)
        return value

    @field_validator("device_fetch_path")
    @classmethod
    def _check_fetch_path(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError(f"device_fetch_path must start with '/', got {value!r}")
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
