# kidink

Smart display for kids, targeted for rendering on an Inkplate 13 SPECTRA color e-ink screen.

## Layout

- `server/` — Python Flask HTTP server; pages are rendered with Jinja2 templates in `server/app/templates/`
- `specs/` — project specs

## Conventions & invariants

See `specs/main.md` for the full design. A few rules that aren't obvious from the
code and must hold across changes:

- **Determinism.** A render is byte-reproducible for a given set of inputs (spec §3.4). Anything otherwise random — the bugbug's spot, the kid flip-flop, the joke offset — must be seeded off the **target date**, never off wall-clock time or unseeded randomness. Inject the date/clock into render code rather than calling `datetime.now()`/`random` inside it. The `/display` `ETag` (and the device's battery life) depends on this.
- **Templates are plain HTML + CSS, no JavaScript** (spec §3.3) — the panel is not interactive.
- **Secrets never get committed or logged.** API keys and the unauthenticated ICS URLs (spec §18) live in environment / a gitignored `.env`, never in checked-in files. The pre-commit hook runs `gitleaks` over staged changes as a backstop. When logging image-generation failures and prompts (spec §7.2), redact keys and secret URLs.

## Python development (`server/`)

Run all commands from `server/`.

- **Packages:** managed with uv. Add runtime deps with `uv add <pkg>`, dev tooling with `uv add --dev <pkg>`. Never use pip directly or edit `uv.lock` by hand. Commit `uv.lock`.
- **Running code:** always go through uv so the project venv is used: `uv run pytest`, `uv run flask ...`, etc. `./run.sh` starts the dev server with hot reload.
- **Formatting/linting:** ruff. `uv run ruff format .` and `uv run ruff check --fix .`. All Python code must be ruff-formatted; lint rules are configured in `pyproject.toml`.
- **Type checking:** ty (`uv run ty check`). All new code should carry type annotations and pass cleanly.
- **Tests:** pytest. Test files live side-by-side with the code they test (e.g. `app/test_index.py` next to `app/__init__.py`), named `test_*.py`. Add or update tests alongside behavior changes.
  - **No real network.** The suite runs with `--disable-socket` (pytest-socket), so tests must never hit the network. Exercise HTTP through the Flask test client (in-process, no socket) and fake all external I/O (OpenAI, Weather, ICS feeds).
  - **Browser tests.** Tests that drive headless Chromium (Playwright, `/display`) are slow and need real sockets. Mark them `@pytest.mark.browser` *and* `@pytest.mark.enable_socket`; they are deselected by default. Run them with `uv run pytest -m browser`.
- **Python:** Project uses python version 3.14.

### Checks (no CI)

This repo has no CI — checks run locally:

- `./check.sh` (from `server/`) formats, lints, type checks, and runs tests. Run it before every commit.
- `./check.sh --ci` is check-only (fails on formatting/lint issues instead of fixing them); the pre-commit hook in `.githooks/` runs this mode.
- Enable the hook once per clone: `git config core.hooksPath .githooks`
