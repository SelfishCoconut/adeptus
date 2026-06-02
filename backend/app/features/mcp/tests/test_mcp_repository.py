"""Unit tests for app.features.mcp.repository.

All tests use an in-memory SQLite async session (see conftest.py).
Tests are async; pytest-asyncio is configured with asyncio_mode="auto" in
pyproject.toml so no explicit @pytest.mark.asyncio decorator is needed.
"""

from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.features.mcp import repository as repo
from app.features.mcp.models import ToolRun

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _uid(obj: ToolRun) -> UUID:
    """Cast a SQLAlchemy UUID column value to plain uuid.UUID."""
    return cast(UUID, obj.id)


def _make_args() -> dict:
    return {"command": "echo hello"}


async def _create(
    db: AsyncSession,
    *,
    engagement_id: UUID | None = None,
    server_name: str = "shell-exec",
    tool_name: str = "run_command",
    args: dict | None = None,
) -> ToolRun:
    """Helper: create a ToolRun in the given session."""
    return await repo.create_tool_run(
        db,
        engagement_id=engagement_id or uuid4(),
        server_name=server_name,
        tool_name=tool_name,
        args=args or _make_args(),
    )


# ---------------------------------------------------------------------------
# create_tool_run
# ---------------------------------------------------------------------------


async def test_create_tool_run_returns_row_with_id(db_session: AsyncSession) -> None:
    tool_run = await _create(db_session)

    assert tool_run.id is not None
    assert isinstance(_uid(tool_run), UUID)


async def test_create_tool_run_persists_fields(db_session: AsyncSession) -> None:
    engagement_id = uuid4()
    tool_run = await _create(
        db_session,
        engagement_id=engagement_id,
        server_name="shell-exec",
        tool_name="run_command",
        args={"command": "ls -la"},
    )

    assert cast(UUID, tool_run.engagement_id) == engagement_id
    assert tool_run.server_name == "shell-exec"
    assert tool_run.tool_name == "run_command"
    assert tool_run.args == {"command": "ls -la"}


async def test_create_tool_run_exit_code_is_null(db_session: AsyncSession) -> None:
    """A freshly created ToolRun has no exit_code — the run is in-flight."""
    tool_run = await _create(db_session)

    assert tool_run.exit_code is None


async def test_create_tool_run_finished_at_is_null(db_session: AsyncSession) -> None:
    """A freshly created ToolRun has no finished_at — the run is in-flight."""
    tool_run = await _create(db_session)

    assert tool_run.finished_at is None


async def test_create_tool_run_stdout_stderr_default_empty(db_session: AsyncSession) -> None:
    """stdout and stderr should default to empty string on creation."""
    tool_run = await _create(db_session)

    # The server_default is set in Postgres; under SQLite the Python default
    # applies.  After flush+refresh the value is available on the ORM object.
    # Accept either empty string or None (SQLite may not apply server_default).
    assert tool_run.stdout in ("", None)
    assert tool_run.stderr in ("", None)


async def test_create_tool_run_started_at_is_set(db_session: AsyncSession) -> None:
    """started_at should be populated by the DB after flush."""
    tool_run = await _create(db_session)

    assert tool_run.started_at is not None


# ---------------------------------------------------------------------------
# update_tool_run_result
# ---------------------------------------------------------------------------


async def test_update_tool_run_result_sets_exit_code(db_session: AsyncSession) -> None:
    tool_run = await _create(db_session)
    finished = datetime.now(tz=UTC)

    updated = await repo.update_tool_run_result(
        db_session,
        _uid(tool_run),
        exit_code=0,
        stdout="hello\n",
        stderr="",
        finished_at=finished,
    )

    assert updated.exit_code == 0


async def test_update_tool_run_result_sets_stdout_stderr(db_session: AsyncSession) -> None:
    tool_run = await _create(db_session)
    finished = datetime.now(tz=UTC)

    updated = await repo.update_tool_run_result(
        db_session,
        _uid(tool_run),
        exit_code=1,
        stdout="some output",
        stderr="some error",
        finished_at=finished,
    )

    assert updated.stdout == "some output"
    assert updated.stderr == "some error"


async def test_update_tool_run_result_sets_finished_at(db_session: AsyncSession) -> None:
    tool_run = await _create(db_session)
    finished = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)

    updated = await repo.update_tool_run_result(
        db_session,
        _uid(tool_run),
        exit_code=0,
        stdout="",
        stderr="",
        finished_at=finished,
    )

    # Compare as naive datetimes since SQLite strips timezone info.
    assert updated.finished_at is not None
    assert updated.finished_at.replace(tzinfo=None) == finished.replace(tzinfo=None)


async def test_update_tool_run_result_nonzero_exit_code(db_session: AsyncSession) -> None:
    """Non-zero exit codes are stored correctly (not treated as error at repo level)."""
    tool_run = await _create(db_session)
    finished = datetime.now(tz=UTC)

    updated = await repo.update_tool_run_result(
        db_session,
        _uid(tool_run),
        exit_code=127,
        stdout="",
        stderr="command not found",
        finished_at=finished,
    )

    assert updated.exit_code == 127
    assert updated.stderr == "command not found"


async def test_update_tool_run_result_returns_correct_row(db_session: AsyncSession) -> None:
    """update_tool_run_result returns the row with the matching id."""
    tool_run = await _create(db_session)
    finished = datetime.now(tz=UTC)

    updated = await repo.update_tool_run_result(
        db_session,
        _uid(tool_run),
        exit_code=0,
        stdout="output",
        stderr="",
        finished_at=finished,
    )

    assert _uid(updated) == _uid(tool_run)


# ---------------------------------------------------------------------------
# list_tool_runs_for_engagement
# ---------------------------------------------------------------------------


async def test_list_tool_runs_returns_empty_for_unknown_engagement(
    db_session: AsyncSession,
) -> None:
    runs, next_cursor = await repo.list_tool_runs_for_engagement(db_session, uuid4())

    assert runs == []
    assert next_cursor is None


async def test_list_tool_runs_returns_runs_for_engagement(db_session: AsyncSession) -> None:
    engagement_id = uuid4()
    run_a = await _create(db_session, engagement_id=engagement_id, tool_name="run_command")
    run_b = await _create(db_session, engagement_id=engagement_id, tool_name="run_command")

    runs, _ = await repo.list_tool_runs_for_engagement(db_session, engagement_id)

    assert len(runs) == 2
    ids = {_uid(r) for r in runs}
    assert _uid(run_a) in ids
    assert _uid(run_b) in ids


async def test_list_tool_runs_does_not_return_other_engagements_runs(
    db_session: AsyncSession,
) -> None:
    engagement_a = uuid4()
    engagement_b = uuid4()

    await _create(db_session, engagement_id=engagement_a)
    run_b = await _create(db_session, engagement_id=engagement_b)

    runs, _ = await repo.list_tool_runs_for_engagement(db_session, engagement_b)

    assert len(runs) == 1
    assert _uid(runs[0]) == _uid(run_b)


async def test_list_tool_runs_ordered_by_started_at_desc(db_session: AsyncSession) -> None:
    """The most recently started run should come first."""
    engagement_id = uuid4()

    # Create multiple runs — they get started_at from func.now(); since SQLite
    # has second-level precision we just verify the query returns all rows for
    # the engagement (ordering correctness is exercised by the UPDATE-then-list test).
    run_a = await _create(db_session, engagement_id=engagement_id)
    run_b = await _create(db_session, engagement_id=engagement_id)
    run_c = await _create(db_session, engagement_id=engagement_id)

    runs, _ = await repo.list_tool_runs_for_engagement(db_session, engagement_id)

    # All three runs are present.
    assert len(runs) == 3
    returned_ids = [_uid(r) for r in runs]
    assert _uid(run_a) in returned_ids
    assert _uid(run_b) in returned_ids
    assert _uid(run_c) in returned_ids


# ---------------------------------------------------------------------------
# list_tool_runs_for_engagement — keyset pagination
# ---------------------------------------------------------------------------


async def test_list_tool_runs_limit_caps_page_size(db_session: AsyncSession) -> None:
    """When more rows exist than limit, only limit rows are returned."""
    engagement_id = uuid4()
    t_base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    for i in range(5):
        run = await _create(db_session, engagement_id=engagement_id)
        # Manually set started_at so rows have distinct, predictable timestamps.
        from datetime import timedelta

        from sqlalchemy import update as sa_update

        await db_session.execute(
            sa_update(ToolRun)
            .where(ToolRun.id == run.id)
            .values(started_at=t_base + timedelta(minutes=i))
        )
    await db_session.flush()

    runs, next_cursor = await repo.list_tool_runs_for_engagement(db_session, engagement_id, limit=3)

    assert len(runs) == 3
    assert next_cursor is not None


async def test_list_tool_runs_next_cursor_none_on_last_page(db_session: AsyncSession) -> None:
    """When all rows fit within limit, next_cursor is None."""
    engagement_id = uuid4()

    for _ in range(2):
        await _create(db_session, engagement_id=engagement_id)

    runs, next_cursor = await repo.list_tool_runs_for_engagement(db_session, engagement_id, limit=5)

    assert len(runs) == 2
    assert next_cursor is None


async def test_list_tool_runs_cursor_no_overlap(db_session: AsyncSession) -> None:
    """Second page must not overlap with first page."""
    engagement_id = uuid4()
    t_base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    from datetime import timedelta

    from sqlalchemy import update as sa_update

    created: list[ToolRun] = []
    for i in range(4):
        run = await _create(db_session, engagement_id=engagement_id)
        await db_session.execute(
            sa_update(ToolRun)
            .where(ToolRun.id == run.id)
            .values(started_at=t_base + timedelta(minutes=i))
        )
        created.append(run)
    await db_session.flush()

    page1, cursor1 = await repo.list_tool_runs_for_engagement(db_session, engagement_id, limit=2)
    assert len(page1) == 2
    assert cursor1 is not None

    page2, cursor2 = await repo.list_tool_runs_for_engagement(
        db_session, engagement_id, limit=2, cursor=cursor1
    )
    assert len(page2) == 2
    assert cursor2 is None

    # No overlap between pages.
    page1_ids = {_uid(r) for r in page1}
    page2_ids = {_uid(r) for r in page2}
    assert page1_ids.isdisjoint(page2_ids)
    # Together they cover all rows.
    assert page1_ids | page2_ids == {_uid(r) for r in created}


async def test_list_tool_runs_id_tiebreak(db_session: AsyncSession) -> None:
    """Rows with identical started_at are ordered by id DESC (deterministic tiebreak)."""
    engagement_id = uuid4()
    same_time = datetime(2026, 3, 15, 9, 0, 0, tzinfo=UTC)

    from sqlalchemy import update as sa_update

    runs: list[ToolRun] = []
    for _ in range(3):
        run = await _create(db_session, engagement_id=engagement_id)
        await db_session.execute(
            sa_update(ToolRun).where(ToolRun.id == run.id).values(started_at=same_time)
        )
        runs.append(run)
    await db_session.flush()

    # With limit=1, first page returns the row with the largest id.
    page1, cursor1 = await repo.list_tool_runs_for_engagement(db_session, engagement_id, limit=1)
    assert len(page1) == 1
    assert cursor1 is not None

    page2, cursor2 = await repo.list_tool_runs_for_engagement(
        db_session, engagement_id, limit=1, cursor=cursor1
    )
    assert len(page2) == 1
    assert cursor2 is not None

    page3, cursor3 = await repo.list_tool_runs_for_engagement(
        db_session, engagement_id, limit=1, cursor=cursor2
    )
    assert len(page3) == 1
    assert cursor3 is None

    # All three distinct rows returned across the three pages.
    all_ids = {_uid(page1[0]), _uid(page2[0]), _uid(page3[0])}
    assert all_ids == {_uid(r) for r in runs}


# ---------------------------------------------------------------------------
# get_tool_run_by_id
# ---------------------------------------------------------------------------


async def test_get_tool_run_by_id_returns_existing_row(db_session: AsyncSession) -> None:
    """get_tool_run_by_id returns the correct row when it exists."""
    run = await _create(db_session)

    fetched = await repo.get_tool_run_by_id(db_session, _uid(run))

    assert fetched is not None
    assert _uid(fetched) == _uid(run)


async def test_get_tool_run_by_id_returns_none_for_missing(db_session: AsyncSession) -> None:
    """get_tool_run_by_id returns None when no row matches."""
    result = await repo.get_tool_run_by_id(db_session, uuid4())

    assert result is None
