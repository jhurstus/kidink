from collections.abc import Sequence
from datetime import date, datetime

from app.calendar import CalendarEvent, EventOverrides, TimeOfDay
from app.config import Kid
from app.event_rows import KID_COLORS
from app.today import build_today

TARGET = date(2026, 6, 3)  # Wednesday

# Default start hour per bucket, matching the §6.4 derivation cutoffs.
_BUCKET_HOUR = {TimeOfDay.MORNING: 8, TimeOfDay.DAY: 12, TimeOfDay.EVENING: 18}

KIDS = [Kid(name="Julia", label="J"), Kid(name="Sam", label="S")]


def _event(
    title: str,
    day: date = TARGET,
    *,
    interesting: int = 100,
    is_chore: bool = False,
    time_of_day: TimeOfDay = TimeOfDay.DAY,
    hour: int | None = None,
    minute: int = 0,
    all_day: bool = False,
    kids: list[str] | None = None,
    icon_description: str | None = None,
) -> CalendarEvent:
    """A minimal event for Today bucketing/ranking tests."""
    if all_day:
        start: datetime | date = day
        end: datetime | date = day
    else:
        start = datetime(
            day.year, day.month, day.day, hour or _BUCKET_HOUR[time_of_day], minute
        )
        end = start
    return CalendarEvent(
        title=title,
        start=start,
        end=end,
        all_day=all_day,
        is_chore=is_chore,
        local_day=day,
        time_of_day=time_of_day,
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


def _titles(panel, key: str) -> list[str]:
    (bucket,) = [b for b in panel.buckets if b.key == key]
    return [row.title for row in bucket.rows]


def test_buckets_are_morning_day_evening_in_order() -> None:
    events = [
        _event("Movie night", time_of_day=TimeOfDay.EVENING),
        _event("Breakfast", time_of_day=TimeOfDay.MORNING),
        _event("Soccer", time_of_day=TimeOfDay.DAY),
    ]
    panel = build_today(TARGET, events)

    assert [b.key for b in panel.buckets] == ["morning", "day", "evening"]
    assert [b.name for b in panel.buckets] == ["MORNING", "DAY", "EVENING"]


def test_empty_buckets_are_dropped() -> None:
    events = [_event("Movie night", time_of_day=TimeOfDay.EVENING)]
    panel = build_today(TARGET, events)

    assert [b.key for b in panel.buckets] == ["evening"]


def test_no_events_yields_no_buckets() -> None:
    panel = build_today(TARGET)

    assert panel.buckets == []


def test_chores_and_other_days_are_excluded() -> None:
    events = [
        _event("Make bed", is_chore=True, interesting=999),
        _event("Birthday", date(2026, 6, 4), interesting=999),
    ]
    panel = build_today(TARGET, events)

    assert panel.buckets == []


def test_worst_case_cap_keeps_top_five_by_interesting() -> None:
    # All three buckets visible -> budget is 5 (§10.1 step 1-2); the four
    # least-interesting events are dropped silently (§4.1).
    events = [
        _event("Breakfast", time_of_day=TimeOfDay.MORNING, interesting=800),
        _event("School", time_of_day=TimeOfDay.MORNING, interesting=700),
        _event("Soccer", time_of_day=TimeOfDay.DAY, interesting=600),
        _event("Library", time_of_day=TimeOfDay.DAY, interesting=500),
        _event("Movie night", time_of_day=TimeOfDay.EVENING, interesting=400),
        _event("Bath", time_of_day=TimeOfDay.EVENING, interesting=300),
        _event("Dropped 1", time_of_day=TimeOfDay.MORNING, interesting=30),
        _event("Dropped 2", time_of_day=TimeOfDay.DAY, interesting=20),
        _event("Dropped 3", time_of_day=TimeOfDay.EVENING, interesting=10),
    ]
    panel = build_today(TARGET, events)

    shown = {row.title for bucket in panel.buckets for row in bucket.rows}
    assert shown == {"Breakfast", "School", "Soccer", "Library", "Movie night"}


def test_backfill_fills_visible_buckets_only() -> None:
    # Top-5 land in Morning+Evening only, so Day never becomes visible: the
    # 6th-ranked (Day) event is skipped and the 7th-ranked (Morning) event
    # backfills the freed header row instead (§10.1 step 4; capacity 6 with
    # two visible headers).
    events = [
        _event("M1", time_of_day=TimeOfDay.MORNING, interesting=900),
        _event("M2", time_of_day=TimeOfDay.MORNING, interesting=800),
        _event("M3", time_of_day=TimeOfDay.MORNING, interesting=700),
        _event("E1", time_of_day=TimeOfDay.EVENING, interesting=600),
        _event("E2", time_of_day=TimeOfDay.EVENING, interesting=500),
        _event("Day skipped", time_of_day=TimeOfDay.DAY, interesting=400),
        _event("M4 backfilled", time_of_day=TimeOfDay.MORNING, interesting=300),
        _event("M5 over capacity", time_of_day=TimeOfDay.MORNING, interesting=200),
    ]
    panel = build_today(TARGET, events)

    assert [b.key for b in panel.buckets] == ["morning", "evening"]
    shown = {row.title for bucket in panel.buckets for row in bucket.rows}
    assert shown == {"M1", "M2", "M3", "E1", "E2", "M4 backfilled"}


def test_single_bucket_day_caps_at_seven() -> None:
    events = [
        _event(f"D{i}", time_of_day=TimeOfDay.DAY, interesting=100 - i, hour=10 + i)
        for i in range(10)
    ]
    panel = build_today(TARGET, events)

    assert _titles(panel, "day") == [f"D{i}" for i in range(7)]


def test_display_order_is_chronological_within_bucket() -> None:
    # Selection is by interesting, but reading order is by start time (§10.2):
    # a low-interest 8am event prints above a high-interest 8:30am one.
    events = [
        _event(
            "Assembly",
            time_of_day=TimeOfDay.MORNING,
            hour=8,
            minute=30,
            interesting=900,
        ),
        _event("Breakfast", time_of_day=TimeOfDay.MORNING, hour=8, interesting=100),
    ]
    panel = build_today(TARGET, events)

    assert _titles(panel, "morning") == ["Breakfast", "Assembly"]


def test_all_day_events_sort_first_within_bucket() -> None:
    events = [
        _event("Soccer", time_of_day=TimeOfDay.DAY, hour=10),
        _event("Field trip", all_day=True),
    ]
    panel = build_today(TARGET, events)

    assert _titles(panel, "day") == ["Field trip", "Soccer"]


def test_display_ties_break_by_interesting_then_title() -> None:
    events = [
        _event("Banana", time_of_day=TimeOfDay.DAY, hour=10, interesting=100),
        _event("Apple", time_of_day=TimeOfDay.DAY, hour=10, interesting=100),
        _event("Zoo", time_of_day=TimeOfDay.DAY, hour=10, interesting=500),
    ]
    panel = build_today(TARGET, events)

    assert _titles(panel, "day") == ["Zoo", "Apple", "Banana"]


def test_shared_event_shows_no_badges() -> None:
    # No labels -> shared -> applies to every kid -> unlabeled, matching the
    # strip's lone shared icon (§8/§9.2).
    panel = build_today(TARGET, [_event("Breakfast")], kids=KIDS)

    assert panel.buckets[0].rows[0].kids == []


def test_labeled_event_shows_matching_kid_only() -> None:
    panel = build_today(TARGET, [_event("Swim", kids=["S"])], kids=KIDS)

    badges = panel.buckets[0].rows[0].kids
    assert [(b.initial, b.color) for b in badges] == [("S", KID_COLORS[1])]


def test_labels_match_case_insensitively() -> None:
    panel = build_today(TARGET, [_event("Swim", kids=["s"])], kids=KIDS)

    assert [b.initial for b in panel.buckets[0].rows[0].kids] == ["S"]


def test_event_labeled_for_every_kid_shows_no_badges() -> None:
    # Explicitly labeled for all configured kids == shared: unlabeled (§8).
    panel = build_today(TARGET, [_event("Trip", kids=["S", "J"])], kids=KIDS)

    assert panel.buckets[0].rows[0].kids == []


def test_labels_match_kid_name_too() -> None:
    # A label value may be the kid's full name instead of the short label (§8).
    panel = build_today(TARGET, [_event("Swim", kids=["sam"])], kids=KIDS)

    badges = panel.buckets[0].rows[0].kids
    assert [(b.initial, b.color) for b in badges] == [("S", KID_COLORS[1])]


def test_unknown_labels_yield_no_badges() -> None:
    panel = build_today(TARGET, [_event("Visit", kids=["X"])], kids=KIDS)

    assert panel.buckets[0].rows[0].kids == []


def test_no_configured_kids_yields_no_badges() -> None:
    panel = build_today(TARGET, [_event("Breakfast")])

    assert panel.buckets[0].rows[0].kids == []


def test_icons_resolved_only_for_surviving_rows() -> None:
    resolver = _RecordingResolver()
    events = [
        _event(f"D{i}", time_of_day=TimeOfDay.DAY, interesting=100 - i, hour=10 + i)
        for i in range(10)
    ]
    build_today(TARGET, events, icon_resolver=resolver)

    # 10 events, one visible bucket -> 7 rows; the dropped three never reach
    # the resolver (no wasted generations), and the whole panel resolves in a
    # single batch so missing images can generate concurrently.
    assert resolver.calls == 1
    assert sorted(resolver.items) == sorted((f"D{i}", None) for i in range(7))


def test_resolver_receives_title_and_icon_description() -> None:
    resolver = _RecordingResolver()
    build_today(
        TARGET,
        [_event("S's game", icon_description="kids soccer match")],
        icon_resolver=resolver,
    )

    assert resolver.items == [("S's game", "kids soccer match")]


def test_failed_resolution_leaves_icon_url_none() -> None:
    resolver = _RecordingResolver(url=None)
    panel = build_today(TARGET, [_event("Soccer")], icon_resolver=resolver)

    row = panel.buckets[0].rows[0]
    assert row.icon_url is None
    assert row.title == "Soccer"


def test_build_is_deterministic_and_seeds_are_date_pure() -> None:
    events = [
        _event("Breakfast", time_of_day=TimeOfDay.MORNING),
        _event("Soccer", time_of_day=TimeOfDay.DAY),
    ]
    panel = build_today(TARGET, events, kids=KIDS)

    assert panel == build_today(TARGET, events, kids=KIDS)
    assert panel.seed == TARGET.toordinal()
    # Bucket seeds are offset by the bucket's fixed index (morning=1, day=2),
    # so they stay distinct within a page and stable across renders (§3.4).
    assert [b.seed for b in panel.buckets] == [
        TARGET.toordinal() + 1,
        TARGET.toordinal() + 2,
    ]
