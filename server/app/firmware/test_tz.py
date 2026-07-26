"""IANA zone -> POSIX TZ string for the device's `setenv("TZ", ...)`."""

import re

import pytest

from app.firmware.tz import posix_tz_for

# A POSIX TZ string is "STD offset [DST [offset] [,start[/time],end[/time]]]".
_SHAPE = re.compile(r"^[A-Za-z<>+\-0-9]{3,}[+\-]?\d{1,2}(:\d{2})?")


def test_us_pacific_is_exact() -> None:
    """The project default, pinned: this exact string ships in config.h."""
    assert posix_tz_for("US/Pacific") == "PST8PDT,M3.2.0,M11.1.0"


def test_utc_is_exact() -> None:
    assert posix_tz_for("UTC") == "UTC0"


@pytest.mark.parametrize(
    "zone",
    [
        "America/New_York",
        "Europe/London",
        "Australia/Sydney",
        "Asia/Kolkata",
        "America/Los_Angeles",
    ],
)
def test_other_zones_have_the_right_shape(zone: str) -> None:
    """Shape only, not exact text - a tzdata bump must not break the build."""
    assert _SHAPE.match(posix_tz_for(zone))


def test_southern_hemisphere_dst_rules_survive() -> None:
    """Sydney's DST runs October to April; both rules must be present."""
    tz = posix_tz_for("Australia/Sydney")
    assert tz.count(",") == 2
    assert "AEDT" in tz


def test_unknown_zone_raises() -> None:
    with pytest.raises(ValueError, match="unknown timezone"):
        posix_tz_for("Not/AZone")


@pytest.mark.parametrize("name", ["../etc/passwd", "/etc/passwd"])
def test_path_traversal_rejected(name: str) -> None:
    """Zone names are joined onto a root directory, so they must stay relative."""
    with pytest.raises(ValueError, match="invalid timezone name"):
        posix_tz_for(name)
