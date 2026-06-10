#!/usr/bin/env bash
# Run all server code checks: format, lint, type check, test.
#
# Usage:
#   ./check.sh        format and auto-fix in place, then type check and test
#   ./check.sh --ci   check only; fail on formatting/lint issues instead of fixing
set -euo pipefail
cd "$(dirname "$0")"

if [[ "${1:-}" == "--ci" ]]; then
  uv run ruff format --check .
  uv run ruff check .
else
  uv run ruff format .
  uv run ruff check --fix .
fi

uv run ty check
uv run pytest
