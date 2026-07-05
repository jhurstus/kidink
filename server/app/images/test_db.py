from pathlib import Path

from app.images.db import (
    ImageSpec,
    get_or_create_record,
    get_record,
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
    first = get_or_create_record(conn, SPEC, "prompt A")
    second = get_or_create_record(conn, SPEC, "prompt B")
    assert first.id == second.id
    # The seed prompt applies only on creation; the stored prompt wins after.
    assert second.prompt == "prompt A"
    conn.close()


def test_null_variant_collides_with_itself(tmp_path: Path) -> None:
    # SQLite treats NULLs as distinct in plain unique indexes; the COALESCE
    # expression index must make variant=None a single logical key (§7.1).
    conn = open_db(tmp_path)
    ids = {get_or_create_record(conn, SPEC, "p").id for _ in range(3)}
    assert len(ids) == 1
    conn.close()


def test_variant_and_size_produce_distinct_rows(tmp_path: Path) -> None:
    conn = open_db(tmp_path)
    base = get_or_create_record(conn, SPEC, "p")
    bugbug = get_or_create_record(
        conn, ImageSpec("Calendar", "Soccer", 100, 60, variant="bugbug"), "p"
    )
    wider = get_or_create_record(conn, ImageSpec("Calendar", "Soccer", 200, 60), "p")
    assert len({base.id, bugbug.id, wider.id}) == 3
    conn.close()


def test_get_record_and_list_records(tmp_path: Path) -> None:
    conn = open_db(tmp_path)
    created = get_or_create_record(conn, SPEC, "p")
    assert get_record(conn, created.id) == created
    assert get_record(conn, created.id + 999) is None
    assert list_records(conn) == [created]
    conn.close()


def test_update_prompt_persists(tmp_path: Path) -> None:
    conn = open_db(tmp_path)
    record = get_or_create_record(conn, SPEC, "old")
    update_prompt(conn, record.id, "new")
    assert get_or_create_record(conn, SPEC, "ignored").prompt == "new"
    conn.close()


def test_attachment_junction_cascades_on_image_delete(tmp_path: Path) -> None:
    conn = open_db(tmp_path)
    record = get_or_create_record(conn, SPEC, "p")
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
