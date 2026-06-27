"""View model for the day-of-week strip (spec §9).

Builds the seven Mon–Sun day cells for a target date: each cell's name, its
fixed per-day comic colors (spec §5.3, cool Mon–Thu / warm Fri–Sun), and whether
it is "today". Calendar data and day icons are intentionally not handled here yet.
"""

from dataclasses import dataclass
from datetime import date

from app.dates import week_of

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

# Cell widths (px). The active cell is ~75% wider than every other cell, and all six
# non-active cells shrink to one uniform width; the values are chosen so the seven
# cells + gaps still span the strip about as fully as a uniform layout would
# (301 + 6*172 ≈ 7*190). The no-burst fallback (every cell 190) is unused while all
# seven days map above, but kept for robustness if a mapping is removed.
_DEFAULT_CELL_W = 190
_BURST_CELL_W = 301
_SHRUNK_CELL_W = 172

# Strip layout geometry (mirrors day_strip.css and the day_group macro): used to
# place the active day's burst horizontally, centered over its cell.
_STRIP_W = 1540
_CELL_GAP = 12
_ROW_PAD = 12
_GROUP_GAP = 48


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


@dataclass(frozen=True)
class DayStrip:
    """The complete view model rendered by ``templates/modules/day_strip.html``."""

    week: list[DayCell]
    date_label: str


def build_day_strip(target: date) -> DayStrip:
    """Build the full day-strip view model for the resolved render date ``target``."""
    return DayStrip(
        week=_build_week_cells(week_of(target), target),
        date_label=_format_date_label(target),
    )


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


def _build_week_cells(week: list[date], target: date) -> list[DayCell]:
    """Build the seven ``DayCell``s for ``week`` (Mon..Sun), flagging ``target``.

    ``week`` must be the seven Mon–Sun dates (see ``dates.week_of``).
    """
    # The today cell sits at index ``target.weekday()`` (week is Mon-first). It gets
    # a burst — and triggers the width redistribution — only if that weekday has one.
    active_idx = target.weekday() if target.weekday() in BURST_BY_WEEKDAY else None
    widths = [_cell_width(i, active_idx) for i in range(7)]
    cx = round(_burst_center_x(widths, active_idx)) if active_idx is not None else None
    return [
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
        )
        for i, (day, palette) in enumerate(zip(week, DAY_PALETTE, strict=True))
    ]


def _format_date_label(target: date) -> str:
    """Format the corner date, e.g. "June 3, 2026" (no leading zero on the day)."""
    return f"{target:%B} {target.day}, {target.year}"
