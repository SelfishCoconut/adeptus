"""Business logic for personas (Slice 15, §5.3 / §5.4) — the ownership chokepoints.

Isolation posture (§17.1, mirrors per-user chat):
  - A custom persona is visible / editable / deletable by ONLY its creator. Edit and
    delete resolve a CALLER-OWNED CUSTOM row; a built-in or another user's persona returns
    ``NotFoundError`` (404), so a built-in can never be mutated and a foreign persona is
    invisible (no existence disclosure).
  - ``resolve_for_turn`` (the chat seam) NEVER raises: an unknown / foreign / null
    persona_id falls back to the ``general`` built-in, so a turn can never be made to use
    another user's private prompt and a deleted persona degrades gracefully.

No redaction (§5.5): persona name + prompt text are stored and returned verbatim.

Callers (router / chat / lifespan) commit; this layer only flushes via the repository.
"""

from __future__ import annotations

from typing import cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.features.auth.models import User
from app.features.personas import repository as repo
from app.features.personas.models import Persona
from app.features.personas.schemas import Persona as PersonaSchema
from app.features.personas.schemas import PersonaList
from app.features.personas.seed import GENERAL_SLUG, SYSTEM_PERSONAS

# The seed entry the fallback synthesizes from when the built-ins are not (yet) in the DB.
_GENERAL_SEED = next(p for p in SYSTEM_PERSONAS if p.slug == GENERAL_SLUG)


class PersonaNameConflictError(ConflictError):
    """Raised when a create/edit would give the caller two custom personas the same name.

    Subclasses the core ``ConflictError`` so the registered handler maps it to HTTP 409.
    Carries the (caller's own) name, which is non-secret.
    """

    def __init__(self, name: str) -> None:
        super().__init__(f"You already have a persona named {name!r}")


def _user_id(user: User) -> UUID:
    return cast(UUID, user.id)


async def list_personas(db: AsyncSession, *, requester: User) -> PersonaList:
    """Return the personas available to the caller: the four built-ins plus the caller's own."""
    rows = await repo.list_for_user(db, user_id=_user_id(requester))
    return PersonaList(items=[PersonaSchema.model_validate(r) for r in rows])


async def create_persona(
    db: AsyncSession, *, requester: User, name: str, system_prompt: str
) -> PersonaSchema:
    """Create a custom persona owned by the caller (§5.3). 409 on a duplicate name."""
    uid = _user_id(requester)
    if await repo.get_owned_by_name(db, user_id=uid, name=name) is not None:
        raise PersonaNameConflictError(name)
    created = await repo.create_custom(db, user_id=uid, name=name, system_prompt=system_prompt)
    return PersonaSchema.model_validate(created)


async def update_persona(
    db: AsyncSession,
    *,
    requester: User,
    persona_id: UUID,
    name: str | None = None,
    system_prompt: str | None = None,
) -> PersonaSchema:
    """Edit one of the caller's own custom personas (§5.3).

    Resolves the target first: a built-in or another user's persona is invisible →
    ``NotFoundError`` (404), so a built-in cannot be edited (even with a clashing name).
    Renaming to a name the caller already uses raises ``PersonaNameConflictError`` (409).
    """
    uid = _user_id(requester)
    target = await repo.get_for_user(db, persona_id=persona_id, user_id=uid)
    if target is None or target.is_builtin:
        raise NotFoundError("Persona not found")
    if name is not None and name != target.name:
        if await repo.get_owned_by_name(db, user_id=uid, name=name) is not None:
            raise PersonaNameConflictError(name)
    updated = await repo.update_custom(
        db, persona_id=persona_id, user_id=uid, name=name, system_prompt=system_prompt
    )
    if updated is None:  # racing delete between resolve and update
        raise NotFoundError("Persona not found")
    return PersonaSchema.model_validate(updated)


async def delete_persona(db: AsyncSession, *, requester: User, persona_id: UUID) -> None:
    """Delete one of the caller's own custom personas (§5.3). 404 for a built-in/foreign id."""
    if not await repo.delete_custom(db, persona_id=persona_id, user_id=_user_id(requester)):
        raise NotFoundError("Persona not found")


async def resolve_for_turn(db: AsyncSession, *, persona_id: UUID | None, user_id: UUID) -> Persona:
    """Resolve the persona to use for a chat turn — the single function chat calls.

    Returns the persona if it is a built-in OR owned by the caller. On a null / unknown /
    foreign id, falls back to the ``general`` built-in (never raises, never another user's
    prompt — §17.1). If the built-ins are not seeded (degenerate), synthesizes a transient
    ``general`` so a turn always has a usable system prompt and stays byte-identical to the
    pre-slice default.
    """
    if persona_id is not None:
        persona = await repo.get_for_user(db, persona_id=persona_id, user_id=user_id)
        if persona is not None:
            return persona
    general = await repo.get_builtin_by_slug(db, slug=GENERAL_SLUG)
    if general is not None:
        return general
    return Persona(
        user_id=None,
        slug=_GENERAL_SEED.slug,
        name=_GENERAL_SEED.name,
        system_prompt=_GENERAL_SEED.system_prompt,
        is_builtin=True,
    )


async def bootstrap_system_personas(db: AsyncSession) -> int:
    """Idempotently seed the four built-ins by slug; return the count newly inserted.

    Mirrors the §3 admin bootstrap (Decision 5): safe to run on every boot — a slug that
    already exists is updated in place (a prompt tweak ships on redeploy), never duplicated.
    The caller commits. Returns 4 on the first ever boot, 0 thereafter.
    """
    inserted = 0
    for seed in SYSTEM_PERSONAS:
        if await repo.upsert_builtin(
            db, slug=seed.slug, name=seed.name, system_prompt=seed.system_prompt
        ):
            inserted += 1
    return inserted
