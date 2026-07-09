"""View model for the Today panel's calendar buckets (spec §10).

Builds the target date's non-chore events into Morning/Day/Evening buckets:
selection is capped by the §10.1 row-budget geometry (worst-case-three-headers
budget, then backfill of freed header rows into already-visible buckets), while
display order within a bucket is chronological (§10.2). The row/badge/icon
machinery shared with the Tomorrow panel lives in :mod:`app.event_rows`. The
weather subpanel (§10.3) is not built yet; its space is reserved by the
template, and the geometry constants below already subtract it.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from app.calendar import CalendarEvent, TimeOfDay
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

# Row-budget geometry (§10.1/§4.1), mirroring static/css/today.css — keep in sync.
# The bucket area is what remains of the 924px column after the top padding
# (68, clearing the pop-out TODAY! tab), the bottom padding (16), the reserved
# weather slot (210), and the flex gap above it (14):
# 924 - 68 - 16 - 14 - 210 = 616.
_AVAILABLE_H = 616
# Per-visible-bucket overhead: 10px body top padding + 34px header row + 12px
# bottom padding + a 12px inter-bucket-gap allowance (over-counted by one gap
# for k buckets — deliberate slack, left below the buckets).
_HEADER_BLOCK_H = 68
# One event row: 12px flex gap + 60px icon row.
_ROW_H = 72

# Bucket display order and header text (§10): Morning → Day → Evening.
_BUCKET_ORDER = [TimeOfDay.MORNING, TimeOfDay.DAY, TimeOfDay.EVENING]


@dataclass(frozen=True)
class TodayBucket:
    """One visible time-of-day sub-panel."""

    name: str
    """Header text, e.g. ``MORNING``."""

    key: str
    """The ``TimeOfDay`` value (``morning``/``day``/``evening``), used as a CSS
    class suffix."""

    seed: int
    """Border seed for the bucket's comic sub-panel (date-pure, §3.4)."""

    rows: list[EventRow]


@dataclass(frozen=True)
class TodayPanel:
    """The complete view model rendered by ``templates/modules/today.html``."""

    seed: int
    """Border seed for the outer panel (date-pure, §3.4)."""

    buckets: list[TodayBucket]
    """Only non-empty buckets, always in Morning → Day → Evening order."""


def build_today(
    target: date,
    events: Iterable[CalendarEvent] = (),
    kids: Sequence[Kid] = (),
    icon_resolver: IconResolver = no_icons,
) -> TodayPanel:
    """Build the Today panel view model for the resolved render date ``target``.

    ``events`` are the render window's expanded calendar events (see
    :func:`app.calendar.expand_events`); only ``target``'s non-chore events are
    shown. ``kids`` (config order, :class:`app.config.Kid`) drives the row
    badges (§8). The surviving rows' icons are resolved through
    ``icon_resolver`` in a single batch — so missing images can generate
    concurrently — after the cap, never for dropped events (see
    :data:`app.images.IconResolver`; the default resolves nothing, keeping the
    view model a pure function of its inputs).
    """
    selected = _select([e for e in events if e.local_day == target and not e.is_chore])
    icons = resolve_icons(
        [
            event
            for time_of_day in _BUCKET_ORDER
            for event in selected.get(time_of_day, [])
        ],
        icon_resolver,
    )
    buckets: list[TodayBucket] = []
    for i, time_of_day in enumerate(_BUCKET_ORDER):
        bucket_events = selected.get(time_of_day)
        if not bucket_events:
            continue
        buckets.append(
            TodayBucket(
                name=time_of_day.value.upper(),
                key=time_of_day.value,
                seed=target.toordinal() + i + 1,
                rows=[build_row(e, kids, icons) for e in bucket_events],
            )
        )
    return TodayPanel(seed=target.toordinal(), buckets=buckets)


def _select(
    day_events: list[CalendarEvent],
) -> dict[TimeOfDay, list[CalendarEvent]]:
    """Apply the §10.1 cap/backfill, returning display-ordered bucket lists.

    Resolution order (the visible-header count is circular, so it is fixed):
    budget assuming all three headers -> global top-N by ``interesting`` ->
    bucket, dropping empty headers -> backfill the freed header rows with the
    next events by rank, but only into already-visible buckets (never creating
    a new header). Overflow is dropped silently (§4.1).
    """
    ranked = sorted(day_events, key=rank_key)
    budget = (_AVAILABLE_H - len(_BUCKET_ORDER) * _HEADER_BLOCK_H) // _ROW_H
    survivors = ranked[:budget]
    visible = {e.time_of_day for e in survivors}
    capacity = (_AVAILABLE_H - len(visible) * _HEADER_BLOCK_H) // _ROW_H
    for event in ranked[budget:]:
        if len(survivors) >= capacity:
            break
        if event.time_of_day in visible:
            survivors.append(event)
    buckets: dict[TimeOfDay, list[CalendarEvent]] = {}
    for event in sorted(survivors, key=display_key):
        buckets.setdefault(event.time_of_day, []).append(event)
    return buckets
