"""View model for the Today panel's calendar buckets (spec §10).

Builds the target date's non-chore events into Morning/Day/Evening buckets:
selection is capped by the §10.1 row-budget geometry (worst-case-three-headers
budget, then backfill of freed header rows into already-visible buckets), while
display order within a bucket is chronological (§10.2). Each row carries the
event's AI icon URL, its kid badges (§8), and its title. The weather subpanel
(§10.3) is not built yet; its space is reserved by the template, and the
geometry constants below already subtract it.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time

from app.calendar import CalendarEvent, TimeOfDay
from app.config import Kid

# Structural stand-in for app.images.IconResolver (kept as a plain Callable so
# this module needs no images import): item description -> icon URL or None.
type _IconResolver = Callable[[str], str | None]


def _no_icons(item_description: str) -> str | None:
    """Default resolver: no icons — keeps build_today pure by default."""
    return None


# Per-kid badge color by config position (kid 0, kid 1). Red and blue are the
# panel's two strongest, most separable ink hues (§5.5); solid ink is fine for
# text (§5.3). A curated design choice, not deployment config — promotable to a
# Settings field later if more kids or custom colors are ever needed.
KID_COLORS = ["#e02b20", "#4aa8e8"]

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
class KidBadge:
    """One kid initial shown on an event row (§8)."""

    initial: str
    color: str


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
    icon_resolver: _IconResolver = _no_icons,
) -> TodayPanel:
    """Build the Today panel view model for the resolved render date ``target``.

    ``events`` are the week's expanded calendar events (see
    :func:`app.calendar.expand_events`); only ``target``'s non-chore events are
    shown. ``kids`` (config order, :class:`app.config.Kid`) drives the row
    badges (§8). Icons for the surviving rows are resolved through
    ``icon_resolver`` (see :data:`app.images.IconResolver`; the default resolves
    nothing, keeping the view model a pure function of its inputs).
    """
    selected = _select([e for e in events if e.local_day == target and not e.is_chore])
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
                rows=[_build_row(e, kids, icon_resolver) for e in bucket_events],
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
    event: CalendarEvent, kids: Sequence[Kid], icon_resolver: _IconResolver
) -> TodayRow:
    """One event row, resolving its icon (keyed like the day strip, §7.1)."""
    return TodayRow(
        title=event.title,
        icon_url=icon_resolver(event.overrides.icon_description or event.title),
        kids=_kid_badges(event, kids),
    )


def _kid_badges(event: CalendarEvent, kids: Sequence[Kid]) -> list[KidBadge]:
    """The row's kid badges (§8), in config order.

    Badges mark a *proper subset* of the configured kids: an event that applies
    to everyone — shared (no labels) or explicitly labeled for every kid —
    shows no badges, matching the day strip's unlabeled lone shared icon
    (§8/§9.2). A label value matches a kid's ``label`` or ``name``,
    case-insensitively; labels matching no configured kid yield nothing (the
    event was explicitly assigned, just not to these kids).
    """
    labels = {label.casefold() for label in event.overrides.labels}
    if not labels:
        return []
    matched = [
        (i, kid)
        for i, kid in enumerate(kids)
        if kid.label.casefold() in labels or kid.name.casefold() in labels
    ]
    if len(matched) == len(kids):
        return []
    return [
        KidBadge(initial=kid.label, color=KID_COLORS[i % len(KID_COLORS)])
        for i, kid in matched
    ]
