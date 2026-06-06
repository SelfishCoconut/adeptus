"""Unit tests for the sandbox guard in app.features.mcp.service.

Tests cover:
  - ADEPTUS_ENV=dev: non-sandbox target raises SandboxGuardViolation.
  - ADEPTUS_ENV=dev: sandbox hosts (localhost, 127.0.0.1, juice-shop) are allowed.
  - ADEPTUS_ENV=dev: bare ``localhost`` (no scheme) is allowed.
  - ADEPTUS_ENV=production: any target passes (guard disabled).
  - ADEPTUS_ENV unset / garbage value: treated as guarded (fail-closed, Risk 5).
  - args without a ``target`` key: no raise regardless of env.
  - execute_tool_run with async_mode=True and a non-sandbox target raises
    SandboxGuardViolation and creates NO tool_runs row.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.features.mcp.service import (
    SandboxGuardViolation,
    _enforce_sandbox_guard,
    execute_tool_run,
)

# ---------------------------------------------------------------------------
# _enforce_sandbox_guard — direct unit tests
# ---------------------------------------------------------------------------


def _with_adeptus_env(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    """Set or unset ADEPTUS_ENV for a single test."""
    if value is None:
        monkeypatch.delenv("ADEPTUS_ENV", raising=False)
    else:
        monkeypatch.setenv("ADEPTUS_ENV", value)


# -- dev env, outside-sandbox targets raise -----------------------------------


def test_dev_http_external_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADEPTUS_ENV=dev: http://example.com raises SandboxGuardViolation."""
    _with_adeptus_env(monkeypatch, "dev")
    with pytest.raises(SandboxGuardViolation, match="example.com"):
        _enforce_sandbox_guard({"target": "http://example.com"})


def test_dev_https_external_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADEPTUS_ENV=dev: https://example.com raises SandboxGuardViolation."""
    _with_adeptus_env(monkeypatch, "dev")
    with pytest.raises(SandboxGuardViolation, match="example.com"):
        _enforce_sandbox_guard({"target": "https://example.com"})


def test_dev_http_external_with_port_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADEPTUS_ENV=dev: http://evil.host:8080 raises SandboxGuardViolation."""
    _with_adeptus_env(monkeypatch, "dev")
    with pytest.raises(SandboxGuardViolation, match="evil.host"):
        _enforce_sandbox_guard({"target": "http://evil.host:8080"})


# -- dev env, sandbox targets are allowed -------------------------------------


def test_dev_localhost_url_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADEPTUS_ENV=dev: http://localhost:3000 does not raise."""
    _with_adeptus_env(monkeypatch, "dev")
    _enforce_sandbox_guard({"target": "http://localhost:3000"})  # must not raise


def test_dev_127_0_0_1_url_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADEPTUS_ENV=dev: http://127.0.0.1:8080 does not raise."""
    _with_adeptus_env(monkeypatch, "dev")
    _enforce_sandbox_guard({"target": "http://127.0.0.1:8080"})  # must not raise


def test_dev_juice_shop_url_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADEPTUS_ENV=dev: http://juice-shop:3000 does not raise."""
    _with_adeptus_env(monkeypatch, "dev")
    _enforce_sandbox_guard({"target": "http://juice-shop:3000"})  # must not raise


def test_dev_bare_localhost_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADEPTUS_ENV=dev: bare ``localhost`` (no scheme) does not raise."""
    _with_adeptus_env(monkeypatch, "dev")
    _enforce_sandbox_guard({"target": "localhost"})  # must not raise


def test_dev_bare_localhost_with_port_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADEPTUS_ENV=dev: bare ``localhost:3000`` does not raise."""
    _with_adeptus_env(monkeypatch, "dev")
    _enforce_sandbox_guard({"target": "localhost:3000"})  # must not raise


# -- nmap coverage (Slice 26): the generic guard covers nmap's bare-host target ----


def test_dev_nmap_bare_sandbox_host_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADEPTUS_ENV=dev: nmap against the bare ``juice-shop`` sandbox host is allowed."""
    _with_adeptus_env(monkeypatch, "dev")
    _enforce_sandbox_guard({"target": "juice-shop"})  # must not raise


def test_dev_nmap_external_host_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADEPTUS_ENV=dev: nmap against an external host (e.g. scanme.nmap.org) is refused.

    nmap takes a bare host as its target, so the generic, tool-agnostic guard
    (keyed on ``args['target']``) covers run_nmap with no nmap-specific code.
    """
    _with_adeptus_env(monkeypatch, "dev")
    with pytest.raises(SandboxGuardViolation, match="scanme.nmap.org"):
        _enforce_sandbox_guard({"target": "scanme.nmap.org"})


# -- userinfo smuggling must not bypass the guard -----------------------------


def test_dev_bare_userinfo_smuggle_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare ``localhost:3000@evil.com`` must resolve to evil.com and be blocked.

    Naive ``split(':')[0]`` parsing would read the sandbox host ``localhost`` and
    let it through, but the httpx binary scans ``evil.com`` — so the guard must
    extract the real authority after the userinfo ``@``.
    """
    _with_adeptus_env(monkeypatch, "dev")
    with pytest.raises(SandboxGuardViolation, match="evil.com"):
        _enforce_sandbox_guard({"target": "localhost:3000@evil.com"})


def test_dev_schemed_userinfo_smuggle_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """``http://localhost@evil.com`` resolves to evil.com and is blocked."""
    _with_adeptus_env(monkeypatch, "dev")
    with pytest.raises(SandboxGuardViolation, match="evil.com"):
        _enforce_sandbox_guard({"target": "http://localhost@evil.com"})


def test_dev_bare_userinfo_password_smuggle_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """``juice-shop:@attacker.com`` resolves to attacker.com and is blocked."""
    _with_adeptus_env(monkeypatch, "dev")
    with pytest.raises(SandboxGuardViolation, match="attacker.com"):
        _enforce_sandbox_guard({"target": "127.0.0.1:1234@attacker.com"})


# -- production env, any target passes ----------------------------------------


def test_production_external_target_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADEPTUS_ENV=production: https://example.com does not raise (guard disabled)."""
    _with_adeptus_env(monkeypatch, "production")
    _enforce_sandbox_guard({"target": "https://example.com"})  # must not raise


def test_production_arbitrary_host_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADEPTUS_ENV=production: any host passes because the guard is disabled."""
    _with_adeptus_env(monkeypatch, "production")
    _enforce_sandbox_guard({"target": "http://internal.corp:443/scan"})  # must not raise


# -- unset / garbage env value, fail-closed (Risk 5) -------------------------


def test_unset_env_is_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADEPTUS_ENV unset: treated as ``dev`` (fail-closed) — external target raises."""
    _with_adeptus_env(monkeypatch, None)
    with pytest.raises(SandboxGuardViolation):
        _enforce_sandbox_guard({"target": "https://example.com"})


def test_garbage_env_value_is_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADEPTUS_ENV=garbage: unrecognised value treated as guarded — external raises."""
    _with_adeptus_env(monkeypatch, "staging")
    with pytest.raises(SandboxGuardViolation):
        _enforce_sandbox_guard({"target": "https://example.com"})


# -- args without a target key: no raise regardless of env --------------------


def test_no_target_key_no_raise_in_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    """args without a ``target`` key do not raise in dev env."""
    _with_adeptus_env(monkeypatch, "dev")
    _enforce_sandbox_guard({"command": "echo hello"})  # run_command style — must not raise


def test_no_target_key_no_raise_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """args without a ``target`` key do not raise in production env."""
    _with_adeptus_env(monkeypatch, "production")
    _enforce_sandbox_guard({"command": "echo hello"})  # must not raise


def test_empty_target_no_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty string ``target`` is not guarded (no host to check)."""
    _with_adeptus_env(monkeypatch, "dev")
    _enforce_sandbox_guard({"target": ""})  # must not raise


def test_non_string_target_no_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-string ``target`` value is not guarded."""
    _with_adeptus_env(monkeypatch, "dev")
    _enforce_sandbox_guard({"target": 42})  # must not raise


# -- SandboxGuardViolation is a subclass of ForbiddenError --------------------


def test_sandbox_guard_violation_is_forbidden_error() -> None:
    """SandboxGuardViolation subclasses ForbiddenError so the handler maps it to 403."""
    from app.core.errors import ForbiddenError

    exc = SandboxGuardViolation("test message")
    assert isinstance(exc, ForbiddenError)
    assert exc.message == "test message"


# ---------------------------------------------------------------------------
# execute_tool_run integration: sandbox guard fires before DB row creation
# ---------------------------------------------------------------------------


@pytest.fixture
def _mock_registry_and_membership() -> Iterator[None]:
    """Patch registry lookup and membership check to pass cleanly."""
    from app.features.mcp.registry import McpToolConfig

    def _make_tool_config() -> McpToolConfig:
        return McpToolConfig(name="run_httpx", weight="light", capability_flags=["network"])

    server_cfg = MagicMock()
    server_cfg.tools = [_make_tool_config()]

    with (
        patch(
            "app.features.mcp.service.eng_repo.get_engagement_for_member",
            new_callable=AsyncMock,
            return_value=(MagicMock(), MagicMock()),
        ),
        patch(
            "app.features.mcp.service.get_registry",
            return_value={"httpx": server_cfg},
        ),
    ):
        yield


@pytest.mark.asyncio
async def test_execute_tool_run_async_non_sandbox_raises_no_row(
    monkeypatch: pytest.MonkeyPatch,
    _mock_registry_and_membership: None,
) -> None:
    """execute_tool_run with async_mode=True and a non-sandbox target in dev raises
    SandboxGuardViolation and creates NO tool_runs row (guard fires before DB insert).
    """
    _with_adeptus_env(monkeypatch, "dev")
    create_mock = AsyncMock()

    with (
        patch("app.features.mcp.service.mcp_repo.create_tool_run", create_mock),
        pytest.raises(SandboxGuardViolation),
    ):
        await execute_tool_run(
            AsyncMock(),  # db session — never reaches a real query
            engagement_id=uuid4(),
            server_name="httpx",
            tool_name="run_httpx",
            args={"target": "https://example.com"},
            timeout_seconds=30,
            user_id=uuid4(),
            async_mode=True,
        )

    create_mock.assert_not_called()


@pytest.mark.asyncio
async def test_execute_tool_run_sync_non_sandbox_raises_no_row(
    monkeypatch: pytest.MonkeyPatch,
    _mock_registry_and_membership: None,
) -> None:
    """execute_tool_run sync path: non-sandbox target raises before row creation."""
    _with_adeptus_env(monkeypatch, "dev")
    create_mock = AsyncMock()

    with (
        patch("app.features.mcp.service.mcp_repo.create_tool_run", create_mock),
        pytest.raises(SandboxGuardViolation),
    ):
        await execute_tool_run(
            AsyncMock(),
            engagement_id=uuid4(),
            server_name="httpx",
            tool_name="run_httpx",
            args={"target": "https://example.com"},
            timeout_seconds=30,
            user_id=uuid4(),
            async_mode=False,
        )

    create_mock.assert_not_called()


@pytest.mark.asyncio
async def test_execute_tool_run_sandbox_target_proceeds_in_dev(
    monkeypatch: pytest.MonkeyPatch,
    _mock_registry_and_membership: None,
) -> None:
    """execute_tool_run: sandbox target in dev passes the guard and reaches create_tool_run."""
    _with_adeptus_env(monkeypatch, "dev")

    tool_run_mock = MagicMock()
    tool_run_mock.id = uuid4()
    tool_run_mock.engagement_id = uuid4()
    tool_run_mock.server_name = "httpx"
    tool_run_mock.tool_name = "run_httpx"
    tool_run_mock.exit_code = None
    tool_run_mock.stdout = ""
    tool_run_mock.stderr = ""
    from datetime import UTC, datetime

    tool_run_mock.started_at = datetime.now(tz=UTC)
    tool_run_mock.finished_at = None
    tool_run_mock.status = "running"
    tool_run_mock.preset_name = None

    create_mock = AsyncMock(return_value=tool_run_mock)
    db_mock = AsyncMock()

    # Patch _stream_to_channel so no background coroutine is created or left unawaited.
    stream_mock = AsyncMock(return_value=None)
    with (
        patch("app.features.mcp.service.mcp_repo.create_tool_run", create_mock),
        patch("app.features.mcp.service._stream_to_channel", stream_mock),
    ):
        result = await execute_tool_run(
            db_mock,
            engagement_id=uuid4(),
            server_name="httpx",
            tool_name="run_httpx",
            args={"target": "http://localhost:3000"},
            timeout_seconds=30,
            user_id=uuid4(),
            async_mode=True,
        )

    create_mock.assert_called_once()
    assert result.status == "running"
