from pathlib import Path

from app.images.db import (
    ImageSpec,
    attach_to_image,
    detach_from_image,
    find_record,
    get_or_create_attachment,
    get_or_create_record,
    get_record,
    list_all_attachments,
    list_image_attachments,
    list_records,
    open_db,
    update_prompt,
)

SPEC = ImageSpec(module="Calendar", item_description="Soccer", width=100, height=60)


def test_open_db_is_idempotent_and_creates_root(tmp_path: Path) -> None:
    root = tmp_path / "nested" / "storage"
    open_db(root).close()
    open_db(root).close()  # second run re-executes the DDL harmlessly
    assert (root / "sqlite.db").exists()


def test_same_logical_key_returns_same_row(tmp_path: Path) -> None:
    conn = open_db(tmp_path)
    first, first_created = get_or_create_record(conn, SPEC, "prompt A")
    second, second_created = get_or_create_record(conn, SPEC, "prompt B")
    assert first.id == second.id
    assert first_created and not second_created
    # The seed prompt applies only on creation; the stored prompt wins after.
    assert second.prompt == "prompt A"
    conn.close()


def test_null_variant_collides_with_itself(tmp_path: Path) -> None:
    # SQLite treats NULLs as distinct in plain unique indexes; the COALESCE
    # expression index must make variant=None a single logical key (§7.1).
    conn = open_db(tmp_path)
    ids = {get_or_create_record(conn, SPEC, "p")[0].id for _ in range(3)}
    assert len(ids) == 1
    conn.close()


def test_variant_and_size_produce_distinct_rows(tmp_path: Path) -> None:
    conn = open_db(tmp_path)
    base, _ = get_or_create_record(conn, SPEC, "p")
    bugbug, _ = get_or_create_record(
        conn, ImageSpec("Calendar", "Soccer", 100, 60, variant="bugbug"), "p"
    )
    wider, _ = get_or_create_record(conn, ImageSpec("Calendar", "Soccer", 200, 60), "p")
    assert len({base.id, bugbug.id, wider.id}) == 3
    conn.close()


def test_find_record_looks_up_without_creating(tmp_path: Path) -> None:
    conn = open_db(tmp_path)
    assert find_record(conn, SPEC) is None
    assert list_records(conn) == []  # the miss created nothing

    created, _ = get_or_create_record(conn, SPEC, "p")
    assert find_record(conn, SPEC) == created
    conn.close()


def test_find_record_discriminates_variant_from_null(tmp_path: Path) -> None:
    # The base (variant=None) row and its named variant are distinct logical
    # keys; a lookup for either must never return the other.
    variant_spec = ImageSpec("Calendar", "Soccer", 100, 60, variant="excited")
    conn = open_db(tmp_path)
    base, _ = get_or_create_record(conn, SPEC, "p")
    assert find_record(conn, variant_spec) is None

    variant, _ = get_or_create_record(conn, variant_spec, "p")
    assert find_record(conn, SPEC) == base
    assert find_record(conn, variant_spec) == variant
    conn.close()


def test_get_record_and_list_records(tmp_path: Path) -> None:
    conn = open_db(tmp_path)
    created, _ = get_or_create_record(conn, SPEC, "p")
    assert get_record(conn, created.id) == created
    assert get_record(conn, created.id + 999) is None
    assert list_records(conn) == [created]
    conn.close()


def test_update_prompt_persists(tmp_path: Path) -> None:
    conn = open_db(tmp_path)
    record, _ = get_or_create_record(conn, SPEC, "old")
    update_prompt(conn, record.id, "new")
    assert get_or_create_record(conn, SPEC, "ignored")[0].prompt == "new"
    conn.close()


def test_get_or_create_attachment_is_idempotent_by_path(tmp_path: Path) -> None:
    conn = open_db(tmp_path)
    first = get_or_create_attachment(conn, "styles/a.png")
    second = get_or_create_attachment(conn, "styles/a.png")
    other = get_or_create_attachment(conn, "styles/b.png")
    assert first == second
    assert other.id != first.id
    conn.close()


def test_attach_detach_round_trip(tmp_path: Path) -> None:
    conn = open_db(tmp_path)
    record, _ = get_or_create_record(conn, SPEC, "p")
    attachment = get_or_create_attachment(conn, "styles/a.png")

    attach_to_image(conn, record.id, attachment.id)
    attach_to_image(conn, record.id, attachment.id)  # double-submit is harmless
    assert list_image_attachments(conn, record.id) == [attachment]

    detach_from_image(conn, record.id, attachment.id)
    assert list_image_attachments(conn, record.id) == []
    # The attachment row itself survives a detach (it may be shared).
    assert list_all_attachments(conn) == [attachment]
    conn.close()


def test_detach_leaves_other_records_attached(tmp_path: Path) -> None:
    conn = open_db(tmp_path)
    first, _ = get_or_create_record(conn, SPEC, "p")
    second, _ = get_or_create_record(conn, ImageSpec("Calendar", "Piano", 100, 60), "p")
    shared = get_or_create_attachment(conn, "styles/shared.png")
    attach_to_image(conn, first.id, shared.id)
    attach_to_image(conn, second.id, shared.id)

    detach_from_image(conn, first.id, shared.id)
    assert list_image_attachments(conn, first.id) == []
    assert list_image_attachments(conn, second.id) == [shared]
    conn.close()


def test_list_image_attachments_ordered_by_attachment_id(tmp_path: Path) -> None:
    # Attachment-creation order, not attach order or path order: the ordinal
    # "reference image N" rewrites depend on this being stable.
    conn = open_db(tmp_path)
    record, _ = get_or_create_record(conn, SPEC, "p")
    zebra = get_or_create_attachment(conn, "zebra.png")
    apple = get_or_create_attachment(conn, "apple.png")
    attach_to_image(conn, record.id, apple.id)
    attach_to_image(conn, record.id, zebra.id)

    assert list_image_attachments(conn, record.id) == [zebra, apple]
    assert list_all_attachments(conn) == [apple, zebra]  # picker: by path
    conn.close()


def test_attachment_junction_cascades_on_image_delete(tmp_path: Path) -> None:
    conn = open_db(tmp_path)
    record, _ = get_or_create_record(conn, SPEC, "p")
    with conn:
        conn.execute(
            "INSERT INTO prompt_attachments (path) VALUES (?)", ("style/a.png",)
        )
        conn.execute(
            "INSERT INTO image_prompt_attachments (image_id, attachment_id)"
            " VALUES (?, 1)",
            (record.id,),
        )
        conn.execute("DELETE FROM images WHERE id = ?", (record.id,))
    assert (
        conn.execute("SELECT COUNT(*) FROM image_prompt_attachments").fetchone()[0] == 0
    )
    # The attachment itself survives (it may be shared across records).
    assert conn.execute("SELECT COUNT(*) FROM prompt_attachments").fetchone()[0] == 1
    conn.close()
