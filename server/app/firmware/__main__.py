"""Deploy CLI: generate `config.h`, compile the sketch, flash the Inkplate.

Run from `server/`:  uv run python -m app.firmware [options]
"""

import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.eink import arduino
from app.firmware.config_header import (
    FirmwareConfig,
    FirmwareConfigError,
    emit_config_header,
    from_settings,
)
from app.firmware.cron import next_fire, parse_cron

SKETCH_DIR = Path(__file__).resolve().parents[3] / "arduino" / "kidink"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.firmware",
        description="Build and flash the kidink firmware for the Inkplate 13 SPECTRA.",
    )
    parser.add_argument(
        "--sketch-dir",
        type=Path,
        default=SKETCH_DIR,
        help="Arduino sketch directory to write config.h into (default: %(default)s)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path(".firmware-out"),
        help="artifact dir holding build/ (default: %(default)s)",
    )
    parser.add_argument("--url", help="override the full fetch URL (must be http://)")
    parser.add_argument("--cron", help="override the wake schedule (crontab syntax)")
    parser.add_argument("--tz", help="override the device's POSIX TZ string")
    parser.add_argument(
        "--next-fires",
        type=int,
        default=5,
        metavar="N",
        help="preview the next N wake times (default: %(default)s)",
    )
    parser.add_argument(
        "--print-config",
        action="store_true",
        help="print the generated header (secrets redacted) and exit",
    )
    parser.add_argument(
        "--port", default=arduino.DEFAULT_PORT, help="serial port for upload"
    )
    parser.add_argument("--fqbn", default=arduino.FQBN, help="arduino-cli board FQBN")
    parser.add_argument(
        "--no-compile",
        action="store_true",
        help="stop after writing config.h (implies --no-upload)",
    )
    parser.add_argument(
        "--no-upload", action="store_true", help="compile but don't flash"
    )
    return parser.parse_args(argv)


def _print_schedule(config: FirmwareConfig, timezone: str, count: int) -> None:
    """Preview the next wake times, so a typo'd schedule is caught before flashing."""
    if count <= 0:
        return
    spec = parse_cron(config.wake_cron)
    # Wall-clock "now" in the display timezone: what the device's RTC will hold.
    moment = datetime.now(ZoneInfo(timezone)).replace(tzinfo=None)
    print(f"Schedule {config.wake_cron!r} -> next {count} wakes ({timezone}):")
    for _ in range(count):
        upcoming = next_fire(spec, moment)
        if upcoming is None:
            print("  (never fires again)")
            return
        print(f"  {upcoming:%a %Y-%m-%d %H:%M}")
        moment = upcoming


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()
    try:
        config = from_settings(
            settings,
            url_override=args.url,
            cron_override=args.cron,
            tz_override=args.tz,
        )
    except FirmwareConfigError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc

    header = emit_config_header(config)
    if args.print_config:
        print(emit_config_header(config, redact=True), end="")
        return 0

    print(f"Fetch URL:  {config.fetch_url}")
    print(
        f"Clock sync: daily at "
        f"{config.clock_sync_hour:02d}:{config.clock_sync_minute:02d} "
        f"from {config.time_url}"
    )
    print(f"Timezone:   {settings.timezone} -> {config.posix_tz}")
    print(
        f"Timeouts:   wifi {config.wifi_timeout_seconds}s, "
        f"http {config.http_timeout_seconds}s"
    )
    _print_schedule(config, settings.timezone, args.next_fires)

    sketch_dir = args.sketch_dir
    if not sketch_dir.is_dir():
        raise SystemExit(f"Sketch directory {sketch_dir} does not exist.")
    header_path = sketch_dir / "config.h"
    header_path.write_text(header)
    print(f"Wrote {header_path} (gitignored - holds Wi-Fi credentials)")

    if args.no_compile:
        return 0
    build_dir = args.out_dir / "build"
    arduino.run_checked(arduino.compile_cmd(sketch_dir, build_dir, args.fqbn))
    if args.no_upload:
        return 0
    port = arduino.resolve_port(args.port)
    arduino.run_checked(arduino.upload_cmd(sketch_dir, build_dir, port, args.fqbn))
    print("Flashed. The device wakes, fetches, and paints on the schedule above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
