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
from app.features.autonomy import service as autonomy_service
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


# --- §5.2 soft scope (Slice 17) -------------------------------------------------------


async def _seed_engagement_with_scope(
    factory: async_sessionmaker[AsyncSession], owner_id: UUID, scope: str
) -> UUID:
    async with factory() as s:
        eng = await eng_repo.create_engagement(
            s, name="Eng", scope=scope, client_info=None, owner_id=owner_id
        )
        await s.commit()
        await s.refresh(eng)
        return cast(UUID, eng.id)


async def _propose_against(
    factory: async_sessionmaker[AsyncSession],
    *,
    eng_id: UUID,
    initiator_id: UUID,
    target: str,
    weight: str = "light",
    server: str = "httpx-server",
    tool: str = "httpx",
) -> service.ClassifiedTurnResult:
    """Drive the real scope-aware classify→gate path for an httpx-style command.

    Auto-commits inside the helper (mirrors ``_gate_credential_command`` above) and returns
    the already-serialised ``ClassifiedTurnResult`` so the caller decides in a fresh session.
    """
    async with factory() as s:
        with patch.object(
            service,
            "_resolve_tool_config",
            return_value=ToolConfig(weight=weight, capability_flags=("network",)),
        ):
            result = await service.create_requests_for_turn(
                s,
                engagement_id=eng_id,
                chat_message_id=uuid4(),
                initiator_user_id=initiator_id,
                actions=[
                    ProposedAction(server_name=server, tool_name=tool, args={"target": target})
                ],
            )
        await s.commit()
        return result


async def test_out_of_scope_command_gated_then_approved_executes(
    app_and_factory: AppFactory,
) -> None:
    # Headline §5.2-soft + §14 happy path: an otherwise-autonomous httpx against an
    # out-of-scope target gates, the initiator approves, and the command then runs.
    _app, factory, exec_run = app_and_factory
    initiator = await _user(factory, "owner")
    eng_id = await _seed_engagement_with_scope(factory, initiator, "juice-shop")

    result = await _propose_against(
        factory, eng_id=eng_id, initiator_id=initiator, target="http://example.com"
    )
    assert len(result.gated) == 1
    req = result.gated[0]
    assert ApprovalReason.OUT_OF_SCOPE in req.reasons
    assert req.out_of_scope_host == "example.com"
    assert req.scope_checked_against == "juice-shop"
    exec_run.assert_not_awaited()  # nothing has run yet (§5.2 soft — warn + confirm)

    async with factory() as s:
        read = await service.decide(
            s,
            engagement_id=eng_id,
            request_id=req.id,
            requester=cast(User, SimpleNamespace(id=initiator, username="owner")),
            decision="approve",
        )
    assert read.status is ApprovalStatus.APPROVED
    assert read.self_approved is True
    exec_run.assert_awaited_once()
    assert await _count_audit(factory, "approval_granted") == 1
    # The approval audit payload carries the out_of_scope reason (§14, rides existing payload).
    async with factory() as s:
        entry = (
            await s.execute(select(AuditEntry).where(AuditEntry.action == "approval_granted"))
        ).scalar_one()
        assert "out_of_scope" in entry.payload["reasons"]


async def test_in_scope_autonomous_command_runs_without_request(
    app_and_factory: AppFactory,
) -> None:
    _app, factory, _exec = app_and_factory
    initiator = await _user(factory, "owner")
    eng_id = await _seed_engagement_with_scope(factory, initiator, "juice-shop")

    result = await _propose_against(
        factory, eng_id=eng_id, initiator_id=initiator, target="http://juice-shop:3000"
    )
    assert result.gated == []
    assert len(result.autonomous) == 1  # in-scope safe httpx stays autonomous
    async with factory() as s:
        rows, _ = await repo.list_for_engagement(s, engagement_id=eng_id)
    assert rows == []


async def test_out_of_scope_combines_with_dangerous_reason(app_and_factory: AppFactory) -> None:
    _app, factory, _exec = app_and_factory
    initiator = await _user(factory, "owner")
    eng_id = await _seed_engagement_with_scope(factory, initiator, "juice-shop")

    result = await _propose_against(
        factory,
        eng_id=eng_id,
        initiator_id=initiator,
        target="http://example.com",
        weight="heavy",  # aggressive_scan
        server="nmap-server",
        tool="nmap",
    )
    reasons = set(result.gated[0].reasons)
    assert ApprovalReason.AGGRESSIVE_SCAN in reasons
    assert ApprovalReason.OUT_OF_SCOPE in reasons


async def test_empty_scope_does_not_gate_on_scope(app_and_factory: AppFactory) -> None:
    _app, factory, _exec = app_and_factory
    initiator = await _user(factory, "owner")
    eng_id = await _seed_engagement_with_scope(factory, initiator, "   ")  # blank scope

    result = await _propose_against(
        factory, eng_id=eng_id, initiator_id=initiator, target="http://anywhere.example"
    )
    assert result.gated == []
    assert len(result.autonomous) == 1


async def test_audit_chain_intact_after_out_of_scope_decisions(
    app_and_factory: AppFactory,
) -> None:
    _app, factory, _exec = app_and_factory
    a = await _user(factory, "alice")
    eng_id = await _seed_engagement_with_scope(factory, a, "juice-shop")
    requester = cast(User, SimpleNamespace(id=a, username="alice"))

    approve = await _propose_against(
        factory, eng_id=eng_id, initiator_id=a, target="http://example.com"
    )
    reject = await _propose_against(
        factory, eng_id=eng_id, initiator_id=a, target="http://other.example"
    )
    async with factory() as s:
        await service.decide(
            s,
            engagement_id=eng_id,
            request_id=approve.gated[0].id,
            requester=requester,
            decision="approve",
        )
    async with factory() as s:
        await service.decide(
            s,
            engagement_id=eng_id,
            request_id=reject.gated[0].id,
            requester=requester,
            decision="reject",
        )

    async with factory() as s:
        ok, verified, broke = await audit_verify.verify(s)
    assert ok is True and broke is None
    assert verified >= 2


# --- Slice 18: standing autonomy — grant → auto-approve → revoke → gate -----------------


async def _propose_aggressive(
    factory: async_sessionmaker[AsyncSession], *, eng_id: UUID, initiator_id: UUID
) -> service.ClassifiedTurnResult:
    """Drive the real classify→gate path for an aggressive_scan (heavy) command."""
    async with factory() as s:
        with patch.object(service, "_resolve_tool_config", return_value=ToolConfig(weight="heavy")):
            result = await service.create_requests_for_turn(
                s,
                engagement_id=eng_id,
                chat_message_id=uuid4(),
                initiator_user_id=initiator_id,
                actions=[
                    ProposedAction(
                        server_name="nmap", tool_name="run_nmap", args={"target": "juice-shop"}
                    )
                ],
            )
        await s.commit()
        return result


async def test_standing_autonomy_auto_approves_then_revoke_regates(
    app_and_factory: AppFactory,
) -> None:
    """The headline Slice-18 cross-feature property: a grant makes a fully-covered gated
    command auto-approve (audited, no human row) on the next turn, and a revoke restores the
    human gate on the turn after. The auto-approved command's *execution* is the chat
    service's job (proven in chat's unit tests); here we prove the approvals short-circuit,
    the audit trail, and that revoke is effective immediately — all on one intact chain (§14).
    """
    _app, factory, _exec = app_and_factory
    initiator = await _user(factory, "owner")
    # Scope matches the proposed target so aggressive_scan is the command's SOLE reason
    # (an out-of-scope target would add OUT_OF_SCOPE and AND-coverage would keep it gated).
    eng_id = await _seed_engagement_with_scope(factory, initiator, "juice-shop")
    requester = cast(User, SimpleNamespace(id=initiator, username="owner"))

    # Baseline — no grant: an aggressive_scan gates (a human card, no auto-approve).
    baseline = await _propose_aggressive(factory, eng_id=eng_id, initiator_id=initiator)
    assert len(baseline.gated) == 1
    assert baseline.auto_approved == []

    # Grant standing autonomy for aggressive_scan via the real service (emits autonomy_granted).
    async with factory() as s:
        g = await autonomy_service.grant(
            s, engagement_id=eng_id, requester=requester, reason=ApprovalReason.AGGRESSIVE_SCAN
        )
    assert await _count_audit(factory, "autonomy_granted") == 1

    # Next aggressive_scan auto-approves: NO new gated row, the action is returned to run, and
    # exactly one approval_auto_granted audit entry is woven in.
    covered = await _propose_aggressive(factory, eng_id=eng_id, initiator_id=initiator)
    assert covered.gated == []
    assert len(covered.auto_approved) == 1
    assert await _count_audit(factory, "approval_auto_granted") == 1
    # §14: the audit entry is traceable to the COMMAND (reasons) and to the GRANT (its id).
    async with factory() as s:
        entry = (
            await s.execute(select(AuditEntry).where(AuditEntry.action == "approval_auto_granted"))
        ).scalar_one()
    assert entry.payload["reasons"] == ["aggressive_scan"]
    assert entry.payload["covered_by_grants"] == [str(g.id)]
    # Only the baseline pending row exists — the auto-approved command created none.
    async with factory() as s:
        rows, _ = await repo.list_for_engagement(s, engagement_id=eng_id)
    assert len(rows) == 1

    # Revoke (emits autonomy_revoked) → the very next aggressive_scan gates again.
    async with factory() as s:
        await autonomy_service.revoke(
            s, engagement_id=eng_id, grant_id=cast(UUID, g.id), requester=requester
        )
    assert await _count_audit(factory, "autonomy_revoked") == 1

    after = await _propose_aggressive(factory, eng_id=eng_id, initiator_id=initiator)
    assert len(after.gated) == 1
    assert after.auto_approved == []
    # approval_auto_granted did NOT fire a second time (the revoke re-gated the command).
    assert await _count_audit(factory, "approval_auto_granted") == 1

    # The whole grant→auto→revoke→gate flow leaves the hash chain intact (§14).
    async with factory() as s:
        ok, _verified, broke = await audit_verify.verify(s)
    assert ok is True and broke is None


async def test_partial_cover_still_gates_under_grant(app_and_factory: AppFactory) -> None:
    """AND-coverage (Risk 1): a command carrying a second, un-granted reason still gates even
    though one of its reasons is delegated — a delegated category can't smuggle another."""
    _app, factory, _exec = app_and_factory
    initiator = await _user(factory, "owner")
    # Scope set so an out-of-scope target adds the OUT_OF_SCOPE reason on top of aggressive_scan.
    async with factory() as s:
        eng = await eng_repo.create_engagement(
            s, name="Eng", scope="juice-shop", client_info=None, owner_id=initiator
        )
        await s.commit()
        await s.refresh(eng)
        eng_id = cast(UUID, eng.id)
    requester = cast(User, SimpleNamespace(id=initiator, username="owner"))

    async with factory() as s:
        await autonomy_service.grant(
            s, engagement_id=eng_id, requester=requester, reason=ApprovalReason.AGGRESSIVE_SCAN
        )

    async with factory() as s:
        with patch.object(service, "_resolve_tool_config", return_value=ToolConfig(weight="heavy")):
            result = await service.create_requests_for_turn(
                s,
                engagement_id=eng_id,
                chat_message_id=uuid4(),
                initiator_user_id=initiator,
                actions=[
                    ProposedAction(
                        server_name="nmap",
                        tool_name="run_nmap",
                        args={"target": "http://evil.example"},
                    )
                ],
            )
        await s.commit()
    # aggressive_scan is granted but out_of_scope is not → the command still gates.
    assert result.auto_approved == []
    assert len(result.gated) == 1
    reasons = set(result.gated[0].reasons)
    assert ApprovalReason.AGGRESSIVE_SCAN in reasons
    assert ApprovalReason.OUT_OF_SCOPE in reasons
