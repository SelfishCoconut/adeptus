"""Schema tests for the personas feature — bounds, partial updates, builtin/custom round-trip."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.features.personas.schemas import (
    MAX_NAME_CHARS,
    MAX_PROMPT_CHARS,
    Persona,
    PersonaCreate,
    PersonaUpdate,
)


def test_persona_create_accepts_bounds() -> None:
    create = PersonaCreate(name="A", system_prompt="x")
    assert create.name == "A"
    create_max = PersonaCreate(name="N" * MAX_NAME_CHARS, system_prompt="p" * MAX_PROMPT_CHARS)
    assert len(create_max.system_prompt) == MAX_PROMPT_CHARS


@pytest.mark.parametrize(
    ("name", "system_prompt"),
    [
        ("", "ok"),  # name too short
        ("N" * (MAX_NAME_CHARS + 1), "ok"),  # name too long
        ("ok", ""),  # prompt too short
        ("ok", "p" * (MAX_PROMPT_CHARS + 1)),  # prompt too long
    ],
)
def test_persona_create_rejects_out_of_bounds(name: str, system_prompt: str) -> None:
    with pytest.raises(ValidationError):
        PersonaCreate(name=name, system_prompt=system_prompt)


def test_persona_update_allows_empty_noop() -> None:
    update = PersonaUpdate()
    assert update.name is None
    assert update.system_prompt is None


def test_persona_update_allows_partial() -> None:
    only_name = PersonaUpdate(name="Renamed")
    assert only_name.name == "Renamed"
    assert only_name.system_prompt is None

    only_prompt = PersonaUpdate(system_prompt="new prompt")
    assert only_prompt.name is None
    assert only_prompt.system_prompt == "new prompt"


@pytest.mark.parametrize("field", ["name", "system_prompt"])
def test_persona_update_rejects_empty_strings(field: str) -> None:
    with pytest.raises(ValidationError):
        PersonaUpdate(**{field: ""})


def test_persona_round_trips_builtin() -> None:
    """A built-in row (slug set, user_id None) maps through the read schema; user_id is dropped."""
    row = SimpleNamespace(
        id=uuid4(),
        user_id=None,
        name="Recon",
        slug="recon",
        system_prompt="recon prompt",
        is_builtin=True,
        created_at=datetime.now(UTC),
    )
    persona = Persona.model_validate(row)
    assert persona.is_builtin is True
    assert persona.slug == "recon"
    # The read schema does not expose ownership.
    assert not hasattr(persona, "user_id")


def test_persona_round_trips_custom() -> None:
    """A custom row (slug None, user_id set) maps through the read schema as is_builtin=False."""
    row = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        name="Cloud Pentest",
        slug=None,
        system_prompt="cloud prompt",
        is_builtin=False,
        created_at=datetime.now(UTC),
    )
    persona = Persona.model_validate(row)
    assert persona.is_builtin is False
    assert persona.slug is None
    assert persona.name == "Cloud Pentest"
