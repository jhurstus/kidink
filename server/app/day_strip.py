"""View model for the day-of-week strip (spec §9).

Builds the seven Mon–Sun day cells for a target date: each cell's name, its
fixed per-day comic colors (spec §5.3, cool weekdays / warm weekend), and whether
it is "today". Calendar data and day icons are intentionally not handled here yet.
"""

from dataclasses import dataclass
from datetime import date

# Per-day comic colors, Monday..Sunday (spec §5.3 day-cell assignments). Each is a
# LIGHT panel background paired with a DARKER halftone dot of a similar hue. Exact
# hex values are starting points to tune on the physical panel.
DAY_PALETTE: list[dict[str, str]] = [
    {"name": "MONDAY", "bg": "#dbe7f5", "dot": "#5b89c4"},  # light-blue (sky)
    {"name": "TUESDAY", "bg": "#d2ece9", "dot": "#3f9b96"},  # teal
    {"name": "WEDNESDAY", "bg": "#dcefdf", "dot": "#4faf7d"},  # mint
    {"name": "THURSDAY", "bg": "#dee2f4", "dot": "#6b74c4"},  # periwinkle
    {"name": "FRIDAY", "bg": "#dbe3ec", "dot": "#5a7da8"},  # steel blue
    {"name": "SATURDAY", "bg": "#fbe6cf", "dot": "#e08a3c"},  # orange
    {"name": "SUNDAY", "bg": "#f7dbe6", "dot": "#d46a93"},  # pink
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


def build_week_cells(week: list[date], target: date) -> list[DayCell]:
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


def format_date_label(target: date) -> str:
    """Format the corner date, e.g. "June 3, 2026" (no leading zero on the day)."""
    return f"{target:%B} {target.day}, {target.year}"
