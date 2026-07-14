"""Serving routes for generated images and prompt-attachment images (§7.6)."""

from pathlib import Path

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    send_file,
    send_from_directory,
)

from app.images.attachments import prompt_images_root
from app.images.store import image_path

images_bp = Blueprint("images", __name__)


@images_bp.get("/images/generated/<int:image_id>")
def generated_image(image_id: int) -> Response:
    """Serve ``gen_images/<id>.png``; 404 when the file doesn't exist."""
    path = image_path(Path(current_app.config["APP_STORAGE_PATH"]), image_id)
    if not path.exists():
        abort(404)
    return send_file(path, mimetype="image/png")


@images_bp.get("/images/prompt/<path:rel_path>")
def prompt_image(rel_path: str) -> Response:
    """Serve a prompt-attachment image from ``prompt_images/`` by relative path.

    ``send_from_directory`` (``safe_join`` underneath) turns both missing
    files and path-traversal attempts into 404s. ``max_age=0`` because the
    admin UI re-reads these after uploads.
    """
    root = prompt_images_root(Path(current_app.config["APP_STORAGE_PATH"]))
    return send_from_directory(root, rel_path, max_age=0)
