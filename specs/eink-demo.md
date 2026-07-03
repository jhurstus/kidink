# E-Ink Push Demo — Specification (as built)

## 1. Overview

A standalone CLI that proves the full render-to-panel path end-to-end, ahead of
the real `/display` + ESP32 pipeline (main spec §19): screenshot the running
Flask `/render` page (or take any PNG), quantize + dither it to the Inkplate 13
SPECTRA's six inks, pack it into a C byte-buffer header, and compile + flash a
minimal Arduino sketch that draws it.

Run from `server/`:

```
uv run python -m app.eink                 # screenshot → dither → flash
uv run python -m app.eink --test-card     # hardware verification card
uv run python -m app.eink --image p.png --resize --no-upload
```

Components:

- `server/app/eink/` — the pipeline (`palette.py`, `dither.py`, `pack.py`,
  `testcard.py`, `screenshot.py`, `arduino.py`, `__main__.py`). The
  palette/dither core is deliberately reusable for the future `?quantize=1`
  preview pass (main spec §5.2).
- `arduino/mockup/mockup.ino` — fixed minimal sketch. The CLI writes the
  generated `mockup.h` beside it (gitignored), then drives `arduino-cli`.

One-time setup: `uv run playwright install chromium`.

## 2. Pipeline

1. **Acquire** — Playwright screenshots the page at viewport 1600×1200,
   `deviceScaleFactor=1` (or `--supersample N` for N× + Lanczos downscale),
   waiting on `document.fonts.ready` and `img.decode()` for every image (not
   `networkidle`). A pre-flight HTTP GET requires status 200 **and** a
   `text/html` content type — macOS AirPlay Receiver squats on port 5000 and
   answers 200 to anything, so a status check alone is not enough.
   Alternatives: `--image <png>` (with `--resize` to Lanczos-fit) or
   `--test-card`.
2. **Quantize/dither** to (h, w) palette indices — §4.
3. **Preview** — `preview.png` written to `--out-dir` (default
   `server/.eink-out/`) is the palette-mapped result: exactly what the panel
   will show, since the device draws our indices verbatim.
4. **Pack** into the 4bpp buffer — §3 — and emit `mockup.h`.
5. **Compile + upload** via `arduino-cli` (FQBN
   `soldered-inkplate-boards:esp32:Inkplate13SPECTRA`, port default
   `/dev/cu.wchusbserial110` with a `/dev/cu.wchusbserial*` glob fallback).
   `--no-compile` / `--no-upload` stop earlier. The ~1.9MB sketch uses ~59% of
   the app partition; the panel refresh takes ~20–30s after reboot.

The pipeline is deterministic: same input image + flags → byte-identical
`preview.png` and `mockup.h`.

## 3. Device buffer format

- 4 bits per pixel, two pixels per byte, **high nibble = left/even-x pixel**.
- Rows top-to-bottom, stride `ceil(w/2)` bytes; 1600×1200 → 960,000 bytes.
- **Nibble = palette_index << 1**. The firmware does `nibble >> 1` and then
  remaps the index to the raw panel code (`colorPalette[]`) internally.

| ink | palette index | nibble |
|---|---|---|
| black | 0 | `0x0` |
| white | 1 | `0x2` |
| yellow | 2 | `0x4` |
| red | 3 | `0x6` |
| blue | 4 | `0x8` |
| green | 5 | `0xA` |

The header exports the user-facing declarations the sketch consumes:
`const uint8_t mockup[] PROGMEM`, `const uint16_t mockup_w/_h`.

## 4. Quantization and dithering

### Dither modes (`--dither`)

| mode | what it is |
|---|---|
| `ordered` (default) | Palette-mixing ordered dither (after Yliluoma): every color resolves to a two-ink mixing plan (inks A, B, ratio) chosen by YCbCr distance between the target and the plan's average color, plus a small contrast penalty (0.05) whose job is keeping grays as black/white mixes instead of yellow+blue checkerboards; an 8×8 Bayer matrix then decides which ink each pixel gets. Spatially stable (no "worms", regular dot lattices, clean edges) and renders pale tints as sparse chromatic dots. Closest to the comic-halftone aesthetic main spec §5.2 wants (a clustered-dot screen can replace the matrix later). |
| `reduced` | Port of the library's default "ReducedDiffusion" kernel: Floyd–Steinberg tap layout (E=5, SW=2, S=3, SE=1) with divisor **26**, so only 11/26 ≈ 42% of the quantization error is diffused. Keeps flat saturated areas clean, but the discarded error means **pale tints never reach their flip boundary and lose their color entirely** — a light green like (231,246,228) renders pure white. |
| `fs` | Full Floyd–Steinberg (E=7, SW=3, S=5, SE=1, ÷16). Conserves color (tints render at correct coverage) but with irregular, noisier speckle than `ordered`. |
| `none` | Nearest ink per pixel. |

Error diffusion matches the firmware semantics exactly: raster order,
per-channel error, accumulated value clamped to 0..255 *before* matching, C
truncating division `(weight*err)/coef`, off-edge taps discarded. With
`--dither reduced --metric rgb --strength 1.0` the output is bit-exact with
what the device would compute itself. `--strength` scales the diffused error
(0 = `none`; no effect on `ordered`).

### Distance metric (`--metric`, error-diffusion modes and `none`)

- `rgb` — unweighted squared RGB euclidean, ties to the lowest palette index:
  exactly the firmware's `findClosestPalette`.
- `ycc` (default) — squared euclidean in **YCbCr** (BT.601 luma; chroma weight
  deliberately 1.0). Rationale: near-neutral pixels (antialiased edges of
  black text/lines, gray fills) sit almost equidistant to *all six* inks in
  plain RGB, so tiny channel imbalances snap them to yellow/red/… and error
  diffusion sprays colored speckle along edges — the "dirty diagonals" seen on
  early renders. Separating luma from chroma fixes that. An earlier variant
  up-weighted chroma 2×, which overcorrected and **muted mid-saturation
  colors**: monday_burst's bright green (81,195,85) landed exactly on the
  green/white decision boundary and rendered gray-green on the panel. Plain
  YCbCr keeps it decisively green while still protecting near-grays.

Both metrics are implemented as a cached 256³ nearest-ink LUT (the ordered
mode uses its own 64³ mixing-plan LUT), so quantizing the full panel takes
~1s. The palette RGB targets are the library's pure primaries; they are
constants in `palette.py` and can later be calibrated to measured (muted)
Spectra ink colors without changing the emitted indices' meaning.

### Authoring guidance for muted-looking colors

A six-ink panel can only render non-ink colors as dot mixes, and a pale tint
is *mostly white by definition* — e.g. light green (231,246,228) is ~7% green
ink coverage, so it reads as a faint stipple at arm's length no matter how
good the dither is. The quantizer now renders those tints faithfully; if a
region still reads too weak on the physical panel, the fix is in the
artwork: push fills toward higher ink coverage (more saturated colors), or
author the halftone explicitly like the CSS Ben-Day fills (main spec §5.3),
which pass through quantization untouched.

### Edge snapping (`--edge-snap`, default 48; 0 disables)

Dithering treats an antialiasing ramp like a tint: each ~50%-gray edge pixel
is scattered to black or white by the screen — which reads as spiky, noisy
glyph contours. The hybrid-screening pass fixes this the way print RIPs do:
pixels sitting on a strong luma step (gradient ≥ the threshold — AA ramps on
text/line art step ~100+/px, soft image gradients stay far below) are
**thresholded to their nearest ink** instead of dithered, committing each
ramp pixel to the dominant side for the cleanest possible stairstep. Flat
regions keep dithering. Side effects are desirable: a ~1px knockout margin
around glyphs on tinted fills (improves legibility), and hard-edged
asset-authored dither patterns pass through crisply instead of being
re-dithered. For the error-diffusion modes the snap runs after diffusion, so
error conservation is slightly off along edges.

### Chroma boost (`--saturate`, default 1.4; 1.0 disables)

A faithful dither can only give a color its true ink coverage — "bright"
green (81,195,85) genuinely contains ~32% white and ~24% black, so it
dithers at just ~45% green ink and reads weak on the panel. The boost pushes
saturated colors toward the pure inks before quantizing (that lettering then
dithers at ~65% green). Two properties are load-bearing, both pinned by
tests:

- It is **vibrance, not plain saturation** — the boost scales quadratically
  with each pixel's own chroma (full factor at chroma ≥ 80). A plain linear
  boost gives near-neutrals phantom color that dithers as yellow/red dirt on
  the cream page; the vibrance curve leaves text ink, paper, and gray washes
  untouched.
- It is **luma-preserving** (chroma is scaled in YCbCr with Y held exactly),
  so nothing lightens or darkens — only dither coverage shifts from
  white/black toward the chromatic inks.

The cheap alternative to this is calibrating `PALETTE_RGB` to measured
Spectra ink colors; the boost may need retuning if that happens.

### Stem darkening (`--edge-gamma`, default 1.5; 1.0 disables)

Thresholding an AA ramp at 50% luma systematically **erodes strokes** —
glyphs render thinner than designed, with pinches and breaks (the reason
e-readers darken text). Snapped edge pixels are gamma-darkened first, moving
the black/white cutoff from luma ~128 to ~160, so ramp pixels with ≥~37% ink
coverage commit to the dark side. Strokes render at full weight; the comic
style's heavy outlines benefit too. Trade-off: light-on-dark features thin
slightly.

### Supersampling (`--supersample`, default 2; 1 disables)

The screenshot renders at N× device scale and BOX-downscales (area
averaging: true per-pixel coverage, no ringing — Lanczos's negative lobes
put halos on edges that quantization turns into sparkle). True coverage
makes stairsteps on shallow diagonals (e.g. the skewed date box's border)
land exactly where the geometry says, so steps are regular instead of
ragged, and pairs with edge snapping as effectively ideal bilevel scan
conversion.

The ordered mode also floors mixing ratios at 3/64 — a 1–2/64 sprinkle reads
as stray dirt in near-pure fields and along AA ramp tails, not as a tint.

`--sharpen <percent>` (pre-quantize unsharp mask) remains for A/B but is
superseded by the above; sharpening overshoot tends to add edge sparkle. A
1px diagonal still stairsteps at the panel's ~150 PPI — remaining gains come
from content (heavier font weights, thicker borders on skewed elements).

## 5. Test card (`--test-card`)

A deterministic 1600×1200 card: six labeled solid ink stripes (color-mapping
check — wrong palette order is immediately visible), a diagonal line fan at
1/2/3/5/8 px strokes, concentric circles + rotated square, text at five sizes,
and a gray ramp (chromatic-speckle check: it must dither purely black/white).

Edge-case colors worth re-checking after quantizer changes: the monday_burst
greens — bright (81,195,85) must stay decisively green, pale (231,246,228)
must keep a sparse green stipple (regression tests cover both in
`test_palette.py` / `test_dither.py`).

## 6. Relationship to the real pipeline (deferred)

What carries forward to `/display?quantize=1` and the ESP32 firmware (main
spec §19): the `app.eink` palette/dither/pack core, the buffer format
knowledge in §3, and on-panel tuning results from the mode/metric A/Bs. What
does not: the Arduino-header + arduino-cli flashing flow, which exists only so
the demo can push pixels before any firmware is written.
