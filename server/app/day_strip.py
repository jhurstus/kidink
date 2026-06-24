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


def _build_week_cells(week: list[date], target: date) -> list[DayCell]:
    """Build the seven ``DayCell``s for ``week`` (Mon..Sun), flagging ``target``.

    ``week`` must be the seven Mon–Sun dates (see ``dates.week_of``).
    """
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
        )
        for i, (day, palette) in enumerate(zip(week, DAY_PALETTE, strict=True))
    ]


def _format_date_label(target: date) -> str:
    """Format the corner date, e.g. "June 3, 2026" (no leading zero on the day)."""
    return f"{target:%B} {target.day}, {target.year}"
