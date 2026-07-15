"""Jokes admin endpoint: the curated joke list with CRUD, images, seed import.

``GET /admin/jokes`` lists every joke (the live source of truth, §15). Each row
can have its text edited or be deleted, and links to its hero image's §7.4 admin
page - with a generate action for jokes whose image record doesn't exist yet
(records are normally created at render/warm-up time when the daily index
reaches that joke). A textarea bulk-adds more jokes (one per line).

Unauthenticated by design, like the image admin (§7.4) - an accepted trade-off
for a trusted home LAN that must not be exposed beyond it (the generate action
can spend OpenAI budget). Plain HTML forms with redirect-after-post; only panel
templates are bound by the no-JS rule (§3.3).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.wrappers.response import Response as WerkzeugResponse

from app.config import get_settings
from app.dates import resolve_date
from app.images import ensure_image
from app.images.db import find_record
from app.images.generate import DEFAULT_IMAGE_MODEL
from app.joke.hero import JOKE_MODULE, joke_hero_prompt, joke_image_spec
from app.joke.jokes import (
    add_jokes,
    delete_joke,
    get_joke,
    list_jokes,
    open_jokes_db,
    update_joke,
)

joke_admin_bp = Blueprint("joke_admin", __name__)


@dataclass(frozen=True)
class JokeRow:
    """One row of the jokes table (``templates/admin/jokes.html``)."""

    id: int
    text: str
    image_id: int | None
    """The joke's hero record id, or ``None`` before its first generation (the
    row then offers the generate action instead of a link)."""

    is_today: bool
    """Whether this joke is the one the board shows for the current date (§15)."""


def _storage_root() -> Path:
    return Path(current_app.config["APP_STORAGE_PATH"])


@joke_admin_bp.get("/admin/jokes")
def admin_jokes() -> str:
    """The joke list: per-row edit/delete/image, plus add and seed-import."""
    settings = get_settings()
    now = current_app.config.get("NOW") or datetime.now(UTC)
    today = resolve_date(None, now=now, tz=settings.timezone)

    conn = open_jokes_db(_storage_root())
    try:
        jokes = list_jokes(conn)
        # The date's joke index (§15); -1 when the store is empty so no row
        # is flagged. Same modulo the render path uses (build_joke).
        today_index = (
            (today - settings.joke_start_date).days % len(jokes) if jokes else -1
        )
        rows = [
            JokeRow(
                id=joke.id,
                text=joke.text,
                image_id=(
                    record.id
                    if (record := find_record(conn, joke_image_spec(joke.text)))
                    else None
                ),
                is_today=(i == today_index),
            )
            for i, joke in enumerate(jokes)
        ]
    finally:
        conn.close()
    return render_template(
        "admin/jokes.html",
        rows=rows,
        error=request.args.get("error"),
        status=request.args.get("status"),
    )


@joke_admin_bp.post("/admin/jokes/add")
def admin_jokes_add() -> WerkzeugResponse:
    """Bulk-add jokes from the textarea (one per line; blank/# lines skipped)."""
    lines = request.form.get("jokes", "").splitlines()
    conn = open_jokes_db(_storage_root())
    try:
        added = add_jokes(conn, lines)
    finally:
        conn.close()
    return redirect(url_for("joke_admin.admin_jokes", status=f"added {added} joke(s)"))


@joke_admin_bp.post("/admin/jokes/<int:joke_id>/edit")
def admin_joke_edit(joke_id: int) -> WerkzeugResponse:
    """Replace one joke's text (an empty save deletes the joke)."""
    text = request.form.get("text", "").strip()
    conn = open_jokes_db(_storage_root())
    try:
        if text:
            update_joke(conn, joke_id, text)
        else:
            delete_joke(conn, joke_id)
    finally:
        conn.close()
    return redirect(url_for("joke_admin.admin_jokes"))


@joke_admin_bp.post("/admin/jokes/<int:joke_id>/delete")
def admin_joke_delete(joke_id: int) -> WerkzeugResponse:
    """Remove one joke."""
    conn = open_jokes_db(_storage_root())
    try:
        delete_joke(conn, joke_id)
    finally:
        conn.close()
    return redirect(url_for("joke_admin.admin_jokes"))


@joke_admin_bp.post("/admin/jokes/<int:joke_id>/generate")
def admin_joke_generate(joke_id: int) -> WerkzeugResponse:
    """Generate the joke's hero image now and jump to its §7.4 admin page.

    The joke text is read server-side from the row (not trusted from the form),
    so a stale page can't generate under an outdated line. Reuses the render
    path's ``ensure_image``, so an already-warm joke just redirects without
    spending a generation.
    """
    conn = open_jokes_db(_storage_root())
    try:
        joke = get_joke(conn, joke_id)
    finally:
        conn.close()
    if joke is None:
        return redirect(url_for("joke_admin.admin_jokes", error="no such joke"))

    settings = get_settings()
    image_id = ensure_image(
        joke_image_spec(joke.text),
        joke_hero_prompt(joke.text),
        storage_root=_storage_root(),
        generate=current_app.config["GENERATE_IMAGE_BYTES"],
        api_key=settings.openai_api_key,
        model=settings.module_model_tiers.get(JOKE_MODULE, DEFAULT_IMAGE_MODEL),
    )
    if image_id is None:
        return redirect(
            url_for(
                "joke_admin.admin_jokes",
                error="generation failed: see the app log and gen_failures.log",
            )
        )
    return redirect(url_for("images_admin.admin_images", img=image_id))
