"""Integration tests for the graph single-writer process (ADR-0001, task 4).

Marked ``integration``: excluded from the default ``make test-backend`` run
(``addopts = -m 'not integration'`` in pyproject.toml).  Run explicitly with:

  cd backend && uv run pytest -m integration \\
    app/features/graph/tests/test_integration.py -v

Prerequisites:
  - Postgres reachable at the default compose DSN or ADEPTUS_TEST_DATABASE_URL.
  - The ``adeptus`` DB user and database exist (``make dev`` sets these up).

These tests skip automatically when Postgres is unreachable so they are safe to
run on hosts without the full stack.

Two tests from the slice-07 test plan (Integration section):
  1. ``test_concurrent_writes_serialize_via_writer`` — fires many concurrent
     create/update operations against ONE engagement through the writer, then
     asserts the final ``read_graph`` is internally consistent and ALL writes
     are present.
  2. ``test_soft_delete_then_undo_roundtrip`` — create a node → soft-delete it
     (gone from live graph, present in history) → undo it (reappears in live
     graph).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import Base, get_engine, get_sessionmaker
from app.features.auth import models as auth_models  # noqa: F401 — register ORM metadata
from app.features.auth import repository as auth_repo
from app.features.engagements import models as eng_models  # noqa: F401 — register ORM metadata
from app.features.engagements import repository as eng_repo
from app.features.graph import models as graph_models  # noqa: F401 — register ORM metadata
from app.features.graph import repository as repo
from app.features.graph import writer as gw

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_DSN = "postgresql+asyncpg://adeptus:adeptus@localhost:5432/adeptus"
_MEMBER_PW = "correcthorse"
_MEMBER_HASH = PasswordHasher().hash(_MEMBER_PW)


def _dsn() -> str:
    return os.environ.get("ADEPTUS_TEST_DATABASE_URL") or _DEFAULT_DSN


# ---------------------------------------------------------------------------
# Session factory scoped to a throwaway Postgres schema (mirrors mcp integration)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def pg_schema_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """Session factory scoped to a throwaway Postgres schema.

    Mirrors the pattern from mcp/tests/test_concurrency_integration.py.
    Skips if Postgres is not reachable.
    """
    monkeypatch.setenv("DATABASE_URL", _dsn())
    monkeypatch.setenv("ADEPTUS_ADMIN_USER", "admin_it")
    monkeypatch.setenv(
        "ADEPTUS_ADMIN_PASSWORD_HASH",
        "$argon2id$v=19$m=65536,t=3,p=4$dGVzdHNhbHQ$hashhashhashhashhashhashhashhashhashhashhas",
    )
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()

    schema = f"graph_it_{uuid.uuid4().hex[:12]}"
    admin_engine = create_async_engine(_dsn(), isolation_level="AUTOCOMMIT")
    try:
        async with admin_engine.connect() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    except Exception as exc:  # noqa: BLE001
        await admin_engine.dispose()
        pytest.skip(f"Postgres not available for integration tests: {exc}")

    engine = create_async_engine(
        _dsn(),
        connect_args={"server_settings": {"search_path": schema}},
    )
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def pg_engagement_id(
    pg_schema_factory: async_sessionmaker[AsyncSession],
) -> uuid.UUID:
    """Seed a member user + engagement in Postgres; return the engagement UUID."""
    async with pg_schema_factory() as session:
        member = await auth_repo.create_user(
            session,
            username=f"member_graph_{uuid.uuid4().hex[:8]}",
            password_hash=_MEMBER_HASH,
            role="user",
        )
        await session.flush()
        member_id = member.id

        engagement = await eng_repo.create_engagement(
            session,
            name="Graph Integration Test",
            scope="10.0.0.0/24",
            client_info=None,
            owner_id=member_id,  # type: ignore[arg-type]
        )
        await session.commit()
        return engagement.id  # type: ignore[return-value]


@pytest_asyncio.fixture(autouse=True)
async def reset_writer_registry() -> AsyncGenerator[None, None]:
    """Reset the global writer registry before and after each test.

    Mirrors writer.reset_state() / concurrency._reset() so registries do not
    leak across tests.
    """
    gw.reset_state()
    yield
    gw.reset_state()


# ---------------------------------------------------------------------------
# Helper: patch get_sessionmaker so the writer uses the test schema factory
# ---------------------------------------------------------------------------


def _patch_writer_factory(
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Point writer.get_sessionmaker at the test schema factory."""
    import app.features.graph.writer as _writer_mod

    monkeypatch.setattr(_writer_mod, "get_sessionmaker", lambda: factory)


# ---------------------------------------------------------------------------
# Integration test 1: concurrent writes serialize via writer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_writes_serialize_via_writer(
    pg_schema_factory: async_sessionmaker[AsyncSession],
    pg_engagement_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Many concurrent create/update operations against ONE engagement serialize.

    Proves the single-writer invariant end-to-end against real Postgres:
    - Fire N concurrent create-node calls (all land, no lost writes, no corruption).
    - Then fire N concurrent update-node calls (each updates a different node).
    - Assert final read_graph is internally consistent: all N live nodes present
      with their updated labels, no duplicates, no missing entries.
    """
    _patch_writer_factory(pg_schema_factory, monkeypatch)

    eng_id = pg_engagement_id
    n = 8

    # -- Phase 1: concurrent creates ------------------------------------------
    created_nodes = await asyncio.gather(
        *[
            gw.submit_create_node(
                eng_id,
                node_type="host",
                label=f"host-{i}",
                properties={"idx": i},
            )
            for i in range(n)
        ]
    )
    assert len(created_nodes) == n, "Not all create-node calls returned a result"

    # -- Phase 2: concurrent updates (each touches a different node) -----------
    updated_nodes = await asyncio.gather(
        *[
            gw.submit_update_node(
                eng_id,
                node.id,
                label=f"host-updated-{i}",
            )
            for i, node in enumerate(created_nodes)
        ]
    )
    assert len(updated_nodes) == n, "Not all update-node calls returned a result"

    # -- Assert consistency from in-memory graph (served by the writer) --------
    snapshot = await gw.read_graph(eng_id)
    assert len(snapshot.nodes) == n, (
        f"Expected {n} live nodes in snapshot; got {len(snapshot.nodes)}"
    )

    live_labels = {node.label for node in snapshot.nodes}
    expected_labels = {f"host-updated-{i}" for i in range(n)}
    assert live_labels == expected_labels, (
        f"Label mismatch: expected {expected_labels}, got {live_labels}"
    )

    # -- Assert consistency from Postgres (the ground truth) -------------------
    async with pg_schema_factory() as db:
        pg_nodes, _ = await repo.load_live_graph(db, eng_id)
    assert len(pg_nodes) == n, f"Expected {n} live nodes in Postgres; got {len(pg_nodes)}"
    pg_labels = {node.label for node in pg_nodes}
    assert pg_labels == expected_labels, (
        f"Postgres label mismatch: expected {expected_labels}, got {pg_labels}"
    )

    # -- Assert exactly one writer/consumer task per engagement ----------------
    assert len(gw._writers) == 1, (
        f"Expected exactly 1 writer registry entry; got {len(gw._writers)}"
    )
    writer = gw._writers[eng_id]
    assert not writer._task.done(), "Writer consumer task should still be running"


# ---------------------------------------------------------------------------
# Integration test 2: soft-delete then undo roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_soft_delete_then_undo_roundtrip(
    pg_schema_factory: async_sessionmaker[AsyncSession],
    pg_engagement_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create a node → soft-delete it → undo it; verify each state against Postgres.

    Proves:
    1. After create: node appears in live graph (read_graph) and Postgres.
    2. After soft-delete: node is GONE from live graph but PRESENT in history
       (read_full returns it with deleted=True).
    3. After undo: node REAPPEARS in live graph with deleted=False.
    """
    _patch_writer_factory(pg_schema_factory, monkeypatch)

    eng_id = pg_engagement_id

    # -- Step 1: create --------------------------------------------------------
    node = await gw.submit_create_node(
        eng_id,
        node_type="host",
        label="10.0.0.42",
        properties={"os": "linux"},
    )
    node_id = node.id

    snapshot_after_create = await gw.read_graph(eng_id)
    live_ids_after_create = {n.id for n in snapshot_after_create.nodes}
    assert node_id in live_ids_after_create, "Node should be live after create"

    # Confirm Postgres
    async with pg_schema_factory() as db:
        pg_nodes_create, _ = await repo.load_live_graph(db, eng_id)
    assert any(n.id == node_id for n in pg_nodes_create), "Node missing from Postgres after create"

    # -- Step 2: soft-delete ---------------------------------------------------
    await gw.submit_soft_delete_node(eng_id, node_id)

    # The in-memory live graph should no longer include the deleted node.
    snapshot_after_delete = await gw.read_graph(eng_id)
    live_ids_after_delete = {n.id for n in snapshot_after_delete.nodes}
    assert node_id not in live_ids_after_delete, "Node should be gone from live graph after delete"

    # Postgres live graph should also not include the deleted node.
    async with pg_schema_factory() as db:
        pg_nodes_delete, _ = await repo.load_live_graph(db, eng_id)
    assert not any(n.id == node_id for n in pg_nodes_delete), (
        "Soft-deleted node should not appear in Postgres live graph"
    )

    # Node should be present in Postgres full graph (includes deleted) with deleted=True.
    async with pg_schema_factory() as db:
        pg_all_nodes_delete, _ = await repo.load_full_graph(db, eng_id)
    deleted_pg_node = next((n for n in pg_all_nodes_delete if n.id == node_id), None)
    assert deleted_pg_node is not None, (
        "Node should still exist in Postgres full graph after delete"
    )
    assert deleted_pg_node.deleted is True, "Node should be marked deleted=True in Postgres"

    # -- Step 3: undo ----------------------------------------------------------
    restored = await gw.submit_undo_node(eng_id, node_id)
    assert restored.deleted is False, "Restored node should have deleted=False"
    assert restored.id == node_id

    snapshot_after_undo = await gw.read_graph(eng_id)
    live_ids_after_undo = {n.id for n in snapshot_after_undo.nodes}
    assert node_id in live_ids_after_undo, "Node should reappear in live graph after undo"

    # Confirm Postgres
    async with pg_schema_factory() as db:
        pg_nodes_undo, _ = await repo.load_live_graph(db, eng_id)
    assert any(n.id == node_id for n in pg_nodes_undo), (
        "Node should reappear in Postgres live graph after undo"
    )
