import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from pydantic import SecretStr

from app.images.db import ImageSpec, get_or_create_record, open_db, update_prompt
from app.images.generate import GenerateImageBytes, ImageGenerationError
from app.images.store import (
    candidate_path,
    ensure_image,
    image_path,
    regenerate_candidate,
)

SPEC = ImageSpec(module="Calendar", item_description="Soccer", width=100, height=60)
KEY_VALUE = "sk-test-not-a-real-key"
API_KEY = SecretStr(KEY_VALUE)


def keyable_png() -> bytes:
    """A red rectangle on a pure-green background — keys cleanly."""
    pixels = np.tile(np.array((0, 255, 0), np.uint8), (480, 800, 1))
    pixels[40:440, 40:760] = (220, 40, 40)
    out = io.BytesIO()
    Image.fromarray(pixels, "RGB").save(out, format="PNG")
    return out.getvalue()


class CountingGenerator:
    """Fake generation seam recording each call's prompt."""

    def __init__(self, result: bytes | Exception) -> None:
        self.result = result
        self.prompts: list[str] = []

    def __call__(
        self, api_key: SecretStr, *, prompt: str, size: str, model: str
    ) -> bytes:
        self.prompts.append(prompt)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _ensure(
    tmp_path: Path, generate: GenerateImageBytes, prompt: str = "p"
) -> int | None:
    return ensure_image(
        SPEC,
        prompt,
        storage_root=tmp_path,
        generate=generate,
        api_key=API_KEY,
        model="gpt-image-2",
    )


def test_generates_once_then_serves_from_disk(tmp_path: Path) -> None:
    generator = CountingGenerator(keyable_png())
    first = _ensure(tmp_path, generator)
    second = _ensure(tmp_path, generator)
    assert first == second and first is not None
    assert len(generator.prompts) == 1  # warm path makes zero API calls (§7.1)
    png = image_path(tmp_path, first).read_bytes()
    image = Image.open(io.BytesIO(png))
    # The stored PNG is the native-resolution crop of the 720×400 subject —
    # larger than the record's logical 100×60 box, which is display-only.
    assert image.mode == "RGBA" and image.size == (720, 400)


def test_generation_uses_requested_size_and_model(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    def generate(api_key: SecretStr, *, prompt: str, size: str, model: str) -> bytes:
        calls.append((size, model))
        return keyable_png()

    ensure_image(
        SPEC,
        "p",
        storage_root=tmp_path,
        generate=generate,
        api_key=API_KEY,
        model="m-x",
    )
    assert calls == [("1600x960", "m-x")]


def test_failure_returns_none_keeps_row_and_logs(tmp_path: Path) -> None:
    generator = CountingGenerator(ImageGenerationError("image generation failed: Boom"))
    assert _ensure(tmp_path, generator, prompt="the full prompt text") is None

    # Row kept (missing file = retry next render), no PNG written.
    conn = open_db(tmp_path)
    record = get_or_create_record(conn, SPEC, "ignored")
    conn.close()
    assert record.prompt == "the full prompt text"
    assert not image_path(tmp_path, record.id).exists()

    log = (tmp_path / "gen_failures.log").read_text()
    assert "Soccer" in log and "the full prompt text" in log
    assert KEY_VALUE not in log  # secrets never reach the failure log


def test_unkeyable_generation_is_a_failure(tmp_path: Path) -> None:
    # An all-green image keys to nothing — treated like a generation failure.
    pixels = np.tile(np.array((0, 255, 0), np.uint8), (480, 800, 1))
    out = io.BytesIO()
    Image.fromarray(pixels, "RGB").save(out, format="PNG")
    assert _ensure(tmp_path, CountingGenerator(out.getvalue())) is None
    assert "KeyingError" in (tmp_path / "gen_failures.log").read_text()


def test_regeneration_uses_stored_prompt(tmp_path: Path) -> None:
    # Row exists (with an edited prompt) but its file is missing: generation must
    # use the stored prompt, not the caller's seed prompt (§7.4/§7.5).
    conn = open_db(tmp_path)
    record = get_or_create_record(conn, SPEC, "original")
    update_prompt(conn, record.id, "edited by admin")
    conn.close()

    generator = CountingGenerator(keyable_png())
    assert _ensure(tmp_path, generator, prompt="seed to ignore") == record.id
    assert generator.prompts == ["edited by admin"]


def test_regenerate_candidate_writes_candidate_only(tmp_path: Path) -> None:
    generator = CountingGenerator(keyable_png())
    image_id = _ensure(tmp_path, generator)
    assert image_id is not None
    live_bytes = image_path(tmp_path, image_id).read_bytes()

    conn = open_db(tmp_path)
    record = get_or_create_record(conn, SPEC, "ignored")
    conn.close()
    regenerate_candidate(
        record,
        storage_root=tmp_path,
        generate=generator,
        api_key=API_KEY,
        model="gpt-image-2",
    )
    assert candidate_path(tmp_path, image_id).exists()
    assert image_path(tmp_path, image_id).read_bytes() == live_bytes  # live untouched


def test_regenerate_candidate_failure_propagates(tmp_path: Path) -> None:
    conn = open_db(tmp_path)
    record = get_or_create_record(conn, SPEC, "p")
    conn.close()
    with pytest.raises(ImageGenerationError):
        regenerate_candidate(
            record,
            storage_root=tmp_path,
            generate=CountingGenerator(ImageGenerationError("boom")),
            api_key=API_KEY,
            model="gpt-image-2",
        )
