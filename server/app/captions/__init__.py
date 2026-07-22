"""Caption module (spec §10.5): the weather kid's rotating speech-bubble line.

Public API:

- :func:`make_caption_provider` - the render route's caption source for
  ``build_today`` (reads the date's pin, or assigns and records the
  rotation's next caption on a date's first eligible render).
- :func:`select_caption` / :class:`SelectedCaption` - the pure rotation.
- :data:`captions_admin_bp` - the ``/admin/captions`` blueprint (the caption
  list and pins are managed there, :mod:`app.captions.admin`).
"""

from app.captions.admin import captions_admin_bp
from app.captions.provider import make_caption_provider
from app.captions.select import SelectedCaption, select_caption

__all__ = [
    "SelectedCaption",
    "captions_admin_bp",
    "make_caption_provider",
    "select_caption",
]
