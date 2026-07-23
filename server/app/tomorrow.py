"""View model for the Tomorrow panel's calendar list (spec §11).

Builds the day after the target date's non-chore events into a single
header-less events panel — no morning/day/evening buckets — laid out two
across per visual row in reading order, up to four events. Selection is
capped by the §4.1 row-budget geometry (two events per row over the visual
rows that fit, dropping the lowest-``interesting`` events silently), while
display order is chronological (§11). The row/badge/icon machinery shared
with the Today panel lives in :mod:`app.event_rows`. The weather subpanel in
the panel's right side is built separately (:func:`app.weather.build_weather`,
slot 1) and passed to the template alongside this model.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from app.calendar import CalendarEvent
from app.config import Kid
from app.event_rows import (
    EventRow,
    IconResolver,
    build_row,
    display_key,
    no_icons,
    rank_key,
    resolve_icons,
)

# Row-budget geometry (§4.1), mirroring static/css/tomorrow.css — keep in sync.
# The events area is what remains of the frame interior — 287px, the 295px
# cell (board.css) minus the frame's 4px top/bottom borders — after the top
# padding (98, clearing the in-panel TOMORROW! label) and the bottom padding
# (16): 287 - 98 - 16 = 173.
_AVAILABLE_H = 173
# The events panel's chrome around the row grid: 4px top + 4px bottom border
# + 12px top + 12px bottom padding, minus the 12px grid gap the first row
# doesn't pay (each row costs _ROW_H = gap + row).
_PANEL_CHROME_H = 20
# One visual row: 12px grid gap + 60px icon row. A row holds two events side
# by side (§11 reading order, matching Today's buckets), so the budget counts
# rows, then doubles.
_ROW_H = 72


@dataclass(frozen=True)
class TomorrowPanel:
    """The complete view model rendered by ``templates/modules/tomorrow.html``."""

    weekday: int
    """The shown day's weekday — the day after the target date (0=Monday..
    6=Sunday, ``date.weekday()``); tints the TOMORROW! label with the day
    strip's colour for that day."""

    rows: list[EventRow]
    """Surviving events in chronological display order (§11)."""


def build_tomorrow(
    target: date,
    events: Iterable[CalendarEvent] = (),
    kids: Sequence[Kid] = (),
    icon_resolver: IconResolver = no_icons,
) -> TomorrowPanel:
    """Build the Tomorrow panel view model for the resolved render date ``target``.

    ``events`` are the render window's expanded calendar events (see
    :func:`app.calendar.expand_events`, whose window includes the day after
    ``target`` — see :func:`app.dates.render_days`); only that next day's
    non-chore events are shown. ``kids`` (config order,
    :class:`app.config.Kid`) drives the row badges (§8). The surviving rows'
    icons are resolved through ``icon_resolver`` in a single batch — so missing
    images can generate concurrently — after the cap, never for dropped events
    (see :data:`app.images.IconResolver`; the default resolves nothing, keeping
    the view model a pure function of its inputs).
    """
    tomorrow = target + timedelta(days=1)
    day_events = [e for e in events if e.local_day == tomorrow and not e.is_chore]
    budget = 2 * ((_AVAILABLE_H - _PANEL_CHROME_H) // _ROW_H)
    survivors = sorted(day_events, key=rank_key)[:budget]
    ordered = sorted(survivors, key=display_key)
    icons = resolve_icons(ordered, icon_resolver)
    return TomorrowPanel(
        weekday=tomorrow.weekday(),
        rows=[build_row(e, kids, icons) for e in ordered],
    )
