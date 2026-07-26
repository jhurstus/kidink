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

The ESP32 firmware and the on-device file format are **built** — see
[firmware.md](firmware.md). The device wakes on a crontab schedule (or its WAKE
button), does a conditional GET on `/display`, and paints only when the server
returns a frame.

---

## 3. System architecture

### 3.1 Two endpoints, the ETag, and conditional GET

- **`/render`** builds the page (HTML + CSS) for a date and returns it as a normal
  `text/html` response. It does **not** write to disk.
- **`/display`** produces the device-facing image: it points headless Chromium at
  this server's own **`/render`** URL, screenshots the page, runs the six-color
  quantize + dither pass (§5.2), and serves the **packed 4bpp framebuffer** with
  `ETag` / conditional-GET support. It writes **no files to disk** (Chromium loads
  the page and its assets over loopback HTTP, §3.2). **All query args on `/display`
  are forwarded to the `/render` URL.**

The ESP32 polls `/display`. The **`ETag` is a hash of the served bytes**, so *any*
change - including a regenerated AI image - changes the `ETag`. The device sends
`If-None-Match`; on a match it gets `304 Not Modified` and skips both the download
and the ~19-second refresh (the main battery win), while continuing to show its last
image (e-ink is bistable). Computing the `ETag` requires rendering and packing the
buffer, which is acceptable at the polling cadence.

### 3.2 The two endpoints in detail

- **`/render` — pipeline steps 1–4** (§3.3). Returns `text/html`. Writes no output
  file; AI image generation still persists images to the durable image cache (§7),
  which is expected. Accepts the debug query args (§3.5). All `<img>` and asset URLs
  in the returned HTML are **root-relative Flask URLs** served by the local image
  route (§7.6), so any browser loading the page fetches them from the same server
  over HTTP rather than from the filesystem.
- **`/display` — pipeline steps 5–7** (§3.3). Navigates headless Chromium to the
  absolute loopback `/render` URL - built from the incoming request's own host, all
  query args forwarded - so Chromium fetches the HTML and every asset over loopback
  from this same server (no disk writes anywhere); then quantizes, packs, and serves
  the buffer with the served-bytes `ETag`. Because Chromium calls back into the
  server while the `/display` request is still in flight, the server must handle
  **concurrent requests** (a threaded WSGI server - the Flask dev server's default).
- **Image admin endpoint** (§7.4).
- **`/admin`** — an index of the admin pages: a plain alphabetized bulleted list of
  links to every parameterless `GET /admin/<page>` route (e.g. `/admin/images`,
  `/admin/weather`), derived from the URL map so new admin pages list themselves.

**Device contract.** The ESP32 firmware is a minimal HTTP/1.1 client: `GET /display`
with `If-None-Match: "<stored etag>"` (omitted on cold boot / button wake),
`Accept: application/octet-stream`, `Accept-Encoding: identity`, and
`Connection: close`; it follows no redirects and speaks no auth or TLS. The server
answers `200` with `Content-Type: application/octet-stream`, `Content-Length`, the
`ETag`, and the **960,000-byte buffer** - 1600 × 1200 at 4 bits per pixel, two pixels
per byte, high nibble = left pixel, each nibble the palette index shifted left by one
(the Inkplate `drawBitmap3Bit` layout the push demo verified on-panel,
[eink-demo.md](eink-demo.md)) - or `304 Not Modified` (still carrying the `ETag`) on
a match.

### 3.3 Render pipeline stages

**`/render` (steps 1–4):**

1. Resolve the target **date** (§3.4).
2. Fetch source data (calendar ICS, meal-plan ICS, weather) and build view models.
3. Ensure AI images exist for the view models, generating any that are missing inline
   (§7).
4. Render modular Jinja2 templates to HTML + CSS (plain markup, **no JavaScript** —
   the panel is not interactive) and return it.

**`/display` (steps 5–7):**

5. Navigate headless Chromium (Playwright) to the `/render` URL and capture a
   **1600 × 1200** screenshot, waiting for all fonts and `<img>` icons (fetched from
   the Flask image route) to finish loading first. The capture runs at
   `deviceScaleFactor: 2` and is BOX-downscaled to 1600 × 1200 (§7.2, eink-demo §4),
   feeding the quantizer the same anti-aliased edges the on-panel tests validated.
6. *(Quantize + pack)* Run the single page-wide six-color quantize + dither pass
   (§5.2) and pack the palette indices into the 4bpp device buffer (§3.2). The
   `?raw=1` / `?quantize=1` debug args (§3.5) substitute PNG views of the
   intermediate stages.
7. Serve the buffer as `application/octet-stream` with the served-bytes `ETag` /
   `304`.

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
- **`?quantize=1`** — serve the §5.2 quantize pass's output as a viewable PNG instead
  of the packed device buffer: exactly the pixels the panel will show (consumed by
  `/display`, step 6).
- **`?raw=1`** — serve the raw full-color screenshot as a PNG, before the quantize
  pass; takes precedence over `?quantize=1` (consumed by `/display`, step 6).
- **`?debug_images=1`** — after the main content, append a list of **every AI image
  included in the render**: each image's id and logical key and a link to the image admin
  endpoint (§7.4, via its `img=` arg) to view and edit that image's prompt (consumed
  by `/render`).
- **`?weather_icon=<name>` / `?weather_outfit=<name>` / `?weather_temp=<int>`** —
  override the corresponding slice of the weather subpanels (§ Weather), for
  previewing any icon/outfit/bar state: `weather_icon` takes a condition bucket
  name (`sunny`, `partly_cloudy`, `cloudy`, `light_rain`, `rain`, `thunder`,
  `snow`), `weather_outfit` an outfit name (`hot`, `normal`, `cold`, `rain`), and
  `weather_temp` an integer °F "feels like" high that both the temperature bar
  and the outfit derivation use. Overrides apply to **both** panels; a value outside the
  supported names is a 400. `weather_temp` alone is enough to render the
  subpanels even when no forecast is available, and when **all three** are set
  the weather fetch is skipped entirely (consumed by `/render`).
- **`?countdown_sleeps=<int>`** - overrides the Countdown module's computed sleeps
  value (§12) - and the tier, copy, and SFX derived from it - for previewing any
  escalation tier against the real target event. Ignored on the blank no-event card;
  a non-integer is a 400 (consumed by `/render`).

### 3.6 Warm-up prerenders

Image generation is **inline** inside `/render` (steps 3–4; the bugbug variant in §16
is generated during step 4's placement pass). There is no separate image-collection
subsystem.

To keep the device-facing `/display` fast and complete, a cron schedule (a config key
still to be added, §18) fires **throwaway prerenders** of the **next date**:
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
| CSS authoring | Plain CSS with custom properties |
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

- **Top:** the full-width day-of-week strip (§9), with the full date in the today
  panel's bottom caption band.
- **Left column:** the **Today** panel (§10), including its weather subpanel at the
  bottom.
- **Right column:** **Tomorrow** (§11) spanning the top, then a **2 × 2 grid**
  filling the rest of the column in reading order: **Countdown** (§12) and
  **Dinner** (§13) across the first row, **Chores** (§14) and the **Joke** (§15)
  panel across the second.

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
set of halftone blends** (§5.3), all treated as first-class colors. Swatches are
authored in CSS as ordinary flat fills; the §5.2 quantize + dither pass
is what turns a blend into its two-ink halftone. The CSS render maps predictably
onto what the panel can display.

### 5.2 Rendering paths and the single quantization pass

- **Authored areas** (backgrounds, panel fills, text, day cells, the temperature bar —
  any large color swath or load-bearing color) are **flat CSS fills** picked from the
  §5.3 palette. Hand-authoring the dot pattern in CSS (layered radial-gradients of red
  dots on yellow to make "orange", with dot size, spacing, and angle under our control)
  was tried and dropped: the device pass already dithers a flat fill into that same
  blend, and flat won by eye — so the authored side stays simple and the screen stays
  consistent across the whole page.
- **Icons and AI images** are carried in the page as full-color, transparent PNGs
  composited onto their cells.

There is exactly **one** quantization pass, and it runs **server-side inside
`/display`** (§3.3 step 6), on the 2×-supersampled, BOX-downscaled screenshot: the
eink-demo core's ordered mixing-plan dither + edge snapping + vibrance boost
(saturate 1.4, ycc metric, edge snap 48, edge gamma 1.5), whose packed output is what
the device draws; `?quantize=1` serves the same pass as a viewable PNG and `?raw=1`
the screenshot before it. That single page-wide pass
dithers everything — flat authored fills and full-color icon/AI regions alike — with an
**ordered / clustered-dot screen** (not Floyd–Steinberg), so the whole page reads as one
comic halftone under one screen, at one dot size and angle. This
choice has been **validated on the physical panel** by the demo pipeline
([eink-demo.md](eink-demo.md)), and §5.4 records what it taught us about color
choice.

### 5.3 Canonical halftone swatches

A proposed starting set, expressed as native inks plus an approximate dot
coverage — i.e. what the quantizer resolves each swatch into, not a pattern we
draw by hand (§5.2). **All coverages are starting points to tune on the physical
panel**, and the dot size and angle are the ordered screen's, page-wide.
E-ink guidance baked into these choices: keep the two inks in a blend
high in luminance contrast, use coarse, hard-edged dots, and use black sparingly
(it darkens a blend fast and muddies it). These rules are now backed by
measurements from the demo pipeline — §5.4 has the full findings and the
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

Forest green, amber, coral, and butter were added after on-panel testing (§5.4) —
all four are high-luma-contrast two-ink blends, the shape that renders best.
Purple and brown are the panel's **weakest** swatches (their inks sit close
together on the luma ladder, §5.4) — keep them for accents rather than large
fills.

**Day-strip caption bands** (§9.1) each get their own colour: a light-tint set
running orange, yellow, lime, green, pink (Mon–Fri), then a **matching
light-blue pair for the weekend** (Sat + Sun). All are kept high in luminance so
the **black caption text stays crisp**: the yellow/green-based days (Mon–Wed)
can be boldly saturated because those are light inks, but the days whose hue
needs a darker ink — green, and especially red or blue — must stay light tints,
since a bold amount of those speckles dark dots that fuzz the black text (§5.4).
Purple and cyan/teal are unavailable here: as a light tint the panel drops one
of their two inks (so a lavender renders as plain blue), and a saturated version
is dark and muddy (§5.4) — which is why the seventh distinct hue isn't possible
and the weekend simply shares the blue. The wider gutter before Saturday still
carries the weekday/weekend split.

### 5.4 What renders well — findings from the physical panel

The demo push pipeline ([eink-demo.md](eink-demo.md)) made color behavior on the
real panel measurable. Its quantizer renders every non-ink color as a mix of at
most **two** inks; that constraint, plus the panel's own ink properties, yields
the rules below. They apply to **every color on the page** — authored CSS fills
and image assets (hand-made PNGs, AI images) alike — since the one quantization
pass (§5.2) dithers all of them through the same screen.

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
  fill quantizes to white + sparse black — i.e. light gray. Anything that must
  *read* cream has to be authored well past near-neutral, at the §5.3 tint's
  yellow coverage. The flip side is protective: grays, paper tones, and text
  edges never sprout colored speckle.
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
| `icon_description` | string | title | Elaborates the AI image prompt in a paragraph of its own; the event title stays in place. Remains the image's logical key (§7.1). |
| `interesting` | int (>0) | `100` | Higher = more interesting; drives ranking. |
| `kids` | list[string] | `[]` | Kid assignment (§8, §9.2); aligns with the app config's `kids` list (§18). |
| `countdown_eligible` | bool | `false` | Eligible to appear in the Countdown module. |
| `sfx` | string | unset | Comic SFX shout text (e.g. `"Yum!"`); opts the event into the Today panel's single SFX slot (§10.4). Today only — Tomorrow and Chores ignore it. |

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

- **`module`** — the owning UI module (`Calendar`, `Chores`, `Countdown`, `DayStrip`,
  `Dinner`, `Joke`, …).
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
attachments. Day-to-day usage (the `{{...}}` prompt tokens and the defaults directory
convention) is covered in §7.7.

**Files.** The rendered image bytes are stored as a flat **`<id>.png`** (the row id) under
`gen_images/` (§18); nothing is encoded in the filename. If a row exists for the key, its
image is used as-is.

### 7.2 Generation

Missing images are generated via the OpenAI image API, **inline** during `/render`
(§3.6). Within one render, a batch's missing images are generated **concurrently**
(records are created serially first, so id assignment stays deterministic, §3.4) —
a cold day costs roughly one generation of wall-clock, not one per image. Cost is
a non-issue (≈1–2 calls/day), so quality is the priority:

- **Model:** `gpt-image-2`, configurable per module.
- **Prompt:** the record's **`prompt`** column (§7.1, §7.5).
- **Style references:** the record's **prompt attachments** (§7.1) — typically the owning
  module's shared style examples — are passed as reference inputs so new images match the
  comic style (keep to ~2–3). The effective set is the union of the record's attached
  images and any `{{...}}` token references in its prompt, resolved at generation time
  (§7.7); the outgoing prompt cites them as "reference image N".
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
  screenshots at 2× device scale, §3.3/eink-demo, and samples the icon at twice its
  CSS size), and that downscale anti-aliases the hard keyed alpha edge.

The final image is a **transparent PNG** written to `gen_images/<id>.png`, and the record
is saved. Failures (generation or keying) are **logged to disk** (with the item and the
prompt) and the render falls back (§7.3).

### 7.3 Fallbacks on a missing/failed image

- **Small icons** (calendar / chore / strip): a comic **fallback chip**.
- **Hero images** (countdown, dinner): the image is **omitted**; surrounding text
  remains (e.g. the menu name).
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
module's default style examples, drawn from the convention directory
`prompt_images/defaults/<module lowercase>/` (§7.7, §18).

The prompt and attachments are then **editable** via the admin endpoint (§7.4); an edit
simply updates the row, so it persists across future renders and regenerations.

### 7.6 Serving images

The page references all images by **absolute loopback URLs** served by a Flask image
route, never by filesystem path — so Chromium, the rendered HTML, and the admin UI all
fetch over HTTP. There are three kinds:

- **AI-generated images** — generated icons and hero images — live as `<id>.png` under
  `gen_images/` (§18) and are served by id (e.g. `GET /images/generated/<id>`).
- **Handcrafted static assets** — the hand-made weather icons and clothing figures (§11)
  and the bugbug creature poses (§16) — are **not** in the image database. They are static
  files served by **name** (e.g. `GET /images/static/<name>`), authored once and shipped
  with the app, so they carry no row, `id`, or `prompt`.
- **Prompt-attachment images** (§7.1) are served by their relative path under
  `prompt_images/` (e.g. `GET /images/prompt/styles/comic-dog.png`); the admin
  attachment previews (§7.4) use this route. The route rejects any path that would
  escape `prompt_images/`.

### 7.7 Prompt attachments in practice

Usage notes for the §7.1 attachment machinery:

- **`{{<path>}}` prompt tokens.** Any prompt text may pull in a reference image by
  naming its relative path under `prompt_images/` in double braces, e.g. *"Draw the
  dog in the style of `{{styles/comic-dog.png}}`"*. At generation time the named
  image is attached as a reference input and the token is rewritten in the
  **outgoing** prompt: "the attached reference image" when it is the only image
  sent, otherwise "reference image N", where N is the image's position among
  **all** images sent. An edit-from-base image (§7.2) occupies the first slot,
  so it both forces the numbered form and shifts the numbering (base plus one
  attachment cites that attachment as "reference image 2"). The **stored**
  prompt keeps its tokens, so they survive admin edits and regenerations. Because
  an event's `icon_description` (§6.4) feeds the prompt template, tokens work
  straight from the calendar: an event description of
  `icon_description = "soccer ball, drawn like {{styles/comic-dog.png}}"` attaches
  the reference with no database work.
- **Module defaults by convention.** Every image dropped into
  `prompt_images/defaults/<module lowercase>/` (e.g. `defaults/calendar/`) is that
  module's shared style example set: when a record is **first created**, all files
  there are attached automatically, in sorted-filename order. Seeding happens only
  at creation, so later changes to the directory affect only future records, and
  detaching a default from a record in the admin sticks (regeneration does not
  re-add it).
- **Resolution order.** At generation time the record's attached images come first
  (in attachment-creation order, defaults before later admin attaches), then token
  references in first-appearance order, deduplicated by path: a path that is both
  attached and token-referenced appears once, at its attached position. An
  attachment whose file is missing (or whose path is invalid) is logged and
  skipped, its tokens are dropped from the outgoing text, and the ordinals
  renumber over the survivors; generation itself never fails over a style
  reference (a slightly off-style image beats a §7.3 fallback).
- **Managing attachments** happens on the §7.4 image detail page: it previews the
  record's attachments (flagging a missing file), and can detach one, attach any
  already-known reference image, or upload a new one to a chosen path under
  `prompt_images/`. Reference images may be **PNG, JPEG, or WebP** (what the
  image API accepts as reference inputs); uploads are validated against that
  set, and the file extension must match the actual content. Detaching removes
  only the link (the file and its uses on other records survive), and uploads
  never overwrite an existing file with different content, since a reference
  may be shared across records; re-uploading identical bytes simply attaches
  the file (idempotent retries, and a way to adopt a hand-dropped file into
  the picker).

---

## 8. Kid labels

Events may pertain to one kid or both. Instead of small face icons (the kids look alike
and tiny faces reproduce poorly), each item shows the **initials** of the kid or kids it
concerns, taken from the event's `kids` field (§6.4 — named to align with the app
config's `kids` list, §18). Each initial rides in a small **badge** - a square with
generously rounded corners, filled with that kid's color, the initial in white, a black
border around the shape - superimposed on the bottom-right corner of the item's icon.
A `kids` entry matches a configured kid by that kid's **label (initials) or name,
case-insensitively**.
An event with an **empty `kids` field is shared** (counts for both kids).

Initials mark the exception, not the rule: an item that applies to **all configured
kids** — shared, or explicitly labeled for every kid — shows **no initials**. Only an
item belonging to a proper subset of the kids is labeled. The **Today/Tomorrow panels
and the day strip behave identically in this regard**.

(A per-kid mascot icon, e.g. a lion vs. a giraffe, is an alternative distinguisher kept
open for later.)

---

## 9. Module — Day-of-week strip

A full-width strip across the top showing **Monday through Sunday** of the week
containing the target date, always in Mon–Sun order, as **seven free-standing comic
panels**. Weekdays (Mon–Fri) and the weekend (Sat–Sun) are split by a visibly wider
gutter, and the weekend panels' caption bands are warmer-colored (§5.3). The full date
prints in a caption band across the bottom of the **today** panel (e.g. "JUNE 3,
2026").

### 9.1 Day panels

- Each day is its **own free-standing panel**: white body, thick (4px) black border,
  near-square corners, a slight gutter between neighbors — wider between Friday and
  Saturday.
- A **caption band** across the top of each panel carries the full day name on its
  own per-day colour (a Mon..Sun spectrum kept light enough for crisp black text,
  §5.3); the wider weekday/weekend gutter carries that split instead.
- The day's art (§9.2) fills the panel body **edge to edge**, cover-cropped by the
  panel: a full-bleed **opaque** scene (no §7.2 transparency treatment — the strip's
  own image module `DayStrip` is unkeyed) generated at a logical 200×200 under a
  full-bleed prompt: bold flat fills, thick black outlines, pure saturated primary
  colors, minimal detail.
- **Today** is the hero panel: **30% wider and taller** than its six siblings,
  with all the extra height popping above their tops — its bottom border stays on
  the shared bottom line. It **always shows a single image**,
  never the torn two-image split (§9.2) — a two-pick day collapses to its one
  most-interesting candidate. That art is swapped for an **"excited" variant**
  regenerated from the base image via an edit-from-base comic-excitement prompt,
  exactly like the §12 countdown hero (own `variant = excited` record; a variant
  miss falls back to the base art). The **date caption band** across the panel
  bottom shows a calendar glyph plus the full date, on the same background as the
  day-name caption above.
- **Past days** in the current week render exactly like upcoming days.
- An empty day (no events) shows the caption band over a plain white body.

### 9.2 Day icons

Each day shows one or two art pieces for the most interesting thing happening that
day. The strip's images are **its own records** (module `DayStrip`, §7.1/§9.1) —
separate from the Today/Tomorrow rows' small transparent icons, though keyed by the
same logical keys. Chores are excluded.

**Display.** One pick cover-fills the panel body. Two picks split the body corner to
corner along the bottom-left → top-right diagonal into two **right triangles** — the
first pick (kid config order) upper-left, tapering to a point at the top-right — each
holding one pick's art (centered and clipped). The diagonal seam is drawn as two thin
black **panel-border** lines with a thin white gutter between them, sitting entirely on
the lower-right triangle's side of the diagonal — so the upper triangle's art extends
to both diagonal corners, flush with the panel edge (and the caption band's edge) at
the top-right, while the lower triangle's top corner recedes down the right edge by the
seam's width. The gutter **knocks through the panel frame** just off both diagonal ends
so the two read as separate triangle panels sitting side by side, not one panel with a
divider. The seam is held clear of the day-name caption (which, with its frame, is
never modified). Kid badges
sit in the outer corners (top-left / bottom-right), inside the panel. The **today
panel is the exception**: it never splits, always collapsing to a single pick (§9.1).

**Candidacy.** An event is a candidate for a kid if it is **shared** (no kid label, so
it counts for both) or **labeled for that kid**. Events labeled for neither kid are not
candidates for the strip icons.

**Selection** — for each day, compute each kid's single most-`interesting` candidate:

- Both kids' top candidate is the **same event** → **one icon**.
- They differ → **two icons** side by side.
- **Only one kid has any candidate** that day → **one icon** (that kid's top candidate),
  labeled with that kid's initial.
- **Neither kid has a candidate** → **no icon** (the empty-day treatment).

**Labels** follow the shown event's own kid assignment (§8), independent of why its
icon was picked:

- A **shared** event's icon is always **unlabeled** — even when it appears beside a
  kid-specific icon (one single-kid event + one shared event → only the kid-specific
  icon carries an initial).
- An event assigned to a **proper subset** of the kids is labeled with those kids'
  initials (e.g. `S`).

---

## 10. Module — Today

Occupies the full left column. Calendar entries are bucketed into **Morning**, **Day**,
and **Evening** panels; empty buckets are not shown (their header is dropped and the
remaining buckets expand to reclaim the space). Each event renders as a fixed-height
cell: AI icon + kid label(s) + title, laid out **two across** per visual row.

### 10.1 Cap and bucketing (resolution order)

The available height is the column minus the fixed weather subpanel. Visual rows hold
**two events each** (§10.2), so budgets count rows: an event is free when its bucket has
a half-filled row and opens a new row otherwise. The visible-header count is circular
(which headers show depends on which events survive, which depends on the cap, which
depends on the header count), so it is resolved in a fixed order:

1. Compute the visual-row budget **assuming the worst case of all three headers
   present**: `R = floor((available_height − 3·header_height) / row_height)`.
2. Take the **longest prefix** of the day's events ranked by `interesting` that fits in
   `R` rows.
3. Bucket the survivors into Morning / Day / Evening and **drop any empty bucket's
   header**.
4. **Backfill:** if fewer than three headers ended up visible, the freed header rows are
   spare space; recompute the row capacity with the actual header count and keep taking
   further events by `interesting` that still fit, but **only if they fall into an
   already-visible bucket** (never creating a new header). This terminates and stays
   deterministic.

### 10.2 Ordering within a bucket

Selection (the cap) is by `interesting`, but **display order within a bucket is
chronological by start time** (all-day events first within Day), with ties broken by
`interesting` then title — so the cap is by interestingness while the reading order is
by time, matching Tomorrow. The ordered list lays out in **book reading order**: two
columns filled left to right, then top to bottom.

### 10.3 Weather subpanel

At the bottom of the column, staged as an **outdoor scene** the way Tomorrow's is
staged as a room (§11). The Today frame is skinned with the day's **condition
backdrop art** — a hand-made `<condition>_bg.png` per condition bucket, one per
icon (§ Weather) — scaled to the frame's full interior width and pinned to its
bottom edge, with flat sky filling the frame above it. Every backdrop shares the
same geometry, so the pieces placed on it hold for all seven.

The three weather pieces (§ Weather) read left to right in that scene:

- the **condition icon** and the **clothing kid** sit in the subpanel's flow, the
  kid overlapping the icon;
- the **temperature bar** leaves the flow and is painted onto the **wooden
  signpost** in the backdrop art, its floating "feels like" label box hanging off
  the bar's left side.

When no forecast is available the slot renders empty but keeps its footprint (the
§10.1 row budget already subtracts it), and the frame falls back to the sunny
backdrop.

### 10.4 SFX shout

An event may carry an `sfx` string (§6.4) — a comic exclamation ("Yum!", "Pow!") drawn
in the Countdown module's SFX shout type (§12) as the **bare word** — no whisker speed
lines (the Countdown corners keep theirs). Rules:

- **At most one** SFX renders in the whole panel; the other panels (Tomorrow, Chores)
  never show SFX.
- The shout extends into the **empty right-hand cell beside its event**, so only events
  in the **left column with no right-hand neighbor** qualify — i.e. the *last displayed*
  event of a bucket with an **odd** event count (§10.2 reading order). Eligibility is
  judged after the cap/backfill, on what actually renders.
- It anchors to the **end of its event's title text**: the word starts a fixed gap
  after the title's last glyph, on the row's centerline, with a small comic tilt
  pivoting on the word's left edge — so the anchoring holds whatever the title or
  sfx length.
- Among qualifying events that have `sfx` set, the winner is the **highest
  `interesting`**, ties broken **alphabetically by title** (then bucket order as a
  stable final tiebreak).

### 10.5 Speech caption

On sparse days the weather kid (§10.3) says a rotating silly line in a comic speech
bubble floating in the backdrop sky **up and right of its head**, its tail pointing
down-left at the kid.

- **Source:** a `captions` table in the shared `sqlite.db` (the joke store's shape,
  §15), managed on **`/admin/captions`**: bulk add one per line (blank and `#` lines
  skipped), per-row edit (an empty save deletes), delete, and a rotation reset
  (forgetting every date's pin and the pointer). Rotation order is insertion order
  (stable across edits).
- **Eligibility:** only layouts that leave the bubble room - **at most two visible
  buckets, each exactly one visual row** (1-2 events, §10.2 two-across). An event-less
  day qualifies. The bubble is also suppressed when the weather subpanel itself is
  unavailable (no forecast → no kid to speak).
- **Selection - a per-date pinned rotation, not §15's date modulo:** captions appear
  only on eligible days, so a date-modulo index would burn lines on the intervening
  bubble-less days. Instead the **first** caption-eligible render of a date - device
  render, `?date=` debug render (§3.5), and warm-up prerender (§3.6) alike - takes the
  caption after the most recently assigned one (the `caption_rotation` pointer,
  wrapping) and **pins it to that date** (`caption_assignments`); every later render of
  the date repeats its pin, keeping renders byte-identical (§3.4 - the store is an
  *input*, and the pin is memoized on first use exactly like a §7.1 image record).
  Rotation therefore follows **assignment order, not calendar order**: previewing
  dates out of order pins them out of list order, and a pinned date whose calendar
  later fills up simply never shows its caption - that line is skipped for good
  (accepted). Eligibility flapping is otherwise harmless: a date first rendered busy
  pins nothing and takes whatever is next when it later renders eligible. A pin past
  the end of a since-shrunken list reads modulo the current length; an emptied list
  shows nothing.
- **Rendering:** the bubble body matches the bucket sub-panels (4px black border, 2px
  radius, white fill) and shrink-wraps its text (1-3 wrapped lines of the panel's
  handwriting type); the tail is an open-topped SVG triangle whose white fill erases
  the border seam so the outline runs unbroken, like the temperature bar's label box
  (§ Weather).

---

## 11. Module — Tomorrow

Top of the right column. Same event rows and kid labels as Today, with these
differences:

- The "TOMORROW!" label sits **inside the panel** like Today's, tinted with the day
  strip's colour for the shown day.
- **No** morning/day/evening buckets: a single **header-less** white sub-panel with
  the Today buckets' border treatment and width holds up to **four** events, laid
  out **two across** over two visual rows in book reading order — **chronological**
  by start time, ties broken by `interesting` then title. Selection past the cap is
  by the same row-budget logic (§4.1: the least-interesting events are dropped
  silently). An event-less day shows no sub-panel at all.

Tomorrow's **weather subpanel** is staged as a **room scene** right of the events
panel: a hand-made room art layer (`room.png`) spans the panel, and the frame's
background behind it is the sky - Today's sky blue on fair days (sunny / partly
cloudy), overcast gray otherwise and when the forecast is unavailable - visible
through the room's transparent window panes. The three weather pieces are placed
in the scene, each independently pinned: the **condition icon** hangs behind the
room art so it shows through a window; the **clothing kid** stands in the room, in
front of everything; and the **temperature bar** (with its temp label) is printed
onto the room's picture frame, perspective-transformed to match the art's linear
perspective.

---

## Module — Weather (shared by Today and Tomorrow)

### Data

From the **Google Maps Platform Weather API** daily forecast. Configuration supplies a
Google Maps API key and a latitude/longitude. We request **imperial units** (the API
defaults to Celsius). For each date we read the **daytime** forecast:
`weatherCondition.type`, `precipitation.probability` (percent and RAIN/SNOW type),
`thunderstormProbability`, cloud cover, and the day's **"feels like" high**
(`feelsLikeMaxTemperature`, falling back to the plain `maxTemperature` when
unusable). Google's own condition icons are ignored.

### Condition icon (Today and Tomorrow)

A single shared set of **seven** hand-made icons: **sunny, partly cloudy, cloudy, light
rain, rain, thunder, snow**. Each bucket also has a matching hand-made **backdrop**
(`<condition>_bg`), the scene Today's subpanel is staged in (§10.3); Tomorrow uses
the icon alone, in its room (§11). Google's long condition enum is mapped onto these seven,
leaning on `thunderstormProbability` and the precip RAIN/SNOW type for the
rain/thunder/snow buckets and on cloud cover for sunny/partly/cloudy. The exact
enum→bucket table is TBD (§20).

### Clothing kid (Today and Tomorrow)

A figure of the relevant kid showing what to wear. **Four outfits** per kid — hot,
normal, cold, rain — i.e. **eight** hand-made figures. Selection for the day:

- daytime **PoP ≥ 25%** → **rain gear** (overrides temperature);
- otherwise by the day's "feels like" high: **< 60°F** cold, **60–72°F** normal,
  **> 72°F** hot.

**Flip-flop:** which kid is featured in Today vs. Tomorrow alternates each day,
deterministically from the date seed.

### Temperature bar (Today and Tomorrow — identical UI)

A vertical SVG bar of five flat-color bands with a solid black arrow pointing to the day's
**"feels like" high** — the bar answers "how will it feel outside?", so wind chill
and humidity count. Its thin outline matches the panel borders. The bands are:

| Band | Feels-like high (°F) | Color |
|---|---|---|
| Coldest | ≤ 50 | blue |
| Cold | 51–59 | light-blue (halftone) |
| Mild | 60–67 | light yellow |
| Warm | 68–75 | light red |
| Hottest | ≥ 76 | red |

These five bands are deliberately **not** the three clothing cutoffs — two separate
systems sharing one input (the "feels like" high). Optional decorative sun/snow
endcaps may reuse the condition icons. The bar needs no hand-made images.

### Hand-made image inventory

Seven condition icons + seven condition backdrops (§10.3) + eight clothing figures =
**22 images**, plus Tomorrow's room art (`room.png`, §11). Being hand-made rather
than generated, they are **static assets served by name** (e.g. `sunny`, `kid0_rain`),
not rows in the image database (§7.6); the files live under `static/img/weather/`.
Figure names carry the kid's **config-order index** (`kid0`, `kid1`, …), never the
kid's name — the files are committed, and the kids' names must not land in the
public repo (names live only in the gitignored config, §18).
Pixel sizes are pinned at layout time (kid figures run taller than the square condition
icons). These are the only non-AI images in the system, alongside the bugbug creature
poses (§16).

**Weather admin page.** `GET /admin/weather` shows the whole inventory in one grid —
every condition icon and every configured kid's outfit figures, each at its pinned
board display size — sized to fit the 1600 × 1200 panel so the set can be pushed to
the device for an on-ink check.

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
  description and hero image; the next day it rolls to the next eligible event.
- If **no eligible event** is ever found (error/misconfig), the panel renders as a
  **blank card** so its footprint is preserved.

### Layout

A freshly generated **hero image** (§7) — its own cache key, hero size, more-detailed
prompt; never the small calendar icon upscaled — is the panel's **full-bleed background
layer**, cover-fitted to the panel. The text rides on top of it in white **caption
boxes** (the Today/Tomorrow inner-box border treatment) so it stays readable over the
art: the event description (the event **title**) in a box at the top, "N sleeps to go!"
in a box at the bottom, with the hero showing through the slack between them. On a
hero-image miss the image is omitted and description + sleeps remain.

### Escalating intensity

The panel gets louder as the event nears, driven by the sleeps value (deterministic),
changing **intensity, not footprint**:

- **Configurable tiers** keyed to sleeps, defaulting to: *calm* when far off, *excited*
  once the event is a few sleeps out, *hype* at 1 sleep, and the *peak* "It's today!"
  state.
- Each tier ramps the visual treatment — burst size, motion-line density, star count —
  within the fixed panel size.
- Copy escalates too: plain **"N sleeps to go!"** for most of the run, an emphatic
  **"Just 1 more sleep!!"** at 1, **"It's today!"** at 0.

---

## 13. Module — Dinner

An **unlabeled** panel — no "DINNER" title; the placemat and the food read as the
label. Shows an AI image of the meal on a hand-made placemat background, and the
menu name(s).

- **Source:** the Anylist meal-plan ICS (§6.1). Anylist is used only for dinner, so the
  meal-plan entries on the target date **are** the dinner — no meal-slot filtering. A
  main plus any side are **joined into one name and one combined image**.
- **Image:** keyed by the dish name (reused across days), hero-sized, a transparent PNG
  generated like any other (§7.2); omitted on a generation miss (leaving the name).
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
- **Two orderings**, capped by the same row-budget geometry, with **no**
  morning/day/evening buckets:
  - *selection* ranks by `interesting` descending, then kid (assigned-kid indices in
    config order), then title — so a cap keeps the most interesting chores;
  - *display* orders by kid, then `interesting` descending, then title — so each kid's
    chores group together in the rendered list.
- **Layout:** one or two chores stack as a single list; three or four render as a 2x2
  grid (two columns), the cap. A blank ruled line separates the first and second row
  of chores.
- Recurrence works for free via the shared parser (e.g. a daily "make bed").
- Non-applicable fields (`countdown_eligible`, `time_of_day`) are ignored.
- **Background:** a sheet of practice writing paper - thin light blue ruled lines the
  chore text sits on (the top of the panel is a lineless header band), a thicker light
  red margin line on the left, and two gray hole punches in the left margin. Titles
  wrap to at most two lines (one per ruling).
- **Label:** a hand-drawn "CHORES" title (a static image, black marker on white),
  centered in the header band right of the margin line.
- **Empty state:** for now centered "no chores today" text over the blank sheet
  (a placeholder; a richer empty card is planned).

---

## 15. Module — Joke / riddle

One joke or riddle per day, rendered as a wholly AI-generated comic panel (the text is
drawn inside the image).

- **Source:** a `jokes` table in the shared `sqlite.db` (§18), one row per joke, managed
  on **`/admin/jokes`**: bulk add one per line (blank and `#` lines skipped), per-row
  edit, and delete. N is the row count. Order is **insertion order** (the row id), which
  an edit preserves — so editing a joke never shuffles the rotation.
- **Selection:** index = (target date − `joke_start_date`, §18) in whole days, **modulo
  N**, so the list loops. The debug date arg selects a different joke. An empty table
  shows a friendly placeholder.
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

- an **anchor** (a panel corner, a border seam, the wider gutter between the weekday
  and weekend blocks of the day strip, a speech-bubble tail, …),
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

| Key | Type / default | Purpose |
|---|---|---|
| `timezone` | str, `US/Pacific` | Display timezone; drives date resolution (§3.4). Validated against the zoneinfo database at load. |
| `latitude`, `longitude` | float, downtown SF | Weather location (§ Weather). Defaults let a fresh checkout render plausible weather. |
| `google_maps_api_key` | `SecretStr`, **required** | Weather API auth (§ Weather). Rides in the request query string, so the URL is a secret too. |
| `family_calendar_ics_url` | `SecretStr`, **required** | Google Calendar private ICS (events + chores, §6.1). |
| `anylist_mealplan_ics_url` | `SecretStr`, **required** | Anylist meal-plan ICS (dinner, §6.1, §13). |
| `openai_api_key` | `SecretStr`, **required** | AI image generation (§7.2). |
| `app_storage_path` | Path, `.storage` | Root for all app-managed storage, created/written as needed: `sqlite.db` (image metadata §7.1, plus the joke §15, caption §10.5, and meal-override §13 tables), `gen_images/` (rendered `<id>.png` files, §7.6), and `prompt_images/` (prompt-attachment images, including each module's default style examples, §7.1). A relative path resolves against `server/`. |
| `module_model_tiers` | dict[str, str], empty | Per-module image-model overrides, e.g. `{"Calendar": "gpt-image-2"}`; modules absent from the map use the default model. |
| `kids` | list of `{name, label}`, empty | The children shown on the board (§8), **in display order** — the order fixes each kid's badge color and label position, and indexes their clothing figures (`kid0`, `kid1`, … — § Weather). `label` is the initials; events' `kids` values (§6.4) match either field case-insensitively. |
| `joke_start_date` | date, `2026-01-01` | Base date for the daily joke index (§15). The jokes themselves live in the DB, not in config. |
| `device_wifi_ssid` | `SecretStr`, empty | Wi-Fi network the Inkplate joins ([firmware.md](firmware.md) §9). Required by the deploy CLI, not by the server. |
| `device_wifi_password` | `SecretStr`, empty | Wi-Fi passphrase. Never logged, not even its length. |
| `device_server_base_url` | str, empty | Origin the device fetches from, e.g. `192.168.1.20:5051`. Required by the deploy CLI: it must be a LAN address the board can reach, so there is no sane default. A bare `host:port` gains `http://`; `https://` is rejected (no TLS stack on the board). |
| `device_fetch_path` | str, `/display` | Path (and any query) the device requests, so the firmware never hard-codes the endpoint's spelling. |
| `device_wake_cron` | str, `0 5-21/2 * * *` | Device wake schedule, 5-field crontab, evaluated on-device in local wall-clock time. Validated at load. |
| `device_wifi_timeout_seconds` | int, `60` | Wi-Fi association deadline; on expiry the device sleeps without painting. |
| `device_http_timeout_seconds` | int, `300` | Whole-fetch deadline for `/display`. Generous because a cold image cache makes `/render` generate images inline (§3.6). |
| `device_fallback_sleep_seconds` | int, `900` | Sleep length when the schedule is uncomputable — first boot with the network down, so no `Date` header has ever set the RTC. |
| `device_repaint_on_button` | bool, `true` | A WAKE press omits `If-None-Match`, so it always repaints instead of getting a silent 304. |
| `device_posix_tz` | str, empty | Override for the device's POSIX `TZ` string; empty derives it from `timezone`. |

The `device_*` keys are consumed only by `uv run python -m app.firmware`, which
bakes them into the gitignored `arduino/kidink/config.h`. They all have defaults
so a checkout that never flashes a board still starts.

**Not yet defined.** Features still unbuilt will add their own keys when they land:
the warm-up prerenders' crontab schedule (§3.6 — which can reuse
`app.firmware.cron`), the countdown escalation cutoffs (§12, currently
hardcoded), and the weekday/weekend theming backdrops (§17).

**Location.** The model lives in `server/app/config.py`. Actual values are read from
`server/config.toml` (gitignored) and/or `KIDINK_`-prefixed environment variables
(env wins over the file).
`server/config.example.toml` is the committed template documenting the shape.

### 18.1 Deployment (Raspberry Pi)

The server deploys on the Raspberry Pi 5 (Raspbian, arm64; §2). Bring-up steps:

1. Install **uv**, clone the repo, and run `uv sync` from `server/` (uv provisions
   the pinned Python).
2. One-time browser install: `uv run playwright install --with-deps chromium` -
   Playwright ships an arm64 Chromium build, and `--with-deps` pulls its required
   system libraries via apt.
3. Install the board's fonts system-wide so headless Chromium can use them - in
   particular the locally customized **NorB Pen** (a stock copy reintroduces the
   day-strip kerning defects) and the other comic display fonts the CSS names.
4. Create `server/config.toml` from `config.example.toml` with the real secrets
   (§18; never committed).
5. Run the server with a **threaded** WSGI server (§3.2) reachable from the LAN,
   e.g. the dev server with `--host 0.0.0.0` (its threaded default satisfies
   `/display`'s loopback re-entry); the ESP32 and Chromium both talk to this one
   process.
6. Install the warm-up prerender cron (§3.6) once its config key lands (still TBD).

---

## 19. Deferred / v2

- **ESP32 firmware — built**, see [firmware.md](firmware.md). It wakes on the RTC
  alarm or the WAKE button (which share GPIO18), joins Wi-Fi, does the §3.2
  conditional GET, blits the packed buffer, and deep sleeps. It does **not**
  retry: any failure leaves the last image up (the panel is bistable) and waits
  for the next scheduled wake. Remaining v2 idea: `memcpy` straight into the
  library's framebuffer, which would need a second, panel-native wire format and
  is not worth it against a ~19-second refresh (firmware.md §7).
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
  Spectra ink colors (the vibrance-boost factor will want retuning with it, §5.4).
*(Resolved: the **firmware stack** is the Inkplate Arduino library, 11.1.2 —
[firmware.md](firmware.md). The wire format is `/display`'s packed 4bpp buffer
(§3.2), which the push demo ([eink-demo.md](eink-demo.md)) verified end-to-end on
the device.)*
