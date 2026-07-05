"""Serving route for generated images."""

from pathlib import Path

from flask import Blueprint, Response, abort, current_app, send_file

from app.images.store import image_path

images_bp = Blueprint("images", __name__)


@images_bp.get("/images/generated/<int:image_id>")
def generated_image(image_id: int) -> Response:
    """Serve ``gen_images/<id>.png``; 404 when the file doesn't exist."""
    path = image_path(Path(current_app.config["APP_STORAGE_PATH"]), image_id)
    if not path.exists():
        abort(404)
    return send_file(path, mimetype="image/png")
