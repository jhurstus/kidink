"""Weather image-inventory admin page.

Shows every hand-made weather asset the board can reference (§ Weather
inventory: seven condition icons plus four outfit figures per configured kid)
at the exact pinned display sizes from ``weather.css``, in a grid sized to fit
the 1600×1200 panel — so the full set can be eyeballed on the device itself.
Assets are static files under ``static/img/weather/`` (§7.6); ones that don't
exist yet simply render as broken images.

Like the image admin (§7.4), this is unauthenticated and for the trusted home
LAN only.
"""

from flask import Blueprint, render_template

from app.config import get_settings
from app.weather.view import Condition, Outfit, figure_name

weather_admin_bp = Blueprint("weather_admin", __name__)


@weather_admin_bp.get("/admin/weather")
def admin_weather() -> str:
    """The full § Weather static-image inventory in one grid."""
    kid_rows = [
        {
            "name": kid.name,
            "figures": [figure_name(i, outfit) for outfit in Outfit],
        }
        for i, kid in enumerate(get_settings().kids)
    ]
    return render_template(
        "admin/weather.html",
        conditions=[condition.value for condition in Condition],
        kid_rows=kid_rows,
    )
