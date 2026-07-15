"""Hermetic test configuration for the kidink server.

The suite must pass with **no ``server/config.toml`` present** and without any
real secrets. The real ``config.toml`` is gitignored and holds private ICS URLs
and API keys (spec §18), so tests can neither read it nor depend on it. Yet the
production ``Settings`` (``app/config.py``) has four *required* secret fields with
no defaults, and the global ``get_settings()`` - reached by every test that goes
through ``create_app()`` - fails to validate without them.

The autouse fixture below injects an obvious *fake* configuration for every test,
so ``get_settings()`` always validates. It is fully hermetic - identical whether
or not a developer has a local ``config.toml``:

* the four required secrets and the ``[[kids]]`` list are supplied as fake
  ``KIDINK_*`` environment values (env wins over the TOML source, per
  ``Settings.settings_customise_sources``);
* the TOML source is pointed at a nonexistent path, so a real ``config.toml`` is
  never read during tests. ``check.sh`` additionally relocates any local
  ``config.toml`` for the test run so a future test that silently starts
  depending on a present one is caught.

Production validation is untouched: the four fields stay required, so a genuinely
misconfigured deploy still fails fast. Only tests get the injected values.

Model *defaults* (``timezone`` -> ``US/Pacific``, ``latitude``/``longitude``) are
deliberately left unset here, so ``test_config.py`` can keep asserting them.
"""

import json
import os
from collections.abc import Iterator

import pytest

from app.config import Settings, get_settings

# Obvious fake values - never real secrets. Mirrors test_config.py's _settings().
_FAKE_ENV: dict[str, str] = {
    "KIDINK_FAMILY_CALENDAR_ICS_URL": "https://example.com/cal.ics",
    "KIDINK_ANYLIST_MEALPLAN_ICS_URL": "https://example.com/meals.ics",
    "KIDINK_OPENAI_API_KEY": "sk-test-not-a-real-key",
    "KIDINK_GOOGLE_MAPS_API_KEY": "maps-test-not-a-real-key",
    # Two kids (matching test_config.py's Julia/Sam) so every per-kid render -
    # the weather subpanels, the daily kid flip-flop, the outfit admin grid - has
    # markup to emit. A list of {name, label} tables is a JSON array to env.
    "KIDINK_KIDS": json.dumps(
        [{"name": "Julia", "label": "J"}, {"name": "Sam", "label": "S"}]
    ),
}

# A path that cannot exist, so TomlConfigSettingsSource reads nothing and any real
# config.toml is ignored during the test run.
_NO_TOML = "/nonexistent/kidink-tests-never-read-a-config.toml"


@pytest.fixture(autouse=True)
def _hermetic_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give every test a valid, deterministic, secret-free configuration."""
    for key in tuple(os.environ):
        if key.startswith("KIDINK_"):
            monkeypatch.delenv(key, raising=False)
    for key, value in _FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    # Ignore any real config.toml: point the TOML source at a nonexistent path so
    # env supplies the secrets and kids and everything else falls back to the
    # model defaults. Saved and restored by hand (monkeypatch.setitem does not
    # type-check against the SettingsConfigDict TypedDict).
    original_toml_file = Settings.model_config.get("toml_file")
    Settings.model_config["toml_file"] = _NO_TOML
    # get_settings() caches the first-built Settings, so rebuild it under the
    # injected config, and clear again afterward so nothing leaks between tests.
    get_settings.cache_clear()
    try:
        yield
    finally:
        Settings.model_config["toml_file"] = original_toml_file
        get_settings.cache_clear()
