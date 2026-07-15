"""Joke module (spec §15): one AI-generated joke/riddle panel per day.

Public API:

- :func:`build_joke` / :class:`JokePanel` - the pure view model rendered by
  ``templates/modules/joke.html`` (the day's joke picked by the §15 modulo
  index).
- :func:`make_joke_hero_resolver` / :data:`HeroResolver` /
  :func:`joke_image_spec` - the hero-image unit on the §7 pipeline (unkeyed,
  displayed as-is; logical key = the joke line, with its text drawn inside).
- :func:`stored_jokes` - the curated joke list read by the render route
  (managed on ``/admin/jokes``, :mod:`app.joke.admin`).
- :data:`joke_admin_bp` - the ``/admin/jokes`` blueprint.
"""

from app.joke.admin import joke_admin_bp
from app.joke.hero import joke_hero_prompt, joke_image_spec, make_joke_hero_resolver
from app.joke.jokes import stored_jokes
from app.joke.view import HeroResolver, JokePanel, build_joke, no_hero

__all__ = [
    "HeroResolver",
    "JokePanel",
    "build_joke",
    "joke_admin_bp",
    "joke_hero_prompt",
    "joke_image_spec",
    "make_joke_hero_resolver",
    "no_hero",
    "stored_jokes",
]
