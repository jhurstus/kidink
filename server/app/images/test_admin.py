import io
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from flask import Flask
from PIL import Image
from pydantic import SecretStr

from app import create_app
from app.images.db import (
    ImageSpec,
    attach_to_image,
    get_or_create_attachment,
    get_or_create_record,
    get_record,
    list_image_attachments,
    open_db,
)
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
        reference_images: Sequence[bytes] = (),
    ) -> bytes:
        return _keyable_png()

    app.config["GENERATE_IMAGE_BYTES"] = generate or default_generate
    return app


def _seed_record(tmp_path: Path, prompt: str = "seed prompt") -> int:
    conn = open_db(tmp_path)
    record, _ = get_or_create_record(conn, SPEC, prompt)
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


def test_detail_view_has_regenerate_busy_overlay(tmp_path: Path) -> None:
    # The overlay ships hidden and is revealed by the page's submit handler
    # while the (slow, budget-spending) regeneration POST is in flight.
    image_id = _seed_record(tmp_path)
    text = _app(tmp_path).test_client().get(f"/admin/images?img={image_id}").text
    assert '<div id="busy" hidden>' in text
    assert 'class="spinner"' in text
    assert 'getElementById("regenerate-form")' in text


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
        reference_images: Sequence[bytes] = (),
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
        reference_images: Sequence[bytes] = (),
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


def _seed_attachment(tmp_path: Path, image_id: int, rel_path: str) -> int:
    file = tmp_path / "prompt_images" / rel_path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(_plain_png())
    conn = open_db(tmp_path)
    attachment = get_or_create_attachment(conn, rel_path)
    attach_to_image(conn, image_id, attachment.id)
    conn.close()
    return attachment.id


def _attachment_paths(tmp_path: Path, image_id: int) -> list[str]:
    conn = open_db(tmp_path)
    paths = [a.path for a in list_image_attachments(conn, image_id)]
    conn.close()
    return paths


def test_detail_lists_attachments_with_previews(tmp_path: Path) -> None:
    image_id = _seed_record(tmp_path)
    _seed_attachment(tmp_path, image_id, "styles/dog.png")

    text = _app(tmp_path).test_client().get(f"/admin/images?img={image_id}").text
    assert "/images/prompt/styles/dog.png" in text
    assert "styles/dog.png" in text
    assert "(file missing)" not in text
    # The prompt-token hint documents the {{path}} syntax.
    assert "{{styles/comic-dog.png}}" in text


def test_detail_marks_missing_attachment_file(tmp_path: Path) -> None:
    image_id = _seed_record(tmp_path)
    _seed_attachment(tmp_path, image_id, "styles/dog.png")
    (tmp_path / "prompt_images" / "styles" / "dog.png").unlink()

    text = _app(tmp_path).test_client().get(f"/admin/images?img={image_id}").text
    assert "(file missing)" in text


def test_detach_removes_junction_only(tmp_path: Path) -> None:
    # The attachment may be shared: detaching from one record must leave the
    # file, the attachment row, and other records' links untouched.
    image_id = _seed_record(tmp_path)
    attachment_id = _seed_attachment(tmp_path, image_id, "styles/shared.png")
    conn = open_db(tmp_path)
    other, _ = get_or_create_record(conn, ImageSpec("Calendar", "Piano", 100, 60), "p")
    attach_to_image(conn, other.id, attachment_id)
    conn.close()

    response = (
        _app(tmp_path)
        .test_client()
        .post(
            f"/admin/images/{image_id}/attachments/detach",
            data={"attachment_id": str(attachment_id)},
        )
    )

    assert response.status_code == 302
    assert _attachment_paths(tmp_path, image_id) == []
    assert _attachment_paths(tmp_path, other.id) == ["styles/shared.png"]
    assert (tmp_path / "prompt_images" / "styles" / "shared.png").exists()


def test_attach_existing_links_attachment(tmp_path: Path) -> None:
    image_id = _seed_record(tmp_path)
    other_id = _seed_record_for(tmp_path, "Piano")
    attachment_id = _seed_attachment(tmp_path, other_id, "styles/shared.png")

    # The picker offers the other record's attachment for this one.
    client = _app(tmp_path).test_client()
    assert "styles/shared.png" in client.get(f"/admin/images?img={image_id}").text

    response = client.post(
        f"/admin/images/{image_id}/attachments/attach",
        data={"attachment_id": str(attachment_id)},
    )
    assert response.status_code == 302
    assert _attachment_paths(tmp_path, image_id) == ["styles/shared.png"]


def _seed_record_for(tmp_path: Path, item: str) -> int:
    conn = open_db(tmp_path)
    record, _ = get_or_create_record(conn, ImageSpec("Calendar", item, 100, 60), "p")
    conn.close()
    return record.id


def test_attach_unknown_attachment_reports_error(tmp_path: Path) -> None:
    image_id = _seed_record(tmp_path)
    response = (
        _app(tmp_path)
        .test_client()
        .post(
            f"/admin/images/{image_id}/attachments/attach",
            data={"attachment_id": "999"},
            follow_redirects=True,
        )
    )
    assert "unknown attachment" in response.text
    assert _attachment_paths(tmp_path, image_id) == []


def test_attachment_upload_writes_file_and_attaches(tmp_path: Path) -> None:
    image_id = _seed_record(tmp_path)
    upload = _plain_png()

    response = (
        _app(tmp_path)
        .test_client()
        .post(
            f"/admin/images/{image_id}/attachments/upload",
            data={
                "image": (io.BytesIO(upload), "dog.png"),
                "path": "styles/comic-dog.png",
            },
            content_type="multipart/form-data",
        )
    )

    assert response.status_code == 302
    written = tmp_path / "prompt_images" / "styles" / "comic-dog.png"
    assert written.read_bytes() == upload
    assert _attachment_paths(tmp_path, image_id) == ["styles/comic-dog.png"]


def test_attachment_upload_defaults_path_to_filename(tmp_path: Path) -> None:
    image_id = _seed_record(tmp_path)

    response = (
        _app(tmp_path)
        .test_client()
        .post(
            f"/admin/images/{image_id}/attachments/upload",
            data={"image": (io.BytesIO(_plain_png()), "house-style.png")},
            content_type="multipart/form-data",
        )
    )

    assert response.status_code == 302
    assert _attachment_paths(tmp_path, image_id) == ["house-style.png"]


def test_attachment_upload_rejects_traversal_path(tmp_path: Path) -> None:
    image_id = _seed_record(tmp_path)

    response = (
        _app(tmp_path)
        .test_client()
        .post(
            f"/admin/images/{image_id}/attachments/upload",
            data={
                "image": (io.BytesIO(_plain_png()), "dog.png"),
                "path": "../outside.png",
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
    )

    assert "invalid attachment path" in response.text
    assert not (tmp_path / "outside.png").exists()
    assert _attachment_paths(tmp_path, image_id) == []


def test_attachment_upload_rejects_non_image_payload(tmp_path: Path) -> None:
    image_id = _seed_record(tmp_path)

    response = (
        _app(tmp_path)
        .test_client()
        .post(
            f"/admin/images/{image_id}/attachments/upload",
            data={"image": (io.BytesIO(b"not an image"), "junk.png")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
    )

    assert "not a valid image" in response.text
    assert _attachment_paths(tmp_path, image_id) == []


def _plain_image(image_format: str) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (10, 6), (255, 0, 0)).save(out, format=image_format)
    return out.getvalue()


def test_attachment_upload_accepts_jpeg_and_webp(tmp_path: Path) -> None:
    image_id = _seed_record(tmp_path)
    client = _app(tmp_path).test_client()

    jpeg_response = client.post(
        f"/admin/images/{image_id}/attachments/upload",
        data={
            "image": (io.BytesIO(_plain_image("JPEG")), "photo.jpg"),
            "path": "styles/photo.jpeg",
        },
        content_type="multipart/form-data",
    )
    webp_response = client.post(
        f"/admin/images/{image_id}/attachments/upload",
        data={"image": (io.BytesIO(_plain_image("WEBP")), "art.webp")},
        content_type="multipart/form-data",
    )

    assert jpeg_response.status_code == 302
    assert webp_response.status_code == 302
    assert _attachment_paths(tmp_path, image_id) == ["styles/photo.jpeg", "art.webp"]
    assert (tmp_path / "prompt_images" / "styles" / "photo.jpeg").exists()
    assert (tmp_path / "prompt_images" / "art.webp").exists()


def test_attachment_upload_rejects_unsupported_image_format(tmp_path: Path) -> None:
    # A real image, but not a format the image API takes as reference input.
    image_id = _seed_record(tmp_path)

    response = (
        _app(tmp_path)
        .test_client()
        .post(
            f"/admin/images/{image_id}/attachments/upload",
            data={"image": (io.BytesIO(_plain_image("GIF")), "anim.gif")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
    )

    assert "must be a PNG, JPEG, or WebP" in response.text
    assert _attachment_paths(tmp_path, image_id) == []


def test_attachment_upload_rejects_mismatched_extension(tmp_path: Path) -> None:
    # The stored suffix drives the serving mimetype, so it must match the
    # actual content: JPEG bytes may not land at a .png path.
    image_id = _seed_record(tmp_path)

    response = (
        _app(tmp_path)
        .test_client()
        .post(
            f"/admin/images/{image_id}/attachments/upload",
            data={
                "image": (io.BytesIO(_plain_image("JPEG")), "photo.jpg"),
                "path": "styles/photo.png",
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
    )

    assert "must end in .jpg or .jpeg for a JPEG upload" in response.text
    assert not (tmp_path / "prompt_images" / "styles" / "photo.png").exists()
    assert _attachment_paths(tmp_path, image_id) == []


def test_attachment_upload_refuses_existing_file_with_different_content(
    tmp_path: Path,
) -> None:
    # A reference image may be shared across records; replacing it in place
    # from one record's page is refused (attach the existing one instead).
    image_id = _seed_record(tmp_path)
    _seed_attachment(tmp_path, image_id, "styles/dog.png")
    original = (tmp_path / "prompt_images" / "styles" / "dog.png").read_bytes()

    response = (
        _app(tmp_path)
        .test_client()
        .post(
            f"/admin/images/{image_id}/attachments/upload",
            data={
                "image": (io.BytesIO(_plain_png() + b"tail"), "dog.png"),
                "path": "styles/dog.png",
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
    )

    assert "a different file already exists" in response.text
    assert (tmp_path / "prompt_images" / "styles" / "dog.png").read_bytes() == original


def test_attachment_upload_adopts_identical_existing_file(tmp_path: Path) -> None:
    # A hand-dropped file (no database row) re-uploaded with identical bytes
    # is adopted: row + link created, file untouched. This also makes a retry
    # of an interrupted upload idempotent instead of permanently refused.
    image_id = _seed_record(tmp_path)
    data = _plain_png()
    file = tmp_path / "prompt_images" / "styles" / "dog.png"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(data)

    response = (
        _app(tmp_path)
        .test_client()
        .post(
            f"/admin/images/{image_id}/attachments/upload",
            data={"image": (io.BytesIO(data), "dog.png"), "path": "styles/dog.png"},
            content_type="multipart/form-data",
        )
    )

    assert response.status_code == 302
    assert _attachment_paths(tmp_path, image_id) == ["styles/dog.png"]
    assert file.read_bytes() == data


def test_attachment_upload_retries_after_row_without_file(tmp_path: Path) -> None:
    # The row-before-file ordering means a mid-upload failure leaves a row
    # whose file is missing; a retry of the same upload must complete it.
    image_id = _seed_record(tmp_path)
    conn = open_db(tmp_path)
    attachment = get_or_create_attachment(conn, "styles/dog.png")
    attach_to_image(conn, image_id, attachment.id)
    conn.close()
    data = _plain_png()

    response = (
        _app(tmp_path)
        .test_client()
        .post(
            f"/admin/images/{image_id}/attachments/upload",
            data={"image": (io.BytesIO(data), "dog.png"), "path": "styles/dog.png"},
            content_type="multipart/form-data",
        )
    )

    assert response.status_code == 302
    assert _attachment_paths(tmp_path, image_id) == ["styles/dog.png"]
    assert (tmp_path / "prompt_images" / "styles" / "dog.png").read_bytes() == data


def test_attachment_upload_double_submit_is_idempotent(tmp_path: Path) -> None:
    image_id = _seed_record(tmp_path)
    data = _plain_png()
    client = _app(tmp_path).test_client()

    for _ in range(2):
        response = client.post(
            f"/admin/images/{image_id}/attachments/upload",
            data={"image": (io.BytesIO(data), "dog.png"), "path": "styles/dog.png"},
            content_type="multipart/form-data",
        )
        assert response.status_code == 302

    assert _attachment_paths(tmp_path, image_id) == ["styles/dog.png"]
