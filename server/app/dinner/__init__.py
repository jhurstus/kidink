"""Dinner module (spec §13): tonight's meal from the Anylist meal plan.

Public API:

- :func:`build_dinner` / :class:`DinnerPanel` - the pure view model rendered
  by ``templates/modules/dinner.html``.
- :func:`make_dinner_hero_resolver` / :data:`HeroResolver` /
  :func:`dinner_image_spec` - the hero-image unit on the §7 pipeline (keyed
  transparent PNG, logical key = the combined meal name).
- :func:`stored_override` - the per-date meal-name override read by the
  render route (managed on ``/admin/meals``, :mod:`app.dinner.admin`).
- :data:`dinner_admin_bp` - the ``/admin/meals`` blueprint.
"""

from app.dinner.admin import dinner_admin_bp
from app.dinner.hero import dinner_image_spec, make_dinner_hero_resolver
from app.dinner.overrides import stored_override
from app.dinner.view import (
    DinnerPanel,
    HeroResolver,
    build_dinner,
    joined_meal_name,
    no_hero,
)

__all__ = [
    "DinnerPanel",
    "HeroResolver",
    "build_dinner",
    "dinner_admin_bp",
    "dinner_image_spec",
    "joined_meal_name",
    "make_dinner_hero_resolver",
    "no_hero",
    "stored_override",
]
