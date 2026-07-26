"""The single shared cron conformance table.

`test_cron.py` runs it against `cron.py`; `test_cron_cpp.py` pipes the very same
rows into a host build of `arduino/kidink/cron.cpp`. One copy means the device
implementation and the reference cannot silently drift.

Each row is ``(expression, after, expected)`` where `after` and `expected` are
naive local wall-clock stamps ``YYYY-MM-DDTHH:MM:SS`` and `expected` is `None`
when the expression can never fire.
"""

CASES: list[tuple[str, str, str | None]] = [
    # The shipped default: every two hours from 05:00 through 21:00.
    ("0 5-21/2 * * *", "2026-07-25T04:00:00", "2026-07-25T05:00:00"),
    ("0 5-21/2 * * *", "2026-07-25T05:00:00", "2026-07-25T07:00:00"),
    ("0 5-21/2 * * *", "2026-07-25T21:00:00", "2026-07-26T05:00:00"),
    # Strictly after: an exact match on `after` is not returned again.
    ("0 * * * *", "2026-07-25T09:00:00", "2026-07-25T10:00:00"),
    # Seconds on `after` are discarded, never rounded up past a match.
    ("0 * * * *", "2026-07-25T08:59:30", "2026-07-25T09:00:00"),
    # Day rollover.
    ("0 6 * * *", "2026-07-25T23:59:00", "2026-07-26T06:00:00"),
    # Step on a star.
    ("*/15 * * * *", "2026-07-25T09:07:00", "2026-07-25T09:15:00"),
    ("*/15 * * * *", "2026-07-25T09:47:00", "2026-07-25T10:00:00"),
    # Month rollover from a month that has no such day.
    ("0 0 1 * *", "2026-01-31T12:00:00", "2026-02-01T00:00:00"),
    # Leap day: 2027 is not a leap year, so this skips to 2028.
    ("0 0 29 2 *", "2026-03-01T00:00:00", "2028-02-29T00:00:00"),
    # Unsatisfiable: February never has 30 days.
    ("0 0 30 2 *", "2026-03-01T00:00:00", None),
    # Vixie's day rule. Neither day field is starred, so they OR: the 13th of
    # any month, or any Friday. 2026-07-25 is a Saturday; the next Friday is
    # 07-31, but the 13th of August is not sooner, so Friday wins.
    ("0 12 13 * FRI", "2026-07-25T13:00:00", "2026-07-31T12:00:00"),
    # ...and the 13th fires even though 2026-08-13 is a Thursday.
    ("0 12 13 * FRI", "2026-07-31T13:00:00", "2026-08-07T12:00:00"),
    # A starred day-of-month switches the rule to AND, so `*/2` still filters:
    # only Fridays that fall on an odd day-of-month. 2026-08-07 is a Friday and
    # day 7 is in {1,3,...}; 2026-07-31 is a Friday but day 31 is also odd.
    ("0 12 */2 * FRI", "2026-07-25T13:00:00", "2026-07-31T12:00:00"),
    ("0 12 */2 * FRI", "2026-07-31T13:00:00", "2026-08-07T12:00:00"),
    # A starred day-of-week is the everyday case: only day-of-month filters.
    ("0 0 13 * *", "2026-07-25T00:00:00", "2026-08-13T00:00:00"),
    # 7 and 0 are both Sunday. 2026-07-25 is a Saturday, so tomorrow.
    ("0 0 * * 7", "2026-07-25T00:00:00", "2026-07-26T00:00:00"),
    ("0 0 * * 0", "2026-07-25T00:00:00", "2026-07-26T00:00:00"),
    # Weekday name ranges. Saturday -> Monday.
    ("30 7 * * MON-FRI", "2026-07-25T09:00:00", "2026-07-27T07:30:00"),
    # Month names.
    ("0 0 1 JAN *", "2026-07-25T00:00:00", "2027-01-01T00:00:00"),
    # Comma lists across fields.
    ("15,45 8,20 * * *", "2026-07-25T08:20:00", "2026-07-25T08:45:00"),
    ("15,45 8,20 * * *", "2026-07-25T08:45:00", "2026-07-25T20:15:00"),
    # Vixie's "N/S": from N through the field maximum, stepping S.
    ("0 9/4 * * *", "2026-07-25T00:00:00", "2026-07-25T09:00:00"),
    ("0 9/4 * * *", "2026-07-25T09:00:00", "2026-07-25T13:00:00"),
    ("0 9/4 * * *", "2026-07-25T21:00:00", "2026-07-26T09:00:00"),
    # Macros.
    ("@daily", "2026-07-25T09:00:00", "2026-07-26T00:00:00"),
    ("@hourly", "2026-07-25T09:30:00", "2026-07-25T10:00:00"),
    ("@weekly", "2026-07-25T09:00:00", "2026-07-26T00:00:00"),
    ("@monthly", "2026-07-25T09:00:00", "2026-08-01T00:00:00"),
    ("@yearly", "2026-07-25T09:00:00", "2027-01-01T00:00:00"),
    # Spring forward: cron matches the wall clock, so a 02:30 fire is simply
    # named. Resolving it to an instant is the caller's job (mktime normalizes).
    ("30 2 * * *", "2026-03-08T00:00:00", "2026-03-08T02:30:00"),
    # A minute-granular schedule at the end of a year.
    ("59 23 31 12 *", "2026-06-01T00:00:00", "2026-12-31T23:59:00"),
]

# Expressions the parser must reject, with a substring its message should carry.
INVALID: list[tuple[str, str]] = [
    ("61 * * * *", "minute"),
    ("* * * *", "5 fields"),
    ("* * * * * *", "5 fields"),
    ("*/0 * * * *", "step"),
    ("0 0 L * *", "L"),
    ("0 0 1W * *", "W"),
    ("0 0 * * 5#2", "#"),
    ("0 0 ? * *", "?"),
    ("@reboot", "macro"),
    ("@nope", "macro"),
    ("0 24 * * *", "hour"),
    ("0 0 0 * *", "day-of-month"),
    ("0 0 * 13 *", "month"),
    ("0 0 * * 8", "day-of-week"),
    ("5-1 * * * *", "backwards"),
    ("0 0 * * MONDAY", "day-of-week"),
    ("", "5 fields"),
]
