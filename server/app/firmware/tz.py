"""IANA zone name -> POSIX TZ string, for the device's `setenv("TZ", ...)`.

The firmware has no zoneinfo database: newlib's `localtime`/`mktime` take the
POSIX TZ form (`"PST8PDT,M3.2.0,M11.1.0"`), which encodes the standard/daylight
abbreviations, the UTC offsets, and the DST transition rules in one string. Every
TZif v2+ file ends with exactly that string in a newline-delimited footer, put
there for this purpose, so we read it rather than deriving the rules ourselves.
"""

import zoneinfo
from importlib import resources
from pathlib import Path

_MAGIC = b"TZif"


def _tzif_bytes(iana: str) -> bytes:
    """Return the raw TZif file for `iana` from the system db or `tzdata`."""
    # A zone name is a relative path under a tz root ("US/Pacific"). Reject any
    # absolute or traversing name before joining it onto a root directory.
    if iana.startswith("/") or ".." in Path(iana).parts:
        raise ValueError(f"invalid timezone name {iana!r}")
    for root in zoneinfo.TZPATH:
        candidate = Path(root) / iana
        if candidate.is_file():
            return candidate.read_bytes()
    try:
        package = "tzdata.zoneinfo." + ".".join(iana.split("/")[:-1])
        resource = resources.files(package.rstrip(".")) / iana.split("/")[-1]
        return resource.read_bytes()
    except (ModuleNotFoundError, FileNotFoundError, NotADirectoryError) as exc:
        raise ValueError(f"unknown timezone {iana!r}") from exc


def posix_tz_for(iana: str) -> str:
    """Return the POSIX TZ string for an IANA zone (e.g. ``PST8PDT,M3.2.0,M11.1.0``).

    Raises `ValueError` for an unknown zone, a non-TZif file, or a version-1-only
    file (which carries no footer).
    """
    data = _tzif_bytes(iana)
    if not data.startswith(_MAGIC):
        raise ValueError(f"{iana!r} is not a TZif file")
    version = data[4:5]
    if version in (b"\x00", b""):
        raise ValueError(
            f"{iana!r} is a version-1 TZif file, which carries no POSIX TZ footer"
        )
    # The footer is the final "\n<TZ string>\n" of the file.
    if not data.endswith(b"\n"):
        raise ValueError(f"{iana!r} has no POSIX TZ footer")
    body = data[:-1]
    start = body.rfind(b"\n")
    if start < 0:
        raise ValueError(f"{iana!r} has no POSIX TZ footer")
    tz = body[start + 1 :].decode("ascii")
    if not tz:
        # Legal in TZif (a zone with no future rule), but useless on the device:
        # newlib would fall back to UTC and the board would wake at wrong times.
        raise ValueError(f"{iana!r} has an empty POSIX TZ footer")
    return tz
