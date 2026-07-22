"""Pure caption rotation (spec §10.5): which caption a date shows, if any.

Kept free of I/O so render bytes stay a pure function of their inputs (§3.4):
the caption list, the date's pin, and the rotation pointer are inputs exactly
like the image store, and the *write* of a fresh pin lives in
:func:`app.captions.captions.assign_caption` (which runs this logic inside
its serializing transaction), never here.
"""

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class SelectedCaption:
    """The caption a date shows: its rotation index and text."""

    index: int
    text: str

    fresh: bool
    """True when this is a new assignment the caller should record - the date
    had no pin yet and ``index`` is the rotation's next caption."""


def select_caption(
    captions: Sequence[str], assigned_index: int | None, last_index: int | None
) -> SelectedCaption | None:
    """The caption a date shows given its pin and the rotation pointer (§10.5).

    - No captions: nothing to show (even for a previously pinned date - the
      list was emptied).
    - ``assigned_index`` set (the date was rendered with a caption before):
      that same caption again, whatever the pointer says now - so repeated
      renders of a date are stable however many other dates were rendered in
      between. The modulo guards against the list shrinking below the pin.
    - Otherwise the caption after ``last_index`` (the most recently assigned
      one, wrapping), or the first caption when nothing was ever assigned;
      ``fresh`` tells the caller to pin it.
    """
    if not captions:
        return None
    if assigned_index is not None:
        index = assigned_index % len(captions)
        return SelectedCaption(index=index, text=captions[index], fresh=False)
    index = 0 if last_index is None else (last_index + 1) % len(captions)
    return SelectedCaption(index=index, text=captions[index], fresh=True)
