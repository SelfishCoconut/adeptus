"""Repository tests against a real async SQLite session.

Centred on the §17.1 isolation shape: the list/get reads return built-ins to anyone but
custom personas only to their owner; edit/delete are owner-scoped; the seed upsert is
idempotent and never touches custom rows.
"""

from __future__ import annotations

import datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from argon2 import PasswordHasher
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.auth import repository as auth_repo
from app.features.auth.models import User
from app.features.personas import repository as repo

_hasher = PasswordHasher()

pytestmark = pytest.mark.asyncio


async def _seed_user(db: AsyncSession, username: str) -> UUID:
    user: User = await auth_repo.create_user(
        db, username=username, password_hash=_hasher.hash("pw"), role="user"
    )
    await db.commit()
    return cast(UUID, user.id)


async def _seed_builtins(db: AsyncSession) -> None:
    await repo.upsert_builtin(db, slug="general", name="General", system_prompt="gen")
    await repo.upsert_builtin(db, slug="recon", name="Recon", system_prompt="rec")
    await db.commit()


async def test_list_returns_builtins_plus_only_callers_own(db_session: AsyncSession) -> None:
    await _seed_builtins(db_session)
    alice = await _seed_user(db_session, "alice")
    bob = await _seed_user(db_session, "bob")
    await repo.create_custom(db_session, user_id=alice, name="Alice P", system_prompt="a")
    await repo.create_custom(db_session, user_id=bob, name="Bob P", system_prompt="b")
    await db_session.commit()

    listed = await repo.list_for_user(db_session, user_id=alice)
    names = [p.name for p in listed]
    assert "Alice P" in names
    assert "Bob P" not in names  # another user's custom never appears
    assert {"General", "Recon"}.issubset(set(names))
    # Built-ins come first.
    assert listed[0].is_builtin and listed[1].is_builtin


async def test_list_orders_customs_newest_first(db_session: AsyncSession) -> None:
    await _seed_builtins(db_session)
    alice = await _seed_user(db_session, "alice")
    # Explicit created_at (the in-memory SQLite clock is only second-resolution, so two
    # quick inserts would tie; production Postgres now() is microsecond-resolution).
    first = await repo.create_custom(db_session, user_id=alice, name="First", system_prompt="1")
    first.created_at = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
    second = await repo.create_custom(db_session, user_id=alice, name="Second", system_prompt="2")
    second.created_at = datetime.datetime(2020, 1, 2, tzinfo=datetime.UTC)
    await db_session.commit()

    customs = [p for p in await repo.list_for_user(db_session, user_id=alice) if not p.is_builtin]
    assert [c.id for c in customs] == [second.id, first.id]


async def test_get_for_user_builtin_visible_to_anyone(db_session: AsyncSession) -> None:
    await _seed_builtins(db_session)
    bob = await _seed_user(db_session, "bob")
    general = await repo.get_builtin_by_slug(db_session, slug="general")
    assert general is not None

    got = await repo.get_for_user(db_session, persona_id=cast(UUID, general.id), user_id=bob)
    assert got is not None
    assert got.is_builtin


async def test_get_for_user_custom_only_for_owner(db_session: AsyncSession) -> None:
    alice = await _seed_user(db_session, "alice")
    bob = await _seed_user(db_session, "bob")
    custom = await repo.create_custom(db_session, user_id=alice, name="A", system_prompt="a")
    await db_session.commit()

    assert (
        await repo.get_for_user(db_session, persona_id=cast(UUID, custom.id), user_id=alice)
        is not None
    )
    # Another user gets None — indistinguishable from missing (§17.1).
    assert (
        await repo.get_for_user(db_session, persona_id=cast(UUID, custom.id), user_id=bob) is None
    )


async def test_get_owned_by_name_enforces_uniqueness_precheck(db_session: AsyncSession) -> None:
    alice = await _seed_user(db_session, "alice")
    await repo.create_custom(db_session, user_id=alice, name="Dup", system_prompt="a")
    await db_session.commit()
    assert await repo.get_owned_by_name(db_session, user_id=alice, name="Dup") is not None
    assert await repo.get_owned_by_name(db_session, user_id=alice, name="Other") is None


async def test_update_custom_owner_scoped(db_session: AsyncSession) -> None:
    alice = await _seed_user(db_session, "alice")
    bob = await _seed_user(db_session, "bob")
    custom = await repo.create_custom(db_session, user_id=alice, name="A", system_prompt="a")
    await db_session.commit()
    pid = cast(UUID, custom.id)

    # Foreign caller can't update.
    assert await repo.update_custom(db_session, persona_id=pid, user_id=bob, name="X") is None
    # Owner can; only provided fields change.
    updated = await repo.update_custom(db_session, persona_id=pid, user_id=alice, name="A2")
    assert updated is not None
    assert updated.name == "A2"
    assert updated.system_prompt == "a"


async def test_delete_custom_owner_scoped(db_session: AsyncSession) -> None:
    alice = await _seed_user(db_session, "alice")
    bob = await _seed_user(db_session, "bob")
    custom = await repo.create_custom(db_session, user_id=alice, name="A", system_prompt="a")
    await db_session.commit()
    pid = cast(UUID, custom.id)

    assert await repo.delete_custom(db_session, persona_id=pid, user_id=bob) is False
    assert await repo.delete_custom(db_session, persona_id=pid, user_id=alice) is True
    # Gone now.
    assert await repo.get_for_user(db_session, persona_id=pid, user_id=alice) is None


async def test_delete_custom_cannot_delete_builtin(db_session: AsyncSession) -> None:
    await _seed_builtins(db_session)
    alice = await _seed_user(db_session, "alice")
    general = await repo.get_builtin_by_slug(db_session, slug="general")
    assert general is not None
    # A built-in (NULL user_id) matches no (id, user_id) row → not deleted.
    assert (
        await repo.delete_custom(db_session, persona_id=cast(UUID, general.id), user_id=alice)
        is False
    )


async def test_upsert_builtin_idempotent(db_session: AsyncSession) -> None:
    inserted_first = await repo.upsert_builtin(
        db_session, slug="recon", name="Recon", system_prompt="v1"
    )
    await db_session.commit()
    assert inserted_first is True

    # Second upsert updates in place, does not insert.
    inserted_second = await repo.upsert_builtin(
        db_session, slug="recon", name="Recon", system_prompt="v2"
    )
    await db_session.commit()
    assert inserted_second is False

    row = await repo.get_builtin_by_slug(db_session, slug="recon")
    assert row is not None
    assert row.system_prompt == "v2"


async def test_upsert_builtin_does_not_clobber_custom(db_session: AsyncSession) -> None:
    alice = await _seed_user(db_session, "alice")
    custom = await repo.create_custom(db_session, user_id=alice, name="Mine", system_prompt="keep")
    await db_session.commit()

    await repo.upsert_builtin(db_session, slug="general", name="General", system_prompt="gen")
    await db_session.commit()

    still = await repo.get_for_user(db_session, persona_id=cast(UUID, custom.id), user_id=alice)
    assert still is not None
    assert still.system_prompt == "keep"


async def test_get_for_user_unknown_id_returns_none(db_session: AsyncSession) -> None:
    alice = await _seed_user(db_session, "alice")
    assert await repo.get_for_user(db_session, persona_id=uuid4(), user_id=alice) is None
