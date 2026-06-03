"""Unit tests for MCP Pydantic schemas.

Covers field presence, types, enum membership, and the timeout_seconds
1–300 range validation on ToolRunCreate.

Also covers the _row_to_result mapping (Task 6): queue_position is populated
from concurrency.position_of when status == 'queued', and None otherwise.
"""

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.features.mcp.schemas import (
    McpServerInfo,
    McpToolDeclaration,
    TimeoutDecision,
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

    def test_queued_type_accepted(self) -> None:
        chunk = WebSocketOutputChunk(type="queued", queue_position=2, reason="slot_full")
        assert chunk.type == "queued"
        assert chunk.queue_position == 2
        assert chunk.reason == "slot_full"

    def test_started_type_accepted(self) -> None:
        chunk = WebSocketOutputChunk(type="started")
        assert chunk.type == "started"
        assert chunk.queue_position is None
        assert chunk.reason is None

    def test_reason_target_locked_accepted(self) -> None:
        chunk = WebSocketOutputChunk(type="queued", queue_position=1, reason="target_locked")
        assert chunk.reason == "target_locked"

    def test_invalid_reason_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WebSocketOutputChunk.model_validate({"type": "queued", "reason": "unknown_reason"})


# ---------------------------------------------------------------------------
# ToolRunResult — queue_position field (Task 6)
# ---------------------------------------------------------------------------


class TestToolRunResultQueuePosition:
    """Verify queue_position field on ToolRunResult schema."""

    def _make(self, **overrides: Any) -> dict[str, Any]:
        now = datetime.now(tz=UTC)
        return {
            "tool_run_id": str(uuid4()),
            "engagement_id": str(uuid4()),
            "server_name": "httpx",
            "tool_name": "run_httpx_heavy",
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "started_at": now,
            "finished_at": None,
            "status": "queued",
            **overrides,
        }

    def test_queue_position_defaults_to_none(self) -> None:
        result = ToolRunResult.model_validate(self._make())
        assert result.queue_position is None

    def test_queue_position_accepted(self) -> None:
        result = ToolRunResult.model_validate(self._make(queue_position=3))
        assert result.queue_position == 3

    def test_queue_position_none_for_running(self) -> None:
        result = ToolRunResult.model_validate(self._make(status="running", queue_position=None))
        assert result.queue_position is None

    def test_queued_status_accepted(self) -> None:
        result = ToolRunResult.model_validate(self._make(status="queued"))
        assert result.status == "queued"


# ---------------------------------------------------------------------------
# _row_to_result mapping (Task 6) — queue_position from concurrency.position_of
# ---------------------------------------------------------------------------


def _make_tool_run_row(
    *,
    run_id: uuid.UUID | None = None,
    status: str = "completed",
) -> MagicMock:
    """Build a minimal ToolRun-like mock for _row_to_result testing."""
    now = datetime.now(tz=UTC)
    row = MagicMock()
    row.id = run_id or uuid4()
    row.engagement_id = uuid4()
    row.server_name = "httpx"
    row.tool_name = "run_httpx_heavy"
    row.exit_code = None
    row.stdout = ""
    row.stderr = ""
    row.started_at = now
    row.finished_at = None
    row.status = status
    row.preset_name = None
    return row


class TestRowToResultMapping:
    """Unit tests for service._row_to_result queue_position population (Task 6)."""

    def setup_method(self) -> None:
        from app.features.mcp import concurrency

        concurrency._reset()

    def teardown_method(self) -> None:
        from app.features.mcp import concurrency

        concurrency._reset()

    def test_queued_row_gets_live_position(self) -> None:
        """A row with status='queued' gets queue_position from concurrency.position_of."""
        from app.features.mcp.service import _row_to_result

        run_id = uuid4()
        row = _make_tool_run_row(run_id=run_id, status="queued")

        with patch(
            "app.features.mcp.service.concurrency.position_of",
            return_value=2,
        ) as mock_pos:
            result = _row_to_result(row)

        mock_pos.assert_called_once_with(run_id)
        assert result.queue_position == 2
        assert result.status == "queued"

    def test_queued_row_not_in_queue_returns_none(self) -> None:
        """A 'queued' row that's no longer in the in-process queue returns None."""
        from app.features.mcp.service import _row_to_result

        run_id = uuid4()
        row = _make_tool_run_row(run_id=run_id, status="queued")

        with patch(
            "app.features.mcp.service.concurrency.position_of",
            return_value=None,
        ):
            result = _row_to_result(row)

        assert result.queue_position is None
        assert result.status == "queued"

    def test_running_row_has_no_queue_position(self) -> None:
        """A running row always has queue_position=None; position_of is not called."""
        from app.features.mcp.service import _row_to_result

        row = _make_tool_run_row(status="running")

        with patch(
            "app.features.mcp.service.concurrency.position_of",
        ) as mock_pos:
            result = _row_to_result(row)

        mock_pos.assert_not_called()
        assert result.queue_position is None
        assert result.status == "running"

    def test_completed_row_has_no_queue_position(self) -> None:
        """A completed row always has queue_position=None."""
        from app.features.mcp.service import _row_to_result

        row = _make_tool_run_row(status="completed")

        with patch("app.features.mcp.service.concurrency.position_of") as mock_pos:
            result = _row_to_result(row)

        mock_pos.assert_not_called()
        assert result.queue_position is None

    def test_failed_row_has_no_queue_position(self) -> None:
        """A failed row always has queue_position=None."""
        from app.features.mcp.service import _row_to_result

        row = _make_tool_run_row(status="failed")

        with patch("app.features.mcp.service.concurrency.position_of") as mock_pos:
            result = _row_to_result(row)

        mock_pos.assert_not_called()
        assert result.queue_position is None

    def test_timed_out_row_has_no_queue_position(self) -> None:
        """A timed_out row always has queue_position=None."""
        from app.features.mcp.service import _row_to_result

        row = _make_tool_run_row(status="timed_out")

        with patch("app.features.mcp.service.concurrency.position_of") as mock_pos:
            result = _row_to_result(row)

        mock_pos.assert_not_called()
        assert result.queue_position is None

    def test_queued_position_first_in_queue(self) -> None:
        """A run at position 1 (front of queue) gets queue_position=1."""
        from app.features.mcp.service import _row_to_result

        run_id = uuid4()
        row = _make_tool_run_row(run_id=run_id, status="queued")

        with patch(
            "app.features.mcp.service.concurrency.position_of",
            return_value=1,
        ):
            result = _row_to_result(row)

        assert result.queue_position == 1

    def test_mapping_preserves_other_fields(self) -> None:
        """_row_to_result still maps all non-queue fields correctly."""
        from app.features.mcp.service import _row_to_result

        run_id = uuid4()
        engagement_id = uuid4()
        now = datetime.now(tz=UTC)
        row = MagicMock()
        row.id = run_id
        row.engagement_id = engagement_id
        row.server_name = "httpx"
        row.tool_name = "run_httpx_heavy"
        row.exit_code = None
        row.stdout = "some output"
        row.stderr = "some error"
        row.started_at = now
        row.finished_at = None
        row.status = "queued"
        row.preset_name = "my-preset"

        with patch(
            "app.features.mcp.service.concurrency.position_of",
            return_value=3,
        ):
            result = _row_to_result(row)

        assert result.tool_run_id == run_id
        assert result.engagement_id == engagement_id
        assert result.server_name == "httpx"
        assert result.tool_name == "run_httpx_heavy"
        assert result.stdout == "some output"
        assert result.stderr == "some error"
        assert result.preset_name == "my-preset"
        assert result.queue_position == 3

    def test_awaiting_since_populated_for_awaiting_decision_row(self) -> None:
        """W-1: _row_to_result surfaces awaiting_since from the in-process registry
        when row.status == 'awaiting_decision'.

        The timestamp is NOT a DB column (per the slice Data-model section); it is
        stored in _RunEntry.awaiting_since by release_for_decision and read here
        via concurrency.get_awaiting_since.  This test verifies the wiring.
        """
        from app.features.mcp.service import _row_to_result

        run_id = uuid4()
        now = datetime.now(tz=UTC)
        row = _make_tool_run_row(run_id=run_id, status="awaiting_decision")

        with patch(
            "app.features.mcp.service.concurrency.get_awaiting_since",
            return_value=now,
        ) as mock_get:
            result = _row_to_result(row)

        mock_get.assert_called_once_with(run_id)
        assert result.awaiting_since == now, (
            "_row_to_result must populate awaiting_since from the registry "
            "when status == 'awaiting_decision'"
        )

    def test_awaiting_since_is_none_for_non_awaiting_rows(self) -> None:
        """W-1: _row_to_result returns awaiting_since=None for non-awaiting rows.

        concurrency.get_awaiting_since must NOT be called for rows with other
        statuses — we only read from the registry when status == 'awaiting_decision'.
        """
        from app.features.mcp.service import _row_to_result

        for status in ("running", "queued", "completed", "killed", "failed", "timed_out"):
            row = _make_tool_run_row(status=status)
            with patch(
                "app.features.mcp.service.concurrency.get_awaiting_since",
            ) as mock_get:
                result = _row_to_result(row)

            if status != "queued":
                mock_get.assert_not_called()
            assert result.awaiting_since is None, (
                f"awaiting_since must be None for status={status!r}"
            )


# ---------------------------------------------------------------------------
# ToolRunStatus — Slice 06 additions (killed, awaiting_decision)
# ---------------------------------------------------------------------------


class TestToolRunStatusSlice06:
    """ToolRunStatus now includes 'killed' and 'awaiting_decision'."""

    def _make(self, **overrides: Any) -> dict[str, Any]:
        now = datetime.now(tz=UTC)
        return {
            "tool_run_id": str(uuid4()),
            "engagement_id": str(uuid4()),
            "server_name": "httpx",
            "tool_name": "run_httpx_heavy",
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "started_at": now,
            "finished_at": None,
            "status": "running",
            **overrides,
        }

    def test_killed_status_accepted(self) -> None:
        result = ToolRunResult.model_validate(self._make(status="killed"))
        assert result.status == "killed"

    def test_awaiting_decision_status_accepted(self) -> None:
        result = ToolRunResult.model_validate(self._make(status="awaiting_decision"))
        assert result.status == "awaiting_decision"

    def test_invalid_status_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ToolRunResult.model_validate(self._make(status="stopping"))

    def test_awaiting_since_defaults_to_none(self) -> None:
        result = ToolRunResult.model_validate(self._make())
        assert result.awaiting_since is None

    def test_awaiting_since_accepted_with_datetime(self) -> None:
        now = datetime.now(tz=UTC)
        result = ToolRunResult.model_validate(
            self._make(status="awaiting_decision", awaiting_since=now)
        )
        assert result.awaiting_since == now

    def test_awaiting_since_is_none_for_terminal_statuses(self) -> None:
        for status in ("completed", "killed", "failed", "timed_out"):
            result = ToolRunResult.model_validate(self._make(status=status))
            assert result.awaiting_since is None


# ---------------------------------------------------------------------------
# WebSocketOutputChunk — Slice 06 type additions (timeout, killed)
# ---------------------------------------------------------------------------


class TestWebSocketOutputChunkSlice06:
    """WebSocketOutputChunk.type now includes 'timeout' and 'killed'."""

    def test_timeout_type_accepted(self) -> None:
        chunk = WebSocketOutputChunk(
            type="timeout", message="Slot released — queue is free to advance."
        )
        assert chunk.type == "timeout"
        assert chunk.message == "Slot released — queue is free to advance."

    def test_killed_type_accepted(self) -> None:
        chunk = WebSocketOutputChunk(type="killed", message="killed by user")
        assert chunk.type == "killed"
        assert chunk.message == "killed by user"

    def test_killed_type_engagement_paused(self) -> None:
        chunk = WebSocketOutputChunk(type="killed", message="engagement paused")
        assert chunk.type == "killed"

    def test_invalid_type_still_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WebSocketOutputChunk.model_validate({"type": "stopping"})

    def test_all_valid_types_accepted(self) -> None:
        valid_types = [
            "stdout",
            "stderr",
            "done",
            "error",
            "queued",
            "started",
            "timeout",
            "killed",
        ]
        for t in valid_types:
            chunk = WebSocketOutputChunk.model_validate({"type": t})
            assert chunk.type == t


# ---------------------------------------------------------------------------
# TimeoutDecision — Slice 06
# ---------------------------------------------------------------------------


class TestTimeoutDecision:
    """TimeoutDecision: required 'decision' literal, bounded extend_seconds."""

    def test_kill_decision_accepted(self) -> None:
        td = TimeoutDecision(decision="kill")
        assert td.decision == "kill"
        assert td.extend_seconds == 30  # default

    def test_extend_decision_accepted(self) -> None:
        td = TimeoutDecision(decision="extend")
        assert td.decision == "extend"
        assert td.extend_seconds == 30

    def test_wait_decision_accepted(self) -> None:
        td = TimeoutDecision(decision="wait")
        assert td.decision == "wait"

    def test_extend_seconds_default_is_30(self) -> None:
        td = TimeoutDecision(decision="kill")
        assert td.extend_seconds == 30

    def test_extend_seconds_minimum_accepted(self) -> None:
        td = TimeoutDecision(decision="extend", extend_seconds=1)
        assert td.extend_seconds == 1

    def test_extend_seconds_maximum_accepted(self) -> None:
        td = TimeoutDecision(decision="extend", extend_seconds=300)
        assert td.extend_seconds == 300

    def test_extend_seconds_below_minimum_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TimeoutDecision(decision="extend", extend_seconds=0)

    def test_extend_seconds_above_maximum_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TimeoutDecision(decision="extend", extend_seconds=301)

    def test_invalid_decision_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TimeoutDecision.model_validate({"decision": "snooze"})

    def test_missing_decision_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TimeoutDecision.model_validate({})

    def test_extend_seconds_custom_value(self) -> None:
        td = TimeoutDecision(decision="extend", extend_seconds=60)
        assert td.extend_seconds == 60

    def test_model_validate_from_dict(self) -> None:
        td = TimeoutDecision.model_validate({"decision": "extend", "extend_seconds": 120})
        assert td.decision == "extend"
        assert td.extend_seconds == 120
