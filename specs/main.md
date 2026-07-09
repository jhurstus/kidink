# Kids' Comic E-Ink Display — Software Specification

## 1. Overview

A wall-mounted color e-ink information display for two children (ages 5 and 7).
The kids have low reading literacy, so the display must therefore work primarily
through pictures.

The board shows the day, the kids' schedule for today and tomorrow, the weather,
tonight's dinner, today's chores, a countdown to the next exciting event, and a
daily joke — all in the visual language of a **kids' comic book**: panels, speech
bubbles, comic display fonts, bold saturated colors, hard edges, and stylized
halftone (Ben-Day) shading. A tiny hidden character ("bugbug") is hidden somewhere
on the page each day as a seek-and-find game.

### Design principles

- **Icon-first, text-second.** Every piece of information that matters is carried
  by an image. Text is supportive and redundant, never required to understand the
  board.
- **Comic styling over strict legibility.** Where the two conflict, we favor a
  visually exciting, engaging board. We do not specially optimize type for
  dyslexia; comic fonts win.
- **Built for color e-ink.** Bright saturated colors, high contrast, sharp lines.
  Avoid fine detail and subtle gradients, which reproduce poorly.

### Audience for this document

This is the specification for the **server-side rendering system** (Python +
Jinja2 + HTML/CSS → PNG) and the **functional behavior of every UI module**. It
reflects decisions made during design iteration. Sections marked *Deferred* or
*TBD* are intentionally left for later.

---

## 2. Hardware context (summary)

- **Panel:** Inkplate 13 Spectra, 13.3", 1600 × 1200, E-Ink Spectra 6.
- **Native colors:** black, white, red, yellow, green, blue (six only — see §5).
- **Full refresh:** ~19 seconds, with a visible multi-pass flash. Fine at an hourly
  cadence; color e-ink cannot do fast partial updates.
- **Bistability:** the panel retains its last image with no power, so "keeps showing
  the last image" is free, even through deep sleep.
- **Frame overlay:** the physical frame sits on top of the e-ink screen and obscures
  the outermost pixels on every side — measured (2026-07-06, via a full-screen
  calibration checkerboard of 10px blocks) at about **3px left, 7px top, 4px right,
  6px bottom**. Treat those edge bands as invisible: no content may rely on them,
  and edge margins should be tuned to look even *after* subtracting them.
- **Compute split:** a Raspberry Pi 5 (Raspbian) renders everything; the ESP32-S3 on
  the Inkplate is a dumb client that wakes on its RTC, fetches a pre-rendered file
  over local Wi-Fi, draws it, and sleeps.

The ESP32 firmware and the on-device file format are **deferred** (see §19).

---

## 3. System architecture

### 3.1 Two endpoints, the ETag, and conditional GET

- **`/render`** builds the page (HTML + CSS) for a date and returns it as a normal
  `text/html` response. It does **not** write to disk.
- **`/display`** produces the device-facing image: it makes an **internal request to
  `/render`**, feeds the returned HTML/CSS to headless Chromium, and serves the
  resulting PNG with `ETag` / conditional-GET support. It writes **no files to disk** (assets load over HTTP, §3.2). **All query args on
  `/display` are passed through to the internal `/render` request.**

The ESP32 polls `/display`. The served PNG's **`ETag` is a hash of the PNG bytes**,
so *any* change — including a regenerated AI image — changes the `ETag`. The device
sends `If-None-Match`; on a match it gets `304 Not Modified` and skips both the
download and the ~19-second refresh (the main battery win), while continuing to show
its last image (e-ink is bistable). Computing the `ETag` requires producing the PNG,
which is acceptable at the polling cadence.

### 3.2 The two endpoints in detail

- **`/render` — pipeline steps 1–4** (§3.3). Returns `text/html`. Writes no output
  file; AI image generation still persists images to the durable image cache (§7),
  which is expected. Accepts the debug query args (§3.5). All `<img>` and asset URLs
  in the returned HTML are **absolute Flask URLs** served by the local image route
  (§7.6), so Chromium fetches them over loopback rather than from the filesystem.
- **`/display` — pipeline steps 5–7** (§3.3). Calls `/render` internally (forwarding
  all query args), loads the returned HTML into Chromium via `set_content` (no disk
  write — image and asset URLs are Flask routes that Chromium fetches over loopback),
  captures the PNG, and serves it with the PNG-hash `ETag`.
- **Image admin endpoint** (§7.4).

### 3.3 Render pipeline stages

**`/render` (steps 1–4):**

1. Resolve the target **date** (§3.4).
2. Fetch source data (calendar ICS, meal-plan ICS, weather) and build view models.
3. Ensure AI images exist for the view models, generating any that are missing inline
   (§7).
4. Render modular Jinja2 templates to HTML + CSS (plain markup, **no JavaScript** —
   the panel is not interactive) and return it.

**`/display` (steps 5–7):**

5. Load the HTML from `/render` into headless Chromium (Playwright) via `set_content`
   and capture a **1600 × 1200 PNG** at `deviceScaleFactor: 1`, waiting for all fonts
   and `<img>` icons (fetched from the Flask image route) to finish loading first.
6. *(Quantize)* If `?quantize=1`, produce the emulated palette-quantized preview (§5);
   otherwise serve the raw screenshot. Pre-packing the controller framebuffer is
   **deferred** (§19).
7. Serve the PNG with the PNG-hash `ETag` / `304`.

### 3.4 Determinism and the date seed

A render is a **pure function of its inputs**. The pseudo-random choices on the board
— the bugbug's hiding spot above all, plus the clothing-kid flip-flop and the joke
index — are seeded off the **date**, so they are **stable for a given day** and never
jump between renders of the same date. The date also fixes which week the strip shows.

This does **not** mean the board is frozen for a whole day. Weather and the ICS feeds
are **time-varying inputs**, so legitimate content changes (an updated forecast, an
edited event) flow through within a day. **Feed cache freshness** (§6.1) governs when
those inputs are re-read, and the PNG-hash `ETag` (§3.1) reflects every resulting
pixel change. So "never off wall-clock time" applies to the *pseudo-random choices*,
not to the data, which is allowed to evolve through the day.

The **date** defaults to the server clock's current calendar day, resolved in the
configured timezone, and is overridable via `?date=` (§3.5).

### 3.5 Debug query arguments

All are accepted on `/display` (and passed through to `/render`), and on `/render`
directly. They are invisible to the ESP32 in normal operation:

- **`?date=YYYY-MM-DD`** — overrides the resolved date, for previewing any day
  (consumed by `/render`).
- **`?quantize=1`** — serve the emulated palette-quantized preview instead of the raw
  screenshot (consumed by `/display`, step 6).
- **`?debug_images=1`** — after the main content, append a list of **every AI image
  included in the render**: each image's id and logical key and a link to the image admin
  endpoint (§7.4, via its `img=` arg) to view and edit that image's prompt (consumed
  by `/render`).

### 3.6 Warm-up prerenders

Image generation is **inline** inside `/render` (steps 3–4; the bugbug variant in §16
is generated during step 4's placement pass). There is no separate image-collection
subsystem.

To keep the device-facing `/display` fast and complete, a cron schedule
(`refresh_cadence`, §18) fires **throwaway prerenders** of the **next date**:
`GET /render?date=<tomorrow>`, discarding the HTML response. No Chromium is needed —
`/render` alone generates and persists any missing AI images to the durable cache. Its
only purpose is to warm that cache; the board's actual refresh happens whenever the
ESP32 polls `/display`.

`+1` day is sufficient to warm, because today's own render already references the whole
week's strip icons, the tomorrow panel, and the countdown hero. The only genuinely new
images each day are tomorrow's joke, tomorrow's dinner, and any brand-new event —
roughly one or two generations per day. A pleasant side effect: the midnight rollover
is nearly free, since tomorrow's images are already cached.

### 3.7 Technology stack

| Concern | Choice |
|---|---|
| Language | Python 3.14 |
| HTTP server | Flask |
| Templating | Jinja2 → plain HTML + CSS, no JS |
| CSS authoring | Plain CSS with custom properties; the palette/tones are owned by a central Python class that emits the CSS variables |
| Headless render | Playwright (Python) driving Chromium |
| Calendar parsing | ICS parser + a recurring-ICS expansion library |
| AI images | OpenAI image API — `gpt-image-2`, configurable per module; transparency is derived in code by background-keying (§7.2) |
| Image processing | Pillow + NumPy — background-keying to alpha (§7.2) and the quantize-preview pass (§5.2) |
| Weather | Google Maps Platform Weather API (daily forecast) |
| App config | Pydantic (`pydantic-settings`) model, validated at startup (§18) |

We control the exact browser, so we **target a modern Chromium and use modern web
platform features freely** where they help — CSS nesting, `:has()`, container queries,
subgrid, modern color syntax, `clip-path`, masks, and so on — rather than coding to
legacy fallbacks.

### 3.8 Coding conventions

All Python conforms to **PEP 8** and the **Google Python Style Guide**, with
type annotations throughout (verifiable by a static type checker).

### 3.9 Verifying rendered changes

Because the board is a visual artifact, changes that affect rendering must be
verified by **looking at the actual output**, not just by reading code or passing
unit tests. Use the **Playwright skill** to snapshot and/or screenshot the local
dev server (`./run.sh`) — typically against `/render` for a fixed
`?date=` — and inspect the result to confirm a change looks right before considering
it done.

---

## 4. Layout

The board is a fixed **1600 × 1200** comic page. The macro-layout is fixed (stable day
to day, which helps young kids), while content within each panel flexes.

- **Top:** the full-width day-of-week strip (§9), with the full date in the top-left
  corner.
- **Left column:** the **Today** panel (§10), including its weather subpanel at the
  bottom.
- **Right column:** **Tomorrow** (§11) at the top, then **Countdown** (§12),
  **Dinner** (§13), **Chores** (§14), and the **Joke** (§15) panel.

The reference mockup is a rough guide to arrangement, not final visuals.

### 4.1 Row budget (space-limited lists)

Where a list shows "the top N as space allows" (today, tomorrow, chores), N is
**derived from geometry**, not hand-set: each list row has a fixed height and the panel
viewport is fixed, so the number of rows that fit is computed up front. For Tomorrow
and Chores (no headers) this is simply `floor(available_height / row_height)`. For
**Today**, the visible-header count interacts with the cap; the resolution order is
specified in §10.

When events exceed the budget, the lowest-`interesting` ones are **dropped silently** —
no "+2 more" affordance (meaningless to a non-reader).

---

## 5. Color and palette

The panel's **native** palette is only six colors: black, white, red, yellow, green,
blue. Every other color — orange, purple, teal, pink, light-blue, the grays — is a
**halftone blend** of two native colors. At ~150 PPI a large flat dithered fill reads
as a visible halftone stipple, which we treat as an intentional comic **Ben-Day**
texture rather than a defect.

### 5.1 Working palette

The design is built from a locked swatch set: the **6 solids** plus a fixed, **named
set of halftone blends** (§5.3), all treated as first-class colors. The CSS render maps
predictably onto what the panel can display.

### 5.2 Rendering paths and the single quantization pass

- **Authored areas** (backgrounds, panel fills, text, day cells, the temperature bar —
  any large color swath or load-bearing color) are drawn directly in CSS as a real dot
  pattern (e.g. layered radial-gradients of red dots on yellow to make "orange"), with
  dot size, spacing, and angle under our control via a reusable halftone dot pattern
  driven by the palette's CSS custom properties (§5.1). They are already near-native
  colors and survive quantization almost unchanged.
- **Icons and AI images** are carried in the page as full-color, transparent PNGs
  composited onto their cells.

There is exactly **one** quantization pass, and it is **not** part of normal
`/display`: the raw full-color screenshot is what `/display` serves, and the six-color
quantize + dither is **deferred to the device pipeline** (§19). The server performs it
only to emulate that step for preview, via `?quantize=1`. That single page-wide pass
dithers the full-color icon/AI regions with an **ordered / clustered-dot screen** (not
Floyd–Steinberg), so they read as comic halftone and rhyme with the hand-authored CSS
backgrounds (which are already dithered and pass through largely untouched). This
choice has since been **validated on the physical panel** by the demo pipeline
([eink-demo.md](eink-demo.md)): its ordered mixing-plan dither + edge snapping +
vibrance boost is the intended basis for this pass, and §5.5 records what it taught
us about color choice.

### 5.3 Canonical halftone swatches

A proposed starting set, expressed as native inks plus an approximate dot
coverage. **All densities and angles are starting points to tune on the physical
panel.** E-ink guidance baked into these choices: keep the two inks in a blend
high in luminance contrast, use coarse, hard-edged dots, and use black sparingly
(it darkens a blend fast and muddies it). These rules are now backed by
measurements from the demo pipeline — §5.5 has the full findings and the
authoring rules they imply.

**Named blends (general palette):**

| Swatch | Inks | Recipe (approx.) |
|---|---|---|
| Orange | red + yellow | ~50% red dots on yellow |
| Lime | green + yellow | ~50% green dots on yellow |
| Teal | blue + green | ~50% blue dots on green |
| Purple | red + blue | ~50% red dots on blue |
| Periwinkle | blue + red | ~35% red dots on blue (blue-leaning) |
| Steel blue | blue + black | ~85% blue / ~15% black (cool, minimal black) |
| Pink | red + white | ~35% red dots on white |
| Light-blue (sky) | blue + white | ~40% blue dots on white |
| Mint | green + white | ~40% green dots on white |
| Cream / tan | yellow (+ red) + white | ~30% yellow dots on white, ~5% red |
| Light gray | black + white | ~20% black dots on white |
| Mid gray | black + white | ~50% black dots on white |
| Dark gray | black + white | ~75% black dots on white |
| Navy | blue + black | ~70% blue / ~30% black |
| Maroon | red + black | ~70% red / ~30% black |
| Brown (approx.) | red + green (+ black) | ~50/50 red+green, optional sparse black |
| Forest green | green + black | ~65% green / ~35% black |
| Amber | red + yellow | ~30% red dots on yellow (yellow-leaning orange) |
| Coral | red + white | ~55% red dots on white |
| Butter | yellow + white | ~40% yellow dots on white |

Forest green, amber, coral, and butter were added after on-panel testing (§5.5) —
all four are high-luma-contrast two-ink blends, the shape that renders best.
Purple and brown are the panel's **weakest** swatches (their inks sit close
together on the luma ladder, §5.5) — keep them for accents rather than large
fills.

**Day-cell assignments** (all halftone, cool weekdays / warm weekend; §9.1):

| Day | Swatch |
|---|---|
| Monday | Light-blue (sky) |
| Tuesday | Teal |
| Wednesday | Mint |
| Thursday | Periwinkle |
| Friday | Steel blue |
| Saturday | Orange |
| Sunday | Pink |

The same color is reused for that day's "today" highlight burst.

### 5.4 Comic-panel rendering primitives (the `comic_panel` macro)

Ben-Day fills (§5.2) and the panel frames are produced by the comic_panel macro:

```jinja
{% from "macros/comic.html" import comic_panel with context %}
{% call comic_panel(
     width=520, height=300, bg='rgb(225,220,202)',
     halftones=[
       {'color': 'rgb(70,110,170)', 'origin_angle': '270deg', 'magnitude': '70%'},
     ],
     border={'color': 'rgb(40,38,34)', 'radius': 16, 'mid_width': 12,
             'corner_width': 3, 'seed': day_seed}) %}   {# day_seed derived from the date #}
  <h1>Today</h1>
{% endcall %}
```

**Halftone field.** Each dict in `halftones` is one continuous Ben-Day field whose dots
shrink *smoothly* from an origin edge toward the center (no visible banding). Keys are
all optional; an omitted key falls back to the `comic.css` / filter default:

| Key | Meaning | Default |
|---|---|---|
| `color` | dot ink color | `rgb(187,180,162)` |
| `dot_size` | lattice pitch (px) | `6` |
| `origin_angle` | edge the dots are strongest at — CSS angle, `0deg`=top, `90deg`=right, `180deg`=bottom, `270deg`=left | `90deg` |
| `magnitude` | how far the dots reach in from that edge | `60%` |
| `max_fill` | peak dot radius as a fraction of the pitch: `0.5` touching, `>0.5` overlapping, `~0.71+` reads solid | `0.42` |
| `offset` | shift the dot lattice diagonally (px); `~dot_size/2` interleaves two otherwise-coincident fields | `0` |
| `transparency` | `0..1` dot see-through-ness (`0.8` = 80% transparent) so stacked fields blend | `0` |
| `uid` | optional stable filter id (only needed for the playground's live editing) | — |

`origin_angle` / `magnitude` / `max_fill` are emitted as CSS custom properties (cheap to
vary); `color` / `dot_size` / `offset` / `transparency` are baked into a per-field SVG
filter, and identical fields share one filter.

**Border.** `border` is a dict or `None`. It draws a single filled **vector** frame that
is thick at the middle of each edge and tapers at the corners, and it **rounds and clips
the panel** so the background only shows inside the frame. Because it is plain geometry
(no SVG filter) it stays crisp at any roughness. Keys:

| Key | Meaning | Default |
|---|---|---|
| `seed` | **required** — selects the deterministic thickness ripple | — |
| `color` | frame color | `rgb(40,38,34)` |
| `radius` | corner radius (px) | `0` |
| `mid_width` | thickness at edge midpoints (px) | = `corner_width` |
| `corner_width` | thickness at corners (px) | `4` |
| `roughness` | px amplitude of a smooth, pen-pressure thickness ripple; `0` = clean | `0` |
| `frequency` | number of ripple undulations around the perimeter | `6` |

### 5.5 What renders well — findings from the physical panel

The demo push pipeline ([eink-demo.md](eink-demo.md)) made color behavior on the
real panel measurable. Its quantizer renders every non-ink color as a mix of at
most **two** inks; that constraint, plus the panel's own ink properties, yields
the rules below. They matter chiefly for **image assets** — hand-made PNGs (the
day bursts, weather icons) and AI images — because authored CSS areas are already
explicit ink-dot patterns and pass through quantization untouched (verified).

- **The two-ink rule.** Every color reduces to ink coverage. A color expressible
  as *ink + white* (tint), *ink + black* (shade), or a §5.3 two-ink blend
  renders cleanly. A color needing **three** inks renders muddy and desaturated:
  the "bright" green `rgb(81,195,85)` is really ~45% green + 32% white + 24%
  black ink and read gray-green on the panel, while the exaggerated
  `rgb(54,214,60)` (~65% green) reads vibrant. When in doubt, push a color
  toward the nearest pure ink hue — this is worth reflecting in AI prompt
  templates too (§7.5): ask for *"pure saturated primary colors"*, not just
  *"bright colors"*.
- **The ink luma ladder:** black 0 → blue 29 → red 76 → green 150 → yellow 226 →
  white 255 (BT.601 luma). Blends between inks **far apart** on the ladder
  dither cleanly and vividly (orange, sky, mint, forest green); blends between
  **neighbors** read dark and muddy — purple (red+blue) and brown (red+green)
  are the panel's weakest colors. Yellow-based blends (orange, lime, amber) are
  the most vibrant things the panel can do. Equal-luma complementary mixes
  (yellow+blue "gray") are rejected by the quantizer outright — they'd be pure
  noise.
- **Tints need ≥ ~25% ink coverage to read as color.** A pale tint *is* mostly
  white: light green `rgb(231,246,228)` is ~7% green ink and reads as white with
  a faint stipple at arm's length (below ~5% the dots are dropped entirely). The
  §5.3 tint recipes (pink 35%, sky 40%, mint 40%) sit safely above the floor.
- **Near-neutrals render neutral.** Subtle warm/cool casts vanish: a flat cream
  fill quantizes to white + sparse black — i.e. light gray. Cream that must
  *read* cream needs the authored yellow-dot halftone of §5.3, never a flat
  fill. The flip side is protective: grays, paper tones, and text edges never
  sprout colored speckle.
- **Grays are dependable.** Black+white mixes render smooth and stable at every
  level; the gray ramp is the cleanest gradient the panel can show.
- **A vibrance boost is part of the pipeline** (luma-preserving, ~1.4×, leaves
  neutrals untouched — eink-demo §4). It buys mid-saturation colors roughly +20
  points of ink coverage, but pure inks are the ceiling — authoring near-ink
  hues is worth more than relying on the boost.
- **Physical inks are muted** relative to the pure sRGB primaries we author
  against, so on-panel saturation lands a notch below the preview even with the
  boost. Calibrating the quantizer's palette targets to measured ink colors is
  an open tuning task (§20).

---

## 6. Data layer

### 6.1 Sources — two ICS feeds

All event-like data comes from iCalendar (`.ics`) feeds, fetched over HTTPS from
secret, unauthenticated URLs and parsed uniformly:

1. **Family calendar** (Google Calendar private ICS) — regular events plus chores
   (chores distinguished by a `chore:` title prefix, §14).
2. **Anylist meal plan** (Anylist's iCalendar export) — dinner. Anylist generates an
   `.ics` subscription link for the meal-planning calendar, so dinner needs no
   scraping, no login/password in config, and no third-party API client.

Feeds are fetched with conditional-GET caching. **Feed cache freshness governs when
content actually updates within a day** (§3.4): the board re-reads a feed when its
cache is stale, and any change then flows into the next render and its `ETag`.

### 6.2 Recurrence

Events may recur, **including exceptions**. The parser expands `RRULE`s into concrete
instances for the target date and honors `EXDATE` and `RECURRENCE-ID` overrides (a
moved or cancelled occurrence), via a recurring-ICS expansion library.

### 6.3 Structured fields (TOML in the description)

The standard event fields — **start/end times, title, and the all-day flag** — are
modeled natively in the ICS. The **remaining, non-standard fields are stored in the
event description as TOML**.

**Model.** The TOML is parsed and validated into a Pydantic `EventOverrides` model
whose fields are exactly those in §6.4. The model is configured for **lenient** parsing,
so a malformed description degrades gracefully instead of dropping the event:

- A description that is empty, non-TOML, or not a top-level **mapping** (a bare scalar or
  a sequence) yields an all-defaults model — i.e. no overrides.
- `extra="ignore"` **drops unknown keys**.
- Each field uses lenient per-field validation: an invalid value (e.g. a non-integer
  `interesting`, or a `time_of_day` outside the allowed set) **falls back to that field's
  default** rather than raising and rejecting the whole event.

(The family calendar drives only this display, so descriptions never need free prose.)

### 6.4 Event model — the TOML-described fields

These are the fields of the `EventOverrides` Pydantic model (§6.3):

| Field | Type | Default | Notes |
|---|---|---|---|
| `time_of_day` | enum | derived | `morning` / `day` / `evening` override (see below). |
| `icon_description` | string | title | Fed to the AI image prompt instead of the title. |
| `interesting` | int (>0) | `100` | Higher = more interesting; drives ranking. |
| `labels` | list[string] | `[]` | Kid assignment and UI treatments (§8, §9.2). |
| `countdown_eligible` | bool | `false` | Eligible to appear in the Countdown module. |

**Time-of-day derivation** (when not overridden), in the configured timezone:
**morning** if the event ends at or before 09:00; **evening** if it starts at or after
16:00; otherwise **day** (anything overlapping the 09:00–16:00 window, plus all-day
events). So a 15:00–17:00 event is *day* (it starts before 16:00), a 16:00–17:00 event
is *evening*, and an 08:00–09:00 event is *morning*.

### 6.5 Regular vs. chore split

The parser splits the family calendar into **regular** events and **chore** events
(`chore:` prefix, case-insensitive, optional space) up front. Chores are excluded from
the day-of-week strip and from the today/tomorrow panels; they appear only in the
Chores module. Fields that don't apply to chores (`countdown_eligible`, `time_of_day`)
are ignored if set.

---

## 7. AI image pipeline

### 7.1 Image records and the logical key

Metadata for every **AI-generated** image lives in a **SQLite database** (`sqlite.db`
under `app_storage_path`, §18). (The hand-made weather and bugbug assets are not in this
table; they are static files, §7.6.) The main table has one row per image, with an
integer `id` primary key and a **unique index** over the logical key `(module,
item_description, width, height, variant)`:

- **`module`** — the owning UI module (`Calendar`, `Chores`, `Countdown`, `Dinner`,
  `Joke`, …).
- **`item_description`** — a *rough* key: a characteristic string taken from a source
  that has no stable upstream id of its own — a calendar event title (the event's
  `icon_description`, which defaults to its title, §6.4), a dinner menu-item name, a joke
  line, and so on. We key on this string directly. Two logical items that yield the same
  `item_description` **share one image**; that collision is the reuse mechanism and is
  fine for our small, hand-curated input space.
- **`width`, `height`** — the **logical display size** in pixels: the CSS box the
  image is shown in (and the basis of the generation size, §7.2). The stored PNG's
  own resolution is independent — it keeps its native generation resolution.
- **`variant`** — `NULL` by default. A non-null tag distinguishes a parallel version of
  the same logical image; its only current use is the bugbug host variant (§16), letting
  that variant coexist with its base.
- **`prompt`** (required) — the prompt used to generate the image (§7.2). Seeded by
  per-module construction logic (§7.5) when the record is created and **editable**
  afterward via the admin endpoint (§7.4).

**Prompt attachments.** The reference images passed alongside the prompt to the image API
(§7.2) live in a second table, **`prompt_attachments`**, whose meaningful column is a
**relative path** to an image under `prompt_images/` (§18). It is **many-to-many** with
the main table through a junction table, so one reference image can be attached to many
image records (e.g. a module's shared style examples) and one record can carry several
attachments.

**Files.** The rendered image bytes are stored as a flat **`<id>.png`** (the row id) under
`gen_images/` (§18); nothing is encoded in the filename. If a row exists for the key, its
image is used as-is.

### 7.2 Generation

Missing images are generated via the OpenAI image API, **inline** during `/render`
(§3.6). Cost is a non-issue (≈1–2 calls/day), so quality is the priority:

- **Model:** `gpt-image-2`, configurable per module.
- **Prompt:** the record's **`prompt`** column (§7.1, §7.5).
- **Style references:** the record's **prompt attachments** (§7.1) — typically the owning
  module's shared style examples — are passed as reference inputs so new images match the
  comic style (keep to ~2–3).
- **Transparency (background-keying).** `gpt-image-2` has **no transparent-background
  mode** (a `background:"transparent"` request errors), so the prompt asks for the subject
  on a **flat, solid key-color background** distinct from the subject's palette, and the
  alpha is then derived **in code** (Pillow + NumPy): starting from the image edges,
  background-connected pixels matching the key color (within a tolerance) are flood-filled
  to transparent, and the key is de-spilled at the subject's rim. Removing only
  *edge-connected* background — rather than every key-colored pixel — keeps a same-colored
  region *inside* the subject opaque. The board's bold, hard-edged, flat art (§5.2) keys
  cleanly; soft/wispy edges would not.
- **Size:** generated at a supported large size matching the record's aspect ratio
  (16× its `width`×`height`). Keying runs at that full resolution; the result is then
  **cropped to its visible pixels** (fully transparent borders are snapped away) and
  **stored at that native resolution** — no server-side downscale. The record's
  `width`×`height` is a *logical display size* only: CSS (`max-width`/`max-height`)
  aspect-fits the image into that box at render time, so the browser always
  downscales — never upscales — at any device scale factor (the e-ink path
  screenshots at 2× device scale, §19/eink-demo, and samples the icon at twice its
  CSS size), and that downscale anti-aliases the hard keyed alpha edge.

The final image is a **transparent PNG** written to `gen_images/<id>.png`, and the record
is saved. Failures (generation or keying) are **logged to disk** (with the item and the
prompt) and the render falls back (§7.3).

### 7.3 Fallbacks on a missing/failed image

- **Small icons** (calendar / chore / strip): a comic **fallback chip**.
- **Hero images** (countdown, dinner): the image is **omitted**; surrounding text
  remains (e.g. "Dinner" + the menu name).
- **Joke** (the panel is *entirely* the generated image): falls back to a comic-styled
  **HTML text** rendering of the joke line in a single bubble (§15).

These are rare because the warm-up (§3.6) generates the day's new images ahead of time.

### 7.4 Image admin endpoint

A separate Flask endpoint to browse and edit the device's images.

- **Default view** (no args): a list of links to every image record (by logical key).
- **`?img=<id>`:** view a single record in isolation — the current image, its `prompt`,
  and its prompt attachments — with controls to:
  - **edit the prompt** and **regenerate**, viewing the new image side-by-side with the
    old before saving or discarding;
  - **manage attachments:** show the associated prompt images and remove one, attach
    another existing reference image, or upload a new one (stored under `prompt_images/`,
    §18);
  - **upload a handcrafted image** to replace the current `<id>.png` outright, with no
    generation.

  Saves persist to the record and its files.

The `?debug_images=1` listing on a render (§3.5) links here per image.

**Security:** the admin endpoint is **unauthenticated** and can spend OpenAI budget on
regeneration. This is an accepted trade-off for a trusted home LAN and must not be
exposed beyond it.

### 7.5 Prompt construction

A record's `prompt` (§7.1) is built when the record is first created, by **per-module
logic**: each module knows how to turn its `item_description` into a generation prompt —
comic-book style, bold saturated colors, sharp edges, no fine detail — and modules that
consume similar data can share one builder. For example, calendar-derived icons use a
template like *"Generate a 40px-wide by 40px-tall icon representing &lt;event title&gt;,
in a kids' comic-book style: bold colors, hard edges, no fine detail."*

The same per-module step also seeds the record's **prompt attachments** (§7.1) with that
module's default style examples, drawn from `prompt_images/` (§18).

The prompt and attachments are then **editable** via the admin endpoint (§7.4); an edit
simply updates the row, so it persists across future renders and regenerations.

### 7.6 Serving images

The page references all images by **absolute loopback URLs** served by a Flask image
route, never by filesystem path — so Chromium, the rendered HTML, and the admin UI all
fetch over HTTP. There are two kinds:

- **AI-generated images** — generated icons and hero images — live as `<id>.png` under
  `gen_images/` (§18) and are served by id (e.g. `GET /images/generated/<id>`).
- **Handcrafted static assets** — the hand-made weather icons and clothing figures (§11)
  and the bugbug creature poses (§16) — are **not** in the image database. They are static
  files served by **name** (e.g. `GET /images/static/<name>`), authored once and shipped
  with the app, so they carry no row, `id`, or `prompt`.

---

## 8. Kid labels

Events may pertain to one kid or both. Instead of small face icons (the kids look alike
and tiny faces reproduce poorly), each item shows the **initials** of the kid or kids it
concerns, in a colorful comic font, taken from the event's `labels` field. A label value
matches a configured kid by that kid's **label (initials) or name, case-insensitively**.
An event with **no kid label is shared** (counts for both kids).

Initials mark the exception, not the rule: an item that applies to **all configured
kids** — shared, or explicitly labeled for every kid — shows **no initials**. Only an
item belonging to a proper subset of the kids is labeled. The **Today/Tomorrow panels
and the day strip behave identically in this regard**.

(A per-kid mascot icon, e.g. a lion vs. a giraffe, is an alternative distinguisher kept
open for later.)

---

## 9. Module — Day-of-week strip

A full-width strip across the top showing **Monday through Sunday** of the week
containing the target date, always in Mon–Sun order. Weekdays (Mon–Fri) are visually
grouped and the weekend (Sat–Sun) is a separate group, with a visible gap between them.
The full date prints in the top-left corner (e.g. "June 3, 2026").

### 9.1 Day cells

- Each cell shows the full day name on a small **solid (non-halftone) label plate** — a
  knockout band — so the comic type stays legible over the cell. (Exact treatment to be
  tuned on the physical panel.)
- Each day has a **distinct background**, consistent per day and never changing. All
  seven cells are **halftone blends** for a uniform Ben-Day texture; the proposed
  cool-weekday / warm-weekend assignments are in §5.3.
- **Today** is highlighted with a comic **burst** in that day's color. The burst is
  absolutely positioned with a higher z-index and is allowed to spill past its cell,
  overlapping neighbors and the row below (it may occlude the static "Today" /
  "Tomorrow" labels on some days — acceptable, since those labels are quickly learned
  and skipped).
- **Past days** in the current week render exactly like upcoming days.
- An empty day (no events) shows the name and halftone background, no icon.

### 9.2 Day icons

Each day shows one or two icons for the most interesting thing happening that day, using
the same icon images and size as the today/tomorrow panels but drawn directly on the
cell (no white plate). Chores are excluded.

**Candidacy.** An event is a candidate for a kid if it is **shared** (no kid label, so
it counts for both) or **labeled for that kid**. Events labeled for neither kid are not
candidates for the strip icons.

**Selection** — for each day, compute each kid's single most-`interesting` candidate:

- Both kids' top candidate is the **same event** → **one icon**.
- They differ → **two icons** side by side.
- **Only one kid has any candidate** that day → **one icon** (that kid's top candidate),
  labeled with that kid's initial.
- **Neither kid has a candidate** → **no icon** (the empty-day treatment).

**Labels** appear whenever any shown icon belongs to just one kid:

- A lone shared-event icon is **unlabeled** (the only label-free case).
- A solo icon is labeled with that kid's initial (e.g. `S`).

---

## 10. Module — Today

Occupies the full left column. Calendar entries are bucketed into **Morning**, **Day**,
and **Evening** panels; empty buckets are not shown (their header is dropped and the
remaining buckets expand to reclaim the space). Each event renders as a fixed-height
row: AI icon + kid label(s) + title.

### 10.1 Cap and bucketing (resolution order)

The available height is the column minus the fixed weather subpanel. The visible-header
count is circular (which headers show depends on which events survive, which depends on
the cap, which depends on the header count), so it is resolved in a fixed order:

1. Compute the event-row budget **assuming the worst case of all three headers present**:
   `N = floor((available_height − 3·header_height) / row_height)`.
2. Take the **global top-N** events for the day by `interesting`.
3. Bucket the survivors into Morning / Day / Evening and **drop any empty bucket's
   header**.
4. **Backfill:** if fewer than three headers ended up visible, the freed header rows are
   spare space; fill them with the next events by `interesting`, but **only if they fall
   into an already-visible bucket** (never creating a new header), until the freed rows
   are used. This terminates and stays deterministic.

### 10.2 Ordering within a bucket

Selection (the cap) is by `interesting`, but **display order within a bucket is
chronological by start time** (all-day events first within Day), with ties broken by
`interesting` then title — so the cap is by interestingness while the reading order is
by time, matching Tomorrow.

### 10.3 Weather subpanel

At the bottom of the column. Today's weather subpanel, left to right: **condition icon →
clothing kid → temperature bar** (§ Weather).

---

## 11. Module — Tomorrow

Top of the right column. Same event rows and kid labels as Today, with these
differences:

- **No** morning/day/evening buckets; events are listed in **chronological** order by
  start time, ties broken by `interesting` then title.
- Fewer events typically fit (smaller panel), via the same row-budget logic.

Tomorrow's **weather subpanel**, left to right: **clothing kid → temperature bar** (no
condition icon).

---

## Module — Weather (shared by Today and Tomorrow)

### Data

From the **Google Maps Platform Weather API** daily forecast. Configuration supplies a
Google Maps API key and a latitude/longitude. We request **imperial units** (the API
defaults to Celsius). For each date we read the **daytime** forecast:
`weatherCondition.type`, `precipitation.probability` (percent and RAIN/SNOW type),
`thunderstormProbability`, cloud cover, and the day's high. Google's own condition icons
are ignored.

### Condition icon (Today only)

A single shared set of **seven** hand-made icons: **sunny, partly cloudy, cloudy, light
rain, rain, thunder, snow**. Google's long condition enum is mapped onto these seven,
leaning on `thunderstormProbability` and the precip RAIN/SNOW type for the
rain/thunder/snow buckets and on cloud cover for sunny/partly/cloudy. The exact
enum→bucket table is TBD (§20).

### Clothing kid (Today and Tomorrow)

A figure of the relevant kid showing what to wear. **Four outfits** per kid — hot,
normal, cold, rain — i.e. **eight** hand-made figures. Selection for the day:

- daytime **PoP ≥ 25%** → **rain gear** (overrides temperature);
- otherwise by the day's high: **< 60°F** cold, **60–72°F** normal, **> 72°F** hot.

**Flip-flop:** which kid is featured in Today vs. Tomorrow alternates each day,
deterministically from the date seed.

### Temperature bar (Today and Tomorrow — identical UI)

A CSS-rendered vertical bar of five bands with an arrow pointing to the day's high.
Segments use the palette (light-blue and orange are halftone blends):

| Band | High (°F) | Color |
|---|---|---|
| Coldest | ≤ 50 | blue |
| Cold | 51–59 | light-blue (halftone) |
| Mild | 60–67 | yellow |
| Warm | 68–75 | orange (halftone) |
| Hottest | ≥ 76 | red |

These five bands are deliberately **not** the three clothing cutoffs — two separate
systems sharing one input (the high). Optional decorative sun/snow endcaps may reuse the
condition icons. The bar needs no hand-made images.

### Hand-made image inventory

Seven condition icons + eight clothing figures = **15 images**. Being hand-made rather
than generated, they are **static assets served by name** (e.g. `sunny`, `<kid>_rain`),
not rows in the image database (§7.6). Pixel sizes are pinned at layout time (kid figures
run taller than the square condition icons). These are the only non-AI images in the
system, alongside the bugbug creature poses (§16).

---

## 12. Module — Countdown

A counter to the next exciting event, visually the most prominent panel on a typical day
(a comic burst-bordered card).

### Target and value

- **Target:** the upcoming `countdown_eligible` event chosen by, in order: **soonest
  event date**, then **highest `interesting`**, then **earliest start datetime**, then
  **title** (a stable final tiebreak).
- **Sleeps:** whole calendar nights between the target date and the event day, in the
  configured timezone (an event tomorrow is "1 sleep").
- On the **event day** the line becomes **"It's today!"** (the zero-state), keeping the
  description and hero image and dropping the moons; the next day it rolls to the next
  eligible event.
- If **no eligible event** is ever found (error/misconfig), the panel renders as a
  **blank card** so its footprint is preserved.

### Layout

Event description (the event **title**) at the top, then a freshly generated **hero
image** (§7) — its own cache key, hero size, more-detailed prompt; never the small
calendar icon upscaled — then "N sleeps to go!". A **moon row** (one small crescent
asset repeated N times) appears when the sleep count is at or under the number of moons
that **fit in the available space** — that fitting count *is* the threshold; it is not a
configured value. On a hero-image miss the image is omitted and description + sleeps +
moons remain.

### Escalating intensity

The panel gets louder as the event nears, driven by the sleeps value (deterministic),
changing **intensity, not footprint**:

- **Configurable tiers** keyed to sleeps, defaulting to: *calm* when far off, *excited*
  once within the (space-derived) moon range, *hype* at 1 sleep, and the *peak* "It's
  today!" state.
- Each tier ramps the visual treatment — burst size, motion-line density, star count —
  within the fixed panel size.
- Copy escalates too: plain **"N sleeps to go!"** for most of the run, an emphatic
  **"Just 1 more sleep!!"** at 1, **"It's today!"** at 0.

---

## 13. Module — Dinner

Shows "Dinner", an AI image of the meal, and the menu name(s).

- **Source:** the Anylist meal-plan ICS (§6.1). Anylist is used only for dinner, so the
  meal-plan entries on the target date **are** the dinner — no meal-slot filtering. A
  main plus any side are **joined into one name and one combined image**.
- **Image:** keyed by the dish name (reused across days), hero-sized, a transparent PNG
  generated like any other (§7.2); omitted on a generation miss (leaving "Dinner" + the
  name).
- **Mystery dinner fallback:** a fixed "question-marks" card titled "Mystery dinner!" is
  shown both when **no dinner is planned** and when the **ICS fetch fails** (the failure
  is logged) — the same friendly visual for either cause.

---

## 14. Module — Chores

Today's chores (the `chore:`-prefixed events for the target date only). Mechanically
identical to a today/tomorrow list:

- Each chore is a row: AI icon (its own `Chores__…` example set and cache keys) + kid
  label(s) + title.
- The `chore:` prefix (case-insensitive, optional space) is stripped before display.
- Sorted by `interesting`, then title; capped by the same row-budget geometry; **no**
  morning/day/evening buckets.
- Recurrence works for free via the shared parser (e.g. a daily "make bed").
- Non-applicable fields (`countdown_eligible`, `time_of_day`) are ignored.
- **Empty state:** a "No chores today!" comic card.

---

## 15. Module — Joke / riddle

One joke or riddle per day, rendered as a wholly AI-generated comic panel (the text is
drawn inside the image).

- **Source:** a configurable UTF-8 text file, one joke/riddle per line. Blank lines are
  ignored and `#`-prefixed lines are comments, so N is the count of real jokes.
- **Selection:** index = (target date − configurable start date) in whole days, **modulo
  N**, so the list loops. The debug date arg selects a different joke.
- **Image:** the whole line is handed to the prompt; the image is keyed by the joke line
  itself (its `item_description`, §7.1), generated once, and reused each cycle.
- **Riddles:** the answer is shown alongside the question (there is no interactivity to
  "reveal" it).
- **Fallback (rare):** the joke line rendered as comic-styled **HTML text in a single
  bubble**, with no setup/punchline splitting.

---

## 16. Module — Bugbug (seek-and-find)

A tiny configurable character (a white labrador by default) is hidden somewhere on the
page each day — small, often partly obscured, in varying and surprising places, taking a
minute or two to find.

### Placement

A single **placement pass during HTML generation** injects one positioned element at the
chosen spot, so the normal Chromium screenshot captures it — there is **no** separate
image-editing step. Modules stay oblivious to the bugbug.

It draws from a curated **registry of hiding spots**, each just data:

- an **anchor** (a panel corner, a border seam, the gap between the weekday and weekend
  groups, the moon row, a speech-bubble tail, …),
- a **hiding style** (in front; tucked behind so it peeks from an edge; or straddling a
  panel's `overflow:hidden` boundary so it's half-cut-off),
- allowed **jitter, rotation, and flip**.

`hash(date + "bugbug")` picks one spot and its jitter — deterministic and stable all day
(it never teleports between refreshes; the debug date arg previews any day's hiding
place). Curating a varied, clever spot list is what makes placement feel surprising; the
behind/clipped styles provide the "partially obscured" look.

### Creature asset

A small transparent PNG at a configurable path, with a few **poses** (peeking, sitting,
lying) so it isn't the identical sprite daily; the registry can favor a pose that suits a
spot.

### In-image hiding (v1)

Some days the bugbug is hidden **inside a regenerated AI image** (e.g. the dog
photobombing the soccer icon or the taco dinner). Any AI image on the page is eligible to
host, and the seed picks which.

- The host's **bugbug variant** is generated by the base prompt plus an instruction to
  *"subtly tuck a tiny white labrador into the scene,"* and is a drop-in replacement for
  that image that day.
- It is stored as its **own image record** — a separate row sharing the host's `(module,
  item_description, width, height)` but with `variant = bugbug` (§7.1) — so the base image
  is untouched.
- The warm-up generates the day's host variant ahead of time.
- **Fallback:** if the variant isn't ready at render time, the panel shows the normal
  image and the placement pass falls back to a CSS-overlay spot — so there is always
  exactly one bugbug somewhere.

---

## 17. Weekday / weekend treatment

So kids can tell at a glance what kind of day it is, a single global **backdrop layer**
sits behind and between the panels, with a configurable weekday look vs. weekend look
(in-palette: cool weekday, warm weekend), driven by the target date. It changes no layout
and no individual panel — it just reinforces the warm-weekend cue the day-strip already
carries.

This backdrop is built as the **base case of the theming layer** that v2 seasonal themes
(and the birthday/special-person mode) will extend via date-range → theme overrides.

---

## 18. Configuration (consolidated)

The configuration is a **Pydantic model** (`pydantic-settings.BaseSettings`), loaded
once at startup from a config file and/or environment variables. Pydantic gives typed
fields, declarative defaults, and validation at load time, so a malformed or missing
config fails fast with a clear error rather than surfacing later as a render bug.
Secrets — the OpenAI key, the Google Maps key, and the private ICS URLs — are typed as
`SecretStr`. The same Pydantic validate-and-coerce-to-default approach naturally covers
the per-event TOML fields in §6.3. The fields:

| Key | Purpose |
|---|---|
| `timezone` | Display timezone (e.g. `US/Pacific`); drives date resolution. |
| `latitude`, `longitude` | Weather location. |
| `google_maps_api_key` | Weather API auth. |
| `family_calendar_ics_url` | Google Calendar private ICS (events + chores). |
| `anylist_mealplan_ics_url` | Anylist meal-plan ICS (dinner). |
| `openai_api_key` | AI image generation. |
| `app_storage_path` | **Required** root for all app-managed storage, created/written as needed: `sqlite.db` (image metadata, §7.1), `gen_images/` (rendered `<id>.png` files, §7.6), and `prompt_images/` (prompt-attachment images, including each module's default style examples, §7.1). |
| `module_model_tiers` | Per-module image-model overrides. |
| `kids` | Names and initials (label text), pose/figure mapping. |
| `joke_file_path`, `joke_start_date` | Joke source and base date. |
| `countdown_tiers` | Escalation cutoffs and treatments. |
| `refresh_cadence` | **Crontab schedule string** for the warm-up prerenders (§3.6). The device's own refresh happens whenever the ESP32 polls `/display`. |
| `weekday_backdrop`, `weekend_backdrop` | Global theming backdrops. |

**Location.** The model lives in `server/app/config.py`. Actual values are read from
`server/config.toml` (gitignored) and/or `KIDINK_`-prefixed environment variables
(env wins over the file).
`server/config.example.toml` is the committed template documenting the shape.

---

## 19. Deferred / v2

- **ESP32 firmware:** wake on RTC → connect Wi-Fi → conditional GET on `/display` →
  draw → deep sleep; retry gracefully and keep showing the last image when the
  server/Wi-Fi is unreachable.
- **Final on-device file:** quantize + ordered-dither to the six colors and pre-pack the
  controller framebuffer in the expected bit layout. (The quantize step is built early
  for preview via `?quantize=1`; the packing and the choice between serving a PNG vs. a
  packed buffer depend on the firmware stack, TBD.) A standalone demo of this whole
  path — screenshot → six-color dither → packed buffer → flash — exists already; see
  [eink-demo.md](eink-demo.md). Its `app/eink/` palette/dither core is the intended
  basis for `?quantize=1`.
- **Seasonal theming** (date-range → theme) and **birthday / special-person mode**, both
  via the theming layer in §17.
- **Visual polish** explored in review and planned for a later mockup, with no software
  change needed now: breaking elements out of panel frames, per-panel border
  personalities, treating the gutters as part of one comic page, and caption-box /
  speech-bubble headers.
- **Fun facts** added to the joke file as additional lines (no software change — a fact
  is just another line).

---

## 20. Open items / TBD

- The exact **Anylist meal-plan ICS encoding** (how, or whether, meal slots appear), to
  be confirmed against the real feed.
- The **Google Weather condition enum → 7-bucket** mapping table, enumerated against the
  API reference.
- Concrete **pixel sizes** for icons, kid figures, panels, and the header/row heights
  that drive §10's budget, fixed at layout time.
- Final **halftone densities/angles** for the §5.3 swatches, tuned on the physical panel,
  and **calibration of the quantizer's palette targets** (eink-demo §4) to measured
  Spectra ink colors (the vibrance-boost factor will want retuning with it, §5.5).
- The **firmware stack** (Inkplate Arduino library vs. ESP-IDF/raw), which determines
  what "a file ready for rendering" is on the wire. The push demo
  ([eink-demo.md](eink-demo.md)) has meanwhile verified the Inkplate Arduino library's
  buffer format and dither behavior end-to-end on the device.