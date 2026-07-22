"""The render route's caption provider (spec §10.5): read, select, pin.

Bridges the pure rotation (:mod:`app.captions.select`) to the stored pins:
the returned callable is what ``build_today`` invokes - at most once, and only
on caption-eligible days (:func:`app.today.caption_eligible`), so a caption is
never consumed by a day whose layout has no room for the bubble.
"""

from collections.abc import Callable
from datetime import date
from pathlib import Path

from app.captions.captions import (
    get_assignment,
    get_last_index,
    list_captions,
    open_captions_db,
    record_assignment,
)
from app.captions.select import select_caption


def make_caption_provider(storage_root: Path, target: date) -> Callable[[], str | None]:
    """A zero-arg caption source for ``build_today``'s ``caption_provider``.

    The callable reads ``target``'s pin and the rotation pointer, selects the
    caption (:func:`select_caption`), and - when the date had no pin yet -
    records the new one. *Every* caption-eligible render pins its date:
    device renders, ``?date=`` debug renders (§3.5), and warm-up prerenders
    (§3.6) alike, so whatever a preview shows is what the date will keep
    showing (the pin is memoized on first use, exactly like a §7.1 image
    record). Rotation therefore follows assignment order, not calendar order.

    Never creates storage: a storage root without a ``sqlite.db`` (fresh
    install, storage-less test app) simply has no captions, and ``/render``
    must not conjure a database just to learn that.
    """

    def provide() -> str | None:
        if not (storage_root / "sqlite.db").exists():  # the open_db filename
            return None
        conn = open_captions_db(storage_root)
        try:
            captions = [caption.text for caption in list_captions(conn)]
            selected = select_caption(
                captions, get_assignment(conn, target), get_last_index(conn)
            )
            if selected is None:
                return None
            if selected.fresh:
                record_assignment(conn, target, selected.index)
            return selected.text
        finally:
            conn.close()

    return provide
