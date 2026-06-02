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
    ToolDescriptor,
    ToolPreset,
    ToolRunCreate,
    ToolRunPage,
    ToolRunResult,
    WebSocketOutputChunk,
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
            "status": "completed",
            **overrides,
        }

    def test_valid(self) -> None:
        result = ToolRunResult.model_validate(self._make())
        assert result.exit_code == 0
        assert result.stdout == "hello\n"
        assert result.status == "completed"

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

    def test_in_progress_run_accepts_null_exit_code_and_finished_at(self) -> None:
        """A running tool-run may have exit_code=None and finished_at=None."""

        result = ToolRunResult.model_validate(
            self._make(exit_code=None, finished_at=None, status="running")
        )
        assert result.exit_code is None
        assert result.finished_at is None
        assert result.status == "running"

    def test_preset_name_defaults_to_none(self) -> None:
        result = ToolRunResult.model_validate(self._make())
        assert result.preset_name is None

    def test_preset_name_accepted(self) -> None:
        result = ToolRunResult.model_validate(self._make(preset_name="quick-scan"))
        assert result.preset_name == "quick-scan"


# ---------------------------------------------------------------------------
# ToolRunCreate — new fields from Task 2
# ---------------------------------------------------------------------------


class TestToolRunCreateTask2:
    def _base(self, **overrides: Any) -> dict[str, Any]:
        return {
            "engagement_id": str(uuid.uuid4()),
            "server_name": "shell-exec",
            "tool_name": "run_command",
            "args": {"command": "echo hello"},
            **overrides,
        }

    def test_async_mode_defaults_to_false(self) -> None:
        req = ToolRunCreate.model_validate(self._base())
        assert req.async_mode is False

    def test_preset_name_defaults_to_none(self) -> None:
        req = ToolRunCreate.model_validate(self._base())
        assert req.preset_name is None

    def test_async_mode_true_accepted(self) -> None:
        req = ToolRunCreate.model_validate(self._base(async_mode=True))
        assert req.async_mode is True

    def test_preset_name_accepted(self) -> None:
        req = ToolRunCreate.model_validate(self._base(preset_name="full-scan"))
        assert req.preset_name == "full-scan"


# ---------------------------------------------------------------------------
# ToolPreset
# ---------------------------------------------------------------------------


class TestToolPreset:
    def test_valid(self) -> None:
        preset = ToolPreset(name="quick", description="Quick scan", args={"flags": "-sV"})
        assert preset.name == "quick"
        assert preset.description == "Quick scan"
        assert preset.args == {"flags": "-sV"}

    def test_description_optional(self) -> None:
        preset = ToolPreset(name="minimal", args={})
        assert preset.description is None

    def test_missing_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ToolPreset.model_validate({"args": {}})


# ---------------------------------------------------------------------------
# ToolDescriptor
# ---------------------------------------------------------------------------


class TestToolDescriptor:
    def test_valid(self) -> None:
        descriptor = ToolDescriptor(
            server_name="shell-exec",
            tool_name="run_command",
            weight="light",
            capability_flags=["shell-exec"],
            presets=[ToolPreset(name="default", args={"command": "echo"})],
            arg_schema={"type": "object", "properties": {"command": {"type": "string"}}},
        )
        assert descriptor.server_name == "shell-exec"
        assert descriptor.tool_name == "run_command"
        assert descriptor.weight == "light"
        assert len(descriptor.presets) == 1
        assert descriptor.presets[0].name == "default"

    def test_empty_presets_accepted(self) -> None:
        descriptor = ToolDescriptor(
            server_name="nmap",
            tool_name="scan",
            weight="heavy",
            capability_flags=["network-scan"],
            presets=[],
            arg_schema={},
        )
        assert descriptor.presets == []

    def test_invalid_weight_rejected(self) -> None:
        data: dict[str, Any] = {
            "server_name": "x",
            "tool_name": "y",
            "weight": "medium",
            "capability_flags": [],
            "presets": [],
            "arg_schema": {},
        }
        with pytest.raises(ValidationError):
            ToolDescriptor.model_validate(data)


# ---------------------------------------------------------------------------
# ToolRunPage
# ---------------------------------------------------------------------------


class TestToolRunPage:
    def _make_result(self) -> dict[str, Any]:
        from datetime import datetime

        now = datetime.now(tz=UTC)
        return {
            "tool_run_id": str(uuid.uuid4()),
            "engagement_id": str(uuid.uuid4()),
            "server_name": "shell-exec",
            "tool_name": "run_command",
            "exit_code": 0,
            "stdout": "ok\n",
            "stderr": "",
            "started_at": now,
            "finished_at": now,
            "status": "completed",
        }

    def test_valid_with_items(self) -> None:
        page = ToolRunPage.model_validate({"items": [self._make_result()], "next_cursor": "abc123"})
        assert len(page.items) == 1
        assert page.next_cursor == "abc123"

    def test_empty_items_accepted(self) -> None:
        page = ToolRunPage.model_validate({"items": [], "next_cursor": None})
        assert page.items == []
        assert page.next_cursor is None


# ---------------------------------------------------------------------------
# WebSocketOutputChunk
# ---------------------------------------------------------------------------


class TestWebSocketOutputChunk:
    def test_stdout_chunk(self) -> None:
        chunk = WebSocketOutputChunk(type="stdout", data="hello\n")
        assert chunk.type == "stdout"
        assert chunk.data == "hello\n"
        assert chunk.exit_code is None
        assert chunk.finished_at is None
        assert chunk.message is None

    def test_stderr_chunk(self) -> None:
        chunk = WebSocketOutputChunk(type="stderr", data="error line\n")
        assert chunk.type == "stderr"
        assert chunk.data == "error line\n"

    def test_done_chunk(self) -> None:
        from datetime import datetime

        now = datetime.now(tz=UTC)
        chunk = WebSocketOutputChunk(type="done", exit_code=0, finished_at=now)
        assert chunk.type == "done"
        assert chunk.exit_code == 0
        assert chunk.finished_at == now

    def test_error_chunk(self) -> None:
        chunk = WebSocketOutputChunk(type="error", message="subprocess crashed")
        assert chunk.type == "error"
        assert chunk.message == "subprocess crashed"

    def test_invalid_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WebSocketOutputChunk.model_validate({"type": "unknown"})

    def test_round_trip_serialisation(self) -> None:
        chunk = WebSocketOutputChunk(type="stdout", data="line\n")
        as_dict = chunk.model_dump()
        restored = WebSocketOutputChunk.model_validate(as_dict)
        assert restored.type == "stdout"
        assert restored.data == "line\n"
