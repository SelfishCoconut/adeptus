"""Pydantic v2 request/response models for the MCP feature.

Schemas match the Slice 03 OpenAPI contract exactly — field names, types,
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


class ToolRunResult(BaseModel):
    """Response body for POST /api/v1/tool-runs."""

    model_config = ConfigDict(from_attributes=True)

    tool_run_id: UUID
    engagement_id: UUID
    server_name: str
    tool_name: str
    exit_code: int
    stdout: str
    stderr: str
    started_at: datetime
    finished_at: datetime
