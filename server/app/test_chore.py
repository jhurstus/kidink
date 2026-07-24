from collections.abc import Sequence
from datetime import date, datetime

from app.calendar import CalendarEvent, EventOverrides, TimeOfDay
from app.chore import build_chore
from app.config import Kid
from app.event_rows import KID_COLORS

TARGET = date(2026, 6, 3)  # Wednesday

KIDS = [Kid(name="Julia", label="J"), Kid(name="Sam", label="S")]


def _chore(
    title: str,
    day: date = TARGET,
    *,
    interesting: int = 100,
    is_chore: bool = True,
    hour: int = 12,
    all_day: bool = False,
    kids: list[str] | None = None,
    icon_description: str | None = None,
) -> CalendarEvent:
    """A minimal event for Chores list tests (title already chore:-stripped)."""
    if all_day:
        start: datetime | date = day
        end: datetime | date = day
    else:
        start = datetime(day.year, day.month, day.day, hour, 0)
        end = start
    return CalendarEvent(
        title=title,
        start=start,
        end=end,
        all_day=all_day,
        is_chore=is_chore,
        local_day=day,
        time_of_day=TimeOfDay.DAY,  # unused by build_chore (§14: no buckets)
        overrides=EventOverrides(
            interesting=interesting,
            kids=kids or [],
            icon_description=icon_description,
        ),
    )


class _RecordingResolver:
    """Batch icon-resolver stub recording the icon items it is asked for."""

    def __init__(self, url: str | None = "http://icons/1") -> None:
        self.url = url
        self.items: list[tuple[str, str | None]] = []
        self.calls = 0

    def __call__(
        self, items: Sequence[tuple[str, str | None]]
    ) -> dict[str, str | None]:
        self.calls += 1
        self.items.extend(items)
        return {description or title: self.url for title, description in items}


def test_shows_only_the_target_days_chores() -> None:
    events = [
        _chore("Regular event", is_chore=False, interesting=999),
        _chore("Tomorrow's chore", date(2026, 6, 4), interesting=999),
        _chore("Make bed"),
    ]
    panel = build_chore(TARGET, events)

    assert [row.title for row in panel.rows] == ["Make bed"]


def test_no_chores_yields_no_rows() -> None:
    assert build_chore(TARGET).rows == []
    # A day of only regular events still yields an empty (plain) panel.
    assert build_chore(TARGET, [_chore("Soccer", is_chore=False)]).rows == []


def test_order_is_by_interesting_not_chronological() -> None:
    # §14: unlike Tomorrow, a more-interesting chore leads even when it is later
    # in the day (a chronological order would put "Early" first).
    events = [
        _chore("Later big", hour=19, interesting=900),
        _chore("Early small", hour=8, interesting=100),
    ]
    panel = build_chore(TARGET, events)

    assert [row.title for row in panel.rows] == ["Later big", "Early small"]


def test_equal_interesting_breaks_ties_by_title() -> None:
    events = [
        _chore("Wash dishes", interesting=100, hour=18),
        _chore("Feed cat", interesting=100, hour=8),
    ]
    panel = build_chore(TARGET, events)

    assert [row.title for row in panel.rows] == ["Feed cat", "Wash dishes"]


def test_two_chores_stay_a_single_block_ranked() -> None:
    # A column holds two (_AVAILABLE_H // _ROW_H): at or below that, one block
    # spanning the panel, ranked by interesting.
    events = [
        _chore("B low", interesting=100),
        _chore("A high", interesting=800),
    ]
    panel = build_chore(TARGET, events)

    assert panel.columns is None
    assert [row.title for row in panel.rows] == ["A high", "B low"]


def test_assigned_chore_shows_matching_kid_only() -> None:
    panel = build_chore(TARGET, [_chore("Make bed", kids=["S"])], kids=KIDS)

    badges = panel.rows[0].kids
    assert [(b.initial, b.color) for b in badges] == [("S", KID_COLORS[1])]


def test_shared_chore_shows_no_badges() -> None:
    panel = build_chore(TARGET, [_chore("Tidy up")], kids=KIDS)

    assert panel.rows[0].kids == []


def test_icons_resolved_in_one_batch_for_surviving_rows_only() -> None:
    resolver = _RecordingResolver()
    events = [_chore(f"C{i}", interesting=100 - i) for i in range(8)]
    build_chore(TARGET, events, icon_resolver=resolver)

    # 8 chores, two columns of 2: the dropped four never reach the resolver (no
    # wasted generations), resolved in a single batch.
    assert resolver.calls == 1
    assert sorted(resolver.items) == [(f"C{i}", None) for i in range(4)]


def test_resolver_receives_title_and_icon_description() -> None:
    resolver = _RecordingResolver()
    build_chore(
        TARGET,
        [_chore("Walk dog", icon_description="kid walking a puppy")],
        icon_resolver=resolver,
    )

    assert resolver.items == [("Walk dog", "kid walking a puppy")]


def test_failed_resolution_leaves_icon_url_none() -> None:
    resolver = _RecordingResolver(url=None)
    panel = build_chore(TARGET, [_chore("Make bed")], icon_resolver=resolver)

    assert panel.rows[0].icon_url is None
    assert panel.rows[0].title == "Make bed"


def test_build_is_deterministic_and_seed_is_date_pure() -> None:
    events = [_chore("Make bed")]
    panel = build_chore(TARGET, events, kids=KIDS)

    assert panel == build_chore(TARGET, events, kids=KIDS)
    # +7: the reserved slot after Dinner's +6 and before Joke's +8, so the
    # panel's border ripple stays distinct within a page (§3.4).
    assert panel.seed == TARGET.toordinal() + 7


# --- Two-column layout (§14) ------------------------------------------------


def test_three_or_more_chores_spill_into_two_columns() -> None:
    events = [_chore(f"C{i}", interesting=100 - i) for i in range(3)]
    panel = build_chore(TARGET, events)

    assert panel.rows == []
    assert panel.columns is not None
    # The top two (by interesting) fill the first column; the third spills.
    assert [r.title for r in panel.columns[0]] == ["C0", "C1"]
    assert [r.title for r in panel.columns[1]] == ["C2"]


def test_two_columns_cap_at_two_full_columns() -> None:
    # Eight chores, two per column (a 2x2 grid): the lowest-ranked four are
    # dropped (§4.1).
    events = [_chore(f"C{i}", interesting=100 - i) for i in range(8)]
    panel = build_chore(TARGET, events)

    assert panel.columns is not None
    assert [r.title for r in panel.columns[0]] == ["C0", "C1"]
    assert [r.title for r in panel.columns[1]] == ["C2", "C3"]


def test_presented_by_kid_then_interesting_then_title() -> None:
    # Presentation groups each kid's chores (config order), sorted by interesting
    # then title within the group; the flowed list then spills across columns.
    events = [
        _chore("Sam A", kids=["S"], interesting=500),
        _chore("Julia B", kids=["J"], interesting=100),
        _chore("Julia A", kids=["J"], interesting=400),
        _chore("Sam B", kids=["S"], interesting=300),
    ]
    panel = build_chore(TARGET, events, kids=KIDS)

    assert panel.columns is not None
    flowed = panel.columns[0] + panel.columns[1]
    assert [r.title for r in flowed] == ["Julia A", "Julia B", "Sam A", "Sam B"]
    # Julia (kid 0) fills the first column; Sam's fill the second (2x2 grid).
    assert [b.initial for b in panel.columns[0][0].kids] == ["J"]  # Julia A
    assert [b.initial for b in panel.columns[1][0].kids] == ["S"]  # Sam A


def test_selection_ranks_by_interesting_across_kids_then_presents_by_kid() -> None:
    # Eight chores: the cap keeps the top four by interesting regardless of kid
    # (the four lowest are dropped), while the survivors present grouped by kid.
    events = [
        _chore("J high", kids=["J"], interesting=900),
        _chore("S high", kids=["S"], interesting=800),
        _chore("J mid", kids=["J"], interesting=700),
        _chore("S mid", kids=["S"], interesting=600),
        _chore("J mid2", kids=["J"], interesting=500),  # dropped by the cap
        _chore("S mid2", kids=["S"], interesting=400),  # dropped by the cap
        _chore("J low", kids=["J"], interesting=100),  # dropped by the cap
        _chore("S low", kids=["S"], interesting=50),  # dropped by the cap
    ]
    panel = build_chore(TARGET, events, kids=KIDS)

    assert panel.columns is not None
    flowed = panel.columns[0] + panel.columns[1]
    assert [r.title for r in flowed] == [
        "J high",
        "J mid",
        "S high",
        "S mid",
    ]


def test_two_column_icons_resolved_in_one_batch() -> None:
    resolver = _RecordingResolver()
    events = [_chore(f"C{i}", interesting=100 - i) for i in range(4)]
    build_chore(TARGET, events, icon_resolver=resolver)

    # One batch across both columns, so missing icons still generate concurrently.
    assert resolver.calls == 1
    assert sorted(resolver.items) == [(f"C{i}", None) for i in range(4)]
