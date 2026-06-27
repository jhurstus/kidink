"""Family-calendar data layer: fetch, recurrence expansion, event model.

Public API:

- :func:`fetch_ics` / :class:`CalendarFetchError` — HTTP fetch (the only network I/O).
- :func:`expand_events` — expand ICS text into concrete :class:`CalendarEvent`s for a
  week, honoring recurrence and exceptions.
- :func:`partition` — split events into regular vs. chore.
- :class:`EventOverrides` / :class:`TimeOfDay` / :func:`parse_overrides` — the
  TOML-described per-event fields.
"""

from app.calendar.events import CalendarEvent, expand_events, partition
from app.calendar.feed import CalendarFetchError, fetch_ics
from app.calendar.overrides import EventOverrides, TimeOfDay, parse_overrides

__all__ = [
    "CalendarEvent",
    "CalendarFetchError",
    "EventOverrides",
    "TimeOfDay",
    "expand_events",
    "fetch_ics",
    "parse_overrides",
    "partition",
]
