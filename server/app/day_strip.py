"""View model for the day-of-week strip (spec §9).

Builds the seven Mon–Sun day cells for a target date: each cell's name, its
fixed per-day border color (see DAY_PALETTE — hues picked for separability on
the six-ink panel, spec §5.5), whether it is "today", and the day's one or two
event icons per the §9.2 per-kid selection: each kid's most-interesting
candidate event, merged into one icon when the kids agree, side by side when
they differ. Icon labels follow the event's own kid assignment (§8): only an
event belonging to a proper subset of the kids carries initials, so a shared
event's icon is always unlabeled — even next to a kid-specific icon.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from app.calendar import CalendarEvent
from app.config import Kid
from app.dates import week_of
from app.event_rows import (
    IconResolver,
    KidBadge,
    assigned_kids,
    icon_key,
    kid_badges,
    no_icons,
)

# Per-day cell color, Monday..Sunday: the day's dominant saturated color, drawn
# as each white cell's 3px border (see the day_cell macro). Full-cell tints and
# gradients were abandoned after on-panel testing (2026-07): smooth saturated
# fills posterize under the six-ink quantizer (spec §5.5), while a thin line of
# a saturated color on white renders crisply. Purple and orange remain the
# panel's weakest hues, but as a border accent (not a large fill) they read
# acceptably.
DAY_PALETTE: list[dict[str, str]] = [
    {"name": "MONDAY", "color": "#3dbb4e"},  # green
    {"name": "TUESDAY", "color": "#4aa8e8"},  # blue
    {"name": "WEDNESDAY", "color": "#8e8e8e"},  # gray
    {"name": "THURSDAY", "color": "#8f6ade"},  # purple
    {"name": "FRIDAY", "color": "#e02b20"},  # red
    {"name": "SATURDAY", "color": "#ee7a14"},  # orange
    {"name": "SUNDAY", "color": "#f2c91d"},  # yellow
]

# Per-weekday burst image filename (spec §9.1), keyed by ``date.weekday()`` (Mon=0).
# Every day has its own starburst; the today cell is replaced by it.
BURST_BY_WEEKDAY: dict[int, str] = {
    0: "monday_burst.png",
    1: "tuesday_burst.png",
    2: "wednesday_burst.png",
    3: "thursday_burst.png",
    4: "friday_burst.png",
    5: "saturday_burst.png",
    6: "sunday_burst.png",
}

# Cell widths (px). The active cell is ~70% wider than every other cell, and all six
# non-active cells shrink to one uniform width; the values are chosen so the seven
# cells exactly fill the strip: with 140px of fixed overhead (gaps + row padding)
# they must sum to _STRIP_W - 140 = 1398 (306 + 6*182). The no-burst fallback
# (every cell 199, ~5px slack) is unused while all seven days map above, but kept
# for robustness if a mapping is removed.
_DEFAULT_CELL_W = 199
_BURST_CELL_W = 306
_SHRUNK_CELL_W = 182

# Strip layout geometry (mirrors day_strip.css and the day_group macro): used to
# place the active day's burst horizontally, centered over its cell.
_STRIP_W = 1538
_CELL_GAP = 12
_ROW_PAD = 12
_GROUP_GAP = 32


@dataclass(frozen=True)
class DayIcon:
    """One event icon in a day cell (§9.2)."""

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
    """One day box in the strip."""

    name: str
    iso: str
    color: str
    is_today: bool
    width: int
    burst: str | None
    burst_cx: int | None
    icons: list[DayIcon]
    """The day's one or two event icons (§9.2), in kid config order; empty for
    an event-less day."""


@dataclass(frozen=True)
class DayStrip:
    """The complete view model rendered by ``templates/modules/day_strip.html``."""

    week: list[DayCell]
    date_label: str


def build_day_strip(
    target: date,
    events: Iterable[CalendarEvent] = (),
    kids: Sequence[Kid] = (),
    icon_resolver: IconResolver = no_icons,
) -> DayStrip:
    """Build the full day-strip view model for the resolved render date ``target``.

    ``events`` are the week's expanded calendar events (see
    :func:`app.calendar.expand_events`); they are grouped by local day and each
    cell shows the §9.2 per-kid icon selection over its non-chore events.
    ``kids`` (config order, :class:`app.config.Kid`) drives both the per-kid
    candidacy and the icon badges. All seven days' icons are resolved through
    ``icon_resolver`` in a single batch — so missing images can generate
    concurrently (see :data:`app.images.IconResolver`; the default resolves
    nothing, keeping the view model a pure function of its inputs).
    """
    by_day: dict[date, list[CalendarEvent]] = {}
    for event in events:
        by_day.setdefault(event.local_day, []).append(event)
    return DayStrip(
        week=_build_week_cells(week_of(target), target, by_day, kids, icon_resolver),
        date_label=_format_date_label(target),
    )


def _rank_key(event: CalendarEvent) -> tuple:
    """Candidate rank: ``interesting`` descending, ties broken by title ascending
    — a total order, so each pick is deterministic for a given day (spec §3.4)."""
    return (-event.overrides.interesting, event.title)


def _day_picks(
    day_events: list[CalendarEvent], kids: Sequence[Kid]
) -> list[CalendarEvent]:
    """The day's shown events per the §9.2 per-kid selection.

    A kid's candidates are the day's non-chore events that apply to them —
    shared, or assigned to them (see :func:`app.event_rows.assigned_kids`);
    their pick is the most-interesting candidate. Kids agreeing on one event
    share a single entry, so the result has 0..len(kids) entries in kid config
    order. With no kids configured the day degrades to one overall pick.
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
    return picks


def _cell_width(i: int, active_idx: int | None) -> int:
    """Width (px) of cell ``i`` given the active burst-day index (or ``None``).

    On a burst week the active cell is widened and every other cell shrinks to one
    uniform smaller width; with no burst all cells keep the default width.
    """
    if active_idx is None:
        return _DEFAULT_CELL_W
    return _BURST_CELL_W if i == active_idx else _SHRUNK_CELL_W


def _burst_center_x(widths: list[int], active_idx: int) -> float:
    """Horizontal center of cell ``active_idx`` relative to the strip's left edge.

    Mirrors the flex layout in day_strip.css and the day_group macro: two centered
    group panels (weekday 0–4, weekend 5–6) split by ``_GROUP_GAP``, each cell row
    padded by ``_ROW_PAD`` with ``_CELL_GAP`` between cells. The burst is then
    centered on this x by the CSS (``translateX(-50%)``).
    """

    def panel_w(cells: list[int]) -> int:
        return sum(cells) + (len(cells) - 1) * _CELL_GAP + 2 * _ROW_PAD

    weekday, weekend = widths[:5], widths[5:]
    groups_left = (_STRIP_W - (panel_w(weekday) + _GROUP_GAP + panel_w(weekend))) / 2
    if active_idx < 5:
        group_left, cells, idx = groups_left, weekday, active_idx
    else:
        group_left = groups_left + panel_w(weekday) + _GROUP_GAP
        cells, idx = weekend, active_idx - 5
    cell_left = group_left + _ROW_PAD + sum(cells[:idx]) + idx * _CELL_GAP
    return cell_left + cells[idx] / 2


def _build_week_cells(
    week: list[date],
    target: date,
    by_day: dict[date, list[CalendarEvent]],
    kids: Sequence[Kid],
    icon_resolver: IconResolver,
) -> list[DayCell]:
    """Build the seven ``DayCell``s for ``week`` (Mon..Sun), flagging ``target``.

    ``week`` must be the seven Mon–Sun dates (see ``dates.week_of``); ``by_day``
    maps each local day to its events. The days' picked events (§9.2) are
    resolved to icon URLs through one batched ``icon_resolver`` call, keyed by
    each event's ``icon_description`` (falling back to its title, §6.4/§7.1).
    """
    # The today cell sits at index ``target.weekday()`` (week is Mon-first). It gets
    # a burst — and triggers the width redistribution — only if that weekday has one.
    active_idx = target.weekday() if target.weekday() in BURST_BY_WEEKDAY else None
    widths = [_cell_width(i, active_idx) for i in range(7)]
    cx = round(_burst_center_x(widths, active_idx)) if active_idx is not None else None
    picks_by_day = [_day_picks(by_day.get(day, []), kids) for day in week]
    icons = icon_resolver(
        [icon_key(event) for picks in picks_by_day for event in picks]
    )
    cells: list[DayCell] = []
    for i, (day, palette) in enumerate(zip(week, DAY_PALETTE, strict=True)):
        cells.append(
            DayCell(
                name=palette["name"],
                iso=day.isoformat(),
                color=palette["color"],
                is_today=(day == target),
                width=widths[i],
                burst=BURST_BY_WEEKDAY[i] if i == active_idx else None,
                burst_cx=cx if i == active_idx else None,
                icons=[
                    DayIcon(
                        title=event.title,
                        icon_url=icons.get(icon_key(event)),
                        kids=kid_badges(event, kids),
                    )
                    for event in picks_by_day[i]
                ],
            )
        )
    return cells


def _format_date_label(target: date) -> str:
    """Format the corner date, e.g. "June 3, 2026" (no leading zero on the day)."""
    return f"{target:%B} {target.day}, {target.year}"
