"""View model for the Dinner panel (spec §13).

Every meal-plan entry on the target date IS the dinner (no meal-slot
filtering): a main plus any sides are joined into one combined name, which is
also the hero image's logical key (§7.1). A per-date admin override (see
:mod:`app.dinner.overrides`) replaces that name wholesale - it keeps winning
even if the feed's name later changes, and it supplies a name when the feed
has none. No name at all renders the "Mystery dinner!" card, which the render
route also falls back to when the feed fetch fails (§13).
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date

from app.calendar import CalendarEvent

# Structural stand-in for the dinner hero resolver (kept a plain Callable so
# this view model needs no images import): (meal name) -> hero URL or None.
type HeroResolver = Callable[[str], str | None]


def no_hero(name: str) -> None:
    """Default resolver: no hero - keeps the builder pure by default."""
    return None


@dataclass(frozen=True)
class DinnerPanel:
    """The complete view model rendered by ``templates/modules/dinner.html``."""

    seed: int
    """Border seed for the panel (date-pure, §3.4)."""

    name: str | None
    """The effective menu name; ``None`` renders the mystery card (§13)."""

    hero_url: str | None
    """Servable hero URL; ``None`` omits the image, text remains (§7.3)."""


def joined_meal_name(events: Iterable[CalendarEvent], day: date) -> str | None:
    """Join ``day``'s meal names into the one combined dinner name (§13).

    Titles are lowercased (Anylist recipe names arrive in inconsistent title
    case; the board and the image prompt want one uniform casing - an admin
    override, being hand-typed, is used verbatim) and join in feed order with
    ``" & "``; blank titles are skipped. Returns ``None`` when the day has no
    (non-blank) meal entries. The joined string doubles as the hero image's
    logical key, so it must stay a pure function of the feed content (§3.4,
    §7.1).
    """
    names = [e.title.lower() for e in events if e.local_day == day and e.title.strip()]
    return " & ".join(names) if names else None


def build_dinner(
    target: date,
    events: Iterable[CalendarEvent] = (),
    override: str | None = None,
    hero_resolver: HeroResolver = no_hero,
) -> DinnerPanel:
    """Build the Dinner panel view model for the resolved render date.

    ``events`` are the expanded meal-plan entries covering ``target``; every
    one of them is part of the dinner - no ``is_chore``/``all_day`` filtering
    (§13). ``override`` is the persisted per-date name override, which beats
    the feed name (and a feedless day) outright. The hero resolves through
    ``hero_resolver`` - never called for the mystery state, so a nameless day
    generates nothing (the default resolves nothing, keeping the view model
    pure).
    """
    # Countdown's border seed is target.toordinal()+5; +6 keeps this panel's
    # ripple distinct on the page while staying date-pure (§3.4). (The chore
    # and joke placeholders currently hardcode literal seeds 5 and 6; +7/+8
    # are reserved for them.)
    seed = target.toordinal() + 6
    name = override or joined_meal_name(events, target)
    if name is None:
        return DinnerPanel(seed=seed, name=None, hero_url=None)
    return DinnerPanel(seed=seed, name=name, hero_url=hero_resolver(name))
