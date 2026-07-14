import io
import threading
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
    ensure_images,
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
    """Fake generation seam recording each call's prompt and base image."""

    def __init__(self, result: bytes | Exception) -> None:
        self.result = result
        self.prompts: list[str] = []
        self.base_pngs: list[bytes | None] = []

    def __call__(
        self,
        api_key: SecretStr,
        *,
        prompt: str,
        size: str,
        model: str,
        base_png: bytes | None = None,
    ) -> bytes:
        self.prompts.append(prompt)
        self.base_pngs.append(base_png)
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

    def generate(
        api_key: SecretStr,
        *,
        prompt: str,
        size: str,
        model: str,
        base_png: bytes | None = None,
    ) -> bytes:
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


def _spec(item: str) -> ImageSpec:
    return ImageSpec(module="Calendar", item_description=item, width=100, height=60)


def _ensure_batch(
    tmp_path: Path, requests: list[tuple[ImageSpec, str]], generate: GenerateImageBytes
) -> list[int | None]:
    return ensure_images(
        requests,
        storage_root=tmp_path,
        generate=generate,
        api_key=API_KEY,
        model="gpt-image-2",
    )


def test_ensure_images_assigns_ids_serially_in_request_order(tmp_path: Path) -> None:
    # Records are created before any (parallel) generation runs, so a cold
    # batch's id assignment is deterministic (§3.4) regardless of which
    # generation finishes first.
    generator = CountingGenerator(keyable_png())
    ids = _ensure_batch(
        tmp_path, [(_spec(f"item {i}"), f"p{i}") for i in range(3)], generator
    )

    assert ids == [1, 2, 3]
    assert len(generator.prompts) == 3
    assert all(image_path(tmp_path, i).exists() for i in (1, 2, 3))


def test_ensure_images_failure_hits_only_its_own_item(tmp_path: Path) -> None:
    def generate(
        api_key: SecretStr,
        *,
        prompt: str,
        size: str,
        model: str,
        base_png: bytes | None = None,
    ) -> bytes:
        if prompt == "boom":
            raise ImageGenerationError("image generation failed: Boom")
        return keyable_png()

    good, bad = _ensure_batch(
        tmp_path, [(_spec("ok"), "fine"), (_spec("broken"), "boom")], generate
    )

    assert good is not None and image_path(tmp_path, good).exists()
    assert bad is None
    log = (tmp_path / "gen_failures.log").read_text()
    assert "item='broken'" in log and "item='ok'" not in log


def test_ensure_images_duplicate_key_generates_once(tmp_path: Path) -> None:
    generator = CountingGenerator(keyable_png())
    first, second = _ensure_batch(
        tmp_path, [(_spec("Soccer"), "p"), (_spec("Soccer"), "p")], generator
    )

    assert first == second and first is not None
    assert len(generator.prompts) == 1


def test_concurrent_ensure_of_same_record_generates_once(tmp_path: Path) -> None:
    # Two renders racing on the same cold record: the second must block on the
    # per-record generation lock and, once inside, find the file already
    # written — one API call total, no clobbered temp file, no false failure.
    entered = threading.Event()
    release = threading.Event()
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
        entered.set()
        assert release.wait(timeout=10)
        return keyable_png()

    results: list[int | None] = []

    def run() -> None:
        results.append(_ensure(tmp_path, generate))

    first = threading.Thread(target=run)
    first.start()
    assert entered.wait(timeout=10)  # first generation is now mid-flight
    second = threading.Thread(target=run)
    second.start()
    release.set()
    first.join(timeout=10)
    second.join(timeout=10)

    assert len(prompts) == 1
    assert results[0] is not None and results[0] == results[1]
    assert image_path(tmp_path, results[0]).exists()


def test_ensure_images_generates_concurrently(tmp_path: Path) -> None:
    # Both generations must be in flight at once: each blocks on a two-party
    # barrier, so a serial implementation would deadlock the first call until
    # the timeout (surfacing as BrokenBarrierError) instead of passing.
    barrier = threading.Barrier(2)

    def generate(
        api_key: SecretStr,
        *,
        prompt: str,
        size: str,
        model: str,
        base_png: bytes | None = None,
    ) -> bytes:
        barrier.wait(timeout=10)
        return keyable_png()

    ids = _ensure_batch(
        tmp_path, [(_spec("first"), "p1"), (_spec("second"), "p2")], generate
    )

    assert ids == [1, 2]


UNKEYED_SPEC = ImageSpec(
    module="Countdown", item_description="camping trip", width=460, height=150
)
EDIT_SPEC = ImageSpec(
    module="Countdown",
    item_description="camping trip",
    width=460,
    height=150,
    variant="excited",
)


def _ensure_spec(
    tmp_path: Path, spec: ImageSpec, generate: GenerateImageBytes
) -> int | None:
    return ensure_image(
        spec,
        "p",
        storage_root=tmp_path,
        generate=generate,
        api_key=API_KEY,
        model="gpt-image-2",
    )


def test_unkeyed_module_stores_raw_bytes_verbatim(tmp_path: Path) -> None:
    # The Countdown hero policy: no chroma keying, no crop — an all-green PNG
    # that would raise KeyingError elsewhere is stored byte-identical.
    pixels = np.tile(np.array((0, 255, 0), np.uint8), (480, 800, 1))
    out = io.BytesIO()
    Image.fromarray(pixels, "RGB").save(out, format="PNG")
    raw = out.getvalue()

    image_id = _ensure_spec(tmp_path, UNKEYED_SPEC, CountingGenerator(raw))

    assert image_id is not None
    assert image_path(tmp_path, image_id).read_bytes() == raw


def test_edit_variant_passes_stored_base_bytes(tmp_path: Path) -> None:
    # The excited hero is an *edit* of its base record's PNG: the base must be
    # handed to the seam verbatim, and the plain generation must not carry one.
    generator = CountingGenerator(b"base png bytes")
    base_id = _ensure_spec(tmp_path, UNKEYED_SPEC, generator)
    assert base_id is not None

    variant_generator = CountingGenerator(b"excited png bytes")
    variant_id = _ensure_spec(tmp_path, EDIT_SPEC, variant_generator)

    assert variant_id is not None and variant_id != base_id
    assert generator.base_pngs == [None]
    assert variant_generator.base_pngs == [b"base png bytes"]
    assert image_path(tmp_path, variant_id).read_bytes() == b"excited png bytes"


def test_edit_variant_without_base_fails_softly(tmp_path: Path) -> None:
    # No base row/file yet: the variant's generation is a normal soft failure
    # (None + a failure-log line), never an exception out of ensure_image.
    generator = CountingGenerator(b"never generated")

    assert _ensure_spec(tmp_path, EDIT_SPEC, generator) is None
    assert generator.base_pngs == []  # failed before ever calling the seam
    assert "ImageGenerationError" in (tmp_path / "gen_failures.log").read_text()


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
