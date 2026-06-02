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

import base64
import logging
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, NotFoundError
from app.features.engagements import repository as eng_repo
from app.features.mcp import repository as mcp_repo
from app.features.mcp import subprocess_manager
from app.features.mcp.registry import get_registry
from app.features.mcp.schemas import (
    McpServerInfo,
    McpToolDeclaration,
    ToolDescriptor,
    ToolPreset,
    ToolRunPage,
    ToolRunResult,
    ToolRunStatus,
)
from app.features.mcp.subprocess_manager import McpServerNotFound

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cursor encoding helpers
# ---------------------------------------------------------------------------


def _encode_cursor(started_at: datetime, run_id: UUID) -> str:
    """Encode a (started_at, id) pair as an opaque base64url string."""
    raw = f"{started_at.isoformat()}|{run_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    """Decode an opaque cursor string back to (started_at, id).

    Raises ValueError when the cursor is malformed.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts_part, id_part = raw.split("|", 1)
        return datetime.fromisoformat(ts_part), UUID(id_part)
    except Exception as exc:
        raise ValueError(f"Malformed cursor: {cursor!r}") from exc


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
# list_tools
# ---------------------------------------------------------------------------


async def list_tools() -> list[ToolDescriptor]:
    """Return all configured tools across all registered MCP servers.

    Iterates the static registry and builds a flat list of ToolDescriptor
    objects enriched with preset definitions and arg_schema from the config.

    All tools are included regardless of subprocess running status — the
    descriptor has no status field, so configured tools appear in the tool
    picker even if their subprocess is momentarily down.

    Ordering: registry insertion order, tools in declared order.
    """
    registry = get_registry()
    result: list[ToolDescriptor] = []

    for server_name, config in registry.items():
        for tool in config.tools:
            presets = [
                ToolPreset(
                    name=p.name,
                    description=p.description,
                    args=p.args,
                )
                for p in tool.presets
            ]
            result.append(
                ToolDescriptor(
                    server_name=server_name,
                    tool_name=tool.name,
                    weight=cast(Literal["light", "heavy"], tool.weight),
                    capability_flags=tool.capability_flags,
                    presets=presets,
                    arg_schema=tool.arg_schema,
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
    # updated.status may be None if the server_default hasn't been flushed back to the
    # in-memory object; the sync path always ends in "completed", so fall back safely.
    raw_status: str | None = getattr(updated, "status", None)
    result_status = cast(ToolRunStatus, raw_status) if raw_status else "completed"
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
        status=result_status,
        preset_name=getattr(updated, "preset_name", None),
    )


# ---------------------------------------------------------------------------
# list_tool_runs
# ---------------------------------------------------------------------------


async def list_tool_runs(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    user_id: UUID,
    limit: int,
    cursor: str | None,
) -> ToolRunPage:
    """Return a paginated page of ToolRunResult for an engagement.

    Flow:
      1. Fused existence + membership check — EngagementNotFound (404) if the
         engagement is missing OR the caller is not an explicit member (§4/§17.1).
      2. Decode the opaque cursor string if provided; a malformed non-empty cursor
         raises BadRequestError (400).
      3. Call the repo with keyset pagination parameters.
      4. Map each ToolRun row → ToolRunResult and encode next_cursor.

    Args:
        db:            Async database session.
        engagement_id: UUID of the engagement.
        user_id:       ID of the requesting user.
        limit:         Maximum number of rows per page (1–100).
        cursor:        Opaque keyset cursor from a previous response, or None for
                       the first page.

    Returns:
        ToolRunPage with items and optional next_cursor.

    Raises:
        EngagementNotFound: engagement_id does not exist OR user_id is not a member.
        BadRequestError:    cursor string is non-empty but malformed.
    """
    # Step 1: fused membership check (§17.1 isolation chokepoint).
    if await eng_repo.get_engagement_for_member(db, engagement_id, user_id) is None:
        raise EngagementNotFound(f"Engagement {engagement_id} not found")

    # Step 2: decode cursor if provided.
    decoded_cursor: tuple[datetime, UUID] | None = None
    if cursor:
        try:
            decoded_cursor = _decode_cursor(cursor)
        except ValueError as exc:
            raise BadRequestError(f"Invalid cursor: {exc}") from exc

    # Step 3: query the repo.
    rows, next_cursor_raw = await mcp_repo.list_tool_runs_for_engagement(
        db, engagement_id, limit=limit, cursor=decoded_cursor
    )

    # Step 4: map rows → ToolRunResult and encode next_cursor.
    items = [_row_to_result(row) for row in rows]
    next_cursor_str: str | None = None
    if next_cursor_raw is not None:
        nc_started, nc_id = next_cursor_raw
        next_cursor_str = _encode_cursor(nc_started, nc_id)

    return ToolRunPage(items=items, next_cursor=next_cursor_str)


# ---------------------------------------------------------------------------
# get_tool_run
# ---------------------------------------------------------------------------


async def get_tool_run(
    db: AsyncSession,
    *,
    tool_run_id: UUID,
    user_id: UUID,
) -> ToolRunResult:
    """Return a single ToolRunResult, enforcing engagement membership.

    Flow:
      1. Fetch the run by id; if missing → EngagementNotFound (404) — collapses
         missing-run and non-member into the same 404 to avoid existence disclosure.
      2. Membership check on run.engagement_id — same chokepoint as execute_tool_run.
      3. Map and return.

    Args:
        db:          Async database session.
        tool_run_id: UUID of the tool run.
        user_id:     ID of the requesting user.

    Returns:
        ToolRunResult with all fields populated.

    Raises:
        EngagementNotFound: tool_run_id does not exist OR user_id is not a member
                            of the run's engagement (404, no existence disclosure).
    """
    # Step 1: fetch the run.
    run = await mcp_repo.get_tool_run_by_id(db, tool_run_id)
    if run is None:
        raise EngagementNotFound("Tool run not found")

    # Step 2: membership check.
    if await eng_repo.get_engagement_for_member(db, cast(UUID, run.engagement_id), user_id) is None:
        raise EngagementNotFound("Tool run not found")

    # Step 3: map and return.
    return _row_to_result(run)


# ---------------------------------------------------------------------------
# Private helper
# ---------------------------------------------------------------------------


def _row_to_result(row: Any) -> ToolRunResult:
    """Map a ToolRun ORM row to a ToolRunResult schema."""
    raw_status: str | None = getattr(row, "status", None)
    result_status = cast(ToolRunStatus, raw_status) if raw_status else "completed"
    return ToolRunResult(
        tool_run_id=cast(UUID, row.id),
        engagement_id=cast(UUID, row.engagement_id),
        server_name=row.server_name,
        tool_name=row.tool_name,
        exit_code=row.exit_code,
        stdout=row.stdout or "",
        stderr=row.stderr or "",
        started_at=row.started_at,
        finished_at=row.finished_at,
        status=result_status,
        preset_name=getattr(row, "preset_name", None),
    )
