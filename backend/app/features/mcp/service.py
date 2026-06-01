"""Business logic for MCP server registry and tool-run execution.

Domain exceptions raised here are translated to HTTP codes in router.py:
  - EngagementNotFound → 404
  - NotMember          → 403 (no admin bypass — §4)
  - McpServerNotFound  → 400
  - McpServerDown      → 503

Membership check (§4 no-admin-bypass):
  Two separate queries are performed:
    1. Check that the engagement exists (bare select on Engagement by ID).
    2. Check that the requesting user has an explicit EngagementMember row
       (reusing engagements.repository.get_member, which performs a pure
       per-user-per-engagement lookup with no role bypass).

  Admin role is irrelevant here — an admin without an explicit membership row
  receives NotMember, just like any other user.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AdeptusError
from app.features.engagements import repository as eng_repo
from app.features.engagements.models import Engagement
from app.features.mcp import repository as mcp_repo
from app.features.mcp import subprocess_manager
from app.features.mcp.registry import get_registry
from app.features.mcp.schemas import (
    McpServerInfo,
    McpToolDeclaration,
    ToolRunResult,
)
from app.features.mcp.subprocess_manager import McpServerNotFound  # re-export for router

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class EngagementNotFound(AdeptusError):
    """Raised when the requested engagement does not exist."""

    def __init__(self, message: str = "Engagement not found") -> None:
        super().__init__(message)


class NotMember(AdeptusError):
    """Raised when the caller does not have an explicit membership row for the engagement.

    This check has no admin bypass — §4 requires explicit per-user membership
    for all callers including admins.
    """

    def __init__(self, message: str = "Not a member of this engagement") -> None:
        super().__init__(message)


# ---------------------------------------------------------------------------
# list_servers
# ---------------------------------------------------------------------------


async def list_servers() -> list[McpServerInfo]:
    """Return all registered MCP servers with their declared tools and live subprocess status.

    Iterates the static registry and queries the subprocess manager for each
    server's current status (``running`` / ``stopped``).  No DB access required.
    """
    registry = get_registry()
    result: list[McpServerInfo] = []

    for server_name, config in registry.items():
        status = subprocess_manager.get_server_status(server_name)
        tools = [
            McpToolDeclaration(
                name=tool.name,
                weight=cast(Literal["light", "heavy"], tool.weight),
                capability_flags=tool.capability_flags,
            )
            for tool in config.tools
        ]
        result.append(
            McpServerInfo(
                server_name=server_name,
                status=cast(Literal["running", "stopped"], status),
                tools=tools,
            )
        )

    return result


# ---------------------------------------------------------------------------
# execute_tool_run
# ---------------------------------------------------------------------------


async def execute_tool_run(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    server_name: str,
    tool_name: str,
    args: dict[str, Any],
    timeout_seconds: int,
    user_id: UUID,
) -> ToolRunResult:
    """Execute a tool call via the named MCP server and persist the result.

    Flow:
      1. Engagement existence check — EngagementNotFound if missing.
      2. Explicit membership check — NotMember if the user has no member row
         (no admin bypass per §4).
      3. Server name validation — McpServerNotFound if not in registry.
      4. Insert an in-flight ToolRun row.
      5. Call subprocess_manager.send_tool_call.
         On McpServerDown we propagate the exception to the router (→ 503).
         We do NOT update the row on McpServerDown — leaving exit_code NULL
         indicates the run never completed.  The router is responsible for the 503
         response; crash-recovery cleanup (§13) is handled at startup (Slice 38).
      6. Update the ToolRun row with the raw result.
      7. Return a ToolRunResult assembled from the updated row.

    Args:
        db:               Async database session (caller commits).
        engagement_id:    UUID of the engagement.
        server_name:      Key in the MCP registry.
        tool_name:        Name of the tool on that server.
        args:             Tool-specific argument map forwarded verbatim.
        timeout_seconds:  Per-request wall-clock budget (1–300 s).
        user_id:          ID of the requesting user.

    Returns:
        ToolRunResult with all fields populated.

    Raises:
        EngagementNotFound:  engagement_id does not exist.
        NotMember:           user_id has no explicit member row (even if admin).
        McpServerNotFound:   server_name not in the registry.
        McpServerDown:       Subprocess not running / timed out (row left in-flight).
    """
    # Step 1: engagement existence check (bare select — no member join).
    engagement_result = await db.execute(select(Engagement).where(Engagement.id == engagement_id))
    if engagement_result.scalar_one_or_none() is None:
        raise EngagementNotFound(f"Engagement {engagement_id} not found")

    # Step 2: explicit membership check — reusing engagements.repository.get_member.
    # get_member performs a pure per-user-per-engagement lookup with no role check;
    # admin role is NOT consulted here (§4 no-admin-bypass).
    member = await eng_repo.get_member(db, engagement_id, user_id)
    if member is None:
        raise NotMember(f"User {user_id} is not an explicit member of engagement {engagement_id}")

    # Step 3: server name validation.
    registry = get_registry()
    if server_name not in registry:
        raise McpServerNotFound(f"MCP server {server_name!r} is not in the registry")

    # Step 4: insert in-flight ToolRun row.
    tool_run = await mcp_repo.create_tool_run(
        db,
        engagement_id=engagement_id,
        server_name=server_name,
        tool_name=tool_name,
        args=args,
    )

    # Step 5: call the MCP subprocess.
    # McpServerDown propagates to the router; the row is left with exit_code NULL.
    raw = await subprocess_manager.send_tool_call(
        server_name=server_name,
        tool_name=tool_name,
        args=args,
        timeout_seconds=float(timeout_seconds),
    )

    # Step 6: update the row with the final result.
    finished_at = datetime.now(tz=UTC)
    updated = await mcp_repo.update_tool_run_result(
        db,
        tool_run.id,  # type: ignore[arg-type]
        exit_code=raw.exit_code,
        stdout=raw.stdout,
        stderr=raw.stderr,
        finished_at=finished_at,
    )

    # Step 7: build and return the result schema.
    return ToolRunResult(
        tool_run_id=cast(UUID, updated.id),
        engagement_id=cast(UUID, updated.engagement_id),
        server_name=updated.server_name,
        tool_name=updated.tool_name,
        exit_code=cast(int, updated.exit_code),
        stdout=updated.stdout,
        stderr=updated.stderr,
        started_at=updated.started_at,
        finished_at=cast(datetime, updated.finished_at),
    )
