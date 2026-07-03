"""Demo CLI: screenshot/load an image, dither to six inks, flash the Inkplate.

Run from server/:  uv run python -m app.eink [options]
"""

import argparse
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from app.eink import arduino, dither, pack, palette, screenshot, testcard

WIDTH, HEIGHT = 1600, 1200


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.eink",
        description="Push a board render (or any image) to the Inkplate 13 SPECTRA.",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--url",
        default="http://127.0.0.1:5000/render",
        help="page to screenshot (default: %(default)s)",
    )
    source.add_argument(
        "--image", type=Path, help="use this PNG instead of screenshotting"
    )
    source.add_argument(
        "--test-card",
        action="store_true",
        help="use the built-in hardware test card (color stripes, edge fans)",
    )
    parser.add_argument(
        "--resize",
        action="store_true",
        help=f"Lanczos-resize --image input to {WIDTH}x{HEIGHT} instead of erroring",
    )
    parser.add_argument(
        "--dither",
        choices=["ordered", "reduced", "fs", "none"],
        default="ordered",
        help="dither mode: ordered = stable two-ink halftone (default), "
        "reduced = the firmware's damped kernel (mutes pale tints), "
        "fs = full Floyd-Steinberg, none = nearest ink",
    )
    parser.add_argument(
        "--metric",
        choices=["ycc", "rgb"],
        default="ycc",
        help="nearest-ink metric for error-diffusion modes; rgb = exact "
        "manufacturer behavior (colored speckle on gray edges), ycc = "
        "luma/chroma space (default: %(default)s)",
    )
    parser.add_argument(
        "--strength",
        type=float,
        default=1.0,
        help="error-diffusion strength; 1.0 = device parity with "
        "'--dither reduced --metric rgb'; no effect on ordered/none "
        "(default: %(default)s)",
    )
    parser.add_argument(
        "--sharpen",
        type=float,
        default=0.0,
        metavar="PERCENT",
        help="unsharp-mask amount applied before quantizing, e.g. 150 (default: off)",
    )
    parser.add_argument(
        "--edge-snap",
        type=int,
        default=dither.DEFAULT_EDGE_SNAP,
        metavar="N",
        help="threshold (not dither) pixels on luma steps >= N — clean text/"
        "line edges; 0 disables (default: %(default)s)",
    )
    parser.add_argument(
        "--saturate",
        type=float,
        default=dither.DEFAULT_SATURATE,
        metavar="FACTOR",
        help="vibrance-style chroma boost before quantizing — raises the "
        "chromatic ink coverage of saturated colors without touching "
        "near-neutrals or luma; 1.0 disables (default: %(default)s)",
    )
    parser.add_argument(
        "--edge-gamma",
        type=float,
        default=dither.DEFAULT_EDGE_GAMMA,
        metavar="G",
        help="stem darkening on snapped edges: >1 bolds strokes, 1.0 "
        "disables (default: %(default)s)",
    )
    parser.add_argument(
        "--supersample",
        type=int,
        default=2,
        metavar="N",
        help="render the screenshot at Nx and BOX-downscale for true-"
        "coverage edges; 1 disables (default: %(default)s)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path(".eink-out"),
        help="artifact dir: screenshot.png, preview.png, build/ (default: %(default)s)",
    )
    parser.add_argument(
        "--sketch-dir",
        type=Path,
        default=Path(__file__).resolve().parents[3] / "arduino" / "mockup",
        help="Arduino sketch directory to write mockup.h into",
    )
    parser.add_argument(
        "--port", default=arduino.DEFAULT_PORT, help="serial port for upload"
    )
    parser.add_argument("--fqbn", default=arduino.FQBN, help="arduino-cli board FQBN")
    parser.add_argument(
        "--no-compile",
        action="store_true",
        help="stop after writing preview.png and mockup.h (implies --no-upload)",
    )
    parser.add_argument(
        "--no-upload", action="store_true", help="compile but don't flash"
    )
    return parser.parse_args(argv)


def _acquire_image(args: argparse.Namespace, out_dir: Path) -> Image.Image:
    if args.test_card:
        print("Using built-in test card")
        return testcard.make_test_card(WIDTH, HEIGHT)
    if args.image:
        with Image.open(args.image) as img:
            img = img.convert("RGB")
        if img.size != (WIDTH, HEIGHT):
            if not args.resize:
                raise SystemExit(
                    f"{args.image} is {img.size[0]}x{img.size[1]}, expected "
                    f"{WIDTH}x{HEIGHT}. Pass --resize to scale it."
                )
            img = img.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
        return img
    shot_path = out_dir / "screenshot.png"
    print(f"Screenshotting {args.url} ...")
    screenshot.capture_screenshot(
        args.url, shot_path, width=WIDTH, height=HEIGHT, supersample=args.supersample
    )
    print(f"  saved {shot_path}")
    with Image.open(shot_path) as img:
        return img.convert("RGB")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    img = _acquire_image(args, out_dir)
    if args.sharpen > 0:
        img = img.filter(ImageFilter.UnsharpMask(radius=1.5, percent=int(args.sharpen)))

    print(
        f"Dithering ({args.dither}, metric={args.metric}, "
        f"strength={args.strength}, edge-snap={args.edge_snap}, "
        f"edge-gamma={args.edge_gamma}, saturate={args.saturate}) ..."
    )
    start = time.perf_counter()
    rgb = dither.saturate(np.asarray(img, dtype=np.uint8), args.saturate)
    indices = dither.quantize(
        rgb,
        args.dither,
        args.metric,
        args.strength,
        args.edge_snap,
        args.edge_gamma,
    )
    print(f"  done in {time.perf_counter() - start:.1f}s")

    preview_path = out_dir / "preview.png"
    Image.fromarray(palette.indices_to_rgb(indices)).save(preview_path)
    print(f"  saved {preview_path} (exactly what the panel will show)")

    header_path = args.sketch_dir / "mockup.h"
    packed = pack.pack_pixels(indices)
    header_path.write_text(pack.emit_header(packed, WIDTH, HEIGHT, name="mockup"))
    print(f"  wrote {header_path} ({len(packed)} data bytes)")

    if args.no_compile:
        return 0
    build_dir = out_dir / "build"
    arduino.run_checked(arduino.compile_cmd(args.sketch_dir, build_dir, args.fqbn))
    if args.no_upload:
        return 0
    port = arduino.resolve_port(args.port)
    arduino.run_checked(arduino.upload_cmd(args.sketch_dir, build_dir, port, args.fqbn))
    print("Flashed. The panel refresh takes ~20-30s from reboot.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
