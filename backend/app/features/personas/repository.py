"""Data-access layer for personas (Slice 15, §5.3 / §5.4).

Every read that resolves personas for a caller scopes to ``is_builtin OR user_id =
caller`` (the built-ins ∪ the caller's own) — the per-user isolation chokepoint
(§17.1). A custom persona is never visible, editable, or deletable by anyone but its
owner; the edit/delete paths additionally key on ``user_id`` so a built-in (NULL
user_id) or a foreign row can never be matched. The service layer is the policy layer;
this layer assumes the caller is already the authenticated user it passes.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.personas.models import Persona


async def list_for_user(db: AsyncSession, *, user_id: UUID) -> list[Persona]:
    """Return the personas available to the caller: built-ins ∪ the caller's own.

    Built-ins first (in seed/creation order), then the caller's custom personas
    newest-first (the contract order). The result set is small (4 built-ins + a user's
    handful of customs), so the two orderings are applied in Python for clarity.
    """
    result = await db.execute(
        select(Persona).where(or_(Persona.is_builtin.is_(True), Persona.user_id == user_id))
    )
    rows = list(result.scalars().all())
    builtins = sorted((r for r in rows if r.is_builtin), key=lambda r: (r.created_at, r.name))
    customs = sorted(
        (r for r in rows if not r.is_builtin), key=lambda r: r.created_at, reverse=True
    )
    return [*builtins, *customs]


async def get_for_user(db: AsyncSession, *, persona_id: UUID, user_id: UUID) -> Persona | None:
    """Return the persona iff it is a built-in OR owned by the caller; else None.

    The resolve/ownership chokepoint (§17.1): a custom persona owned by another user is
    indistinguishable from a missing one (both → None), so a caller can never probe for
    or use another user's private persona.
    """
    result = await db.execute(
        select(Persona).where(
            Persona.id == persona_id,
            or_(Persona.is_builtin.is_(True), Persona.user_id == user_id),
        )
    )
    return result.scalar_one_or_none()


async def get_builtin_by_slug(db: AsyncSession, *, slug: str) -> Persona | None:
    """Return the built-in persona with this stable slug, or None. Drives the default lookup."""
    result = await db.execute(
        select(Persona).where(Persona.slug == slug, Persona.is_builtin.is_(True))
    )
    return result.scalar_one_or_none()


async def create_custom(
    db: AsyncSession, *, user_id: UUID, name: str, system_prompt: str
) -> Persona:
    """Insert a custom persona owned by ``user_id`` and flush so its id is populated.

    The per-user name uniqueness is enforced by the partial unique index (the backstop);
    the service does the pre-check that maps a clash to a 409. The caller commits.
    """
    persona = Persona(
        user_id=user_id,
        name=name,
        slug=None,
        system_prompt=system_prompt,
        is_builtin=False,
    )
    db.add(persona)
    await db.flush()
    return persona


async def update_custom(
    db: AsyncSession,
    *,
    persona_id: UUID,
    user_id: UUID,
    name: str | None = None,
    system_prompt: str | None = None,
) -> Persona | None:
    """Update one of the caller's own custom personas; return the refreshed row or None.

    Owner-scoped: the row is resolved by ``(id, user_id)``, so a built-in (NULL user_id)
    or another user's persona never matches → None (the service maps that to 404). Only
    provided (non-None) fields change; an all-None call is a no-op that returns the row.
    The caller commits.
    """
    result = await db.execute(
        select(Persona).where(Persona.id == persona_id, Persona.user_id == user_id)
    )
    persona = result.scalar_one_or_none()
    if persona is None:
        return None
    if name is not None:
        persona.name = name
    if system_prompt is not None:
        persona.system_prompt = system_prompt
    await db.flush()
    return persona


async def get_owned_by_name(db: AsyncSession, *, user_id: UUID, name: str) -> Persona | None:
    """Return the caller's own custom persona with this name, or None (the 409 pre-check)."""
    result = await db.execute(
        select(Persona).where(Persona.user_id == user_id, Persona.name == name)
    )
    return result.scalar_one_or_none()


async def delete_custom(db: AsyncSession, *, persona_id: UUID, user_id: UUID) -> bool:
    """Delete one of the caller's own custom personas; return True iff a row was removed.

    Owner-scoped (keyed on ``user_id``), so a built-in or a foreign persona matches zero
    rows and returns False (the service maps that to 404). The caller commits.
    """
    result = await db.execute(
        delete(Persona).where(Persona.id == persona_id, Persona.user_id == user_id)
    )
    return cast("CursorResult[Any]", result).rowcount > 0


async def upsert_builtin(db: AsyncSession, *, slug: str, name: str, system_prompt: str) -> bool:
    """Idempotently seed one built-in by its stable slug; return True iff a row was inserted.

    Insert if the slug is absent; otherwise update the name/prompt in place (so a prompt
    tweak ships on redeploy without a duplicate). Keyed strictly on the built-in slug —
    custom rows (NULL slug) are never matched, so a user's library is never clobbered
    (Risk 5). A SELECT-then-write so it runs on both Postgres and the SQLite test engine;
    the partial unique ``(slug) WHERE is_builtin`` is the backstop against a startup race.
    """
    existing = await get_builtin_by_slug(db, slug=slug)
    if existing is None:
        db.add(
            Persona(
                user_id=None,
                slug=slug,
                name=name,
                system_prompt=system_prompt,
                is_builtin=True,
            )
        )
        await db.flush()
        return True
    existing.name = name
    existing.system_prompt = system_prompt
    await db.flush()
    return False
