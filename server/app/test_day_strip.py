from datetime import date

from app.dates import week_of
from app.day_strip import build_week_cells, format_date_label


def _june_2026_week() -> list[date]:
    return week_of(date(2026, 6, 3))  # Mon Jun 1 .. Sun Jun 7


def test_build_week_cells_marks_exactly_one_today() -> None:
    target = date(2026, 6, 3)  # Wednesday
    cells = build_week_cells(_june_2026_week(), target)

    today = [c for c in cells if c.is_today]
    assert len(today) == 1
    assert today[0].iso == target.isoformat()
    assert today[0].name == "WEDNESDAY"


def test_build_week_cells_names_are_mon_to_sun() -> None:
    cells = build_week_cells(_june_2026_week(), date(2026, 6, 3))

    assert [c.name for c in cells] == [
        "MONDAY",
        "TUESDAY",
        "WEDNESDAY",
        "THURSDAY",
        "FRIDAY",
        "SATURDAY",
        "SUNDAY",
    ]


def test_build_week_cells_assigns_spec_colors() -> None:
    cells = build_week_cells(_june_2026_week(), date(2026, 6, 3))

    # §5.3 cool-weekday / warm-weekend mapping.
    assert (cells[0].bg, cells[0].dot) == ("#dbe7f5", "#5b89c4")  # Mon light-blue
    assert (cells[5].bg, cells[5].dot) == ("#fbe6cf", "#e08a3c")  # Sat orange
    assert (cells[6].bg, cells[6].dot) == ("#f7dbe6", "#d46a93")  # Sun pink


def test_format_date_label_strips_leading_zero() -> None:
    assert format_date_label(date(2026, 6, 3)) == "June 3, 2026"
    assert format_date_label(date(2026, 12, 25)) == "December 25, 2026"
