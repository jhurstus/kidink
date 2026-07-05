"""Calendar event model + recurrence expansion.

Turns raw ICS text into concrete :class:`CalendarEvent` occurrences for a target
week. Recurrence (``RRULE``/``RDATE``) is expanded and exceptions (``EXDATE`` and
``RECURRENCE-ID`` overrides) are honored by ``recurring_ical_events`` — we do not
hand-roll any of that. This module performs no network I/O; the HTTP fetch lives
in :mod:`app.calendar.feed`.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import icalendar
import recurring_ical_events

from app.calendar.overrides import EventOverrides, TimeOfDay, parse_overrides

# Chore prefix "chore:" case-insensitive, optional whitespace after.
_CHORE_RE = re.compile(r"^chore:\s*", re.IGNORECASE)

# Time-of-day cutoffs in local clock time.
_MORNING_END = time(9, 0)
_EVENING_START = time(16, 0)


@dataclass(frozen=True)
class CalendarEvent:
    """One concrete (expanded) calendar occurrence on a single local day."""

    title: str
    """SUMMARY, with any ``chore:`` prefix stripped when ``is_chore``."""

    start: datetime | date
    """Local-zone start: a ``datetime`` for timed events, a ``date`` if all-day."""

    end: datetime | date
    """Local-zone end (same kind as ``start``)."""

    all_day: bool
    is_chore: bool

    local_day: date
    """The calendar day (configured timezone) this occurrence belongs to."""

    time_of_day: TimeOfDay
    """Override if set, else derived from start/end (spec §6.4)."""

    overrides: EventOverrides
    """Parsed TOML description: interesting, labels, countdown_eligible, …."""


def expand_events(ics_text: str, week: Sequence[date], tz: str) -> list[CalendarEvent]:
    """Expand ``ics_text`` into the concrete events of ``week`` (Mon..Sun), in ``tz``.

    ``week`` is the seven Mon–Sun dates (see :func:`app.dates.week_of`). May raise
    ``ValueError`` (and similar) if the ICS is unparseable; the caller maps that to a
    500 (spec, §13 contrasts the meal-plan's friendly fallback).
    """
    calendar = icalendar.Calendar.from_ical(ics_text)
    zone = ZoneInfo(tz)
    # Local-midnight window covering the whole week, end-exclusive at the next Monday.
    window_start = datetime.combine(week[0], time.min, zone)
    window_end = datetime.combine(week[-1] + timedelta(days=1), time.min, zone)

    occurrences = recurring_ical_events.of(calendar).between(window_start, window_end)
    events: list[CalendarEvent] = []
    for component in occurrences:
        event = _to_event(component, zone)
        # Guard the rare tz-edge occurrence the window includes but whose local day
        # falls just outside Mon..Sun.
        if event is not None and week[0] <= event.local_day <= week[-1]:
            events.append(event)
    return events


def partition(
    events: Iterable[CalendarEvent],
) -> tuple[list[CalendarEvent], list[CalendarEvent]]:
    """Split events into ``(regular, chores)`` (spec §6.5)."""
    regular: list[CalendarEvent] = []
    chores: list[CalendarEvent] = []
    for event in events:
        (chores if event.is_chore else regular).append(event)
    return regular, chores


def _to_event(component: Any, zone: ZoneInfo) -> CalendarEvent | None:
    """Build a :class:`CalendarEvent` from one expanded ICS component.

    ``component`` is an ``icalendar`` event (dict-like, loosely typed by the
    library), so it is annotated ``Any`` at this boundary.
    """
    dtstart = component.get("DTSTART")
    if dtstart is None:
        return None
    start_raw = dtstart.dt
    # datetime subclasses date, so check datetime-ness explicitly (order matters).
    all_day = isinstance(start_raw, date) and not isinstance(start_raw, datetime)

    dtend = component.get("DTEND")
    end_raw = dtend.dt if dtend is not None else start_raw

    summary = str(component.get("SUMMARY", ""))
    is_chore, title = _split_chore(summary)

    description = component.get("DESCRIPTION")
    overrides = parse_overrides(str(description) if description is not None else None)

    if all_day:
        return CalendarEvent(
            title=title,
            start=start_raw,
            end=end_raw,
            all_day=True,
            is_chore=is_chore,
            local_day=start_raw,
            time_of_day=overrides.time_of_day or TimeOfDay.DAY,
            overrides=overrides,
        )

    start_local = _to_local(start_raw, zone)
    end_local = (
        _to_local(end_raw, zone) if isinstance(end_raw, datetime) else start_local
    )
    time_of_day = overrides.time_of_day or _derive_time_of_day(start_local, end_local)
    return CalendarEvent(
        title=title,
        start=start_local,
        end=end_local,
        all_day=False,
        is_chore=is_chore,
        local_day=start_local.date(),
        time_of_day=time_of_day,
        overrides=overrides,
    )


def _to_local(dt: datetime, zone: ZoneInfo) -> datetime:
    """Convert to the configured zone; a floating (naive) time is read as local."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=zone)
    return dt.astimezone(zone)


def _split_chore(summary: str) -> tuple[bool, str]:
    """Return ``(is_chore, display_title)`` (spec §6.5)."""
    if _CHORE_RE.match(summary):
        return True, _CHORE_RE.sub("", summary, count=1).strip()
    return False, summary.strip()


def _derive_time_of_day(start: datetime, end: datetime) -> TimeOfDay:
    """Bucket a timed event by local clock time (spec §6.4).

    Morning if it ends at/before 09:00; evening if it starts at/after 16:00;
    otherwise day.
    """
    if end.time() <= _MORNING_END:
        return TimeOfDay.MORNING
    if start.time() >= _EVENING_START:
        return TimeOfDay.EVENING
    return TimeOfDay.DAY
