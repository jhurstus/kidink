"""Orchestration: ensure generated images exist on disk (spec §7.1–§7.3).

``ensure_images`` is the entry point used during ``/render``: look up (or
create) the record for each logical key, and generate → key → write any whose
``gen_images/<id>.png`` is missing. Records are created **serially** so id
assignment stays deterministic (§3.4); the expensive generate+key work then
runs **concurrently** across the batch, so a cold day with many uncached icons
costs roughly one API round-trip of wall-clock, not N. Failures never
propagate — they are logged (secret-free) plus appended to ``gen_failures.log``
on disk (with the item and prompt, §7.2), and the caller gets ``None`` for that
item so the render falls back (§7.3). The row is kept on failure: a missing
file simply marks the record for retry on the next render.
"""

import logging
import os
import threading
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from pydantic import SecretStr

from app.images.db import ImageRecord, ImageSpec, get_or_create_record, open_db
from app.images.generate import (
    GenerateImageBytes,
    ImageGenerationError,
    generation_size,
)
from app.images.keying import KeyingError, key_and_crop

GEN_IMAGES_DIR = "gen_images"
_FAILURE_LOG = "gen_failures.log"

# Cap on concurrent generations within one batch. Generous relative to a
# render's realistic worst case (a fully cold strip + today, ~14 icons) while
# staying well inside API rate limits.
_MAX_CONCURRENT_GENERATIONS = 8

logger = logging.getLogger(__name__)

# Generation is serialized per image id: two renders (or one render's two
# batches) racing on the same cold record must not both spend an API call or
# fight over the destination file — the loser of the race finds the file
# already present and skips (see the double-check in ensure_images). The locks
# are per-process, which matches the single-server deployment (§2); the
# writer-unique temp name in _generate_to is the backstop if multiple processes
# ever share a storage root. The dict grows one entry per image ever generated
# by this process — a few dozen over its lifetime, never pruned.
_generation_locks: dict[int, threading.Lock] = {}
_generation_locks_guard = threading.Lock()


def _generation_lock(image_id: int) -> threading.Lock:
    """The process-wide lock serializing generation of ``image_id``."""
    with _generation_locks_guard:
        return _generation_locks.setdefault(image_id, threading.Lock())


def image_path(storage_root: Path, image_id: int) -> Path:
    """The on-disk path of a record's rendered PNG (``gen_images/<id>.png``)."""
    return storage_root / GEN_IMAGES_DIR / f"{image_id}.png"


def candidate_path(storage_root: Path, image_id: int) -> Path:
    """The staging path for an admin regeneration awaiting keep/discard (§7.4)."""
    return storage_root / GEN_IMAGES_DIR / f"{image_id}.candidate.png"


def ensure_image(
    spec: ImageSpec,
    prompt: str,
    *,
    storage_root: Path,
    generate: GenerateImageBytes,
    api_key: SecretStr,
    model: str,
) -> int | None:
    """Return the id of ``spec``'s record with its PNG guaranteed on disk.

    Single-image convenience over :func:`ensure_images` — same contract.
    """
    return ensure_images(
        [(spec, prompt)],
        storage_root=storage_root,
        generate=generate,
        api_key=api_key,
        model=model,
    )[0]


def ensure_images(
    requests: Sequence[tuple[ImageSpec, str]],
    *,
    storage_root: Path,
    generate: GenerateImageBytes,
    api_key: SecretStr,
    model: str,
) -> list[int | None]:
    """Return each request's record id with its PNG guaranteed on disk.

    ``requests`` pairs each :class:`ImageSpec` with the prompt that seeds its
    record on first creation; generation always uses the *stored* prompt, so
    admin edits persist (§7.4, §7.5). Records are created serially in request
    order (deterministic id assignment, §3.4); images whose file already exists
    make no API call (§7.1), and the rest — deduplicated by record, in case two
    requests share a logical key — are generated **concurrently**. Generation
    is serialized per record (with a re-check under the lock), so overlapping
    calls racing on the same cold image spend one API call between them. A
    generation/keying failure yields ``None`` for that item only (logged; the
    render falls back, §7.3).
    """
    conn = open_db(storage_root)
    try:
        records = [
            get_or_create_record(conn, spec, prompt) for spec, prompt in requests
        ]
    finally:
        conn.close()

    missing = {
        record.id: record
        for record in records
        if not image_path(storage_root, record.id).exists()
    }
    failed: set[int] = set()
    if missing:

        def generate_one(record: ImageRecord) -> Exception | None:
            destination = image_path(storage_root, record.id)
            with _generation_lock(record.id):
                # Double-check under the lock: a concurrent render may have
                # generated this record while we waited.
                if destination.exists():
                    return None
                try:
                    _generate_to(
                        destination, record, storage_root, generate, api_key, model
                    )
                except (ImageGenerationError, KeyingError, ValueError, OSError) as exc:
                    return exc
            return None

        workers = min(_MAX_CONCURRENT_GENERATIONS, len(missing))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            outcomes = list(pool.map(generate_one, missing.values()))
        # Workers only capture their exception; logging happens here, serially
        # and in request order, so gen_failures.log lines never interleave.
        for record, exc in zip(missing.values(), outcomes, strict=True):
            if exc is not None:
                failed.add(record.id)
                _log_failure(storage_root, record, exc)

    return [None if record.id in failed else record.id for record in records]


def regenerate_candidate(
    record: ImageRecord,
    *,
    storage_root: Path,
    generate: GenerateImageBytes,
    api_key: SecretStr,
    model: str,
) -> None:
    """Generate a fresh image to ``<id>.candidate.png`` for admin review (§7.4).

    The candidate sits beside the live PNG until the admin keeps (replacing the
    live file) or discards it. Unlike :func:`ensure_image`, failures propagate —
    the admin UI reports them directly.
    """
    _generate_to(
        candidate_path(storage_root, record.id),
        record,
        storage_root,
        generate,
        api_key,
        model,
    )


def _generate_to(
    destination: Path,
    record: ImageRecord,
    storage_root: Path,
    generate: GenerateImageBytes,
    api_key: SecretStr,
    model: str,
) -> None:
    """Generate + key ``record``'s image and atomically write it to ``destination``."""
    spec = record.spec
    raw = generate(
        api_key,
        prompt=record.prompt,
        size=generation_size(spec.width, spec.height),
        model=model,
    )
    final = key_and_crop(raw)

    destination.parent.mkdir(parents=True, exist_ok=True)
    # Writer-unique temp name (pid + thread id): even writers not covered by
    # the per-process generation locks (another process on the same storage
    # root) can never clobber each other's in-flight temp file; last replace
    # wins with a complete image either way.
    temp = destination.with_suffix(f".{os.getpid()}.{threading.get_native_id()}.tmp")
    try:
        temp.write_bytes(final)
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)


def _log_failure(storage_root: Path, record: ImageRecord, exc: Exception) -> None:
    """Log a generation failure: app log (secret-free) + gen_failures.log (§7.2).

    The disk line carries the item and full prompt (neither contains secrets by
    construction); both destinations name only the exception *type*, since SDK
    message text could echo request details.
    """
    spec = record.spec
    logger.warning(
        "image generation failed module=%s item=%r size=%dx%d variant=%s error=%s",
        spec.module,
        spec.item_description,
        spec.width,
        spec.height,
        spec.variant,
        type(exc).__name__,
    )
    line = (
        f"{datetime.now(UTC).isoformat()}\t"
        f"module={spec.module}\titem={spec.item_description!r}\t"
        f"size={spec.width}x{spec.height}\tvariant={spec.variant}\t"
        f"error={type(exc).__name__}\tprompt={record.prompt!r}\n"
    )
    storage_root.mkdir(parents=True, exist_ok=True)
    with (storage_root / _FAILURE_LOG).open("a", encoding="utf-8") as f:
        f.write(line)
