"""Generate `arduino/kidink/config.h` from the app settings.

The sketch needs Wi-Fi credentials, a fetch URL, and a schedule, none of which
may be committed (CLAUDE.md). Emitting them into a gitignored header - the same
trick `app.eink` uses for `mockup.h` - keeps every secret in `config.toml` /
`KIDINK_*` and out of tracked source, and lets the firmware read them as compile
-time constants with no runtime parsing or string building.
"""

from dataclasses import dataclass

from app.config import Settings, parse_clock_sync_time
from app.firmware.cron import parse_cron
from app.firmware.tz import posix_tz_for

# The device frame: the packed 4bpp buffer `/display` serves (spec §3, eink-demo
# §3). Two pixels per byte, so the byte count is half the pixel count.
FRAME_WIDTH = 1600
FRAME_HEIGHT = 1200
FRAME_BYTES = FRAME_WIDTH * FRAME_HEIGHT // 2

# Baked policy constants - deliberately not config keys, since they exist to
# guard against hardware quirks rather than to be tuned (see specs/firmware.md).
MIN_SLEEP_SECONDS = 90
"""Floor on a scheduled sleep. Without it, an RTC that drifts a couple of seconds
early re-fires the same cron minute and repaints a second time."""

MAX_SLEEP_SECONDS = 86_400
"""Ceiling on a scheduled sleep. The PCF85063A alarm matches day-of-month, so it
cannot express a horizon beyond about a month; a day is a comfortable margin."""

BOOT_DEADLINE_MS = 180_000
"""Hard cap on one awake cycle. The library's `waitForBusy()` spins forever on a
panel fault, which would flatten the battery; the watchdog task forces sleep."""

ETAG_MAX = 80
"""Bytes reserved for the stored ETag, including quotes and the NUL. `/display`
serves a quoted SHA-256 hex digest (66 bytes), so this has room to spare."""


class FirmwareConfigError(ValueError):
    """The settings cannot produce a valid device configuration."""


@dataclass(frozen=True)
class FirmwareConfig:
    """Fully resolved values ready to be written into `config.h`."""

    wifi_ssid: str
    wifi_password: str
    fetch_url: str
    time_url: str
    wake_cron: str
    clock_sync_hour: int
    clock_sync_minute: int
    posix_tz: str
    wifi_timeout_seconds: int
    http_timeout_seconds: int
    fallback_sleep_seconds: int
    repaint_on_button: bool


def c_string_literal(value: str) -> str:
    """Quote `value` as a C string literal, escaping to plain ASCII.

    Non-ASCII bytes become **octal** escapes, not hex: C hex escapes are greedy,
    so `"\\xc3" "a"` would parse as a single escape and silently corrupt (say) a
    Wi-Fi passphrase with an accented character. `\\NNN` is exactly three digits
    and cannot run on.
    """
    if "\x00" in value:
        raise FirmwareConfigError("value contains a NUL byte")
    out = ['"']
    for byte in value.encode("utf-8"):
        char = chr(byte)
        if char == "\\":
            out.append("\\\\")
        elif char == '"':
            out.append('\\"')
        elif 0x20 <= byte <= 0x7E:
            out.append(char)
        else:
            out.append(f"\\{byte:03o}")
    out.append('"')
    return "".join(out)


def normalize_base_url(raw: str) -> str:
    """Return `raw` as an `http://host[:port]` origin with no trailing slash."""
    url = raw.strip().rstrip("/")
    if not url:
        raise FirmwareConfigError(
            "device_server_base_url is empty - set it in config.toml or "
            "KIDINK_DEVICE_SERVER_BASE_URL to an address the device can reach "
            "on the LAN, e.g. '192.168.1.20:5051'. Not 'localhost': that would "
            "resolve on the board, to itself."
        )
    if url.startswith("https://"):
        raise FirmwareConfigError(
            "device_server_base_url must be http:// - the firmware has no TLS "
            "stack (spec: the board fetches over the home LAN)"
        )
    if "://" not in url:
        # A bare host:port is the documented default form; add the scheme so the
        # emitted URL is complete.
        url = f"http://{url}"
    if not url.startswith("http://"):
        raise FirmwareConfigError(
            f"device_server_base_url must be http://, got {raw!r}"
        )
    return url


def _origin_of(url: str) -> str:
    """The `http://host[:port]` part of an already-validated http:// URL."""
    rest = url[len("http://") :]
    slash = rest.find("/")
    return "http://" + (rest if slash < 0 else rest[:slash])


def from_settings(
    settings: Settings,
    *,
    url_override: str | None = None,
    cron_override: str | None = None,
    tz_override: str | None = None,
) -> FirmwareConfig:
    """Resolve `settings` (plus CLI overrides) into a `FirmwareConfig`.

    Raises `FirmwareConfigError` with an actionable, secret-free message when a
    required value is missing or malformed.
    """
    ssid = settings.device_wifi_ssid.get_secret_value()
    if not ssid:
        raise FirmwareConfigError(
            "device_wifi_ssid is empty - set it in config.toml or "
            "KIDINK_DEVICE_WIFI_SSID"
        )
    password = settings.device_wifi_password.get_secret_value()

    if url_override:
        fetch_url = url_override
        if not fetch_url.startswith("http://"):
            raise FirmwareConfigError(f"--url must be http://, got {fetch_url!r}")
        # The override names the frame endpoint; the clock-sync endpoint lives
        # on the same server, so derive it from the override's origin.
        time_url = _origin_of(fetch_url) + settings.device_time_path
    else:
        base = normalize_base_url(settings.device_server_base_url)
        fetch_url = base + settings.device_fetch_path
        time_url = base + settings.device_time_path

    cron = cron_override or settings.device_wake_cron
    try:
        parse_cron(cron)
    except ValueError as exc:
        raise FirmwareConfigError(f"device_wake_cron: {exc}") from exc

    # Settings validates this on load; re-parsing here keeps a directly-built
    # Settings honest too (same belt-and-braces as the cron above).
    try:
        sync_hour, sync_minute = parse_clock_sync_time(settings.device_clock_sync_time)
    except ValueError as exc:
        raise FirmwareConfigError(str(exc)) from exc

    if tz_override:
        posix_tz = tz_override
    elif settings.device_posix_tz:
        posix_tz = settings.device_posix_tz
    else:
        try:
            posix_tz = posix_tz_for(settings.timezone)
        except ValueError as exc:
            raise FirmwareConfigError(
                f"cannot derive a POSIX TZ string from timezone "
                f"{settings.timezone!r}: {exc}. Set device_posix_tz explicitly."
            ) from exc

    return FirmwareConfig(
        wifi_ssid=ssid,
        wifi_password=password,
        fetch_url=fetch_url,
        time_url=time_url,
        wake_cron=cron,
        clock_sync_hour=sync_hour,
        clock_sync_minute=sync_minute,
        posix_tz=posix_tz,
        wifi_timeout_seconds=settings.device_wifi_timeout_seconds,
        http_timeout_seconds=settings.device_http_timeout_seconds,
        fallback_sleep_seconds=settings.device_fallback_sleep_seconds,
        repaint_on_button=settings.device_repaint_on_button,
    )


def emit_config_header(config: FirmwareConfig, *, redact: bool = False) -> str:
    """Render `config.h`. With `redact`, secrets are replaced for safe printing."""
    ssid = "<redacted>" if redact else config.wifi_ssid
    password = "<redacted>" if redact else config.wifi_password
    defines: list[tuple[str, str]] = [
        ("KIDINK_WIFI_SSID", c_string_literal(ssid)),
        ("KIDINK_WIFI_PASSWORD", c_string_literal(password)),
        ("KIDINK_FETCH_URL", c_string_literal(config.fetch_url)),
        ("KIDINK_TIME_URL", c_string_literal(config.time_url)),
        ("KIDINK_WAKE_CRON", c_string_literal(config.wake_cron)),
        ("KIDINK_CLOCK_SYNC_HOUR", str(config.clock_sync_hour)),
        ("KIDINK_CLOCK_SYNC_MINUTE", str(config.clock_sync_minute)),
        ("KIDINK_POSIX_TZ", c_string_literal(config.posix_tz)),
        ("KIDINK_WIFI_TIMEOUT_S", str(config.wifi_timeout_seconds)),
        ("KIDINK_HTTP_TIMEOUT_MS", str(config.http_timeout_seconds * 1000)),
        ("KIDINK_FALLBACK_SLEEP_S", str(config.fallback_sleep_seconds)),
        ("KIDINK_REPAINT_ON_BUTTON", "1" if config.repaint_on_button else "0"),
        ("KIDINK_MIN_SLEEP_S", str(MIN_SLEEP_SECONDS)),
        ("KIDINK_MAX_SLEEP_S", str(MAX_SLEEP_SECONDS)),
        ("KIDINK_BOOT_DEADLINE_MS", str(BOOT_DEADLINE_MS)),
        ("KIDINK_ETAG_MAX", str(ETAG_MAX)),
        ("KIDINK_FRAME_WIDTH", str(FRAME_WIDTH)),
        ("KIDINK_FRAME_HEIGHT", str(FRAME_HEIGHT)),
        ("KIDINK_FRAME_BYTES", str(FRAME_BYTES)),
    ]
    width = max(len(name) for name, _ in defines)
    body = "\n".join(f"#define {name:<{width}} {value}" for name, value in defines)
    return (
        "// Generated by `uv run python -m app.firmware` from the kidink server\n"
        "// settings. Holds Wi-Fi credentials: gitignored, never commit it.\n"
        "#pragma once\n"
        "\n"
        f"{body}\n"
    )
