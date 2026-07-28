"""The device's `/time` body parser feeds rtc.setDate()/setTime() directly.

`arduino/kidink/timesync.cpp` runs only on the ESP32, so it is compiled for the
host here and checked against Python's own datetime. The weekday matters as
much as the fields: it must be tm_wday-convention (0 = Sunday) because the
RTC's alarm comparator matches the weekday register (specs/firmware.md §8
quirks 4 and 6) - a wrong value silently stops scheduled wakes.
"""

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_HARNESS = _REPO_ROOT / "arduino" / "kidink_tests" / "timesync_host_test.cpp"

pytestmark = pytest.mark.skipif(
    shutil.which("g++") is None, reason="no host C++ compiler available"
)


def _fields(text: str) -> str:
    """Expected parse, via the standard library: the six fields plus the
    weekday in tm_wday convention (isoweekday: Mon=1..Sun=7, so mod 7)."""
    parsed = datetime.fromisoformat(text.strip().replace(" ", "T", 1))
    weekday = parsed.isoweekday() % 7
    return (
        f"{parsed.year} {parsed.month} {parsed.day} "
        f"{parsed.hour} {parsed.minute} {parsed.second} {weekday}"
    )


_VALID = [
    # The exact shape /time serves (its trailing newline is stripped by the
    # line protocol here; trailing-space tolerance stands in for it below).
    "2026-07-28 03:15:00",
    "2026-07-28 03:15:00   ",  # trailing whitespace, as after the body newline
    "2028-02-29 12:34:56",  # leap day
    "2026-12-31 23:59:59",  # year boundary
    "2020-01-01 00:00:00",  # lower edge of the sanity window
    "2099-12-31 23:59:59",  # upper edge: setDate() stores two-digit years
]

_INVALID = [
    ("", "empty"),
    ("2026-07-28", "date only"),
    ("2026-07-28T03:15:00", "T separator"),
    ("2026-13-01 00:00:00", "month out of range"),
    ("2026-00-10 00:00:00", "month zero"),
    ("2026-02-30 00:00:00", "impossible calendar day"),
    ("2027-02-29 00:00:00", "leap day in a non-leap year"),
    ("2026-07-28 24:00:00", "hour out of range"),
    ("2026-07-28 03:60:00", "minute out of range"),
    ("2026-07-28 03:15:60", "second out of range"),
    ("2019-12-31 23:59:59", "before the sanity window"),
    ("2100-01-01 00:00:00", "past the two-digit RTC year"),
    ("2026-7-28 03:15:00", "unpadded month"),
    ("2026-07-28 03:15:00 extra", "trailing junk"),
    ("Sat, 25 Jul 2026 18:02:38 GMT", "HTTP Date shape"),
]


@pytest.fixture(scope="module")
def timesync_binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    binary = tmp_path_factory.mktemp("timesync") / "timesync_host_test"
    subprocess.run(
        [
            "g++",
            "-std=c++17",
            "-O1",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-o",
            str(binary),
            str(_HARNESS),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return binary


def _run(binary: Path, rows: list[tuple[str, str]]) -> list[str]:
    stdin = "".join(f"{value}\t{expected}\n" for value, expected in rows)
    result = subprocess.run(
        [str(binary)], input=stdin, capture_output=True, text=True, check=False
    )
    return result.stdout.splitlines()


def test_valid_timestamps(timesync_binary: Path) -> None:
    rows = [(text, _fields(text)) for text in _VALID]
    lines = _run(timesync_binary, rows)
    assert len(lines) == len(rows)
    failures = [line for line in lines if not line.startswith("OK")]
    assert not failures, "\n".join(failures)


def test_invalid_timestamps_rejected(timesync_binary: Path) -> None:
    rows = [(text, "INVALID") for text, _ in _INVALID]
    lines = _run(timesync_binary, rows)
    assert len(lines) == len(rows)
    failures = [
        f"{line} ({_INVALID[i][1]})"
        for i, line in enumerate(lines)
        if not line.startswith("OK")
    ]
    assert not failures, "\n".join(failures)


def test_weekday_convention_is_sunday_zero() -> None:
    """Pin the reference itself: 2026-07-26 was a Sunday."""
    assert _fields("2026-07-26 00:00:00").endswith(" 0")
    assert _fields("2026-07-27 00:00:00").endswith(" 1")
