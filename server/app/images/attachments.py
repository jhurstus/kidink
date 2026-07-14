"""Prompt attachments: style-reference images fed to generation (spec §7.1, §7.2).

Attachment files live under ``prompt_images/`` inside the storage root and are
identified everywhere by their **relative POSIX path** under that directory
(the ``prompt_attachments.path`` column). A record's effective attachments at
generation time are the union of its junction-table rows and any
``{{<path>}}`` tokens in its stored prompt — the token form lets a calendar
event's ``icon_description`` (§6.4) or an admin-edited prompt pull in a
reference image without touching the database. Tokens are rewritten to ordinal
references ("reference image N") in the outgoing prompt; the stored prompt is
never modified.

Module defaults (§7.5) follow a convention directory: every image under
``prompt_images/defaults/<module lowercase>/`` seeds a newly created record of
that module (see ``app.images.store``).
"""

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

PROMPT_IMAGES_DIR = "prompt_images"
DEFAULTS_DIR = "defaults"

# The reference-image formats the image API accepts on its edits endpoint
# (besides dall-e-2's PNG-only rule, which no configured model uses).
_REFERENCE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})

logger = logging.getLogger(__name__)

# {{ path/to/image.png }} — whitespace inside the braces is ignored; the
# payload itself may not contain braces.
_TOKEN_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def prompt_images_root(storage_root: Path) -> Path:
    """The directory holding all prompt-attachment images (§18)."""
    return storage_root / PROMPT_IMAGES_DIR


def normalize_attachment_path(path: str) -> str:
    """Validate and normalize a ``prompt_images/``-relative path.

    The single gatekeeper for every path that reaches the filesystem — from an
    admin form, a ``{{...}}`` prompt token, or a database row. Raises
    :class:`ValueError` for anything that could escape ``prompt_images/``
    (absolute paths, ``.``/``..`` segments, backslashes, NULs) or is empty.
    Returns the cleaned POSIX-style relative path.
    """
    candidate = path.strip()
    if not candidate or "\\" in candidate or "\x00" in candidate:
        raise ValueError(f"invalid attachment path: {path!r}")
    pure = PurePosixPath(candidate)
    if pure.is_absolute() or not pure.parts:
        raise ValueError(f"invalid attachment path: {path!r}")
    if any(part in (".", "..") for part in pure.parts):
        raise ValueError(f"invalid attachment path: {path!r}")
    return str(pure)


def attachment_file_path(storage_root: Path, path: str) -> Path:
    """The on-disk file for an attachment path (normalizing defensively)."""
    return prompt_images_root(storage_root) / normalize_attachment_path(path)


def default_attachment_paths(storage_root: Path, module: str) -> list[str]:
    """A module's default style examples (§7.5), as attachment paths.

    Scans ``prompt_images/defaults/<module lowercase>/`` non-recursively for
    regular, non-hidden image files (png/jpg/jpeg/webp, by suffix), sorted by
    filename so seeding order — and with it the ordinal numbering in prompts —
    is deterministic (§3.4). An absent directory simply means no defaults.
    """
    relative_dir = f"{DEFAULTS_DIR}/{module.lower()}"
    directory = prompt_images_root(storage_root) / relative_dir
    if not directory.is_dir():
        return []
    return [
        f"{relative_dir}/{entry.name}"
        for entry in sorted(directory.iterdir(), key=lambda entry: entry.name)
        if entry.is_file()
        and not entry.name.startswith(".")
        and entry.suffix.lower() in _REFERENCE_SUFFIXES
    ]


def sniff_image_format(data: bytes) -> tuple[str, str] | None:
    """``(extension, mimetype)`` for supported reference-image bytes.

    Detects the accepted upload formats (PNG, JPEG, WebP) by magic number, so
    labeling never trusts a file's name; returns ``None`` for anything else.
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


@dataclass(frozen=True)
class ResolvedPrompt:
    """The outcome of :func:`resolve_prompt_attachments`.

    ``reference_images`` holds the attachment bytes (PNG, JPEG, or WebP) in
    the exact order they are sent to the API; ``paths`` is parallel to it
    (for logging and tests).
    """

    prompt: str
    reference_images: tuple[bytes, ...]
    paths: tuple[str, ...]


def resolve_prompt_attachments(
    prompt: str,
    junction_paths: Sequence[str],
    *,
    storage_root: Path,
    extra_images: int = 0,
) -> ResolvedPrompt:
    """Resolve a record's attachments and rewrite its prompt for the API (§7.2).

    Candidates are ``junction_paths`` (the record's junction-table rows, in
    their stored order) followed by ``{{<path>}}`` tokens in first-appearance
    order, deduplicated by normalized path — a path present in both appears
    once, at its junction position. Each candidate's bytes are read up front;
    an invalid path, an unreadable file, or content that is not one of the
    accepted image formats (PNG, JPEG, WebP — see :func:`sniff_image_format`)
    is logged and skipped, never failing the generation (§7.3), and ordinals
    are assigned only to survivors so "reference image N" always matches what
    is actually sent.

    Tokens are replaced with "the attached reference image" when exactly one
    attachment survives and no other image accompanies the request, otherwise
    with "reference image N", where N is the image's position among *all*
    images the API receives. ``extra_images`` counts non-attachment images
    sent ahead of the attachments (the edit-from-base PNG): they force the
    numbered form and shift the numbering, so a base plus one attachment
    cites that attachment as "reference image 2" — the position the model
    actually sees. A skipped candidate's tokens are removed outright. The
    input ``prompt`` (the stored, token-bearing form) is never modified.
    """
    candidates = _candidate_paths(junction_paths, _TOKEN_RE.findall(prompt))

    ordinals: dict[str, int] = {}
    reference_images: list[bytes] = []
    for path in candidates:
        try:
            data = attachment_file_path(storage_root, path).read_bytes()
        except OSError as exc:
            _skip(path, type(exc).__name__)
            continue
        if sniff_image_format(data) is None:
            _skip(path, "unsupported image format")
            continue
        reference_images.append(data)
        # Number by position in the full API input array: the edit base (when
        # present) occupies the leading slot(s), so the model's "image 1" is
        # the base, not the first attachment.
        ordinals[path] = len(reference_images) + extra_images

    singular = len(reference_images) == 1 and extra_images == 0

    def replace(match: re.Match[str]) -> str:
        try:
            path = normalize_attachment_path(match.group(1))
        except ValueError:
            return ""
        ordinal = ordinals.get(path)
        if ordinal is None:
            return ""
        return (
            "the attached reference image" if singular else f"reference image {ordinal}"
        )

    return ResolvedPrompt(
        prompt=_TOKEN_RE.sub(replace, prompt),
        reference_images=tuple(reference_images),
        paths=tuple(ordinals),
    )


def _candidate_paths(
    junction_paths: Iterable[str], token_paths: Iterable[str]
) -> list[str]:
    """Normalized candidate paths: junction order first, then token order."""
    seen: dict[str, None] = {}
    for source, paths in (("junction", junction_paths), ("token", token_paths)):
        for raw in paths:
            try:
                seen.setdefault(normalize_attachment_path(raw), None)
            except ValueError:
                _skip(raw, f"invalid {source} path")
    return list(seen)


def _skip(path: str, reason: str) -> None:
    # Paths and reasons are never secret (unlike API keys/ICS URLs, §18).
    logger.warning("prompt attachment skipped path=%r reason=%s", path, reason)
