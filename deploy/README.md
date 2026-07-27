# Deploying the kidink server

Push-to-deploy onto a Linux box.  See `specs/main.md` §18.1 for the design and
why it is shaped this way.

## Setup (once per target)

```sh
cp deploy/target.env.example deploy/target.env   # fill in host, paths, port, uv
deploy/bootstrap.sh
```

`target.env` is gitignored - it is the only place the target's hostname, user, and
paths appear. Nothing target-specific belongs in any other file here.

`bootstrap.sh` is idempotent: it creates `<root>/{src,storage}`, initializes the bare
repo and installs the hook, copies this machine's `server/config.toml` to
`<root>/config.toml` (with `app_storage_path` pointed at `<root>/storage`) and
symlinks it into the checkout, installs and enables the systemd unit, ships the
fonts, and adds the `pi` git remote. Re-run it after editing `post-receive.in`,
`kidink.service.in`, or `target.env`. `--force` also replaces the target's
`config.toml`; without it, hand-edits made on the target survive.

The target needs `git`, `rsync`, `curl`, `flock`, `fontconfig`, passwordless `sudo`,
and `uv` at the absolute path in `DEPLOY_UV`.

## Deploying

```sh
git push pi main:deployed                 # a branch
git push pi <sha>:deployed                # a commit
git push --force pi <old-sha>:deployed    # roll back
```

Only `refs/heads/deployed` deploys. The hook's whole log streams back to your
terminal as `remote: ...` and is appended to `<root>/deploy.log`.

Each deploy checks the commit out over the existing tree, runs `uv sync`, ensures
Chromium, verifies the board fonts resolve, imports the app (catching a bad commit
while the old process is still serving), restarts the service, and waits for a
health check.

If anything fails, the last commit that deployed successfully is checked back out and
restarted, and `refs/heads/deployed` is rolled back to match - so the ref, the tree,
and `<root>/deployed.sha` always agree on what is running.

**`git push` still exits 0 on a failed deploy.** Git ignores a post-receive hook's
exit status, because by the time it runs the refs are already updated - no hook can
undo that. Read the streamed log: a failure ends with a `*** DEPLOY FAILED ***`
banner and `*** the push above did NOT deploy ***`. To gate a script on it:

```sh
git push pi main:deployed 2>&1 | tee /dev/stderr | grep -q "DEPLOY FAILED" && exit 1
```

## On the target

```sh
systemctl status kidink
journalctl -u kidink -f
```

```
<root>/
├── repo.git/     bare repo + post-receive hook
├── src/          the checkout (deploys overwrite this)
├── config.toml   persistent, 0600 - deploys never touch it
├── storage/      sqlite.db, gen_images/, prompt_images/ - deploys never touch it
└── deploy.log
```

Config and storage live outside `src/` on purpose: a deploy replaces the checkout
wholesale, and storage is meant to be backed up as a single directory.

## Fonts

`sync-fonts.sh` (run by bootstrap, re-runnable on its own) ships the two families
the board CSS names - NorB Pen and Comic Sans MS - from this machine's font
directories (the globs are macOS paths). They are not in the repo: they are
licensed, and the NorB Pen copies here
are locally customized, so a stock install visibly regresses the day strip. Every
deploy re-checks with `fc-match` and refuses to continue if a family stops
resolving.
