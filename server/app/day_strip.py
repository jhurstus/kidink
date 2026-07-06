"""View model for the day-of-week strip (spec §9).

Builds the seven Mon–Sun day cells for a target date: each cell's name, its
fixed per-day comic colors (spec §5.3, cool Mon–Thu / warm Fri–Sun), whether
it is "today", and the AI icon (plus title) of its most-interesting (non-chore)
event. The per-kid one/two-icon selection of §9.2 is deferred — each cell shows
a single icon for its top event, with the title as fallback text.
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


# Per-day comic colors, Monday..Sunday (spec §5.3 day-cell assignments). Each is a
# LIGHT panel background paired with a DARKER halftone dot of a similar hue. Exact
# hex values are starting points to tune on the physical panel.
DAY_PALETTE: list[dict[str, str]] = [
    {"name": "MONDAY", "bg": "#d7f0dc", "dot": "#4ebc60"},  # green (~130°)
    {"name": "TUESDAY", "bg": "#d7eaf4", "dot": "#4e97bc"},  # sky blue (~200°)
    {"name": "WEDNESDAY", "bg": "#d7daf4", "dot": "#4e57bc"},  # indigo (~235°)
    {"name": "THURSDAY", "bg": "#e6d7f4", "dot": "#854ebc"},  # violet (~270°)
    {"name": "FRIDAY", "bg": "#f7dbe6", "dot": "#d46a93"},  # pink (~340°)
    {"name": "SATURDAY", "bg": "#fbe6cf", "dot": "#e08a3c"},  # orange (~28°)
    {"name": "SUNDAY", "bg": "#faf3d1", "dot": "#e2c536"},  # yellow (~50°)
]

# Day-cell halftone shape (shared by every cell); see the comic_panel macro.
_CELL_MAX_FILL = 0.58
_CELL_ORIGIN_ANGLE = "180deg"
_CELL_MAGNITUDE = "35%"

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
# cells exactly fill the strip: with 139px of fixed overhead (gaps + row padding)
# they must sum to _STRIP_W - 139 = 1398 (306 + 6*182). The no-burst fallback
# (every cell 199, ~5px slack) is unused while all seven days map above, but kept
# for robustness if a mapping is removed.
_DEFAULT_CELL_W = 199
_BURST_CELL_W = 306
_SHRUNK_CELL_W = 182

# Strip layout geometry (mirrors day_strip.css and the day_group macro): used to
# place the active day's burst horizontally, centered over its cell.
_STRIP_W = 1537
_CELL_GAP = 12
_ROW_PAD = 12
_GROUP_GAP = 31


@dataclass(frozen=True)
class DayCell:
    """One day box in the strip."""

    name: str
    iso: str
    bg: str
    dot: str
    max_fill: float
    origin_angle: str
    magnitude: str
    seed: int
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
                bg=palette["bg"],
                dot=palette["dot"],
                max_fill=_CELL_MAX_FILL,
                origin_angle=_CELL_ORIGIN_ANGLE,
                magnitude=_CELL_MAGNITUDE,
                seed=173 + i,
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
