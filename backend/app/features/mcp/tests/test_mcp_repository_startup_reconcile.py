"""Tests for the startup reconciliation routine in app.features.mcp.repository.

Covers reconcile_stale_tool_runs — the function called on every backend startup
to mark phantom 'queued' and 'running' rows as 'failed'.

After a restart the in-process admission queue is empty, so any row left in
'queued' or 'running' status is a phantom whose background task no longer
exists.  The routine issues a single idempotent UPDATE; these tests verify:

- A 'queued' row becomes 'failed' with a non-null finished_at.
- A 'running' row becomes 'failed' with a non-null finished_at.
- Terminal rows ('completed', 'failed', 'timed_out') are left untouched.
- When no stale rows exist, the function returns 0 (idempotent / safe to call
  repeatedly).
- The function returns the correct count of updated rows.
"""

from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.mcp import repository as repo
from app.features.mcp.models import ToolRun

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _uid(obj: ToolRun) -> UUID:
    return cast(UUID, obj.id)


async def _create_with_status(
    db: AsyncSession,
    status: str,
    *,
    engagement_id: UUID | None = None,
) -> ToolRun:
    """Insert a ToolRun with an explicit status value."""
    run = await repo.create_tool_run(
        db,
        engagement_id=engagement_id or uuid4(),
        server_name="httpx",
        tool_name="run_httpx",
        args={"target": "http://localhost:3000"},
        status=status,
    )
    return run


async def _refresh(db: AsyncSession, run: ToolRun) -> ToolRun:
    """Re-fetch the row from the DB to get current persisted values."""
    await db.flush()
    await db.refresh(run)
    return run


# ---------------------------------------------------------------------------
# Core reconciliation behaviour
# ---------------------------------------------------------------------------


async def test_queued_row_becomes_failed(db_session: AsyncSession) -> None:
    """A 'queued' row at startup must be transitioned to 'failed'."""
    run = await _create_with_status(db_session, "queued")

    updated_count = await repo.reconcile_stale_tool_runs(db_session)

    await _refresh(db_session, run)
    assert run.status == "failed"
    assert updated_count == 1


async def test_queued_row_gets_finished_at(db_session: AsyncSession) -> None:
    """Reconciled 'queued' rows must have finished_at set (consistent terminal state)."""
    run = await _create_with_status(db_session, "queued")
    assert run.finished_at is None  # precondition: queued rows have no finished_at

    await repo.reconcile_stale_tool_runs(db_session)

    await _refresh(db_session, run)
    assert run.finished_at is not None


async def test_running_row_becomes_failed(db_session: AsyncSession) -> None:
    """A 'running' row at startup must be transitioned to 'failed'."""
    run = await _create_with_status(db_session, "running")

    updated_count = await repo.reconcile_stale_tool_runs(db_session)

    await _refresh(db_session, run)
    assert run.status == "failed"
    assert updated_count == 1


async def test_running_row_gets_finished_at(db_session: AsyncSession) -> None:
    """Reconciled 'running' rows must have finished_at set."""
    run = await _create_with_status(db_session, "running")

    await repo.reconcile_stale_tool_runs(db_session)

    await _refresh(db_session, run)
    assert run.finished_at is not None


async def test_both_queued_and_running_rows_reconciled(db_session: AsyncSession) -> None:
    """Both queued and running rows are updated in a single call."""
    queued_run = await _create_with_status(db_session, "queued")
    running_run = await _create_with_status(db_session, "running")

    updated_count = await repo.reconcile_stale_tool_runs(db_session)

    await _refresh(db_session, queued_run)
    await _refresh(db_session, running_run)

    assert updated_count == 2
    assert queued_run.status == "failed"
    assert running_run.status == "failed"


# ---------------------------------------------------------------------------
# Terminal rows are never clobbered
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("terminal_status", ["completed", "failed", "timed_out"])
async def test_terminal_row_is_not_touched(db_session: AsyncSession, terminal_status: str) -> None:
    """Rows already in a terminal state must not be modified by reconciliation."""
    finished = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    run = await _create_with_status(db_session, terminal_status)
    # Manually set finished_at to a known value so we can verify it is unchanged.
    from sqlalchemy import update as sa_update

    await db_session.execute(
        sa_update(ToolRun).where(ToolRun.id == run.id).values(finished_at=finished)
    )
    await db_session.flush()

    updated_count = await repo.reconcile_stale_tool_runs(db_session)

    await _refresh(db_session, run)
    assert updated_count == 0
    assert run.status == terminal_status
    # finished_at must be exactly the value we set, not overwritten.
    assert run.finished_at is not None
    assert run.finished_at.replace(tzinfo=None) == finished.replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


async def test_idempotent_when_no_stale_rows(db_session: AsyncSession) -> None:
    """Returns 0 and causes no errors when there are no stale rows."""
    updated_count = await repo.reconcile_stale_tool_runs(db_session)
    assert updated_count == 0


async def test_idempotent_second_call(db_session: AsyncSession) -> None:
    """Calling reconcile twice is safe — the second call is a no-op."""
    await _create_with_status(db_session, "queued")

    first_count = await repo.reconcile_stale_tool_runs(db_session)
    second_count = await repo.reconcile_stale_tool_runs(db_session)

    assert first_count == 1
    assert second_count == 0


# ---------------------------------------------------------------------------
# Row count accuracy
# ---------------------------------------------------------------------------


async def test_returns_correct_count_for_multiple_stale_rows(db_session: AsyncSession) -> None:
    """The return value accurately reflects the number of rows updated."""
    for _ in range(3):
        await _create_with_status(db_session, "queued")
    for _ in range(2):
        await _create_with_status(db_session, "running")

    updated_count = await repo.reconcile_stale_tool_runs(db_session)

    assert updated_count == 5


async def test_only_stale_rows_counted_when_mix_present(db_session: AsyncSession) -> None:
    """Terminal rows do not inflate the returned count."""
    await _create_with_status(db_session, "queued")
    await _create_with_status(db_session, "running")
    completed_run = await _create_with_status(db_session, "completed")
    # Give the completed row a finished_at so it is properly terminal.
    from sqlalchemy import update as sa_update

    await db_session.execute(
        sa_update(ToolRun)
        .where(ToolRun.id == completed_run.id)
        .values(finished_at=datetime.now(tz=UTC))
    )
    await db_session.flush()

    updated_count = await repo.reconcile_stale_tool_runs(db_session)

    assert updated_count == 2
