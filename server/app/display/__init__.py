"""The device-facing `/display` endpoint (spec §3.1-3.3).

Public API:

- :data:`display_bp` — blueprint serving ``GET /display``.
"""

from app.display.routes import display_bp

__all__ = ["display_bp"]
