"""Unit tests for chat.tool_calling (Slice 16): normalization + mode + tool shapes."""

from collections.abc import Iterator

import pytest

from app.core.config import get_settings
from app.features.chat import tool_calling
from app.features.chat.tool_calling import ProposedToolCall


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("ADEPTUS_ADMIN_USER", "admin")
    monkeypatch.setenv("ADEPTUS_ADMIN_PASSWORD_HASH", "x")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_to_proposed_action_maps_fields() -> None:
    action = tool_calling.to_proposed_action(
        {
            "server": "shell-exec",
            "tool": "run",
            "args": {"cmd": "id"},
            "preset": "aggressive",
            "rationale": "enumerate",
        }
    )
    assert action is not None
    assert action.server_name == "shell-exec"
    assert action.tool_name == "run"
    assert action.args == {"cmd": "id"}
    assert action.preset_name == "aggressive"
    assert action.rationale == "enumerate"


def test_to_proposed_action_defaults_missing_optionals() -> None:
    action = tool_calling.to_proposed_action({"server": "s", "tool": "t"})
    assert action is not None
    assert action.args == {}
    assert action.preset_name is None
    assert action.rationale is None


@pytest.mark.parametrize(
    "bad",
    [
        {"tool": "run"},  # no server
        {"server": "s"},  # no tool
        {"server": "", "tool": "t"},  # empty server
        {"server": 5, "tool": "t"},  # wrong type
    ],
)
def test_to_proposed_action_drops_malformed(bad: dict[str, object]) -> None:
    assert tool_calling.to_proposed_action(bad) is None


def test_to_proposed_actions_filters_other_tools() -> None:
    calls = [
        ProposedToolCall(name="other_tool", arguments={"server": "s", "tool": "t"}),
        ProposedToolCall(name="propose_command", arguments={"server": "s", "tool": "t"}),
        ProposedToolCall(name="propose_command", arguments={"tool": "t"}),  # malformed → dropped
    ]
    actions = tool_calling.to_proposed_actions(calls)
    assert len(actions) == 1
    assert actions[0].server_name == "s"


def test_resolve_mode_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADEPTUS_TOOLCALL_MODE", "fallback")
    get_settings.cache_clear()
    assert tool_calling.resolve_mode() == "fallback"


@pytest.mark.parametrize("value", ["auto", "native", "ANYTHING-ELSE"])
def test_resolve_mode_defaults_to_native(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("ADEPTUS_TOOLCALL_MODE", value)
    get_settings.cache_clear()
    assert tool_calling.resolve_mode() == "native"


def test_tool_shapes() -> None:
    assert tool_calling.ollama_tools()[0]["function"]["name"] == "propose_command"
    assert tool_calling.anthropic_tools()[0]["name"] == "propose_command"
    # Anthropic uses input_schema; Ollama uses parameters under function.
    assert "input_schema" in tool_calling.anthropic_tools()[0]
    assert "parameters" in tool_calling.ollama_tools()[0]["function"]
