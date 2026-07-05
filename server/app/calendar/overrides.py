"""Per-event structured overrides parsed from the ICS description.

The standard event fields (start/end, title, all-day) are modeled natively in the
ICS; everything else lives in the event ``DESCRIPTION`` as TOML and is parsed here
into the :class:`EventOverrides` model.

Parsing is deliberately **lenient** so a malformed description degrades to defaults
instead of dropping the whole event (spec §6.3):

- empty / non-TOML / not a top-level mapping → all-defaults model;
- unknown keys are ignored (``extra="ignore"``);
- a single invalid field falls back to *that field's* default rather than rejecting
  the event — achieved by validating each provided key on its own and discarding the
  ones that fail.
"""

import contextlib
import tomllib
from enum import StrEnum
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    TypeAdapter,
    ValidationError,
)


class TimeOfDay(StrEnum):
    """Morning / day / evening bucket for the Today panel (spec §6.4, §10)."""

    MORNING = "morning"
    DAY = "day"
    EVENING = "evening"


class EventOverrides(BaseModel):
    """The TOML-described, non-standard event fields (spec §6.4)."""

    model_config = ConfigDict(extra="ignore")

    time_of_day: TimeOfDay | None = None
    """Explicit bucket; ``None`` means derive from start/end (spec §6.4)."""

    icon_description: str | None = None
    """Prompt text for the AI icon; ``None`` means fall back to the title (§7.1)."""

    interesting: PositiveInt = 100
    """Ranking weight, higher = more interesting (spec §6.4)."""

    labels: list[str] = Field(default_factory=list)
    """Kid initials / UI treatments (spec §8, §9.2)."""

    countdown_eligible: bool = False
    """Whether the event may appear in the Countdown module (spec §12)."""


def _field_adapter(annotation: object, metadata: list[object]) -> TypeAdapter:
    """A ``TypeAdapter`` for one model field, preserving its constraint metadata.

    Pydantic keeps a field's constraints (e.g. ``PositiveInt``'s ``Gt(0)``) in
    ``FieldInfo.metadata``, separate from ``.annotation``; re-attach them so the
    per-field validator enforces the same rules the full model does.
    """
    if metadata:
        # Annotated is built dynamically here, which the type checker can't follow.
        return TypeAdapter(Annotated[(annotation, *metadata)])  # ty: ignore[invalid-type-form]
    return TypeAdapter(annotation)


# One validator per field, used to test a provided value in isolation so that a bad
# field reverts to its own default without rejecting its (valid) siblings.
_FIELD_ADAPTERS: dict[str, TypeAdapter] = {
    name: _field_adapter(field.annotation, field.metadata)
    for name, field in EventOverrides.model_fields.items()
}


def parse_overrides(description: str | None) -> EventOverrides:
    """Parse an event ``DESCRIPTION`` into an :class:`EventOverrides` (spec §6.3).

    Never raises: any problem degrades to defaults for the affected scope.
    """
    if not description or not description.strip():
        return EventOverrides()
    try:
        data = tomllib.loads(description)
    except tomllib.TOMLDecodeError:
        return EventOverrides()
    # TOML's top level is always a table, but guard anyway for the §6.3 "not a
    # mapping" case (e.g. a future parser, or being handed pre-decoded data).
    if not isinstance(data, dict):
        return EventOverrides()

    valid: dict[str, object] = {}
    for name, adapter in _FIELD_ADAPTERS.items():
        if name not in data:
            continue
        # An invalid single field is omitted → the model default applies to it.
        with contextlib.suppress(ValidationError):
            valid[name] = adapter.validate_python(data[name])
    # Every kept value already validated against its field, so this never raises.
    return EventOverrides.model_validate(valid)
