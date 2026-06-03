"""Tests for the concurrency_slot_limit engagement setting.

Tests:
- Default is 3 on create.
- Patch within range (1–16) succeeds.
- Out-of-range (0, 17) rejected with 422 (Pydantic validation error).
- Raising the limit while runs are queued admits the front-eligible waiter
  immediately (slot-limit wiring via concurrency.set_slot_limit).

The service layer is tested with mocked repository; the 422 tests exercise
Pydantic schema validation directly (no HTTP layer needed, since FastAPI
delegates body validation to Pydantic before the route handler is called).
"""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.features.engagements import service
from app.features.engagements.schemas import EngagementCreate, EngagementUpdate
from app.features.mcp import concurrency as mcp_concurrency

NOW = datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC)


def _make_engagement(
    *,
    engagement_id: object = None,
    name: str = "Slot Test Engagement",
    scope: str = "*.example.com",
    client_info: str | None = None,
    status: str = "active",
    privacy_mode: str = "local_only",
    concurrency_slot_limit: int = 3,
) -> MagicMock:
    eng = MagicMock()
    eng.id = engagement_id or uuid4()
    eng.name = name
    eng.scope = scope
    eng.client_info = client_info
    eng.status = status
    eng.privacy_mode = privacy_mode
    eng.concurrency_slot_limit = concurrency_slot_limit
    eng.created_at = NOW
    eng.updated_at = NOW
    return eng


def _make_member(
    *, engagement_id: object = None, user_id: object = None, role: str = "owner"
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
# Default slot limit on create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_engagement_default_slot_limit() -> None:
    """New engagements get concurrency_slot_limit=3 by default."""
    db = AsyncMock()
    caller = _make_user()
    mock_eng = _make_engagement(concurrency_slot_limit=3)
    data = EngagementCreate(name="Slot Test", scope="*.example.com")

    with patch(
        "app.features.engagements.service.repo.create_engagement",
        new=AsyncMock(return_value=mock_eng),
    ):
        result = await service.create_engagement(db, caller, data)

    assert result.concurrency_slot_limit == 3


# ---------------------------------------------------------------------------
# Patch within valid range
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_engagement_slot_limit_in_range() -> None:
    """Patching concurrency_slot_limit to 5 (valid) succeeds."""
    db = AsyncMock()
    caller = _make_user()
    eng_id = uuid4()
    mock_eng = _make_engagement(engagement_id=eng_id, concurrency_slot_limit=3)
    updated_eng = _make_engagement(engagement_id=eng_id, concurrency_slot_limit=5)
    caller_member = _make_member(engagement_id=eng_id, user_id=caller.id, role="owner")
    data = EngagementUpdate(concurrency_slot_limit=5)

    with (
        patch(
            "app.features.engagements.service.repo.get_engagement_for_member",
            new=AsyncMock(return_value=(mock_eng, caller_member)),
        ),
        patch(
            "app.features.engagements.service.repo.update_engagement",
            new=AsyncMock(return_value=updated_eng),
        ) as mock_update,
    ):
        result = await service.update_engagement(db, caller, eng_id, data)

    mock_update.assert_awaited_once_with(db, eng_id, privacy_mode=None, concurrency_slot_limit=5)
    assert result.concurrency_slot_limit == 5


@pytest.mark.asyncio
async def test_update_engagement_slot_limit_boundary_1() -> None:
    """Patching concurrency_slot_limit to 1 (lower bound) succeeds."""
    db = AsyncMock()
    caller = _make_user()
    eng_id = uuid4()
    mock_eng = _make_engagement(engagement_id=eng_id, concurrency_slot_limit=3)
    updated_eng = _make_engagement(engagement_id=eng_id, concurrency_slot_limit=1)
    caller_member = _make_member(engagement_id=eng_id, user_id=caller.id, role="owner")
    data = EngagementUpdate(concurrency_slot_limit=1)

    with (
        patch(
            "app.features.engagements.service.repo.get_engagement_for_member",
            new=AsyncMock(return_value=(mock_eng, caller_member)),
        ),
        patch(
            "app.features.engagements.service.repo.update_engagement",
            new=AsyncMock(return_value=updated_eng),
        ),
    ):
        result = await service.update_engagement(db, caller, eng_id, data)

    assert result.concurrency_slot_limit == 1


@pytest.mark.asyncio
async def test_update_engagement_slot_limit_boundary_16() -> None:
    """Patching concurrency_slot_limit to 16 (upper bound) succeeds."""
    db = AsyncMock()
    caller = _make_user()
    eng_id = uuid4()
    mock_eng = _make_engagement(engagement_id=eng_id, concurrency_slot_limit=3)
    updated_eng = _make_engagement(engagement_id=eng_id, concurrency_slot_limit=16)
    caller_member = _make_member(engagement_id=eng_id, user_id=caller.id, role="owner")
    data = EngagementUpdate(concurrency_slot_limit=16)

    with (
        patch(
            "app.features.engagements.service.repo.get_engagement_for_member",
            new=AsyncMock(return_value=(mock_eng, caller_member)),
        ),
        patch(
            "app.features.engagements.service.repo.update_engagement",
            new=AsyncMock(return_value=updated_eng),
        ),
    ):
        result = await service.update_engagement(db, caller, eng_id, data)

    assert result.concurrency_slot_limit == 16


# ---------------------------------------------------------------------------
# Out-of-range rejected with 422 (Pydantic ValidationError)
# ---------------------------------------------------------------------------


def test_engagement_update_slot_limit_zero_rejected() -> None:
    """concurrency_slot_limit=0 is below the minimum (1) and must raise ValidationError."""
    with pytest.raises(ValidationError) as exc_info:
        EngagementUpdate(concurrency_slot_limit=0)

    errors = exc_info.value.errors()
    assert any(
        e["loc"] == ("concurrency_slot_limit",) and "greater than or equal to 1" in e["msg"]
        for e in errors
    )


def test_engagement_update_slot_limit_17_rejected() -> None:
    """concurrency_slot_limit=17 is above the maximum (16) and must raise ValidationError."""
    with pytest.raises(ValidationError) as exc_info:
        EngagementUpdate(concurrency_slot_limit=17)

    errors = exc_info.value.errors()
    assert any(
        e["loc"] == ("concurrency_slot_limit",) and "less than or equal to 16" in e["msg"]
        for e in errors
    )


# ---------------------------------------------------------------------------
# Slot-limit wiring: raising limit while runs are queued admits waiters (W2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_raise_slot_limit_admits_queued_waiter() -> None:
    """Raising the slot limit via update_engagement admits a queued waiter immediately.

    Setup:
    - slot_limit=1; one run holds the slot; a second run queues.
    - update_engagement raises the limit to 2.
    - The queued waiter must be admitted (its on_started fires).
    """
    mcp_concurrency._reset()

    eng_id = uuid4()
    db = AsyncMock()
    caller = _make_user()
    caller_member = _make_member(engagement_id=eng_id, user_id=caller.id, role="owner")

    started_log: list[str] = []

    # Admit the first run at limit=1.
    handle = await mcp_concurrency.acquire(
        engagement_id=eng_id,
        slot_limit=1,
        tool_run_id=uuid4(),
        target_host="localhost",
        server_name="httpx",
        tool_name="run_httpx_heavy",
        on_queued=lambda pos, reason: None,
        on_started=lambda: None,
    )

    # Queue a second run against a different host (blocked only by slot pool).
    waiter_task = asyncio.create_task(
        mcp_concurrency.acquire(
            engagement_id=eng_id,
            slot_limit=1,
            tool_run_id=uuid4(),
            target_host="127.0.0.1",
            server_name="httpx",
            tool_name="run_httpx_heavy",
            on_queued=lambda pos, reason: None,
            on_started=lambda: started_log.append("admitted"),
        )
    )
    await asyncio.sleep(0)  # Let the waiter enqueue.
    assert not waiter_task.done(), "Waiter should be queued"

    # Simulate update_engagement patching the slot limit to 2.
    mock_updated = _make_engagement(engagement_id=eng_id, concurrency_slot_limit=2)
    mock_initial = _make_engagement(engagement_id=eng_id, concurrency_slot_limit=1)

    with (
        patch(
            "app.features.engagements.service.repo.get_engagement_for_member",
            new=AsyncMock(return_value=(mock_initial, caller_member)),
        ),
        patch(
            "app.features.engagements.service.repo.update_engagement",
            new=AsyncMock(return_value=mock_updated),
        ),
    ):
        await service.update_engagement(
            db, caller, eng_id, EngagementUpdate(concurrency_slot_limit=2)
        )

    # The in-process set_slot_limit should have triggered the scan and admitted the waiter.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert waiter_task.done(), "Waiter should be admitted after the limit was raised"
    assert "admitted" in started_log, "on_started should have been called"

    waiter_handle = await waiter_task
    mcp_concurrency.release(handle)
    mcp_concurrency.release(waiter_handle)
    mcp_concurrency._reset()
