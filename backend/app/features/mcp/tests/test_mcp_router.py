"""Router-layer tests for the MCP feature.

Uses httpx.AsyncClient with ASGITransport against a test FastAPI app that:
- Includes the MCP router.
- Includes the auth router (needed to create real sessions for authentication).
- Registers core error handlers (for AuthenticationError → 401, ForbiddenError → 403).
- Overrides get_db with an in-memory SQLite session factory.

The MCP service layer is mocked with unittest.mock.patch so no real subprocess
or DB state for tool runs is needed — only a real user/session row is needed to
satisfy the get_current_user dependency.

Status codes tested:
  GET /admin/mcp-servers:  200 (admin), 403 (non-admin), 401 (unauthenticated)
  POST /tool-runs:         200 (success), 400 (unknown server), 403 (non-member),
                           403 (admin but not member), 404 (engagement not found),
                           503 (server down), 401 (unauthenticated)
"""

from __future__ import annotations

import datetime
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON, Column, ColumnDefault, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import Base, get_db
from app.core.errors import register_error_handlers
from app.features.auth import models as auth_models
from app.features.auth import repository as auth_repo
from app.features.auth.router import router as auth_router
from app.features.engagements import models as eng_models  # noqa: F401 — registers ORM tables
from app.features.mcp import models as mcp_models  # noqa: F401 — registers ORM tables
from app.features.mcp.router import router as mcp_router
from app.features.mcp.schemas import McpServerInfo, McpToolDeclaration, ToolRunResult
from app.features.mcp.service import EngagementNotFound, NotMember
from app.features.mcp.subprocess_manager import McpServerDown, McpServerNotFound, McpToolNotFound

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_hasher = PasswordHasher()


def _now() -> datetime.datetime:
    return datetime.datetime(2026, 6, 1, 12, 0, 0, tzinfo=datetime.UTC)


def _make_mcp_server_info() -> McpServerInfo:
    return McpServerInfo(
        server_name="shell-exec",
        status="running",
        tools=[
            McpToolDeclaration(
                name="run_command",
                weight="light",
                capability_flags=["shell-exec", "filesystem-write"],
            )
        ],
    )


def _make_tool_run_result(*, engagement_id: UUID | None = None) -> ToolRunResult:
    eid = engagement_id or uuid4()
    return ToolRunResult(
        tool_run_id=uuid4(),
        engagement_id=eid,
        server_name="shell-exec",
        tool_name="run_command",
        exit_code=0,
        stdout="hello\n",
        stderr="",
        started_at=_now(),
        finished_at=_now(),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def app_and_db(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[tuple[FastAPI, async_sessionmaker[AsyncSession]], None]:
    """Test FastAPI app backed by a fresh SQLite in-memory database."""
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

    args_col: Column = mcp_models.ToolRun.__table__.c.args  # type: ignore[assignment]
    args_col.type = JSON()

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
async def admin_user(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> auth_models.User:
    """Insert an admin user directly into the test DB."""
    _, factory = app_and_db
    pw_hash = _hasher.hash("adminpass")
    async with factory() as session:
        user = await auth_repo.create_user(
            session,
            username="testadmin",
            password_hash=pw_hash,
            role="admin",
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


@pytest_asyncio.fixture
async def admin_client(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    admin_user: auth_models.User,
) -> AsyncGenerator[AsyncClient, None]:
    """Authenticated AsyncClient logged in as the admin user."""
    app, _ = app_and_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "testadmin", "password": "adminpass"},
        )
        assert resp.status_code == 200, resp.text
        yield client


# ---------------------------------------------------------------------------
# Tests — GET /api/v1/admin/mcp-servers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_mcp_servers_returns_200_for_admin(
    admin_client: AsyncClient,
) -> None:
    """Admin user receives 200 with a list of McpServerInfo."""
    servers = [_make_mcp_server_info()]
    with patch(
        "app.features.mcp.router.service.list_servers",
        new=AsyncMock(return_value=servers),
    ):
        resp = await admin_client.get("/api/v1/admin/mcp-servers")

    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["server_name"] == "shell-exec"
    assert body[0]["status"] == "running"
    assert body[0]["tools"][0]["name"] == "run_command"
    assert body[0]["tools"][0]["weight"] == "light"
    assert "shell-exec" in body[0]["tools"][0]["capability_flags"]


@pytest.mark.asyncio
async def test_list_mcp_servers_returns_403_for_non_admin(
    regular_client: AsyncClient,
) -> None:
    """Regular (non-admin) authenticated user receives 403."""
    resp = await regular_client.get("/api/v1/admin/mcp-servers")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_mcp_servers_returns_401_for_unauthenticated(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    """Unauthenticated request receives 401."""
    app, _ = app_and_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await client.get("/api/v1/admin/mcp-servers")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Tests — POST /api/v1/tool-runs
# ---------------------------------------------------------------------------

_TOOL_RUN_BODY = {
    "engagement_id": str(uuid4()),
    "server_name": "shell-exec",
    "tool_name": "run_command",
    "args": {"command": "echo hello"},
    "timeout_seconds": 30,
}


@pytest.mark.asyncio
async def test_execute_tool_run_returns_200_on_success(
    regular_client: AsyncClient,
) -> None:
    """Authenticated member receives 200 with ToolRunResult on success."""
    eid = uuid4()
    result = _make_tool_run_result(engagement_id=eid)
    body = {**_TOOL_RUN_BODY, "engagement_id": str(eid)}

    with patch(
        "app.features.mcp.router.service.execute_tool_run",
        new=AsyncMock(return_value=result),
    ):
        resp = await regular_client.post("/api/v1/tool-runs", json=body)

    assert resp.status_code == 200
    data = resp.json()
    assert data["server_name"] == "shell-exec"
    assert data["tool_name"] == "run_command"
    assert data["exit_code"] == 0
    assert data["stdout"] == "hello\n"
    assert data["stderr"] == ""


@pytest.mark.asyncio
async def test_execute_tool_run_returns_400_for_unknown_server(
    regular_client: AsyncClient,
) -> None:
    """McpServerNotFound from service → 400 Bad Request."""
    with patch(
        "app.features.mcp.router.service.execute_tool_run",
        new=AsyncMock(side_effect=McpServerNotFound("MCP server 'unknown' is not in the registry")),
    ):
        resp = await regular_client.post("/api/v1/tool-runs", json=_TOOL_RUN_BODY)

    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "bad_request"


@pytest.mark.asyncio
async def test_execute_tool_run_returns_400_for_unknown_tool_name(
    regular_client: AsyncClient,
) -> None:
    """McpToolNotFound (JSON-RPC -32601) from subprocess → 400 Bad Request.

    Unknown tool name is a client error; it must NOT return 503.
    """
    with patch(
        "app.features.mcp.router.service.execute_tool_run",
        new=AsyncMock(
            side_effect=McpToolNotFound(
                "MCP server 'shell-exec' tool 'bad_tool' not found: "
                "{'code': -32601, 'message': 'Tool not found: bad_tool'}"
            )
        ),
    ):
        body = {**_TOOL_RUN_BODY, "tool_name": "bad_tool"}
        resp = await regular_client.post("/api/v1/tool-runs", json=body)

    assert resp.status_code == 400
    data = resp.json()
    assert data["error"]["code"] == "bad_request"


@pytest.mark.asyncio
async def test_execute_tool_run_returns_403_for_non_member(
    regular_client: AsyncClient,
) -> None:
    """NotMember from service → 403 Forbidden."""
    with patch(
        "app.features.mcp.router.service.execute_tool_run",
        new=AsyncMock(side_effect=NotMember("Not a member of this engagement")),
    ):
        resp = await regular_client.post("/api/v1/tool-runs", json=_TOOL_RUN_BODY)

    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["code"] == "forbidden"


@pytest.mark.asyncio
async def test_execute_tool_run_returns_403_for_admin_not_member(
    admin_client: AsyncClient,
) -> None:
    """Admin who is NOT an explicit engagement member receives 403 — §4 no-admin-bypass.

    The service enforces the membership check without consulting the user's role;
    this test confirms the router correctly surfaces that 403 even for admin callers.
    """
    with patch(
        "app.features.mcp.router.service.execute_tool_run",
        new=AsyncMock(
            side_effect=NotMember("Admin user is not an explicit member of this engagement")
        ),
    ):
        resp = await admin_client.post("/api/v1/tool-runs", json=_TOOL_RUN_BODY)

    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["code"] == "forbidden"


@pytest.mark.asyncio
async def test_execute_tool_run_returns_404_for_engagement_not_found(
    regular_client: AsyncClient,
) -> None:
    """EngagementNotFound from service → 404 Not Found."""
    with patch(
        "app.features.mcp.router.service.execute_tool_run",
        new=AsyncMock(side_effect=EngagementNotFound("Engagement not found")),
    ):
        resp = await regular_client.post("/api/v1/tool-runs", json=_TOOL_RUN_BODY)

    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_execute_tool_run_returns_503_for_server_down(
    regular_client: AsyncClient,
) -> None:
    """McpServerDown from service → 503 Service Unavailable."""
    with patch(
        "app.features.mcp.router.service.execute_tool_run",
        new=AsyncMock(side_effect=McpServerDown("MCP server 'shell-exec' is not running")),
    ):
        resp = await regular_client.post("/api/v1/tool-runs", json=_TOOL_RUN_BODY)

    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "service_unavailable"


@pytest.mark.asyncio
async def test_execute_tool_run_returns_401_for_unauthenticated(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    """Unauthenticated request to POST /tool-runs returns 401."""
    app, _ = app_and_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await client.post("/api/v1/tool-runs", json=_TOOL_RUN_BODY)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_execute_tool_run_passes_correct_args_to_service(
    regular_client: AsyncClient,
    regular_user: auth_models.User,
) -> None:
    """The router forwards all fields from ToolRunCreate to service.execute_tool_run."""
    eid = uuid4()
    result = _make_tool_run_result(engagement_id=eid)
    mock_execute = AsyncMock(return_value=result)

    with patch("app.features.mcp.router.service.execute_tool_run", new=mock_execute):
        resp = await regular_client.post(
            "/api/v1/tool-runs",
            json={
                "engagement_id": str(eid),
                "server_name": "shell-exec",
                "tool_name": "run_command",
                "args": {"command": "echo hello"},
                "timeout_seconds": 60,
            },
        )

    assert resp.status_code == 200
    mock_execute.assert_called_once()
    call_kwargs = mock_execute.call_args.kwargs
    assert call_kwargs["engagement_id"] == eid
    assert call_kwargs["server_name"] == "shell-exec"
    assert call_kwargs["tool_name"] == "run_command"
    assert call_kwargs["args"] == {"command": "echo hello"}
    assert call_kwargs["timeout_seconds"] == 60
