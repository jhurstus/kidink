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


def _write_prompt_image(tmp_path: Path, rel_path: str, data: bytes) -> None:
    file = tmp_path / "prompt_images" / rel_path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(data)


def test_prompt_image_served_with_subdirectories(tmp_path: Path) -> None:
    _write_prompt_image(tmp_path, "styles/comic-dog.png", b"dog-bytes")

    response = _app(tmp_path).test_client().get("/images/prompt/styles/comic-dog.png")
    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert response.data == b"dog-bytes"


def test_missing_prompt_image_404s(tmp_path: Path) -> None:
    response = _app(tmp_path).test_client().get("/images/prompt/styles/ghost.png")
    assert response.status_code == 404


def test_prompt_image_traversal_is_rejected(tmp_path: Path) -> None:
    # A file *outside* prompt_images/ must be unreachable. The test client
    # normalizes literal "..", so send the encoded form; safe_join inside
    # send_from_directory rejects it after decoding.
    (tmp_path / "secret.txt").write_bytes(b"secret")

    response = _app(tmp_path).test_client().get("/images/prompt/%2e%2e/secret.txt")
    assert response.status_code == 404
