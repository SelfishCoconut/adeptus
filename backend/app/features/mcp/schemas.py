"""Pydantic v2 request/response models for the MCP feature.

Schemas match the Slice 03/04 OpenAPI contract exactly — field names, types,
enums, and validation constraints are authoritative here.
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
ToolRunStatus = Literal["running", "completed", "failed", "timed_out"]


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
    """Response body for POST /api/v1/tool-runs and GET /api/v1/tool-runs/{id}."""

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
    """

    type: Literal["stdout", "stderr", "done", "error"]
    data: str | None = None
    exit_code: int | None = None
    finished_at: datetime | None = None
    message: str | None = None
