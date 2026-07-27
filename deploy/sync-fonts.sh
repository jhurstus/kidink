#!/usr/bin/env bash
# Ship the board's fonts to the deploy target.
#
# The board CSS names exactly two families - "NorB Pen" (app/static/css/board.css)
# and "Comic Sans MS" (app/static/css/today.css) - with no @font-face rules and no
# font files in the repo, so headless Chromium on the target needs them installed
# at the OS level or the whole board silently renders in DejaVu.
#
# Fonts are shipped from this machine rather than committed: they are licensed, and
# the NorB Pen copies here are locally customized (italic T-kern stripped, Y left
# bearing tightened) - a stock install reintroduces the day-strip kerning defects.
#
# Idempotent; re-run whenever the fonts change.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
env_file="$here/target.env"

[ -f "$env_file" ] || {
    echo "missing $env_file - copy target.env.example and fill it in" >&2
    exit 1
}
# shellcheck source=/dev/null
. "$env_file"
: "${DEPLOY_SSH:?}" "${DEPLOY_HOME:?}"

dest="$DEPLOY_HOME/.local/share/fonts/kidink"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT

# stage_family <family name> <file>...  - fails loudly on an empty match, so a
# renamed or uninstalled font is caught here rather than on the rendered board.
stage_family() {
    local family="$1" n=0 f
    shift
    for f in "$@"; do
        [ -e "$f" ] || continue
        cp "$f" "$stage/"
        n=$((n + 1))
    done
    [ "$n" -gt 0 ] || {
        echo "no files found for font family '$family' - is it still installed?" >&2
        exit 1
    }
    printf '  %-16s %d file(s)\n' "$family" "$n"
}

echo "staging fonts:"
stage_family "NorB Pen" "$HOME"/Library/Fonts/NorBPen-*.otf
stage_family "Comic Sans MS" "/System/Library/Fonts/Supplemental/Comic Sans MS"*.ttf

echo "syncing to $DEPLOY_SSH:$dest"
ssh "$DEPLOY_SSH" "mkdir -p '$dest'"
rsync -a --delete "$stage/" "$DEPLOY_SSH:$dest/"
ssh "$DEPLOY_SSH" "fc-cache -f '$dest' >/dev/null"

echo "verifying on the target:"
ssh "$DEPLOY_SSH" '
    for family in "NorB Pen" "Comic Sans MS"; do
        printf "  %-16s -> %s\n" "$family" "$(fc-match -f "%{file}" "$family")"
    done'
