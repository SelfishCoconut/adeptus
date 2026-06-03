"""FastAPI routes for the MCP feature.

Endpoints:
  GET  /api/v1/admin/mcp-servers
      Admin-only; returns list[McpServerInfo].
  GET  /api/v1/mcp/tools
      Any authenticated session; flat list[ToolDescriptor] (no engagement scope).
  POST /api/v1/tool-runs
      Requires authenticated session AND explicit engagement membership (§4).
  GET  /api/v1/tool-runs
      Paginated list of tool runs; explicit membership required (§4/§17.1).
  GET  /api/v1/tool-runs/{tool_run_id}
      Single tool run; membership on its engagement required (§4/§17.1).
  POST /api/v1/tool-runs/{tool_run_id}/kill
      Stop a single tool run (Slice 06); membership-gated (404 for non-members).
      Idempotent on terminal runs.
  POST /api/v1/tool-runs/{tool_run_id}/timeout-decision
      Answer a pending timeout prompt (Slice 06); membership-gated.  409 when no
      run is currently awaiting a decision.
  GET  /api/v1/engagements/{engagement_id}/tool-queue
      In-process concurrency snapshot; membership-gated (404 for non-members,
      no existence disclosure — §17.1/§4); Decision Q3.
  WS   /ws/tool-runs/{tool_run_id}
      WebSocket; streams live output chunks for an async tool run, or replays
      stored output for a completed run; auth via session cookie;
      closes 4003 on auth failure or non-member.

Most domain exceptions subclass the core error hierarchy and are translated by
the registered handlers in app.core.errors.handlers:

  McpServerNotFound  (BadRequestError) → 400  (unknown MCP server)
  McpToolNotFound    (BadRequestError) → 400  (unknown tool / bad params)
  BadRequestError                      → 400  (malformed cursor)
  EngagementNotFound (NotFoundError)   → 404  (engagement missing OR caller is
                                               not a member — §17.1 hides existence,
                                               §4 allows no admin bypass)

Three exceptions are translated inline (no core error type maps to their HTTP
codes without an ADR to widen core/):
  McpServerDown         → 503
  ToolQueueFullError    → 429
  EngagementPaused      → 409  (POST /tool-runs; Slice 06 Task 4/6)
  TimeoutDecisionConflict → 409 (POST /tool-runs/{id}/timeout-decision; Slice 06 Task 6)
"""

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db, get_sessionmaker
from app.core.errors import ForbiddenError
from app.features.auth.deps import get_current_user
from app.features.auth.models import User
from app.features.mcp import service
from app.features.mcp.concurrency import EngagementPaused, ToolQueueFullError
from app.features.mcp.schemas import (
    McpServerInfo,
    TimeoutDecision,
    ToolDescriptor,
    ToolQueueSnapshot,
    ToolRunCreate,
    ToolRunPage,
    ToolRunResult,
    WebSocketOutputChunk,
)
from app.features.mcp.service import TimeoutDecisionConflict
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
    except ToolQueueFullError as exc:
        # No core error type maps to HTTP 429; translate inline (same pattern as 503).
        # ToolQueueFullError is a domain exception raised by concurrency.acquire when
        # the per-engagement admission queue has reached MAX_QUEUE_DEPTH.
        return JSONResponse(
            status_code=429,
            content={"error": {"code": "too_many_requests", "message": exc.message}},
        )
    except EngagementPaused as exc:
        # No core error type maps to HTTP 409; translate inline (same pattern as 503/429).
        # EngagementPaused is raised by service.execute_tool_run when the engagement
        # has been paused via POST /engagements/{id}/pause — Slice 06 Task 4/6.
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "conflict", "message": exc.message}},
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


# ---------------------------------------------------------------------------
# POST /api/v1/tool-runs/{tool_run_id}/kill
# ---------------------------------------------------------------------------


@router.post(
    "/api/v1/tool-runs/{tool_run_id}/kill",
    response_model=ToolRunResult,
    operation_id="kill_tool_run",
    tags=["tools"],
)
async def kill_tool_run(
    tool_run_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ToolRunResult:
    """Stop a single tool run.

    Membership-gated via the same ``get_tool_run`` + ``get_engagement_for_member``
    chokepoint used by the other tool-run endpoints — both a missing run and a
    non-member caller return 404 to avoid existence disclosure (§17.1).

    Idempotent: killing an already-terminal run returns 200 with the current state.

    Returns:
        ``ToolRunResult`` — current state of the run after the kill signal is sent.

    Raises (translated to HTTP by the registered error handlers):
        EngagementNotFound (NotFoundError → 404): run not found or caller is not
            a member of its engagement (no existence disclosure).
    """
    return await service.kill_tool_run(
        db,
        tool_run_id=tool_run_id,
        user_id=current_user.id,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# POST /api/v1/tool-runs/{tool_run_id}/timeout-decision
# ---------------------------------------------------------------------------


@router.post(
    "/api/v1/tool-runs/{tool_run_id}/timeout-decision",
    response_model=ToolRunResult,
    operation_id="submit_timeout_decision",
    tags=["tools"],
)
async def submit_timeout_decision(
    tool_run_id: UUID,
    body: TimeoutDecision,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ToolRunResult:
    """Answer a pending timeout prompt for a run in state ``awaiting_decision``.

    Membership-gated via the same chokepoint as kill_tool_run (§17.1/§4).

    Returns 409 when the run is not currently awaiting a decision — covers:
    - Already resolved (another member answered first).
    - Run is in a different state (running, completed, killed, etc.).
    - Unknown run ID (past the membership gate; unreachable in practice since
      the membership gate returns 404 for unknown runs — listed for completeness).

    Returns:
        ``ToolRunResult`` — current state after the decision is forwarded.

    Raises (translated to HTTP by registered handlers):
        EngagementNotFound (NotFoundError → 404): run not found or non-member.

    Returns inline:
        409 when no run is awaiting a decision (TimeoutDecisionConflict).
    """
    try:
        return await service.submit_timeout_decision(
            db,
            tool_run_id=tool_run_id,
            user_id=current_user.id,  # type: ignore[arg-type]
            decision=body.decision,
            extend_seconds=body.extend_seconds,
        )
    except TimeoutDecisionConflict as exc:
        # No core error type maps to HTTP 409; translate inline (same pattern as 503/429).
        # FastAPI bypasses serialization for Response subclasses — JSONResponse is passed
        # through as-is even though the annotation says ToolRunResult.  cast() satisfies
        # mypy without widening the annotation to ToolRunResult | JSONResponse (W-5).
        return cast(
            ToolRunResult,
            JSONResponse(
                status_code=409,
                content={"error": {"code": "conflict", "message": exc.message}},
            ),
        )


# ---------------------------------------------------------------------------
# GET /api/v1/engagements/{engagement_id}/tool-queue
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/engagements/{engagement_id}/tool-queue",
    response_model=ToolQueueSnapshot,
    operation_id="get_tool_queue",
    tags=["tools"],
)
async def get_tool_queue(
    engagement_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ToolQueueSnapshot:
    """Return the live concurrency snapshot for an engagement's heavy-tool pool.

    Used by the Tool Runner queue strip to display "N running / M queued".
    Membership-gated via the same ``get_engagement_for_member`` chokepoint used
    by ``execute_tool_run`` (§4 no-admin-bypass, §17.1 no-existence-disclosure):
    both a missing engagement and a non-member return 404.

    Decision Q3: this endpoint lives in the mcp feature router under the
    engagement-scoped path because the queue is a tool-runner concern; its
    schemas and service logic all live in ``mcp``.

    Returns:
        ``ToolQueueSnapshot`` — slot_limit from the DB row, running_count and
        queued_count from the in-process admission manager.

    Raises (translated to HTTP by the registered error handlers):
        EngagementNotFound (NotFoundError → 404): engagement_id missing or
            caller is not a member (no existence disclosure per §17.1).
    """
    return await service.get_tool_queue_snapshot(
        db,
        engagement_id=engagement_id,
        user_id=current_user.id,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# WS /ws/tool-runs/{tool_run_id}
# ---------------------------------------------------------------------------


@router.websocket("/ws/tool-runs/{tool_run_id}")
async def stream_tool_run_ws(websocket: WebSocket, tool_run_id: UUID) -> None:
    """Stream live output chunks for an async tool run over WebSocket.

    Authentication is performed via the session cookie on the upgrade request
    (not the get_current_user dependency — that dependency slides the session
    expiry and emits a Set-Cookie header which is inappropriate on a WS upgrade).

    Authorization: the caller must be an explicit member of the engagement that
    owns the requested tool run (§4 no-admin-bypass, §17.1 no-existence-disclosure).

    Close codes:
      4003 — any auth/authz failure (cookie missing / invalid / expired,
              tool_run_id not found, or caller is not a member of its engagement).
              All failure paths use the same code to avoid existence disclosure.
      1000 — normal close after the run completes or stored output has been sent.

    Streaming behaviour:
      - If the run is live (a pub/sub channel exists): replay buffered chunks first,
        then stream new chunks from the queue until a 'done' or 'error' chunk arrives.
      - If the run is already complete (no live channel): send stored stdout/stderr
        chunks from the DB row, then a synthetic 'done' chunk.

    Mid-run reconnect (Decision 3): buffered chunks are replayed before new ones,
    so a reconnecting client sees continuous output rather than an empty console.
    """
    _WS_CLOSE_UNAUTH = 4003

    # ------------------------------------------------------------------
    # Auth + authorization (BEFORE accepting the WebSocket). The cookie is a
    # transport concern resolved here; the session/membership protocol lives in
    # the service layer. Any failure collapses to one close code (no disclosure).
    # ------------------------------------------------------------------
    session_id = websocket.cookies.get(get_settings().SESSION_COOKIE_NAME)
    async with get_sessionmaker()() as session:
        run = await service.authenticate_ws_tool_run(
            session, session_id=session_id, tool_run_id=tool_run_id
        )
    if run is None:
        await websocket.close(code=_WS_CLOSE_UNAUTH)
        return

    # ------------------------------------------------------------------
    # All checks passed — accept the WebSocket and start streaming.
    # ------------------------------------------------------------------
    await websocket.accept()

    try:
        sub = service.try_subscribe_tool_run(tool_run_id)

        if sub is not None:
            # Live run: replay buffered chunks, then stream new ones.
            replay, queue = sub
            try:
                # 1. Flush replay buffer.
                for chunk in replay:
                    await websocket.send_json(chunk.model_dump(mode="json", exclude_none=True))
                    if chunk.type in ("done", "error"):
                        # Run finished while we were mid-connect; stop here.
                        await websocket.close(code=1000)
                        return

                # 2. Stream live chunks from the queue.
                while True:
                    chunk = await queue.get()
                    await websocket.send_json(chunk.model_dump(mode="json", exclude_none=True))
                    if chunk.type in ("done", "error"):
                        break

            except WebSocketDisconnect:
                # Client disconnected; exit quietly.
                return
            finally:
                service.unsubscribe_tool_run(tool_run_id, queue)

            await websocket.close(code=1000)

        else:
            # No live channel — run already completed (possibly during the
            # auth→subscribe window). Re-read the row so we serve the final
            # persisted output, not the auth-time snapshot (empty while running).
            async with get_sessionmaker()() as session:
                latest = await service.fetch_tool_run_row(session, tool_run_id)
            if latest is not None:
                run = latest
            try:
                if run.stdout:
                    await websocket.send_json(
                        WebSocketOutputChunk(type="stdout", data=run.stdout).model_dump(
                            mode="json", exclude_none=True
                        )
                    )
                if run.stderr:
                    await websocket.send_json(
                        WebSocketOutputChunk(type="stderr", data=run.stderr).model_dump(
                            mode="json", exclude_none=True
                        )
                    )
                # Synthetic done chunk with stored exit_code and finished_at.
                done_chunk = WebSocketOutputChunk(
                    type="done",
                    exit_code=run.exit_code,
                    finished_at=run.finished_at,
                )
                await websocket.send_json(done_chunk.model_dump(mode="json", exclude_none=True))
            except WebSocketDisconnect:
                return

            await websocket.close(code=1000)

    except WebSocketDisconnect:
        # Client disconnected during the streaming preamble; exit quietly.
        return
