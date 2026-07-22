"""The render route's caption provider (spec §10.5): read, select, pin.

Bridges the panel build to the caption store: the returned callable is what
``build_today`` invokes - at most once, and only on caption-eligible days
(:func:`app.today.caption_eligible`), so a caption is never consumed by a day
whose layout has no room for the bubble.
"""

from collections.abc import Callable
from datetime import date
from pathlib import Path

from app.captions.captions import assign_caption, open_captions_db


def make_caption_provider(storage_root: Path, target: date) -> Callable[[], str | None]:
    """A zero-arg caption source for ``build_today``'s ``caption_provider``.

    The callable resolves ``target``'s caption through
    :func:`app.captions.captions.assign_caption` - one atomic lookup-or-pin,
    serialized against concurrent renders. *Every* caption-eligible render
    pins its date: device renders, ``?date=`` debug renders (§3.5), and
    warm-up prerenders (§3.6) alike, so whatever a preview shows is what the
    date will keep showing (the pin is memoized on first use, exactly like a
    §7.1 image record). Rotation therefore follows assignment order, not
    calendar order.

    Never creates storage: a storage root without a ``sqlite.db`` (fresh
    install, storage-less test app) simply has no captions, and ``/render``
    must not conjure a database just to learn that.
    """

    def provide() -> str | None:
        if not (storage_root / "sqlite.db").exists():  # the open_db filename
            return None
        conn = open_captions_db(storage_root)
        try:
            selected = assign_caption(conn, target)
            return selected.text if selected is not None else None
        finally:
            conn.close()

    return provide
