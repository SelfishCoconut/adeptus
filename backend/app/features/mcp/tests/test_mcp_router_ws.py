"""WebSocket endpoint tests for GET /ws/tool-runs/{tool_run_id}.

Uses Starlette's synchronous TestClient.websocket_connect (which runs the ASGI
app in a thread with an event loop, compatible with pytest-asyncio's event loop).

Auth model:
  - Session cookie missing / invalid / expired  → close 4003
  - tool_run_id not found                       → close 4003
  - Caller not a member of the run's engagement → close 4003
  - Caller is member, run completed             → stored stdout/stderr + synthetic done
  - Caller is member, live channel              → replay buffered chunks + done

Isolation: each test that touches the pub/sub module calls _reset_channels()
via the autouse fixture so no state leaks between tests.
"""

from __future__ import annotations

import datetime
from collections.abc import AsyncGenerator, Iterator
from typing import cast
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from fastapi import FastAPI
from sqlalchemy import JSON, Column, ColumnDefault, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.config import get_settings
from app.core.db import Base, get_db
from app.core.errors import register_error_handlers
from app.features.auth import models as auth_models
from app.features.auth import repository as auth_repo
from app.features.auth.router import router as auth_router
from app.features.engagements import models as eng_models
from app.features.engagements import repository as eng_repo
from app.features.mcp import models as mcp_models
from app.features.mcp import repository as mcp_repo
from app.features.mcp import service
from app.features.mcp.router import router as mcp_router
from app.features.mcp.schemas import WebSocketOutputChunk

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_hasher = PasswordHasher()

_SESSION_COOKIE = "session_id"


def _now() -> datetime.datetime:
    return datetime.datetime(2026, 6, 1, 12, 0, 0, tzinfo=datetime.UTC)


def _future() -> datetime.datetime:
    """Far-future expiry so sessions don't expire during tests."""
    return datetime.datetime(2099, 1, 1, tzinfo=datetime.UTC)


# ---------------------------------------------------------------------------
# App + DB fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def app_and_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[tuple[FastAPI, async_sessionmaker[AsyncSession]], None]:
    """Test FastAPI app backed by a fresh SQLite in-memory database.

    Also patches ``app.features.mcp.router.get_sessionmaker`` so the WebSocket
    handler's manual ``get_sessionmaker()()`` call uses the same in-memory
    SQLite DB rather than trying to connect to the real Postgres instance.
    The patch is active for the lifetime of the fixture.
    """
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
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db

    # Patch get_sessionmaker in the router module so the WS handler's direct call
    # uses the test SQLite factory instead of the real Postgres engine.
    with patch("app.features.mcp.router.get_sessionmaker", return_value=factory):
        yield app, factory

    get_settings.cache_clear()
    await engine.dispose()


# ---------------------------------------------------------------------------
# Channel isolation fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_channels() -> Iterator[None]:
    """Reset the pub/sub channel map before every test."""
    service._reset_channels()
    yield
    service._reset_channels()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_user(
    factory: async_sessionmaker[AsyncSession],
    username: str,
    password: str = "pass123",
) -> auth_models.User:
    pw_hash = _hasher.hash(password)
    async with factory() as session:
        user = await auth_repo.create_user(
            session, username=username, password_hash=pw_hash, role="user"
        )
        await session.commit()
        await session.refresh(user)
        return user


async def _seed_session(
    factory: async_sessionmaker[AsyncSession],
    user_id: UUID,
    session_id: str | None = None,
    expires_at: datetime.datetime | None = None,
) -> auth_models.Session:
    sid = session_id or str(uuid4())
    exp = expires_at or _future()
    async with factory() as session:
        db_session = await auth_repo.create_session(
            session,
            session_id=sid,
            user_id=user_id,
            expires_at=exp,
        )
        await session.commit()
        await session.refresh(db_session)
        return db_session


async def _seed_engagement(
    factory: async_sessionmaker[AsyncSession],
    owner_id: UUID,
) -> UUID:
    async with factory() as session:
        engagement = await eng_repo.create_engagement(
            session,
            name="Test Engagement",
            scope="https://example.com",
            client_info=None,
            owner_id=owner_id,
        )
        await session.commit()
        await session.refresh(engagement)
        return cast(UUID, engagement.id)


async def _seed_completed_tool_run(
    factory: async_sessionmaker[AsyncSession],
    engagement_id: UUID,
    stdout: str = "output line\n",
    stderr: str = "",
    exit_code: int = 0,
) -> mcp_models.ToolRun:
    async with factory() as session:
        run = await mcp_repo.create_tool_run(
            session,
            engagement_id=engagement_id,
            server_name="shell-exec",
            tool_name="run_command",
            args={"command": "echo hello"},
            status="completed",
        )
        run = await mcp_repo.update_tool_run_result(
            session,
            cast(UUID, run.id),
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            finished_at=_now(),
            status="completed",
        )
        await session.commit()
        await session.refresh(run)
        return run


def _make_test_client(app: FastAPI) -> TestClient:
    """Create a synchronous TestClient (raise_server_exceptions=False for WS tests)."""
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Tests — Auth failures → close 4003
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_rejects_missing_cookie(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    """WebSocket upgrade without a session cookie → closed with 4003."""
    app, factory = app_and_factory
    run_id = uuid4()

    client = _make_test_client(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/ws/tool-runs/{run_id}"):
            pass  # pragma: no cover

    assert exc_info.value.code == 4003


@pytest.mark.asyncio
async def test_ws_rejects_invalid_cookie(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    """WebSocket upgrade with an unknown (fabricated) session id → closed with 4003."""
    app, factory = app_and_factory
    run_id = uuid4()

    client = _make_test_client(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            f"/ws/tool-runs/{run_id}",
            cookies={_SESSION_COOKIE: "not-a-real-session-id"},
        ):
            pass  # pragma: no cover

    assert exc_info.value.code == 4003


@pytest.mark.asyncio
async def test_ws_rejects_expired_session(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    """WebSocket upgrade with an expired session → closed with 4003."""
    app, factory = app_and_factory
    user = await _seed_user(factory, "expireduser")
    past = datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC)
    db_session = await _seed_session(factory, cast(UUID, user.id), expires_at=past)
    run_id = uuid4()

    client = _make_test_client(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            f"/ws/tool-runs/{run_id}",
            cookies={_SESSION_COOKIE: cast(str, db_session.id)},
        ):
            pass  # pragma: no cover

    assert exc_info.value.code == 4003


@pytest.mark.asyncio
async def test_ws_rejects_missing_tool_run(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    """WebSocket upgrade for a non-existent tool_run_id → closed with 4003."""
    app, factory = app_and_factory
    user = await _seed_user(factory, "validuser")
    db_session = await _seed_session(factory, cast(UUID, user.id))
    run_id = uuid4()  # this run doesn't exist in the DB

    client = _make_test_client(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            f"/ws/tool-runs/{run_id}",
            cookies={_SESSION_COOKIE: cast(str, db_session.id)},
        ):
            pass  # pragma: no cover

    assert exc_info.value.code == 4003


@pytest.mark.asyncio
async def test_ws_rejects_non_member(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    """Caller who is NOT a member of the run's engagement → closed with 4003.

    The run exists and belongs to a real engagement; the connecting user simply
    has no EngagementMember row for that engagement.
    """
    app, factory = app_and_factory
    owner = await _seed_user(factory, "owner")
    non_member = await _seed_user(factory, "nonmember")
    db_session = await _seed_session(factory, cast(UUID, non_member.id))

    eid = await _seed_engagement(factory, cast(UUID, owner.id))
    run = await _seed_completed_tool_run(factory, eid)

    client = _make_test_client(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            f"/ws/tool-runs/{run.id}",
            cookies={_SESSION_COOKIE: cast(str, db_session.id)},
        ):
            pass  # pragma: no cover

    assert exc_info.value.code == 4003


# ---------------------------------------------------------------------------
# Tests — Member, completed run (no live channel)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_completed_run_sends_stored_output_then_done(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    """Member connecting to a completed run receives stored stdout then a synthetic done."""
    app, factory = app_and_factory
    member = await _seed_user(factory, "memberuser")
    db_session = await _seed_session(factory, cast(UUID, member.id))

    eid = await _seed_engagement(factory, cast(UUID, member.id))
    run = await _seed_completed_tool_run(
        factory, eid, stdout="hello output\n", stderr="", exit_code=0
    )

    client = _make_test_client(app)
    with client.websocket_connect(
        f"/ws/tool-runs/{run.id}",
        cookies={_SESSION_COOKIE: cast(str, db_session.id)},
    ) as ws:
        msg1 = ws.receive_json()
        assert msg1["type"] == "stdout"
        assert msg1["data"] == "hello output\n"

        msg2 = ws.receive_json()
        assert msg2["type"] == "done"
        assert msg2["exit_code"] == 0


@pytest.mark.asyncio
async def test_ws_completed_run_with_stderr_sends_both_chunks(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    """Member connecting to a run that has both stdout and stderr receives both chunks."""
    app, factory = app_and_factory
    member = await _seed_user(factory, "memberusr2")
    db_session = await _seed_session(factory, cast(UUID, member.id))

    eid = await _seed_engagement(factory, cast(UUID, member.id))
    run = await _seed_completed_tool_run(
        factory, eid, stdout="out line\n", stderr="err line\n", exit_code=1
    )

    client = _make_test_client(app)
    with client.websocket_connect(
        f"/ws/tool-runs/{run.id}",
        cookies={_SESSION_COOKIE: cast(str, db_session.id)},
    ) as ws:
        stdout_msg = ws.receive_json()
        assert stdout_msg["type"] == "stdout"
        assert stdout_msg["data"] == "out line\n"

        stderr_msg = ws.receive_json()
        assert stderr_msg["type"] == "stderr"
        assert stderr_msg["data"] == "err line\n"

        done_msg = ws.receive_json()
        assert done_msg["type"] == "done"
        assert done_msg["exit_code"] == 1


@pytest.mark.asyncio
async def test_ws_completed_run_no_stdout_sends_only_done(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    """A completed run with empty stdout and stderr sends only the done chunk."""
    app, factory = app_and_factory
    member = await _seed_user(factory, "silentmember")
    db_session = await _seed_session(factory, cast(UUID, member.id))

    eid = await _seed_engagement(factory, cast(UUID, member.id))
    run = await _seed_completed_tool_run(factory, eid, stdout="", stderr="", exit_code=0)

    client = _make_test_client(app)
    with client.websocket_connect(
        f"/ws/tool-runs/{run.id}",
        cookies={_SESSION_COOKIE: cast(str, db_session.id)},
    ) as ws:
        msg = ws.receive_json()
        assert msg["type"] == "done"
        assert msg["exit_code"] == 0


# ---------------------------------------------------------------------------
# Tests — Member, live run with populated channel (replay buffer)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_live_run_replay_buffer_then_done(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    """Mid-run reconnect: replay buffer chunks arrive before new ones (Decision 3).

    Pre-seed the pub/sub channel with a 'stdout' and 'done' chunk BEFORE the
    client connects.  The handler must replay both from the buffer without
    waiting on the live queue.
    """
    app, factory = app_and_factory
    member = await _seed_user(factory, "livemember")
    db_session = await _seed_session(factory, cast(UUID, member.id))

    eid = await _seed_engagement(factory, cast(UUID, member.id))

    # Seed a 'running' tool run row so the auth checks pass.
    async with factory() as session:
        run = await mcp_repo.create_tool_run(
            session,
            engagement_id=eid,
            server_name="shell-exec",
            tool_name="run_command",
            args={"command": "echo hi"},
            status="running",
        )
        await session.commit()
        await session.refresh(run)

    run_id = cast(UUID, run.id)

    # Pre-seed the replay buffer with stdout + done so it's already in the channel
    # before the client connects.  This exercises the "replay buffer" path and
    # ensures the done chunk short-circuits the live queue.
    service.broadcast_tool_run_output(run_id, WebSocketOutputChunk(type="stdout", data="hello"))
    service.broadcast_tool_run_output(
        run_id,
        WebSocketOutputChunk(type="done", exit_code=0, finished_at=_now()),
    )

    client = _make_test_client(app)
    with client.websocket_connect(
        f"/ws/tool-runs/{run_id}",
        cookies={_SESSION_COOKIE: cast(str, db_session.id)},
    ) as ws:
        msg1 = ws.receive_json()
        assert msg1["type"] == "stdout"
        assert msg1["data"] == "hello"

        msg2 = ws.receive_json()
        assert msg2["type"] == "done"
        assert msg2["exit_code"] == 0


@pytest.mark.asyncio
async def test_ws_live_run_ordering_replay_before_live(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    """Replay chunks arrive BEFORE any live chunks — ordering is deterministic (Decision 3).

    Pre-seed THREE chunks in the replay buffer: two stdout chunks and a done chunk.
    The handler must deliver them in the exact seeded order: first, second, done.
    This confirms the replay buffer preserves insertion order and that the WS
    handler iterates replay before touching the live queue.
    """
    app, factory = app_and_factory
    member = await _seed_user(factory, "ordermember")
    db_session = await _seed_session(factory, cast(UUID, member.id))

    eid = await _seed_engagement(factory, cast(UUID, member.id))

    async with factory() as session:
        run = await mcp_repo.create_tool_run(
            session,
            engagement_id=eid,
            server_name="shell-exec",
            tool_name="run_command",
            args={"command": "sleep 1"},
            status="running",
        )
        await session.commit()
        await session.refresh(run)

    run_id = cast(UUID, run.id)

    # Seed three chunks into the replay buffer in order.  The 'done' chunk at
    # the end ensures the handler exits cleanly without waiting on the live queue.
    service.broadcast_tool_run_output(run_id, WebSocketOutputChunk(type="stdout", data="first"))
    service.broadcast_tool_run_output(run_id, WebSocketOutputChunk(type="stdout", data="second"))
    service.broadcast_tool_run_output(
        run_id, WebSocketOutputChunk(type="done", exit_code=0, finished_at=_now())
    )

    received: list[dict] = []

    client = _make_test_client(app)
    with client.websocket_connect(
        f"/ws/tool-runs/{run_id}",
        cookies={_SESSION_COOKIE: cast(str, db_session.id)},
    ) as ws:
        for _ in range(3):
            received.append(ws.receive_json())

    assert received[0]["type"] == "stdout"
    assert received[0]["data"] == "first"
    assert received[1]["type"] == "stdout"
    assert received[1]["data"] == "second"
    assert received[2]["type"] == "done"
    assert received[2]["exit_code"] == 0
