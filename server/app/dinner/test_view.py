from datetime import date, timedelta

from app.calendar import CalendarEvent, EventOverrides, TimeOfDay
from app.dinner.view import build_dinner, joined_meal_name

TARGET = date(2026, 6, 3)  # Wednesday


def _meal(title: str, day: date = TARGET) -> CalendarEvent:
    """A minimal all-day meal-plan entry, as the Anylist feed emits them."""
    return CalendarEvent(
        title=title,
        start=day,
        end=day,
        all_day=True,
        is_chore=False,
        local_day=day,
        time_of_day=TimeOfDay.DAY,
        overrides=EventOverrides(),
    )


class _RecordingResolver:
    """Hero-resolver stub recording each meal-name call."""

    def __init__(self, url: str | None = "http://heroes/1") -> None:
        self.url = url
        self.calls: list[str] = []

    def __call__(self, name: str) -> str | None:
        self.calls.append(name)
        return self.url


# --- joined_meal_name (§13: entries on the date ARE the dinner) --------------


def test_single_meal_is_its_own_name() -> None:
    assert joined_meal_name([_meal("Tacos")], TARGET) == "tacos"


def test_feed_names_are_lowercased() -> None:
    # Anylist recipe names arrive in inconsistent title case; the board, the
    # prompt, and the image key all use one uniform lowercase form.
    meals = [_meal("Healthier Homemade Crunchwrap Supreme")]
    assert joined_meal_name(meals, TARGET) == "healthier homemade crunchwrap supreme"


def test_multiple_meals_join_in_feed_order() -> None:
    meals = [_meal("Tacos"), _meal("Rice"), _meal("Beans")]
    assert joined_meal_name(meals, TARGET) == "tacos & rice & beans"


def test_other_day_meals_are_ignored() -> None:
    meals = [_meal("Tacos"), _meal("Soup", TARGET + timedelta(days=1))]
    assert joined_meal_name(meals, TARGET) == "tacos"


def test_blank_titles_are_skipped() -> None:
    meals = [_meal("Tacos"), _meal("   ")]
    assert joined_meal_name(meals, TARGET) == "tacos"


def test_no_meals_yields_none() -> None:
    assert joined_meal_name([], TARGET) is None
    assert joined_meal_name([_meal("Soup", TARGET + timedelta(days=1))], TARGET) is None


# --- build_dinner ------------------------------------------------------------


def test_panel_carries_joined_name_and_hero() -> None:
    resolver = _RecordingResolver()
    panel = build_dinner(
        TARGET, [_meal("Tacos"), _meal("Rice")], hero_resolver=resolver
    )

    assert panel.name == "tacos & rice"
    assert panel.hero_url == "http://heroes/1"
    assert resolver.calls == ["tacos & rice"]


def test_mystery_state_never_resolves_a_hero() -> None:
    resolver = _RecordingResolver()
    panel = build_dinner(TARGET, [], hero_resolver=resolver)

    assert panel.name is None
    assert panel.hero_url is None
    assert resolver.calls == []


def test_override_beats_the_feed_name() -> None:
    # The hand-typed override is used verbatim - no lowercasing.
    resolver = _RecordingResolver()
    panel = build_dinner(
        TARGET, [_meal("Tacos")], override="Pizza Night", hero_resolver=resolver
    )

    assert panel.name == "Pizza Night"
    assert resolver.calls == ["Pizza Night"]


def test_override_supplies_the_name_when_the_feed_is_empty() -> None:
    panel = build_dinner(TARGET, [], override="Pizza night")

    assert panel.name == "Pizza night"


def test_hero_miss_keeps_the_name() -> None:
    panel = build_dinner(
        TARGET, [_meal("Tacos")], hero_resolver=_RecordingResolver(url=None)
    )

    assert panel.name == "tacos"
    assert panel.hero_url is None


def test_default_resolver_is_pure() -> None:
    assert build_dinner(TARGET, [_meal("Tacos")]).hero_url is None


def test_seed_is_date_pure_and_distinct_from_countdown() -> None:
    # Countdown uses toordinal()+5 (view.py's seed comment tracks the budget).
    assert build_dinner(TARGET).seed == TARGET.toordinal() + 6
