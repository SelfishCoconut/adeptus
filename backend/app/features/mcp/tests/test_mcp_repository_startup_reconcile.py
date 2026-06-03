"""Tests for the startup reconciliation routine in app.features.mcp.repository.

Covers reconcile_stale_tool_runs — the function called on every backend startup
to mark phantom 'queued', 'running', and 'awaiting_decision' rows as 'failed'.

After a restart the in-process admission queue, slot pool, and
timeout-decision rendezvous are all gone, so any row left in a non-terminal
transient state is a phantom whose background task no longer exists.  The
routine issues a single idempotent UPDATE; these tests verify:

- A 'queued' row becomes 'failed' with a non-null finished_at.
- A 'running' row becomes 'failed' with a non-null finished_at.
- An 'awaiting_decision' row becomes 'failed' with a non-null finished_at
  (the in-process timeout rendezvous does not survive a restart — Decision 6 /
  Risk 8 / Slice 06 task 8).
- Terminal rows ('completed', 'failed', 'timed_out', 'killed') are left
  untouched, including the 'killed' status added in Slice 06.
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
# awaiting_decision rows are reconciled (Slice 06 task 8)
# ---------------------------------------------------------------------------


async def test_awaiting_decision_row_becomes_failed(db_session: AsyncSession) -> None:
    """An 'awaiting_decision' row at startup must be transitioned to 'failed'.

    A run in awaiting_decision has released its concurrency slot and is parked
    waiting for a human kill/extend/wait decision via an in-process asyncio.Event
    rendezvous (Decision 6 / Risk 8).  That rendezvous does not survive a
    backend restart, so the row must be marked failed to avoid zombie rows.
    """
    run = await _create_with_status(db_session, "awaiting_decision")

    updated_count = await repo.reconcile_stale_tool_runs(db_session)

    await _refresh(db_session, run)
    assert run.status == "failed"
    assert updated_count == 1


async def test_awaiting_decision_row_gets_finished_at(db_session: AsyncSession) -> None:
    """Reconciled 'awaiting_decision' rows must have finished_at set."""
    run = await _create_with_status(db_session, "awaiting_decision")
    assert run.finished_at is None  # precondition: parked rows have no finished_at

    await repo.reconcile_stale_tool_runs(db_session)

    await _refresh(db_session, run)
    assert run.finished_at is not None


async def test_all_three_phantom_statuses_reconciled_together(db_session: AsyncSession) -> None:
    """queued, running, and awaiting_decision rows are all updated in a single call."""
    queued_run = await _create_with_status(db_session, "queued")
    running_run = await _create_with_status(db_session, "running")
    awaiting_run = await _create_with_status(db_session, "awaiting_decision")

    updated_count = await repo.reconcile_stale_tool_runs(db_session)

    await _refresh(db_session, queued_run)
    await _refresh(db_session, running_run)
    await _refresh(db_session, awaiting_run)

    assert updated_count == 3
    assert queued_run.status == "failed"
    assert running_run.status == "failed"
    assert awaiting_run.status == "failed"


async def test_killed_row_is_not_touched(db_session: AsyncSession) -> None:
    """A 'killed' row (Slice 06) must not be modified by reconciliation.

    'killed' is a terminal status — the run was already stopped by a per-tool
    kill or an engagement pause.  Reconciliation must leave it untouched.
    """
    finished = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    run = await _create_with_status(db_session, "killed")
    from sqlalchemy import update as sa_update

    await db_session.execute(
        sa_update(ToolRun).where(ToolRun.id == run.id).values(finished_at=finished)
    )
    await db_session.flush()

    updated_count = await repo.reconcile_stale_tool_runs(db_session)

    await _refresh(db_session, run)
    assert updated_count == 0
    assert run.status == "killed"
    assert run.finished_at is not None
    assert run.finished_at.replace(tzinfo=None) == finished.replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Terminal rows are never clobbered
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("terminal_status", ["completed", "failed", "timed_out", "killed"])
async def test_terminal_row_is_not_touched(db_session: AsyncSession, terminal_status: str) -> None:
    """Rows already in a terminal state must not be modified by reconciliation.

    Includes 'killed' (Slice 06) — a run already stopped by a per-tool kill or
    engagement pause is terminal and must not be re-processed on startup.
    """
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
    """The return value accurately reflects the number of rows updated.

    Includes awaiting_decision rows (Slice 06) in the count.
    """
    for _ in range(3):
        await _create_with_status(db_session, "queued")
    for _ in range(2):
        await _create_with_status(db_session, "running")
    for _ in range(1):
        await _create_with_status(db_session, "awaiting_decision")

    updated_count = await repo.reconcile_stale_tool_runs(db_session)

    assert updated_count == 6


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
