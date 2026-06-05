"""Integration tests for the approval flow (Slice 16) — the load-bearing safety properties.

Real audit chain (SQLite — FOR UPDATE is ignored, so this proves serialized correctness; the
true row-locking no-double-decision guarantee is a Postgres-only property), real
engagement/membership tables, and the real ``service.decide`` end to end. The MCP tool-run
pipeline (``execute_tool_run``) is mocked — no subprocess ever runs (CLAUDE.md). These prove
Risk 1 (no double-execution), §14 (the chain stays intact with approval entries woven in), and
the decider/initiator attribution split (Resolved decision 3).

Marked ``integration`` so the fast unit gate skips them; run via ``make test-integration``.
"""

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import NotFoundError
from app.features.approvals import repository as repo
from app.features.approvals import service
from app.features.approvals.classifier import ToolConfig
from app.features.approvals.schemas import ApprovalReason, ApprovalStatus, ProposedAction
from app.features.audit import verify as audit_verify
from app.features.audit.models import AuditEntry
from app.features.auth import repository as auth_repo
from app.features.auth.models import User
from app.features.engagements import repository as eng_repo

pytestmark = pytest.mark.integration

_hasher = PasswordHasher()

AppFactory = tuple[FastAPI, async_sessionmaker[AsyncSession], AsyncMock]


async def _seed_user(factory: async_sessionmaker[AsyncSession], username: str) -> User:
    async with factory() as s:
        user = await auth_repo.create_user(
            s, username=username, password_hash=_hasher.hash("pw"), role="user"
        )
        await s.commit()
        await s.refresh(user)
        return user


async def _seed_engagement(factory: async_sessionmaker[AsyncSession], owner_id: UUID) -> UUID:
    async with factory() as s:
        eng = await eng_repo.create_engagement(
            s, name="Eng", scope="https://example.com", client_info=None, owner_id=owner_id
        )
        await s.commit()
        await s.refresh(eng)
        return cast(UUID, eng.id)


async def _add_member(
    factory: async_sessionmaker[AsyncSession], eng_id: UUID, user_id: UUID
) -> None:
    async with factory() as s:
        await eng_repo.add_member(s, eng_id, user_id)
        await s.commit()


async def _gate_credential_command(
    factory: async_sessionmaker[AsyncSession], *, eng_id: UUID, initiator_id: UUID
) -> UUID:
    """Drive the real classify→gate path for a dangerous command; return the pending id."""
    async with factory() as s:
        with patch.object(service, "_resolve_tool_config", return_value=ToolConfig(weight="heavy")):
            result = await service.create_requests_for_turn(
                s,
                engagement_id=eng_id,
                chat_message_id=uuid4(),
                initiator_user_id=initiator_id,
                actions=[
                    ProposedAction(
                        server_name="shell-exec",
                        tool_name="hydra",
                        args={"cmd": "hydra -P rockyou.txt"},
                    )
                ],
            )
        await s.commit()
        assert len(result.gated) == 1
        return result.gated[0].id


async def _user(factory: async_sessionmaker[AsyncSession], username: str) -> UUID:
    return cast(UUID, (await _seed_user(factory, username)).id)


async def _count_audit(factory: async_sessionmaker[AsyncSession], action: str) -> int:
    async with factory() as s:
        return cast(
            int,
            (
                await s.execute(
                    select(func.count()).select_from(AuditEntry).where(AuditEntry.action == action)
                )
            ).scalar_one(),
        )


# --- Headline §5.2 + §14 + Resolved-decision-3 happy path ------------------------------


async def test_dangerous_command_gated_then_approved_executes(app_and_factory: AppFactory) -> None:
    _app, factory, exec_run = app_and_factory
    initiator = await _user(factory, "owner")
    eng_id = await _seed_engagement(factory, initiator)
    req_id = await _gate_credential_command(factory, eng_id=eng_id, initiator_id=initiator)

    # Gated: a pending row exists and NO tool run has happened yet.
    exec_run.assert_not_awaited()
    async with factory() as s:
        pending = await repo.get_request_for_engagement(s, engagement_id=eng_id, request_id=req_id)
        assert pending is not None and pending.status == "pending"

    # The initiator approves their own request.
    async with factory() as s:
        read = await service.decide(
            s,
            engagement_id=eng_id,
            request_id=req_id,
            requester=cast(User, SimpleNamespace(id=initiator, username="owner")),
            decision="approve",
        )
    assert read.status is ApprovalStatus.APPROVED
    assert read.self_approved is True
    # Exactly one approval_granted audit entry (attributed to the decider) and one run, on
    # the initiator's behalf.
    assert await _count_audit(factory, "approval_granted") == 1
    exec_run.assert_awaited_once()
    assert exec_run.await_args is not None
    assert exec_run.await_args.kwargs["user_id"] == initiator  # INITIATOR, not approver


# --- Risk 1: no double-execution -------------------------------------------------------


async def test_double_approve_runs_only_once(app_and_factory: AppFactory) -> None:
    _app, factory, exec_run = app_and_factory
    initiator = await _user(factory, "owner")
    eng_id = await _seed_engagement(factory, initiator)
    req_id = await _gate_credential_command(factory, eng_id=eng_id, initiator_id=initiator)
    requester = cast(User, SimpleNamespace(id=initiator, username="owner"))

    async with factory() as s:
        first = await service.decide(
            s, engagement_id=eng_id, request_id=req_id, requester=requester, decision="approve"
        )
        assert first.status is ApprovalStatus.APPROVED
        # A second decision on the now-terminal request claims nothing (Risk 1).
        with pytest.raises(service.AlreadyDecidedError):
            await service.decide(
                s, engagement_id=eng_id, request_id=req_id, requester=requester, decision="approve"
            )

    # Exactly one grant audit + exactly one run — never twice.
    assert await _count_audit(factory, "approval_granted") == 1
    exec_run.assert_awaited_once()


# --- §14: chain integrity preserved with approval entries woven in ---------------------


async def test_audit_chain_intact_after_approvals(app_and_factory: AppFactory) -> None:
    _app, factory, _exec = app_and_factory
    a = await _user(factory, "alice")
    eng_id = await _seed_engagement(factory, a)
    requester = cast(User, SimpleNamespace(id=a, username="alice"))

    # A mix of an approve and a reject (each appends a real chain entry).
    approve_id = await _gate_credential_command(factory, eng_id=eng_id, initiator_id=a)
    reject_id = await _gate_credential_command(factory, eng_id=eng_id, initiator_id=a)
    async with factory() as s:
        await service.decide(
            s, engagement_id=eng_id, request_id=approve_id, requester=requester, decision="approve"
        )
    async with factory() as s:
        await service.decide(
            s, engagement_id=eng_id, request_id=reject_id, requester=requester, decision="reject"
        )

    assert await _count_audit(factory, "approval_granted") == 1
    assert await _count_audit(factory, "approval_rejected") == 1
    async with factory() as s:
        ok, verified, broke = await audit_verify.verify(s)
    assert ok is True and broke is None
    assert verified >= 2


# --- §5.2 cross-member + Resolved decision 3 ------------------------------------------


async def test_cross_member_approval_attributed_to_approver_self_approved_false(
    app_and_factory: AppFactory,
) -> None:
    _app, factory, exec_run = app_and_factory
    initiator = await _user(factory, "owner")
    other = await _user(factory, "second")
    eng_id = await _seed_engagement(factory, initiator)
    await _add_member(factory, eng_id, other)
    req_id = await _gate_credential_command(factory, eng_id=eng_id, initiator_id=initiator)

    async with factory() as s:
        read = await service.decide(
            s,
            engagement_id=eng_id,
            request_id=req_id,
            requester=cast(User, SimpleNamespace(id=other, username="second")),
            decision="approve",
        )
    assert read.self_approved is False
    assert read.acted_by_username == "second"
    # The run is still attributed to the INITIATOR, not the cross-member approver.
    assert exec_run.await_args is not None
    assert exec_run.await_args.kwargs["user_id"] == initiator


# --- §5.2 rejection never executes -----------------------------------------------------


async def test_rejection_does_not_execute(app_and_factory: AppFactory) -> None:
    _app, factory, exec_run = app_and_factory
    initiator = await _user(factory, "owner")
    eng_id = await _seed_engagement(factory, initiator)
    req_id = await _gate_credential_command(factory, eng_id=eng_id, initiator_id=initiator)

    async with factory() as s:
        read = await service.decide(
            s,
            engagement_id=eng_id,
            request_id=req_id,
            requester=cast(User, SimpleNamespace(id=initiator, username="owner")),
            decision="reject",
        )
    assert read.status is ApprovalStatus.REJECTED
    assert await _count_audit(factory, "approval_rejected") == 1
    exec_run.assert_not_awaited()  # zero runs created on reject


# --- §17.1 isolation -------------------------------------------------------------------


async def test_non_member_cannot_act(app_and_factory: AppFactory) -> None:
    _app, factory, _exec = app_and_factory
    initiator = await _user(factory, "owner")
    outsider = await _user(factory, "outsider")
    eng_id = await _seed_engagement(factory, initiator)
    req_id = await _gate_credential_command(factory, eng_id=eng_id, initiator_id=initiator)

    async with factory() as s:
        with pytest.raises(NotFoundError):
            await service.decide(
                s,
                engagement_id=eng_id,
                request_id=req_id,
                requester=cast(User, SimpleNamespace(id=outsider, username="outsider")),
                decision="approve",
            )


# --- §5.2 autonomous / inverted default ------------------------------------------------


async def test_autonomous_command_creates_no_request(app_and_factory: AppFactory) -> None:
    _app, factory, _exec = app_and_factory
    initiator = await _user(factory, "owner")
    eng_id = await _seed_engagement(factory, initiator)
    async with factory() as s:
        with patch.object(
            service,
            "_resolve_tool_config",
            return_value=ToolConfig(weight="light", capability_flags=("network",)),
        ):
            result = await service.create_requests_for_turn(
                s,
                engagement_id=eng_id,
                chat_message_id=uuid4(),
                initiator_user_id=initiator,
                actions=[ProposedAction(server_name="httpx", tool_name="httpx", args={})],
            )
        await s.commit()
    assert result.gated == []
    assert len(result.autonomous) == 1
    async with factory() as s:
        rows, _ = await repo.list_for_engagement(s, engagement_id=eng_id)
    assert rows == []  # no approval_requests row for an autonomous command


# --- Resolved decision 2: escape hatch gates an unclassified manifest ------------------


async def test_unclassified_manifest_command_is_gated(app_and_factory: AppFactory) -> None:
    _app, factory, _exec = app_and_factory
    initiator = await _user(factory, "owner")
    eng_id = await _seed_engagement(factory, initiator)
    async with factory() as s:
        with patch.object(service, "_resolve_tool_config", return_value=ToolConfig(weight=None)):
            result = await service.create_requests_for_turn(
                s,
                engagement_id=eng_id,
                chat_message_id=uuid4(),
                initiator_user_id=initiator,
                actions=[ProposedAction(server_name="mystery", tool_name="thing", args={})],
            )
        await s.commit()
    assert len(result.gated) == 1
    assert ApprovalReason.UNCLASSIFIED_MANIFEST in result.gated[0].reasons
