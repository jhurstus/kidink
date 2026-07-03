"""Browser test: slow, needs Playwright Chromium and real sockets.

Run with: uv run pytest -m browser
"""

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image
from werkzeug.serving import make_server

from app import create_app
from app.eink.screenshot import capture_screenshot

EMPTY_ICS = "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//test//EN\nEND:VCALENDAR\n"


@pytest.fixture
def live_server_url() -> Iterator[str]:
    app = create_app()
    app.config["FETCH_ICS"] = lambda url: EMPTY_ICS
    server = make_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.mark.browser
@pytest.mark.enable_socket
def test_capture_screenshot_produces_panel_sized_png(
    live_server_url: str, tmp_path: Path
) -> None:
    out = tmp_path / "shot.png"
    capture_screenshot(f"{live_server_url}/render?date=2026-06-03", out)
    with Image.open(out) as img:
        assert img.size == (1600, 1200)
        assert img.format == "PNG"


@pytest.mark.browser
@pytest.mark.enable_socket
def test_capture_screenshot_refuses_error_page(
    live_server_url: str, tmp_path: Path
) -> None:
    with pytest.raises(SystemExit, match="500"):
        capture_screenshot(
            f"{live_server_url}/render?date=not-a-date", out_path=tmp_path / "x.png"
        )
