"""Unit tests for app.features.mcp.registry.

All tests use either tmp_path (pytest fixture) or monkeypatch to avoid touching
the real filesystem at /etc/adeptus/mcp.yaml.  The module-level singleton is
reset before every test via _reset_registry().
"""

import textwrap
from collections.abc import Generator
from pathlib import Path

import pytest

from app.features.mcp.registry import (
    ConfigError,
    McpPresetConfig,
    McpServerConfig,
    McpToolConfig,
    _reset_registry,
    get_registry,
    load_registry,
)

# ---------------------------------------------------------------------------
# Autouse fixture: always start with a clean registry
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_registry() -> Generator[None, None, None]:
    _reset_registry()
    yield
    _reset_registry()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_YAML = textwrap.dedent(
    """\
    servers:
      - name: shell-exec
        command: python
        args:
          - -m
          - mcp_servers.shell_exec
        tools:
          - name: run_command
            weight: light
            capability_flags:
              - shell-exec
              - filesystem-write
    """
)


def _write(tmp_path: Path, content: str) -> str:
    p = tmp_path / "mcp.yaml"
    p.write_text(content)
    return str(p)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestValidConfig:
    def test_loads_single_server(self, tmp_path: Path) -> None:
        path = _write(tmp_path, _VALID_YAML)
        load_registry(config_path=path)

        registry = get_registry()
        assert "shell-exec" in registry

    def test_server_config_fields(self, tmp_path: Path) -> None:
        path = _write(tmp_path, _VALID_YAML)
        load_registry(config_path=path)

        server = get_registry()["shell-exec"]
        assert isinstance(server, McpServerConfig)
        assert server.name == "shell-exec"
        assert server.command == "python"
        assert server.args == ["-m", "mcp_servers.shell_exec"]

    def test_tool_declaration(self, tmp_path: Path) -> None:
        path = _write(tmp_path, _VALID_YAML)
        load_registry(config_path=path)

        tools = get_registry()["shell-exec"].tools
        assert len(tools) == 1
        tool = tools[0]
        assert isinstance(tool, McpToolConfig)
        assert tool.name == "run_command"
        assert tool.weight == "light"
        assert "shell-exec" in tool.capability_flags
        assert "filesystem-write" in tool.capability_flags

    def test_args_defaults_to_empty_list(self, tmp_path: Path) -> None:
        yaml_no_args = textwrap.dedent(
            """\
            servers:
              - name: shell-exec
                command: /usr/bin/shell-exec-server
                tools:
                  - name: run_command
                    weight: heavy
                    capability_flags: []
            """
        )
        path = _write(tmp_path, yaml_no_args)
        load_registry(config_path=path)

        server = get_registry()["shell-exec"]
        assert server.args == []

    def test_capability_flags_defaults_to_empty_list(self, tmp_path: Path) -> None:
        yaml_no_flags = textwrap.dedent(
            """\
            servers:
              - name: shell-exec
                command: /usr/bin/shell-exec-server
                tools:
                  - name: run_command
                    weight: light
            """
        )
        path = _write(tmp_path, yaml_no_flags)
        load_registry(config_path=path)

        tool = get_registry()["shell-exec"].tools[0]
        assert tool.capability_flags == []

    def test_multiple_servers(self, tmp_path: Path) -> None:
        yaml_multi = textwrap.dedent(
            """\
            servers:
              - name: shell-exec
                command: /usr/bin/shell-exec-server
                tools:
                  - name: run_command
                    weight: light
                    capability_flags: [shell-exec]
              - name: nmap
                command: /usr/bin/nmap-server
                tools:
                  - name: scan
                    weight: heavy
                    capability_flags: [network-scan]
            """
        )
        path = _write(tmp_path, yaml_multi)
        load_registry(config_path=path)

        registry = get_registry()
        assert set(registry.keys()) == {"shell-exec", "nmap"}
        assert registry["nmap"].tools[0].weight == "heavy"

    def test_get_registry_returns_same_instance_on_repeated_calls(self, tmp_path: Path) -> None:
        path = _write(tmp_path, _VALID_YAML)
        load_registry(config_path=path)

        assert get_registry() is get_registry()

    def test_env_var_used_when_no_explicit_path(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        path = _write(tmp_path, _VALID_YAML)
        monkeypatch.setenv("MCP_CONFIG_PATH", path)
        load_registry()  # no explicit path — should pick up env var

        assert "shell-exec" in get_registry()


# ---------------------------------------------------------------------------
# Missing file
# ---------------------------------------------------------------------------


class TestMissingFile:
    def test_missing_file_raises_config_error(self) -> None:
        with pytest.raises(ConfigError, match="not found"):
            load_registry(config_path="/nonexistent/path/mcp.yaml")

    def test_config_error_is_adeptus_error(self) -> None:
        from app.core.errors import AdeptusError

        with pytest.raises(AdeptusError):
            load_registry(config_path="/nonexistent/path/mcp.yaml")


# ---------------------------------------------------------------------------
# Malformed YAML
# ---------------------------------------------------------------------------


class TestMalformedYaml:
    def test_invalid_yaml_raises_config_error(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "key: [unclosed bracket")
        with pytest.raises(ConfigError, match="[Mm]alformed"):
            load_registry(config_path=path)

    def test_non_mapping_top_level_raises_config_error(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "- just a list item\n")
        with pytest.raises(ConfigError):
            load_registry(config_path=path)


# ---------------------------------------------------------------------------
# Missing mandatory fields
# ---------------------------------------------------------------------------


class TestMissingMandatoryFields:
    def test_missing_servers_key(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "other_key: value\n")
        with pytest.raises(ConfigError, match="'servers'"):
            load_registry(config_path=path)

    def test_servers_not_a_list(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "servers: not-a-list\n")
        with pytest.raises(ConfigError, match="list"):
            load_registry(config_path=path)

    def test_server_missing_name(self, tmp_path: Path) -> None:
        yaml_no_name = textwrap.dedent(
            """\
            servers:
              - command: /usr/bin/shell-exec-server
                tools:
                  - name: run_command
                    weight: light
            """
        )
        path = _write(tmp_path, yaml_no_name)
        with pytest.raises(ConfigError, match="'name'"):
            load_registry(config_path=path)

    def test_server_missing_command(self, tmp_path: Path) -> None:
        yaml_no_cmd = textwrap.dedent(
            """\
            servers:
              - name: shell-exec
                tools:
                  - name: run_command
                    weight: light
            """
        )
        path = _write(tmp_path, yaml_no_cmd)
        with pytest.raises(ConfigError, match="'command'"):
            load_registry(config_path=path)

    def test_server_missing_tools(self, tmp_path: Path) -> None:
        yaml_no_tools = textwrap.dedent(
            """\
            servers:
              - name: shell-exec
                command: /usr/bin/shell-exec-server
            """
        )
        path = _write(tmp_path, yaml_no_tools)
        with pytest.raises(ConfigError, match="'tools'"):
            load_registry(config_path=path)

    def test_tool_missing_name(self, tmp_path: Path) -> None:
        yaml_no_tool_name = textwrap.dedent(
            """\
            servers:
              - name: shell-exec
                command: /usr/bin/shell-exec-server
                tools:
                  - weight: light
            """
        )
        path = _write(tmp_path, yaml_no_tool_name)
        with pytest.raises(ConfigError, match="'name'"):
            load_registry(config_path=path)

    def test_tool_missing_weight(self, tmp_path: Path) -> None:
        yaml_no_weight = textwrap.dedent(
            """\
            servers:
              - name: shell-exec
                command: /usr/bin/shell-exec-server
                tools:
                  - name: run_command
            """
        )
        path = _write(tmp_path, yaml_no_weight)
        with pytest.raises(ConfigError, match="'weight'"):
            load_registry(config_path=path)

    def test_tool_invalid_weight_raises_config_error(self, tmp_path: Path) -> None:
        yaml_bad_weight = textwrap.dedent(
            """\
            servers:
              - name: shell-exec
                command: /usr/bin/shell-exec-server
                tools:
                  - name: run_command
                    weight: medium
            """
        )
        path = _write(tmp_path, yaml_bad_weight)
        with pytest.raises(ConfigError):
            load_registry(config_path=path)

    def test_duplicate_server_names_raises_config_error(self, tmp_path: Path) -> None:
        yaml_dup = textwrap.dedent(
            """\
            servers:
              - name: shell-exec
                command: /usr/bin/shell-exec-server-1
                tools:
                  - name: run_command
                    weight: light
              - name: shell-exec
                command: /usr/bin/shell-exec-server-2
                tools:
                  - name: run_command
                    weight: light
            """
        )
        path = _write(tmp_path, yaml_dup)
        with pytest.raises(ConfigError, match="[Dd]uplicate"):
            load_registry(config_path=path)


# ---------------------------------------------------------------------------
# get_registry before load_registry
# ---------------------------------------------------------------------------


class TestGetRegistryBeforeLoad:
    def test_raises_config_error_when_not_loaded(self) -> None:
        with pytest.raises(ConfigError, match="not been loaded"):
            get_registry()


# ---------------------------------------------------------------------------
# Tool presets and arg_schema (Part A of Slice 04, Task 4)
# ---------------------------------------------------------------------------


class TestToolPresetsAndArgSchema:
    def test_tool_with_presets_and_arg_schema_parses_correctly(self, tmp_path: Path) -> None:
        yaml_with_presets = textwrap.dedent(
            """\
            servers:
              - name: httpx
                command: python
                args:
                  - -m
                  - mcp_servers.httpx
                tools:
                  - name: run_httpx
                    weight: light
                    capability_flags:
                      - network
                    presets:
                      - name: quick
                        description: Quick scan
                        args:
                          flags: ["-sc", "-title"]
                      - name: full
                        description: Full scan
                        args:
                          flags: ["-sc", "-title", "-tech-detect", "-follow-redirects"]
                    arg_schema:
                      type: object
                      properties:
                        target:
                          type: string
            """
        )
        path = _write(tmp_path, yaml_with_presets)
        load_registry(config_path=path)

        tool = get_registry()["httpx"].tools[0]
        assert isinstance(tool, McpToolConfig)
        assert tool.name == "run_httpx"
        assert tool.weight == "light"
        assert tool.capability_flags == ["network"]

        assert len(tool.presets) == 2
        quick = tool.presets[0]
        assert isinstance(quick, McpPresetConfig)
        assert quick.name == "quick"
        assert quick.description == "Quick scan"
        assert quick.args == {"flags": ["-sc", "-title"]}

        full = tool.presets[1]
        assert full.name == "full"
        assert "-tech-detect" in full.args["flags"]

        assert tool.arg_schema == {
            "type": "object",
            "properties": {"target": {"type": "string"}},
        }

    def test_tool_without_presets_and_arg_schema_defaults_to_empty(self, tmp_path: Path) -> None:
        yaml_no_extras = textwrap.dedent(
            """\
            servers:
              - name: shell-exec
                command: python
                tools:
                  - name: run_command
                    weight: light
                    capability_flags:
                      - shell-exec
            """
        )
        path = _write(tmp_path, yaml_no_extras)
        load_registry(config_path=path)

        tool = get_registry()["shell-exec"].tools[0]
        assert tool.presets == []
        assert tool.arg_schema == {}

    def test_malformed_preset_entry_raises_config_error(self, tmp_path: Path) -> None:
        yaml_bad_preset = textwrap.dedent(
            """\
            servers:
              - name: httpx
                command: python
                tools:
                  - name: run_httpx
                    weight: light
                    presets:
                      - just-a-string
            """
        )
        path = _write(tmp_path, yaml_bad_preset)
        with pytest.raises(ConfigError, match="mapping"):
            load_registry(config_path=path)

    def test_preset_missing_name_raises_config_error(self, tmp_path: Path) -> None:
        yaml_no_name = textwrap.dedent(
            """\
            servers:
              - name: httpx
                command: python
                tools:
                  - name: run_httpx
                    weight: light
                    presets:
                      - description: Missing name field
                        args: {}
            """
        )
        path = _write(tmp_path, yaml_no_name)
        with pytest.raises(ConfigError, match="'name'"):
            load_registry(config_path=path)

    def test_preset_with_no_description_defaults_to_none(self, tmp_path: Path) -> None:
        yaml_no_desc = textwrap.dedent(
            """\
            servers:
              - name: httpx
                command: python
                tools:
                  - name: run_httpx
                    weight: light
                    presets:
                      - name: quick
                        args:
                          flags: ["-sc"]
            """
        )
        path = _write(tmp_path, yaml_no_desc)
        load_registry(config_path=path)

        preset = get_registry()["httpx"].tools[0].presets[0]
        assert preset.description is None
        assert preset.args == {"flags": ["-sc"]}

    def test_preset_with_no_args_defaults_to_empty_dict(self, tmp_path: Path) -> None:
        yaml_no_args = textwrap.dedent(
            """\
            servers:
              - name: httpx
                command: python
                tools:
                  - name: run_httpx
                    weight: light
                    presets:
                      - name: quick
            """
        )
        path = _write(tmp_path, yaml_no_args)
        load_registry(config_path=path)

        preset = get_registry()["httpx"].tools[0].presets[0]
        assert preset.args == {}
