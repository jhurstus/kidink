from datetime import date

from app.day_strip import build_day_strip


def test_build_day_strip_marks_exactly_one_today() -> None:
    target = date(2026, 6, 3)  # Wednesday
    strip = build_day_strip(target)

    today = [c for c in strip.week if c.is_today]
    assert len(today) == 1
    assert today[0].iso == target.isoformat()
    assert today[0].name == "WEDNESDAY"


def test_build_day_strip_week_is_mon_to_sun() -> None:
    strip = build_day_strip(date(2026, 6, 3))

    assert [c.name for c in strip.week] == [
        "MONDAY",
        "TUESDAY",
        "WEDNESDAY",
        "THURSDAY",
        "FRIDAY",
        "SATURDAY",
        "SUNDAY",
    ]


def test_build_day_strip_assigns_spec_colors() -> None:
    strip = build_day_strip(date(2026, 6, 3))

    # §5.3 cool-weekday / warm-weekend mapping.
    assert (strip.week[0].bg, strip.week[0].dot) == ("#dbe7f5", "#5b89c4")  # Mon blue
    assert (strip.week[5].bg, strip.week[5].dot) == ("#fbe6cf", "#e08a3c")  # Sat orange
    assert (strip.week[6].bg, strip.week[6].dot) == ("#f7dbe6", "#d46a93")  # Sun pink


def test_build_day_strip_date_label_strips_leading_zero() -> None:
    assert build_day_strip(date(2026, 6, 3)).date_label == "June 3, 2026"
    assert build_day_strip(date(2026, 12, 25)).date_label == "December 25, 2026"
