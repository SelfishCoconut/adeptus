"""Pydantic v2 schemas for the personas feature (Slice 15, §5.3 / §5.4).

The read ``Persona`` schema deliberately OMITS ``user_id`` — ownership is an internal
isolation concept (the list/get reads are already owner-scoped), never surfaced to the
client. ``is_builtin`` tells the UI which rows are read-only; ``slug`` is the stable
built-in identifier (null for custom personas).

Persona name + prompt text are stored and rendered VERBATIM (§5.5) — never redacted.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "MAX_NAME_CHARS",
    "MAX_PROMPT_CHARS",
    "Persona",
    "PersonaCreate",
    "PersonaList",
    "PersonaUpdate",
]

# Bounds also enforced by the API contract. Name is a short label; the prompt is bounded
# generously but capped so a pathological paste cannot bloat every turn's system message.
MAX_NAME_CHARS = 80
MAX_PROMPT_CHARS = 8192


class Persona(BaseModel):
    """One persona as exposed by the read/write API (§5.3).

    A built-in carries a ``slug`` and ``is_builtin=true`` (read-only, shared); a custom
    persona has ``slug=None`` and ``is_builtin=false`` (editable/deletable by its owner).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str = Field(description="Human-readable persona name (verbatim, §5.5). Unique per owner.")
    system_prompt: str = Field(
        description="The persona's distinct system prompt (§5.3), sent verbatim (§5.5)."
    )
    is_builtin: bool = Field(
        description=(
            "True for the four global seeded personas (read-only, shared); false for a "
            "caller-owned custom persona (editable/deletable by the caller only)."
        )
    )
    slug: str | None = Field(
        default=None,
        description=(
            "Stable slug for a built-in (general/recon/web-exploit/report-writer); null for "
            "custom personas. Drives the default-persona lookup."
        ),
    )
    created_at: datetime


class PersonaList(BaseModel):
    """The personas available to the caller: the four built-ins plus the caller's own."""

    items: list[Persona]


class PersonaCreate(BaseModel):
    """Request body for POST /api/v1/personas — create a custom persona owned by the caller."""

    name: str = Field(
        min_length=1,
        max_length=MAX_NAME_CHARS,
        description="Persona name; must be unique among the caller's own personas.",
    )
    system_prompt: str = Field(
        min_length=1,
        max_length=MAX_PROMPT_CHARS,
        description="The persona's system prompt, stored and sent verbatim (§5.5).",
    )


class PersonaUpdate(BaseModel):
    """Request body for PATCH /api/v1/personas/{persona_id}.

    All fields optional; only provided (non-null) fields are updated. An empty body is a
    no-op that returns the unchanged persona.
    """

    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME_CHARS)
    system_prompt: str | None = Field(default=None, min_length=1, max_length=MAX_PROMPT_CHARS)
