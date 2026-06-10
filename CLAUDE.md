# kidink

Smart display for kids, targeted for rendering on an Inkplate 13 SPECTRA color e-ink screen.

## Layout

- `server/` — Python Flask HTTP server; pages are rendered with Jinja2 templates in `server/app/templates/`
- `specs/` — project specs

## Python development (`server/`)

Run all commands from `server/`.

- **Packages:** managed with uv. Add runtime deps with `uv add <pkg>`, dev tooling with `uv add --dev <pkg>`. Never use pip directly or edit `uv.lock` by hand. Commit `uv.lock`.
- **Running code:** always go through uv so the project venv is used: `uv run pytest`, `uv run flask ...`, etc. `./run.sh` starts the dev server with hot reload.
- **Formatting/linting:** ruff. `uv run ruff format .` and `uv run ruff check --fix .`. All Python code must be ruff-formatted; lint rules are configured in `pyproject.toml`.
- **Type checking:** ty (`uv run ty check`). All new code should carry type annotations and pass cleanly.
- **Tests:** pytest. Test files live side-by-side with the code they test (e.g. `app/test_index.py` next to `app/__init__.py`), named `test_*.py`. Add or update tests alongside behavior changes.
- **Python:** Project uses python version 3.14.

### Checks (no CI)

This repo has no CI — checks run locally:

- `./check.sh` (from `server/`) formats, lints, type checks, and runs tests. Run it before every commit.
- `./check.sh --ci` is check-only (fails on formatting/lint issues instead of fixing them); the pre-commit hook in `.githooks/` runs this mode.
- Enable the hook once per clone: `git config core.hooksPath .githooks`
