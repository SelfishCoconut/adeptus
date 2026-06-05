"""Business logic for the approvals feature (Slice 16).

Three entry points:

* :func:`create_requests_for_turn` — classify each parsed+validated proposed action;
  AUTONOMOUS actions are returned for the chat service to execute immediately (no row),
  gated actions create a ``pending`` ``approval_requests`` row. Unknown server/tool
  actions are dropped (§17.1 — no hallucinated-tool execution).
* :func:`list_requests` — the engagement-shared queue for the Approvals tab; membership
  chokepoint (404 for non-members/missing, §17.1).
* :func:`decide` — approve/reject. Membership chokepoint, archived guard on approve (§4),
  the **guarded** decision transition (Risk 1), the ``approval_granted`` /
  ``approval_rejected`` audit entry written **atomically** with the transition and
  attributed to the **decider** with ``self_approved`` (§5.2/§14, Resolved decision 3),
  and — only inside the winning approve branch — the run is handed to the existing
  tool-run pipeline attributed to the **initiator**.

Services raise domain exceptions; the router translates them (NotFoundError→404; the two
409s carry an ``ApprovalConflict`` body). The caller (router/chat streamer) owns the
commit — except that ``mcp.service.execute_tool_run(async_mode=True)`` commits internally,
so on approve the claim + audit are flushed first and commit atomically with the run row.
"""

from __future__ import annotations

import base64
import logging
from typing import Literal, cast
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.features.approvals import repository as repo
from app.features.approvals.classifier import ToolConfig, classify
from app.features.approvals.models import ApprovalRequest
from app.features.approvals.schemas import (
    ApprovalRequestPage,
    ApprovalRequestRead,
    ApprovalStatus,
    ApprovalTier,
    ProposedAction,
)
from app.features.audit import service as audit_service
from app.features.audit.schemas import AuditAction
from app.features.auth import repository as auth_repo
from app.features.auth.models import User
from app.features.engagements import repository as eng_repo
from app.features.mcp import service as mcp_service
from app.features.mcp.registry import ConfigError, get_registry

logger = logging.getLogger(__name__)

# The dangerous command, once approved, enters the existing concurrency model unchanged
# (§6.2). A fixed default budget mirrors the manual tool-run default (Slice 04/05).
DEFAULT_APPROVAL_TIMEOUT_SECONDS = 30


# ---------------------------------------------------------------------------
# Domain exceptions (the two 409s — translated inline with an ApprovalConflict body)
# ---------------------------------------------------------------------------


class EngagementArchivedError(ConflictError):
    """Approve-and-run blocked in an archived engagement (§4 read-only). 409."""

    def __init__(self, message: str = "Engagement is archived (read-only)") -> None:
        super().__init__(message)


class AlreadyDecidedError(ConflictError):
    """A second decision on a terminal request (double-decision guard, Risk 1). 409.

    Carries the request's current terminal ``status`` for the ``ApprovalConflict`` body.
    """

    def __init__(self, *, status: str, message: str = "Approval request already decided") -> None:
        self.status = status
        super().__init__(message)


# ---------------------------------------------------------------------------
# Value object: the per-turn classification result the chat streamer consumes
# ---------------------------------------------------------------------------


class ClassifiedTurnResult(BaseModel):
    """What one assistant turn's proposed actions resolved to.

    ``autonomous`` runs immediately (the chat service executes each via the tool-run
    pipeline — no DB row); ``gated`` are the persisted ``pending`` requests (rendered as
    inline approval cards).
    """

    autonomous: list[ProposedAction]
    gated: list[ApprovalRequestRead]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user_id(user: User) -> UUID:
    return cast(UUID, user.id)


def _resolve_tool_config(server_name: str, tool_name: str) -> ToolConfig | None:
    """Resolve a proposed action's manifest classification from the live MCP registry.

    Returns ``None`` when the server or tool is unknown (the action is then dropped —
    §17.1, no hallucinated-tool execution).
    """
    try:
        registry = get_registry()
    except ConfigError:
        return None
    server = registry.get(server_name)
    if server is None:
        return None
    tool = next((t for t in server.tools if t.name == tool_name), None)
    if tool is None:
        return None
    return ToolConfig(weight=tool.weight, capability_flags=tuple(tool.capability_flags))


def _encode_cursor(cursor: tuple[object, object] | None) -> str | None:
    """Encode a ``(created_at, id)`` keyset cursor as an opaque base64url string."""
    if cursor is None:
        return None
    created_at, row_id = cursor
    raw = f"{created_at!s}|{row_id!s}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str | None) -> tuple[str, str] | None:
    """Decode an opaque cursor to ``(created_at_iso, id_str)``; None passes through.

    A malformed cursor is a client error → ``BadRequestError`` (400).
    """
    if cursor is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        created_at, row_id = raw.split("|", 1)
        return created_at, row_id
    except (ValueError, UnicodeDecodeError) as exc:
        raise BadRequestError("Malformed cursor") from exc


async def _to_read(
    db: AsyncSession,
    row: ApprovalRequest,
    *,
    username_cache: dict[UUID, str | None] | None = None,
) -> ApprovalRequestRead:
    """Build the read schema, resolving ``acted_by_username`` for decided rows.

    The username is a read-time convenience (§5.2 "Approved by @user"); the audit log
    is still the attribution source of truth. A small per-call cache avoids re-fetching
    the same decider across a page.
    """
    read = ApprovalRequestRead.model_validate(row)
    acted_by = row.acted_by_user_id
    if acted_by is not None:
        cache = username_cache if username_cache is not None else {}
        key = cast(UUID, acted_by)
        if key not in cache:
            user = await auth_repo.get_user_by_id(db, key)
            cache[key] = user.username if user is not None else None
        read = read.model_copy(update={"acted_by_username": cache[key]})
    return read


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


async def create_requests_for_turn(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    chat_message_id: UUID,
    initiator_user_id: UUID,
    actions: list[ProposedAction],
) -> ClassifiedTurnResult:
    """Classify a turn's proposed actions into autonomous vs gated.

    Each action is resolved against the live MCP config and classified (§5.2). Unknown
    server/tool actions are dropped (§17.1). Gated actions create a ``pending`` row; the
    caller commits.
    """
    autonomous: list[ProposedAction] = []
    gated: list[ApprovalRequestRead] = []
    for action in actions:
        tool_config = _resolve_tool_config(action.server_name, action.tool_name)
        if tool_config is None:
            logger.info(
                "Dropping proposed action for unknown server/tool %s/%s (§17.1)",
                action.server_name,
                action.tool_name,
            )
            continue
        result = classify(action, tool_config=tool_config)
        if result.tier is ApprovalTier.AUTONOMOUS:
            autonomous.append(action)
            continue
        row = await repo.create_request(
            db,
            engagement_id=engagement_id,
            chat_message_id=chat_message_id,
            initiator_user_id=initiator_user_id,
            server_name=action.server_name,
            tool_name=action.tool_name,
            args=action.args,
            reasons=[r.value for r in result.reasons],
            preset_name=action.preset_name,
            rationale=action.rationale,
        )
        gated.append(await _to_read(db, row))
    return ClassifiedTurnResult(autonomous=autonomous, gated=gated)


async def list_requests(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    requester: User,
    status: ApprovalStatus | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> ApprovalRequestPage:
    """List the engagement's approval requests (shared queue, §5.2). Membership required."""
    member = await eng_repo.get_engagement_for_member(db, engagement_id, _user_id(requester))
    if member is None:
        raise NotFoundError("Engagement not found")

    rows, next_cursor = await repo.list_for_engagement(
        db,
        engagement_id=engagement_id,
        status=status.value if status is not None else None,
        cursor=_decode_cursor(cursor),  # type: ignore[arg-type]
        limit=limit,
    )
    cache: dict[UUID, str | None] = {}
    items = [await _to_read(db, row, username_cache=cache) for row in rows]
    return ApprovalRequestPage(items=items, next_cursor=_encode_cursor(next_cursor))


async def decide(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    request_id: UUID,
    requester: User,
    decision: Literal["approve", "reject"],
) -> ApprovalRequestRead:
    """Approve or reject a pending request (any engagement member, §5.2).

    Order is load-bearing (Risk 1 + Risk 4): membership → load → archived guard (approve)
    → **claim** the pending row (guarded UPDATE) → emit the decider-attributed audit entry
    → only the winning approve then executes the run (initiator-attributed). The audit
    entry commits atomically with the transition; the run is created ONLY inside the
    winning branch so the command can never run twice.
    """
    member = await eng_repo.get_engagement_for_member(db, engagement_id, _user_id(requester))
    if member is None:
        raise NotFoundError("Engagement not found")
    engagement, _membership = member

    request = await repo.get_request_for_engagement(
        db, engagement_id=engagement_id, request_id=request_id
    )
    if request is None:
        raise NotFoundError("Approval request not found")
    if request.status != ApprovalStatus.PENDING.value:
        raise AlreadyDecidedError(status=request.status)

    if decision == "approve" and engagement.status == "archived":
        raise EngagementArchivedError()

    initiator_id = cast(UUID, request.initiator_user_id)
    requester_id = _user_id(requester)
    self_approved = requester_id == initiator_id
    new_status = "approved" if decision == "approve" else "rejected"

    claimed = await repo.decide_request(
        db,
        request_id=request_id,
        status=new_status,
        acted_by_user_id=requester_id,
        self_approved=self_approved,
    )
    if claimed is None:
        # Lost the race — someone decided between our load and our claim (Risk 1).
        latest = await repo.get_request_for_engagement(
            db, engagement_id=engagement_id, request_id=request_id
        )
        raise AlreadyDecidedError(status=latest.status if latest is not None else "approved")

    # Atomic with the transition: the decider-attributed approval audit entry (§14/§5.2).
    action = (
        AuditAction.APPROVAL_GRANTED if decision == "approve" else AuditAction.APPROVAL_REJECTED
    )
    await audit_service.record(
        db,
        action=action,
        actor_user_id=requester_id,
        engagement_id=engagement_id,
        target_type="approval_request",
        target_id=str(request_id),
        self_approved=self_approved,
        payload={
            "server": request.server_name,
            "tool": request.tool_name,
            "reasons": list(request.reasons),
            "initiator_user_id": str(initiator_id),
            "decision": new_status,
        },
    )

    if decision == "approve":
        # Run the AI-on-behalf-of-the-initiator command (Resolved decision 3): attributed
        # to the INITIATOR, never the approver. Only the winning branch reaches here.
        run = await mcp_service.execute_tool_run(
            db,
            engagement_id=engagement_id,
            server_name=request.server_name,
            tool_name=request.tool_name,
            args=request.args,
            timeout_seconds=DEFAULT_APPROVAL_TIMEOUT_SECONDS,
            user_id=initiator_id,
            async_mode=True,
            preset_name=request.preset_name,
        )
        await repo.set_tool_run_id(db, request_id=request_id, tool_run_id=run.tool_run_id)
        final = await repo.get_request_for_engagement(
            db, engagement_id=engagement_id, request_id=request_id
        )
        return await _to_read(db, final if final is not None else claimed)

    return await _to_read(db, claimed)
