import io
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from flask import Flask
from PIL import Image
from pydantic import SecretStr

from app import create_app
from app.images import ImageGenerationError
from app.images.db import get_or_create_record
from app.joke.hero import joke_image_spec
from app.joke.jokes import add_jokes, list_jokes, open_jokes_db

# 2026-06-03 20:00 UTC == 13:00 US/Pacific (the fake config's timezone) - a
# fixed "today" for the ★ marker, away from midnight.
NOW = datetime(2026, 6, 3, 20, 0, tzinfo=UTC)


def _solid_png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (800, 480), (40, 90, 200)).save(out, format="PNG")
    return out.getvalue()


def _generate_unexpected(
    api_key: SecretStr,
    *,
    prompt: str,
    size: str,
    model: str,
    base_png: bytes | None = None,
    reference_images: Sequence[bytes] = (),
) -> bytes:
    raise AssertionError("image generation was invoked but no test seam was set")


def _generate_ok(
    api_key: SecretStr,
    *,
    prompt: str,
    size: str,
    model: str,
    base_png: bytes | None = None,
    reference_images: Sequence[bytes] = (),
) -> bytes:
    return _solid_png()


def _generate_boom(
    api_key: SecretStr,
    *,
    prompt: str,
    size: str,
    model: str,
    base_png: bytes | None = None,
    reference_images: Sequence[bytes] = (),
) -> bytes:
    raise ImageGenerationError("image generation failed: Boom")


def _app(tmp_path: Path, generate: object = None) -> Flask:
    app = create_app()
    app.config["NOW"] = NOW
    app.config["GENERATE_IMAGE_BYTES"] = generate or _generate_unexpected
    app.config["APP_STORAGE_PATH"] = tmp_path
    return app


def _seed(tmp_path: Path, texts: list[str]) -> None:
    conn = open_jokes_db(tmp_path)
    try:
        add_jokes(conn, texts)
    finally:
        conn.close()


def test_empty_store_shows_a_prompt_to_add(tmp_path: Path) -> None:
    text = _app(tmp_path).test_client().get("/admin/jokes").text
    assert "No jokes yet" in text
    assert "Add jokes" in text


def test_lists_jokes_and_flags_today(tmp_path: Path) -> None:
    _seed(tmp_path, ["joke A", "joke B", "joke C"])

    text = _app(tmp_path).test_client().get("/admin/jokes").text

    assert "joke A" in text
    assert "joke C" in text
    # Exactly one joke is flagged as today's (§15 index into the list).
    assert text.count('class="today"') == 1


def test_add_via_textarea_appends(tmp_path: Path) -> None:
    client = _app(tmp_path).test_client()

    response = client.post("/admin/jokes/add", data={"jokes": "joke A\n\njoke B"})
    assert response.status_code == 302

    conn = open_jokes_db(tmp_path)
    try:
        assert [j.text for j in list_jokes(conn)] == ["joke A", "joke B"]
    finally:
        conn.close()


def test_edit_updates_text(tmp_path: Path) -> None:
    _seed(tmp_path, ["joke A"])
    conn = open_jokes_db(tmp_path)
    joke_id = list_jokes(conn)[0].id
    conn.close()
    client = _app(tmp_path).test_client()

    response = client.post(f"/admin/jokes/{joke_id}/edit", data={"text": "joke A!"})
    assert response.status_code == 302

    conn = open_jokes_db(tmp_path)
    try:
        assert [j.text for j in list_jokes(conn)] == ["joke A!"]
    finally:
        conn.close()


def test_empty_edit_deletes_the_joke(tmp_path: Path) -> None:
    _seed(tmp_path, ["joke A"])
    conn = open_jokes_db(tmp_path)
    joke_id = list_jokes(conn)[0].id
    conn.close()
    client = _app(tmp_path).test_client()

    client.post(f"/admin/jokes/{joke_id}/edit", data={"text": "   "})

    conn = open_jokes_db(tmp_path)
    try:
        assert list_jokes(conn) == []
    finally:
        conn.close()


def test_delete_removes_the_joke(tmp_path: Path) -> None:
    _seed(tmp_path, ["joke A", "joke B"])
    conn = open_jokes_db(tmp_path)
    joke_id = list_jokes(conn)[0].id
    conn.close()
    client = _app(tmp_path).test_client()

    response = client.post(f"/admin/jokes/{joke_id}/delete")
    assert response.status_code == 302

    conn = open_jokes_db(tmp_path)
    try:
        assert [j.text for j in list_jokes(conn)] == ["joke B"]
    finally:
        conn.close()


def test_links_to_the_image_admin_when_the_record_exists(tmp_path: Path) -> None:
    _seed(tmp_path, ["joke A"])
    conn = open_jokes_db(tmp_path)
    record, _ = get_or_create_record(conn, joke_image_spec("joke A"), "p")
    conn.close()

    text = _app(tmp_path).test_client().get("/admin/jokes").text

    assert f"/admin/images?img={record.id}" in text


def test_generate_creates_the_record_and_redirects_to_it(tmp_path: Path) -> None:
    _seed(tmp_path, ["joke A"])
    conn = open_jokes_db(tmp_path)
    joke_id = list_jokes(conn)[0].id
    conn.close()
    client = _app(tmp_path, _generate_ok).test_client()

    response = client.post(f"/admin/jokes/{joke_id}/generate")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/images?img=1")
    # Unkeyed: the raw generation bytes are stored verbatim (RGB, not keyed RGBA).
    assert (tmp_path / "gen_images" / "1.png").exists()
    assert Image.open(tmp_path / "gen_images" / "1.png").mode == "RGB"


def test_generate_for_absent_joke_redirects_with_error(tmp_path: Path) -> None:
    client = _app(tmp_path).test_client()

    response = client.post("/admin/jokes/999/generate", follow_redirects=True)

    assert "no such joke" in response.text


def test_generate_failure_redirects_with_error(tmp_path: Path) -> None:
    _seed(tmp_path, ["joke A"])
    conn = open_jokes_db(tmp_path)
    joke_id = list_jokes(conn)[0].id
    conn.close()
    client = _app(tmp_path, _generate_boom).test_client()

    response = client.post(f"/admin/jokes/{joke_id}/generate", follow_redirects=True)

    assert "generation failed" in response.text
