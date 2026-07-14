"""Meals admin endpoint: the upcoming dinners, name overrides, image links.

``GET /admin/meals`` lists today plus the next 13 days of the Anylist meal
plan. Each date's name can be overridden (persisted, :mod:`.overrides`) - the
override keeps winning even if the feed's name later changes - and each meal
links to its hero image's §7.4 admin page, with a generate action for meals
whose image record doesn't exist yet (records are normally created at
render/warm-up time, so future dates usually have none).

Unauthenticated by design, like the image admin (§7.4) - an accepted
trade-off for a trusted home LAN that must not be exposed beyond it (the
generate action can spend OpenAI budget). Plain HTML forms with
redirect-after-post; only panel templates are bound by the no-JS rule (§3.3).
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.wrappers.response import Response as WerkzeugResponse

from app.calendar import CalendarFetchError, expand_events
from app.config import get_settings
from app.dates import resolve_date
from app.dinner.hero import DINNER_MODULE, dinner_hero_prompt, dinner_image_spec
from app.dinner.overrides import (
    clear_override,
    get_override,
    open_meals_db,
    overrides_for,
    set_override,
)
from app.dinner.view import joined_meal_name
from app.images import ensure_image
from app.images.db import find_record
from app.images.generate import DEFAULT_IMAGE_MODEL

dinner_admin_bp = Blueprint("dinner_admin", __name__)

# Today plus 13 days out - two full weeks of meal planning.
MEAL_ADMIN_DAYS = 14


@dataclass(frozen=True)
class MealRow:
    """One date of the meals table (``templates/admin/meals.html``)."""

    day: date
    ics_name: str | None
    """The feed's combined meal name; ``None`` when the day has no entries."""

    override: str | None
    effective: str | None
    """``override or ics_name`` - what the board will show (§13)."""

    image_id: int | None
    """The effective meal's hero record id, or ``None`` before its first
    generation (the row then offers the generate action instead of a link)."""


def _storage_root() -> Path:
    return Path(current_app.config["APP_STORAGE_PATH"])


def _parse_day(day: str) -> date:
    try:
        return date.fromisoformat(day)
    except ValueError:
        abort(400, description="invalid meal date")


def _feed_names(days: list[date]) -> tuple[dict[date, str | None], str | None]:
    """Each day's combined feed name, plus a fetch-error note.

    Mirrors the render route's soft failure (§13): on a fetch/parse error the
    page must still serve - overrides remain editable while Anylist is down -
    so this logs (type name only, the message could echo the secret URL) and
    returns nameless days plus the note for the error banner.
    """
    settings = get_settings()
    try:
        ics_text = current_app.config["FETCH_MEALPLAN_ICS"](
            settings.anylist_mealplan_ics_url
        )
        events = expand_events(ics_text, days, settings.timezone)
    except (CalendarFetchError, ValueError) as exc:
        current_app.logger.warning("meal plan fetch failed: %s", type(exc).__name__)
        return dict.fromkeys(days), f"meal plan fetch failed: {type(exc).__name__}"
    return {day: joined_meal_name(events, day) for day in days}, None


@dinner_admin_bp.get("/admin/meals")
def admin_meals() -> str:
    """The two-week meal table: feed name, override form, image link."""
    now = current_app.config.get("NOW") or datetime.now(UTC)
    today = resolve_date(None, now=now, tz=get_settings().timezone)
    days = [today + timedelta(days=i) for i in range(MEAL_ADMIN_DAYS)]

    ics_names, fetch_error = _feed_names(days)
    conn = open_meals_db(_storage_root())
    try:
        overrides = overrides_for(conn, days)
        rows = []
        for day in days:
            override = overrides.get(day)
            effective = override or ics_names[day]
            record = (
                find_record(conn, dinner_image_spec(effective)) if effective else None
            )
            rows.append(
                MealRow(
                    day=day,
                    ics_name=ics_names[day],
                    override=override,
                    effective=effective,
                    image_id=record.id if record is not None else None,
                )
            )
    finally:
        conn.close()
    return render_template(
        "admin/meals.html",
        rows=rows,
        today=today,
        error=request.args.get("error") or fetch_error,
    )


@dinner_admin_bp.post("/admin/meals/<day>/override")
def admin_meal_override(day: str) -> WerkzeugResponse:
    """Set the date's name override; an empty (or blank) name clears it."""
    target = _parse_day(day)
    name = request.form.get("name", "").strip()
    conn = open_meals_db(_storage_root())
    try:
        if name:
            set_override(conn, target, name)
        else:
            clear_override(conn, target)
    finally:
        conn.close()
    return redirect(url_for("dinner_admin.admin_meals"))


@dinner_admin_bp.post("/admin/meals/<day>/generate")
def admin_meal_generate(day: str) -> WerkzeugResponse:
    """Generate the date's hero image now and jump to its §7.4 admin page.

    The effective name is recomputed server-side (fresh fetch + override)
    rather than trusted from the form, so a stale page can't generate under
    an outdated name. Reuses the render path's ``ensure_image``, so an
    already-warm meal just redirects without spending a generation.
    """
    target = _parse_day(day)
    names, fetch_error = _feed_names([target])
    conn = open_meals_db(_storage_root())
    try:
        override = get_override(conn, target)
    finally:
        conn.close()
    effective = override or names[target]
    if effective is None:
        error = fetch_error or "no meal to generate an image for"
        return redirect(url_for("dinner_admin.admin_meals", error=error))

    settings = get_settings()
    image_id = ensure_image(
        dinner_image_spec(effective),
        dinner_hero_prompt(effective),
        storage_root=_storage_root(),
        generate=current_app.config["GENERATE_IMAGE_BYTES"],
        api_key=settings.openai_api_key,
        model=settings.module_model_tiers.get(DINNER_MODULE, DEFAULT_IMAGE_MODEL),
    )
    if image_id is None:
        return redirect(
            url_for(
                "dinner_admin.admin_meals",
                error="generation failed: see the app log and gen_failures.log",
            )
        )
    return redirect(url_for("images_admin.admin_images", img=image_id))
