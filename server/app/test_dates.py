from datetime import UTC, date, datetime, timedelta
from itertools import pairwise

import pytest

from app.dates import COUNTDOWN_HORIZON_DAYS, render_days, resolve_date, week_of


def test_resolve_date_uses_explicit_arg() -> None:
    # An explicit ?date= wins regardless of the clock.
    now = datetime(2020, 1, 1, 12, 0, tzinfo=UTC)

    assert resolve_date("2026-06-03", now=now, tz="US/Pacific") == date(2026, 6, 3)


def test_resolve_date_defaults_to_now_in_tz() -> None:
    # 03:00 UTC on Jun 23 is still Jun 22 in US/Pacific (UTC-7 in summer), so the
    # timezone conversion — not a bare .date() — is load-bearing.
    now = datetime(2026, 6, 23, 3, 0, tzinfo=UTC)

    assert resolve_date(None, now=now, tz="US/Pacific") == date(2026, 6, 22)


def test_resolve_date_rejects_bad_arg() -> None:
    now = datetime(2026, 6, 23, 12, 0, tzinfo=UTC)

    with pytest.raises(ValueError):
        resolve_date("not-a-date", now=now, tz="US/Pacific")


def test_week_of_returns_mon_to_sun() -> None:
    # Wednesday 2026-06-03.
    week = week_of(date(2026, 6, 3))

    assert len(week) == 7
    assert week[0] == date(2026, 6, 1)  # Monday
    assert week[6] == date(2026, 6, 7)  # Sunday
    assert week[0].weekday() == 0
    assert week[6].weekday() == 6
    assert date(2026, 6, 3) in week


def test_week_of_when_target_is_sunday() -> None:
    # Sunday's week still starts on the preceding Monday.
    week = week_of(date(2026, 6, 7))

    assert week[0] == date(2026, 6, 1)
    assert week[6] == date(2026, 6, 7)


def test_week_of_when_target_is_monday() -> None:
    # Monday is week[0] (its own week, not the prior one).
    week = week_of(date(2026, 6, 1))

    assert week[0] == date(2026, 6, 1)
    assert week[6] == date(2026, 6, 7)


def test_render_days_spans_week_monday_through_the_countdown_horizon() -> None:
    # Wednesday 2026-06-03: from its week's Monday (the day strip) through the
    # countdown horizon after the target itself.
    target = date(2026, 6, 3)
    days = render_days(target)

    assert days[0] == date(2026, 6, 1)  # Monday of the target's week
    assert days[-1] == target + timedelta(days=COUNTDOWN_HORIZON_DAYS)
    assert target in days


def test_render_days_is_contiguous_and_ascending() -> None:
    # expand_events builds its window from days[0]..days[-1] and filters to
    # membership, so the span must have no gaps.
    days = render_days(date(2026, 6, 7))

    assert all(
        later - earlier == timedelta(days=1) for earlier, later in pairwise(days)
    )
