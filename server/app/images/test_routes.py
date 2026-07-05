from pathlib import Path

from flask import Flask

from app import create_app
from app.images.store import image_path


def _app(tmp_path: Path) -> Flask:
    app = create_app()
    app.config["APP_STORAGE_PATH"] = tmp_path
    return app


def test_missing_image_404s(tmp_path: Path) -> None:
    response = _app(tmp_path).test_client().get("/images/generated/7")
    assert response.status_code == 404


def test_existing_image_served_as_png(tmp_path: Path) -> None:
    path = image_path(tmp_path, 7)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"png-bytes")

    response = _app(tmp_path).test_client().get("/images/generated/7")
    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert response.data == b"png-bytes"


def test_non_integer_id_is_rejected(tmp_path: Path) -> None:
    # The route only matches integer ids, so path text never reaches the
    # filesystem (no traversal surface).
    response = _app(tmp_path).test_client().get("/images/generated/../secret")
    assert response.status_code == 404
