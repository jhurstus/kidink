"""Shared building blocks for the event-list modules (spec §8, §9.2, §10, §11).

The Today/Tomorrow panels and the day-of-week strip all turn calendar events
into icon-plus-kid-badge items. This module holds the pieces they share — the
batch icon-resolver seam, the event -> image key, and the §8 kid assignment and
badge rules — so the per-module view models stay thin and agree on the
semantics.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from app.calendar import CalendarEvent
from app.config import Kid

# Structural stand-in for app.images.IconResolver (kept as a plain Callable so
# view-model modules need no images import): a batch of item descriptions -> a
# description -> icon-URL-or-None mapping, resolved in one call so missing
# images can generate concurrently behind it.
type IconResolver = Callable[[Sequence[str]], Mapping[str, str | None]]


def no_icons(item_descriptions: Sequence[str]) -> Mapping[str, str | None]:
    """Default resolver: no icons — keeps view-model builders pure by default."""
    return {}


def icon_key(event: CalendarEvent) -> str:
    """The event's image key: ``icon_description`` or its title (§6.4/§7.1)."""
    return event.overrides.icon_description or event.title


# Per-kid badge color by config position (kid 0, kid 1). Red and blue are the
# panel's two strongest, most separable ink hues (§5.5); solid ink is fine for
# text (§5.3). A curated design choice, not deployment config — promotable to a
# Settings field later if more kids or custom colors are ever needed.
KID_COLORS = ["#e02b20", "#4aa8e8"]


@dataclass(frozen=True)
class KidBadge:
    """One kid initial shown on an event item (§8)."""

    initial: str
    color: str


def assigned_kids(event: CalendarEvent, kids: Sequence[Kid]) -> tuple[int, ...]:
    """Indices (config order) of the kids ``event`` applies to (§8).

    An empty ``kids`` override -> shared -> every kid. Otherwise the named
    kids: an entry matches a kid's ``label`` or ``name``, case-insensitively;
    entries matching no configured kid assign nobody (the event was explicitly
    assigned, just not to these kids).
    """
    names = {name.casefold() for name in event.overrides.kids}
    if not names:
        return tuple(range(len(kids)))
    return tuple(
        i
        for i, kid in enumerate(kids)
        if kid.label.casefold() in names or kid.name.casefold() in names
    )


def kid_badge(index: int, kids: Sequence[Kid]) -> KidBadge:
    """The badge for the kid at config position ``index``."""
    return KidBadge(
        initial=kids[index].label, color=KID_COLORS[index % len(KID_COLORS)]
    )


def kid_badges(event: CalendarEvent, kids: Sequence[Kid]) -> list[KidBadge]:
    """The event's kid badges (§8), in config order.

    Badges mark a *proper subset* of the configured kids: an event that applies
    to everyone — shared (empty ``kids`` override) or explicitly assigned to
    every kid — shows no badges, matching the day strip's unlabeled lone shared
    icon (§8/§9.2).
    """
    assigned = assigned_kids(event, kids)
    if len(assigned) == len(kids):
        return []
    return [kid_badge(i, kids) for i in assigned]
