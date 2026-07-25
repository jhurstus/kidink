"""The device-facing `/display` endpoint (§3.1-3.3, steps 5-7)."""

import hashlib

from flask import Blueprint, Response, abort, current_app, request
from playwright.sync_api import Error as PlaywrightError

from app.display.pipeline import indices_to_png, screenshot_to_indices
from app.eink.pack import pack_pixels
from app.eink.screenshot import CaptureHTTPError

display_bp = Blueprint("display", __name__)

_WIDTH, _HEIGHT = 1600, 1200
# 2x + BOX downscale feeds the quantizer the same anti-aliased edges the
# on-panel tests validated (§3.3 step 5, eink-demo §4).
_SUPERSAMPLE = 2
# A cold image cache can push /render well past a minute (§3.6 mitigates).
_TIMEOUT_MS = 300_000


@display_bp.get("/display")
def display() -> Response:
    """Screenshot `/render` and serve the packed 4bpp device buffer.

    Chromium navigates to this server's own `/render` URL (every query arg
    forwarded), so the HTML and all its assets load over loopback. `?raw=1`
    and `?quantize=1` substitute PNG debug views (§3.5). The `ETag` hashes
    the served bytes; `If-None-Match` gets a `304` (§3.1).
    """
    qs = request.query_string.decode()
    url = f"{request.host_url}render" + (f"?{qs}" if qs else "")
    capture = current_app.config["CAPTURE_PNG"]
    try:
        png = capture(
            url,
            width=_WIDTH,
            height=_HEIGHT,
            supersample=_SUPERSAMPLE,
            timeout_ms=_TIMEOUT_MS,
        )
    except CaptureHTTPError as exc:
        # Mirror /render's own failure (bad ?date= is a 500, a bad debug arg
        # a 400); anything sub-400 from a redirect chain is still a failure.
        abort(exc.status if exc.status >= 400 else 502)
    except PlaywrightError:
        current_app.logger.exception("display capture failed")
        abort(500)

    if request.args.get("raw") == "1":
        body, mimetype = png, "image/png"
    else:
        indices = screenshot_to_indices(png)
        if request.args.get("quantize") == "1":
            body, mimetype = indices_to_png(indices), "image/png"
        else:
            body, mimetype = pack_pixels(indices), "application/octet-stream"

    response = Response(body, mimetype=mimetype)
    response.set_etag(hashlib.sha256(body).hexdigest())
    response.make_conditional(request)
    return response
