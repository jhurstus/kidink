from datetime import date, datetime, timedelta

from app.calendar import CalendarEvent, EventOverrides, TimeOfDay
from app.countdown.view import Tier, build_countdown, tier_for

TARGET = date(2026, 6, 3)  # Wednesday


def _event(
    title: str,
    day: date,
    *,
    eligible: bool = True,
    interesting: int = 100,
    is_chore: bool = False,
    hour: int | None = 12,
    icon_description: str | None = None,
) -> CalendarEvent:
    """A minimal event for countdown tests (time_of_day is irrelevant, §12)."""
    if hour is None:
        start: datetime | date = day
        end: datetime | date = day
        all_day = True
    else:
        start = datetime(day.year, day.month, day.day, hour, 0)
        end = start
        all_day = False
    return CalendarEvent(
        title=title,
        start=start,
        end=end,
        all_day=all_day,
        is_chore=is_chore,
        local_day=day,
        time_of_day=TimeOfDay.DAY,
        overrides=EventOverrides(
            interesting=interesting,
            countdown_eligible=eligible,
            icon_description=icon_description,
        ),
    )


def _in(days: int) -> date:
    return TARGET + timedelta(days=days)


class _RecordingResolver:
    """Hero-resolver stub recording each (icon item, excited) call."""

    def __init__(self, url: str | None = "http://heroes/1") -> None:
        self.url = url
        self.calls: list[tuple[tuple[str, str | None], bool]] = []

    def __call__(self, item: tuple[str, str | None], excited: bool) -> str | None:
        self.calls.append((item, excited))
        return self.url


# --- Target selection (§12: soonest day, interesting, start, title) ---------


def test_soonest_event_wins_over_interesting() -> None:
    events = [
        _event("Big trip", _in(20), interesting=999),
        _event("Small outing", _in(5), interesting=1),
    ]
    assert build_countdown(TARGET, events).title == "Small outing"


def test_same_day_higher_interesting_wins() -> None:
    events = [
        _event("Minor", _in(5), interesting=100),
        _event("Major", _in(5), interesting=200),
    ]
    assert build_countdown(TARGET, events).title == "Major"


def test_same_day_and_interest_earlier_start_wins() -> None:
    # start_key sorts all-day events first, then by clock time — the §12
    # "earliest start datetime" tiebreak, consistent with the other panels.
    events = [
        _event("Afternoon party", _in(5), hour=15),
        _event("Morning party", _in(5), hour=9),
        _event("All-day fair", _in(5), hour=None),
    ]
    assert build_countdown(TARGET, events).title == "All-day fair"


def test_title_is_the_final_tiebreak() -> None:
    events = [
        _event("Zoo visit", _in(5)),
        _event("Aquarium visit", _in(5)),
    ]
    assert build_countdown(TARGET, events).title == "Aquarium visit"


def test_ineligible_chore_and_past_events_are_skipped() -> None:
    events = [
        _event("Not eligible", _in(2), eligible=False),
        _event("Eligible chore", _in(3), is_chore=True),
        _event("Last week", _in(-7), interesting=999),
        _event("The real target", _in(9)),
    ]
    assert build_countdown(TARGET, events).title == "The real target"


def test_event_today_still_targets_today() -> None:
    # The zero-state (§12): the event day keeps the event; local_day >= target.
    events = [_event("Birthday", _in(0)), _event("Later", _in(4))]
    panel = build_countdown(TARGET, events)

    assert panel.title == "Birthday"
    assert panel.sleeps == 0


def test_blank_card_when_no_eligible_event() -> None:
    resolver = _RecordingResolver()
    panel = build_countdown(TARGET, [_event("Nope", _in(5), eligible=False)], resolver)

    assert panel.title is None
    assert panel.hero_url is None
    assert panel.copy == ""
    assert panel.sfx == ()
    assert resolver.calls == []  # no wasted generation for the blank state


# --- Sleeps, tiers, moons, copy ----------------------------------------------


def test_sleeps_counts_calendar_nights() -> None:
    assert build_countdown(TARGET, [_event("Trip", _in(1))]).sleeps == 1
    assert build_countdown(TARGET, [_event("Trip", _in(17))]).sleeps == 17


def test_tier_for_boundaries() -> None:
    # Hardcoded cutoffs: peak 0, hype 1, excited within 8 sleeps, calm beyond.
    assert tier_for(0) is Tier.PEAK
    assert tier_for(1) is Tier.HYPE
    assert tier_for(2) is Tier.EXCITED
    assert tier_for(8) is Tier.EXCITED
    assert tier_for(9) is Tier.CALM


def test_copy_escalates() -> None:
    # Calm has no exclamation point yet; the bang arrives with excited.
    assert build_countdown(TARGET, [_event("T", _in(17))]).copy == "17 sleeps to go"
    assert build_countdown(TARGET, [_event("T", _in(5))]).copy == "5 sleeps to go!"
    assert build_countdown(TARGET, [_event("T", _in(1))]).copy == "Just 1 more sleep!!"
    assert build_countdown(TARGET, [_event("T", _in(0))]).copy == "It's today!"


# --- SFX ----------------------------------------------------------------------


def test_sfx_only_at_peak() -> None:
    assert build_countdown(TARGET, [_event("T", _in(17))]).sfx == ()
    assert build_countdown(TARGET, [_event("T", _in(5))]).sfx == ()
    assert build_countdown(TARGET, [_event("T", _in(1))]).sfx == ()
    peak_sfx = build_countdown(TARGET, [_event("T", _in(0))]).sfx
    assert len(peak_sfx) == 2
    assert len(set(peak_sfx)) == 2  # both words, never a repeat


def test_sfx_order_is_date_seeded() -> None:
    # Consecutive dates flip the word order (ordinal parity over two words);
    # the same date always yields the same order (§3.4).
    day_one = build_countdown(TARGET, [_event("T", _in(0))]).sfx
    day_two = build_countdown(_in(1), [_event("T", _in(1))]).sfx

    assert day_one == build_countdown(TARGET, [_event("T", _in(0))]).sfx
    assert day_one != day_two


# --- Hero resolution ----------------------------------------------------------


def test_hero_resolver_receives_title_and_icon_description() -> None:
    resolver = _RecordingResolver()
    build_countdown(
        TARGET,
        [_event("Camping!!", _in(5), icon_description="a family camping trip")],
        resolver,
    )

    assert resolver.calls == [(("Camping!!", "a family camping trip"), False)]


def test_hero_requests_excited_variant_at_hype_and_peak() -> None:
    resolver = _RecordingResolver()
    build_countdown(TARGET, [_event("Trip", _in(5))], resolver)
    build_countdown(TARGET, [_event("Trip", _in(1))], resolver)
    build_countdown(TARGET, [_event("Trip", _in(0))], resolver)

    assert resolver.calls == [
        (("Trip", None), False),
        (("Trip", None), True),
        (("Trip", None), True),
    ]


def test_failed_resolution_leaves_hero_url_none() -> None:
    panel = build_countdown(TARGET, [_event("Trip", _in(5))], _RecordingResolver(None))

    assert panel.hero_url is None
    assert panel.title == "Trip"  # text remains on a hero miss (§7.3)


def test_build_is_deterministic_and_seed_is_date_pure() -> None:
    events = [_event("Trip", _in(5))]
    panel = build_countdown(TARGET, events)

    assert panel == build_countdown(TARGET, events)
    # +5: the next seed after Tomorrow's +4, so the border ripples stay
    # distinct within a page (§3.4).
    assert panel.seed == TARGET.toordinal() + 5
