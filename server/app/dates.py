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
