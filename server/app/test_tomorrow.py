from collections.abc import Sequence
from datetime import date, datetime

from app.calendar import CalendarEvent, EventOverrides, TimeOfDay
from app.config import Kid
from app.event_rows import KID_COLORS
from app.tomorrow import build_tomorrow

TARGET = date(2026, 6, 3)  # Wednesday
TOMORROW = date(2026, 6, 4)  # Thursday

KIDS = [Kid(name="Julia", label="J"), Kid(name="Sam", label="S")]


def _event(
    title: str,
    day: date = TOMORROW,
    *,
    interesting: int = 100,
    is_chore: bool = False,
    hour: int = 12,
    minute: int = 0,
    all_day: bool = False,
    kids: list[str] | None = None,
    icon_description: str | None = None,
) -> CalendarEvent:
    """A minimal event for Tomorrow list tests (time_of_day is irrelevant, §11)."""
    if all_day:
        start: datetime | date = day
        end: datetime | date = day
    else:
        start = datetime(day.year, day.month, day.day, hour, minute)
        end = start
    return CalendarEvent(
        title=title,
        start=start,
        end=end,
        all_day=all_day,
        is_chore=is_chore,
        local_day=day,
        time_of_day=TimeOfDay.DAY,  # unused by build_tomorrow (§11: no buckets)
        overrides=EventOverrides(
            interesting=interesting,
            kids=kids or [],
            icon_description=icon_description,
        ),
    )


class _RecordingResolver:
    """Batch icon-resolver stub recording the icon items it is asked for."""

    def __init__(self, url: str | None = "http://icons/1") -> None:
        self.url = url
        self.items: list[tuple[str, str | None]] = []
        self.calls = 0

    def __call__(
        self, items: Sequence[tuple[str, str | None]]
    ) -> dict[str, str | None]:
        self.calls += 1
        self.items.extend(items)
        return {description or title: self.url for title, description in items}


def test_shows_only_the_next_days_non_chore_events() -> None:
    events = [
        _event("Today's event", TARGET, interesting=999),
        _event("Two days out", date(2026, 6, 5), interesting=999),
        _event("Make bed", is_chore=True, interesting=999),
        _event("Swim lesson"),
    ]
    panel = build_tomorrow(TARGET, events)

    assert [row.title for row in panel.rows] == ["Swim lesson"]


def test_no_events_yields_no_rows() -> None:
    assert build_tomorrow(TARGET).rows == []


def test_display_order_is_chronological_not_by_interesting() -> None:
    # A single flat list (§11): no buckets, reading order by start time even
    # when a later event is more interesting.
    events = [
        _event("Movie night", hour=19, interesting=900),
        _event("Breakfast", hour=8, interesting=100),
    ]
    panel = build_tomorrow(TARGET, events)

    assert [row.title for row in panel.rows] == ["Breakfast", "Movie night"]


def test_all_day_events_sort_first() -> None:
    events = [
        _event("Soccer", hour=10),
        _event("Field trip", all_day=True),
    ]
    panel = build_tomorrow(TARGET, events)

    assert [row.title for row in panel.rows] == ["Field trip", "Soccer"]


def test_cap_keeps_top_four_by_interesting() -> None:
    # 2 · ((_AVAILABLE_H - _PANEL_CHROME_H) // _ROW_H) = 4 (§4.1: two events
    # per visual row, two rows): the least-interesting events are dropped
    # silently, while the survivors still read chronologically.
    events = [
        _event("Dropped", hour=9, interesting=10),
        _event("Movie night", hour=19, interesting=400),
        _event("Breakfast", hour=8, interesting=800),
        _event("Soccer", hour=15, interesting=600),
        _event("Lunch", hour=12, interesting=500),
    ]
    panel = build_tomorrow(TARGET, events)

    assert [row.title for row in panel.rows] == [
        "Breakfast",
        "Lunch",
        "Soccer",
        "Movie night",
    ]


def test_assigned_event_shows_matching_kid_only() -> None:
    panel = build_tomorrow(TARGET, [_event("Swim", kids=["S"])], kids=KIDS)

    badges = panel.rows[0].kids
    assert [(b.initial, b.color) for b in badges] == [("S", KID_COLORS[1])]


def test_shared_event_shows_no_badges() -> None:
    panel = build_tomorrow(TARGET, [_event("Breakfast")], kids=KIDS)

    assert panel.rows[0].kids == []


def test_icons_resolved_in_one_batch_for_surviving_rows_only() -> None:
    resolver = _RecordingResolver()
    events = [_event(f"E{i}", hour=10 + i, interesting=100 - i) for i in range(6)]
    build_tomorrow(TARGET, events, icon_resolver=resolver)

    # 6 events, budget 4: the dropped two never reach the resolver (no wasted
    # generations), and the panel resolves in a single batch.
    assert resolver.calls == 1
    assert sorted(resolver.items) == [
        ("E0", None),
        ("E1", None),
        ("E2", None),
        ("E3", None),
    ]


def test_resolver_receives_title_and_icon_description() -> None:
    resolver = _RecordingResolver()
    build_tomorrow(
        TARGET,
        [_event("S's game", icon_description="kids soccer match")],
        icon_resolver=resolver,
    )

    assert resolver.items == [("S's game", "kids soccer match")]


def test_failed_resolution_leaves_icon_url_none() -> None:
    resolver = _RecordingResolver(url=None)
    panel = build_tomorrow(TARGET, [_event("Soccer")], icon_resolver=resolver)

    assert panel.rows[0].icon_url is None
    assert panel.rows[0].title == "Soccer"


def test_build_is_deterministic() -> None:
    events = [_event("Breakfast", hour=8)]
    panel = build_tomorrow(TARGET, events, kids=KIDS)

    assert panel == build_tomorrow(TARGET, events, kids=KIDS)


def test_weekday_is_the_day_after_targets() -> None:
    # Tints the TOMORROW! label with the day strip's colour for the shown day.
    assert build_tomorrow(TARGET).weekday == 3  # target is Wednesday -> Thursday
