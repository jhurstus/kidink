"""View model for the Countdown panel (spec §12).

Picks the next upcoming ``countdown_eligible`` event, counts the "sleeps"
until it, and derives the escalation tier that drives the template's visual
treatment (plain border → burst border → the full hype kit). Everything is a
pure function of the target date and the expanded events (§3.4): the border
seed and the SFX word picks derive from the date ordinal, never from the wall
clock or unseeded randomness.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.calendar import CalendarEvent
from app.event_rows import IconItem, icon_item, start_key

# The excited tier begins this many sleeps out. Hardcoded like the other
# cutoffs (peak 0, hype 1) per the tier-config decision; a future
# ``Settings.countdown_tiers`` field (§18) would replace these.
_EXCITED_AT = 5

# SFX words shown at the peak tier, ordered off the date ordinal (§3.4).
_SFX_WORDS = ("Yass!", "Woot!")


class Tier(StrEnum):
    """Escalation tier (§12); values double as CSS class suffixes."""

    CALM = "calm"
    EXCITED = "excited"
    HYPE = "hype"
    PEAK = "peak"


# Structural stand-in for the countdown hero resolver (like
# app.event_rows.IconResolver, kept a plain Callable so this view model needs
# no images import): (icon item, excited) -> hero URL or None.
type HeroResolver = Callable[[IconItem, bool], str | None]


def no_hero(item: IconItem, excited: bool) -> None:
    """Default resolver: no hero — keeps the builder pure by default."""
    return None


@dataclass(frozen=True)
class CountdownPanel:
    """The complete view model rendered by ``templates/modules/countdown.html``."""

    seed: int
    """Border seed for the panel (date-pure, §3.4)."""

    title: str | None
    """The target event's title; ``None`` renders the blank card (§12)."""

    hero_url: str | None
    """Servable hero URL; ``None`` omits the image, text remains (§7.3)."""

    sleeps: int
    """Whole calendar nights until the event (0 on the event day)."""

    tier: Tier

    copy: str
    """The sleeps line ("N sleeps to go" / "Just 1 more sleep!!" / "It's today!")."""

    sfx: tuple[str, ...]
    """SFX words; both (date-ordered) at peak, empty at every other tier."""


def tier_for(sleeps: int, *, excited_at: int = _EXCITED_AT, hype_at: int = 1) -> Tier:
    """The escalation tier for a sleeps value (§12).

    Cutoffs are deliberately hardcoded (peak at 0, hype at 1, excited within
    ``excited_at``); the keyword seams are where a future
    ``Settings.countdown_tiers`` field (§18) would plug in.
    """
    if sleeps <= 0:
        return Tier.PEAK
    if sleeps <= hype_at:
        return Tier.HYPE
    if sleeps <= excited_at:
        return Tier.EXCITED
    return Tier.CALM


def build_countdown(
    target: date,
    events: Iterable[CalendarEvent] = (),
    hero_resolver: HeroResolver = no_hero,
    sleeps_override: int | None = None,
) -> CountdownPanel:
    """Build the Countdown panel view model for the resolved render date.

    ``events`` are the render window's expanded calendar events, whose horizon
    extends ``COUNTDOWN_HORIZON_DAYS`` past ``target`` (see
    :func:`app.dates.render_days`). The target event is the upcoming
    ``countdown_eligible`` one by soonest day, then highest ``interesting``,
    then earliest start, then title (§12); ``local_day >= target`` keeps the
    event-day "It's today!" state and rolls to the next eligible event the day
    after. The hero resolves through ``hero_resolver`` — never called for the
    blank state, and asked for the excited edit variant only at hype/peak (the
    default resolves nothing, keeping the view model pure).

    ``sleeps_override`` is the ``?countdown_sleeps=`` debug arg (§3.5): it
    replaces the computed sleeps - and everything derived from it (tier, copy,
    SFX, the hero's excited variant) - for previewing any tier against the
    real target event. The blank no-event card ignores it: with no event there
    is nothing to count down to.
    """
    # Tomorrow's border seed is target.toordinal()+4; +5 keeps this panel's
    # ripple distinct on the page while staying date-pure (§3.4).
    seed = target.toordinal() + 5
    candidates = [
        e
        for e in events
        if e.overrides.countdown_eligible and not e.is_chore and e.local_day >= target
    ]
    if not candidates:
        # Error/misconfig state (§12): a blank card preserving the footprint.
        return CountdownPanel(
            seed=seed,
            title=None,
            hero_url=None,
            sleeps=0,
            tier=Tier.CALM,
            copy="",
            sfx=(),
        )

    event = min(
        candidates,
        key=lambda e: (e.local_day, -e.overrides.interesting, start_key(e), e.title),
    )
    # Both days are already in the configured timezone (resolve_date /
    # expand_events), so the difference is exactly "whole calendar nights".
    sleeps = (event.local_day - target).days
    if sleeps_override is not None:
        sleeps = sleeps_override
    tier = tier_for(sleeps)

    if tier is Tier.PEAK:
        copy = "It's today!"
    elif tier is Tier.HYPE:
        copy = "Just 1 more sleep!!"
    elif tier is Tier.EXCITED:
        copy = f"{sleeps} sleeps to go!"  # always >= 2 here, so always plural
    else:
        copy = f"{sleeps} sleeps to go"  # calm: no exclamation yet

    sfx_index = target.toordinal() % len(_SFX_WORDS)
    if tier is Tier.PEAK:
        sfx = (_SFX_WORDS[sfx_index], _SFX_WORDS[(sfx_index + 1) % len(_SFX_WORDS)])
    else:
        sfx = ()

    return CountdownPanel(
        seed=seed,
        title=event.title,
        hero_url=hero_resolver(icon_item(event), tier in (Tier.HYPE, Tier.PEAK)),
        sleeps=sleeps,
        tier=tier,
        copy=copy,
        sfx=sfx,
    )
