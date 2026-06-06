"""Integration tests for the findings feature (Slice 19 test plan).

Marked ``integration``: excluded from the default ``make test-backend`` run
(``addopts = -m 'not integration'`` in pyproject.toml). Run explicitly with:

  cd backend && uv run pytest -m integration \\
    app/features/findings/tests/test_integration.py -v

Prerequisites:
  - Postgres reachable at the default compose DSN or ADEPTUS_TEST_DATABASE_URL.
These tests skip automatically when Postgres is unreachable.

Two tests from the slice-19 test plan (Integration section):
  1. ``test_finding_lifecycle_roundtrip`` — create (unverified/open) → verify →
     remediation fixed → soft-delete (hidden) → re-list with include_deleted
     (present); one audit entry per mutation with the right action, and the
     hash-chain still verifies (verify-chain) after all the finding_* entries.
  2. ``test_finding_linked_to_graph_node`` — create a node, link a finding to it,
     hard-delete the node, assert the finding's node_id becomes NULL (FK ON
     DELETE SET NULL) and the finding survives.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import Base, get_engine, get_sessionmaker
from app.features.audit import verify as audit_verify
from app.features.audit.models import AuditEntry
from app.features.auth import models as auth_models  # noqa: F401 — register ORM metadata
from app.features.auth import repository as auth_repo
from app.features.engagements import models as eng_models  # noqa: F401 — register ORM metadata
from app.features.engagements import repository as eng_repo
from app.features.findings import models as findings_models  # noqa: F401 — register ORM metadata
from app.features.findings import repository as repo
from app.features.findings import service
from app.features.findings.schemas import (
    FindingCreate,
    RemediationStatus,
    RemediationUpdate,
    Severity,
    VerificationStatus,
    VerificationUpdate,
)
from app.features.graph import models as graph_models  # noqa: F401 — register ORM metadata
from app.features.graph.models import GraphNode

pytestmark = pytest.mark.integration

_DEFAULT_DSN = "postgresql+asyncpg://adeptus:adeptus@localhost:5432/adeptus"
_MEMBER_PW = "correcthorse"
_MEMBER_HASH = PasswordHasher().hash(_MEMBER_PW)


def _dsn() -> str:
    import os

    return os.environ.get("ADEPTUS_TEST_DATABASE_URL") or _DEFAULT_DSN


@pytest_asyncio.fixture
async def pg_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """Session factory scoped to a throwaway Postgres schema. Skips if PG is down."""
    monkeypatch.setenv("DATABASE_URL", _dsn())
    monkeypatch.setenv("ADEPTUS_ADMIN_USER", "admin_it")
    monkeypatch.setenv(
        "ADEPTUS_ADMIN_PASSWORD_HASH",
        "$argon2id$v=19$m=65536,t=3,p=4$dGVzdHNhbHQ$hashhashhashhashhashhashhashhashhashhashhas",
    )
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()

    schema = f"findings_it_{uuid.uuid4().hex[:12]}"
    admin_engine = create_async_engine(_dsn(), isolation_level="AUTOCOMMIT")
    try:
        async with admin_engine.connect() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    except Exception as exc:  # noqa: BLE001
        await admin_engine.dispose()
        pytest.skip(f"Postgres not available for integration tests: {exc}")

    engine = create_async_engine(_dsn(), connect_args={"server_settings": {"search_path": schema}})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        yield factory
    finally:
        await engine.dispose()
        async with admin_engine.connect() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin_engine.dispose()
        get_settings.cache_clear()
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()


@pytest_asyncio.fixture
async def member_and_engagement(
    pg_factory: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed a member user + engagement; return (engagement_id, user_id)."""
    async with pg_factory() as session:
        member = await auth_repo.create_user(
            session,
            username=f"member_find_{uuid.uuid4().hex[:8]}",
            password_hash=_MEMBER_HASH,
            role="user",
        )
        await session.flush()
        member_id = member.id
        engagement = await eng_repo.create_engagement(
            session,
            name="Findings Integration Test",
            scope="10.0.0.0/24",
            client_info=None,
            owner_id=member_id,  # type: ignore[arg-type]
        )
        await session.commit()
        return engagement.id, member_id  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_finding_lifecycle_roundtrip(
    pg_factory: async_sessionmaker[AsyncSession],
    member_and_engagement: tuple[uuid.UUID, uuid.UUID],
) -> None:
    engagement_id, user_id = member_and_engagement

    async with pg_factory() as session:
        created = await service.create_finding(
            session,
            engagement_id,
            user_id,
            FindingCreate(title="Reflected XSS on /search", severity=Severity.high),
        )
        assert created.verification_status is VerificationStatus.unverified
        assert created.remediation_status is RemediationStatus.open
        finding_id = created.id

    async with pg_factory() as session:
        verified = await service.set_verification(
            session,
            engagement_id,
            finding_id,
            user_id,
            VerificationUpdate(verification_status=VerificationStatus.verified),
        )
        assert verified.verification_status is VerificationStatus.verified

    async with pg_factory() as session:
        fixed = await service.set_remediation(
            session,
            engagement_id,
            finding_id,
            user_id,
            RemediationUpdate(remediation_status=RemediationStatus.fixed),
        )
        assert fixed.remediation_status is RemediationStatus.fixed

    async with pg_factory() as session:
        await service.delete_finding(session, engagement_id, finding_id, user_id)

    async with pg_factory() as session:
        live = await service.list_findings(session, engagement_id, user_id)
        assert live.items == []
        with_deleted = await service.list_findings(
            session, engagement_id, user_id, include_deleted=True
        )
        assert len(with_deleted.items) == 1
        assert with_deleted.items[0].deleted is True

    # One audit entry per mutation, with the correct actions in order.
    async with pg_factory() as session:
        rows = (
            (
                await session.execute(
                    select(AuditEntry)
                    .where(AuditEntry.target_id == str(finding_id))
                    .order_by(AuditEntry.seq)
                )
            )
            .scalars()
            .all()
        )
        actions = [r.action for r in rows]
        assert actions == [
            "finding_created",
            "finding_verification_changed",
            "finding_remediation_changed",
            "finding_deleted",
        ]
        assert all(r.actor_user_id == user_id for r in rows)

        # The hash-chain still verifies after the finding_* entries + CHECK widening.
        ok, count, first_break = await audit_verify.verify(session)
        assert ok, f"chain broke at {first_break}"
        assert count >= 4

    # A FindingHistory snapshot exists for the soft-delete (recoverability).
    async with pg_factory() as session:
        hist = (
            (
                await session.execute(
                    select(findings_models.FindingHistory).where(
                        findings_models.FindingHistory.finding_id == finding_id
                    )
                )
            )
            .scalars()
            .all()
        )
        # verify + remediation + delete each snapshot the pre-state (create does not).
        assert len(hist) == 3


@pytest.mark.asyncio
async def test_finding_linked_to_graph_node(
    pg_factory: async_sessionmaker[AsyncSession],
    member_and_engagement: tuple[uuid.UUID, uuid.UUID],
) -> None:
    engagement_id, user_id = member_and_engagement

    # Insert a live graph node directly (the finding only references it by FK).
    async with pg_factory() as session:
        node = GraphNode(engagement_id=engagement_id, type="host", label="10.0.0.5", properties={})
        session.add(node)
        await session.commit()
        await session.refresh(node)
        node_id: uuid.UUID = node.id  # type: ignore[assignment]

    async with pg_factory() as session:
        created = await service.create_finding(
            session,
            engagement_id,
            user_id,
            FindingCreate(title="vuln on host", severity=Severity.medium, node_id=node_id),
        )
        assert created.node_id == node_id
        finding_id = created.id

    # Hard-delete the node — FK ON DELETE SET NULL must null the link, not the finding.
    async with pg_factory() as session:
        await session.execute(delete(GraphNode).where(GraphNode.id == node_id))
        await session.commit()

    async with pg_factory() as session:
        survivor = await repo.get_finding(session, engagement_id, finding_id)
        assert survivor is not None  # finding outlives its node
        assert survivor.node_id is None  # FK SET NULL fired
