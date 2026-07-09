from datetime import date, datetime

from app.calendar import CalendarEvent, EventOverrides, TimeOfDay
from app.config import Kid
from app.event_rows import (
    KID_COLORS,
    assigned_kids,
    icon_key,
    kid_badge,
    kid_badges,
    no_icons,
)

KIDS = [Kid(name="Julia", label="J"), Kid(name="Sam", label="S")]


def _event(
    kids: list[str] | None = None, icon_description: str | None = None
) -> CalendarEvent:
    day = date(2026, 6, 3)
    noon = datetime(day.year, day.month, day.day, 12)
    return CalendarEvent(
        title="Soccer",
        start=noon,
        end=noon,
        all_day=False,
        is_chore=False,
        local_day=day,
        time_of_day=TimeOfDay.DAY,
        overrides=EventOverrides(kids=kids or [], icon_description=icon_description),
    )


def test_no_labels_assigns_every_kid() -> None:
    assert assigned_kids(_event(), KIDS) == (0, 1)


def test_label_assigns_matching_kid_only() -> None:
    assert assigned_kids(_event(kids=["S"]), KIDS) == (1,)


def test_labels_match_label_or_name_case_insensitively() -> None:
    assert assigned_kids(_event(kids=["j"]), KIDS) == (0,)
    assert assigned_kids(_event(kids=["JULIA"]), KIDS) == (0,)


def test_unknown_labels_assign_nobody() -> None:
    # The event was explicitly assigned, just not to these kids (§8).
    assert assigned_kids(_event(kids=["X"]), KIDS) == ()


def test_no_configured_kids_assigns_nobody() -> None:
    assert assigned_kids(_event(), []) == ()


def test_kid_badge_uses_config_position_color() -> None:
    badge = kid_badge(1, KIDS)
    assert (badge.initial, badge.color) == ("S", KID_COLORS[1])


def test_kid_badges_mark_proper_subsets_only() -> None:
    # Shared, labeled-for-everyone, and unknown-labels events all show no
    # badges; a proper subset shows its kids' badges in config order (§8).
    assert kid_badges(_event(), KIDS) == []
    assert kid_badges(_event(kids=["J", "S"]), KIDS) == []
    assert kid_badges(_event(kids=["X"]), KIDS) == []
    assert [b.initial for b in kid_badges(_event(kids=["S"]), KIDS)] == ["S"]


def test_icon_key_prefers_icon_description() -> None:
    assert icon_key(_event()) == "Soccer"
    assert icon_key(_event(icon_description="kids soccer match")) == "kids soccer match"


def test_no_icons_resolves_nothing() -> None:
    assert no_icons(["Soccer"]) == {}
