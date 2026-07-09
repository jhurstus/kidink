"""View model for the Today panel's calendar buckets (spec §10).

Builds the target date's non-chore events into Morning/Day/Evening buckets:
selection is capped by the §10.1 row-budget geometry (worst-case-three-headers
budget, then backfill of freed header rows into already-visible buckets), while
display order within a bucket is chronological (§10.2). Each row carries the
event's AI icon URL, its kid badges (§8), and its title. The weather subpanel
(§10.3) is not built yet; its space is reserved by the template, and the
geometry constants below already subtract it.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time

from app.calendar import CalendarEvent, TimeOfDay
from app.config import Kid
from app.event_rows import IconResolver, KidBadge, icon_key, kid_badges, no_icons

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
class TodayRow:
    """One event row: icon + kid badge(s) + title (§10)."""

    title: str
    icon_url: str | None
    """``None`` -> the template renders the fallback chip (§7.3)."""

    kids: list[KidBadge]


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

    rows: list[TodayRow]


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

    ``events`` are the week's expanded calendar events (see
    :func:`app.calendar.expand_events`); only ``target``'s non-chore events are
    shown. ``kids`` (config order, :class:`app.config.Kid`) drives the row
    badges (§8). The surviving rows' icons are resolved through
    ``icon_resolver`` in a single batch — so missing images can generate
    concurrently — after the cap, never for dropped events (see
    :data:`app.images.IconResolver`; the default resolves nothing, keeping the
    view model a pure function of its inputs).
    """
    selected = _select([e for e in events if e.local_day == target and not e.is_chore])
    icons = icon_resolver(
        [
            icon_key(event)
            for time_of_day in _BUCKET_ORDER
            for event in selected.get(time_of_day, [])
        ]
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
                rows=[_build_row(e, kids, icons) for e in bucket_events],
            )
        )
    return TodayPanel(seed=target.toordinal(), buckets=buckets)


def _rank_key(event: CalendarEvent) -> tuple:
    """Selection rank (§10.1): ``interesting`` desc, then title, then start.

    The start component makes the order total even for identical
    interesting+title pairs, keeping the cap deterministic (§3.4).
    """
    return (-event.overrides.interesting, event.title, _start_key(event))


def _start_key(event: CalendarEvent) -> tuple[int, time]:
    """Chronological key: all-day events first (§10.2), then by start time."""
    # The isinstance check narrows start to datetime for the type checker; a
    # bare date start implies all-day anyway (see CalendarEvent).
    if event.all_day or not isinstance(event.start, datetime):
        return (0, time.min)
    return (1, event.start.time())


def _display_key(event: CalendarEvent) -> tuple:
    """Within-bucket display order (§10.2): chronological, all-day first, ties
    by ``interesting`` desc then title."""
    return (*_start_key(event), -event.overrides.interesting, event.title)


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
    ranked = sorted(day_events, key=_rank_key)
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
    for event in sorted(survivors, key=_display_key):
        buckets.setdefault(event.time_of_day, []).append(event)
    return buckets


def _build_row(
    event: CalendarEvent, kids: Sequence[Kid], icons: Mapping[str, str | None]
) -> TodayRow:
    """One event row, its icon looked up from the batch-resolved ``icons``."""
    return TodayRow(
        title=event.title,
        icon_url=icons.get(icon_key(event)),
        kids=kid_badges(event, kids),
    )
