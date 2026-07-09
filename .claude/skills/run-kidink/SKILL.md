---
name: run-kidink
description: Run, smoke-test, and screenshot the kidink server (the kids' e-ink board), or deploy/flash the board to the Inkplate over USB. Use when asked to start the server, run or verify the app, screenshot /render, check a board change visually, preview the e-ink dither, push to the device, or run the tests.
---

kidink is a Flask server that renders a kids' smart-display board as plain
HTML/CSS (no JS) for a 1600x1200 Inkplate e-ink panel. The handle for driving
it is `.claude/skills/run-kidink/smoke.sh` - it launches the server (or reuses
a running one), probes every route, verifies the byte-determinism invariant,
and screenshots the board via the repo's own Playwright + dither pipeline.

All commands below run from the repo root unless noted; the server lives in
`server/` and all `uv` commands must run from there.

## Prerequisites

- `uv` (manages Python 3.14 + all deps; `uv run` auto-creates `.venv` and
  installs everything on first use - takes seconds, nothing else to install)
- Playwright's Chromium for screenshots and browser tests:

```bash
cd server && uv run playwright install chromium
```

## Setup

The server fails fast at startup without two secrets (see `app/config.py`):
a family-calendar ICS URL and an OpenAI API key. Copy
`server/config.example.toml` to `server/config.toml` (gitignored) and fill
them in, or set `KIDINK_FAMILY_CALENDAR_ICS_URL` / `KIDINK_OPENAI_API_KEY`.
On the primary dev machine `server/config.toml` already exists - touch
nothing.

## Run (agent path)

```bash
.claude/skills/run-kidink/smoke.sh --date 2026-07-09
```

Server selection: if a `flask --app app run` process is already running with
this checkout's `server/` as its cwd, it is reused; otherwise a fresh
instance starts on a pseudorandom free port (5200-6199) and is stopped when
the script exits. A dev server started from a *different* checkout is never
matched - point at it explicitly with `--url` if that's what you want.

The script checks `/render` and `/admin/images` return 200, fetches `/render`
twice and fails unless the two responses are byte-identical (spec §3.4
determinism), then screenshots the board and dithers it to the six e-ink
inks. Prints `SMOKE PASSED` on success.

Artifacts land in `server/.eink-out/smoke/` (gitignored):

| file | what it is |
|---|---|
| `render.html` | the board HTML as served |
| `screenshot.png` | 1600x1200 Chromium capture of `/render` - look at this to verify a change |
| `preview.png` | the same frame dithered to the six inks - exactly what the panel will show |
| `flask.log` | server log (only in self-launch mode) |

Flags: `--url http://localhost:5051` reuse a specific running server (e.g.
one from another checkout); `--date YYYY-MM-DD` render a specific day (omit
for today); `--no-screenshot` HTTP checks only (fast); `--port N` pin the
self-launch port instead of the random pick; `--out-dir DIR` artifact
location; `--flash` deploy to the Inkplate (see below - explicit user
request only).

Useful routes when poking manually with curl: `/render?date=YYYY-MM-DD` (the
board), `/admin/images` (generated-image admin), `/images/generated/<id>`.

## Run (human path)

```bash
cd server && ./run.sh   # hot-reload dev server on http://localhost:5051; Ctrl-C to stop
```

Same as `uv run flask --app app run --debug --port 5051` (the smoke driver
launches this exact command, minus `--debug`, on its own port).

## Deploy to the Inkplate over USB

**Only on an explicit user request.** Flashing repaints the physical panel on
the family's wall and overwrites `arduino/mockup/mockup.h` (the record of
what the device shows). Never run this as a side effect of verifying a
change; the screenshot + `preview.png` from the smoke run show exactly what
the panel would display.

```bash
.claude/skills/run-kidink/smoke.sh --date 2026-07-09 --flash
```

This runs the full smoke checks, then `uv run python -m app.eink` end to end:
screenshot, dither, write `arduino/mockup/mockup.h`, `arduino-cli compile`
(FQBN `soldered-inkplate-boards:esp32:Inkplate13SPECTRA`), and upload to the
auto-detected `/dev/cu.wchusbserial*` port. Needs the Inkplate on USB and
`arduino-cli` with the Soldered board core (both present on the primary dev
machine; see `specs/eink-demo.md` and `app/eink/arduino.py`). Verified here
through the compile step (~59% flash usage); the upload leg is exercised only
on a real user-requested deploy.

To stop at compile without touching the device, run the underlying CLI with
`--no-upload`; without hardware at all, stop at the artifacts (what the smoke
driver's screenshot mode does):

```bash
cd server && mkdir -p .eink-out/smoke/sketch && uv run python -m app.eink \
  --url "http://localhost:5051/render?date=2026-07-09" --no-compile \
  --out-dir .eink-out/smoke --sketch-dir .eink-out/smoke/sketch
```

## Test

```bash
cd server && uv run pytest              # 179 passed, 2 deselected, ~6s
cd server && uv run pytest -m browser   # the 2 Playwright tests, needs chromium
cd server && ./check.sh                 # format + lint + type check + tests; run before commits
```

## Gotchas

- **Never use port 5000** - macOS AirPlay Receiver squats on it and answers
  200 with the wrong content-type to anything, so health checks "pass"
  against the wrong process. The screenshot module detects this and bails.
- **Port 5051 belongs to the developer's hot-reload server.** If something
  answers there, reuse it via `--url`; don't kill it or bind over it. The
  driver's auto-reuse only matches servers whose cwd is *this* checkout's
  `server/`, so a 5051 server from another checkout is deliberately ignored.
- **Flashing the device is explicit-request-only.** `--flash` (or bare
  `uv run python -m app.eink`) repaints the physical panel. Verifying a
  change never requires it - use `preview.png` instead.
- **arduino-cli requires the sketch folder name to match the `.ino`** - it
  compiles `<dir>/<dir-basename>.ino`, so a copied/renamed sketch dir fails
  with "main file missing". Real deploys must build `arduino/mockup/`
  itself; if you need a no-side-effect compile, copy it to a dir *named
  `mockup`* first.
- **Bare `uv run python -m app.eink` mutates the repo**: its default
  `--sketch-dir` overwrites `arduino/mockup/mockup.h` (the header last
  flashed to the real device, gitignored but still the device's state) and
  without `--no-compile` it tries to flash hardware. Always pass
  `--sketch-dir` + `--no-compile` unless you mean to push to the panel.
- **First render of a new date is slow and costs money**: `/render` fetches
  the real ICS feed and generates any missing AI images inline via OpenAI,
  caching them in `server/.storage/`. Re-renders of the same date are
  instant (<0.5s). The smoke driver allows 300s for this first fetch.
- **Byte-determinism is an invariant, not a nicety** - anything
  date-seeded (bugbug spot, joke offset) must not drift between renders of
  the same date. The smoke driver double-fetches and `cmp`s to enforce it;
  if it fails, your change broke spec §3.4.

## Troubleshooting

- **`FileNotFoundError: ... sketch/mockup.h`** from `python -m app.eink`:
  the `--sketch-dir` directory must already exist; the CLI writes into it
  but doesn't create it. `mkdir -p` it first (the smoke driver does).
- **`Can't open sketch: main file missing from sketch: .../<dir>.ino`** from
  `arduino-cli compile`: the sketch directory isn't named `mockup` (see
  Gotchas above).
- **`curl` returns `000` right after launch**: Flask takes ~2s to bind.
  The smoke driver polls for up to 30s; do the same in ad-hoc scripts.
- **Server exits immediately at startup**: almost always missing/invalid
  config - pydantic fails fast on absent `family_calendar_ics_url` /
  `openai_api_key`. Check `server/.eink-out/smoke/flask.log` (never paste
  the secret values it complains about into a commit or chat).
