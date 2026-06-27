"""Tests for recurrence expansion, chore split, and time-of-day (spec §6.2–§6.5).

All ICS is inline; no network is touched (the suite runs with ``--disable-socket``).
"""

from datetime import date, datetime, timedelta

from app.calendar.events import expand_events, partition
from app.calendar.overrides import TimeOfDay

TZ = "US/Pacific"
WEEK = [date(2026, 6, 1) + timedelta(days=i) for i in range(7)]  # Mon..Sun


def _cal(*vevents: str) -> str:
    body = "\n".join(vevents)
    return f"BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//test//EN\n{body}\nEND:VCALENDAR\n"


def _timed(uid: str, summary: str, start: str, end: str, extra: str = "") -> str:
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"SUMMARY:{summary}",
        f"DTSTART;TZID=America/Los_Angeles:{start}",
        f"DTEND;TZID=America/Los_Angeles:{end}",
    ]
    if extra:
        lines.append(extra)
    lines.append("END:VEVENT")
    return "\n".join(lines)


def test_empty_calendar_yields_no_events() -> None:
    assert expand_events(_cal(), WEEK, TZ) == []


def test_single_timed_event() -> None:
    events = expand_events(
        _cal(_timed("a", "Soccer", "20260603T120000", "20260603T130000")), WEEK, TZ
    )
    assert len(events) == 1
    event = events[0]
    assert event.title == "Soccer"
    assert event.all_day is False
    assert event.is_chore is False
    assert event.local_day == date(2026, 6, 3)
    assert event.time_of_day == TimeOfDay.DAY


def test_all_day_event() -> None:
    ics = _cal(
        "BEGIN:VEVENT\nUID:c\nSUMMARY:Camp day\n"
        "DTSTART;VALUE=DATE:20260603\nDTEND;VALUE=DATE:20260604\nEND:VEVENT"
    )
    (event,) = expand_events(ics, WEEK, TZ)
    assert event.all_day is True
    assert event.local_day == date(2026, 6, 3)
    assert event.time_of_day == TimeOfDay.DAY


def test_weekly_rrule_expands_only_within_the_week() -> None:
    # Weekly on Monday from 06-01 → exactly one instance in the 06-01..06-07 week.
    ics = _cal(
        _timed("w", "Soccer", "20260601T120000", "20260601T130000", "RRULE:FREQ=WEEKLY")
    )
    events = expand_events(ics, WEEK, TZ)
    assert [e.local_day for e in events] == [date(2026, 6, 1)]


def test_daily_rrule_fills_the_week() -> None:
    ics = _cal(
        _timed("d", "Standup", "20260601T120000", "20260601T123000", "RRULE:FREQ=DAILY")
    )
    events = expand_events(ics, WEEK, TZ)
    assert sorted(e.local_day for e in events) == WEEK


def test_exdate_removes_one_occurrence() -> None:
    ics = _cal(
        _timed(
            "d",
            "Standup",
            "20260601T120000",
            "20260601T123000",
            "RRULE:FREQ=DAILY\nEXDATE;TZID=America/Los_Angeles:20260603T120000",
        )
    )
    days = sorted(e.local_day for e in expand_events(ics, WEEK, TZ))
    assert date(2026, 6, 3) not in days
    assert len(days) == 6


def test_recurrence_id_override_moves_an_occurrence() -> None:
    # A daily 09:00 event, with the 06-03 instance overridden to 15:00 + a new title.
    ics = _cal(
        _timed(
            "r", "Daily thing", "20260601T090000", "20260601T093000", "RRULE:FREQ=DAILY"
        ),
        "BEGIN:VEVENT\nUID:r\n"
        "RECURRENCE-ID;TZID=America/Los_Angeles:20260603T090000\n"
        "SUMMARY:Moved thing\n"
        "DTSTART;TZID=America/Los_Angeles:20260603T150000\n"
        "DTEND;TZID=America/Los_Angeles:20260603T153000\nEND:VEVENT",
    )
    on_0603 = [
        e for e in expand_events(ics, WEEK, TZ) if e.local_day == date(2026, 6, 3)
    ]
    assert len(on_0603) == 1
    assert on_0603[0].title == "Moved thing"
    assert isinstance(on_0603[0].start, datetime)
    assert on_0603[0].start.hour == 15


def test_utc_event_lands_on_correct_local_day() -> None:
    # 2026-06-02 05:00 UTC == 2026-06-01 22:00 in US/Pacific (PDT, −07:00).
    ics = _cal(
        "BEGIN:VEVENT\nUID:u\nSUMMARY:Late\n"
        "DTSTART:20260602T050000Z\nDTEND:20260602T060000Z\nEND:VEVENT"
    )
    (event,) = expand_events(ics, WEEK, TZ)
    assert event.local_day == date(2026, 6, 1)
    assert event.time_of_day == TimeOfDay.EVENING  # 22:00 local


def test_window_includes_sunday_excludes_next_monday() -> None:
    ics = _cal(
        _timed("sun", "Sunday late", "20260607T230000", "20260607T233000"),
        _timed("mon", "Next week", "20260608T090000", "20260608T093000"),
    )
    days = [e.local_day for e in expand_events(ics, WEEK, TZ)]
    assert days == [date(2026, 6, 7)]


def test_time_of_day_derivation_examples() -> None:
    # Spec §6.4 boundary examples.
    ics = _cal(
        _timed("m", "Morning", "20260601T080000", "20260601T090000"),  # ends 09:00
        _timed("d", "Day", "20260602T150000", "20260602T170000"),  # starts 15:00
        _timed("e", "Evening", "20260603T160000", "20260603T170000"),  # starts 16:00
    )
    by_title = {e.title: e.time_of_day for e in expand_events(ics, WEEK, TZ)}
    assert by_title == {
        "Morning": TimeOfDay.MORNING,
        "Day": TimeOfDay.DAY,
        "Evening": TimeOfDay.EVENING,
    }


def test_time_of_day_override_wins_over_derivation() -> None:
    # A midday event (would derive to DAY) explicitly marked evening.
    ics = _cal(
        _timed(
            "o",
            "Thing",
            "20260603T120000",
            "20260603T130000",
            'DESCRIPTION:time_of_day = "evening"',
        )
    )
    (event,) = expand_events(ics, WEEK, TZ)
    assert event.time_of_day == TimeOfDay.EVENING


def test_description_overrides_flow_into_event() -> None:
    ics = _cal(
        _timed(
            "x",
            "Soccer",
            "20260603T120000",
            "20260603T130000",
            r'DESCRIPTION:interesting = 250\nlabels = ["J"]',
        )
    )
    (event,) = expand_events(ics, WEEK, TZ)
    assert event.overrides.interesting == 250
    assert event.overrides.labels == ["J"]


def test_chore_split_variants_and_partition() -> None:
    ics = _cal(
        _timed("c1", "chore: Make bed", "20260601T080000", "20260601T081500"),
        _timed("c2", "Chore:Wash up", "20260601T080000", "20260601T081500"),
        _timed("c3", "CHORE: Sweep", "20260601T080000", "20260601T081500"),
        _timed("r1", "Soccer", "20260601T120000", "20260601T130000"),
    )
    events = expand_events(ics, WEEK, TZ)
    regular, chores = partition(events)
    assert {e.title for e in regular} == {"Soccer"}
    assert {e.title for e in chores} == {"Make bed", "Wash up", "Sweep"}
    assert all(e.is_chore for e in chores)
