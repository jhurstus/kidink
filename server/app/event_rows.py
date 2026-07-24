"""Shared building blocks for the event-list modules (spec §8, §9.2, §10, §11).

The Today/Tomorrow panels and the day-of-week strip all turn calendar events
into icon-plus-kid-badge items. This module holds the pieces they share — the
batch icon-resolver seam, the event -> image key, the §8 kid assignment and
badge rules, and the row vocabulary of the list panels (the :class:`EventRow`
view model, the rank/display sort keys, and the row builder) — so the
per-module view models stay thin and agree on the semantics. Panel-specific
geometry (row budgets) and grouping stay in the owning modules.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, time

from app.calendar import CalendarEvent
from app.config import Kid

type IconItem = tuple[str, str | None]
"""One event's image inputs: ``(title, icon_description)`` (§6.4/§7.1).

The optional ``icon_description`` elaborates the title in the generation
prompt rather than replacing it; the logical image key stays
``icon_description or title`` (:func:`icon_key`).
"""

# Structural stand-in for app.images.IconResolver (kept as a plain Callable so
# view-model modules need no images import): a batch of icon items -> an
# icon_key -> icon-URL-or-None mapping, resolved in one call so missing
# images can generate concurrently behind it.
type IconResolver = Callable[[Sequence[IconItem]], Mapping[str, str | None]]


def no_icons(items: Sequence[IconItem]) -> Mapping[str, str | None]:
    """Default resolver: no icons — keeps view-model builders pure by default."""
    return {}


def icon_item(event: CalendarEvent) -> IconItem:
    """The event's image inputs: its title plus optional ``icon_description``."""
    return (event.title, event.overrides.icon_description)


def icon_key(event: CalendarEvent) -> str:
    """The event's image key: ``icon_description`` or its title (§6.4/§7.1)."""
    return event.overrides.icon_description or event.title


def resolve_icons(
    events: Sequence[CalendarEvent], icon_resolver: IconResolver
) -> Mapping[str, str | None]:
    """Batch-resolve the icons for ``events`` in a single resolver call.

    One call per panel — after the row cap, never for dropped events — so
    missing images can generate concurrently (§7.2) and no generation is
    wasted on events that won't render.
    """
    return icon_resolver([icon_item(event) for event in events])


# Per-kid badge color by config position (kid 0, kid 1). Red and blue are the
# panel's two strongest, most separable ink hues (§5.4); solid ink is fine for
# the shield fill (§5.3). A curated design choice, not deployment config —
# promotable to a Settings field later if more kids or custom colors are ever
# needed.
KID_COLORS = ["#ff0000", "#0000ff"]


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


@dataclass(frozen=True)
class EventRow:
    """One event row: icon + kid badge(s) + title (§10/§11)."""

    title: str
    icon_url: str | None
    """``None`` -> the template renders the fallback chip (§7.3)."""

    kids: list[KidBadge]


def rank_key(event: CalendarEvent) -> tuple:
    """Selection rank (§4.1): ``interesting`` desc, then title, then start.

    The start component makes the order total even for identical
    interesting+title pairs, keeping the cap deterministic (§3.4).
    """
    return (-event.overrides.interesting, event.title, start_key(event))


def start_key(event: CalendarEvent) -> tuple[int, time]:
    """Chronological key: all-day events first (§10.2), then by start time."""
    # The isinstance check narrows start to datetime for the type checker; a
    # bare date start implies all-day anyway (see CalendarEvent).
    if event.all_day or not isinstance(event.start, datetime):
        return (0, time.min)
    return (1, event.start.time())


def display_key(event: CalendarEvent) -> tuple:
    """Display order (§10.2/§11): chronological, all-day first, ties by
    ``interesting`` desc then title."""
    return (*start_key(event), -event.overrides.interesting, event.title)


def build_row(
    event: CalendarEvent, kids: Sequence[Kid], icons: Mapping[str, str | None]
) -> EventRow:
    """One event row, its icon looked up from the batch-resolved ``icons``."""
    return EventRow(
        title=event.title,
        icon_url=icons.get(icon_key(event)),
        kids=kid_badges(event, kids),
    )
