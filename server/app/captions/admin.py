"""Captions admin endpoint: the weather kid's speech-bubble lines with CRUD.

``GET /admin/captions`` lists every caption (the §10.5 rotation order). Each
row can have its text edited, be deleted, or be made today's caption (pinning
today to it and continuing the rotation from there), a textarea bulk-adds more (one per
line), and the rotation's latest pin is shown with a reset action (forgetting
every date's pin and the pointer, so the next rendered day restarts at the
first caption).

Unauthenticated by design, like the joke admin - an accepted trade-off for a
trusted home LAN that must not be exposed beyond it. Plain HTML forms with
redirect-after-post; only panel templates are bound by the no-JS rule (§3.3).
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
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

from app.captions.captions import (
    Assignment,
    Caption,
    add_captions,
    clear_rotation,
    delete_caption,
    get_assignment,
    get_last_index,
    latest_assignment,
    list_captions,
    open_captions_db,
    set_today_caption,
    update_caption,
)
from app.config import get_settings
from app.dates import resolve_date

captions_admin_bp = Blueprint("captions_admin", __name__)


@dataclass(frozen=True)
class CaptionRow:
    """One row of the captions table (``templates/admin/captions.html``)."""

    id: int
    text: str

    is_next: bool
    """Whether this caption is the one the next unpinned day will show."""

    is_today: bool
    """Whether today is pinned to this caption (§10.5)."""


@dataclass(frozen=True)
class AssignedState:
    """The latest pin resolved for display: which date, and which text."""

    date: str
    """The most recently pinned date, ISO formatted."""

    text: str | None
    """That caption's current text, or ``None`` when the list shrank past
    the pinned index (the row it pointed at was deleted)."""


def _storage_root() -> Path:
    return Path(current_app.config["APP_STORAGE_PATH"])


def _today() -> date:
    """The board's current date (the same resolution ``/render`` uses)."""
    settings = get_settings()
    now = current_app.config.get("NOW") or datetime.now(UTC)
    return resolve_date(None, now=now, tz=settings.timezone)


@captions_admin_bp.get("/admin/captions")
def admin_captions() -> str:
    """The caption list: per-row edit/delete/show-today, add, rotation state."""
    conn = open_captions_db(_storage_root())
    try:
        captions = list_captions(conn)
        last_index = get_last_index(conn)
        latest = latest_assignment(conn)
        today_pin = get_assignment(conn, _today())
    finally:
        conn.close()
    # The next unpinned day's index: the rotation start with no pointer, else
    # one past it - the same arithmetic select_caption applies.
    next_index = -1
    if captions:
        next_index = 0 if last_index is None else (last_index + 1) % len(captions)
    # Today's pin reads modulo the current length, as select_caption does.
    today_index = (
        today_pin % len(captions) if captions and today_pin is not None else -1
    )
    rows = [
        CaptionRow(
            id=caption.id,
            text=caption.text,
            is_next=(i == next_index),
            is_today=(i == today_index),
        )
        for i, caption in enumerate(captions)
    ]
    return render_template(
        "admin/captions.html",
        rows=rows,
        assigned=_assigned_state(captions, latest),
        error=request.args.get("error"),
        status=request.args.get("status"),
    )


def _assigned_state(
    captions: list[Caption], latest: Assignment | None
) -> AssignedState | None:
    if latest is None:
        return None
    text = (
        captions[latest.caption_index].text
        if latest.caption_index < len(captions)
        else None
    )
    return AssignedState(date=latest.day.isoformat(), text=text)


@captions_admin_bp.post("/admin/captions/add")
def admin_captions_add() -> WerkzeugResponse:
    """Bulk-add captions from the textarea (one per line; blank/# lines skipped)."""
    lines = request.form.get("captions", "").splitlines()
    conn = open_captions_db(_storage_root())
    try:
        added = add_captions(conn, lines)
    finally:
        conn.close()
    return redirect(
        url_for("captions_admin.admin_captions", status=f"added {added} caption(s)")
    )


@captions_admin_bp.post("/admin/captions/<int:caption_id>/edit")
def admin_caption_edit(caption_id: int) -> WerkzeugResponse:
    """Replace one caption's text (an empty save deletes the caption)."""
    text = request.form.get("text", "").strip()
    conn = open_captions_db(_storage_root())
    try:
        if text:
            update_caption(conn, caption_id, text)
        else:
            delete_caption(conn, caption_id)
    finally:
        conn.close()
    return redirect(url_for("captions_admin.admin_captions"))


@captions_admin_bp.post("/admin/captions/<int:caption_id>/delete")
def admin_caption_delete(caption_id: int) -> WerkzeugResponse:
    """Remove one caption."""
    conn = open_captions_db(_storage_root())
    try:
        delete_caption(conn, caption_id)
    finally:
        conn.close()
    return redirect(url_for("captions_admin.admin_captions"))


@captions_admin_bp.post("/admin/captions/<int:caption_id>/today")
def admin_caption_make_today(caption_id: int) -> WerkzeugResponse:
    """Show this caption today; the rotation then continues from it."""
    conn = open_captions_db(_storage_root())
    try:
        ids = [caption.id for caption in list_captions(conn)]
        if caption_id not in ids:
            return redirect(
                url_for("captions_admin.admin_captions", error="no such caption")
            )
        set_today_caption(conn, _today(), ids.index(caption_id))
    finally:
        conn.close()
    return redirect(
        url_for("captions_admin.admin_captions", status="today's caption updated")
    )


@captions_admin_bp.post("/admin/captions/state/reset")
def admin_captions_reset() -> WerkzeugResponse:
    """Forget every pin and the pointer: the next rendered day restarts at #1."""
    conn = open_captions_db(_storage_root())
    try:
        clear_rotation(conn)
    finally:
        conn.close()
    return redirect(url_for("captions_admin.admin_captions", status="rotation reset"))
