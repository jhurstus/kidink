"""Capture a page as a lossless 1600x1200 PNG.

`capture_png` is the shared, disk-free core (used by `/display`, §3.3 step 5);
`capture_screenshot` wraps it for the demo CLI with a preflight reachability
check and a file write.
"""

import io
from pathlib import Path

import httpx
from PIL import Image
from playwright.sync_api import sync_playwright

# Deterministic readiness: fonts loaded and every <img> decoded. networkidle
# is flaky and load alone doesn't cover late font/img decode.
_WAIT_FOR_ASSETS_JS = """
async () => {
    await document.fonts.ready;
    await Promise.all([...document.images].map((i) => i.decode().catch(() => {})));
}
"""


class CaptureHTTPError(Exception):
    """Chromium's navigation to the target URL got a non-200 response."""

    def __init__(self, status: int, url: str) -> None:
        super().__init__(f"{url} returned {status}")
        self.status = status


def capture_png(
    url: str,
    *,
    width: int = 1600,
    height: int = 1200,
    supersample: int = 1,
    timeout_ms: int = 300_000,
) -> bytes:
    """Screenshot `url` and return PNG bytes at exactly width x height.

    With supersample > 1 the page renders at that device scale factor and is
    BOX-downscaled, which feeds cleaner anti-aliasing ramps to the quantizer
    than Chromium's native 1x edges. Raises `CaptureHTTPError` when the
    navigation lands on a non-200 response.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": width, "height": height},
            device_scale_factor=supersample,
        )
        response = page.goto(url, wait_until="load", timeout=timeout_ms)
        if response is None or response.status != 200:
            raise CaptureHTTPError(response.status if response else 502, url)
        page.evaluate(_WAIT_FOR_ASSETS_JS)
        png = page.screenshot(type="png")
        browser.close()

    if supersample > 1:
        # BOX (area average) gives true per-pixel coverage with no ringing —
        # Lanczos's negative lobes put halos on glyph edges that quantization
        # turns into sparkle.
        with Image.open(io.BytesIO(png)) as img:
            downscaled = img.convert("RGB").resize(
                (width, height), Image.Resampling.BOX
            )
        out = io.BytesIO()
        downscaled.save(out, format="PNG")
        png = out.getvalue()
    return png


def capture_screenshot(
    url: str,
    out_path: Path,
    *,
    width: int = 1600,
    height: int = 1200,
    supersample: int = 1,
    timeout_ms: int = 300_000,
) -> None:
    """Screenshot `url` to `out_path` at exactly width x height (demo CLI)."""
    try:
        response = httpx.get(url, timeout=10)
    except httpx.TransportError as exc:
        raise SystemExit(
            f"Could not reach {url} ({exc}). Is the dev server running? "
            "Start it with ./run.sh"
        ) from exc
    if response.status_code != 200:
        raise SystemExit(
            f"{url} returned {response.status_code}; refusing to screenshot "
            "an error page."
        )
    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type:
        # macOS AirPlay Receiver squats on port 5000 and answers 200 to
        # anything, so a status check alone isn't enough.
        raise SystemExit(
            f"{url} returned content-type {content_type!r}, not text/html — "
            "is something else (e.g. macOS AirPlay Receiver) on this port?"
        )

    out_path.write_bytes(
        capture_png(
            url,
            width=width,
            height=height,
            supersample=supersample,
            timeout_ms=timeout_ms,
        )
    )
