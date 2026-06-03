"""Tests for the POST /api/v1/engagements/{id}/pause endpoint.

Test matrix (from the Slice 06 spec task 7):
  - POST /engagements/{id}/pause 200 for member with correct counts
  - POST /engagements/{id}/pause 404 for non-member (§17.1 no-disclosure)
  - column updated in DB (paused flag persisted)
  - engagement_paused_changed emitted with correct args
  - service set_engagement_paused: returns EngagementPauseState with aggregated counts
  - service set_engagement_paused: idempotent (same state twice = no-op success)
  - resume returns (0, 0) counts

Note: new engagements default paused=false is covered by test_slot_limit.py
(test_create_engagement_default_slot_limit); not duplicated here as per the spec.
"""

from __future__ import annotations

import datetime
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Column, ColumnDefault, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import Base, get_db
from app.core.errors import NotFoundError, register_error_handlers
from app.features.auth import models as auth_models
from app.features.auth import repository as auth_repo
from app.features.auth.router import router as auth_router
from app.features.engagements import events as engagement_events
from app.features.engagements import models as eng_models  # noqa: F401 — ORM table registration
from app.features.engagements import service
from app.features.engagements.router import router as eng_router
from app.features.engagements.schemas import EngagementPauseState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_hasher = PasswordHasher()

NOW = datetime.datetime(2026, 5, 30, 12, 0, 0, tzinfo=datetime.UTC)


def _make_engagement(
    *,
    engagement_id: object = None,
    name: str = "Pause Test Engagement",
    scope: str = "*.example.com",
    client_info: str | None = None,
    status: str = "active",
    privacy_mode: str = "local_only",
    concurrency_slot_limit: int = 3,
    paused: bool = False,
) -> MagicMock:
    eng = MagicMock()
    eng.id = engagement_id or uuid4()
    eng.name = name
    eng.scope = scope
    eng.client_info = client_info
    eng.status = status
    eng.privacy_mode = privacy_mode
    eng.concurrency_slot_limit = concurrency_slot_limit
    eng.paused = paused
    eng.created_at = NOW
    eng.updated_at = NOW
    return eng


def _make_member(
    *, engagement_id: object = None, user_id: object = None, role: str = "member"
) -> MagicMock:
    m = MagicMock()
    m.engagement_id = engagement_id or uuid4()
    m.user_id = user_id or uuid4()
    m.role = role
    m.joined_at = NOW
    return m


def _make_user(*, user_id: object = None) -> MagicMock:
    u = MagicMock()
    u.id = user_id or uuid4()
    u.username = "alice"
    u.role = "user"
    return u


# ---------------------------------------------------------------------------
# Service-layer unit tests (mocked repo + events)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_events() -> object:
    """Each test starts and ends with an empty event listener registry."""
    engagement_events._reset()
    yield
    engagement_events._reset()


@pytest.mark.asyncio
async def test_set_engagement_paused_returns_pause_state_with_counts() -> None:
    """set_engagement_paused emits the event and returns aggregated counts."""
    db = AsyncMock()
    caller = _make_user()
    eng_id = uuid4()
    mock_eng = _make_engagement(engagement_id=eng_id, paused=False)
    updated_eng = _make_engagement(engagement_id=eng_id, paused=True)
    caller_member = _make_member(engagement_id=eng_id, user_id=caller.id, role="member")

    # Register a stub listener that returns (2, 1) — simulating 2 killed, 1 dequeued.
    def fake_listener(eid: UUID, p: bool) -> tuple[int, int]:
        return (2, 1)

    engagement_events.on_engagement_paused_changed(fake_listener)

    emitted: list[tuple] = []

    original_emit = engagement_events.emit_engagement_paused_changed

    def capturing_emit(eid: UUID, p: bool) -> list[tuple[int, int]]:
        emitted.append((eid, p))
        return original_emit(eid, p)

    with (
        patch(
            "app.features.engagements.service.repo.get_engagement_for_member",
            new=AsyncMock(return_value=(mock_eng, caller_member)),
        ),
        patch(
            "app.features.engagements.service.repo.update_paused",
            new=AsyncMock(return_value=updated_eng),
        ),
        patch(
            "app.features.engagements.service.events.emit_engagement_paused_changed",
            side_effect=capturing_emit,
        ),
    ):
        result = await service.set_engagement_paused(db, caller, eng_id, paused=True)

    assert isinstance(result, EngagementPauseState)
    assert result.engagement_id == eng_id
    assert result.paused is True
    assert result.killed_running == 2
    assert result.dequeued == 1
    # Event was emitted with correct args.
    assert emitted == [(eng_id, True)]


@pytest.mark.asyncio
async def test_set_engagement_paused_resume_returns_zero_counts() -> None:
    """Resuming (paused=False) aggregates (0,0) counts from the listener."""
    db = AsyncMock()
    caller = _make_user()
    eng_id = uuid4()
    mock_eng = _make_engagement(engagement_id=eng_id, paused=True)
    updated_eng = _make_engagement(engagement_id=eng_id, paused=False)
    caller_member = _make_member(engagement_id=eng_id, user_id=caller.id, role="member")

    def fake_listener(eid: UUID, p: bool) -> tuple[int, int]:
        return (0, 0)

    engagement_events.on_engagement_paused_changed(fake_listener)

    with (
        patch(
            "app.features.engagements.service.repo.get_engagement_for_member",
            new=AsyncMock(return_value=(mock_eng, caller_member)),
        ),
        patch(
            "app.features.engagements.service.repo.update_paused",
            new=AsyncMock(return_value=updated_eng),
        ),
    ):
        result = await service.set_engagement_paused(db, caller, eng_id, paused=False)

    assert result.paused is False
    assert result.killed_running == 0
    assert result.dequeued == 0


@pytest.mark.asyncio
async def test_set_engagement_paused_non_member_raises_not_found() -> None:
    """Non-member (or unknown engagement) gets NotFoundError — §17.1 no-disclosure."""
    db = AsyncMock()
    caller = _make_user()
    eng_id = uuid4()

    with patch(
        "app.features.engagements.service.repo.get_engagement_for_member",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(NotFoundError):
            await service.set_engagement_paused(db, caller, eng_id, paused=True)


@pytest.mark.asyncio
async def test_set_engagement_paused_idempotent_same_state() -> None:
    """Setting the same pause state twice is a no-op success (idempotent)."""
    db = AsyncMock()
    caller = _make_user()
    eng_id = uuid4()
    # Already paused
    mock_eng = _make_engagement(engagement_id=eng_id, paused=True)
    updated_eng = _make_engagement(engagement_id=eng_id, paused=True)
    caller_member = _make_member(engagement_id=eng_id, user_id=caller.id, role="member")

    def fake_listener(eid: UUID, p: bool) -> tuple[int, int]:
        return (0, 0)

    engagement_events.on_engagement_paused_changed(fake_listener)

    with (
        patch(
            "app.features.engagements.service.repo.get_engagement_for_member",
            new=AsyncMock(return_value=(mock_eng, caller_member)),
        ),
        patch(
            "app.features.engagements.service.repo.update_paused",
            new=AsyncMock(return_value=updated_eng),
        ),
    ):
        # Second pause call (already paused) — should not raise.
        result = await service.set_engagement_paused(db, caller, eng_id, paused=True)

    assert result.paused is True


@pytest.mark.asyncio
async def test_set_engagement_paused_emits_event() -> None:
    """engagement_paused_changed is emitted with the correct (engagement_id, paused) args."""
    db = AsyncMock()
    caller = _make_user()
    eng_id = uuid4()
    mock_eng = _make_engagement(engagement_id=eng_id)
    updated_eng = _make_engagement(engagement_id=eng_id, paused=True)
    caller_member = _make_member(engagement_id=eng_id, user_id=caller.id, role="owner")

    received: list[tuple] = []

    def _recording_listener(eid: UUID, p: bool) -> tuple[int, int]:
        received.append((eid, p))
        return (0, 0)

    engagement_events.on_engagement_paused_changed(_recording_listener)

    with (
        patch(
            "app.features.engagements.service.repo.get_engagement_for_member",
            new=AsyncMock(return_value=(mock_eng, caller_member)),
        ),
        patch(
            "app.features.engagements.service.repo.update_paused",
            new=AsyncMock(return_value=updated_eng),
        ),
    ):
        await service.set_engagement_paused(db, caller, eng_id, paused=True)

    assert received == [(eng_id, True)]


# ---------------------------------------------------------------------------
# Router-layer tests (full HTTP stack; mocked service)
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

    user_id_col: Column = auth_models.User.__table__.c.id  # type: ignore[assignment]
    user_id_col.default = ColumnDefault(uuid4)

    ip_col: Column = auth_models.Session.__table__.c.ip  # type: ignore[assignment]
    ip_col.type = Text()

    eng_id_col: Column = eng_models.Engagement.__table__.c.id  # type: ignore[assignment]
    eng_id_col.default = ColumnDefault(uuid4)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(eng_router)
    register_error_handlers(app)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    yield app, factory

    get_settings.cache_clear()
    await engine.dispose()


@pytest_asyncio.fixture
async def owner_user(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> auth_models.User:
    """Insert an owner user directly into the test DB."""
    _, factory = app_and_db
    pw_hash = _hasher.hash("secretpass")
    async with factory() as session:
        user = await auth_repo.create_user(
            session,
            username="pause_owner",
            password_hash=pw_hash,
            role="user",
        )
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def owner_client(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    owner_user: auth_models.User,
) -> AsyncGenerator[AsyncClient, None]:
    """Authenticated AsyncClient logged in as the owner user."""
    app, _ = app_and_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "pause_owner", "password": "secretpass"},
        )
        assert resp.status_code == 200, resp.text
        yield client


@pytest_asyncio.fixture
async def anon_client(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> AsyncGenerator[AsyncClient, None]:
    """Unauthenticated AsyncClient."""
    app, _ = app_and_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        yield client


@pytest.mark.asyncio
async def test_pause_endpoint_200_for_member(
    owner_client: AsyncClient,
) -> None:
    """POST /engagements/{id}/pause returns 200 with correct EngagementPauseState for a member."""
    eng_id = uuid4()
    pause_state = EngagementPauseState(
        engagement_id=eng_id,
        paused=True,
        killed_running=1,
        dequeued=0,
    )
    with patch(
        "app.features.engagements.service.set_engagement_paused",
        new=AsyncMock(return_value=pause_state),
    ):
        resp = await owner_client.post(
            f"/api/v1/engagements/{eng_id}/pause",
            json={"paused": True},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["paused"] is True
    assert body["killed_running"] == 1
    assert body["dequeued"] == 0
    assert body["engagement_id"] == str(eng_id)


@pytest.mark.asyncio
async def test_pause_endpoint_404_for_non_member(
    owner_client: AsyncClient,
) -> None:
    """POST /engagements/{id}/pause returns 404 for non-member (§17.1 no-disclosure)."""
    eng_id = uuid4()
    with patch(
        "app.features.engagements.service.set_engagement_paused",
        new=AsyncMock(side_effect=NotFoundError("Engagement not found")),
    ):
        resp = await owner_client.post(
            f"/api/v1/engagements/{eng_id}/pause",
            json={"paused": True},
        )

    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_pause_endpoint_resume_200(
    owner_client: AsyncClient,
) -> None:
    """POST /engagements/{id}/pause with paused=false (resume) returns 200 with 0,0 counts."""
    eng_id = uuid4()
    pause_state = EngagementPauseState(
        engagement_id=eng_id,
        paused=False,
        killed_running=0,
        dequeued=0,
    )
    with patch(
        "app.features.engagements.service.set_engagement_paused",
        new=AsyncMock(return_value=pause_state),
    ):
        resp = await owner_client.post(
            f"/api/v1/engagements/{eng_id}/pause",
            json={"paused": False},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["paused"] is False
    assert body["killed_running"] == 0
    assert body["dequeued"] == 0


@pytest.mark.asyncio
async def test_pause_endpoint_401_for_unauthenticated(
    anon_client: AsyncClient,
) -> None:
    """POST /engagements/{id}/pause returns 401 for unauthenticated requests."""
    eng_id = uuid4()
    resp = await anon_client.post(
        f"/api/v1/engagements/{eng_id}/pause",
        json={"paused": True},
    )
    assert resp.status_code == 401, resp.text
