from datetime import date
from pathlib import Path

from flask import Flask

from app import create_app
from app.captions.captions import (
    add_captions,
    get_last_index,
    latest_assignment,
    list_captions,
    open_captions_db,
    record_assignment,
)


def _app(tmp_path: Path) -> Flask:
    app = create_app()
    app.config["APP_STORAGE_PATH"] = tmp_path
    return app


def _seed(tmp_path: Path, texts: list[str]) -> None:
    conn = open_captions_db(tmp_path)
    try:
        add_captions(conn, texts)
    finally:
        conn.close()


def _record(tmp_path: Path, day: date, index: int) -> None:
    conn = open_captions_db(tmp_path)
    try:
        record_assignment(conn, day, index)
    finally:
        conn.close()


def test_empty_store_shows_a_prompt_to_add(tmp_path: Path) -> None:
    text = _app(tmp_path).test_client().get("/admin/captions").text
    assert "No captions yet" in text
    assert "Add captions" in text
    assert "Never shown yet" in text


def test_lists_captions_and_flags_the_next_one(tmp_path: Path) -> None:
    _seed(tmp_path, ["caption A", "caption B", "caption C"])

    text = _app(tmp_path).test_client().get("/admin/captions").text

    assert "caption A" in text
    assert "caption C" in text
    # With nothing assigned yet the first caption is next (§10.5).
    assert text.count('class="next"') == 1
    assert text.index('class="next"') < text.index("caption A")


def test_next_flag_follows_the_pointer_and_wraps(tmp_path: Path) -> None:
    _seed(tmp_path, ["caption A", "caption B"])
    _record(tmp_path, date(2026, 7, 21), 1)

    text = _app(tmp_path).test_client().get("/admin/captions").text

    # Pointer on the last caption -> the next one wraps to the first.
    assert text.count('class="next"') == 1
    assert text.index('class="next"') < text.index("caption A")


def test_state_line_shows_the_latest_assignment(tmp_path: Path) -> None:
    _seed(tmp_path, ["caption A", "caption B"])
    _record(tmp_path, date(2026, 7, 21), 1)

    text = _app(tmp_path).test_client().get("/admin/captions").text

    assert "Last assigned 2026-07-21" in text
    assert "caption B" in text


def test_state_line_survives_a_deleted_row(tmp_path: Path) -> None:
    _seed(tmp_path, ["caption A"])
    _record(tmp_path, date(2026, 7, 21), 3)

    text = _app(tmp_path).test_client().get("/admin/captions").text

    assert "caption since deleted" in text


def test_add_via_textarea_appends(tmp_path: Path) -> None:
    client = _app(tmp_path).test_client()

    response = client.post(
        "/admin/captions/add", data={"captions": "caption A\n\ncaption B"}
    )
    assert response.status_code == 302

    conn = open_captions_db(tmp_path)
    try:
        assert [c.text for c in list_captions(conn)] == ["caption A", "caption B"]
    finally:
        conn.close()


def test_edit_updates_text(tmp_path: Path) -> None:
    _seed(tmp_path, ["caption A"])
    conn = open_captions_db(tmp_path)
    caption_id = list_captions(conn)[0].id
    conn.close()
    client = _app(tmp_path).test_client()

    response = client.post(
        f"/admin/captions/{caption_id}/edit", data={"text": "caption A!"}
    )
    assert response.status_code == 302

    conn = open_captions_db(tmp_path)
    try:
        assert [c.text for c in list_captions(conn)] == ["caption A!"]
    finally:
        conn.close()


def test_empty_edit_deletes_the_caption(tmp_path: Path) -> None:
    _seed(tmp_path, ["caption A"])
    conn = open_captions_db(tmp_path)
    caption_id = list_captions(conn)[0].id
    conn.close()
    client = _app(tmp_path).test_client()

    client.post(f"/admin/captions/{caption_id}/edit", data={"text": "   "})

    conn = open_captions_db(tmp_path)
    try:
        assert list_captions(conn) == []
    finally:
        conn.close()


def test_delete_removes_the_caption(tmp_path: Path) -> None:
    _seed(tmp_path, ["caption A", "caption B"])
    conn = open_captions_db(tmp_path)
    caption_id = list_captions(conn)[0].id
    conn.close()
    client = _app(tmp_path).test_client()

    response = client.post(f"/admin/captions/{caption_id}/delete")
    assert response.status_code == 302

    conn = open_captions_db(tmp_path)
    try:
        assert [c.text for c in list_captions(conn)] == ["caption B"]
    finally:
        conn.close()


def test_reset_clears_the_rotation(tmp_path: Path) -> None:
    _seed(tmp_path, ["caption A"])
    _record(tmp_path, date(2026, 7, 21), 0)
    client = _app(tmp_path).test_client()

    response = client.post("/admin/captions/state/reset", follow_redirects=True)

    assert "rotation reset" in response.text
    conn = open_captions_db(tmp_path)
    try:
        assert get_last_index(conn) is None
        assert latest_assignment(conn) is None
    finally:
        conn.close()
