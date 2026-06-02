"""Business logic for MCP server registry and tool-run execution.

Domain exceptions raised here are translated to HTTP codes in router.py:
  - EngagementNotFound → 404
  - McpServerNotFound  → 400
  - McpServerDown      → 503

Membership check (§4 no-admin-bypass + §17.1 isolation):
  A single fused query (engagements.repository.get_engagement_for_member) is the
  §17.1 isolation chokepoint: it returns the (Engagement, EngagementMember) pair
  only when the caller has an explicit member row, and None otherwise. Both
  "engagement does not exist" and "caller is not a member" collapse to the same
  EngagementNotFound (→ 404) so a non-member cannot infer that the engagement
  exists — matching the established engagements-feature posture.

  Admin role is irrelevant here — the query never consults role, so an admin
  without an explicit membership row is denied exactly like any other user
  (§4 no admin bypass). The denial is a 404, not a 403, to avoid existence
  disclosure.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.features.engagements import repository as eng_repo
from app.features.mcp import repository as mcp_repo
from app.features.mcp import subprocess_manager
from app.features.mcp.registry import get_registry
from app.features.mcp.schemas import (
    McpServerInfo,
    McpToolDeclaration,
    ToolRunResult,
)
from app.features.mcp.subprocess_manager import McpServerNotFound

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class EngagementNotFound(NotFoundError):
    """Raised when the requested engagement does not exist.

    Subclasses the core ``NotFoundError`` so the registered error handler maps
    it to HTTP 404 — no inline translation needed in the router.
    """

    def __init__(self, message: str = "Engagement not found") -> None:
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
      1. Fused existence + membership check via get_engagement_for_member —
         EngagementNotFound (404) if the engagement is missing OR the caller is
         not an explicit member (no admin bypass per §4; no existence disclosure
         per §17.1).
      2. Server name validation — McpServerNotFound if not in registry.
      3. Insert an in-flight ToolRun row.
      4. Call subprocess_manager.send_tool_call.
         On McpServerDown we propagate the exception to the router (→ 503).
         We do NOT update the row on McpServerDown — leaving exit_code NULL
         indicates the run never completed.  The router is responsible for the 503
         response; crash-recovery cleanup (§13) is handled at startup (Slice 38).
      5. Update the ToolRun row with the raw result.
      6. Return a ToolRunResult assembled from the updated row.

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
        EngagementNotFound:  engagement_id does not exist OR user_id is not an
                             explicit member (even if admin — §4/§17.1).
        McpServerNotFound:   server_name not in the registry.
        McpServerDown:       Subprocess not running / timed out (row left in-flight).
    """
    # Step 1: fused existence + membership check (§17.1 isolation chokepoint).
    # get_engagement_for_member returns None when the engagement is missing OR the
    # caller has no explicit member row; both collapse to 404 so a non-member
    # cannot infer the engagement exists. Role is never consulted (§4 no-bypass).
    if await eng_repo.get_engagement_for_member(db, engagement_id, user_id) is None:
        raise EngagementNotFound(f"Engagement {engagement_id} not found")

    # Step 2: server name validation.
    registry = get_registry()
    if server_name not in registry:
        raise McpServerNotFound(f"MCP server {server_name!r} is not in the registry")

    # Step 3: insert in-flight ToolRun row.
    tool_run = await mcp_repo.create_tool_run(
        db,
        engagement_id=engagement_id,
        server_name=server_name,
        tool_name=tool_name,
        args=args,
    )

    # Step 4: call the MCP subprocess.
    # McpServerDown propagates to the router; the row is left with exit_code NULL.
    raw = await subprocess_manager.send_tool_call(
        server_name=server_name,
        tool_name=tool_name,
        args=args,
        timeout_seconds=float(timeout_seconds),
    )

    # Step 5: update the row with the final result.
    finished_at = datetime.now(tz=UTC)
    updated = await mcp_repo.update_tool_run_result(
        db,
        tool_run.id,  # type: ignore[arg-type]
        exit_code=raw.exit_code,
        stdout=raw.stdout,
        stderr=raw.stderr,
        finished_at=finished_at,
    )

    # Step 6: build and return the result schema.
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
