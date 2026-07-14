"""Image admin endpoint (spec §7.4): browse and edit the device's images.

Unauthenticated by design — an accepted trade-off for a trusted home LAN that
must not be exposed beyond it (it can spend OpenAI budget on regeneration).
Plain HTML forms: only panel templates are bound by the no-JS rule (§3.3),
and the detail page carries one small script — a busy overlay while a
regeneration POST is in flight, which also blocks budget-spending double
submits.

Regeneration is a two-step review flow: a POST generates ``<id>.candidate.png``
next to the live image, the detail page then shows old and new side by side,
and a second POST either keeps the candidate (replacing the live PNG) or
discards it. Prompt edits persist immediately on regenerate — a discarded
candidate does not roll the prompt back.

The detail page also manages the record's prompt attachments (§7.1): remove
one, attach an existing reference image, or upload a new one into
``prompt_images/``. Attachment changes apply on the next (re)generation.
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
from werkzeug.utils import secure_filename
from werkzeug.wrappers.response import Response as WerkzeugResponse

from app.config import get_settings
from app.images.attachments import attachment_file_path, normalize_attachment_path
from app.images.db import (
    ImageRecord,
    attach_to_image,
    detach_from_image,
    get_attachment,
    get_or_create_attachment,
    get_record,
    list_all_attachments,
    list_image_attachments,
    list_records,
    open_db,
    update_prompt,
)
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
    conn = open_db(root)
    try:
        attached = list_image_attachments(conn, record.id)
        known = list_all_attachments(conn)
    finally:
        conn.close()
    attached_ids = {attachment.id for attachment in attached}
    return render_template(
        "admin/image_detail.html",
        record=record,
        has_image=image_path(root, record.id).exists(),
        has_candidate=candidate_path(root, record.id).exists(),
        attachments=[
            (attachment, _attachment_file_exists(root, attachment.path))
            for attachment in attached
        ],
        available=[a for a in known if a.id not in attached_ids],
        error=request.args.get("error"),
    )


def _attachment_file_exists(root: Path, path: str) -> bool:
    try:
        return attachment_file_path(root, path).exists()
    except ValueError:  # a malformed DB path renders as missing, never a 500
        return False


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


@admin_bp.post("/admin/images/<int:image_id>/attachments/detach")
def admin_attachment_detach(image_id: int) -> WerkzeugResponse:
    """Unlink one attachment from the record (§7.4).

    Junction row only: the attachment row and its file survive, since either
    may be shared with other records.
    """
    _load_record(image_id)
    attachment_id = request.form.get("attachment_id", type=int)
    if attachment_id is None:
        return _back_to(image_id, error="no attachment selected")
    conn = open_db(_storage_root())
    try:
        detach_from_image(conn, image_id, attachment_id)
    finally:
        conn.close()
    return _back_to(image_id)


@admin_bp.post("/admin/images/<int:image_id>/attachments/attach")
def admin_attachment_attach(image_id: int) -> WerkzeugResponse:
    """Link an existing reference image to the record (§7.4)."""
    _load_record(image_id)
    attachment_id = request.form.get("attachment_id", type=int)
    if attachment_id is None:
        return _back_to(image_id, error="no attachment selected")
    conn = open_db(_storage_root())
    try:
        if get_attachment(conn, attachment_id) is None:
            return _back_to(image_id, error="unknown attachment")
        attach_to_image(conn, image_id, attachment_id)
    finally:
        conn.close()
    return _back_to(image_id)


# Accepted reference-image formats (what the image API's edits endpoint
# takes, §7.2): PIL format name -> allowed path suffixes. The stored suffix
# must match the actual content, since serving guesses the mimetype from it.
_REFERENCE_FORMATS = {
    "PNG": (".png",),
    "JPEG": (".jpg", ".jpeg"),
    "WEBP": (".webp",),
}


@admin_bp.post("/admin/images/<int:image_id>/attachments/upload")
def admin_attachment_upload(image_id: int) -> WerkzeugResponse:
    """Upload a new reference image into ``prompt_images/`` and attach it (§7.4).

    Accepts PNG, JPEG, or WebP (the formats the image API takes as reference
    inputs, §7.2). The optional ``path`` form field names the destination
    relative to ``prompt_images/`` (e.g. ``styles/comic-dog.png``); it
    defaults to the upload's own filename.

    An existing destination with **different** content is refused — reference
    images can be shared across records, so silently replacing one from a
    single record's page could change other images' generations. Identical
    content simply (re)attaches, which makes retries and double submits
    idempotent and adopts a hand-dropped file into the database. The row and
    junction link are written **before** the file: a failure between the two
    leaves a row whose file is missing — visible on this page, skipped at
    generation (§7.7), and cleanly retryable — never an orphaned file that
    would block later uploads.
    """
    _load_record(image_id)
    upload = request.files.get("image")
    if upload is None or not upload.filename:
        return _back_to(image_id, error="no file uploaded")

    data = upload.read()
    try:
        with Image.open(io.BytesIO(data)) as probe:
            image_format = probe.format
    except UnidentifiedImageError:
        return _back_to(image_id, error="upload is not a valid image")
    suffixes = _REFERENCE_FORMATS.get(image_format or "")
    if suffixes is None:
        return _back_to(image_id, error="upload must be a PNG, JPEG, or WebP")

    raw_path = request.form.get("path", "").strip() or secure_filename(upload.filename)
    try:
        path = normalize_attachment_path(raw_path)
    except ValueError:
        return _back_to(image_id, error="invalid attachment path")
    if not path.lower().endswith(suffixes):
        return _back_to(
            image_id,
            error=f"attachment path must end in {' or '.join(suffixes)}"
            f" for a {image_format} upload",
        )

    destination = attachment_file_path(_storage_root(), path)
    file_exists = destination.exists()
    if file_exists and destination.read_bytes() != data:
        return _back_to(image_id, error="a different file already exists at that path")

    conn = open_db(_storage_root())
    try:
        attachment = get_or_create_attachment(conn, path)
        attach_to_image(conn, image_id, attachment.id)
    finally:
        conn.close()

    if not file_exists:
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
