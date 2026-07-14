from pathlib import Path

import pytest

from app.images.attachments import (
    attachment_file_path,
    default_attachment_paths,
    normalize_attachment_path,
    prompt_images_root,
    resolve_prompt_attachments,
    sniff_image_format,
)


def _png(tag: bytes = b"") -> bytes:
    """Bytes that sniff as PNG; ``tag`` keeps fixtures distinguishable."""
    return b"\x89PNG\r\n\x1a\n" + tag


def _jpg(tag: bytes = b"") -> bytes:
    return b"\xff\xd8\xff" + tag


def _webp(tag: bytes = b"") -> bytes:
    return b"RIFF\x00\x00\x00\x00WEBP" + tag


def _write(storage_root: Path, rel_path: str, data: bytes) -> None:
    file = prompt_images_root(storage_root) / rel_path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(data)


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("styles/a.png", "styles/a.png"),
        ("  styles/a.png  ", "styles/a.png"),  # surrounding whitespace stripped
        ("styles//a.png", "styles/a.png"),  # doubled separators collapse
        ("a.png", "a.png"),
    ],
)
def test_normalize_accepts_relative_paths(raw: str, normalized: str) -> None:
    assert normalize_attachment_path(raw) == normalized


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "/abs/a.png",
        "../x.png",
        "a/../b.png",
        ".",
        "..",
        "a\\b.png",
        "a\x00b",
    ],
)
def test_normalize_rejects_escapes_and_empties(raw: str) -> None:
    with pytest.raises(ValueError, match="invalid attachment path"):
        normalize_attachment_path(raw)


def test_attachment_file_path_stays_under_prompt_images(tmp_path: Path) -> None:
    assert attachment_file_path(tmp_path, "styles/a.png") == (
        tmp_path / "prompt_images" / "styles" / "a.png"
    )
    with pytest.raises(ValueError):
        attachment_file_path(tmp_path, "../sqlite.db")


def test_sniff_image_format_by_magic_number() -> None:
    assert sniff_image_format(_png()) == ("png", "image/png")
    assert sniff_image_format(_jpg()) == ("jpg", "image/jpeg")
    assert sniff_image_format(_webp()) == ("webp", "image/webp")
    assert sniff_image_format(b"GIF89a...") is None
    assert sniff_image_format(b"not an image") is None
    assert sniff_image_format(b"") is None
    assert sniff_image_format(b"RIFF\x00\x00\x00\x00WAVE") is None  # RIFF != WebP


def test_default_paths_absent_dir_is_empty(tmp_path: Path) -> None:
    assert default_attachment_paths(tmp_path, "Calendar") == []


def test_default_paths_sorted_image_files_only(tmp_path: Path) -> None:
    # Module name is lowercased; dotfiles, subdirectories, and non-image
    # extensions are ignored; the sorted-by-filename order is what makes
    # seeding deterministic.
    _write(tmp_path, "defaults/calendar/zebra.png", _png(b"z"))
    _write(tmp_path, "defaults/calendar/apple.JPG", _jpg(b"a"))
    _write(tmp_path, "defaults/calendar/misc.webp", _webp(b"m"))
    _write(tmp_path, "defaults/calendar/.hidden.png", _png(b"h"))
    _write(tmp_path, "defaults/calendar/notes.txt", b"not an image")
    _write(tmp_path, "defaults/calendar/nested/deep.png", _png(b"d"))

    assert default_attachment_paths(tmp_path, "Calendar") == [
        "defaults/calendar/apple.JPG",
        "defaults/calendar/misc.webp",
        "defaults/calendar/zebra.png",
    ]


def test_resolve_without_attachments_is_a_no_op(tmp_path: Path) -> None:
    resolved = resolve_prompt_attachments("plain prompt", [], storage_root=tmp_path)
    assert resolved.prompt == "plain prompt"
    assert resolved.reference_images == ()
    assert resolved.paths == ()


def test_resolve_junction_only_leaves_prompt_untouched(tmp_path: Path) -> None:
    _write(tmp_path, "styles/a.png", _png(b"a"))
    resolved = resolve_prompt_attachments(
        "match the style", ["styles/a.png"], storage_root=tmp_path
    )
    assert resolved.prompt == "match the style"
    assert resolved.reference_images == (_png(b"a"),)
    assert resolved.paths == ("styles/a.png",)


def test_resolve_accepts_jpeg_and_webp_content(tmp_path: Path) -> None:
    _write(tmp_path, "styles/photo.jpg", _jpg(b"p"))
    _write(tmp_path, "styles/art.webp", _webp(b"w"))
    resolved = resolve_prompt_attachments(
        "Use {{styles/photo.jpg}} and {{styles/art.webp}}.",
        [],
        storage_root=tmp_path,
    )
    assert resolved.prompt == "Use reference image 1 and reference image 2."
    assert resolved.reference_images == (_jpg(b"p"), _webp(b"w"))


def test_resolve_skips_unsupported_content(tmp_path: Path) -> None:
    # A non-image (or unsupported format) file never reaches the API, no
    # matter what its filename claims; its tokens drop like a missing file's.
    _write(tmp_path, "styles/fake.png", b"GIF89a not actually a png")
    _write(tmp_path, "styles/real.png", _png(b"r"))
    resolved = resolve_prompt_attachments(
        "Use {{styles/fake.png}} then {{styles/real.png}}.",
        [],
        storage_root=tmp_path,
    )
    assert resolved.prompt == "Use  then the attached reference image."
    assert resolved.reference_images == (_png(b"r"),)
    assert resolved.paths == ("styles/real.png",)


def test_single_token_uses_singular_wording(tmp_path: Path) -> None:
    _write(tmp_path, "styles/dog.png", _png(b"dog"))
    resolved = resolve_prompt_attachments(
        "Draw a dog like {{ styles/dog.png }}.", [], storage_root=tmp_path
    )
    assert resolved.prompt == "Draw a dog like the attached reference image."
    assert resolved.reference_images == (_png(b"dog"),)


def test_multiple_attachments_use_numbered_wording(tmp_path: Path) -> None:
    _write(tmp_path, "styles/a.png", _png(b"a"))
    _write(tmp_path, "styles/b.png", _png(b"b"))
    resolved = resolve_prompt_attachments(
        "Blend {{styles/b.png}} with {{styles/a.png}} and {{styles/b.png}}.",
        [],
        storage_root=tmp_path,
    )
    # First-appearance order: b is reference 1, a is 2; the repeat shares 1.
    assert resolved.prompt == (
        "Blend reference image 1 with reference image 2 and reference image 1."
    )
    assert resolved.reference_images == (_png(b"b"), _png(b"a"))


def test_extra_images_shift_and_force_numbered_wording(tmp_path: Path) -> None:
    # With an edit-from-base PNG in the same request, the base occupies the
    # model's first image slot: singular wording would be ambiguous and the
    # numbering must count the base, so the lone attachment is image 2.
    _write(tmp_path, "styles/a.png", _png(b"a"))
    resolved = resolve_prompt_attachments(
        "Like {{styles/a.png}}.", [], storage_root=tmp_path, extra_images=1
    )
    assert resolved.prompt == "Like reference image 2."


def test_extra_images_shift_every_ordinal(tmp_path: Path) -> None:
    _write(tmp_path, "styles/a.png", _png(b"a"))
    _write(tmp_path, "styles/b.png", _png(b"b"))
    resolved = resolve_prompt_attachments(
        "Mix {{styles/a.png}} into {{styles/b.png}}.",
        [],
        storage_root=tmp_path,
        extra_images=1,
    )
    assert resolved.prompt == "Mix reference image 2 into reference image 3."
    assert resolved.reference_images == (_png(b"a"), _png(b"b"))


def test_junction_paths_come_before_token_paths(tmp_path: Path) -> None:
    _write(tmp_path, "defaults/calendar/style.png", _png(b"default"))
    _write(tmp_path, "styles/extra.png", _png(b"extra"))
    resolved = resolve_prompt_attachments(
        "Also use {{styles/extra.png}}.",
        ["defaults/calendar/style.png"],
        storage_root=tmp_path,
    )
    assert resolved.prompt == "Also use reference image 2."
    assert resolved.reference_images == (_png(b"default"), _png(b"extra"))


def test_path_in_junction_and_token_dedups_to_junction_position(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "styles/a.png", _png(b"a"))
    _write(tmp_path, "styles/b.png", _png(b"b"))
    resolved = resolve_prompt_attachments(
        "Use {{styles/b.png}} and {{styles/a.png}}.",
        ["styles/a.png"],
        storage_root=tmp_path,
    )
    # a holds its junction slot (1); b is appended after (2).
    assert resolved.prompt == "Use reference image 2 and reference image 1."
    assert resolved.reference_images == (_png(b"a"), _png(b"b"))


def test_missing_file_is_skipped_and_ordinals_renumber(tmp_path: Path) -> None:
    _write(tmp_path, "styles/real.png", _png(b"real"))
    resolved = resolve_prompt_attachments(
        "Use {{styles/ghost.png}} then {{styles/real.png}}.",
        ["styles/gone.png"],
        storage_root=tmp_path,
    )
    # Both missing files drop before numbering, so the survivor is number 1 —
    # and with one attachment left, the singular wording applies.
    assert resolved.prompt == "Use  then the attached reference image."
    assert resolved.reference_images == (_png(b"real"),)
    assert resolved.paths == ("styles/real.png",)


def test_invalid_token_path_is_dropped(tmp_path: Path) -> None:
    resolved = resolve_prompt_attachments(
        "Nice try {{../../etc/passwd}}.", [], storage_root=tmp_path
    )
    assert resolved.prompt == "Nice try ."
    assert resolved.reference_images == ()
