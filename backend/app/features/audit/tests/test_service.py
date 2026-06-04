"""Service tests for the audit chokepoint + read paths (Slice 10 task 5).

The membership lookup (``eng_repo.get_engagement_for_member``) is monkeypatched so
these stay focused on the service's authorization + assembly logic; ``record`` and the
listings hit the real audit tables via the db_session fixture.
"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.features.audit import service
from app.features.audit.schemas import AuditAction
from app.features.auth.models import User


def _user(role: str = "user") -> User:
    return User(id=uuid4(), username="u", password_hash="$argon2id$placeholder", role=role)


def _patch_member(monkeypatch: pytest.MonkeyPatch, result: object) -> None:
    async def fake(db: object, engagement_id: UUID, user_id: UUID) -> object:
        return result

    monkeypatch.setattr(service.eng_repo, "get_engagement_for_member", fake)


async def test_record_emits_entry_with_attribution(db_session: AsyncSession) -> None:
    actor = uuid4()
    entry = await service.record(db_session, action=AuditAction.LOGIN, actor_user_id=actor)
    await db_session.commit()
    assert entry.action == "login"
    assert entry.actor_user_id == actor
    assert entry.seq == 1


async def test_record_self_approved_passthrough(db_session: AsyncSession) -> None:
    entry = await service.record(
        db_session,
        action=AuditAction.APPROVAL_GRANTED,
        actor_user_id=uuid4(),
        engagement_id=uuid4(),
        self_approved=True,
    )
    await db_session.commit()
    assert entry.self_approved is True


async def test_record_login_has_null_engagement(db_session: AsyncSession) -> None:
    entry = await service.record(db_session, action="login", actor_user_id=uuid4())
    await db_session.commit()
    assert entry.engagement_id is None


async def test_record_rejects_unknown_action(db_session: AsyncSession) -> None:
    with pytest.raises(ValueError):
        await service.record(db_session, action="not_a_real_action", actor_user_id=uuid4())


async def test_list_global_non_admin_403(db_session: AsyncSession) -> None:
    with pytest.raises(ForbiddenError):
        await service.list_global_audit(db_session, requester=_user(role="user"))


async def test_list_global_admin_returns_entries(db_session: AsyncSession) -> None:
    await service.record(db_session, action="login", actor_user_id=uuid4())
    await db_session.commit()
    page = await service.list_global_audit(db_session, requester=_user(role="admin"))
    assert [i.action for i in page.items] == [AuditAction.LOGIN]


async def test_list_engagement_non_member_404(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_member(monkeypatch, None)
    with pytest.raises(NotFoundError):
        await service.list_engagement_audit(db_session, engagement_id=uuid4(), requester=_user())


async def test_list_engagement_self_approved_filter(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_member(monkeypatch, ("engagement", "member"))  # any truthy => member
    eng = uuid4()
    await service.record(
        db_session,
        action="approval_granted",
        actor_user_id=uuid4(),
        engagement_id=eng,
        self_approved=True,
    )
    await service.record(db_session, action="tool_run", actor_user_id=uuid4(), engagement_id=eng)
    await db_session.commit()

    page = await service.list_engagement_audit(
        db_session, engagement_id=eng, requester=_user(), self_approved=True
    )
    assert [i.self_approved for i in page.items] == [True]


async def test_list_engagement_cursor_pagination(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_member(monkeypatch, ("engagement", "member"))
    eng = uuid4()
    for _ in range(3):
        await service.record(
            db_session, action="tool_run", actor_user_id=uuid4(), engagement_id=eng
        )
    await db_session.commit()

    page1 = await service.list_engagement_audit(
        db_session, engagement_id=eng, requester=_user(), limit=2
    )
    assert [i.seq for i in page1.items] == [3, 2]
    assert page1.next_cursor is not None

    page2 = await service.list_engagement_audit(
        db_session, engagement_id=eng, requester=_user(), cursor=page1.next_cursor, limit=2
    )
    assert [i.seq for i in page2.items] == [1]
    assert page2.next_cursor is None


async def test_malformed_cursor_raises_400(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_member(monkeypatch, ("engagement", "member"))
    with pytest.raises(BadRequestError):
        await service.list_engagement_audit(
            db_session, engagement_id=uuid4(), requester=_user(), cursor="!!!not-base64!!!"
        )
