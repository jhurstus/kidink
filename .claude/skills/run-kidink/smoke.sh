#!/usr/bin/env bash
# Smoke-drive the kidink server: launch it (or reuse a running one), probe the
# routes, verify the byte-determinism invariant, and screenshot the board.
#
# Usage (from repo root or anywhere):
#   .claude/skills/run-kidink/smoke.sh                 # reuse a server already running from this
#                                                      # checkout, else start one on a random port
#   .claude/skills/run-kidink/smoke.sh --url http://localhost:5051
#                                                      # reuse a specific server
#   .claude/skills/run-kidink/smoke.sh --date 2026-07-09 --out-dir /tmp/somewhere
#   .claude/skills/run-kidink/smoke.sh --no-screenshot # HTTP checks only (fast)
#   .claude/skills/run-kidink/smoke.sh --flash         # ALSO compile + upload to the Inkplate over
#                                                      # USB. Only when the user explicitly asks.
#
# Artifacts land in --out-dir (default server/.eink-out/smoke/, gitignored):
#   render.html      the board HTML as served
#   screenshot.png   1600x1200 Chromium capture of /render
#   preview.png      the same frame dithered to the six e-ink inks
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SKILL_DIR/../../.." && pwd)"
SERVER_DIR="$REPO_ROOT/server"

URL=""
PORT=""
DATE=""
OUT_DIR="$SERVER_DIR/.eink-out/smoke"
SCREENSHOT=1
FLASH=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url) URL="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --date) DATE="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --no-screenshot) SCREENSHOT=0; shift ;;
    --flash) FLASH=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# Port of a `flask --app app run` process whose cwd is THIS checkout's server/
# dir (the hot-reload child holds the socket; its parent has no LISTEN row -
# the loop skips it). A server from another checkout never matches.
find_running_server_port() {
  local pid cwd port
  for pid in $(pgrep -f "flask --app app run" 2>/dev/null); do
    cwd=$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')
    [[ "$cwd" == "$SERVER_DIR" ]] || continue
    port=$(lsof -a -p "$pid" -iTCP -sTCP:LISTEN -Fn 2>/dev/null \
      | sed -n 's/^n.*:\([0-9][0-9]*\)$/\1/p' | head -1)
    if [[ -n "$port" ]]; then
      echo "$port"
      return 0
    fi
  done
  return 1
}

# Pseudorandom free port well away from 5000 (AirPlay) and 5051 (dev server).
pick_free_port() {
  local p
  for _ in $(seq 1 20); do
    p=$((RANDOM % 1000 + 5200))
    if ! nc -z localhost "$p" >/dev/null 2>&1; then
      echo "$p"
      return 0
    fi
  done
  echo "FAIL: no free port found in 5200-6199 after 20 tries" >&2
  return 1
}

mkdir -p "$OUT_DIR"
# Canonicalize: a relative --out-dir would otherwise silently re-resolve
# against server/ after the cd below (flask.log lands in the wrong place).
OUT_DIR="$(cd "$OUT_DIR" && pwd)"
cd "$SERVER_DIR"

FLASK_PID=""
cleanup() {
  if [[ -n "$FLASK_PID" ]]; then
    kill "$FLASK_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

if [[ -n "$URL" ]]; then
  echo "Reusing server at $URL"
elif RUNNING_PORT=$(find_running_server_port); then
  URL="http://localhost:$RUNNING_PORT"
  echo "Reusing server already running from this checkout at $URL"
fi

if [[ -z "$URL" ]]; then
  if [[ -z "$PORT" ]]; then
    PORT=$(pick_free_port)
  elif nc -z localhost "$PORT" >/dev/null 2>&1; then
    echo "FAIL: something is already listening on port $PORT." >&2
    echo "Reuse it with --url http://localhost:$PORT, or pick another --port." >&2
    exit 1
  fi
  URL="http://localhost:$PORT"
  echo "Starting kidink server on $URL ..."
  uv run flask --app app run --port "$PORT" >"$OUT_DIR/flask.log" 2>&1 &
  FLASK_PID=$!
  for _ in $(seq 1 30); do
    curl -sf -o /dev/null --max-time 2 "$URL/admin/images" && break
    if ! kill -0 "$FLASK_PID" 2>/dev/null; then
      echo "FAIL: server exited during startup; log follows:" >&2
      cat "$OUT_DIR/flask.log" >&2
      exit 1
    fi
    sleep 1
  done
fi

RENDER_URL="$URL/render"
[[ -n "$DATE" ]] && RENDER_URL="$URL/render?date=$DATE"

check() { # check <name> <url> [max-time]
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time "${3:-30}" "$2")
  if [[ "$code" != "200" ]]; then
    echo "FAIL: $1 ($2) returned $code" >&2
    exit 1
  fi
  echo "ok: $1 -> 200"
}

# First render of a date may generate missing AI images inline (real OpenAI
# calls); allow it time. Subsequent renders serve from .storage.
code=$(curl -s -o "$OUT_DIR/render.html" -w '%{http_code}' --max-time 300 "$RENDER_URL")
if [[ "$code" != "200" ]]; then
  echo "FAIL: render ($RENDER_URL) returned $code (bad ICS URL/secrets? see $OUT_DIR/flask.log)" >&2
  exit 1
fi
echo "ok: render -> 200 ($(wc -c <"$OUT_DIR/render.html" | tr -d ' ') bytes)"

# Determinism invariant (spec §3.4): same inputs -> byte-identical HTML.
curl -s -o "$OUT_DIR/render2.html" --max-time 60 "$RENDER_URL"
if ! cmp -s "$OUT_DIR/render.html" "$OUT_DIR/render2.html"; then
  echo "FAIL: /render is not byte-deterministic; diff $OUT_DIR/render.html $OUT_DIR/render2.html" >&2
  exit 1
fi
rm -f "$OUT_DIR/render2.html"
echo "ok: render is byte-deterministic"

check "admin" "$URL/admin/images"

if [[ "$FLASH" == "1" ]]; then
  # Full deploy: screenshot, dither, write arduino/mockup/mockup.h, compile,
  # and upload to the Inkplate over USB. This repaints the physical panel -
  # run it only when the user explicitly asked to deploy to the device.
  echo "Deploying $RENDER_URL to the Inkplate over USB ..."
  uv run python -m app.eink --url "$RENDER_URL" --out-dir "$OUT_DIR"
elif [[ "$SCREENSHOT" == "1" ]]; then
  # --sketch-dir keeps the CLI from overwriting arduino/mockup/mockup.h (the
  # header that was last flashed to the device); --no-compile skips arduino-cli.
  echo "Screenshotting $RENDER_URL ..."
  mkdir -p "$OUT_DIR/sketch" # the CLI errors if --sketch-dir doesn't exist
  uv run python -m app.eink --url "$RENDER_URL" --no-compile \
    --out-dir "$OUT_DIR" --sketch-dir "$OUT_DIR/sketch"
fi

echo
echo "SMOKE PASSED. Artifacts in $OUT_DIR:"
ls "$OUT_DIR"
