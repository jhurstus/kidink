"""Orchestration: ensure a generated image exists on disk (spec §7.1–§7.3).

``ensure_image`` is the single entry point used during ``/render``: look up (or
create) the record for a logical key, and if its ``gen_images/<id>.png`` is
missing, generate → key → write it inline. Failures never propagate — they are
logged (secret-free) plus appended to ``gen_failures.log`` on disk (with the
item and prompt, §7.2), and the caller gets ``None`` so the render falls back
(§7.3). The row is kept on failure: a missing file simply marks the record for
retry on the next render.
"""

import logging
import os
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

logger = logging.getLogger(__name__)


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

    ``prompt`` seeds the record only on first creation; generation always uses
    the *stored* prompt, so admin edits persist (§7.4, §7.5). If the file
    already exists the record is used as-is with no API call (§7.1). Returns
    ``None`` on generation/keying failure (logged; render falls back, §7.3).
    """
    conn = open_db(storage_root)
    try:
        record = get_or_create_record(conn, spec, prompt)
    finally:
        conn.close()

    destination = image_path(storage_root, record.id)
    if destination.exists():
        return record.id
    try:
        _generate_to(destination, record, storage_root, generate, api_key, model)
    except (ImageGenerationError, KeyingError, ValueError, OSError) as exc:
        _log_failure(storage_root, record, exc)
        return None
    return record.id


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
    temp = destination.with_suffix(".tmp")
    temp.write_bytes(final)
    os.replace(temp, destination)


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
