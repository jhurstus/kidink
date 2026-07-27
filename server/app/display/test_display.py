"""Fast /display tests: the capture seam is faked, no Chromium involved."""

import hashlib
import io
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

import numpy as np
import pytest
from flask import Flask
from PIL import Image
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app import create_app
from app.display.monitoring import DisplayRequest, list_requests, open_monitoring_db
from app.eink import dither, palette
from app.eink.pack import pack_pixels
from app.eink.screenshot import CaptureHTTPError

# Small and solid off-palette orange: quantizing must turn every pixel into
# one of the six inks, and 16x16 keeps the dither step instant.
_SIZE = 16
_ORANGE = (255, 140, 0)


def _orange_png() -> bytes:
    pixels = np.tile(np.array(_ORANGE, np.uint8), (_SIZE, _SIZE, 1))
    out = io.BytesIO()
    Image.fromarray(pixels, "RGB").save(out, format="PNG")
    return out.getvalue()


class _RecordingCapture:
    """Fake CAPTURE_PNG seam: records each call, returns canned PNG bytes."""

    def __init__(self, png: bytes) -> None:
        self.png = png
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __call__(self, url: str, **kwargs: object) -> bytes:
        self.calls.append((url, kwargs))
        return self.png


def _app_with_capture(capture: object, storage: Path) -> Flask:
    """An app with the capture seam and storage faked.

    Every /display request appends to the monitoring log under
    ``APP_STORAGE_PATH``, so tests must always point it at a temp dir, never
    the developer's real ``.storage``.
    """
    app = create_app()
    app.config["CAPTURE_PNG"] = capture
    app.config["APP_STORAGE_PATH"] = storage
    return app


def _log_rows(storage: Path) -> list[DisplayRequest]:
    conn = open_monitoring_db(storage)
    try:
        return list_requests(conn)
    finally:
        conn.close()


def test_display_forwards_all_query_args_to_render(tmp_path: Path) -> None:
    capture = _RecordingCapture(_orange_png())
    client = _app_with_capture(capture, tmp_path).test_client()
    response = client.get("/display?date=2026-06-03&weather_temp=70&quantize=1")
    assert response.status_code == 200
    (url, _), *rest = capture.calls
    assert not rest
    assert url == "http://localhost/render?date=2026-06-03&weather_temp=70&quantize=1"


def test_display_capture_args(tmp_path: Path) -> None:
    capture = _RecordingCapture(_orange_png())
    client = _app_with_capture(capture, tmp_path).test_client()
    assert client.get("/display").status_code == 200
    [(url, kwargs)] = capture.calls
    assert url == "http://localhost/render"
    assert kwargs == {
        "width": 1600,
        "height": 1200,
        "supersample": 2,
        "timeout_ms": 300_000,
    }


def test_display_serves_packed_device_buffer(tmp_path: Path) -> None:
    png = _orange_png()
    client = _app_with_capture(_RecordingCapture(png), tmp_path).test_client()
    response = client.get("/display")
    assert response.status_code == 200
    assert response.mimetype == "application/octet-stream"
    assert len(response.data) == _SIZE * _SIZE // 2  # 4bpp, two pixels per byte
    rgb = np.tile(np.array(_ORANGE, np.uint8), (_SIZE, _SIZE, 1))
    expected = pack_pixels(
        dither.quantize(dither.saturate(rgb, dither.DEFAULT_SATURATE))
    )
    assert response.data == expected


def test_display_etag_is_quoted_sha256_of_body(tmp_path: Path) -> None:
    client = _app_with_capture(_RecordingCapture(_orange_png()), tmp_path).test_client()
    response = client.get("/display")
    digest = hashlib.sha256(response.data).hexdigest()
    assert response.headers["ETag"] == f'"{digest}"'


def test_display_quantize_serves_palette_only_png(tmp_path: Path) -> None:
    client = _app_with_capture(_RecordingCapture(_orange_png()), tmp_path).test_client()
    response = client.get("/display?quantize=1")
    assert response.status_code == 200
    assert response.mimetype == "image/png"
    with Image.open(io.BytesIO(response.data)) as img:
        assert img.size == (_SIZE, _SIZE)
        colors = {color for _, color in img.convert("RGB").getcolors() or []}
    assert colors  # the solid orange must have been replaced ...
    assert colors <= {tuple(ink) for ink in palette.PALETTE_RGB.tolist()}  # ... by inks


def test_display_format_png_is_quantize_alias(tmp_path: Path) -> None:
    client = _app_with_capture(_RecordingCapture(_orange_png()), tmp_path).test_client()
    response = client.get("/display?format=png")
    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert response.data == client.get("/display?quantize=1").data


def test_display_raw_serves_screenshot_unchanged(tmp_path: Path) -> None:
    png = _orange_png()
    client = _app_with_capture(_RecordingCapture(png), tmp_path).test_client()
    response = client.get("/display?raw=1")
    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert response.data == png
    # raw takes precedence over quantize
    assert client.get("/display?raw=1&quantize=1").data == png


def test_display_debug_args_require_exact_values(tmp_path: Path) -> None:
    client = _app_with_capture(_RecordingCapture(_orange_png()), tmp_path).test_client()
    queries = ("?quantize=true", "?quantize=0", "?raw=true", "?raw=0", "?format=jpeg")
    for query in queries:
        assert client.get(f"/display{query}").mimetype == "application/octet-stream"


def test_display_if_none_match_roundtrip(tmp_path: Path) -> None:
    capture = _RecordingCapture(_orange_png())
    client = _app_with_capture(capture, tmp_path).test_client()
    etag = client.get("/display").headers["ETag"]

    hit = client.get("/display", headers={"If-None-Match": etag})
    assert hit.status_code == 304
    assert hit.data == b""
    assert hit.headers["ETag"] == etag  # the device re-stores it on 304 too
    # Computing the ETag requires producing the buffer (§3.1), so the capture
    # ran for the conditional request as well.
    assert len(capture.calls) == 2

    miss = client.get("/display", headers={"If-None-Match": '"stale"'})
    assert miss.status_code == 200
    assert miss.data != b""


def test_display_head_has_etag_and_no_body(tmp_path: Path) -> None:
    client = _app_with_capture(_RecordingCapture(_orange_png()), tmp_path).test_client()
    response = client.head("/display")
    assert response.status_code == 200
    assert response.data == b""
    assert response.headers["ETag"]
    assert int(response.headers["Content-Length"]) == _SIZE * _SIZE // 2


@pytest.mark.parametrize("status", [400, 500])
def test_display_mirrors_render_error_status(status: int, tmp_path: Path) -> None:
    def capture(url: str, **kwargs: object) -> NoReturn:
        raise CaptureHTTPError(status, url)

    client = _app_with_capture(capture, tmp_path).test_client()
    assert client.get("/display").status_code == status


@pytest.mark.parametrize(
    "error", [PlaywrightError("boom"), PlaywrightTimeoutError("too slow")]
)
def test_display_playwright_failure_is_500(
    error: PlaywrightError, tmp_path: Path
) -> None:
    def capture(url: str, **kwargs: object) -> NoReturn:
        raise error

    client = _app_with_capture(capture, tmp_path).test_client()
    assert client.get("/display").status_code == 500


def test_display_request_is_logged_with_utc_arrival_and_status(
    tmp_path: Path,
) -> None:
    client = _app_with_capture(_RecordingCapture(_orange_png()), tmp_path).test_client()
    before = datetime.now(UTC)
    assert client.get("/display").status_code == 200
    after = datetime.now(UTC)

    [row] = _log_rows(tmp_path)
    assert row.status == 200
    assert row.requested_at.tzinfo == UTC
    assert before <= row.requested_at <= after

    # Only /display feeds the log: other routes on the same app add no rows.
    assert client.get("/").status_code == 200
    assert len(_log_rows(tmp_path)) == 1


def test_display_304_and_errors_are_logged(tmp_path: Path) -> None:
    class _FailingSecondCapture:
        """First two calls succeed, later calls blow up like a dead Chromium."""

        def __init__(self) -> None:
            self.png = _orange_png()
            self.calls = 0

        def __call__(self, url: str, **kwargs: object) -> bytes:
            self.calls += 1
            if self.calls > 2:
                raise PlaywrightError("boom")
            return self.png

    client = _app_with_capture(_FailingSecondCapture(), tmp_path).test_client()
    etag = client.get("/display").headers["ETag"]
    assert client.get("/display", headers={"If-None-Match": etag}).status_code == 304
    assert client.get("/display").status_code == 500

    assert [row.status for row in _log_rows(tmp_path)] == [200, 304, 500]


def test_display_bad_capture_status_is_logged(tmp_path: Path) -> None:
    def capture(url: str, **kwargs: object) -> NoReturn:
        raise CaptureHTTPError(400, url)

    client = _app_with_capture(capture, tmp_path).test_client()
    assert client.get("/display").status_code == 400
    assert [row.status for row in _log_rows(tmp_path)] == [400]


def test_display_logging_failure_does_not_break_response(tmp_path: Path) -> None:
    # A path under /dev/null can never be created, so the log write fails; the
    # device-facing response must still be served (telemetry never breaks it).
    poison = Path("/dev/null/kidink-display-log")
    client = _app_with_capture(_RecordingCapture(_orange_png()), poison).test_client()
    response = client.get("/display")
    assert response.status_code == 200
    assert len(response.data) == _SIZE * _SIZE // 2
