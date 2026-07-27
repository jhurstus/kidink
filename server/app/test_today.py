from collections.abc import Sequence
from datetime import date, datetime

from app.calendar import CalendarEvent, EventOverrides, TimeOfDay
from app.config import Kid
from app.event_rows import KID_COLORS
from app.today import build_today, caption_eligible

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
    sfx: str | None = None,
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
            sfx=sfx,
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


def test_worst_case_cap_keeps_three_rows_of_pairs() -> None:
    # All three buckets visible -> budget is 3 visual rows (§10.1 step 1-2);
    # rows hold two events each, so six events fit (Morning 2 + Day 2 +
    # Evening 2 = 1+1+1 rows) and the least-interesting three are dropped
    # silently (§4.1): with all three headers still visible, capacity stays
    # 3 rows and no backfill fits.
    events = [
        _event("M1", time_of_day=TimeOfDay.MORNING, interesting=900),
        _event("M2", time_of_day=TimeOfDay.MORNING, interesting=850),
        _event("D1", time_of_day=TimeOfDay.DAY, interesting=700),
        _event("D2", time_of_day=TimeOfDay.DAY, interesting=650),
        _event("E1", time_of_day=TimeOfDay.EVENING, interesting=600),
        _event("E2", time_of_day=TimeOfDay.EVENING, interesting=550),
        _event("Dropped 1", time_of_day=TimeOfDay.MORNING, interesting=30),
        _event("Dropped 2", time_of_day=TimeOfDay.DAY, interesting=20),
        _event("Dropped 3", time_of_day=TimeOfDay.EVENING, interesting=10),
    ]
    panel = build_today(TARGET, events)

    shown = {row.title for bucket in panel.buckets for row in bucket.rows}
    assert shown == {"M1", "M2", "D1", "D2", "E1", "E2"}


def test_odd_buckets_waste_a_half_row() -> None:
    # Rows never mix buckets: three Morning + three Day events need four
    # visual rows (2+2, each with a half-filled last row), so the worst-case
    # budget of 3 exhausts at D3 and the Evening pair is dropped with its
    # header even though only six events are shown; freeing that header
    # lifts capacity to 5 rows and D3 backfills (§10.1 step 4).
    events = [
        _event("M1", time_of_day=TimeOfDay.MORNING, interesting=900),
        _event("M2", time_of_day=TimeOfDay.MORNING, interesting=880),
        _event("M3", time_of_day=TimeOfDay.MORNING, interesting=860),
        _event("D1", time_of_day=TimeOfDay.DAY, interesting=840),
        _event("D2", time_of_day=TimeOfDay.DAY, interesting=820),
        _event("D3", time_of_day=TimeOfDay.DAY, interesting=800),
        _event("E1", time_of_day=TimeOfDay.EVENING, interesting=700),
        _event("E2", time_of_day=TimeOfDay.EVENING, interesting=680),
    ]
    panel = build_today(TARGET, events)

    assert [b.key for b in panel.buckets] == ["morning", "day"]
    shown = {row.title for bucket in panel.buckets for row in bucket.rows}
    assert shown == {"M1", "M2", "M3", "D1", "D2", "D3"}


def test_backfill_fills_visible_buckets_only() -> None:
    # The top-ranked events fill the three worst-case rows with
    # Morning+Evening only, so Day never becomes visible: the next-ranked
    # (Day) event is skipped and later Morning events backfill the freed
    # header row instead (§10.1 step 4; capacity 5 rows with two visible
    # headers).
    events = [
        _event(f"M{i}", time_of_day=TimeOfDay.MORNING, interesting=900 - i, minute=i)
        for i in range(1, 5)
    ] + [
        _event("E1", time_of_day=TimeOfDay.EVENING, interesting=700),
        _event("E2", time_of_day=TimeOfDay.EVENING, interesting=690),
        _event("Day skipped", time_of_day=TimeOfDay.DAY, interesting=500),
        _event("M5 backfilled", time_of_day=TimeOfDay.MORNING, interesting=400),
        _event("M6 backfilled", time_of_day=TimeOfDay.MORNING, interesting=390),
        _event("M7 backfilled", time_of_day=TimeOfDay.MORNING, interesting=380),
        _event("M8 backfilled", time_of_day=TimeOfDay.MORNING, interesting=370),
        _event("M9 over capacity", time_of_day=TimeOfDay.MORNING, interesting=360),
    ]
    panel = build_today(TARGET, events)

    assert [b.key for b in panel.buckets] == ["morning", "evening"]
    shown = {row.title for bucket in panel.buckets for row in bucket.rows}
    assert shown == {f"M{i}" for i in range(1, 5)} | {
        "E1",
        "E2",
        "M5 backfilled",
        "M6 backfilled",
        "M7 backfilled",
        "M8 backfilled",
    }


def test_single_bucket_day_caps_at_twelve() -> None:
    # One visible bucket -> capacity 6 visual rows = 12 events, two per row.
    events = [
        _event(f"D{i:02}", time_of_day=TimeOfDay.DAY, interesting=100 - i, minute=i)
        for i in range(15)
    ]
    panel = build_today(TARGET, events)

    assert _titles(panel, "day") == [f"D{i:02}" for i in range(12)]


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
        _event(f"D{i:02}", time_of_day=TimeOfDay.DAY, interesting=100 - i, minute=i)
        for i in range(15)
    ]
    build_today(TARGET, events, icon_resolver=resolver)

    # 15 events, one visible bucket -> 12 (six visual rows of two); the
    # dropped three never reach the resolver (no wasted generations), and the
    # whole panel resolves in a single batch so missing images can generate
    # concurrently.
    assert resolver.calls == 1
    assert sorted(resolver.items) == sorted((f"D{i:02}", None) for i in range(12))


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


def test_sfx_shows_on_a_lone_event() -> None:
    # A single event leaves its bucket odd: its left-column cell has an empty
    # right-hand neighbor, so the shout renders there (§10.4).
    panel = build_today(TARGET, [_event("Breakfast", sfx="Yum!")])

    assert panel.buckets[0].sfx == "Yum!"


def test_sfx_needs_an_empty_right_cell() -> None:
    # Two events fill the visual row completely — no empty right-hand cell, so
    # no shout, no matter which events carry the override.
    events = [
        _event("Breakfast", hour=8, sfx="Yum!"),
        _event("Soccer", hour=10, sfx="Pow!"),
    ]
    panel = build_today(TARGET, events)

    assert panel.buckets[0].sfx is None


def test_sfx_only_the_trailing_event_qualifies() -> None:
    # The override sits on the chronologically-first event: it renders in the
    # left column, but its row is full. Only the trailing event's cell is
    # empty, and that event carries no sfx -> no shout anywhere.
    events = [
        _event("Breakfast", hour=8, sfx="Yum!"),
        _event("Assembly", hour=10),
        _event("Lunch", hour=12),
    ]
    panel = build_today(TARGET, events)

    assert [b.sfx for b in panel.buckets] == [None]


def test_sfx_candidate_is_the_display_order_last_event() -> None:
    # The trailing (empty-right-cell) slot belongs to the chronologically-last
    # displayed event (§10.2), not the highest-ranked one.
    events = [
        _event("Zoo", hour=10, interesting=500),
        _event("Breakfast", hour=8),
        _event("Nap", hour=14, interesting=50, sfx="Zzz!"),
    ]
    panel = build_today(TARGET, events)

    assert panel.buckets[0].sfx == "Zzz!"


def test_sfx_at_most_one_and_most_interesting_wins() -> None:
    # Two qualifying candidates in different buckets: only the more
    # interesting one shouts; the other bucket stays silent.
    events = [
        _event("Breakfast", time_of_day=TimeOfDay.MORNING, interesting=200, sfx="Yum!"),
        _event(
            "Movie night", time_of_day=TimeOfDay.EVENING, interesting=300, sfx="Pow!"
        ),
    ]
    panel = build_today(TARGET, events)

    assert {b.key: b.sfx for b in panel.buckets} == {
        "morning": None,
        "evening": "Pow!",
    }


def test_sfx_tie_breaks_alphabetically_by_title() -> None:
    # Equal interesting -> the alphabetically-first title wins, regardless of
    # bucket order (the evening event here beats the morning one).
    events = [
        _event("Zebra feeding", time_of_day=TimeOfDay.MORNING, sfx="Roar!"),
        _event("Art class", time_of_day=TimeOfDay.EVENING, sfx="Tada!"),
    ]
    panel = build_today(TARGET, events)

    assert {b.key: b.sfx for b in panel.buckets} == {
        "morning": None,
        "evening": "Tada!",
    }


def test_sfx_empty_string_never_shows() -> None:
    panel = build_today(TARGET, [_event("Breakfast", sfx="")])

    assert panel.buckets[0].sfx is None


def test_sfx_ignores_events_dropped_by_the_cap() -> None:
    # 13 single-bucket events: the cap keeps 12 (§10.1), an even count filling
    # every row. The dropped 13th — chronologically last, sfx set — must not
    # conjure a shout off the pre-cap odd count: eligibility is judged on what
    # actually renders (§10.4).
    events = [
        _event(
            f"D{i:02}",
            time_of_day=TimeOfDay.DAY,
            interesting=100 - i,
            minute=i,
            sfx="Boom!" if i == 12 else None,
        )
        for i in range(13)
    ]
    panel = build_today(TARGET, events)

    assert panel.buckets[0].sfx is None


def test_build_is_deterministic() -> None:
    events = [
        _event("Breakfast", time_of_day=TimeOfDay.MORNING),
        _event("Soccer", time_of_day=TimeOfDay.DAY),
    ]
    panel = build_today(TARGET, events, kids=KIDS)

    assert panel == build_today(TARGET, events, kids=KIDS)


def test_weekday_is_targets() -> None:
    # Tints the TODAY! tab with the day strip's colour for the target day.
    assert build_today(TARGET).weekday == 2  # Wednesday


class _RecordingCaptionProvider:
    """Caption-provider stub recording whether it was consulted (§10.5)."""

    def __init__(self, caption: str | None = "Blorp.") -> None:
        self.caption = caption
        self.calls = 0

    def __call__(self) -> str | None:
        self.calls += 1
        return self.caption


def _bucket_events(shape: dict[TimeOfDay, int]) -> list[CalendarEvent]:
    """Events producing ``shape``'s per-bucket counts (spread across minutes)."""
    return [
        _event(f"{time_of_day.value} {i}", time_of_day=time_of_day, minute=i)
        for time_of_day, count in shape.items()
        for i in range(count)
    ]


def test_caption_shows_on_an_empty_day() -> None:
    provider = _RecordingCaptionProvider()
    panel = build_today(TARGET, caption_provider=provider)

    assert panel.caption == "Blorp."
    assert provider.calls == 1


def test_caption_shows_on_one_and_two_single_row_buckets() -> None:
    for shape in (
        {TimeOfDay.MORNING: 1},
        {TimeOfDay.DAY: 2},
        {TimeOfDay.MORNING: 1, TimeOfDay.EVENING: 2},
    ):
        panel = build_today(
            TARGET, _bucket_events(shape), caption_provider=_RecordingCaptionProvider()
        )
        assert panel.caption == "Blorp.", shape


def test_caption_shows_on_a_lone_bucket_of_two_rows() -> None:
    # A single bucket may span two visual rows (3-4 events, §10.2 two-across).
    for shape in ({TimeOfDay.DAY: 3}, {TimeOfDay.DAY: 4}):
        panel = build_today(
            TARGET, _bucket_events(shape), caption_provider=_RecordingCaptionProvider()
        )
        assert panel.caption == "Blorp.", shape


def test_caption_hides_when_a_lone_bucket_has_three_rows() -> None:
    # Five events in one bucket span three visual rows (§10.2 two-across).
    provider = _RecordingCaptionProvider()
    panel = build_today(
        TARGET, _bucket_events({TimeOfDay.DAY: 5}), caption_provider=provider
    )

    assert panel.caption is None
    assert provider.calls == 0  # never consulted: the rotation must not advance


def test_caption_hides_on_three_buckets() -> None:
    provider = _RecordingCaptionProvider()
    events = _bucket_events(
        {TimeOfDay.MORNING: 1, TimeOfDay.DAY: 1, TimeOfDay.EVENING: 1}
    )
    panel = build_today(TARGET, events, caption_provider=provider)

    assert panel.caption is None
    assert provider.calls == 0


def test_caption_hides_when_two_buckets_share_three_rows() -> None:
    # A two-row bucket only qualifies alone; next to another bucket the
    # total is three visual rows.
    provider = _RecordingCaptionProvider()
    events = _bucket_events({TimeOfDay.MORNING: 1, TimeOfDay.DAY: 4})
    panel = build_today(TARGET, events, caption_provider=provider)

    assert panel.caption is None
    assert provider.calls == 0


def test_caption_defaults_to_none() -> None:
    # The default no_caption provider keeps the builder pure.
    assert build_today(TARGET, [_event("Breakfast")]).caption is None


def test_caption_provider_may_supply_none() -> None:
    # An eligible day with nothing stored (or empty store) shows no bubble.
    provider = _RecordingCaptionProvider(caption=None)
    panel = build_today(TARGET, [_event("Breakfast")], caption_provider=provider)

    assert panel.caption is None
    assert provider.calls == 1


def test_caption_eligible_judges_visual_rows() -> None:
    def buckets(*row_counts: int):
        return build_today(
            TARGET,
            _bucket_events(
                dict(zip([TimeOfDay.MORNING, TimeOfDay.DAY], row_counts, strict=False))
            ),
        ).buckets

    assert caption_eligible([])
    assert caption_eligible(buckets(1))
    assert caption_eligible(buckets(2))
    assert caption_eligible(buckets(2, 2))
    assert caption_eligible(buckets(3))
    assert caption_eligible(buckets(4))
    assert not caption_eligible(buckets(5))
    assert not caption_eligible(buckets(1, 3))
