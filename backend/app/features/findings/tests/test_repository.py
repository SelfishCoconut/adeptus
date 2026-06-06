"""Repository tests for the findings feature (Slice 19 task 5).

These run against a real in-memory SQLite async session (the ``db_session``
fixture). SQLite does not enforce FK constraints unless the ``foreign_keys``
pragma is on, so the FK ON DELETE SET NULL behaviour is verified in a dedicated
test that builds its own pragma-enabled engine.
"""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base
from app.features.findings import repository as repo
from app.features.findings.models import Finding, FindingHistory
from app.features.graph.models import GraphNode

pytestmark = pytest.mark.asyncio


def _uid(value: object) -> UUID:
    """Cast a SQLAlchemy UUID column value to plain uuid.UUID (mirrors graph tests)."""
    return cast(UUID, value)


async def _insert(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    title: str = "Reflected XSS",
    severity: str = "high",
    description: str = "",
    node_id: UUID | None = None,
) -> Finding:
    return await repo.insert_finding(
        db,
        engagement_id=engagement_id,
        title=title,
        description=description,
        severity=severity,
        node_id=node_id,
    )


async def test_insert_and_get_finding(db_session: AsyncSession) -> None:
    eng = uuid4()
    created = await _insert(db_session, engagement_id=eng, title="SQLi on /login")
    await db_session.commit()

    assert created.id is not None
    # Server defaults applied.
    assert created.verification_status == "unverified"
    assert created.remediation_status == "open"
    assert created.deleted is False

    fetched = await repo.get_finding(db_session, eng, _uid(created.id))
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.title == "SQLi on /login"

    # Engagement-scoped: a different engagement does not see it.
    assert await repo.get_finding(db_session, uuid4(), _uid(created.id)) is None


async def test_list_excludes_deleted_by_default(db_session: AsyncSession) -> None:
    eng = uuid4()
    live = await _insert(db_session, engagement_id=eng, title="live")
    gone = await _insert(db_session, engagement_id=eng, title="gone")
    await repo.soft_delete_finding(db_session, finding=gone)
    await db_session.commit()

    items = await repo.list_findings(db_session, eng)
    ids = {f.id for f in items}
    assert live.id in ids
    assert gone.id not in ids


async def test_list_includes_deleted_when_requested(db_session: AsyncSession) -> None:
    eng = uuid4()
    live = await _insert(db_session, engagement_id=eng, title="live")
    gone = await _insert(db_session, engagement_id=eng, title="gone")
    await repo.soft_delete_finding(db_session, finding=gone)
    await db_session.commit()

    items = await repo.list_findings(db_session, eng, include_deleted=True)
    ids = {f.id for f in items}
    assert live.id in ids
    assert gone.id in ids


async def test_list_is_newest_first(db_session: AsyncSession) -> None:
    eng = uuid4()
    first = await _insert(db_session, engagement_id=eng, title="first")
    second = await _insert(db_session, engagement_id=eng, title="second")
    # Set explicit created_at so the ordering is deterministic (SQLite's CURRENT_TIMESTAMP
    # is second-resolution, so two same-second inserts would otherwise tie; production
    # Postgres now() is microsecond-resolution).
    first.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    second.created_at = datetime(2026, 1, 2, tzinfo=UTC)
    await db_session.commit()

    items = await repo.list_findings(db_session, eng)
    # created_at DESC → most recently created first.
    assert [f.id for f in items] == [second.id, first.id]


async def test_soft_delete_hides_from_live_list(db_session: AsyncSession) -> None:
    eng = uuid4()
    f = await _insert(db_session, engagement_id=eng)
    await db_session.commit()
    assert len(await repo.list_findings(db_session, eng)) == 1

    await repo.soft_delete_finding(db_session, finding=f)
    await db_session.commit()
    assert await repo.list_findings(db_session, eng) == []
    # Still retrievable by id (recoverable) and visible with include_deleted.
    assert await repo.get_finding(db_session, eng, _uid(f.id)) is not None
    assert len(await repo.list_findings(db_session, eng, include_deleted=True)) == 1


async def test_history_snapshot_records_prestate(db_session: AsyncSession) -> None:
    eng = uuid4()
    f = await _insert(db_session, engagement_id=eng, title="orig", severity="low")
    await db_session.commit()

    # Snapshot the pre-mutation state, THEN mutate.
    hist = await repo.record_finding_history(db_session, finding=f)
    await repo.update_finding_row(
        db_session, finding=f, fields={"title": "changed", "severity": "critical"}
    )
    await db_session.commit()

    assert hist.title == "orig"
    assert hist.severity == "low"
    assert hist.verification_status == "unverified"
    assert hist.deleted is False
    # The live row now reflects the mutation.
    assert f.title == "changed"
    assert f.severity == "critical"

    rows = (
        (await db_session.execute(select(FindingHistory).where(FindingHistory.finding_id == f.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 1


async def test_set_verification_and_remediation(db_session: AsyncSession) -> None:
    eng = uuid4()
    f = await _insert(db_session, engagement_id=eng)
    await db_session.commit()

    await repo.set_verification(db_session, finding=f, status="false_positive")
    await repo.set_remediation(db_session, finding=f, status="risk_accepted")
    await db_session.commit()

    refetched = await repo.get_finding(db_session, eng, _uid(f.id))
    assert refetched is not None
    assert refetched.verification_status == "false_positive"
    assert refetched.remediation_status == "risk_accepted"


async def test_node_link_validation_rejects_cross_engagement_node(
    db_session: AsyncSession,
) -> None:
    eng_a = uuid4()
    eng_b = uuid4()

    # A live node in engagement A.
    node = GraphNode(engagement_id=eng_a, type="host", label="10.0.0.5", properties={})
    db_session.add(node)
    await db_session.flush()

    # A soft-deleted node in engagement A.
    dead = GraphNode(
        engagement_id=eng_a, type="host", label="10.0.0.6", properties={}, deleted=True
    )
    db_session.add(dead)
    await db_session.flush()
    await db_session.commit()

    # Same engagement, live → ok.
    assert await repo.node_exists_in_engagement(db_session, eng_a, _uid(node.id)) is True
    # Cross-engagement → rejected (not disclosed).
    assert await repo.node_exists_in_engagement(db_session, eng_b, _uid(node.id)) is False
    # Soft-deleted node → rejected.
    assert await repo.node_exists_in_engagement(db_session, eng_a, _uid(dead.id)) is False
    # Nonexistent node → rejected.
    assert await repo.node_exists_in_engagement(db_session, eng_a, uuid4()) is False


async def test_node_set_null_on_node_hard_delete() -> None:
    """FK ON DELETE SET NULL: hard-deleting a linked node nulls the finding's
    node_id and the finding survives (Risk 3). Uses a dedicated SQLite engine with
    foreign_keys enforcement on (the shared fixture leaves it off)."""
    from app.features.engagements.models import Engagement
    from app.features.findings.tests.conftest import _apply_sqlite_patches

    # Apply the full SQLite-compat patch set (uuid defaults + Session.ip → Text)
    # directly so this test is self-sufficient and create_all never trips on the
    # INET column regardless of test ordering (pytest-randomly).
    _apply_sqlite_patches()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    # Enable FK enforcement on every raw connection so ON DELETE SET NULL fires.
    @event.listens_for(engine.sync_engine, "connect")
    def _fk_pragma(dbapi_conn: Any, _record: Any) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as db:
            # Real engagement parent (FK enforcement is on in this engine).
            engagement = Engagement(name="e", scope="s", status="active")
            db.add(engagement)
            await db.flush()
            eng = _uid(engagement.id)
            node = GraphNode(engagement_id=eng, type="host", label="10.0.0.7", properties={})
            db.add(node)
            await db.flush()
            f = await repo.insert_finding(
                db,
                engagement_id=eng,
                title="vuln on host",
                description="",
                severity="high",
                node_id=_uid(node.id),
            )
            await db.commit()
            assert f.node_id == node.id
            finding_id = _uid(f.id)  # capture before expiring the identity map

            # Hard-delete the node.
            await db.delete(node)
            await db.commit()
            # Expire the identity map so the re-fetch reads the FK-nulled row from
            # the DB rather than the stale in-session object.
            db.expire_all()

            survivor = await repo.get_finding(db, eng, finding_id)
            assert survivor is not None  # finding outlives its node
            assert survivor.node_id is None  # FK SET NULL
    finally:
        await engine.dispose()
