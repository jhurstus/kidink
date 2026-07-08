"""View model for the day-of-week strip (spec §9).

Builds the seven Mon–Sun day cells for a target date: each cell's name, its
fixed per-day border color (see DAY_PALETTE — hues picked for separability on
the six-ink panel, spec §5.5), whether it is "today", and the AI icon (plus
title) of its most-interesting (non-chore) event. The per-kid one/two-icon
selection of §9.2 is deferred — each cell shows a single icon for its top
event, with the title as fallback text.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date

from app.calendar import CalendarEvent
from app.dates import week_of

# Structural stand-in for app.images.IconResolver (kept as a plain Callable so
# this module needs no images import): item description -> icon URL or None.
type _IconResolver = Callable[[str], str | None]


def _no_icons(item_description: str) -> str | None:
    """Default resolver: no icons — keeps build_day_strip pure by default."""
    return None


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
class DayCell:
    """One day box in the strip."""

    name: str
    iso: str
    color: str
    is_today: bool
    width: int
    burst: str | None
    burst_cx: int | None
    event_title: str | None
    icon_url: str | None


@dataclass(frozen=True)
class DayStrip:
    """The complete view model rendered by ``templates/modules/day_strip.html``."""

    week: list[DayCell]
    date_label: str


def build_day_strip(
    target: date,
    events: Iterable[CalendarEvent] = (),
    icon_resolver: _IconResolver = _no_icons,
) -> DayStrip:
    """Build the full day-strip view model for the resolved render date ``target``.

    ``events`` are the week's expanded calendar events (see
    :func:`app.calendar.expand_events`); they are grouped by local day and each
    cell shows an icon for its most-interesting non-chore event, resolved
    through ``icon_resolver`` (see :data:`app.images.IconResolver`; the default
    resolves nothing, keeping the view model a pure function of its inputs).
    """
    by_day: dict[date, list[CalendarEvent]] = {}
    for event in events:
        by_day.setdefault(event.local_day, []).append(event)
    return DayStrip(
        week=_build_week_cells(week_of(target), target, by_day, icon_resolver),
        date_label=_format_date_label(target),
    )


def _top_event(day_events: list[CalendarEvent]) -> CalendarEvent | None:
    """The day's most-interesting non-chore event, or ``None``.

    Ranked by ``interesting`` descending, ties broken by title ascending — a total
    order, so the choice is deterministic for a given day (spec §3.4). Chores are
    excluded from the strip (§6.5, §9.2). The full per-kid one/two-icon selection
    of §9.2 is deferred; this picks a single event per day.
    """
    candidates = [event for event in day_events if not event.is_chore]
    if not candidates:
        return None
    return min(candidates, key=lambda e: (-e.overrides.interesting, e.title))


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
    icon_resolver: _IconResolver,
) -> list[DayCell]:
    """Build the seven ``DayCell``s for ``week`` (Mon..Sun), flagging ``target``.

    ``week`` must be the seven Mon–Sun dates (see ``dates.week_of``); ``by_day``
    maps each local day to its events. Each day's top event is resolved to an
    icon URL through ``icon_resolver``, keyed by the event's ``icon_description``
    (falling back to its title, §6.4/§7.1).
    """
    # The today cell sits at index ``target.weekday()`` (week is Mon-first). It gets
    # a burst — and triggers the width redistribution — only if that weekday has one.
    active_idx = target.weekday() if target.weekday() in BURST_BY_WEEKDAY else None
    widths = [_cell_width(i, active_idx) for i in range(7)]
    cx = round(_burst_center_x(widths, active_idx)) if active_idx is not None else None
    cells: list[DayCell] = []
    for i, (day, palette) in enumerate(zip(week, DAY_PALETTE, strict=True)):
        best = _top_event(by_day.get(day, []))
        icon_url = None
        if best is not None:
            icon_url = icon_resolver(best.overrides.icon_description or best.title)
        cells.append(
            DayCell(
                name=palette["name"],
                iso=day.isoformat(),
                color=palette["color"],
                is_today=(day == target),
                width=widths[i],
                burst=BURST_BY_WEEKDAY[i] if i == active_idx else None,
                burst_cx=cx if i == active_idx else None,
                event_title=best.title if best is not None else None,
                icon_url=icon_url,
            )
        )
    return cells


def _format_date_label(target: date) -> str:
    """Format the corner date, e.g. "June 3, 2026" (no leading zero on the day)."""
    return f"{target:%B} {target.day}, {target.year}"
