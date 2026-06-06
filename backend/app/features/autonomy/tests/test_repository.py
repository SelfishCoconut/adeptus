"""Unit tests for the autonomy repository (Slice 18).

The load-bearing behaviours: ``get_active_reasons`` (drives auto-approve), the partial
unique index (one active grant per category), and the guarded ``revoke`` (effective
immediately; double-revoke / wrong-engagement is a clean None).
"""

from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.autonomy import repository as repo


def _uid(value: object) -> UUID:
    """Cast a SQLAlchemy UUID column value to plain uuid.UUID (codebase idiom)."""
    return cast(UUID, value)


async def test_create_persists_active_grant(db_session: AsyncSession) -> None:
    eng, user = uuid4(), uuid4()
    grant = await repo.create_grant(
        db_session, engagement_id=eng, reason="aggressive_scan", granted_by_user_id=user
    )
    await db_session.commit()
    assert grant.id is not None
    assert grant.reason == "aggressive_scan"
    assert grant.revoked_at is None
    assert grant.granted_by_user_id == user


async def test_get_active_reasons_returns_only_active(db_session: AsyncSession) -> None:
    eng, user = uuid4(), uuid4()
    await repo.create_grant(
        db_session, engagement_id=eng, reason="aggressive_scan", granted_by_user_id=user
    )
    g2 = await repo.create_grant(
        db_session, engagement_id=eng, reason="target_write", granted_by_user_id=user
    )
    await db_session.commit()
    assert await repo.get_active_reasons(db_session, engagement_id=eng) == {
        "aggressive_scan",
        "target_write",
    }
    # Revoke one → it drops out of the active set.
    await repo.revoke(db_session, engagement_id=eng, grant_id=_uid(g2.id), revoked_by_user_id=user)
    await db_session.commit()
    assert await repo.get_active_reasons(db_session, engagement_id=eng) == {"aggressive_scan"}


async def test_get_active_grant_map_maps_reason_to_grant_id(db_session: AsyncSession) -> None:
    eng, user = uuid4(), uuid4()
    g1 = await repo.create_grant(
        db_session, engagement_id=eng, reason="aggressive_scan", granted_by_user_id=user
    )
    g2 = await repo.create_grant(
        db_session, engagement_id=eng, reason="out_of_scope", granted_by_user_id=user
    )
    await db_session.commit()
    grant_map = await repo.get_active_grant_map(db_session, engagement_id=eng)
    assert grant_map == {"aggressive_scan": _uid(g1.id), "out_of_scope": _uid(g2.id)}
    # A revoke drops the reason from the map (used per-turn to trace the covering grant).
    await repo.revoke(db_session, engagement_id=eng, grant_id=_uid(g2.id), revoked_by_user_id=user)
    await db_session.commit()
    assert await repo.get_active_grant_map(db_session, engagement_id=eng) == {
        "aggressive_scan": _uid(g1.id)
    }


async def test_get_active_reasons_scoped_to_engagement(db_session: AsyncSession) -> None:
    eng_a, eng_b, user = uuid4(), uuid4(), uuid4()
    await repo.create_grant(
        db_session, engagement_id=eng_a, reason="aggressive_scan", granted_by_user_id=user
    )
    await db_session.commit()
    assert await repo.get_active_reasons(db_session, engagement_id=eng_b) == set()


async def test_duplicate_active_grant_violates_unique_index(db_session: AsyncSession) -> None:
    eng, user = uuid4(), uuid4()
    await repo.create_grant(
        db_session, engagement_id=eng, reason="aggressive_scan", granted_by_user_id=user
    )
    await db_session.commit()
    with pytest.raises(IntegrityError):
        await repo.create_grant(
            db_session, engagement_id=eng, reason="aggressive_scan", granted_by_user_id=user
        )
        await db_session.flush()
    await db_session.rollback()


async def test_revoke_then_regrant_allowed(db_session: AsyncSession) -> None:
    eng, user = uuid4(), uuid4()
    g1 = await repo.create_grant(
        db_session, engagement_id=eng, reason="out_of_scope", granted_by_user_id=user
    )
    await db_session.commit()
    await repo.revoke(db_session, engagement_id=eng, grant_id=_uid(g1.id), revoked_by_user_id=user)
    await db_session.commit()
    # Re-grant the same category after revoke: allowed (partial index only guards active).
    g2 = await repo.create_grant(
        db_session, engagement_id=eng, reason="out_of_scope", granted_by_user_id=user
    )
    await db_session.commit()
    assert g2.id != g1.id
    assert await repo.get_active_reasons(db_session, engagement_id=eng) == {"out_of_scope"}


async def test_revoke_sets_attribution_and_returns_row(db_session: AsyncSession) -> None:
    eng, granter, revoker = uuid4(), uuid4(), uuid4()
    g = await repo.create_grant(
        db_session, engagement_id=eng, reason="credential_attack", granted_by_user_id=granter
    )
    await db_session.commit()
    revoked = await repo.revoke(
        db_session, engagement_id=eng, grant_id=_uid(g.id), revoked_by_user_id=revoker
    )
    await db_session.commit()
    assert revoked is not None
    assert revoked.revoked_at is not None
    assert revoked.revoked_by_user_id == revoker


async def test_revoke_unknown_or_already_revoked_returns_none(db_session: AsyncSession) -> None:
    eng, user = uuid4(), uuid4()
    # Unknown id.
    assert (
        await repo.revoke(db_session, engagement_id=eng, grant_id=uuid4(), revoked_by_user_id=user)
        is None
    )
    g = await repo.create_grant(
        db_session, engagement_id=eng, reason="target_write", granted_by_user_id=user
    )
    await db_session.commit()
    await repo.revoke(db_session, engagement_id=eng, grant_id=_uid(g.id), revoked_by_user_id=user)
    await db_session.commit()
    # Second revoke → already revoked → None.
    assert (
        await repo.revoke(
            db_session, engagement_id=eng, grant_id=_uid(g.id), revoked_by_user_id=user
        )
        is None
    )


async def test_revoke_wrong_engagement_returns_none(db_session: AsyncSession) -> None:
    eng_a, eng_b, user = uuid4(), uuid4(), uuid4()
    g = await repo.create_grant(
        db_session, engagement_id=eng_a, reason="aggressive_scan", granted_by_user_id=user
    )
    await db_session.commit()
    # Revoke attempt scoped to a different engagement must not touch it.
    assert (
        await repo.revoke(
            db_session, engagement_id=eng_b, grant_id=_uid(g.id), revoked_by_user_id=user
        )
        is None
    )
    assert await repo.get_active_reasons(db_session, engagement_id=eng_a) == {"aggressive_scan"}
