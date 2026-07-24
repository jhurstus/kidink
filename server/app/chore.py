"""View model for the Chores panel (spec §14).

Today's chores - the ``chore:``-prefixed calendar events for the target date -
rendered as icon + kid-badge + title rows, mechanically identical to a
today/tomorrow list (no chronological reorder, §14). Two orderings drive the
panel: chores are *ranked* for selection by ``interesting`` then kid then title
(so a cap keeps the most interesting), and *presented* by kid then ``interesting``
then title (so each kid's chores group together). The row/badge/icon machinery is
the shared :mod:`app.event_rows`; only the ``Chores`` icon cache namespace differs
(see :func:`app.images.make_chore_icon_resolver`).

Layout: up to a column's worth of chores (two) fill a single block spanning the
panel; a third or beyond spills the presented list into two columns - a 2x2 grid
when full - the top column-full in the first and the rest in the second, capped
at four chores total (§14).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from app.calendar import CalendarEvent
from app.config import Kid
from app.event_rows import (
    EventRow,
    IconResolver,
    assigned_kids,
    build_row,
    no_icons,
    resolve_icons,
)

# Row-budget geometry (§4.1), mirroring static/css/chore.css - keep in sync. The
# top-aligned list fills what remains of the ~291px paper (the ~449x299.5 grid
# cell minus the frame border, board.css) after the 91px top padding seating the
# first row on the second ruled line: 291 - 91 = 200, plus one 38px row gap
# since the last row has no trailing blank line: 200 + 38 = 238. This is one
# column's row capacity (two): it both caps each column and is the threshold at
# which a single block spills into two columns.
_AVAILABLE_H = 238
# One event row spans two 38px rulings (76px, so a title can wrap to a second
# line) plus the 38px flex gap - a blank ruling - before the next row.
_ROW_H = 114


@dataclass(frozen=True)
class ChorePanel:
    """The complete view model rendered by ``templates/modules/chore.html``."""

    seed: int
    """Border seed for the panel (date-pure, §3.4)."""

    rows: list[EventRow]
    """Single-block mode: up to a column's worth of chores, presented by kid then
    ``interesting`` then title (§14). Empty in two-column mode and when there are
    no chores."""

    columns: list[list[EventRow]] | None = None
    """Two-column mode (§14): the presented chores split across two columns - the
    top column-full in the first, the rest spilling into the second - when there
    are more than a single column holds. ``None`` otherwise."""


def _rank_key(event: CalendarEvent, kids: Sequence[Kid]) -> tuple:
    """Selection rank (§14): ``interesting`` desc, then kid, then title.

    Kid is the event's assigned-kid indices (config order); an event's rank is
    what a cap keeps, so the most interesting chores survive.
    """
    return (-event.overrides.interesting, assigned_kids(event, kids), event.title)


def _display_key(event: CalendarEvent, kids: Sequence[Kid]) -> tuple:
    """Presentation order (§14): kid, then ``interesting`` desc, then title.

    Grouping by kid first keeps each kid's chores together in the rendered list.
    """
    return (assigned_kids(event, kids), -event.overrides.interesting, event.title)


def build_chore(
    target: date,
    events: Iterable[CalendarEvent] = (),
    kids: Sequence[Kid] = (),
    icon_resolver: IconResolver = no_icons,
) -> ChorePanel:
    """Build the Chores panel view model for the resolved render date ``target``.

    ``events`` are the render window's expanded calendar events (see
    :func:`app.calendar.expand_events`); only the target date's ``chore:`` events
    are shown (the ``chore:`` prefix is already stripped from the title by the
    parser). ``kids`` (config order, :class:`app.config.Kid`) drives the row
    badges (§8) and both orderings. Surviving rows' icons are resolved through
    ``icon_resolver`` in a single batch - so missing images can generate
    concurrently - after the cap, never for dropped chores (the default resolves
    nothing, keeping the view model a pure function of its inputs).
    """
    chores = [e for e in events if e.local_day == target and e.is_chore]
    # +7 is the Chores panel's reserved seed slot (after Dinner's +6, before
    # Joke's +8), keeping its border ripple distinct on the page (§3.4).
    seed = target.toordinal() + 7
    per_column = _AVAILABLE_H // _ROW_H  # rows a single column holds

    if len(chores) > per_column:
        # Two columns: select which chores survive by rank (interesting, kid,
        # title), capped at two full columns; then present the survivors by kid,
        # interesting, title, spilling past the first column into the second.
        selected = sorted(chores, key=lambda e: _rank_key(e, kids))[: 2 * per_column]
        shown = sorted(selected, key=lambda e: _display_key(e, kids))
        icons = resolve_icons(shown, icon_resolver)
        rows = [build_row(e, kids, icons) for e in shown]
        return ChorePanel(
            seed=seed, rows=[], columns=[rows[:per_column], rows[per_column:]]
        )

    # Single block: the whole (short) list, presented by kid, interesting, title.
    ordered = sorted(chores, key=lambda e: _display_key(e, kids))
    icons = resolve_icons(ordered, icon_resolver)
    return ChorePanel(seed=seed, rows=[build_row(e, kids, icons) for e in ordered])
