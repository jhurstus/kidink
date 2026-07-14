import io
from pathlib import Path

import numpy as np
from flask import Flask
from PIL import Image
from pydantic import SecretStr

from app import create_app
from app.images.db import ImageSpec, get_or_create_record, get_record, open_db
from app.images.generate import ImageGenerationError
from app.images.store import candidate_path, image_path

SPEC = ImageSpec(module="Calendar", item_description="Soccer", width=100, height=60)


def _keyable_png() -> bytes:
    pixels = np.tile(np.array((0, 255, 0), np.uint8), (480, 800, 1))
    pixels[40:440, 40:760] = (220, 40, 40)
    out = io.BytesIO()
    Image.fromarray(pixels, "RGB").save(out, format="PNG")
    return out.getvalue()


def _plain_png() -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", (10, 6), (255, 0, 0, 255)).save(out, format="PNG")
    return out.getvalue()


def _app(tmp_path: Path, generate: object = None) -> Flask:
    app = create_app()
    app.config["APP_STORAGE_PATH"] = tmp_path

    def default_generate(
        api_key: SecretStr,
        *,
        prompt: str,
        size: str,
        model: str,
        base_png: bytes | None = None,
    ) -> bytes:
        return _keyable_png()

    app.config["GENERATE_IMAGE_BYTES"] = generate or default_generate
    return app


def _seed_record(tmp_path: Path, prompt: str = "seed prompt") -> int:
    conn = open_db(tmp_path)
    record = get_or_create_record(conn, SPEC, prompt)
    conn.close()
    path = image_path(tmp_path, record.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_plain_png())
    return record.id


def test_list_view_links_each_record(tmp_path: Path) -> None:
    image_id = _seed_record(tmp_path)
    text = _app(tmp_path).test_client().get("/admin/images").text
    assert "Soccer" in text
    assert f"/admin/images?img={image_id}" in text


def test_detail_view_shows_prompt_and_image(tmp_path: Path) -> None:
    image_id = _seed_record(tmp_path, prompt="the prompt text")
    text = _app(tmp_path).test_client().get(f"/admin/images?img={image_id}").text
    assert "the prompt text" in text
    assert f"/images/generated/{image_id}" in text
    # Previews are constrained to the record's logical display size — the
    # stored PNG itself is native generation resolution.
    assert "max-width: 100px; max-height: 60px" in text


def test_detail_view_unknown_id_404s(tmp_path: Path) -> None:
    assert _app(tmp_path).test_client().get("/admin/images?img=99").status_code == 404


def test_regenerate_persists_prompt_and_writes_candidate(tmp_path: Path) -> None:
    image_id = _seed_record(tmp_path)
    live_before = image_path(tmp_path, image_id).read_bytes()
    prompts: list[str] = []

    def generate(
        api_key: SecretStr,
        *,
        prompt: str,
        size: str,
        model: str,
        base_png: bytes | None = None,
    ) -> bytes:
        prompts.append(prompt)
        return _keyable_png()

    client = _app(tmp_path, generate).test_client()
    response = client.post(
        f"/admin/images/{image_id}/regenerate", data={"prompt": "edited prompt"}
    )

    assert response.status_code == 302
    assert prompts == ["edited prompt"]  # regeneration used the edited prompt
    assert candidate_path(tmp_path, image_id).exists()
    assert image_path(tmp_path, image_id).read_bytes() == live_before  # live untouched

    conn = open_db(tmp_path)
    record = get_record(conn, image_id)
    conn.close()
    assert record is not None and record.prompt == "edited prompt"

    # The detail page now offers the side-by-side review.
    text = client.get(f"/admin/images?img={image_id}").text
    assert "candidate" in text


def test_regenerate_failure_reports_error_without_candidate(tmp_path: Path) -> None:
    image_id = _seed_record(tmp_path)

    def generate(
        api_key: SecretStr,
        *,
        prompt: str,
        size: str,
        model: str,
        base_png: bytes | None = None,
    ) -> bytes:
        raise ImageGenerationError("image generation failed: Boom")

    client = _app(tmp_path, generate).test_client()
    response = client.post(
        f"/admin/images/{image_id}/regenerate",
        data={"prompt": "p"},
        follow_redirects=True,
    )
    assert "regeneration failed" in response.text
    assert not candidate_path(tmp_path, image_id).exists()


def test_keep_candidate_replaces_live_image(tmp_path: Path) -> None:
    image_id = _seed_record(tmp_path)
    candidate_bytes = _plain_png() + b"tail"  # distinguishable from the live file
    candidate_path(tmp_path, image_id).write_bytes(candidate_bytes)

    client = _app(tmp_path).test_client()
    client.post(f"/admin/images/{image_id}/candidate", data={"action": "keep"})

    assert image_path(tmp_path, image_id).read_bytes() == candidate_bytes
    assert not candidate_path(tmp_path, image_id).exists()


def test_discard_candidate_keeps_live_image(tmp_path: Path) -> None:
    image_id = _seed_record(tmp_path)
    live_before = image_path(tmp_path, image_id).read_bytes()
    candidate_path(tmp_path, image_id).write_bytes(b"candidate")

    client = _app(tmp_path).test_client()
    client.post(f"/admin/images/{image_id}/candidate", data={"action": "discard"})

    assert image_path(tmp_path, image_id).read_bytes() == live_before
    assert not candidate_path(tmp_path, image_id).exists()


def test_candidate_image_route_serves_pending_candidate(tmp_path: Path) -> None:
    image_id = _seed_record(tmp_path)
    client = _app(tmp_path).test_client()
    assert client.get(f"/admin/images/{image_id}/candidate.png").status_code == 404

    candidate_path(tmp_path, image_id).write_bytes(b"candidate-bytes")
    response = client.get(f"/admin/images/{image_id}/candidate.png")
    assert response.status_code == 200
    assert response.data == b"candidate-bytes"


def test_upload_replaces_live_image_verbatim(tmp_path: Path) -> None:
    image_id = _seed_record(tmp_path)
    upload = _plain_png()

    client = _app(tmp_path).test_client()
    response = client.post(
        f"/admin/images/{image_id}/upload",
        data={"image": (io.BytesIO(upload), "handmade.png")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 302
    assert image_path(tmp_path, image_id).read_bytes() == upload


def test_upload_rejects_non_png(tmp_path: Path) -> None:
    image_id = _seed_record(tmp_path)
    live_before = image_path(tmp_path, image_id).read_bytes()

    client = _app(tmp_path).test_client()
    response = client.post(
        f"/admin/images/{image_id}/upload",
        data={"image": (io.BytesIO(b"not a png"), "junk.png")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert "not a valid image" in response.text
    assert image_path(tmp_path, image_id).read_bytes() == live_before
