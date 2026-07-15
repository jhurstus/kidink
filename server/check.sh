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

# The test suite must pass with **no config.toml present** - a clean checkout and
# the automated quality gate have no gitignored secret config (spec §18). The
# hermetic conftest.py injects fake settings so get_settings() validates without
# it. As a backstop that survives a regression in that conftest, physically hide
# any local config.toml for the test run: a future test that silently starts
# depending on a present config.toml then fails here instead of passing only on
# machines that happen to have one. The stash lives in server/ (same filesystem,
# gitignored) and is restored on any exit, including pytest failure.
config_toml="config.toml"
config_stash=".config.toml.checkstash"

restore_config() {
  if [[ -e "$config_stash" ]]; then
    mv -f "$config_stash" "$config_toml"
  fi
}

# Recover an orphaned stash from a previously hard-killed run before touching it.
if [[ -e "$config_stash" && ! -e "$config_toml" ]]; then
  mv "$config_stash" "$config_toml"
fi
trap restore_config EXIT
if [[ -e "$config_toml" ]]; then
  mv "$config_toml" "$config_stash"
fi

uv run pytest
