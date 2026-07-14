from datetime import UTC, date, datetime, timedelta

from flask import Flask, abort, render_template, request

from app.calendar import CalendarFetchError, expand_events, fetch_ics
from app.comic import comic_border_path
from app.config import get_settings
from app.countdown import build_countdown, make_countdown_hero_resolver
from app.dates import render_days, resolve_date
from app.day_strip import build_day_strip
from app.images import (
    RenderedImage,
    admin_bp,
    generate_image_bytes,
    images_bp,
    make_calendar_icon_resolver,
)
from app.today import build_today
from app.tomorrow import build_tomorrow
from app.weather import (
    Condition,
    Outfit,
    WeatherFetchError,
    WeatherPanel,
    build_weather,
    fetch_forecast,
    override_high,
)
from app.weather.admin import weather_admin_bp


def _weather_overrides() -> tuple[Condition | None, Outfit | None, int | None]:
    """Parse the ``?weather_icon``/``?weather_outfit``/``?weather_temp`` debug
    args (§3.5) from the current request; a value outside the supported names
    (or a non-integer temp) aborts with a 400 rather than being ignored."""
    icon = request.args.get("weather_icon")
    outfit = request.args.get("weather_outfit")
    temp = request.args.get("weather_temp")
    try:
        return (
            Condition(icon) if icon else None,
            Outfit(outfit) if outfit else None,
            int(temp) if temp else None,
        )
    except ValueError:
        abort(400, description="invalid weather_* debug arg")


def create_app() -> Flask:
    app = Flask(__name__)
    settings = get_settings()

    app.add_template_global(comic_border_path, name="comic_border_path")
    # Injectable seams (mirror app.config["NOW"]): tests override these with fakes
    # so the suite never hits the network or the developer's real storage.
    app.config.setdefault("FETCH_ICS", fetch_ics)
    app.config.setdefault("FETCH_FORECAST", fetch_forecast)
    app.config.setdefault("GENERATE_IMAGE_BYTES", generate_image_bytes)
    app.config.setdefault("APP_STORAGE_PATH", settings.app_storage_path)

    app.register_blueprint(images_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(weather_admin_bp)

    @app.get("/")
    def index() -> str:
        return render_template("index.html")

    @app.get("/render")
    def render() -> str:
        now = app.config.get("NOW") or datetime.now(UTC)
        target = resolve_date(request.args.get("date"), now=now, tz=settings.timezone)
        try:
            ics_text = app.config["FETCH_ICS"](settings.family_calendar_ics_url)
            events = expand_events(ics_text, render_days(target), settings.timezone)
        except (CalendarFetchError, ValueError) as exc:
            app.logger.warning("family calendar render failed: %s", type(exc).__name__)
            abort(500)
        condition_override, outfit_override, temp_override = _weather_overrides()
        # Weather degrades softly: on a fetch failure (or a target outside the
        # forecast horizon) the subpanels render empty but keep their
        # footprint, rather than failing the whole board. The message never
        # carries the request URL (it holds the API key). One fetch serves
        # both panels — the returned mapping covers the whole horizon — and
        # with every weather_* debug arg set (§3.5) nothing real is left to
        # show, so the fetch is skipped outright.
        forecast: dict = {}
        if None in (condition_override, outfit_override, temp_override):
            try:
                forecast = app.config["FETCH_FORECAST"](
                    settings.google_maps_api_key, settings.latitude, settings.longitude
                )
            except WeatherFetchError as exc:
                app.logger.warning("weather fetch failed: %s", type(exc).__name__)

        def panel_weather(day: date, slot: int) -> WeatherPanel | None:
            """One subpanel's view model, debug overrides applied (§3.5).

            Both panels seed the kid flip-flop off the same target date; the
            slot offset keeps them on different kids the same day (§ Weather).
            """
            day_forecast = override_high(forecast.get(day), temp_override)
            if day_forecast is None:
                return None
            return build_weather(
                target,
                day_forecast,
                settings.kids,
                slot=slot,
                condition_override=condition_override,
                outfit_override=outfit_override,
            )

        weather = panel_weather(target, 0)
        tomorrow_weather = panel_weather(target + timedelta(days=1), 1)
        # Missing AI images are generated inline here (§3.6); an individual
        # image failure falls back to a chip (§7.3), never a 500. One resolver
        # is shared across modules so the strip and the Today/Tomorrow rows
        # reuse the same image records and ?debug_images= stays deduplicated.
        rendered_images: list[RenderedImage] = []
        resolver = make_calendar_icon_resolver(rendered_images)
        strip = build_day_strip(target, events, settings.kids, resolver)
        today_panel = build_today(target, events, settings.kids, resolver)
        tomorrow_panel = build_tomorrow(target, events, settings.kids, resolver)
        countdown_panel = build_countdown(
            target, events, hero_resolver=make_countdown_hero_resolver(rendered_images)
        )
        debug_images = (
            rendered_images if request.args.get("debug_images") == "1" else None
        )
        return render_template(
            "board.html",
            strip=strip,
            today_panel=today_panel,
            tomorrow_panel=tomorrow_panel,
            countdown_panel=countdown_panel,
            weather=weather,
            tomorrow_weather=tomorrow_weather,
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
