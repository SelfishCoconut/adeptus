"""Unit tests for MCP Pydantic schemas.

Covers field presence, types, enum membership, and the timeout_seconds
1–300 range validation on ToolRunCreate.
"""

import uuid
from datetime import UTC
from typing import Any

import pytest
from pydantic import ValidationError

from app.features.mcp.schemas import (
    McpServerInfo,
    McpToolDeclaration,
    ToolRunCreate,
    ToolRunResult,
)

# ---------------------------------------------------------------------------
# McpToolDeclaration
# ---------------------------------------------------------------------------


class TestMcpToolDeclaration:
    def test_valid(self) -> None:
        tool = McpToolDeclaration(
            name="run_command",
            weight="light",
            capability_flags=["shell-exec", "filesystem-write"],
        )
        assert tool.name == "run_command"
        assert tool.weight == "light"
        assert tool.capability_flags == ["shell-exec", "filesystem-write"]

    def test_heavy_weight(self) -> None:
        tool = McpToolDeclaration(name="nmap", weight="heavy", capability_flags=["network-scan"])
        assert tool.weight == "heavy"

    def test_invalid_weight(self) -> None:
        data: dict[str, Any] = {"name": "tool", "weight": "medium", "capability_flags": []}
        with pytest.raises(ValidationError):
            McpToolDeclaration.model_validate(data)

    def test_empty_capability_flags_allowed(self) -> None:
        tool = McpToolDeclaration(name="tool", weight="light", capability_flags=[])
        assert tool.capability_flags == []

    def test_missing_required_fields(self) -> None:
        with pytest.raises(ValidationError):
            McpToolDeclaration(name="tool")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# McpServerInfo
# ---------------------------------------------------------------------------


class TestMcpServerInfo:
    def test_valid_running(self) -> None:
        info = McpServerInfo(
            server_name="shell-exec",
            status="running",
            tools=[
                McpToolDeclaration(
                    name="run_command",
                    weight="light",
                    capability_flags=["shell-exec"],
                )
            ],
        )
        assert info.server_name == "shell-exec"
        assert info.status == "running"
        assert len(info.tools) == 1

    def test_valid_stopped(self) -> None:
        info = McpServerInfo(server_name="nmap", status="stopped", tools=[])
        assert info.status == "stopped"

    def test_invalid_status(self) -> None:
        data: dict[str, Any] = {"server_name": "x", "status": "degraded", "tools": []}
        with pytest.raises(ValidationError):
            McpServerInfo.model_validate(data)

    def test_missing_server_name(self) -> None:
        with pytest.raises(ValidationError):
            McpServerInfo(status="running", tools=[])  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# ToolRunCreate
# ---------------------------------------------------------------------------


class TestToolRunCreate:
    def _base(self, **overrides: Any) -> dict[str, Any]:
        return {
            "engagement_id": str(uuid.uuid4()),
            "server_name": "shell-exec",
            "tool_name": "run_command",
            "args": {"command": "echo hello"},
            **overrides,
        }

    def test_defaults(self) -> None:
        req = ToolRunCreate.model_validate(self._base())
        assert req.timeout_seconds == 30

    def test_timeout_at_minimum(self) -> None:
        req = ToolRunCreate.model_validate(self._base(timeout_seconds=1))
        assert req.timeout_seconds == 1

    def test_timeout_at_maximum(self) -> None:
        req = ToolRunCreate.model_validate(self._base(timeout_seconds=300))
        assert req.timeout_seconds == 300

    def test_timeout_below_minimum_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ToolRunCreate.model_validate(self._base(timeout_seconds=0))

    def test_timeout_above_maximum_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ToolRunCreate.model_validate(self._base(timeout_seconds=301))

    def test_engagement_id_is_uuid(self) -> None:
        req = ToolRunCreate.model_validate(self._base())
        assert isinstance(req.engagement_id, uuid.UUID)

    def test_invalid_engagement_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ToolRunCreate.model_validate(self._base(engagement_id="not-a-uuid"))

    def test_args_accepts_arbitrary_dict(self) -> None:
        req = ToolRunCreate.model_validate(
            self._base(args={"command": "ls -la", "env": {"FOO": "bar"}})
        )
        assert req.args["env"] == {"FOO": "bar"}

    def test_missing_required_fields(self) -> None:
        data: dict[str, Any] = {"engagement_id": str(uuid.uuid4())}
        with pytest.raises(ValidationError):
            ToolRunCreate.model_validate(data)


# ---------------------------------------------------------------------------
# ToolRunResult
# ---------------------------------------------------------------------------


class TestToolRunResult:
    def _make(self, **overrides: Any) -> dict[str, Any]:
        from datetime import datetime

        now = datetime.now(tz=UTC)
        return {
            "tool_run_id": str(uuid.uuid4()),
            "engagement_id": str(uuid.uuid4()),
            "server_name": "shell-exec",
            "tool_name": "run_command",
            "exit_code": 0,
            "stdout": "hello\n",
            "stderr": "",
            "started_at": now,
            "finished_at": now,
            **overrides,
        }

    def test_valid(self) -> None:
        result = ToolRunResult.model_validate(self._make())
        assert result.exit_code == 0
        assert result.stdout == "hello\n"

    def test_non_zero_exit_code_accepted(self) -> None:
        result = ToolRunResult.model_validate(self._make(exit_code=127))
        assert result.exit_code == 127

    def test_missing_required_field(self) -> None:
        data = self._make()
        del data["exit_code"]
        with pytest.raises(ValidationError):
            ToolRunResult.model_validate(data)

    def test_all_uuid_fields_parsed(self) -> None:
        result = ToolRunResult.model_validate(self._make())
        assert isinstance(result.tool_run_id, uuid.UUID)
        assert isinstance(result.engagement_id, uuid.UUID)
