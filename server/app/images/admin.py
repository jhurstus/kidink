"""Image admin endpoint (spec §7.4): browse and edit the device's images.

Unauthenticated by design — an accepted trade-off for a trusted home LAN that
must not be exposed beyond it (it can spend OpenAI budget on regeneration).
Plain HTML forms, no JavaScript: only panel templates are bound by the no-JS
rule (§3.3), but forms are the simplest thing that works here too.

Regeneration is a two-step review flow: a POST generates ``<id>.candidate.png``
next to the live image, the detail page then shows old and new side by side,
and a second POST either keeps the candidate (replacing the live PNG) or
discards it. Prompt edits persist immediately on regenerate — a discarded
candidate does not roll the prompt back.
"""

import io
import os
from pathlib import Path

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from PIL import Image, UnidentifiedImageError
from werkzeug.wrappers.response import Response as WerkzeugResponse

from app.config import get_settings
from app.images.db import ImageRecord, get_record, list_records, open_db, update_prompt
from app.images.generate import DEFAULT_IMAGE_MODEL, ImageGenerationError
from app.images.keying import KeyingError
from app.images.store import candidate_path, image_path, regenerate_candidate

admin_bp = Blueprint("images_admin", __name__)


def _storage_root() -> Path:
    return Path(current_app.config["APP_STORAGE_PATH"])


def _load_record(image_id: int) -> ImageRecord:
    conn = open_db(_storage_root())
    try:
        record = get_record(conn, image_id)
    finally:
        conn.close()
    if record is None:
        abort(404)
    return record


@admin_bp.get("/admin/images")
def admin_images() -> str:
    """List every image record, or show one in isolation via ``?img=<id>``."""
    image_id = request.args.get("img", type=int)
    if image_id is None:
        conn = open_db(_storage_root())
        try:
            records = list_records(conn)
        finally:
            conn.close()
        return render_template("admin/images_list.html", records=records)

    record = _load_record(image_id)
    root = _storage_root()
    return render_template(
        "admin/image_detail.html",
        record=record,
        has_image=image_path(root, record.id).exists(),
        has_candidate=candidate_path(root, record.id).exists(),
        error=request.args.get("error"),
    )


@admin_bp.get("/admin/images/<int:image_id>/candidate.png")
def admin_candidate_image(image_id: int) -> Response:
    """Serve the pending regeneration candidate for side-by-side review."""
    path = candidate_path(_storage_root(), image_id)
    if not path.exists():
        abort(404)
    return send_file(path, mimetype="image/png", max_age=0)


@admin_bp.post("/admin/images/<int:image_id>/regenerate")
def admin_regenerate(image_id: int) -> WerkzeugResponse:
    """Persist the edited prompt, then generate a review candidate (§7.4)."""
    record = _load_record(image_id)
    root = _storage_root()

    prompt = request.form.get("prompt", "").strip()
    if not prompt:
        return _back_to(image_id, error="prompt must not be empty")
    if prompt != record.prompt:
        conn = open_db(root)
        try:
            update_prompt(conn, image_id, prompt)
            record = get_record(conn, image_id)
        finally:
            conn.close()
        assert record is not None

    settings = get_settings()
    model = settings.module_model_tiers.get(record.spec.module, DEFAULT_IMAGE_MODEL)
    try:
        regenerate_candidate(
            record,
            storage_root=root,
            generate=current_app.config["GENERATE_IMAGE_BYTES"],
            api_key=settings.openai_api_key,
            model=model,
        )
    except (ImageGenerationError, KeyingError, ValueError, OSError) as exc:
        return _back_to(image_id, error=f"regeneration failed: {type(exc).__name__}")
    return _back_to(image_id)


@admin_bp.post("/admin/images/<int:image_id>/candidate")
def admin_candidate_decision(image_id: int) -> WerkzeugResponse:
    """Keep (replace the live PNG) or discard the pending candidate."""
    _load_record(image_id)
    root = _storage_root()
    candidate = candidate_path(root, image_id)
    if not candidate.exists():
        return _back_to(image_id, error="no candidate to review")

    if request.form.get("action") == "keep":
        os.replace(candidate, image_path(root, image_id))
    else:
        candidate.unlink()
    return _back_to(image_id)


@admin_bp.post("/admin/images/<int:image_id>/upload")
def admin_upload(image_id: int) -> WerkzeugResponse:
    """Replace ``<id>.png`` outright with a handcrafted upload — no generation."""
    _load_record(image_id)
    upload = request.files.get("image")
    if upload is None or not upload.filename:
        return _back_to(image_id, error="no file uploaded")

    data = upload.read()
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format != "PNG":
                return _back_to(image_id, error="upload must be a PNG")
    except UnidentifiedImageError:
        return _back_to(image_id, error="upload is not a valid image")

    destination = image_path(_storage_root(), image_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(".tmp")
    temp.write_bytes(data)
    os.replace(temp, destination)
    return _back_to(image_id)


def _back_to(image_id: int, error: str | None = None) -> WerkzeugResponse:
    """Redirect back to the record's detail view, optionally with an error note."""
    return redirect(
        url_for("images_admin.admin_images", img=image_id, error=error or None)
    )
