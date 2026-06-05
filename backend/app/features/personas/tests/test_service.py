"""Service tests — the ownership chokepoints and the resolve-for-turn fallback (§17.1).

Real async SQLite session; no external services. These cover the load-bearing isolation
behavior: built-in/foreign edit-delete → 404, and resolve falling back to general so a
foreign id never uses another user's prompt.
"""

from __future__ import annotations

from typing import cast
from uuid import UUID, uuid4

import pytest
from argon2 import PasswordHasher
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.features.auth import repository as auth_repo
from app.features.auth.models import User
from app.features.personas import repository as repo
from app.features.personas import service
from app.features.personas.seed import GENERAL_SYSTEM_PROMPT

_hasher = PasswordHasher()

pytestmark = pytest.mark.asyncio


async def _seed_user(db: AsyncSession, username: str) -> User:
    user = await auth_repo.create_user(
        db, username=username, password_hash=_hasher.hash("pw"), role="user"
    )
    await db.commit()
    return user


async def _seed_builtins(db: AsyncSession) -> None:
    await service.bootstrap_system_personas(db)
    await db.commit()


async def test_list_returns_builtins_plus_own(db_session: AsyncSession) -> None:
    await _seed_builtins(db_session)
    alice = await _seed_user(db_session, "alice")
    await service.create_persona(db_session, requester=alice, name="Mine", system_prompt="x")
    await db_session.commit()

    listed = await service.list_personas(db_session, requester=alice)
    names = [p.name for p in listed.items]
    assert "Mine" in names
    assert {"General", "Recon", "Web Exploit", "Report Writer"}.issubset(set(names))


async def test_create_custom_persona(db_session: AsyncSession) -> None:
    alice = await _seed_user(db_session, "alice")
    created = await service.create_persona(
        db_session, requester=alice, name="Cloud", system_prompt="cloud prompt"
    )
    await db_session.commit()
    assert created.is_builtin is False
    assert created.slug is None
    assert created.system_prompt == "cloud prompt"


async def test_create_duplicate_name_409(db_session: AsyncSession) -> None:
    alice = await _seed_user(db_session, "alice")
    await service.create_persona(db_session, requester=alice, name="Dup", system_prompt="a")
    await db_session.commit()
    with pytest.raises(service.PersonaNameConflictError):
        await service.create_persona(db_session, requester=alice, name="Dup", system_prompt="b")


async def test_update_builtin_404(db_session: AsyncSession) -> None:
    await _seed_builtins(db_session)
    alice = await _seed_user(db_session, "alice")
    general = await repo.get_builtin_by_slug(db_session, slug="general")
    assert general is not None
    with pytest.raises(NotFoundError):
        await service.update_persona(
            db_session, requester=alice, persona_id=cast(UUID, general.id), name="Hacked"
        )


async def test_update_other_users_persona_404(db_session: AsyncSession) -> None:
    alice = await _seed_user(db_session, "alice")
    bob = await _seed_user(db_session, "bob")
    bobs = await service.create_persona(db_session, requester=bob, name="Bobs", system_prompt="b")
    await db_session.commit()
    with pytest.raises(NotFoundError):
        await service.update_persona(db_session, requester=alice, persona_id=bobs.id, name="Stolen")


async def test_update_own_persona_partial(db_session: AsyncSession) -> None:
    alice = await _seed_user(db_session, "alice")
    p = await service.create_persona(db_session, requester=alice, name="A", system_prompt="a")
    await db_session.commit()
    updated = await service.update_persona(
        db_session, requester=alice, persona_id=p.id, system_prompt="a2"
    )
    await db_session.commit()
    assert updated.name == "A"
    assert updated.system_prompt == "a2"


async def test_update_rename_to_existing_name_409(db_session: AsyncSession) -> None:
    alice = await _seed_user(db_session, "alice")
    await service.create_persona(db_session, requester=alice, name="Taken", system_prompt="a")
    other = await service.create_persona(
        db_session, requester=alice, name="Other", system_prompt="b"
    )
    await db_session.commit()
    with pytest.raises(service.PersonaNameConflictError):
        await service.update_persona(db_session, requester=alice, persona_id=other.id, name="Taken")


async def test_delete_builtin_404(db_session: AsyncSession) -> None:
    await _seed_builtins(db_session)
    alice = await _seed_user(db_session, "alice")
    general = await repo.get_builtin_by_slug(db_session, slug="general")
    assert general is not None
    with pytest.raises(NotFoundError):
        await service.delete_persona(db_session, requester=alice, persona_id=cast(UUID, general.id))


async def test_delete_other_users_persona_404(db_session: AsyncSession) -> None:
    alice = await _seed_user(db_session, "alice")
    bob = await _seed_user(db_session, "bob")
    bobs = await service.create_persona(db_session, requester=bob, name="Bobs", system_prompt="b")
    await db_session.commit()
    with pytest.raises(NotFoundError):
        await service.delete_persona(db_session, requester=alice, persona_id=bobs.id)


async def test_resolve_returns_owned_custom(db_session: AsyncSession) -> None:
    await _seed_builtins(db_session)
    alice = await _seed_user(db_session, "alice")
    mine = await service.create_persona(db_session, requester=alice, name="Mine", system_prompt="m")
    await db_session.commit()

    resolved = await service.resolve_for_turn(
        db_session, persona_id=mine.id, user_id=cast(UUID, alice.id)
    )
    assert resolved.system_prompt == "m"
    assert resolved.is_builtin is False


async def test_resolve_foreign_id_falls_back_to_general(db_session: AsyncSession) -> None:
    """§17.1 — a foreign id never errors and never uses another user's prompt."""
    await _seed_builtins(db_session)
    alice = await _seed_user(db_session, "alice")
    bob = await _seed_user(db_session, "bob")
    bobs = await service.create_persona(
        db_session, requester=bob, name="Bobs", system_prompt="SECRET"
    )
    await db_session.commit()

    resolved = await service.resolve_for_turn(
        db_session, persona_id=bobs.id, user_id=cast(UUID, alice.id)
    )
    assert resolved.system_prompt == GENERAL_SYSTEM_PROMPT
    assert resolved.system_prompt != "SECRET"


async def test_resolve_null_returns_general(db_session: AsyncSession) -> None:
    await _seed_builtins(db_session)
    alice = await _seed_user(db_session, "alice")
    resolved = await service.resolve_for_turn(
        db_session, persona_id=None, user_id=cast(UUID, alice.id)
    )
    assert resolved.slug == "general"
    assert resolved.system_prompt == GENERAL_SYSTEM_PROMPT


async def test_resolve_unknown_id_falls_back_to_general(db_session: AsyncSession) -> None:
    await _seed_builtins(db_session)
    alice = await _seed_user(db_session, "alice")
    resolved = await service.resolve_for_turn(
        db_session, persona_id=uuid4(), user_id=cast(UUID, alice.id)
    )
    assert resolved.system_prompt == GENERAL_SYSTEM_PROMPT


async def test_resolve_synthesizes_general_when_unseeded(db_session: AsyncSession) -> None:
    """Built-ins not seeded → a transient general is synthesized (never raises, no None)."""
    alice = await _seed_user(db_session, "alice")
    resolved = await service.resolve_for_turn(
        db_session, persona_id=None, user_id=cast(UUID, alice.id)
    )
    assert resolved.is_builtin is True
    assert resolved.system_prompt == GENERAL_SYSTEM_PROMPT


async def test_bootstrap_seeds_four_then_idempotent(db_session: AsyncSession) -> None:
    first = await service.bootstrap_system_personas(db_session)
    await db_session.commit()
    assert first == 4

    second = await service.bootstrap_system_personas(db_session)
    await db_session.commit()
    assert second == 0

    # Still exactly four built-ins.
    alice = await _seed_user(db_session, "alice")
    listed = await service.list_personas(db_session, requester=alice)
    builtins = [p for p in listed.items if p.is_builtin]
    assert len(builtins) == 4
