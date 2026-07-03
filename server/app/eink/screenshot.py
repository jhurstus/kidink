"""Capture the running dev server's page as a lossless 1600x1200 PNG."""

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


def capture_screenshot(
    url: str,
    out_path: Path,
    *,
    width: int = 1600,
    height: int = 1200,
    supersample: int = 1,
    timeout_ms: int = 60_000,
) -> None:
    """Screenshot `url` to `out_path` at exactly width x height.

    With supersample > 1 the page renders at that device scale factor and is
    Lanczos-downscaled, which feeds cleaner anti-aliasing ramps to the
    quantizer than Chromium's native 1x edges.
    """
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

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": width, "height": height},
            device_scale_factor=supersample,
        )
        page.goto(url, wait_until="load", timeout=timeout_ms)
        page.evaluate(_WAIT_FOR_ASSETS_JS)
        page.screenshot(path=out_path, type="png")
        browser.close()

    if supersample > 1:
        # BOX (area average) gives true per-pixel coverage with no ringing —
        # Lanczos's negative lobes put halos on glyph edges that quantization
        # turns into sparkle.
        with Image.open(out_path) as img:
            img.convert("RGB").resize((width, height), Image.Resampling.BOX).save(
                out_path
            )
