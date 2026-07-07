from datetime import date, datetime

from app.calendar import CalendarEvent, EventOverrides, TimeOfDay
from app.day_strip import build_day_strip


def _event(
    title: str,
    day: date,
    *,
    interesting: int = 100,
    is_chore: bool = False,
    icon_description: str | None = None,
) -> CalendarEvent:
    """A minimal event for day-strip selection tests (only ranking fields matter)."""
    noon = datetime(day.year, day.month, day.day, 12)
    return CalendarEvent(
        title=title,
        start=noon,
        end=noon,
        all_day=False,
        is_chore=is_chore,
        local_day=day,
        time_of_day=TimeOfDay.DAY,
        overrides=EventOverrides(
            interesting=interesting, icon_description=icon_description
        ),
    )


class _RecordingResolver:
    """Icon-resolver stub that records the item descriptions it is asked for."""

    def __init__(self, url: str | None = "http://icons/1") -> None:
        self.url = url
        self.items: list[str] = []

    def __call__(self, item_description: str) -> str | None:
        self.items.append(item_description)
        return self.url


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


def test_build_day_strip_assigns_palette_colors() -> None:
    strip = build_day_strip(date(2026, 6, 3))

    # Spot-check the panel-tuned DAY_PALETTE mapping (see day_strip.py): each
    # day's border color lands on the right weekday.
    assert strip.week[0].color == "#3dbb4e"  # Mon green
    assert strip.week[3].color == "#8f6ade"  # Thu purple
    assert strip.week[5].color == "#ee7a14"  # Sat orange
    assert strip.week[6].color == "#f2c91d"  # Sun yellow


def test_build_day_strip_date_label_strips_leading_zero() -> None:
    assert build_day_strip(date(2026, 6, 3)).date_label == "June 3, 2026"
    assert build_day_strip(date(2026, 12, 25)).date_label == "December 25, 2026"


def test_build_day_strip_today_gets_burst_and_redistributes_widths() -> None:
    # 2026-06-22 is a Monday — today's cell is replaced by its burst and widened.
    strip = build_day_strip(date(2026, 6, 22))

    monday = strip.week[0]
    assert monday.is_today
    assert monday.burst == "monday_burst.png"
    assert monday.width == 306
    assert monday.burst_cx == 165  # centered over the leftmost cell

    # Every other cell (Tue–Sun) shrinks to one uniform smaller width, no burst.
    assert [c.width for c in strip.week[1:]] == [182] * 6
    assert [c for c in strip.week if c.burst] == [monday]


def test_build_day_strip_weekend_burst_centers_in_weekend_group() -> None:
    # 2026-06-27 is a Saturday — burst works for weekend days too, centered over
    # the (right-hand) weekend group cell.
    strip = build_day_strip(date(2026, 6, 27))

    saturday = strip.week[5]
    assert saturday.burst == "saturday_burst.png"
    assert saturday.width == 306
    assert saturday.burst_cx == 1178
    assert [c for c in strip.week if c.burst] == [saturday]


def test_build_day_strip_every_weekday_has_a_burst() -> None:
    # Each day of the week is its own burst, so today is always replaced.
    week_start = date(2026, 6, 22)  # Monday
    for offset in range(7):
        strip = build_day_strip(date(week_start.year, week_start.month, 22 + offset))
        bursts = [c for c in strip.week if c.burst]
        assert len(bursts) == 1
        assert bursts[0].is_today


def test_build_day_strip_has_no_event_titles_without_events() -> None:
    strip = build_day_strip(date(2026, 6, 3))

    assert all(cell.event_title is None for cell in strip.week)


def test_build_day_strip_shows_most_interesting_event_per_day() -> None:
    # Week of 2026-06-01 (Mon)..06-07 (Sun); cell index == weekday (Mon=0).
    events = [
        _event("Dentist", date(2026, 6, 2), interesting=50),
        _event("Soccer", date(2026, 6, 2), interesting=300),  # wins Tuesday
        _event("Library", date(2026, 6, 4), interesting=100),  # only Thursday event
    ]
    strip = build_day_strip(date(2026, 6, 3), events)

    assert strip.week[1].event_title == "Soccer"  # Tuesday
    assert strip.week[3].event_title == "Library"  # Thursday
    assert strip.week[0].event_title is None  # empty Monday


def test_build_day_strip_breaks_interesting_ties_by_title() -> None:
    events = [
        _event("Banana", date(2026, 6, 2), interesting=200),
        _event("Apple", date(2026, 6, 2), interesting=200),
    ]
    strip = build_day_strip(date(2026, 6, 3), events)

    assert strip.week[1].event_title == "Apple"  # ascending title tiebreak


def test_build_day_strip_excludes_chores_from_selection() -> None:
    events = [
        _event("Make bed", date(2026, 6, 2), interesting=999, is_chore=True),
        _event("Soccer", date(2026, 6, 2), interesting=100),
    ]
    strip = build_day_strip(date(2026, 6, 3), events)

    assert strip.week[1].event_title == "Soccer"


def test_build_day_strip_ignores_chore_only_and_out_of_week_days() -> None:
    events = [
        _event("Sweep", date(2026, 6, 5), interesting=500, is_chore=True),  # chore only
        _event("Birthday", date(2026, 6, 20), interesting=999),  # other week
    ]
    strip = build_day_strip(date(2026, 6, 3), events)

    assert all(cell.event_title is None for cell in strip.week)


def test_build_day_strip_default_resolver_yields_no_icons() -> None:
    events = [_event("Soccer", date(2026, 6, 2))]
    strip = build_day_strip(date(2026, 6, 3), events)

    assert strip.week[1].event_title == "Soccer"
    assert all(cell.icon_url is None for cell in strip.week)


def test_build_day_strip_resolves_icon_for_each_days_top_event() -> None:
    resolver = _RecordingResolver()
    events = [
        _event("Dentist", date(2026, 6, 2), interesting=50),
        _event("Soccer", date(2026, 6, 2), interesting=300),  # wins Tuesday
        _event("Library", date(2026, 6, 4)),
    ]
    strip = build_day_strip(date(2026, 6, 3), events, resolver)

    # Only the winning event per non-empty day reaches the resolver.
    assert sorted(resolver.items) == ["Library", "Soccer"]
    assert strip.week[1].icon_url == "http://icons/1"
    assert strip.week[0].icon_url is None  # empty Monday


def test_build_day_strip_prefers_icon_description_over_title() -> None:
    # §6.4/§7.1: the image is keyed by icon_description when the event sets one.
    resolver = _RecordingResolver()
    events = [
        _event("S's game", date(2026, 6, 2), icon_description="kids soccer match")
    ]
    build_day_strip(date(2026, 6, 3), events, resolver)

    assert resolver.items == ["kids soccer match"]


def test_build_day_strip_never_resolves_chores() -> None:
    resolver = _RecordingResolver()
    events = [_event("Make bed", date(2026, 6, 2), interesting=999, is_chore=True)]
    build_day_strip(date(2026, 6, 3), events, resolver)

    assert resolver.items == []


def test_build_day_strip_failed_resolution_keeps_title_for_fallback() -> None:
    # §7.3: a failed generation leaves icon_url None; the template then renders
    # the fallback chip from event_title.
    resolver = _RecordingResolver(url=None)
    events = [_event("Soccer", date(2026, 6, 2))]
    strip = build_day_strip(date(2026, 6, 3), events, resolver)

    assert strip.week[1].icon_url is None
    assert strip.week[1].event_title == "Soccer"
