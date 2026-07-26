"""The device's HTTP `Date:` parser is the board's only clock source.

`arduino/kidink/httpdate.cpp` runs only on the ESP32, so it is compiled for the
host here and checked against epochs computed by Python's own datetime. A bug
would set the RTC wrong and therefore wake the panel at the wrong hour.
"""

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_HARNESS = _REPO_ROOT / "arduino" / "kidink_tests" / "httpdate_host_test.cpp"

pytestmark = pytest.mark.skipif(
    shutil.which("g++") is None, reason="no host C++ compiler available"
)


def _epoch(text: str) -> str:
    """Expected epoch for an IMF-fixdate, via the standard library."""
    parsed = datetime.strptime(text, "%a, %d %b %Y %H:%M:%S GMT").replace(tzinfo=UTC)
    return str(int(parsed.timestamp()))


_VALID = [
    # The exact header shape /display served during bring-up.
    "Sat, 25 Jul 2026 18:02:38 GMT",
    "Wed, 01 Jan 2020 00:00:00 GMT",  # the lower edge of the sanity window
    "Mon, 29 Feb 2028 12:34:56 GMT",  # leap day
    "Fri, 31 Dec 2027 23:59:59 GMT",  # year boundary
    "Sun, 01 Mar 2026 00:00:00 GMT",  # day after a non-leap February
]

_INVALID = [
    ("", "empty"),
    ("Sat, 25 Jul 2026 18:02:38", "missing zone"),
    ("Sat, 25 Jul 2026 18:02:38 UTC", "non-GMT zone"),
    ("Sat, 25 Xxx 2026 18:02:38 GMT", "bogus month name"),
    ("Sat 25 Jul 2026 18:02:38 GMT", "missing comma"),
    ("Sat, 25 Jul 2026 18:02 GMT", "too short"),
    ("Saturday, 25-Jul-26 18:02:38 GMT", "obsolete RFC 850 form"),
    ("Sat Jul 25 18:02:38 2026", "obsolete asctime form"),
    ("Sat, 25 Jul 2026 99:02:38 GMT", "hour out of range"),
    ("Sat, 25 Jul 1999 18:02:38 GMT", "implausible year"),
    ("Sat, 25 Jul 2026 1a:02:38 GMT", "non-digit in time"),
]


@pytest.fixture(scope="module")
def httpdate_binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    binary = tmp_path_factory.mktemp("httpdate") / "httpdate_host_test"
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


def test_valid_dates(httpdate_binary: Path) -> None:
    rows = [(text, _epoch(text)) for text in _VALID]
    lines = _run(httpdate_binary, rows)
    assert len(lines) == len(rows)
    failures = [line for line in lines if not line.startswith("OK")]
    assert not failures, "\n".join(failures)


def test_invalid_dates_rejected(httpdate_binary: Path) -> None:
    rows = [(text, "INVALID") for text, _ in _INVALID]
    lines = _run(httpdate_binary, rows)
    assert len(lines) == len(rows)
    failures = [
        f"{line} ({_INVALID[i][1]})"
        for i, line in enumerate(lines)
        if not line.startswith("OK")
    ]
    assert not failures, "\n".join(failures)
