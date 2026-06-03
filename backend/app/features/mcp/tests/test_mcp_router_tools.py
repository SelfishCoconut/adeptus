"""Router-layer tests for GET /api/v1/mcp/tools.

Verifies that:
- An authenticated user receives 200 with a flat list of ToolDescriptor.
- The list includes a descriptor for "httpx" / "run_httpx" with presets and
  arg_schema populated from the mocked registry.
- An unauthenticated request is rejected with 401.

The registry is mocked via monkeypatch on service.get_registry so no real
YAML file is needed.  The test app setup mirrors test_mcp_router.py exactly:
same in-memory SQLite engine, same fixture hierarchy, same login approach.
"""

from __future__ import annotations

import textwrap
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Column, ColumnDefault, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import Base, get_db
from app.core.errors import register_error_handlers
from app.features.auth import models as auth_models
from app.features.auth import repository as auth_repo
from app.features.auth.router import router as auth_router
from app.features.engagements import models as eng_models  # noqa: F401
from app.features.mcp import models as mcp_models  # noqa: F401
from app.features.mcp.registry import (
    _reset_registry,
    load_registry,
)
from app.features.mcp.router import router as mcp_router

_hasher = PasswordHasher()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_yaml(tmp_path: Path, content: str) -> str:
    p = tmp_path / "mcp.yaml"
    p.write_text(content)
    return str(p)


_HTTPX_YAML = textwrap.dedent(
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
              required: [target]
              properties:
                target:
                  type: string
                flags:
                  type: array
                  items:
                    type: string
                timeout_seconds:
                  type: integer
          - name: run_httpx_heavy
            weight: heavy
            capability_flags:
              - network
            arg_schema:
              type: object
              required: [target]
              properties:
                target:
                  type: string
                hold_seconds:
                  type: number
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
              - network
    """
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def app_and_db(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> AsyncGenerator[tuple[FastAPI, async_sessionmaker[AsyncSession]], None]:
    """Test FastAPI app with a fresh SQLite in-memory database and a loaded
    registry containing an httpx server with two presets."""
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("ADEPTUS_ADMIN_USER", "admin")
    monkeypatch.setenv(
        "ADEPTUS_ADMIN_PASSWORD_HASH",
        "$argon2id$v=19$m=65536,t=3,p=4$dGVzdHNhbHQ$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )

    get_settings.cache_clear()

    # Patch Postgres-specific column types for SQLite compatibility.
    user_id_col: Column = auth_models.User.__table__.c.id  # type: ignore[assignment]
    user_id_col.default = ColumnDefault(uuid4)

    ip_col: Column = auth_models.Session.__table__.c.ip  # type: ignore[assignment]
    ip_col.type = Text()

    eng_id_col: Column = eng_models.Engagement.__table__.c.id  # type: ignore[assignment]
    eng_id_col.default = ColumnDefault(uuid4)

    tool_run_id_col: Column = mcp_models.ToolRun.__table__.c.id  # type: ignore[assignment]
    tool_run_id_col.default = ColumnDefault(uuid4)

    # Load a real registry from a temp YAML file containing httpx + shell-exec.
    _reset_registry()
    cfg_path = _write_yaml(tmp_path, _HTTPX_YAML)
    load_registry(config_path=cfg_path)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(mcp_router)
    register_error_handlers(app)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    yield app, factory

    _reset_registry()
    get_settings.cache_clear()
    await engine.dispose()


@pytest_asyncio.fixture
async def regular_user(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> auth_models.User:
    """Insert a regular (non-admin) user directly into the test DB."""
    _, factory = app_and_db
    pw_hash = _hasher.hash("secretpass")
    async with factory() as session:
        user = await auth_repo.create_user(
            session,
            username="regular",
            password_hash=pw_hash,
            role="user",
        )
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def regular_client(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    regular_user: auth_models.User,
) -> AsyncGenerator[AsyncClient, None]:
    """Authenticated AsyncClient logged in as a regular (non-admin) user."""
    app, _ = app_and_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "regular", "password": "secretpass"},
        )
        assert resp.status_code == 200, resp.text
        yield client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_mcp_tools_returns_200_for_authenticated_user(
    regular_client: AsyncClient,
) -> None:
    """Any authenticated user receives 200 with a list of ToolDescriptor."""
    resp = await regular_client.get("/api/v1/mcp/tools")

    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) >= 1


@pytest.mark.asyncio
async def test_list_mcp_tools_includes_httpx_descriptor(
    regular_client: AsyncClient,
) -> None:
    """Response includes an httpx / run_httpx descriptor with correct fields."""
    resp = await regular_client.get("/api/v1/mcp/tools")

    assert resp.status_code == 200
    body: list[dict[str, Any]] = resp.json()

    httpx_descriptors = [
        d for d in body if d["server_name"] == "httpx" and d["tool_name"] == "run_httpx"
    ]
    assert len(httpx_descriptors) == 1, "Expected exactly one descriptor for httpx/run_httpx"

    descriptor = httpx_descriptors[0]
    assert descriptor["tool_name"] == "run_httpx"
    assert descriptor["weight"] == "light"
    assert "network" in descriptor["capability_flags"]

    # Presets: quick and full
    preset_names = [p["name"] for p in descriptor["presets"]]
    assert "quick" in preset_names
    assert "full" in preset_names

    quick = next(p for p in descriptor["presets"] if p["name"] == "quick")
    assert quick["args"]["flags"] == ["-sc", "-title"]

    full = next(p for p in descriptor["presets"] if p["name"] == "full")
    assert "-tech-detect" in full["args"]["flags"]

    # arg_schema is a non-empty mapping
    assert isinstance(descriptor["arg_schema"], dict)
    assert descriptor["arg_schema"]["type"] == "object"


@pytest.mark.asyncio
async def test_list_mcp_tools_includes_shell_exec_descriptor(
    regular_client: AsyncClient,
) -> None:
    """shell-exec server appears in the list with empty presets and arg_schema."""
    resp = await regular_client.get("/api/v1/mcp/tools")

    assert resp.status_code == 200
    body: list[dict[str, Any]] = resp.json()

    shell_descriptors = [d for d in body if d["server_name"] == "shell-exec"]
    assert len(shell_descriptors) == 1

    descriptor = shell_descriptors[0]
    assert descriptor["tool_name"] == "run_command"
    assert descriptor["presets"] == []
    assert descriptor["arg_schema"] == {}


@pytest.mark.asyncio
async def test_list_mcp_tools_returns_401_for_unauthenticated(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    """Unauthenticated request to GET /api/v1/mcp/tools returns 401."""
    app, _ = app_and_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await client.get("/api/v1/mcp/tools")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_mcp_tools_returns_descriptors_in_registry_order(
    regular_client: AsyncClient,
) -> None:
    """Descriptors appear in the same order as the registry (httpx first, then shell-exec)."""
    resp = await regular_client.get("/api/v1/mcp/tools")

    assert resp.status_code == 200
    body: list[dict[str, Any]] = resp.json()

    server_names = [d["server_name"] for d in body]
    # httpx is declared first in _HTTPX_YAML
    assert server_names.index("httpx") < server_names.index("shell-exec")


@pytest.mark.asyncio
async def test_list_mcp_tools_includes_run_httpx_heavy_with_heavy_weight(
    regular_client: AsyncClient,
) -> None:
    """GET /api/v1/mcp/tools surfaces run_httpx_heavy with weight == 'heavy'.

    Manifest/registry unit test (Task 7): confirms that adding the tool to the
    static MCP config causes it to be parsed and returned by the tools endpoint
    with the correct weight so the backend admission manager classifies it as a
    heavy run.
    """
    resp = await regular_client.get("/api/v1/mcp/tools")

    assert resp.status_code == 200
    body: list[dict[str, Any]] = resp.json()

    heavy_descriptors = [
        d for d in body if d["server_name"] == "httpx" and d["tool_name"] == "run_httpx_heavy"
    ]
    assert len(heavy_descriptors) == 1, "Expected exactly one descriptor for httpx/run_httpx_heavy"

    descriptor = heavy_descriptors[0]
    assert descriptor["weight"] == "heavy", (
        f"run_httpx_heavy must have weight='heavy'; got {descriptor['weight']!r}"
    )
    assert "network" in descriptor["capability_flags"]

    # arg_schema must declare target and hold_seconds.
    arg_schema = descriptor["arg_schema"]
    assert isinstance(arg_schema, dict)
    assert "target" in arg_schema.get("properties", {})
    assert "hold_seconds" in arg_schema.get("properties", {})
