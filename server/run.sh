#!/usr/bin/env bash
# Run the dev server with hot reload.
set -euo pipefail
cd "$(dirname "$0")"

# Bind all interfaces, not Flask's 127.0.0.1 default: the Inkplate fetches
# /display over the LAN, and a loopback-only socket is unreachable from it no
# matter how the firewall is set. This does expose the unauthenticated /admin
# pages to the local network (spec §7.4) - intended for a trusted home LAN only.
# Override with KIDINK_HOST=127.0.0.1 ./run.sh to go back to loopback.
uv run flask --app app run --debug --port 5051 --host "${KIDINK_HOST:-0.0.0.0}"
