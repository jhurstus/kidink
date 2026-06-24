from datetime import UTC, datetime

from flask import Flask, render_template, request

from app.comic import comic_border_path
from app.config import get_settings
from app.dates import resolve_date
from app.day_strip import build_day_strip


def create_app() -> Flask:
    app = Flask(__name__)
    settings = get_settings()

    app.add_template_global(comic_border_path, name="comic_border_path")

    @app.get("/")
    def index() -> str:
        return render_template("index.html")

    @app.get("/render")
    def render() -> str:
        now = app.config.get("NOW") or datetime.now(UTC)
        target = resolve_date(request.args.get("date"), now=now, tz=settings.timezone)
        return render_template("board.html", strip=build_day_strip(target))

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
            "border_color": request.args.get(
                "border_color", default="rgb(40,38,34)", type=str
            ),
            "radius": request.args.get("radius", default=16, type=int),
            "mid_width": request.args.get("mid_width", default=12, type=int),
            "corner_width": request.args.get("corner_width", default=3, type=int),
            "roughness": request.args.get("roughness", default=0, type=int),
            "frequency": request.args.get("frequency", default=6, type=int),
            "seed": request.args.get("seed", default=173, type=int),
        }
        return render_template("panel.html", **params)

    return app
