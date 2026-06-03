"""Pydantic v2 request/response models for the MCP feature.

Schemas match the Slice 03/04/05 OpenAPI contract exactly — field names, types,
enums, and validation constraints are authoritative here.

Slice 05 additions:
- ``ToolRunStatus`` gains the ``"queued"`` member.
- ``QueuedRun`` and ``ToolQueueSnapshot`` models for the queue-status endpoint.
  These are defined here (not inline in concurrency.py) because concurrency.py
  needs a concrete return type for ``snapshot()`` and Task 6 will only extend them.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Enums (modelled as Literal aliases for Pydantic v2 compatibility)
# ---------------------------------------------------------------------------

McpServerStatus = Literal["running", "stopped"]
ToolWeight = Literal["light", "heavy"]
ToolRunStatus = Literal[
    "queued", "running", "awaiting_decision", "completed", "killed", "failed", "timed_out"
]
QueueReason = Literal["slot_full", "target_locked"]


# ---------------------------------------------------------------------------
# MCP server registry schemas (read-only, returned by GET /admin/mcp-servers)
# ---------------------------------------------------------------------------


class McpToolDeclaration(BaseModel):
    """A single tool declared by an MCP server in its manifest."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    weight: ToolWeight
    capability_flags: list[str]


class McpServerInfo(BaseModel):
    """Runtime view of a registered MCP server — used in GET /admin/mcp-servers."""

    model_config = ConfigDict(from_attributes=True)

    server_name: str
    status: McpServerStatus
    tools: list[McpToolDeclaration]


# ---------------------------------------------------------------------------
# Tool preset and descriptor schemas (used by GET /api/v1/mcp/tools)
# ---------------------------------------------------------------------------


class ToolPreset(BaseModel):
    """A named preset for a tool, bundling a set of default arguments."""

    name: str
    description: str | None = None
    args: dict[str, Any]


class ToolDescriptor(BaseModel):
    """Enriched descriptor for a tool, including presets and arg schema.

    Returned by GET /api/v1/mcp/tools; used by the tool runner panel to
    populate the tool selector and render the dynamic argument form.
    """

    server_name: str
    tool_name: str
    weight: ToolWeight
    capability_flags: list[str]
    presets: list[ToolPreset]
    arg_schema: dict[str, Any]


# ---------------------------------------------------------------------------
# Tool-run request / response schemas
# ---------------------------------------------------------------------------


class ToolRunCreate(BaseModel):
    """Request body for POST /api/v1/tool-runs."""

    engagement_id: UUID
    server_name: str
    tool_name: str
    args: dict[str, Any]
    timeout_seconds: int = Field(
        default=30,
        ge=1,
        le=300,
        description=(
            "Per-request timeout override. Default 30 s. "
            "The MCP server kills the subprocess and returns a non-zero exit code "
            "when the limit is reached. Full kill/extend/wait UX is deferred to Slice 06."
        ),
    )
    async_mode: bool = Field(
        default=False,
        description=(
            "When true the endpoint responds 202 with a partial ToolRunResult "
            "(finished_at null, stdout/stderr empty). Output is streamed via "
            "the WebSocket endpoint."
        ),
    )
    preset_name: str | None = None


class ToolRunResult(BaseModel):
    """Response body for POST /api/v1/tool-runs and GET /api/v1/tool-runs/{id}.

    Slice 05 additions:
    - ``status`` gains the ``"queued"`` member (via ``ToolRunStatus``).
    - ``queue_position`` is the 1-based FIFO position while ``status == "queued"``;
      ``None`` once running or terminal, and always ``None`` for light runs.

    Slice 06 additions:
    - ``status`` gains ``"killed"`` and ``"awaiting_decision"`` members.
    - ``awaiting_since`` is set (non-null) while ``status == "awaiting_decision"``
      to let the UI show how long the timeout prompt has been open.  Cleared when
      the run resolves.  Derived in-process; NOT persisted as a DB column.
    """

    model_config = ConfigDict(from_attributes=True)

    tool_run_id: UUID
    engagement_id: UUID
    server_name: str
    tool_name: str
    exit_code: int | None
    stdout: str
    stderr: str
    started_at: datetime
    finished_at: datetime | None
    status: ToolRunStatus
    preset_name: str | None = None
    queue_position: int | None = None
    awaiting_since: datetime | None = None


# ---------------------------------------------------------------------------
# Paginated tool-run listing
# ---------------------------------------------------------------------------


class ToolRunPage(BaseModel):
    """Paginated list of tool runs; returned by GET /api/v1/tool-runs."""

    items: list[ToolRunResult]
    next_cursor: str | None


# ---------------------------------------------------------------------------
# WebSocket output streaming
# ---------------------------------------------------------------------------


class WebSocketOutputChunk(BaseModel):
    """A single JSON message sent over the /ws/tool-runs/{id} WebSocket.

    type "stdout" / "stderr": data carries the output line.
    type "done": exit_code and finished_at are populated.
    type "error": message carries the error description.
    type "queued" (Slice 05): run is waiting; queue_position and reason are set.
    type "started" (Slice 05): run was admitted from the queue and is now running.
    type "timeout" (Slice 06): the run hit its timeout, released its concurrency
      slot + host lock back to the FIFO queue, and is awaiting a kill/extend/wait
      decision; message notes the slot was released.  No auto-kill countdown.
    type "killed" (Slice 06): the run was stopped (per-tool kill or engagement
      pause); message carries the cause ("killed by user" / "engagement paused").
    """

    type: Literal["stdout", "stderr", "done", "error", "queued", "started", "timeout", "killed"]
    data: str | None = None
    exit_code: int | None = None
    finished_at: datetime | None = None
    message: str | None = None
    queue_position: int | None = None
    reason: QueueReason | None = None


# ---------------------------------------------------------------------------
# Kill-switch / timeout-confirm schemas (Slice 06)
# ---------------------------------------------------------------------------


class TimeoutDecision(BaseModel):
    """Request body for POST /api/v1/tool-runs/{id}/timeout-decision.

    ``decision`` is required; ``extend_seconds`` is used only when
    ``decision == "extend"`` and defaults to 30 s (range 1–300).
    """

    decision: Literal["kill", "extend", "wait"]
    extend_seconds: int = Field(
        default=30,
        ge=1,
        le=300,
        description="Additional seconds granted when decision == 'extend'. Default 30 s.",
    )


# ---------------------------------------------------------------------------
# Concurrency / queue-status schemas (Slice 05, Task 2)
# ---------------------------------------------------------------------------


class QueuedRun(BaseModel):
    """A single run waiting for admission in the FIFO queue.

    Returned as part of ``ToolQueueSnapshot``; the ``position`` field is
    1-based (1 = next to admit).  ``target_host`` is ``None`` for tools that
    take no ``target`` arg (they acquire only a slot, no host lock).
    ``enqueued_at`` is the wall-clock time the ticket was created — it lives
    in the in-process queue only and is NOT persisted to the DB.
    """

    tool_run_id: UUID
    server_name: str
    tool_name: str
    target_host: str | None
    position: int
    reason: QueueReason
    enqueued_at: datetime


class ToolQueueSnapshot(BaseModel):
    """Snapshot of the heavy-tool concurrency pool for one engagement.

    Returned by ``GET /api/v1/engagements/{engagement_id}/tool-queue``.
    All fields are derived from the in-process admission manager; nothing is
    read from the DB at snapshot time (the DB has status rows but no queue order).
    """

    slot_limit: int
    running_count: int
    queued_count: int
    queued: list[QueuedRun]
