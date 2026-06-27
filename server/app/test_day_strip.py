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

    # §5.3 cool Mon–Thu / warm Fri–Sun mapping.
    assert (strip.week[0].bg, strip.week[0].dot) == ("#d7f0dc", "#4ebc60")  # Mon green
    assert (strip.week[4].bg, strip.week[4].dot) == ("#f7dbe6", "#d46a93")  # Fri pink
    assert (strip.week[5].bg, strip.week[5].dot) == ("#fbe6cf", "#e08a3c")  # Sat orange
    assert (strip.week[6].bg, strip.week[6].dot) == ("#faf3d1", "#e2c536")  # Sun yellow


def test_build_day_strip_date_label_strips_leading_zero() -> None:
    assert build_day_strip(date(2026, 6, 3)).date_label == "June 3, 2026"
    assert build_day_strip(date(2026, 12, 25)).date_label == "December 25, 2026"


def test_build_day_strip_today_gets_burst_and_redistributes_widths() -> None:
    # 2026-06-22 is a Monday — today's cell is replaced by its burst and widened.
    strip = build_day_strip(date(2026, 6, 22))

    monday = strip.week[0]
    assert monday.is_today
    assert monday.burst == "monday_burst.png"
    assert monday.width == 301
    assert monday.burst_cx == 188  # centered over the leftmost cell

    # Every other cell (Tue–Sun) shrinks to one uniform smaller width, no burst.
    assert [c.width for c in strip.week[1:]] == [172] * 6
    assert [c for c in strip.week if c.burst] == [monday]


def test_build_day_strip_weekend_burst_centers_in_weekend_group() -> None:
    # 2026-06-27 is a Saturday — burst works for weekend days too, centered over
    # the (right-hand) weekend group cell.
    strip = build_day_strip(date(2026, 6, 27))

    saturday = strip.week[5]
    assert saturday.burst == "saturday_burst.png"
    assert saturday.width == 301
    assert saturday.burst_cx == 1168
    assert [c for c in strip.week if c.burst] == [saturday]


def test_build_day_strip_every_weekday_has_a_burst() -> None:
    # Each day of the week is its own burst, so today is always replaced.
    week_start = date(2026, 6, 22)  # Monday
    for offset in range(7):
        strip = build_day_strip(date(week_start.year, week_start.month, 22 + offset))
        bursts = [c for c in strip.week if c.burst]
        assert len(bursts) == 1
        assert bursts[0].is_today
