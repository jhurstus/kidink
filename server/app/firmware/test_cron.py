"""The reference cron implementation, checked against the shared table."""

from datetime import datetime

import pytest

from app.firmware.cron import CronError, next_fire, parse_cron
from app.firmware.cron_cases import CASES, INVALID


@pytest.mark.parametrize(("expr", "after", "expected"), CASES)
def test_next_fire(expr: str, after: str, expected: str | None) -> None:
    result = next_fire(parse_cron(expr), datetime.fromisoformat(after))
    assert (result.isoformat() if result else None) == expected


@pytest.mark.parametrize(("expr", "fragment"), INVALID)
def test_invalid_expressions(expr: str, fragment: str) -> None:
    with pytest.raises(CronError) as excinfo:
        parse_cron(expr)
    # The message must name the offending field or construct: this text is what
    # the deploy CLI surfaces when a schedule is mistyped.
    assert fragment.lower() in str(excinfo.value).lower()


def test_star_flags_follow_raw_text() -> None:
    """`*/2` counts as starred, which is what flips the day rule to AND."""
    spec = parse_cron("0 12 */2 * FRI")
    assert spec.dom_star is True
    assert spec.dow_star is False
    assert parse_cron("0 12 13 * FRI").dom_star is False


def test_dow_seven_folds_to_sunday() -> None:
    assert parse_cron("0 0 * * 7").dow == parse_cron("0 0 * * 0").dow


def test_step_may_exceed_the_field_range() -> None:
    """A step larger than the field simply yields the start value."""
    assert parse_cron("*/90 * * * *").minute == frozenset({0})


def test_names_are_case_insensitive() -> None:
    assert parse_cron("0 0 * * fri").dow == parse_cron("0 0 * * FRI").dow
    assert parse_cron("0 0 1 jan *").month == frozenset({1})


def test_default_schedule_covers_the_waking_day() -> None:
    """The shipped default wakes every two hours from 05:00 to 21:00."""
    spec = parse_cron("0 5-21/2 * * *")
    assert spec.hour == frozenset({5, 7, 9, 11, 13, 15, 17, 19, 21})
    assert spec.minute == frozenset({0})
