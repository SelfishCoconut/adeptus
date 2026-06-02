"""FastAPI routes for the MCP feature.

Endpoints:
  GET  /api/v1/admin/mcp-servers       — admin-only; returns list[McpServerInfo]
  GET  /api/v1/mcp/tools               — any authenticated session; returns flat
                                         list[ToolDescriptor] aggregated across all
                                         registered MCP servers (no engagement scope,
                                         no admin requirement)
  POST /api/v1/tool-runs               — requires authenticated session AND explicit
                                         engagement membership (no admin bypass — §4)
  GET  /api/v1/tool-runs               — paginated list of tool runs for an engagement;
                                         requires explicit membership (§4/§17.1)
  GET  /api/v1/tool-runs/{tool_run_id} — single tool run; requires membership on its
                                         engagement (§4/§17.1)

Most domain exceptions subclass the core error hierarchy and are translated by
the registered handlers in app.core.errors.handlers:

  McpServerNotFound  (BadRequestError) → 400  (unknown MCP server)
  McpToolNotFound    (BadRequestError) → 400  (unknown tool / bad params)
  BadRequestError                      → 400  (malformed cursor)
  EngagementNotFound (NotFoundError)   → 404  (engagement missing OR caller is
                                               not a member — §17.1 hides existence,
                                               §4 allows no admin bypass)

Only McpServerDown → 503 is translated inline below: there is no core error
type for HTTP 503, and adding one would widen core/ and require an ADR.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import ForbiddenError
from app.features.auth.deps import get_current_user
from app.features.auth.models import User
from app.features.mcp import service
from app.features.mcp.schemas import (
    McpServerInfo,
    ToolDescriptor,
    ToolRunCreate,
    ToolRunPage,
    ToolRunResult,
)
from app.features.mcp.subprocess_manager import McpServerDown

router = APIRouter(tags=["mcp"])


# ---------------------------------------------------------------------------
# GET /api/v1/admin/mcp-servers
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/admin/mcp-servers",
    response_model=list[McpServerInfo],
    operation_id="list_mcp_servers",
)
async def list_mcp_servers(
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[McpServerInfo]:
    """List all registered MCP servers with their declared capabilities and live status.

    Admin-only.  Returns 403 for any authenticated non-admin caller.
    """
    if current_user.role != "admin":
        raise ForbiddenError("Admin access required")

    return await service.list_servers()


# ---------------------------------------------------------------------------
# GET /api/v1/mcp/tools
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/mcp/tools",
    response_model=list[ToolDescriptor],
    operation_id="list_mcp_tools",
)
async def list_mcp_tools(
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[ToolDescriptor]:
    """List all tools available across all registered MCP servers.

    Returns a flat list of ToolDescriptor enriched with preset definitions and
    arg_schema.  No admin requirement and no engagement scoping — any
    authenticated session may call this endpoint.
    """
    return await service.list_tools()


# ---------------------------------------------------------------------------
# POST /api/v1/tool-runs
# ---------------------------------------------------------------------------


@router.post(
    "/api/v1/tool-runs",
    response_model=ToolRunResult,
    operation_id="execute_tool_run",
)
async def execute_tool_run(
    body: ToolRunCreate,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ToolRunResult | JSONResponse:
    """Execute a tool call via the named MCP server and wait for the result.

    Requires an authenticated session AND explicit engagement membership.
    Admin role does NOT bypass the membership requirement (§4).

    When ``async_mode=True`` the endpoint returns HTTP 202 immediately with a
    partial ToolRunResult (status='running', finished_at/exit_code null).
    Output is streamed via the WebSocket endpoint (Task 7).
    """
    try:
        result = await service.execute_tool_run(
            db,
            engagement_id=body.engagement_id,
            server_name=body.server_name,
            tool_name=body.tool_name,
            args=body.args,
            timeout_seconds=body.timeout_seconds,
            user_id=current_user.id,  # type: ignore[arg-type]
            async_mode=body.async_mode,
            preset_name=body.preset_name,
        )
        if body.async_mode:
            # The service has already committed the running row; set 202.
            response.status_code = status.HTTP_202_ACCEPTED
        else:
            await db.commit()
        return result
    except McpServerDown as exc:
        # No core error type maps to HTTP 503; translate this one inline.
        # EngagementNotFound/McpServerNotFound/McpToolNotFound subclass core error
        # types and are handled by the registered handlers.
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "service_unavailable", "message": exc.message}},
        )


# ---------------------------------------------------------------------------
# GET /api/v1/tool-runs
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/tool-runs",
    response_model=ToolRunPage,
    operation_id="list_tool_runs",
)
async def list_tool_runs(
    engagement_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: str | None = None,
) -> ToolRunPage:
    """Return a paginated list of tool runs for an engagement (newest first).

    Requires explicit engagement membership.  Admin role does NOT bypass the
    membership requirement (§4).  Non-member and missing-engagement both return
    404 to avoid existence disclosure (§17.1).
    """
    return await service.list_tool_runs(
        db,
        engagement_id=engagement_id,
        user_id=current_user.id,  # type: ignore[arg-type]
        limit=limit,
        cursor=cursor,
    )


# ---------------------------------------------------------------------------
# GET /api/v1/tool-runs/{tool_run_id}
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/tool-runs/{tool_run_id}",
    response_model=ToolRunResult,
    operation_id="get_tool_run",
)
async def get_tool_run(
    tool_run_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ToolRunResult:
    """Return a single tool run by id.

    Requires explicit membership in the run's engagement.  Admin role does NOT
    bypass the membership requirement (§4).  Both a missing run and a non-member
    caller return 404 to avoid existence disclosure (§17.1).
    """
    return await service.get_tool_run(
        db,
        tool_run_id=tool_run_id,
        user_id=current_user.id,  # type: ignore[arg-type]
    )
