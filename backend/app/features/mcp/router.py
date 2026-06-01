"""FastAPI routes for the MCP feature.

Endpoints:
  GET  /api/v1/admin/mcp-servers  — admin-only; returns list[McpServerInfo]
  POST /api/v1/tool-runs          — requires authenticated session AND explicit
                                    engagement membership (no admin bypass — §4)

Domain exceptions are translated to HTTP codes inline (try/except) because the
MCP-specific exceptions (EngagementNotFound, NotMember, McpServerNotFound,
McpServerDown) are feature-level types, not subclasses of the core error
hierarchy, and registering them in core/errors/handlers.py would require an
ADR per CLAUDE.md.  The mapping is:

  McpServerNotFound   → 400  (unknown MCP server or tool)
  EngagementNotFound  → 404  (engagement does not exist)
  NotMember           → 403  (caller is not an explicit member — no admin bypass)
  McpServerDown       → 503  (subprocess not running)
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import ForbiddenError
from app.features.auth.deps import get_current_user
from app.features.auth.models import User
from app.features.mcp import service
from app.features.mcp.schemas import McpServerInfo, ToolRunCreate, ToolRunResult
from app.features.mcp.service import EngagementNotFound, NotMember
from app.features.mcp.subprocess_manager import McpServerDown, McpServerNotFound

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
# POST /api/v1/tool-runs
# ---------------------------------------------------------------------------


@router.post(
    "/api/v1/tool-runs",
    response_model=ToolRunResult,
    operation_id="execute_tool_run",
)
async def execute_tool_run(
    body: ToolRunCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ToolRunResult | JSONResponse:
    """Execute a tool call via the named MCP server and wait for the result.

    Requires an authenticated session AND explicit engagement membership.
    Admin role does NOT bypass the membership requirement (§4).
    """
    try:
        return await service.execute_tool_run(
            db,
            engagement_id=body.engagement_id,
            server_name=body.server_name,
            tool_name=body.tool_name,
            args=body.args,
            timeout_seconds=body.timeout_seconds,
            user_id=current_user.id,  # type: ignore[arg-type]
        )
    except EngagementNotFound as exc:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "not_found", "message": exc.message}},
        )
    except NotMember as exc:
        return JSONResponse(
            status_code=403,
            content={"error": {"code": "forbidden", "message": exc.message}},
        )
    except McpServerNotFound as exc:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "bad_request", "message": exc.message}},
        )
    except McpServerDown as exc:
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "service_unavailable", "message": exc.message}},
        )
