"""Build and deploy the Inkplate firmware in `arduino/kidink/` (specs/firmware.md).

Run from `server/`:  ``uv run python -m app.firmware [options]``

The CLI resolves the app settings into the gitignored `arduino/kidink/config.h`,
then drives `arduino-cli` through the helpers in `app.eink.arduino`. It is a
sibling of `app.eink`, which stays the one-shot image-push tool.

Public API:

- :class:`FirmwareConfig`, :func:`from_settings`, :func:`emit_config_header` -
  the header generator.
- :class:`CronSpec`, :func:`parse_cron`, :func:`next_fire` - the reference
  crontab implementation that `arduino/kidink/cron.cpp` mirrors.
- :func:`posix_tz_for` - IANA zone name to POSIX ``TZ`` string.
"""

from app.firmware.config_header import (
    FirmwareConfig,
    FirmwareConfigError,
    c_string_literal,
    emit_config_header,
    from_settings,
    normalize_base_url,
)
from app.firmware.cron import CronError, CronSpec, next_fire, parse_cron
from app.firmware.tz import posix_tz_for

__all__ = [
    "CronError",
    "CronSpec",
    "FirmwareConfig",
    "FirmwareConfigError",
    "c_string_literal",
    "emit_config_header",
    "from_settings",
    "next_fire",
    "normalize_base_url",
    "parse_cron",
    "posix_tz_for",
]
