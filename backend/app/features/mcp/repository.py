"""Database access for MCP tool runs.

Provides create_tool_run, update_tool_run_result, list_tool_runs_for_engagement,
get_tool_run_by_id, and reconcile_stale_tool_runs.
All functions accept an AsyncSession and follow the same patterns used across
the rest of the features — module-level async functions, flush/refresh for
server-generated defaults, select() + execute() for reads.
"""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, desc, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.mcp.models import ToolRun


async def create_tool_run(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    server_name: str,
    tool_name: str,
    args: dict[str, Any],
    status: str = "completed",
    preset_name: str | None = None,
) -> ToolRun:
    """Insert a new ToolRun row and return it with server-generated fields populated.

    exit_code and finished_at are NULL (the run is in-flight).
    stdout and stderr default to '' via the server_default on the model.
    flush() + refresh() ensure id and started_at are available before returning.
    The caller is responsible for committing (or not) the transaction.

    Args:
        status:      Initial status for the run.  Defaults to ``'completed'`` so
                     existing callers (sync path) are unaffected.  Pass
                     ``'running'`` for the async path.
        preset_name: Optional name of the preset the user selected.
    """
    tool_run = ToolRun(
        engagement_id=engagement_id,
        server_name=server_name,
        tool_name=tool_name,
        args=args,
        status=status,
        preset_name=preset_name,
    )
    db.add(tool_run)
    await db.flush()
    await db.refresh(tool_run)
    return tool_run


async def update_tool_run_result(
    db: AsyncSession,
    tool_run_id: UUID,
    *,
    exit_code: int,
    stdout: str,
    stderr: str,
    finished_at: datetime,
    status: str = "completed",
) -> ToolRun:
    """Update an in-flight ToolRun row with its final results and return it.

    Issues a SQL UPDATE then re-fetches the row so the returned object reflects
    the persisted state.  The caller is responsible for committing.

    Args:
        status: Final status for the run.  Defaults to ``'completed'`` so
                existing callers (sync path) are unaffected.  Pass
                ``'failed'`` or ``'timed_out'`` as appropriate.
    """
    await db.execute(
        update(ToolRun)
        .where(ToolRun.id == tool_run_id)
        .values(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            finished_at=finished_at,
            status=status,
        )
    )
    result = await db.execute(select(ToolRun).where(ToolRun.id == tool_run_id))
    return result.scalar_one()


async def update_tool_run_status(
    db: AsyncSession,
    tool_run_id: UUID,
    *,
    status: str,
    started_at: datetime | None = None,
) -> None:
    """Update only the status (and optionally started_at) of a ToolRun row.

    Used by the admission manager callbacks to transition a row from 'queued'
    to 'running' (setting started_at = admission time per Decision 6) without
    touching exit_code / stdout / stderr / finished_at.

    The caller is responsible for committing the transaction.
    """
    values: dict[str, object] = {"status": status}
    if started_at is not None:
        values["started_at"] = started_at
    await db.execute(update(ToolRun).where(ToolRun.id == tool_run_id).values(**values))


async def list_tool_runs_for_engagement(
    db: AsyncSession,
    engagement_id: UUID,
    *,
    limit: int = 20,
    cursor: tuple[datetime, UUID] | None = None,
) -> tuple[list[ToolRun], tuple[datetime, UUID] | None]:
    """Return a paginated page of ToolRun rows for an engagement, newest first.

    Ordering is strictly (started_at DESC, id DESC) — id serves as a deterministic
    tiebreak for rows that share the same started_at timestamp.

    When a cursor ``(c_started, c_id)`` is provided only rows strictly *after* it
    in the sort order are returned, i.e. rows where::

        started_at < c_started OR (started_at == c_started AND id < c_id)

    The SQLAlchemy ``or_``/``and_`` form is used rather than a row-value comparison
    so the query is compatible with both PostgreSQL and the SQLite test engine.

    Fetches ``limit + 1`` rows to detect whether a next page exists.  Returns
    ``(rows[:limit], next_cursor)`` where ``next_cursor`` is ``None`` when there
    are no further rows, otherwise the ``(started_at, id)`` pair of the last row
    in the returned page.
    """
    stmt = (
        select(ToolRun)
        .where(ToolRun.engagement_id == engagement_id)
        .order_by(desc(ToolRun.started_at), desc(ToolRun.id))
        .limit(limit + 1)
    )

    if cursor is not None:
        c_started, c_id = cursor
        stmt = stmt.where(
            or_(
                ToolRun.started_at < c_started,
                and_(ToolRun.started_at == c_started, ToolRun.id < c_id),
            )
        )

    result = await db.execute(stmt)
    rows = list(result.scalars().all())

    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor: tuple[datetime, UUID] | None = (last.started_at, last.id)  # type: ignore[assignment]
    else:
        next_cursor = None

    return rows, next_cursor


async def get_tool_run_by_id(db: AsyncSession, tool_run_id: UUID) -> ToolRun | None:
    """Return the ToolRun row with the given id, or None if not found."""
    result = await db.execute(select(ToolRun).where(ToolRun.id == tool_run_id))
    return result.scalar_one_or_none()


async def reconcile_stale_tool_runs(db: AsyncSession) -> int:
    """Mark any phantom-queued or in-flight rows as failed on startup.

    After a backend restart the in-process admission queue and slot pool are
    empty, so any ``tool_runs`` row still in ``status='queued'`` or
    ``status='running'`` is a phantom — its background task no longer exists.
    This function performs a single idempotent UPDATE that:

    - Sets ``status='failed'`` for all rows where
      ``status IN ('queued', 'running')``.
    - Sets ``finished_at`` to the current wall-clock time (UTC) for those
      rows, keeping terminal rows consistent (every failed row has a
      ``finished_at``).

    Terminal rows (``completed``, ``failed``, ``timed_out``) are never touched
    because the WHERE clause matches only the two non-terminal phantom states.

    The caller is responsible for committing the transaction.

    Returns the number of rows updated (0 when there are no stale rows, which
    is the normal steady-state case).

    Note: full crash-recovery semantics (preserving stdout/stderr fragments,
    re-attaching WS clients, etc.) are deferred to Slice 38.  This function
    is intentionally minimal — phantom-status cleanup only.
    """
    now = datetime.now(tz=UTC)
    result = await db.execute(
        update(ToolRun)
        .where(ToolRun.status.in_(["queued", "running"]))
        .values(status="failed", finished_at=now)
    )
    return cast(int, result.rowcount)  # type: ignore[attr-defined]
