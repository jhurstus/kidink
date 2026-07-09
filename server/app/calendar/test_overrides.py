"""Tests for lenient TOML override parsing (spec §6.3–§6.4)."""

from app.calendar.overrides import EventOverrides, TimeOfDay, parse_overrides


def test_full_valid_toml_sets_every_field() -> None:
    overrides = parse_overrides(
        'time_of_day = "evening"\n'
        "interesting = 200\n"
        'kids = ["J", "S"]\n'
        "countdown_eligible = true\n"
        'icon_description = "soccer ball"'
    )
    assert overrides == EventOverrides(
        time_of_day=TimeOfDay.EVENING,
        interesting=200,
        kids=["J", "S"],
        countdown_eligible=True,
        icon_description="soccer ball",
    )


def test_empty_description_is_all_defaults() -> None:
    assert parse_overrides("") == EventOverrides()


def test_none_description_is_all_defaults() -> None:
    assert parse_overrides(None) == EventOverrides()


def test_whitespace_only_is_all_defaults() -> None:
    assert parse_overrides("   \n  \t ") == EventOverrides()


def test_non_toml_garbage_is_all_defaults() -> None:
    assert parse_overrides("!!! this is not toml ###") == EventOverrides()


def test_non_mapping_top_level_is_all_defaults() -> None:
    # A bare scalar / sequence is not a valid top-level TOML document → defaults.
    assert parse_overrides("42") == EventOverrides()
    assert parse_overrides('"just a string"') == EventOverrides()


def test_unknown_keys_are_ignored_and_siblings_kept() -> None:
    overrides = parse_overrides("mystery = 1\ninteresting = 7")
    assert overrides.interesting == 7
    assert overrides == EventOverrides(interesting=7)


def test_invalid_interesting_falls_back_keeping_siblings() -> None:
    overrides = parse_overrides('interesting = "lots"\nkids = ["J"]')
    assert overrides.interesting == 100  # default
    assert overrides.kids == ["J"]  # sibling preserved


def test_non_positive_interesting_falls_back_to_default() -> None:
    assert parse_overrides("interesting = 0").interesting == 100
    assert parse_overrides("interesting = -5").interesting == 100


def test_invalid_time_of_day_falls_back_keeping_siblings() -> None:
    overrides = parse_overrides('time_of_day = "midnight"\ncountdown_eligible = true')
    assert overrides.time_of_day is None  # default → derive later
    assert overrides.countdown_eligible is True  # sibling preserved


def test_bad_kids_element_drops_the_whole_field() -> None:
    # Field-level granularity (§6.3): one bad element reverts kids to its default.
    assert parse_overrides('kids = ["J", 5]').kids == []


def test_multiple_bad_fields_keep_only_the_valid_one() -> None:
    overrides = parse_overrides(
        'interesting = "x"\ntime_of_day = "noon"\nkids = ["J", "S"]'
    )
    assert overrides == EventOverrides(kids=["J", "S"])
