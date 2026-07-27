#!/usr/bin/env bash
# One-time provisioning of a deploy target. Idempotent - safe to re-run after
# changing deploy/post-receive.in, deploy/kidink.service.in, or target.env.
#
#   cp deploy/target.env.example deploy/target.env   # fill in your host
#   deploy/bootstrap.sh
#   git push pi main:deployed
#
# --force additionally overwrites the target's config.toml with this machine's.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$here/.." && pwd)"
env_file="$here/target.env"
rendered="$here/.rendered"

force=0
case "${1:-}" in
    --force) force=1 ;;
    "") ;;
    *) echo "usage: $(basename "$0") [--force]" >&2; exit 2 ;;
esac

[ -f "$env_file" ] || {
    echo "missing $env_file - copy target.env.example and fill it in" >&2
    exit 1
}
# shellcheck source=/dev/null
. "$env_file"
: "${DEPLOY_SSH:?}" "${DEPLOY_ROOT:?}" "${DEPLOY_USER:?}" "${DEPLOY_HOME:?}" \
  "${DEPLOY_PORT:?}" "${DEPLOY_UV:?}"

root="$DEPLOY_ROOT"
repo="$root/repo.git"
step() { printf '\n== %s\n' "$*"; }
rsh() { ssh "$DEPLOY_SSH" "$@"; }

# render <template> <out> - substitute the @...@ placeholders.
render() {
    sed -e "s|@ROOT@|$root|g" \
        -e "s|@UV@|$DEPLOY_UV|g" \
        -e "s|@PORT@|$DEPLOY_PORT|g" \
        -e "s|@USER@|$DEPLOY_USER|g" \
        -e "s|@HOME@|$DEPLOY_HOME|g" \
        "$1" >"$2"
}

step "checking the target"
rsh true
for tool in git rsync curl flock fc-cache fc-match tar sudo; do
    rsh "command -v $tool >/dev/null" ||
        { echo "$tool is missing on the target" >&2; exit 1; }
done
# uv needs an absolute path everywhere: neither `ssh host '<cmd>'` nor systemd
# sources a shell, so ~/.local/bin is never on PATH.
rsh "test -x '$DEPLOY_UV'" || { echo "no uv at $DEPLOY_UV" >&2; exit 1; }
rsh "sudo -n true" || { echo "passwordless sudo is required on the target" >&2; exit 1; }
echo "ok: $(rsh "'$DEPLOY_UV' --version"), $(rsh 'git --version')"

step "creating $root"
rsh "mkdir -p '$root/src/server' '$root/storage'"

step "creating the bare repo and hook"
# HEAD tracks the deploy branch so the bare repo reads sensibly by hand. Set only
# at creation: the hook's `checkout -f` detaches HEAD, and re-pointing it later
# would silently rewrite what the repo says is checked out.
rsh "test -d '$repo' || {
        git init --quiet --bare '$repo' &&
        git --git-dir='$repo' symbolic-ref HEAD refs/heads/deployed
     }"
mkdir -p "$rendered"
render "$here/post-receive.in" "$rendered/post-receive"
bash -n "$rendered/post-receive"
scp -q "$rendered/post-receive" "$DEPLOY_SSH:$repo/hooks/post-receive"
rsh "chmod 0755 '$repo/hooks/post-receive'"

step "installing config.toml"
if [ "$force" -eq 0 ] && rsh "test -f '$root/config.toml'"; then
    echo "keeping the existing $root/config.toml (--force to replace it)"
else
    [ -f "$repo_root/server/config.toml" ] ||
        { echo "no server/config.toml on this machine to copy" >&2; exit 1; }
    # app_storage_path goes at the top: TOML folds a bare key written after a
    # table header into that table, and config.toml ends with [[kids]].
    tmp="$(mktemp)"
    {
        printf '# Set by deploy/bootstrap.sh: storage lives outside the checkout so\n'
        printf '# deploys never touch it, and it backs up as one directory.\n'
        printf 'app_storage_path = "%s/storage"\n\n' "$root"
        grep -v '^[[:space:]]*app_storage_path[[:space:]]*=' \
            "$repo_root/server/config.toml"
    } >"$tmp"
    scp -q "$tmp" "$DEPLOY_SSH:$root/config.toml"
    rm -f "$tmp"
    rsh "chmod 0600 '$root/config.toml'"
    echo "copied this machine's config.toml with app_storage_path=$root/storage"
fi
# The symlink is where app/config.py looks; it is gitignored, so the deploy
# checkout never disturbs it.
rsh "ln -sfn '$root/config.toml' '$root/src/server/config.toml'"

step "installing the systemd unit"
render "$here/kidink.service.in" "$rendered/kidink.service"
scp -q "$rendered/kidink.service" "$DEPLOY_SSH:/tmp/kidink.service"
rsh "sudo install -m 0644 /tmp/kidink.service /etc/systemd/system/kidink.service &&
     rm -f /tmp/kidink.service &&
     sudo systemctl daemon-reload &&
     sudo systemctl enable kidink >/dev/null"
echo "enabled kidink.service (not started - nothing is checked out yet)"

step "syncing fonts"
"$here/sync-fonts.sh"

step "adding the git remote"
if git -C "$repo_root" remote get-url pi >/dev/null 2>&1; then
    echo "remote 'pi' already set to $(git -C "$repo_root" remote get-url pi)"
else
    git -C "$repo_root" remote add pi "$DEPLOY_SSH:$repo"
    echo "added remote 'pi' -> $DEPLOY_SSH:$repo"
fi

cat <<EOF

Bootstrap complete. Deploy with:

    git push pi main:deployed

The first push downloads Chromium (~150 MB) and will take a few minutes.
EOF
