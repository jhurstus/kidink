#!/usr/bin/env bash
# Run the dev server with hot reload.
set -euo pipefail
cd "$(dirname "$0")"

uv run flask --app app run --debug
