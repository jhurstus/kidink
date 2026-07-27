"""View model for the Joke panel (spec §15).

One joke/riddle per day, selected deterministically from the curated list by
day offset: ``index = (target - joke_start_date).days % N`` (§15), so the list
loops and a given date always shows the same joke (§3.4 - the ``?date=`` debug
arg is what picks a different one; nothing reads the wall clock). The whole
panel is a wholly AI-generated comic image with the joke text drawn inside it
(:mod:`app.joke.hero`); on a generation miss the template falls back to the
joke line as HTML text in a single bubble (§7.3).
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

# Structural stand-in for the joke hero resolver (kept a plain Callable so this
# view model needs no images import): (joke text) -> hero URL or None.
type HeroResolver = Callable[[str], str | None]


def no_hero(text: str) -> None:
    """Default resolver: no hero - keeps the builder pure by default."""
    return


@dataclass(frozen=True)
class JokePanel:
    """The complete view model rendered by ``templates/modules/joke.html``."""

    seed: int
    """Border seed for the panel (date-pure, §3.4)."""

    text: str | None
    """The day's joke line; ``None`` when the store is empty (§15)."""

    hero_url: str | None
    """Servable hero URL; ``None`` falls back to the HTML text bubble (§7.3)."""


def build_joke(
    target: date,
    jokes: Sequence[str],
    start_date: date,
    hero_resolver: HeroResolver = no_hero,
) -> JokePanel:
    """Build the Joke panel view model for the resolved render date.

    ``jokes`` is the curated list in order (:func:`app.joke.jokes.stored_jokes`);
    the day's joke is ``jokes[(target - start_date).days % len(jokes)]`` (§15).
    An empty list yields a text-less panel (the template shows a friendly
    placeholder). The hero resolves through ``hero_resolver`` (the default
    resolves nothing, keeping the view model pure).
    """
    # Countdown's border seed is toordinal()+5 and dinner's +6; +8 is the joke's
    # reserved slot (see app/dinner/view.py) - distinct on the page, date-pure.
    seed = target.toordinal() + 8
    if not jokes:
        return JokePanel(seed=seed, text=None, hero_url=None)
    text = jokes[(target - start_date).days % len(jokes)]
    return JokePanel(seed=seed, text=text, hero_url=hero_resolver(text))
