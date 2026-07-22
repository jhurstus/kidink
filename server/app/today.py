"""View model for the Today panel's calendar buckets (spec §10).

Builds the target date's non-chore events into Morning/Day/Evening buckets:
selection is capped by the §10.1 row-budget geometry (worst-case-three-headers
budget, then backfill of freed header rows into already-visible buckets), while
display order within a bucket is chronological (§10.2), laid out two events
per visual row in reading order. At most one displayed event's ``sfx``
override renders as a comic shout in its bucket's trailing empty cell
(§10.4). The row/badge/icon
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
# The bucket area is what remains of the frame interior — 916px, the 924px
# column minus the module frame's 4px top/bottom borders — after the top
# padding (98, clearing the in-panel TODAY! tab), the bottom padding (16), the
# reserved weather slot (250), and the flex gap above it (14):
# 916 - 98 - 16 - 14 - 250 = 538.
_AVAILABLE_H = 538
# Per-visible-bucket overhead: 4px top + 4px bottom bucket border + 10px body
# top padding + 34px header row + 12px bottom padding + a 12px
# inter-bucket-gap allowance (over-counted by one gap for k buckets —
# deliberate slack, left below the buckets).
_HEADER_BLOCK_H = 76
# One visual row: 12px grid gap + 60px icon row. A row holds two events side
# by side (§10.2 reading order), so budgets count rows, not events.
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

    rows: list[EventRow]

    sfx: str | None = None
    """SFX shout text (§10.4) rendered in the empty right-hand cell after the
    bucket's last row; set on at most one bucket per panel, and only when this
    bucket's row count is odd (so the cell exists)."""


@dataclass(frozen=True)
class TodayPanel:
    """The complete view model rendered by ``templates/modules/today.html``."""

    weekday: int
    """The target date's weekday (0=Monday..6=Sunday, ``date.weekday()``);
    tints the TODAY! tab with the day strip's colour for that day."""

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
    sfx_event = _pick_sfx(selected)
    buckets: list[TodayBucket] = []
    for time_of_day in _BUCKET_ORDER:
        bucket_events = selected.get(time_of_day)
        if not bucket_events:
            continue
        buckets.append(
            TodayBucket(
                name=time_of_day.value.upper(),
                key=time_of_day.value,
                rows=[build_row(e, kids, icons) for e in bucket_events],
                sfx=(
                    sfx_event.overrides.sfx
                    if sfx_event is not None and sfx_event.time_of_day == time_of_day
                    else None
                ),
            )
        )
    return TodayPanel(weekday=target.weekday(), buckets=buckets)


def _pick_sfx(
    buckets: dict[TimeOfDay, list[CalendarEvent]],
) -> CalendarEvent | None:
    """The one displayed event whose SFX shout renders, or ``None`` (§10.4).

    The shout occupies the empty right-hand cell beside its event, so only a
    bucket's last displayed event qualifies, and only when the bucket's count
    is odd (§10.2 reading order fills every row of an even bucket) — judged
    here on ``_select``'s output, i.e. after the cap/backfill. Among the
    qualifying events that carry an ``sfx`` override, the winner is the most
    interesting, ties broken by title; a full tie falls to bucket order
    (``min`` is stable, candidates are gathered Morning → Day → Evening).
    """
    candidates = [
        events[-1]
        for time_of_day in _BUCKET_ORDER
        if (events := buckets.get(time_of_day))
        and len(events) % 2 == 1
        and events[-1].overrides.sfx
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda e: (-e.overrides.interesting, e.title))


def _select(
    day_events: list[CalendarEvent],
) -> dict[TimeOfDay, list[CalendarEvent]]:
    """Apply the §10.1 cap/backfill, returning display-ordered bucket lists.

    Buckets lay events out two per visual row (§10.2 reading order), so the
    budget counts rows: an event is free when its bucket has a half-filled
    row and opens a new row otherwise. Resolution order (the visible-header
    count is circular, so it is fixed): row budget assuming all three
    headers -> longest rank-order prefix that fits -> bucket, dropping empty
    headers -> backfill the freed header rows with the next events by rank
    that still fit, but only into already-visible buckets (never creating a
    new header). Overflow is dropped silently (§4.1).
    """
    ranked = sorted(day_events, key=rank_key)
    budget = (_AVAILABLE_H - len(_BUCKET_ORDER) * _HEADER_BLOCK_H) // _ROW_H
    counts: dict[TimeOfDay, int] = {}
    rows = 0
    survivors: list[CalendarEvent] = []
    backfill: list[CalendarEvent] = []
    for i, event in enumerate(ranked):
        opens = 1 if counts.get(event.time_of_day, 0) % 2 == 0 else 0
        if rows + opens > budget:
            backfill = ranked[i:]
            break
        rows += opens
        counts[event.time_of_day] = counts.get(event.time_of_day, 0) + 1
        survivors.append(event)
    visible = {e.time_of_day for e in survivors}
    capacity = (_AVAILABLE_H - len(visible) * _HEADER_BLOCK_H) // _ROW_H
    for event in backfill:
        if event.time_of_day not in visible:
            continue
        opens = 1 if counts[event.time_of_day] % 2 == 0 else 0
        if rows + opens > capacity:
            continue
        rows += opens
        counts[event.time_of_day] += 1
        survivors.append(event)
    buckets: dict[TimeOfDay, list[CalendarEvent]] = {}
    for event in sorted(survivors, key=display_key):
        buckets.setdefault(event.time_of_day, []).append(event)
    return buckets
