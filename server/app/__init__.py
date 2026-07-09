from datetime import UTC, datetime

from flask import Flask, abort, render_template, request

from app.calendar import CalendarFetchError, expand_events, fetch_ics
from app.comic import comic_border_path
from app.config import get_settings
from app.dates import resolve_date, week_of
from app.day_strip import build_day_strip
from app.images import (
    RenderedImage,
    admin_bp,
    generate_image_bytes,
    images_bp,
    make_calendar_icon_resolver,
)
from app.today import build_today


def create_app() -> Flask:
    app = Flask(__name__)
    settings = get_settings()

    app.add_template_global(comic_border_path, name="comic_border_path")
    # Injectable seams (mirror app.config["NOW"]): tests override these with fakes
    # so the suite never hits the network or the developer's real storage.
    app.config.setdefault("FETCH_ICS", fetch_ics)
    app.config.setdefault("GENERATE_IMAGE_BYTES", generate_image_bytes)
    app.config.setdefault("APP_STORAGE_PATH", settings.app_storage_path)

    app.register_blueprint(images_bp)
    app.register_blueprint(admin_bp)

    @app.get("/")
    def index() -> str:
        return render_template("index.html")

    @app.get("/render")
    def render() -> str:
        now = app.config.get("NOW") or datetime.now(UTC)
        target = resolve_date(request.args.get("date"), now=now, tz=settings.timezone)
        try:
            ics_text = app.config["FETCH_ICS"](settings.family_calendar_ics_url)
            events = expand_events(ics_text, week_of(target), settings.timezone)
        except (CalendarFetchError, ValueError) as exc:
            app.logger.warning("family calendar render failed: %s", type(exc).__name__)
            abort(500)
        # Missing AI images are generated inline here (§3.6); an individual
        # image failure falls back to a chip (§7.3), never a 500. One resolver
        # is shared across modules so the strip and the Today rows reuse the
        # same image records and ?debug_images= stays deduplicated.
        rendered_images: list[RenderedImage] = []
        resolver = make_calendar_icon_resolver(rendered_images)
        strip = build_day_strip(target, events, settings.kids, resolver)
        today_panel = build_today(target, events, settings.kids, resolver)
        debug_images = (
            rendered_images if request.args.get("debug_images") == "1" else None
        )
        return render_template(
            "board.html",
            strip=strip,
            today_panel=today_panel,
            debug_images=debug_images,
        )

    @app.get("/panel")
    def panel() -> str:
        """
        Developer playground for the comic-panel primitives (halftone shading +
        hand-drawn border).
        """
        params = {
            "width": request.args.get("width", default=1520, type=int),
            "height": request.args.get("height", default=190, type=int),
            "bg": request.args.get("bg", default="rgb(225,220,202)", type=str),
            "dot_color": request.args.get(
                "dot_color", default="rgb(187,180,162)", type=str
            ),
            "dot_size": request.args.get("dot_size", default=6, type=int),
            "offset": request.args.get("offset", default=0, type=int),
            "transparency": request.args.get("transparency", default=0.0, type=float),
            "max_fill": request.args.get("max_fill", default=0.42, type=float),
            "origin_angle": request.args.get("origin_angle", default="90deg", type=str),
            "magnitude": request.args.get("magnitude", default="60%", type=str),
            "border_color": request.args.get("border_color", default="#000", type=str),
            "radius": request.args.get("radius", default=16, type=int),
            "mid_width": request.args.get("mid_width", default=12, type=int),
            "corner_width": request.args.get("corner_width", default=3, type=int),
            "roughness": request.args.get("roughness", default=0, type=int),
            "frequency": request.args.get("frequency", default=6, type=int),
            "seed": request.args.get("seed", default=173, type=int),
        }
        return render_template("panel.html", **params)

    return app
