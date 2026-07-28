"""The generated `config.h`: escaping, required defines, and secret handling."""

import pytest
from pydantic import SecretStr

from app.config import Settings, get_settings
from app.firmware.config_header import (
    FRAME_BYTES,
    FirmwareConfigError,
    c_string_literal,
    emit_config_header,
    from_settings,
    normalize_base_url,
)


def _settings(**overrides: object) -> Settings:
    """A valid Settings with device fields filled in, bypassing the TOML source."""
    # Bare `dict` (as in test_config.py): a precise value type makes the splat
    # unassignable to Settings' individually-typed fields.
    base: dict = {
        "family_calendar_ics_url": SecretStr("https://example.com/cal.ics"),
        "anylist_mealplan_ics_url": SecretStr("https://example.com/meals.ics"),
        "openai_api_key": SecretStr("sk-test"),
        "google_maps_api_key": SecretStr("maps-test"),
        "device_wifi_ssid": SecretStr("Test Network"),
        "device_wifi_password": SecretStr("hunter2"),
        "device_server_base_url": "kidink.local:5051",
    }
    base.update(overrides)
    return Settings(**base)


# --- escaping -------------------------------------------------------------


def test_plain_ascii_passes_through() -> None:
    assert c_string_literal("hello world") == '"hello world"'


def test_quotes_and_backslashes_escaped() -> None:
    assert c_string_literal(r'a"b\c') == r'"a\"b\\c"'


def test_non_ascii_uses_octal_not_hex() -> None:
    """C hex escapes are greedy, so `\\xc3` would swallow a following hex digit.

    A passphrase like "cafe" with an accent followed by an 'a' would silently
    become a different string; three-digit octal cannot run on.
    """
    literal = c_string_literal("caféa")
    assert literal == '"caf\\303\\251a"'
    assert "\\x" not in literal


def test_control_characters_escaped() -> None:
    literal = c_string_literal("a\nb\tc")
    assert "\n" not in literal
    assert literal == '"a\\012b\\011c"'


def test_nul_rejected() -> None:
    with pytest.raises(FirmwareConfigError, match="NUL"):
        c_string_literal("a\x00b")


# --- base URL normalization ----------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("localhost:5051", "http://localhost:5051"),
        ("http://localhost:5051", "http://localhost:5051"),
        ("http://localhost:5051/", "http://localhost:5051"),
        ("  kidink.local  ", "http://kidink.local"),
        ("192.168.1.20:8080", "http://192.168.1.20:8080"),
    ],
)
def test_base_url_normalization(raw: str, expected: str) -> None:
    assert normalize_base_url(raw) == expected


def test_https_rejected() -> None:
    """The firmware has no TLS stack, so an https:// URL must fail loudly."""
    with pytest.raises(FirmwareConfigError, match="http://"):
        normalize_base_url("https://kidink.local")


def test_empty_base_url_rejected() -> None:
    with pytest.raises(FirmwareConfigError, match="device_server_base_url"):
        normalize_base_url("   ")


# --- from_settings --------------------------------------------------------


def test_defaults_produce_the_expected_url() -> None:
    config = from_settings(_settings())
    assert config.fetch_url == "http://kidink.local:5051/display"
    assert config.time_url == "http://kidink.local:5051/time"
    assert config.wake_cron == "0 5-21/2 * * *"
    assert config.posix_tz == "PST8PDT,M3.2.0,M11.1.0"
    assert config.http_timeout_seconds == 300


def test_clock_sync_time_resolves_to_hour_and_minute() -> None:
    config = from_settings(_settings(device_clock_sync_time="04:05"))
    assert (config.clock_sync_hour, config.clock_sync_minute) == (4, 5)
    default = from_settings(_settings())
    assert (default.clock_sync_hour, default.clock_sync_minute) == (3, 15)


def test_url_override_still_derives_the_time_url() -> None:
    """--url names the frame endpoint; the clock-sync endpoint must follow its
    origin, or a test flash against another server would sync from the wrong
    (possibly unreachable) one."""
    config = from_settings(
        _settings(), url_override="http://other.local:8080/display?date=2026-06-03"
    )
    assert config.time_url == "http://other.local:8080/time"


def test_missing_base_url_is_actionable() -> None:
    """There is no default: flashing a board with no reachable server is a
    silent failure (it would just never update), so this must stop the deploy."""
    with pytest.raises(FirmwareConfigError) as excinfo:
        from_settings(_settings(device_server_base_url=""))
    assert "KIDINK_DEVICE_SERVER_BASE_URL" in str(excinfo.value)


def test_missing_ssid_is_actionable_and_secret_free() -> None:
    with pytest.raises(FirmwareConfigError) as excinfo:
        from_settings(_settings(device_wifi_ssid=SecretStr("")))
    message = str(excinfo.value)
    assert "KIDINK_DEVICE_WIFI_SSID" in message
    assert "hunter2" not in message


def test_overrides_win() -> None:
    config = from_settings(
        _settings(),
        url_override="http://other.local/display?date=2026-06-03",
        cron_override="@daily",
        tz_override="UTC0",
    )
    assert config.fetch_url == "http://other.local/display?date=2026-06-03"
    assert config.wake_cron == "@daily"
    assert config.posix_tz == "UTC0"


def test_url_override_must_be_http() -> None:
    with pytest.raises(FirmwareConfigError, match="http://"):
        from_settings(_settings(), url_override="https://other.local/display")


def test_explicit_posix_tz_beats_derivation() -> None:
    config = from_settings(_settings(device_posix_tz="EST5EDT,M3.2.0,M11.1.0"))
    assert config.posix_tz == "EST5EDT,M3.2.0,M11.1.0"


def test_bad_cron_override_is_reported() -> None:
    with pytest.raises(FirmwareConfigError, match="device_wake_cron"):
        from_settings(_settings(), cron_override="61 * * * *")


# --- header emission ------------------------------------------------------

_REQUIRED_DEFINES = [
    "KIDINK_WIFI_SSID",
    "KIDINK_WIFI_PASSWORD",
    "KIDINK_FETCH_URL",
    "KIDINK_TIME_URL",
    "KIDINK_WAKE_CRON",
    "KIDINK_CLOCK_SYNC_HOUR",
    "KIDINK_CLOCK_SYNC_MINUTE",
    "KIDINK_POSIX_TZ",
    "KIDINK_WIFI_TIMEOUT_S",
    "KIDINK_HTTP_TIMEOUT_MS",
    "KIDINK_FALLBACK_SLEEP_S",
    "KIDINK_REPAINT_ON_BUTTON",
    "KIDINK_MIN_SLEEP_S",
    "KIDINK_MAX_SLEEP_S",
    "KIDINK_BOOT_DEADLINE_MS",
    "KIDINK_ETAG_MAX",
    "KIDINK_FRAME_WIDTH",
    "KIDINK_FRAME_HEIGHT",
    "KIDINK_FRAME_BYTES",
]


def test_every_define_is_present() -> None:
    header = emit_config_header(from_settings(_settings()))
    for name in _REQUIRED_DEFINES:
        assert f"#define {name} " in header


def test_frame_bytes_matches_the_wire_contract() -> None:
    """960,000 bytes: what /display serves and what pack.py emits."""
    assert FRAME_BYTES == 960_000
    header = emit_config_header(from_settings(_settings()))
    line = next(text for text in header.splitlines() if "KIDINK_FRAME_BYTES" in text)
    assert line.split()[-1] == "960000"


def test_etag_buffer_fits_a_quoted_sha256() -> None:
    """/display serves a quoted 64-char hex digest, so 66 bytes plus a NUL."""
    from app.firmware.config_header import ETAG_MAX

    assert len('"' + "0" * 64 + '"') + 1 <= ETAG_MAX


def test_timeout_is_converted_to_milliseconds() -> None:
    header = emit_config_header(
        from_settings(_settings(device_http_timeout_seconds=45))
    )
    assert "#define KIDINK_HTTP_TIMEOUT_MS" in header
    assert " 45000\n" in header


def test_secrets_appear_only_unredacted() -> None:
    config = from_settings(_settings())
    assert "hunter2" in emit_config_header(config)
    redacted = emit_config_header(config, redact=True)
    assert "hunter2" not in redacted
    assert "Test Network" not in redacted
    assert "<redacted>" in redacted


def test_output_is_deterministic() -> None:
    config = from_settings(_settings())
    assert emit_config_header(config) == emit_config_header(config)


def test_header_has_a_do_not_commit_warning() -> None:
    header = emit_config_header(from_settings(_settings()))
    assert "gitignored" in header
    assert "#pragma once" in header


def test_settings_repr_hides_the_wifi_password() -> None:
    """A traceback or log line must never carry the passphrase."""
    assert "hunter2" not in repr(_settings())


def test_default_settings_parse_but_need_a_server() -> None:
    """A fresh checkout validates; the SSID and base URL are what must be filled in."""
    settings = get_settings()
    assert settings.device_fetch_path == "/display"
    assert settings.device_server_base_url == ""
    assert settings.device_wake_cron == "0 5-21/2 * * *"
