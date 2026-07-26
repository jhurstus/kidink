"""Crontab parsing and next-fire evaluation - the reference implementation.

This is the *specification* for `arduino/kidink/cron.cpp`, which the device runs
to decide when to wake. The two are kept in lockstep by `cron_cases.py`, a single
shared table that `test_cron.py` runs against this module and `test_cron_cpp.py`
runs against a host-compiled build of the C++ twin.

Everything here is **naive local wall-clock** arithmetic with no timezone math,
exactly matching the C++ side: cron matches a wall clock, and converting the
result to an instant (where DST ambiguity lives) is the caller's job.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

_MONTH_NAMES = {
    name: index
    for index, name in enumerate(
        [
            "JAN",
            "FEB",
            "MAR",
            "APR",
            "MAY",
            "JUN",
            "JUL",
            "AUG",
            "SEP",
            "OCT",
            "NOV",
            "DEC",
        ],
        start=1,
    )
}
_DOW_NAMES = {
    name: index
    for index, name in enumerate(
        ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"], start=0
    )
}

_MACROS = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
}

# How far ahead next_fire searches before declaring an expression unsatisfiable.
# Four years covers the worst legal case, "Feb 29" from March 1 of a leap year.
_SEARCH_LIMIT_DAYS = 366 * 4 + 1


class CronError(ValueError):
    """A crontab expression that cannot be parsed."""


@dataclass(frozen=True)
class CronSpec:
    """A parsed 5-field crontab expression."""

    minute: frozenset[int]
    hour: frozenset[int]
    dom: frozenset[int]
    month: frozenset[int]
    dow: frozenset[int]
    dom_star: bool
    """The raw day-of-month field began with ``*`` (including ``*/2``)."""
    dow_star: bool
    """The raw day-of-week field began with ``*``."""


def _parse_value(
    token: str, lo: int, hi: int, names: dict[str, int], field: str
) -> int:
    named = names.get(token.upper())
    if named is not None:
        return named
    if not token.isdigit():
        raise CronError(f"{field}: {token!r} is not a number or known name")
    value = int(token)
    if not lo <= value <= hi:
        raise CronError(f"{field}: {value} is outside {lo}-{hi}")
    return value


def _parse_field(
    raw: str, lo: int, hi: int, names: dict[str, int], field: str
) -> frozenset[int]:
    """Expand one crontab field into the set of values it matches."""
    if not raw:
        raise CronError(f"{field}: empty field")
    values: set[int] = set()
    for part in raw.split(","):
        if not part:
            raise CronError(f"{field}: empty list element in {raw!r}")
        body, _, step_text = part.partition("/")
        if _ and not step_text:
            raise CronError(f"{field}: missing step after '/' in {part!r}")
        step = 1
        if step_text:
            if not step_text.isdigit() or int(step_text) == 0:
                raise CronError(f"{field}: invalid step {step_text!r}")
            step = int(step_text)
        if body == "*":
            start, end = lo, hi
        elif "-" in body[1:]:
            # Slice past index 0 so a leading '-' stays an error, not a split.
            start_text, _, end_text = body.partition("-")
            start = _parse_value(start_text, lo, hi, names, field)
            end = _parse_value(end_text, lo, hi, names, field)
            if end < start:
                raise CronError(f"{field}: range {body!r} runs backwards")
        else:
            start = _parse_value(body, lo, hi, names, field)
            # Vixie extension: "N/S" means "N through the field maximum, step S",
            # while a bare "N" is just that one value.
            end = hi if step_text else start
        values.update(range(start, end + 1, step))
    return frozenset(values)


def parse_cron(expr: str) -> CronSpec:
    """Parse a 5-field crontab expression (or an ``@macro``).

    Supports ``*``, ``N``, ``A-B``, comma lists, ``*/S``, ``A-B/S``, ``A/S``,
    month names ``JAN``-``DEC`` and weekday names ``SUN``-``SAT``
    (case-insensitive), and day-of-week ``7`` as a synonym for Sunday. The
    6-field (seconds) form and the Quartz extensions ``L``, ``W``, ``#``, ``?``
    are rejected, as is ``@reboot``.
    """
    text = expr.strip()
    if text.startswith("@"):
        macro = _MACROS.get(text.lower())
        if macro is None:
            raise CronError(f"unknown macro {text!r}")
        text = macro
    fields = text.split()
    if len(fields) != 5:
        raise CronError(
            f"expected 5 fields (minute hour day-of-month month day-of-week), "
            f"got {len(fields)} in {expr!r}"
        )
    for field in fields:
        for bad in "LW#?":
            if bad in field.upper():
                raise CronError(f"unsupported character {bad!r} in {field!r}")
    minute = _parse_field(fields[0], 0, 59, {}, "minute")
    hour = _parse_field(fields[1], 0, 23, {}, "hour")
    dom = _parse_field(fields[2], 1, 31, {}, "day-of-month")
    month = _parse_field(fields[3], 1, 12, _MONTH_NAMES, "month")
    # 7 is an alias for Sunday, so parse over 0-7 and fold afterwards.
    dow_raw = _parse_field(fields[4], 0, 7, _DOW_NAMES, "day-of-week")
    dow = frozenset(0 if value == 7 else value for value in dow_raw)
    return CronSpec(
        minute=minute,
        hour=hour,
        dom=dom,
        month=month,
        dow=dow,
        dom_star=fields[2].startswith("*"),
        dow_star=fields[4].startswith("*"),
    )


def _day_matches(spec: CronSpec, moment: datetime) -> bool:
    """Vixie's day rule: AND when either day field is starred, else OR.

    The classic surprise is that `0 0 13 * FRI` fires on the 13th *or* on any
    Friday, because neither day field is starred. Whenever one of them is
    starred - the overwhelmingly common case - the two are ANDed, which makes
    the starred (full) field a no-op and the other one the filter. Note that
    `*/2` counts as starred here, so it ANDs while still filtering days.
    """
    # Python weeks start on Monday; cron weeks start on Sunday.
    dow = (moment.weekday() + 1) % 7
    dom_ok = moment.day in spec.dom
    dow_ok = dow in spec.dow
    if spec.dom_star or spec.dow_star:
        return dom_ok and dow_ok
    return dom_ok or dow_ok


def next_fire(spec: CronSpec, after: datetime) -> datetime | None:
    """Return the first wall-clock minute strictly after `after` that matches.

    `after` and the result are naive local datetimes. Returns `None` when the
    expression cannot fire within four years (e.g. `0 0 30 2 *`).
    """
    candidate = (after + timedelta(minutes=1)).replace(second=0, microsecond=0)
    limit = after + timedelta(days=_SEARCH_LIMIT_DAYS)
    while candidate <= limit:
        if candidate.month not in spec.month:
            # Jump to the first instant of the next month.
            year, month = divmod(candidate.month, 12)
            candidate = candidate.replace(
                year=candidate.year + year, month=month + 1, day=1, hour=0, minute=0
            )
            continue
        if not _day_matches(spec, candidate):
            candidate = (candidate + timedelta(days=1)).replace(hour=0, minute=0)
            continue
        if candidate.hour not in spec.hour:
            candidate = (candidate + timedelta(hours=1)).replace(minute=0)
            continue
        if candidate.minute not in spec.minute:
            candidate += timedelta(minutes=1)
            continue
        return candidate
    return None
