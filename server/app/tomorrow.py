"""View model for the Tomorrow panel's calendar list (spec §11).

Builds the day after the target date's non-chore events into a single
chronological list — no morning/day/evening buckets. Selection is capped by
the §4.1 row-budget geometry (``floor(available_height / row_height)``,
dropping the lowest-``interesting`` events silently), while display order is
chronological (§11). The row/badge/icon machinery shared with the Today panel
lives in :mod:`app.event_rows`. The weather subpanel filling the panel's right
half is built separately (:func:`app.weather.build_weather`, slot 1) and
passed to the template alongside this model.
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
# The list area is what remains of the 295px panel (board.css sizes the cell
# for three rows) after the top padding (68, clearing the pop-out TOMORROW!
# tab, sized like Today's) and the bottom padding (11): 295 - 68 - 11 = 216.
_AVAILABLE_H = 216
# One event row: 12px flex gap + 60px icon row (shared with Today, §11).
_ROW_H = 72


@dataclass(frozen=True)
class TomorrowPanel:
    """The complete view model rendered by ``templates/modules/tomorrow.html``."""

    seed: int
    """Border seed for the panel (date-pure, §3.4)."""

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
    budget = _AVAILABLE_H // _ROW_H
    survivors = sorted(day_events, key=rank_key)[:budget]
    ordered = sorted(survivors, key=display_key)
    icons = resolve_icons(ordered, icon_resolver)
    # The Today module's border seeds span target.toordinal()..+3 (panel plus
    # up to three buckets); +4 keeps this panel's ripple distinct on the page
    # while staying date-pure (§3.4).
    return TomorrowPanel(
        seed=target.toordinal() + 4,
        rows=[build_row(e, kids, icons) for e in ordered],
    )
