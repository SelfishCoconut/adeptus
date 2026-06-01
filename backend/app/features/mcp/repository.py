"""Database access for MCP tool runs.

Provides create_tool_run, update_tool_run_result, and list_tool_runs_for_engagement.
All functions accept an AsyncSession and follow the same patterns used across
the rest of the features — module-level async functions, flush/refresh for
server-generated defaults, select() + execute() for reads.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.mcp.models import ToolRun


async def create_tool_run(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    server_name: str,
    tool_name: str,
    args: dict,
) -> ToolRun:
    """Insert a new ToolRun row and return it with server-generated fields populated.

    exit_code and finished_at are NULL (the run is in-flight).
    stdout and stderr default to '' via the server_default on the model.
    flush() + refresh() ensure id and started_at are available before returning.
    The caller is responsible for committing (or not) the transaction.
    """
    tool_run = ToolRun(
        engagement_id=engagement_id,
        server_name=server_name,
        tool_name=tool_name,
        args=args,
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
) -> ToolRun:
    """Update an in-flight ToolRun row with its final results and return it.

    Issues a SQL UPDATE then re-fetches the row so the returned object reflects
    the persisted state.  The caller is responsible for committing.
    """
    await db.execute(
        update(ToolRun)
        .where(ToolRun.id == tool_run_id)
        .values(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            finished_at=finished_at,
        )
    )
    result = await db.execute(select(ToolRun).where(ToolRun.id == tool_run_id))
    return result.scalar_one()


async def list_tool_runs_for_engagement(
    db: AsyncSession,
    engagement_id: UUID,
) -> list[ToolRun]:
    """Return all ToolRun rows for an engagement ordered by started_at DESC."""
    result = await db.execute(
        select(ToolRun)
        .where(ToolRun.engagement_id == engagement_id)
        .order_by(desc(ToolRun.started_at))
    )
    return list(result.scalars().all())
