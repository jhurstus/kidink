"""View model for the day-of-week strip (spec §9).

Builds the seven Mon–Sun day panels for a target date: each panel's name,
whether it is "today" (the widened, taller panel with the excited art and the
date caption), its width, and the day's one or two event icons per the §9.2
per-kid selection: each kid's most-interesting candidate event, merged into
one icon when the kids agree, split by the torn-panel treatment when they
differ. Icon labels follow the event's own kid assignment (§8): only an event
belonging to a proper subset of the kids carries initials, so a shared event's
icon is always unlabeled — even next to a kid-specific icon.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from app.calendar import CalendarEvent
from app.config import Kid
from app.dates import week_of
from app.event_rows import (
    IconItem,
    KidBadge,
    assigned_kids,
    icon_item,
    icon_key,
    kid_badges,
)

# Panel display names, Monday..Sunday (week_of is Mon-first).
DAY_NAMES = [
    "MONDAY",
    "TUESDAY",
    "WEDNESDAY",
    "THURSDAY",
    "FRIDAY",
    "SATURDAY",
    "SUNDAY",
]

# Strip geometry (px), mirrored by day_strip.css. Seven free-standing panels
# fill the strip exactly: the today panel is ~30% wider than the six others,
# panels are split by a small gutter with a wider one between the weekday and
# weekend blocks (the flex gap plus Saturday's extra margin):
#   6*205 + 268 + 5*5 + (5 + 10) = 1538 = _STRIP_W.
_STRIP_W = 1538
_PANEL_W = 205
_TODAY_PANEL_W = 268
_PANEL_GAP = 5
_WEEKEND_EXTRA_GAP = 10

type StripIconRequest = tuple[IconItem, bool]
"""One §9.2 strip pick's image inputs: ``(item, excited)`` — the excited flag
asks for the today panel's amped-up art variant (§9.1)."""

# Structural stand-in for app.images.StripIconResolver (kept as a plain
# Callable so this view-model module needs no images import): a batch of
# (item, excited) requests -> a (icon_key, excited) -> URL-or-None mapping,
# resolved in one call so missing images can generate concurrently behind it.
# Keyed by the pair, not the key alone: a recurring event can land on today
# (excited) and another day (base) in the same render.
type StripIconResolver = Callable[
    [Sequence[StripIconRequest]], Mapping[tuple[str, bool], str | None]
]


def no_strip_icons(
    requests: Sequence[StripIconRequest],
) -> Mapping[tuple[str, bool], str | None]:
    """Default resolver: no icons — keeps the builder pure by default."""
    return {}


@dataclass(frozen=True)
class DayIcon:
    """One event icon in a day panel (§9.2)."""

    title: str
    """The event title: the icon's alt text and the §7.3 fallback-chip text."""

    icon_url: str | None
    """``None`` -> the template renders the fallback chip (§7.3)."""

    kids: list[KidBadge]
    """The event's own §8 kid badges: empty unless the event belongs to a
    proper subset of the configured kids — a shared event's icon is unlabeled
    even beside a kid-specific one (§9.2)."""


@dataclass(frozen=True)
class DayCell:
    """One day panel in the strip."""

    name: str
    iso: str
    is_today: bool
    width: int
    icons: list[DayIcon]
    """The day's one or two event icons (§9.2), in kid config order; empty for
    an event-less day."""


@dataclass(frozen=True)
class DayStrip:
    """The complete view model rendered by ``templates/modules/day_strip.html``."""

    week: list[DayCell]
    date_month: str
    """Today-panel date, month-name part, e.g. "June" — the template styles it
    separately from the day/year."""
    date_rest: str
    """Today-panel date, day-and-year part, e.g. "3, 2026"."""


def build_day_strip(
    target: date,
    events: Iterable[CalendarEvent] = (),
    kids: Sequence[Kid] = (),
    icon_resolver: StripIconResolver = no_strip_icons,
) -> DayStrip:
    """Build the full day-strip view model for the resolved render date ``target``.

    ``events`` are the week's expanded calendar events (see
    :func:`app.calendar.expand_events`); they are grouped by local day and each
    panel shows the §9.2 per-kid icon selection over its non-chore events.
    ``kids`` (config order, :class:`app.config.Kid`) drives both the per-kid
    candidacy and the icon badges. All seven days' icons are resolved through
    ``icon_resolver`` in a single batch — today's picks flagged excited so the
    today panel gets the amped-up art (§9.1) — so missing images can generate
    concurrently (see :data:`app.images.StripIconResolver`; the default
    resolves nothing, keeping the view model a pure function of its inputs).
    """
    by_day: dict[date, list[CalendarEvent]] = {}
    for event in events:
        by_day.setdefault(event.local_day, []).append(event)
    month, rest = _format_date_parts(target)
    return DayStrip(
        week=_build_week_cells(week_of(target), target, by_day, kids, icon_resolver),
        date_month=month,
        date_rest=rest,
    )


def _rank_key(event: CalendarEvent) -> tuple:
    """Candidate rank: ``interesting`` descending, ties broken by title ascending
    — a total order, so each pick is deterministic for a given day (spec §3.4)."""
    return (-event.overrides.interesting, event.title)


def _day_picks(
    day_events: list[CalendarEvent], kids: Sequence[Kid], *, single: bool = False
) -> list[CalendarEvent]:
    """The day's shown events per the §9.2 per-kid selection.

    A kid's candidates are the day's non-chore events that apply to them —
    shared, or assigned to them (see :func:`app.event_rows.assigned_kids`);
    their pick is the most-interesting candidate. Kids agreeing on one event
    share a single entry, so the result has 0..len(kids) entries in kid config
    order. With no kids configured the day degrades to one overall pick.

    When ``single`` (the today panel, §9.1, which never splits into a torn
    two-image cell), the picks collapse to just the one most-interesting
    candidate — the global top, which is always one of the per-kid picks — so
    the today cell shows exactly one image, carrying that event's own kid label.
    """
    candidates = [event for event in day_events if not event.is_chore]
    if not candidates:
        return []
    if not kids:
        return [min(candidates, key=_rank_key)]
    picks: list[CalendarEvent] = []
    for i in range(len(kids)):
        mine = [e for e in candidates if i in assigned_kids(e, kids)]
        if not mine:
            continue
        top = min(mine, key=_rank_key)
        if not any(top is pick for pick in picks):
            picks.append(top)
    if single and picks:
        return [min(picks, key=_rank_key)]
    return picks


def _build_week_cells(
    week: list[date],
    target: date,
    by_day: dict[date, list[CalendarEvent]],
    kids: Sequence[Kid],
    icon_resolver: StripIconResolver,
) -> list[DayCell]:
    """Build the seven ``DayCell``s for ``week`` (Mon..Sun), flagging ``target``.

    ``week`` must be the seven Mon–Sun dates (see ``dates.week_of``); ``by_day``
    maps each local day to its events. The days' picked events (§9.2) are
    resolved to icon URLs through one batched ``icon_resolver`` call, keyed by
    each event's ``icon_description`` (falling back to its title, §6.4/§7.1)
    plus the excited flag — set on today's picks only, selecting the today
    panel's amped-up art variant (§9.1).
    """
    # The today cell never splits into a torn two-image panel (§9.1), so its
    # picks collapse to the single most-interesting candidate; every other day
    # keeps the full 0..2 §9.2 selection.
    picks_by_day = [
        _day_picks(by_day.get(day, []), kids, single=(day == target)) for day in week
    ]
    icons = icon_resolver(
        [
            (icon_item(event), day == target)
            for day, picks in zip(week, picks_by_day, strict=True)
            for event in picks
        ]
    )
    cells: list[DayCell] = []
    for day, name, picks in zip(week, DAY_NAMES, picks_by_day, strict=True):
        is_today = day == target
        cells.append(
            DayCell(
                name=name,
                iso=day.isoformat(),
                is_today=is_today,
                width=_TODAY_PANEL_W if is_today else _PANEL_W,
                icons=[
                    DayIcon(
                        title=event.title,
                        icon_url=icons.get((icon_key(event), is_today)),
                        kids=kid_badges(event, kids),
                    )
                    for event in picks
                ],
            )
        )
    return cells


def _format_date_parts(target: date) -> tuple[str, str]:
    """Format the today-panel date as its (month, "day, year") parts — e.g.
    ("June", "3, 2026"), no leading zero — split so the template can style the
    month name separately."""
    return f"{target:%B}", f"{target.day}, {target.year}"
