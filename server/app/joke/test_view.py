from datetime import date

from app.joke.view import build_joke

START = date(2026, 1, 1)  # Thursday
JOKES = ["joke A", "joke B", "joke C"]


class _RecordingResolver:
    """Hero-resolver stub recording each joke-text call."""

    def __init__(self, url: str | None = "http://heroes/1") -> None:
        self.url = url
        self.calls: list[str] = []

    def __call__(self, text: str) -> str | None:
        self.calls.append(text)
        return self.url


def test_index_is_the_day_offset_from_the_start_date() -> None:
    # index = (target - start).days % N (§15).
    resolver = _RecordingResolver()
    panel = build_joke(START, JOKES, START, hero_resolver=resolver)

    assert panel.text == "joke A"  # day 0
    assert panel.hero_url == "http://heroes/1"
    assert resolver.calls == ["joke A"]


def test_index_advances_by_day() -> None:
    assert build_joke(date(2026, 1, 2), JOKES, START).text == "joke B"
    assert build_joke(date(2026, 1, 3), JOKES, START).text == "joke C"


def test_index_wraps_modulo_n() -> None:
    # Day 3 wraps back to the first joke; day 7 to the second.
    assert build_joke(date(2026, 1, 4), JOKES, START).text == "joke A"
    assert build_joke(date(2026, 1, 8), JOKES, START).text == "joke B"


def test_target_before_start_still_indexes_in_range() -> None:
    # Python's % is non-negative, so a date before the start date is safe.
    panel = build_joke(date(2025, 12, 31), JOKES, START)
    assert panel.text in JOKES


def test_empty_store_yields_no_text_and_no_hero() -> None:
    resolver = _RecordingResolver()
    panel = build_joke(START, [], START, hero_resolver=resolver)

    assert panel.text is None
    assert panel.hero_url is None
    assert resolver.calls == []


def test_hero_miss_keeps_the_text() -> None:
    panel = build_joke(START, JOKES, START, hero_resolver=_RecordingResolver(url=None))

    assert panel.text == "joke A"
    assert panel.hero_url is None


def test_default_resolver_is_pure() -> None:
    assert build_joke(START, JOKES, START).hero_url is None


def test_seed_is_date_pure_and_distinct_from_dinner() -> None:
    # Dinner uses toordinal()+6; the joke's reserved slot is +8 (view comments).
    target = date(2026, 6, 3)
    assert build_joke(target, JOKES, START).seed == target.toordinal() + 8
