"""Unit tests for the autonomy service (Slice 18).

Membership is the §17.1 chokepoint (non-member → 404). Each test patches the membership
lookup, the audit recorder, and the username resolver so the service logic is exercised
against the real autonomy_grants table without the full DB.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.features.approvals.schemas import ApprovalReason
from app.features.auth.models import User
from app.features.autonomy import repository as repo
from app.features.autonomy import service


def _user(username: str = "alice", user_id: UUID | None = None) -> User:
    return cast(User, SimpleNamespace(id=user_id or uuid4(), username=username))


@contextmanager
def _patched(*, member: bool = True) -> Iterator[AsyncMock]:
    """Patch membership (member/non-member), audit recorder, username resolver.

    Yields the audit-record AsyncMock so tests can assert the emitted action.
    """
    with (
        patch.object(service.eng_repo, "get_engagement_for_member", new_callable=AsyncMock) as m,
        patch.object(service.audit_service, "record", new_callable=AsyncMock) as rec,
        patch.object(service.auth_repo, "get_user_by_id", new_callable=AsyncMock) as gu,
    ):
        m.return_value = (SimpleNamespace(status="active"), SimpleNamespace()) if member else None
        gu.return_value = SimpleNamespace(username="alice")
        yield rec


async def test_grant_persists_and_audits(db_session: AsyncSession) -> None:
    eng = uuid4()
    with _patched() as audit:
        read = await service.grant(
            db_session, engagement_id=eng, requester=_user(), reason=ApprovalReason.AGGRESSIVE_SCAN
        )
    assert read.reason is ApprovalReason.AGGRESSIVE_SCAN
    assert read.revoked_at is None
    assert read.granted_by_username == "alice"
    audit.assert_awaited_once()
    assert audit.call_args.kwargs["action"].value == "autonomy_granted"
    assert await repo.get_active_reasons(db_session, engagement_id=eng) == {"aggressive_scan"}


async def test_grant_non_member_raises_not_found(db_session: AsyncSession) -> None:
    with _patched(member=False), pytest.raises(NotFoundError):
        await service.grant(
            db_session, engagement_id=uuid4(), requester=_user(), reason=ApprovalReason.TARGET_WRITE
        )


async def test_grant_duplicate_raises_conflict(db_session: AsyncSession) -> None:
    eng = uuid4()
    with _patched():
        await service.grant(
            db_session, engagement_id=eng, requester=_user(), reason=ApprovalReason.TARGET_WRITE
        )
        with pytest.raises(ConflictError):
            await service.grant(
                db_session, engagement_id=eng, requester=_user(), reason=ApprovalReason.TARGET_WRITE
            )


async def test_grant_unclassified_manifest_raises_bad_request(db_session: AsyncSession) -> None:
    # The service is the defense-in-depth backstop behind the schema validator.
    with _patched(), pytest.raises(BadRequestError):
        await service.grant(
            db_session,
            engagement_id=uuid4(),
            requester=_user(),
            reason=ApprovalReason.UNCLASSIFIED_MANIFEST,
        )


async def test_grant_out_of_scope_is_delegable(db_session: AsyncSession) -> None:
    eng = uuid4()
    with _patched():
        read = await service.grant(
            db_session, engagement_id=eng, requester=_user(), reason=ApprovalReason.OUT_OF_SCOPE
        )
    assert read.reason is ApprovalReason.OUT_OF_SCOPE


async def test_list_returns_active_grants(db_session: AsyncSession) -> None:
    eng = uuid4()
    with _patched():
        await service.grant(
            db_session, engagement_id=eng, requester=_user(), reason=ApprovalReason.AGGRESSIVE_SCAN
        )
        await service.grant(
            db_session, engagement_id=eng, requester=_user(), reason=ApprovalReason.TARGET_WRITE
        )
        grants = await service.list_grants(db_session, engagement_id=eng, requester=_user())
    assert {g.reason.value for g in grants} == {"aggressive_scan", "target_write"}


async def test_list_non_member_raises_not_found(db_session: AsyncSession) -> None:
    with _patched(member=False), pytest.raises(NotFoundError):
        await service.list_grants(db_session, engagement_id=uuid4(), requester=_user())


async def test_revoke_makes_grant_inactive_and_audits(db_session: AsyncSession) -> None:
    eng = uuid4()
    with _patched() as audit:
        read = await service.grant(
            db_session,
            engagement_id=eng,
            requester=_user(),
            reason=ApprovalReason.CREDENTIAL_ATTACK,
        )
        await service.revoke(db_session, engagement_id=eng, grant_id=read.id, requester=_user())
    assert audit.await_count == 2
    assert audit.call_args.kwargs["action"].value == "autonomy_revoked"
    assert await repo.get_active_reasons(db_session, engagement_id=eng) == set()


async def test_revoke_unknown_raises_not_found(db_session: AsyncSession) -> None:
    with _patched(), pytest.raises(NotFoundError):
        await service.revoke(db_session, engagement_id=uuid4(), grant_id=uuid4(), requester=_user())


async def test_revoke_non_member_raises_not_found(db_session: AsyncSession) -> None:
    with _patched(member=False), pytest.raises(NotFoundError):
        await service.revoke(db_session, engagement_id=uuid4(), grant_id=uuid4(), requester=_user())
