"""Countdown module (spec §12): a counter to the next exciting event.

Public API:

- :func:`build_countdown` / :class:`CountdownPanel` / :class:`Tier` — the pure
  view model rendered by ``templates/modules/countdown.html``.
- :func:`make_countdown_hero_resolver` / :data:`HeroResolver` — the hero-image
  unit on the §7 pipeline (base hero + the hype/peak "excited" edit variant).
"""

from app.countdown.hero import make_countdown_hero_resolver
from app.countdown.view import (
    CountdownPanel,
    HeroResolver,
    Tier,
    build_countdown,
    no_hero,
)

__all__ = [
    "CountdownPanel",
    "HeroResolver",
    "Tier",
    "build_countdown",
    "make_countdown_hero_resolver",
    "no_hero",
]
