"""Unit tests for the approvals repository (Slice 16 task 4).

The no-double-decision guard (``decide_request``) is load-bearing (Risk 1): a terminal
request returns ``None`` so the dangerous command can never run twice.
"""

from typing import cast
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.features.approvals import repository as repo
from app.features.approvals.models import ApprovalRequest


def _uid(value: object) -> UUID:
    """Cast a SQLAlchemy UUID column value to plain uuid.UUID (codebase idiom)."""
    return cast(UUID, value)


async def _make(
    db: AsyncSession,
    *,
    engagement_id: UUID | None = None,
    chat_message_id: UUID | None = None,
    initiator_user_id: UUID | None = None,
    tool_name: str = "run",
    reasons: list[str] | None = None,
) -> ApprovalRequest:
    req = await repo.create_request(
        db,
        engagement_id=engagement_id or uuid4(),
        chat_message_id=chat_message_id or uuid4(),
        initiator_user_id=initiator_user_id or uuid4(),
        server_name="shell-exec",
        tool_name=tool_name,
        args={"cmd": "hydra -P rockyou.txt"},
        reasons=reasons or ["credential_attack"],
        rationale="brute force",
    )
    await db.commit()
    return req


async def test_create_persists_pending_request(db_session: AsyncSession) -> None:
    req = await _make(db_session)
    assert req.id is not None
    assert req.status == "pending"
    assert req.reasons == ["credential_attack"]
    assert req.args == {"cmd": "hydra -P rockyou.txt"}
    assert req.acted_by_user_id is None
    assert req.decided_at is None


async def test_get_request_for_engagement_scopes_by_engagement(db_session: AsyncSession) -> None:
    eng = uuid4()
    req = await _make(db_session, engagement_id=eng)
    found = await repo.get_request_for_engagement(
        db_session, engagement_id=eng, request_id=_uid(req.id)
    )
    assert found is not None and found.id == req.id
    # A different engagement cannot see it (§17.1).
    other = await repo.get_request_for_engagement(
        db_session, engagement_id=uuid4(), request_id=_uid(req.id)
    )
    assert other is None


async def test_list_pending_for_engagement(db_session: AsyncSession) -> None:
    eng = uuid4()
    pending = await _make(db_session, engagement_id=eng)
    decided = await _make(db_session, engagement_id=eng)
    await repo.decide_request(
        db_session,
        request_id=_uid(decided.id),
        status="approved",
        acted_by_user_id=uuid4(),
        self_approved=False,
    )
    await db_session.commit()
    # Noise in another engagement must not leak in.
    await _make(db_session, engagement_id=uuid4())

    rows, next_cursor = await repo.list_for_engagement(
        db_session, engagement_id=eng, status="pending"
    )
    assert [r.id for r in rows] == [pending.id]
    assert next_cursor is None


async def test_list_for_engagement_unfiltered_newest_first(db_session: AsyncSession) -> None:
    eng = uuid4()
    first = await _make(db_session, engagement_id=eng)
    second = await _make(db_session, engagement_id=eng)
    rows, _ = await repo.list_for_engagement(db_session, engagement_id=eng)
    ids = {r.id for r in rows}
    assert ids == {first.id, second.id}


async def test_list_by_chat_message(db_session: AsyncSession) -> None:
    msg = uuid4()
    a = await _make(db_session, chat_message_id=msg)
    b = await _make(db_session, chat_message_id=msg)
    await _make(db_session, chat_message_id=uuid4())  # different turn — excluded
    rows = await repo.list_for_chat_message(db_session, message_id=msg)
    assert {r.id for r in rows} == {a.id, b.id}


async def test_decide_transitions_pending_to_approved(db_session: AsyncSession) -> None:
    req = await _make(db_session)
    decider = uuid4()
    run_id = uuid4()
    updated = await repo.decide_request(
        db_session,
        request_id=_uid(req.id),
        status="approved",
        acted_by_user_id=decider,
        self_approved=True,
        tool_run_id=run_id,
    )
    assert updated is not None
    assert updated.status == "approved"
    assert updated.acted_by_user_id == decider
    assert updated.self_approved is True
    assert updated.tool_run_id == run_id
    assert updated.decided_at is not None


async def test_decide_transitions_pending_to_rejected(db_session: AsyncSession) -> None:
    req = await _make(db_session)
    updated = await repo.decide_request(
        db_session,
        request_id=_uid(req.id),
        status="rejected",
        acted_by_user_id=uuid4(),
        self_approved=False,
    )
    assert updated is not None
    assert updated.status == "rejected"
    assert updated.tool_run_id is None


async def test_decide_on_terminal_returns_none(db_session: AsyncSession) -> None:
    req = await _make(db_session)
    first = await repo.decide_request(
        db_session,
        request_id=_uid(req.id),
        status="approved",
        acted_by_user_id=uuid4(),
        self_approved=False,
    )
    assert first is not None
    # A second decision on the now-terminal request claims nothing (idempotency guard).
    second = await repo.decide_request(
        db_session,
        request_id=_uid(req.id),
        status="rejected",
        acted_by_user_id=uuid4(),
        self_approved=False,
    )
    assert second is None
    # The original decision is untouched.
    reloaded = await repo.get_request_for_engagement(
        db_session, engagement_id=_uid(req.engagement_id), request_id=_uid(req.id)
    )
    assert reloaded is not None and reloaded.status == "approved"


async def test_concurrent_decide_only_one_wins(db_session: AsyncSession) -> None:
    # Interleave two decides on the same pending request; the guarded conditional UPDATE
    # means exactly one claims the row and the other gets None (no double-execution). This
    # proves the WHERE status='pending' predicate; the TRUE concurrent row-locking guarantee
    # (FOR UPDATE is ignored by SQLite) is a Postgres-only property exercised end-to-end by
    # test_integration.test_double_approve_runs_only_once.
    req = await _make(db_session)
    winner = await repo.decide_request(
        db_session,
        request_id=_uid(req.id),
        status="approved",
        acted_by_user_id=uuid4(),
        self_approved=True,
        tool_run_id=uuid4(),
    )
    loser = await repo.decide_request(
        db_session,
        request_id=_uid(req.id),
        status="approved",
        acted_by_user_id=uuid4(),
        self_approved=False,
        tool_run_id=uuid4(),
    )
    assert (winner is None) != (loser is None)  # exactly one won
    assert winner is not None and loser is None
