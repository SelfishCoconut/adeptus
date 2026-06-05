"""Static MCP server registry.

Loads the YAML config from the path given by the ``MCP_CONFIG_PATH`` environment
variable (default ``/etc/adeptus/mcp.yaml``) and caches it as a module-level
singleton.

Typical startup sequence (called from the FastAPI lifespan hook):

    from app.features.mcp.registry import load_registry, get_registry

    load_registry()          # raises ConfigError on any problem
    reg = get_registry()     # returns dict[str, McpServerConfig]

Tests that need to vary the config can call ``_reset_registry()`` to clear the
cache between cases.
"""

from __future__ import annotations

import os
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.errors import AdeptusError

# ---------------------------------------------------------------------------
# Domain exception
# ---------------------------------------------------------------------------


class ConfigError(AdeptusError):
    """Raised when the MCP config file cannot be loaded or is invalid."""

    def __init__(self, message: str = "MCP configuration error") -> None:
        super().__init__(message)


# ---------------------------------------------------------------------------
# Config models
# ---------------------------------------------------------------------------


class McpPresetConfig(BaseModel):
    """A named preset declared by a tool in the static manifest."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)


class McpToolConfig(BaseModel):
    """A single tool declared by an MCP server in the static manifest."""

    model_config = ConfigDict(frozen=True)

    name: str
    weight: str
    capability_flags: list[str] = Field(default_factory=list)
    presets: list[McpPresetConfig] = Field(default_factory=list)
    arg_schema: dict[str, Any] = Field(default_factory=dict)

    @field_validator("weight")
    @classmethod
    def validate_weight(cls, v: str) -> str:
        if v not in {"light", "heavy"}:
            raise ValueError(f"weight must be 'light' or 'heavy', got {v!r}")
        return v


class McpServerConfig(BaseModel):
    """Configuration for a single MCP server as declared in the YAML manifest."""

    model_config = ConfigDict(frozen=True)

    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    tools: list[McpToolConfig]


# ---------------------------------------------------------------------------
# Registry singleton
# ---------------------------------------------------------------------------

_registry: dict[str, McpServerConfig] | None = None

_DEFAULT_CONFIG_PATH = "/etc/adeptus/mcp.yaml"


def _reset_registry() -> None:
    """Clear the cached registry.  For use in tests only."""
    global _registry
    _registry = None


def load_registry(config_path: str | None = None) -> None:
    """Parse the YAML config and populate the module-level registry singleton.

    Args:
        config_path: Override the path (for testing).  When *None* the value of
            ``MCP_CONFIG_PATH`` env-var is used, falling back to the default path.

    Raises:
        ConfigError: If the file is missing, YAML is malformed, or any mandatory
            field is absent / invalid.
    """
    global _registry

    resolved_path: str = (
        config_path
        if config_path is not None
        else os.environ.get("MCP_CONFIG_PATH", _DEFAULT_CONFIG_PATH)
    )

    try:
        with open(resolved_path) as fh:
            raw = fh.read()
    except FileNotFoundError as exc:
        raise ConfigError(f"MCP config file not found: {resolved_path}") from exc
    except OSError as exc:
        raise ConfigError(f"Cannot read MCP config file {resolved_path!r}: {exc}") from exc

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Malformed YAML in MCP config {resolved_path!r}: {exc}") from exc

    _registry = _parse_config(data, resolved_path)
    _validate_manifests_for_approvals(_registry)


def _validate_manifests_for_approvals(registry: dict[str, McpServerConfig]) -> None:
    """Slice 16 hook: flag tools with no manifest classification at load time.

    The two-tier classifier treats a tool with no present weight as dangerous via the
    ``unclassified_manifest`` escape hatch (Resolved decision 2); this surfaces such a
    mis-manifested tool as a loud startup warning so the admin can fix the server
    manifest. Imported locally to avoid coupling the mcp registry to the approvals
    feature at module-load time.
    """
    from app.features.approvals import classifier

    tools = [
        (
            f"{server.name}/{tool.name}",
            classifier.ToolConfig(
                weight=tool.weight, capability_flags=tuple(tool.capability_flags)
            ),
        )
        for server in registry.values()
        for tool in server.tools
    ]
    classifier.validate_tool_manifests(tools)


def get_registry() -> dict[str, McpServerConfig]:
    """Return the loaded registry singleton.

    Raises:
        ConfigError: If :func:`load_registry` has not been called yet.
    """
    if _registry is None:
        raise ConfigError("MCP registry has not been loaded — call load_registry() first")
    return _registry


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_config(data: Any, path: str) -> dict[str, McpServerConfig]:
    """Validate and build the registry dict from raw YAML data.

    Args:
        data: The deserialised YAML (may be anything — we validate shape here).
        path: Config file path (used only in error messages).

    Returns:
        A dict mapping server name → McpServerConfig.

    Raises:
        ConfigError: On any structural or value validation problem.
    """
    if not isinstance(data, dict):
        raise ConfigError(
            f"MCP config {path!r} must be a YAML mapping at the top level; "
            f"got {type(data).__name__}"
        )

    servers_raw = data.get("servers")
    if servers_raw is None:
        raise ConfigError(f"MCP config {path!r} is missing mandatory 'servers' key")
    if not isinstance(servers_raw, list):
        raise ConfigError(
            f"MCP config {path!r}: 'servers' must be a list; got {type(servers_raw).__name__}"
        )

    registry: dict[str, McpServerConfig] = {}

    for idx, entry in enumerate(servers_raw):
        server = _parse_server_entry(entry, idx, path)
        if server.name in registry:
            raise ConfigError(
                f"MCP config {path!r}: duplicate server name {server.name!r} at index {idx}"
            )
        registry[server.name] = server

    return registry


def _parse_server_entry(entry: Any, idx: int, path: str) -> McpServerConfig:
    """Parse and validate a single server entry from the YAML list.

    Raises:
        ConfigError: If any mandatory field is missing or has an invalid value.
    """
    if not isinstance(entry, dict):
        raise ConfigError(
            f"MCP config {path!r}: servers[{idx}] must be a mapping; got {type(entry).__name__}"
        )

    for field in ("name", "command", "tools"):
        if field not in entry:
            raise ConfigError(
                f"MCP config {path!r}: servers[{idx}] is missing mandatory field {field!r}"
            )

    if not isinstance(entry["tools"], list):
        raise ConfigError(
            f"MCP config {path!r}: servers[{idx}].tools must be a list; "
            f"got {type(entry['tools']).__name__}"
        )

    tools: list[McpToolConfig] = []
    for tool_idx, tool_entry in enumerate(entry["tools"]):
        tool = _parse_tool_entry(tool_entry, idx, tool_idx, path)
        tools.append(tool)

    try:
        return McpServerConfig(
            name=str(entry["name"]),
            command=str(entry["command"]),
            args=list(entry.get("args", [])),
            tools=tools,
        )
    except Exception as exc:
        raise ConfigError(f"MCP config {path!r}: servers[{idx}] validation failed: {exc}") from exc


def _parse_tool_entry(
    entry: Any,
    server_idx: int,
    tool_idx: int,
    path: str,
) -> McpToolConfig:
    """Parse and validate a single tool entry.

    Raises:
        ConfigError: If any mandatory field is missing or has an invalid value.
    """
    if not isinstance(entry, dict):
        raise ConfigError(
            f"MCP config {path!r}: servers[{server_idx}].tools[{tool_idx}] "
            f"must be a mapping; got {type(entry).__name__}"
        )

    for field in ("name", "weight"):
        if field not in entry:
            raise ConfigError(
                f"MCP config {path!r}: servers[{server_idx}].tools[{tool_idx}] "
                f"is missing mandatory field {field!r}"
            )

    # Parse optional presets list.
    raw_presets = entry.get("presets", [])
    if not isinstance(raw_presets, list):
        raise ConfigError(
            f"MCP config {path!r}: servers[{server_idx}].tools[{tool_idx}].presets "
            f"must be a list; got {type(raw_presets).__name__}"
        )
    presets: list[McpPresetConfig] = []
    for p_idx, p_entry in enumerate(raw_presets):
        if not isinstance(p_entry, dict) or "name" not in p_entry:
            raise ConfigError(
                f"MCP config {path!r}: servers[{server_idx}].tools[{tool_idx}]"
                f".presets[{p_idx}] must be a mapping with at least 'name'"
            )
        try:
            presets.append(
                McpPresetConfig(
                    name=str(p_entry["name"]),
                    description=p_entry.get("description"),
                    args=dict(p_entry.get("args", {})),
                )
            )
        except Exception as exc:
            raise ConfigError(
                f"MCP config {path!r}: servers[{server_idx}].tools[{tool_idx}]"
                f".presets[{p_idx}] validation failed: {exc}"
            ) from exc

    # Parse optional arg_schema mapping.
    raw_arg_schema = entry.get("arg_schema", {})
    if not isinstance(raw_arg_schema, dict):
        raise ConfigError(
            f"MCP config {path!r}: servers[{server_idx}].tools[{tool_idx}].arg_schema "
            f"must be a mapping; got {type(raw_arg_schema).__name__}"
        )

    try:
        return McpToolConfig(
            name=str(entry["name"]),
            weight=str(entry["weight"]),
            capability_flags=list(entry.get("capability_flags", [])),
            presets=presets,
            arg_schema=raw_arg_schema,
        )
    except Exception as exc:
        raise ConfigError(
            f"MCP config {path!r}: servers[{server_idx}].tools[{tool_idx}] validation failed: {exc}"
        ) from exc
