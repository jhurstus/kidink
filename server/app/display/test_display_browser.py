"""Browser tests for /display end-to-end: slow, needs Playwright Chromium and
real sockets.

Run with: uv run pytest -m browser
"""

import hashlib
import io
import threading
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from PIL import Image
from werkzeug.serving import make_server

from app import create_app
from app.eink import palette
from app.test_render import EMPTY_ICS, _EveryDayForecast

# Chromium launch + render + the one-time quantizer LUT build can take a
# while on the first request of the process.
_TIMEOUT_S = 120.0


@pytest.fixture
def live_server_url(tmp_path: Path) -> Iterator[str]:
    app = create_app()
    app.config["FETCH_ICS"] = lambda url: EMPTY_ICS
    app.config["FETCH_MEALPLAN_ICS"] = lambda url: EMPTY_ICS
    app.config["FETCH_FORECAST"] = lambda key, lat, lon: _EveryDayForecast()
    app.config["APP_STORAGE_PATH"] = tmp_path
    # threaded=True is load-bearing: Chromium fetches /render and its assets
    # from this same server while the /display thread is blocked in Playwright.
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.mark.browser
@pytest.mark.enable_socket
def test_display_serves_packed_buffer_with_etag(live_server_url: str) -> None:
    response = httpx.get(
        f"{live_server_url}/display?date=2026-06-03", timeout=_TIMEOUT_S
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert len(response.content) == 1600 * 1200 // 2  # 4bpp
    digest = hashlib.sha256(response.content).hexdigest()
    assert response.headers["etag"] == f'"{digest}"'


@pytest.mark.browser
@pytest.mark.enable_socket
def test_display_is_deterministic_and_304s(live_server_url: str) -> None:
    """The battery win (§3.1): repeat renders byte-match, so the device skips."""
    url = f"{live_server_url}/display?date=2026-06-03"
    first = httpx.get(url, timeout=_TIMEOUT_S)
    second = httpx.get(url, timeout=_TIMEOUT_S)
    assert first.content == second.content
    assert first.headers["etag"] == second.headers["etag"]

    third = httpx.get(
        url, headers={"If-None-Match": first.headers["etag"]}, timeout=_TIMEOUT_S
    )
    assert third.status_code == 304
    assert third.content == b""


@pytest.mark.browser
@pytest.mark.enable_socket
def test_display_quantize_preview_is_palette_only_png(live_server_url: str) -> None:
    response = httpx.get(
        f"{live_server_url}/display?quantize=1&date=2026-06-03", timeout=_TIMEOUT_S
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    with Image.open(io.BytesIO(response.content)) as img:
        assert img.size == (1600, 1200)
        rgb = img.convert("RGB")
        colors = {color for _, color in rgb.getcolors(maxcolors=1600 * 1200) or []}
    assert colors <= {tuple(ink) for ink in palette.PALETTE_RGB.tolist()}


@pytest.mark.browser
@pytest.mark.enable_socket
def test_display_raw_is_fullcolor_png(live_server_url: str) -> None:
    response = httpx.get(
        f"{live_server_url}/display?raw=1&date=2026-06-03", timeout=_TIMEOUT_S
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    with Image.open(io.BytesIO(response.content)) as img:
        assert img.size == (1600, 1200)
        assert img.format == "PNG"


@pytest.mark.browser
@pytest.mark.enable_socket
def test_display_mirrors_render_errors(live_server_url: str) -> None:
    bad_date = httpx.get(
        f"{live_server_url}/display?date=not-a-date", timeout=_TIMEOUT_S
    )
    assert bad_date.status_code == 500
    bad_icon = httpx.get(
        f"{live_server_url}/display?weather_icon=bogus", timeout=_TIMEOUT_S
    )
    assert bad_icon.status_code == 400
