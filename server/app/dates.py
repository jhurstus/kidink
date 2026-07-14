"""Date resolution and week math for the render pipeline.

Determinism (spec §3.4, CLAUDE.md): a render is a pure function of its inputs, so
the wall clock is *injected* (``now``) rather than read here. The HTTP boundary
reads the clock once and hands it in; everything downstream is a function of the
resolved date.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


def resolve_date(
    date_arg: str | None,
    *,
    now: datetime,
    tz: str,
) -> date:
    """Resolve the target date for a render.

    An explicit ``?date=YYYY-MM-DD`` wins; otherwise the calendar day of ``now``
    in ``tz`` (the configured display timezone, §18). ``now`` is injected (never
    ``datetime.now()``) so renders stay deterministic and testable.

    Raises ``ValueError`` on a malformed ``date_arg``.
    """
    if date_arg:
        return date.fromisoformat(date_arg)
    return now.astimezone(ZoneInfo(tz)).date()


def week_of(target: date) -> list[date]:
    """The seven dates of the Monday–Sunday week containing ``target``.

    Always returns 7 dates in Mon-first order (``date.weekday()`` has Monday=0).
    """
    monday = target - timedelta(days=target.weekday())
    return [monday + timedelta(days=i) for i in range(7)]


# How far past the target the Countdown module (§12) looks for its next
# eligible event. A render-window concern, so it lives here (not in
# app/countdown/) — one expansion serves every module.
COUNTDOWN_HORIZON_DAYS = 365


def render_days(target: date) -> list[date]:
    """The contiguous, ascending local days one render needs events for.

    From the Monday of ``target``'s week (the day strip, §9 — which also
    covers the Tomorrow panel, §11) through ``COUNTDOWN_HORIZON_DAYS`` after
    ``target`` (the Countdown target search, §12). The other modules filter by
    ``local_day``, so the long tail costs only expansion time.
    """
    monday = week_of(target)[0]
    horizon = target + timedelta(days=COUNTDOWN_HORIZON_DAYS)
    return [monday + timedelta(days=i) for i in range((horizon - monday).days + 1)]
