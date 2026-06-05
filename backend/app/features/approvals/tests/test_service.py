"""Unit tests for the approvals service (Slice 16 task 5).

mcp ``execute_tool_run`` + audit ``record`` + membership + username lookup are mocked;
the ``approval_requests`` rows are real (SQLite). The load-bearing assertions are the
attribution split (decider gets the approval audit; initiator gets the run — Resolved
decision 3), the atomic audit emission, archived/already-decided 409s, and no-redaction.
"""

from collections.abc import Iterator
from contextlib import AbstractContextManager
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.features.approvals import repository as repo
from app.features.approvals import service
from app.features.approvals.classifier import ToolConfig
from app.features.approvals.schemas import ApprovalReason, ApprovalStatus, ProposedAction
from app.features.audit.schemas import AuditAction
from app.features.auth.models import User


def _user(username: str = "alice", user_id: UUID | None = None) -> User:
    return cast(User, SimpleNamespace(id=user_id or uuid4(), username=username))


def _action(server: str = "shell-exec", tool: str = "run", **kw: object) -> ProposedAction:
    return ProposedAction(
        server_name=server,
        tool_name=tool,
        args=cast(dict, kw.get("args", {"cmd": "whoami"})),
        preset_name=cast("str | None", kw.get("preset")),
        rationale=cast("str | None", kw.get("rationale", "because")),
    )


@pytest.fixture
def member_active() -> Iterator[AsyncMock]:
    """Membership chokepoint returns an active engagement + member pair by default."""
    with patch.object(service.eng_repo, "get_engagement_for_member", new_callable=AsyncMock) as m:
        m.return_value = (SimpleNamespace(status="active"), SimpleNamespace())
        yield m


@pytest.fixture
def exec_run() -> Iterator[AsyncMock]:
    with patch.object(service.mcp_service, "execute_tool_run", new_callable=AsyncMock) as m:
        m.return_value = SimpleNamespace(tool_run_id=uuid4())
        yield m


@pytest.fixture
def audit_record() -> Iterator[AsyncMock]:
    with patch.object(service.audit_service, "record", new_callable=AsyncMock) as m:
        yield m


@pytest.fixture
def resolve_username() -> Iterator[AsyncMock]:
    with patch.object(service.auth_repo, "get_user_by_id", new_callable=AsyncMock) as m:
        m.return_value = SimpleNamespace(username="decider-name")
        yield m


@pytest.fixture
def eng_with_scope() -> Iterator[AsyncMock]:
    """Patch the per-turn engagement load create_requests_for_turn does for scope.

    Defaults to an engagement with **no** declared scope (so scope never fires unless a
    test sets a scope); individual tests override ``return_value`` with a scope string.
    """
    with patch.object(service.eng_repo, "get_engagement_for_member", new_callable=AsyncMock) as m:
        m.return_value = (SimpleNamespace(scope=""), SimpleNamespace())
        yield m


def _eng(scope: str) -> tuple[SimpleNamespace, SimpleNamespace]:
    return (SimpleNamespace(scope=scope), SimpleNamespace())


def _patch_tool_config(cfg: ToolConfig | None) -> AbstractContextManager[AsyncMock]:
    return patch.object(service, "_resolve_tool_config", return_value=cfg)


def _await_kwargs(mock: AsyncMock) -> dict[str, object]:
    """Return the kwargs of a mock's awaited call (asserting it was awaited)."""
    assert mock.await_args is not None
    return dict(mock.await_args.kwargs)


# --- create_requests_for_turn ---------------------------------------------------------


async def test_autonomous_action_returns_no_request(
    db_session: AsyncSession, eng_with_scope: AsyncMock
) -> None:
    with _patch_tool_config(ToolConfig(weight="light", capability_flags=("network",))):
        result = await service.create_requests_for_turn(
            db_session,
            engagement_id=uuid4(),
            chat_message_id=uuid4(),
            initiator_user_id=uuid4(),
            actions=[_action(tool="httpx")],
        )
    assert len(result.autonomous) == 1
    assert result.gated == []


async def test_gated_action_creates_pending_request(
    db_session: AsyncSession, eng_with_scope: AsyncMock, resolve_username: AsyncMock
) -> None:
    eng = uuid4()
    with _patch_tool_config(ToolConfig(weight="heavy")):
        result = await service.create_requests_for_turn(
            db_session,
            engagement_id=eng,
            chat_message_id=uuid4(),
            initiator_user_id=uuid4(),
            actions=[_action(tool="nmap")],
        )
    assert result.autonomous == []
    assert len(result.gated) == 1
    assert result.gated[0].status is ApprovalStatus.PENDING
    rows, _ = await repo.list_for_engagement(db_session, engagement_id=eng)
    assert len(rows) == 1


async def test_unknown_tool_action_dropped(
    db_session: AsyncSession, eng_with_scope: AsyncMock
) -> None:
    with _patch_tool_config(None):
        result = await service.create_requests_for_turn(
            db_session,
            engagement_id=uuid4(),
            chat_message_id=uuid4(),
            initiator_user_id=uuid4(),
            actions=[_action(server="ghost", tool="nope")],
        )
    assert result.autonomous == [] and result.gated == []


async def test_unclassified_manifest_action_gated(
    db_session: AsyncSession, eng_with_scope: AsyncMock, resolve_username: AsyncMock
) -> None:
    with _patch_tool_config(ToolConfig(weight=None)):
        result = await service.create_requests_for_turn(
            db_session,
            engagement_id=uuid4(),
            chat_message_id=uuid4(),
            initiator_user_id=uuid4(),
            actions=[_action(tool="weird")],
        )
    assert len(result.gated) == 1
    assert ApprovalReason.UNCLASSIFIED_MANIFEST in result.gated[0].reasons


# --- create_requests_for_turn: scope arm (Slice 17 task 5) ----------------------------


def _target_action(
    target: str, *, server: str = "httpx-server", tool: str = "httpx", **kw: object
) -> ProposedAction:
    return _action(server=server, tool=tool, args={"target": target}, **kw)


async def test_out_of_scope_autonomous_command_is_gated_with_out_of_scope_reason(
    db_session: AsyncSession, eng_with_scope: AsyncMock
) -> None:
    eng_with_scope.return_value = _eng("juice-shop")
    with _patch_tool_config(ToolConfig(weight="light", capability_flags=("network",))):
        result = await service.create_requests_for_turn(
            db_session,
            engagement_id=uuid4(),
            chat_message_id=uuid4(),
            initiator_user_id=uuid4(),
            actions=[_target_action("http://example.com")],
        )
    assert result.autonomous == []
    assert len(result.gated) == 1
    assert ApprovalReason.OUT_OF_SCOPE in result.gated[0].reasons


async def test_in_scope_autonomous_command_still_runs(
    db_session: AsyncSession, eng_with_scope: AsyncMock
) -> None:
    eng_with_scope.return_value = _eng("juice-shop, example.com")
    with _patch_tool_config(ToolConfig(weight="light", capability_flags=("network",))):
        result = await service.create_requests_for_turn(
            db_session,
            engagement_id=uuid4(),
            chat_message_id=uuid4(),
            initiator_user_id=uuid4(),
            actions=[_target_action("http://example.com")],
        )
    assert len(result.autonomous) == 1
    assert result.gated == []


async def test_out_of_scope_context_persisted_on_request(
    db_session: AsyncSession, eng_with_scope: AsyncMock
) -> None:
    eng = uuid4()
    eng_with_scope.return_value = _eng("juice-shop, 10.0.0.0/24")
    with _patch_tool_config(ToolConfig(weight="light", capability_flags=("network",))):
        result = await service.create_requests_for_turn(
            db_session,
            engagement_id=eng,
            chat_message_id=uuid4(),
            initiator_user_id=uuid4(),
            actions=[_target_action("http://example.com:8080/admin")],
        )
    gated = result.gated[0]
    assert gated.out_of_scope_host == "example.com"  # port/path stripped (parse_host)
    assert gated.scope_checked_against == "juice-shop, 10.0.0.0/24"
    # Persisted on the row, not just on the read object.
    rows, _ = await repo.list_for_engagement(db_session, engagement_id=eng)
    assert rows[0].out_of_scope_host == "example.com"
    assert rows[0].scope_checked_against == "juice-shop, 10.0.0.0/24"


async def test_dangerous_and_out_of_scope_combines_reasons(
    db_session: AsyncSession, eng_with_scope: AsyncMock
) -> None:
    eng_with_scope.return_value = _eng("juice-shop")
    with _patch_tool_config(ToolConfig(weight="heavy", capability_flags=("network",))):
        result = await service.create_requests_for_turn(
            db_session,
            engagement_id=uuid4(),
            chat_message_id=uuid4(),
            initiator_user_id=uuid4(),
            actions=[_target_action("http://example.com", server="nmap-server", tool="nmap")],
        )
    reasons = set(result.gated[0].reasons)
    assert ApprovalReason.AGGRESSIVE_SCAN in reasons
    assert ApprovalReason.OUT_OF_SCOPE in reasons


async def test_empty_engagement_scope_never_gates_on_scope(
    db_session: AsyncSession, eng_with_scope: AsyncMock
) -> None:
    eng_with_scope.return_value = _eng("")  # blank scope ⇒ never out-of-scope (soft)
    with _patch_tool_config(ToolConfig(weight="light", capability_flags=("network",))):
        result = await service.create_requests_for_turn(
            db_session,
            engagement_id=uuid4(),
            chat_message_id=uuid4(),
            initiator_user_id=uuid4(),
            actions=[_target_action("http://anywhere.example")],
        )
    assert len(result.autonomous) == 1
    assert result.gated == []


async def test_targetless_command_not_gated_for_scope(
    db_session: AsyncSession, eng_with_scope: AsyncMock, resolve_username: AsyncMock
) -> None:
    # A run_command with no target arg has no host to test: it still gates on its
    # Slice-16 shell-exec → target_write reason, but NOT on scope.
    eng_with_scope.return_value = _eng("juice-shop")
    with _patch_tool_config(ToolConfig(weight="light", capability_flags=("shell-exec",))):
        result = await service.create_requests_for_turn(
            db_session,
            engagement_id=uuid4(),
            chat_message_id=uuid4(),
            initiator_user_id=uuid4(),
            actions=[_action(server="shell-exec", tool="run", args={"cmd": "id"})],
        )
    gated = result.gated[0]
    assert ApprovalReason.TARGET_WRITE in gated.reasons
    assert ApprovalReason.OUT_OF_SCOPE not in gated.reasons
    assert gated.out_of_scope_host is None


async def test_scope_parsed_once_per_turn(
    db_session: AsyncSession, eng_with_scope: AsyncMock
) -> None:
    eng_with_scope.return_value = _eng("juice-shop")
    with (
        _patch_tool_config(ToolConfig(weight="light", capability_flags=("network",))),
        patch.object(service, "parse_scope", wraps=service.parse_scope) as spy,
    ):
        await service.create_requests_for_turn(
            db_session,
            engagement_id=uuid4(),
            chat_message_id=uuid4(),
            initiator_user_id=uuid4(),
            actions=[_target_action("http://juice-shop"), _target_action("http://juice-shop")],
        )
    spy.assert_called_once()


# --- list_requests --------------------------------------------------------------------


async def test_list_non_member_404(db_session: AsyncSession, member_active: AsyncMock) -> None:
    member_active.return_value = None
    with pytest.raises(NotFoundError):
        await service.list_requests(db_session, engagement_id=uuid4(), requester=_user())


# --- decide: approve attribution split ------------------------------------------------


async def _pending(db: AsyncSession, *, engagement_id: UUID, initiator_id: UUID) -> UUID:
    row = await repo.create_request(
        db,
        engagement_id=engagement_id,
        chat_message_id=uuid4(),
        initiator_user_id=initiator_id,
        server_name="shell-exec",
        tool_name="run",
        args={"cmd": "hydra -P rockyou.txt ssh://10.0.0.5"},
        reasons=["credential_attack"],
    )
    await db.commit()
    return cast(UUID, row.id)


async def test_approve_audit_attributed_to_decider_self_approved_true_for_initiator(
    db_session: AsyncSession,
    member_active: AsyncMock,
    exec_run: AsyncMock,
    audit_record: AsyncMock,
    resolve_username: AsyncMock,
) -> None:
    eng, initiator = uuid4(), uuid4()
    req_id = await _pending(db_session, engagement_id=eng, initiator_id=initiator)
    me = _user(user_id=initiator)  # initiator approves their own request

    read = await service.decide(
        db_session, engagement_id=eng, request_id=req_id, requester=me, decision="approve"
    )

    assert read.status is ApprovalStatus.APPROVED
    audit_record.assert_awaited_once()
    kwargs = _await_kwargs(audit_record)
    assert kwargs["action"] is AuditAction.APPROVAL_GRANTED
    assert kwargs["actor_user_id"] == initiator  # the DECIDER
    assert kwargs["self_approved"] is True
    assert kwargs["target_type"] == "approval_request"


async def test_approve_audit_attributed_to_decider_self_approved_false_for_other_member(
    db_session: AsyncSession,
    member_active: AsyncMock,
    exec_run: AsyncMock,
    audit_record: AsyncMock,
    resolve_username: AsyncMock,
) -> None:
    eng, initiator = uuid4(), uuid4()
    req_id = await _pending(db_session, engagement_id=eng, initiator_id=initiator)
    other = _user(user_id=uuid4())  # a different member approves

    await service.decide(
        db_session, engagement_id=eng, request_id=req_id, requester=other, decision="approve"
    )
    kwargs = _await_kwargs(audit_record)
    assert kwargs["actor_user_id"] == _user_id_of(other)
    assert kwargs["self_approved"] is False


def _user_id_of(user: User) -> UUID:
    return cast(UUID, user.id)


async def test_approve_executes_tool_run_attributed_to_initiator(
    db_session: AsyncSession,
    member_active: AsyncMock,
    exec_run: AsyncMock,
    audit_record: AsyncMock,
    resolve_username: AsyncMock,
) -> None:
    eng, initiator = uuid4(), uuid4()
    req_id = await _pending(db_session, engagement_id=eng, initiator_id=initiator)
    other = _user(user_id=uuid4())

    read = await service.decide(
        db_session, engagement_id=eng, request_id=req_id, requester=other, decision="approve"
    )
    exec_run.assert_awaited_once()
    exec_kwargs = _await_kwargs(exec_run)
    assert exec_kwargs["user_id"] == initiator  # the INITIATOR, not the approver
    assert exec_kwargs["async_mode"] is True
    assert read.tool_run_id == exec_run.return_value.tool_run_id


async def test_reject_records_audit_and_does_not_execute(
    db_session: AsyncSession,
    member_active: AsyncMock,
    exec_run: AsyncMock,
    audit_record: AsyncMock,
    resolve_username: AsyncMock,
) -> None:
    eng, initiator = uuid4(), uuid4()
    req_id = await _pending(db_session, engagement_id=eng, initiator_id=initiator)

    read = await service.decide(
        db_session,
        engagement_id=eng,
        request_id=req_id,
        requester=_user(user_id=initiator),
        decision="reject",
    )
    assert read.status is ApprovalStatus.REJECTED
    assert _await_kwargs(audit_record)["action"] is AuditAction.APPROVAL_REJECTED
    exec_run.assert_not_awaited()  # the command is NEVER executed on reject


async def test_decide_on_terminal_409(
    db_session: AsyncSession,
    member_active: AsyncMock,
    exec_run: AsyncMock,
    audit_record: AsyncMock,
    resolve_username: AsyncMock,
) -> None:
    eng, initiator = uuid4(), uuid4()
    req_id = await _pending(db_session, engagement_id=eng, initiator_id=initiator)
    # Pre-decide the request out-of-band so it is already terminal.
    await repo.decide_request(
        db_session,
        request_id=req_id,
        status="approved",
        acted_by_user_id=uuid4(),
        self_approved=False,
    )
    await db_session.commit()

    with pytest.raises(service.AlreadyDecidedError) as exc:
        await service.decide(
            db_session,
            engagement_id=eng,
            request_id=req_id,
            requester=_user(user_id=initiator),
            decision="approve",
        )
    assert exc.value.status == "approved"
    audit_record.assert_not_awaited()
    exec_run.assert_not_awaited()


async def test_approve_archived_engagement_409(
    db_session: AsyncSession,
    member_active: AsyncMock,
    exec_run: AsyncMock,
    audit_record: AsyncMock,
) -> None:
    member_active.return_value = (SimpleNamespace(status="archived"), SimpleNamespace())
    eng, initiator = uuid4(), uuid4()
    req_id = await _pending(db_session, engagement_id=eng, initiator_id=initiator)

    with pytest.raises(service.EngagementArchivedError):
        await service.decide(
            db_session,
            engagement_id=eng,
            request_id=req_id,
            requester=_user(user_id=initiator),
            decision="approve",
        )
    exec_run.assert_not_awaited()


async def test_args_not_redacted(
    db_session: AsyncSession,
    member_active: AsyncMock,
    exec_run: AsyncMock,
    audit_record: AsyncMock,
    resolve_username: AsyncMock,
) -> None:
    secret_args = {"cmd": "hydra -l admin -p Sup3rSecret! ssh://10.0.0.5"}
    eng, initiator = uuid4(), uuid4()
    row = await repo.create_request(
        db_session,
        engagement_id=eng,
        chat_message_id=uuid4(),
        initiator_user_id=initiator,
        server_name="shell-exec",
        tool_name="run",
        args=secret_args,
        reasons=["credential_attack"],
    )
    await db_session.commit()

    read = await service.decide(
        db_session,
        engagement_id=eng,
        request_id=cast(UUID, row.id),
        requester=_user(user_id=initiator),
        decision="approve",
    )
    # Verbatim everywhere: the read schema, the persisted row, and the run handoff (§5.5).
    assert read.args == secret_args
    assert _await_kwargs(exec_run)["args"] == secret_args


async def test_decide_lost_race_raises_already_decided(
    db_session: AsyncSession,
    member_active: AsyncMock,
    exec_run: AsyncMock,
    audit_record: AsyncMock,
    resolve_username: AsyncMock,
) -> None:
    # W2 (Slice 16): the request looked pending at load, but the guarded UPDATE claimed
    # nothing (another decider won between our load and our claim) — decide() must raise
    # AlreadyDecidedError from the claimed-is-None branch and NOT execute the command.
    eng, initiator = uuid4(), uuid4()
    req_id = await _pending(db_session, engagement_id=eng, initiator_id=initiator)
    with patch.object(service.repo, "decide_request", new_callable=AsyncMock) as decide_mock:
        decide_mock.return_value = None  # lost the race
        with pytest.raises(service.AlreadyDecidedError):
            await service.decide(
                db_session,
                engagement_id=eng,
                request_id=req_id,
                requester=_user(user_id=initiator),
                decision="approve",
            )
    exec_run.assert_not_awaited()
    audit_record.assert_not_awaited()
