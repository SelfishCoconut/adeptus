"""The single-writer process (ADR-0001): one writer per engagement owning the
in-memory NetworkX graph and serializing every write through a single consumer
task. The critical invariant of this slice (task 4).

REGISTRY DESIGN (mirrors mcp/concurrency._get_state)
------------------------------------------------------
``_writers: dict[UUID, _Writer]`` is created lazily on first access per
engagement.  The registry entry is created SYNCHRONOUSLY — no ``await``
between the ``if engagement_id not in _writers`` check and the assignment —
so concurrent async callers cannot race into creating two consumer tasks for
the same engagement (Risk 1 / ADR-0001).

WARM-START AFTER RESTART (contrast with mcp_repo.reconcile_stale_tool_runs)
-----------------------------------------------------------------------------
The writer registry is entirely in-process.  After a process restart the
registry is empty and each engagement's ``_Writer`` is warm-started lazily on
its first read or write: the consumer calls ``load_full_graph`` (includes
soft-deleted rows for undo correctness) and rebuilds the in-memory NetworkX
graph from the persisted Postgres state.  There is NO phantom state to
reconcile on startup — unlike tool_runs, which may be stuck ``running`` across
a restart, the write queue is in-memory and simply disappears; pending HTTP
requests time out naturally.

TRANSACTION DISCIPLINE (Risk 2)
--------------------------------
Every command inside the consumer runs in exactly one DB transaction:
  1. Record pre-mutation history snapshot (before any change).
  2. Mutate Postgres (insert/update/soft-delete).
  3. commit().
  4. THEN mutate the in-memory NetworkX graph.

Step 4 happens ONLY after a successful commit so that a rolled-back or failed
transaction never leaves the in-memory graph ahead of Postgres.

PER-COMMAND RESULT DELIVERY (Risk 5)
--------------------------------------
Each public ``submit_*`` method enqueues a ``_Command`` dataclass that carries
an ``asyncio.Future``.  The single consumer resolves the Future with the result
or with the raised domain exception.  Callers ``await`` the Future, getting the
entity or error back, while all writes remain strictly serialized through the
one consumer task.

The consumer wraps each command in try/except/finally that ALWAYS resolves the
Future so a consumer crash can never leave a request Future pending forever.
On crash, all remaining in-flight Futures are resolved with an error.

IN-MEMORY DELETED REPRESENTATION
----------------------------------
All nodes and edges — including soft-deleted ones — are stored in the
``networkx.MultiDiGraph`` with a ``deleted`` node/edge attribute.  Live reads
filter by ``data['deleted'] == False``.  This mirrors the full Postgres state
so warm-start, undo, and cascade soft-delete are all consistent.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

import networkx as nx
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import get_sessionmaker
from app.features.graph import repository as repo
from app.features.graph.errors import DuplicateEdge, EdgeNotFound, NodeNotFound, NoHistory
from app.features.graph.models import GraphEdge, GraphEdgeHistory, GraphNode, GraphNodeHistory
from app.features.graph.schemas import Edge, GraphSnapshot, Node

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal command dataclasses
# ---------------------------------------------------------------------------


@dataclass
class _CreateNodeCmd:
    node_type: str
    label: str
    properties: dict[str, Any]
    future: asyncio.Future[Any]


@dataclass
class _UpdateNodeCmd:
    node_id: UUID
    label: str | None
    properties: dict[str, Any] | None
    future: asyncio.Future[Any]


@dataclass
class _SoftDeleteNodeCmd:
    node_id: UUID
    future: asyncio.Future[Any]


@dataclass
class _UndoNodeCmd:
    node_id: UUID
    future: asyncio.Future[Any]


@dataclass
class _CreateEdgeCmd:
    source_id: UUID
    target_id: UUID
    relation: str
    properties: dict[str, Any]
    future: asyncio.Future[Any]


@dataclass
class _SoftDeleteEdgeCmd:
    edge_id: UUID
    future: asyncio.Future[Any]


@dataclass
class _UndoEdgeCmd:
    edge_id: UUID
    future: asyncio.Future[Any]


_Command = (
    _CreateNodeCmd
    | _UpdateNodeCmd
    | _SoftDeleteNodeCmd
    | _UndoNodeCmd
    | _CreateEdgeCmd
    | _SoftDeleteEdgeCmd
    | _UndoEdgeCmd
)


# ---------------------------------------------------------------------------
# In-memory graph attribute key
# ---------------------------------------------------------------------------

_DELETED_ATTR = "deleted"
_NODE_DATA_ATTR = "node_data"  # stores the Node schema dict for each graph node
_EDGE_DATA_ATTR = "edge_data"  # stores the Edge schema dict for each graph edge


# ---------------------------------------------------------------------------
# _Writer — one per engagement
# ---------------------------------------------------------------------------


class _Writer:
    """Owns one engagement's in-memory NetworkX graph and a single consumer task.

    Do not instantiate directly — use ``_get_writer()`` which creates the
    registry entry synchronously to avoid a race.
    """

    def __init__(
        self,
        engagement_id: UUID,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._engagement_id = engagement_id
        self._session_factory = session_factory
        self._queue: asyncio.Queue[_Command] = asyncio.Queue()
        # MultiDiGraph stores all nodes/edges including deleted ones with a
        # ``deleted`` attribute so warm-start + undo stay consistent.
        self._graph: nx.MultiDiGraph = nx.MultiDiGraph()
        self._warm: bool = False  # True once the graph has been loaded from Postgres.
        self._task: asyncio.Task[None] = asyncio.get_event_loop().create_task(
            self._consume(), name=f"writer-{engagement_id}"
        )

    # ------------------------------------------------------------------
    # Warm-start
    # ------------------------------------------------------------------

    async def _ensure_warm(self, db: AsyncSession) -> None:
        """Rebuild in-memory graph from Postgres if not already warm."""
        if self._warm:
            return
        nodes, edges = await repo.load_full_graph(db, self._engagement_id)
        for node in nodes:
            node_schema = Node.model_validate(node)
            self._graph.add_node(
                node_schema.id,
                deleted=node_schema.deleted,
                node_data=node_schema,
            )
        for edge in edges:
            edge_schema = Edge.model_validate(edge)
            self._graph.add_edge(
                edge_schema.source_id,
                edge_schema.target_id,
                key=edge_schema.id,
                deleted=edge_schema.deleted,
                edge_data=edge_schema,
            )
        self._warm = True

    # ------------------------------------------------------------------
    # Undo history queries (ordered by recorded_at DESC then id DESC as tiebreaker)
    # ------------------------------------------------------------------

    @staticmethod
    async def _latest_node_history(db: AsyncSession, node_id: UUID) -> GraphNodeHistory | None:
        """Fetch the most recent history row for a node.

        Orders by (recorded_at DESC, id DESC) so that when two rows share the
        same timestamp (possible on low-resolution DBs such as SQLite in tests),
        the row that was inserted later (higher UUID v4 ordering by row position)
        is still returned as the «latest».  On Postgres the timestamp precision is
        sub-microsecond and ties are vanishingly rare, but the id tiebreaker makes
        the ordering deterministic across all backends.
        """
        result = await db.execute(
            select(GraphNodeHistory)
            .where(GraphNodeHistory.node_id == node_id)
            .order_by(desc(GraphNodeHistory.recorded_at), desc(GraphNodeHistory.id))
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def _latest_edge_history(db: AsyncSession, edge_id: UUID) -> GraphEdgeHistory | None:
        """Fetch the most recent history row for an edge.

        Same (recorded_at DESC, id DESC) ordering as _latest_node_history.
        """
        result = await db.execute(
            select(GraphEdgeHistory)
            .where(GraphEdgeHistory.edge_id == edge_id)
            .order_by(desc(GraphEdgeHistory.recorded_at), desc(GraphEdgeHistory.id))
            .limit(1)
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Consumer loop
    # ------------------------------------------------------------------

    async def _consume(self) -> None:
        """Single consumer task: drain the queue strictly serially."""
        # In-flight commands that have been dequeued but not yet resolved —
        # used to resolve all Futures if the consumer crashes.
        current_cmd: _Command | None = None
        try:
            while True:
                cmd = await self._queue.get()
                current_cmd = cmd
                try:
                    await self._dispatch(cmd)
                except Exception as exc:  # noqa: BLE001
                    # Propagate error to the caller's Future; never leave it pending.
                    self._resolve_future_with_error(cmd, exc)
                finally:
                    self._queue.task_done()
                    current_cmd = None
        except asyncio.CancelledError:
            # Consumer was cancelled (shutdown). Drain any already-dequeued command.
            if current_cmd is not None:
                self._resolve_future_with_error(
                    current_cmd,
                    RuntimeError("Writer consumer shut down before command completed"),
                )
            # Drain remaining queued commands.
            while not self._queue.empty():
                try:
                    remaining = self._queue.get_nowait()
                    self._resolve_future_with_error(
                        remaining,
                        RuntimeError("Writer consumer shut down; command was not executed"),
                    )
                    self._queue.task_done()
                except asyncio.QueueEmpty:
                    break
            raise
        except Exception:
            logger.exception(
                "Writer consumer for engagement %s crashed; draining pending commands",
                self._engagement_id,
            )
            # Drain remaining queued commands after a crash.
            while not self._queue.empty():
                try:
                    remaining = self._queue.get_nowait()
                    self._resolve_future_with_error(
                        remaining,
                        RuntimeError("Writer consumer crashed; command was not executed"),
                    )
                    self._queue.task_done()
                except asyncio.QueueEmpty:
                    break

    @staticmethod
    def _resolve_future_with_error(cmd: _Command, exc: Exception) -> None:
        """Resolve cmd.future with an exception if it is not already done."""
        if not cmd.future.done():
            cmd.future.set_exception(exc)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def _dispatch(self, cmd: _Command) -> None:
        """Route each command type to its handler; always resolves cmd.future."""
        async with self._session_factory() as db:
            try:
                if isinstance(cmd, _CreateNodeCmd):
                    node_result = await self._handle_create_node(db, cmd)
                    await db.commit()
                    self._apply_create_node(node_result)
                    cmd.future.set_result(node_result)

                elif isinstance(cmd, _UpdateNodeCmd):
                    node_result = await self._handle_update_node(db, cmd)
                    await db.commit()
                    self._apply_update_node(node_result)
                    cmd.future.set_result(node_result)

                elif isinstance(cmd, _SoftDeleteNodeCmd):
                    await self._handle_soft_delete_node(db, cmd)
                    await db.commit()
                    self._apply_soft_delete_node(cmd.node_id)
                    cmd.future.set_result(None)

                elif isinstance(cmd, _UndoNodeCmd):
                    node_result = await self._handle_undo_node(db, cmd)
                    await db.commit()
                    self._apply_update_node(node_result)
                    cmd.future.set_result(node_result)

                elif isinstance(cmd, _CreateEdgeCmd):
                    edge_result = await self._handle_create_edge(db, cmd)
                    await db.commit()
                    self._apply_create_edge(edge_result)
                    cmd.future.set_result(edge_result)

                elif isinstance(cmd, _SoftDeleteEdgeCmd):
                    await self._handle_soft_delete_edge(db, cmd)
                    await db.commit()
                    self._apply_soft_delete_edge(cmd.edge_id)
                    cmd.future.set_result(None)

                elif isinstance(cmd, _UndoEdgeCmd):
                    edge_result = await self._handle_undo_edge(db, cmd)
                    await db.commit()
                    self._apply_update_edge(edge_result)
                    cmd.future.set_result(edge_result)

            except Exception as exc:  # noqa: BLE001
                # Roll back the DB transaction; do NOT touch in-memory graph.
                await db.rollback()
                if not cmd.future.done():
                    cmd.future.set_exception(exc)

    # ------------------------------------------------------------------
    # DB-level handlers (run inside transaction; no in-memory mutation here)
    # ------------------------------------------------------------------

    async def _handle_create_node(
        self,
        db: AsyncSession,
        cmd: _CreateNodeCmd,
    ) -> Node:
        await self._ensure_warm(db)
        node_row = await repo.insert_node(
            db,
            engagement_id=self._engagement_id,
            node_type=cmd.node_type,
            label=cmd.label,
            properties=cmd.properties,
        )
        return Node.model_validate(node_row)

    async def _handle_update_node(
        self,
        db: AsyncSession,
        cmd: _UpdateNodeCmd,
    ) -> Node:
        await self._ensure_warm(db)
        node_row: GraphNode | None = await repo.get_node(db, cmd.node_id)
        if node_row is None or node_row.engagement_id != self._engagement_id or node_row.deleted:
            raise NodeNotFound(f"Node {cmd.node_id} not found or deleted")

        # Merge: use existing values when not provided.
        new_label = cmd.label if cmd.label is not None else node_row.label
        new_props = (
            cmd.properties
            if cmd.properties is not None
            else cast(dict[str, Any], node_row.properties)
        )

        await repo.record_node_history(db, node=node_row)
        updated = await repo.update_node_row(
            db, node=node_row, label=new_label, properties=new_props
        )
        return Node.model_validate(updated)

    async def _handle_soft_delete_node(
        self,
        db: AsyncSession,
        cmd: _SoftDeleteNodeCmd,
    ) -> None:
        await self._ensure_warm(db)
        node_row: GraphNode | None = await repo.get_node(db, cmd.node_id)
        if node_row is None or node_row.engagement_id != self._engagement_id or node_row.deleted:
            raise NodeNotFound(f"Node {cmd.node_id} not found or deleted")

        # Gather live incident edges before cascade to record their history.
        from sqlalchemy import and_, or_, select

        from app.features.graph.models import GraphEdge as GE

        incident_result = await db.execute(
            select(GE).where(
                and_(
                    or_(GE.source_id == cmd.node_id, GE.target_id == cmd.node_id),
                    GE.deleted.is_(False),
                )
            )
        )
        incident_edges = list(incident_result.scalars().all())

        # Record history for the node (pre-delete state).
        await repo.record_node_history(db, node=node_row)
        # Record history for each live incident edge before cascade soft-delete.
        for edge_row in incident_edges:
            await repo.record_edge_history(db, edge=edge_row)

        await repo.soft_delete_node(db, node=node_row)

    async def _handle_undo_node(
        self,
        db: AsyncSession,
        cmd: _UndoNodeCmd,
    ) -> Node:
        await self._ensure_warm(db)
        node_row: GraphNode | None = await repo.get_node(db, cmd.node_id)
        if node_row is None or node_row.engagement_id != self._engagement_id:
            raise NodeNotFound(f"Node {cmd.node_id} not found")

        # Fetch the prior state BEFORE recording the current state (order matters:
        # record THEN use the previously-fetched value so the new history row does
        # not shadow the one we intend to restore).
        history = await self._latest_node_history(db, cmd.node_id)
        if history is None:
            raise NoHistory(f"No prior state for node {cmd.node_id}")

        # Record the current state as history AFTER fetching, so future undos can
        # walk back further.  Using _latest_node_history (with id tiebreaker) makes
        # this repeatable even when recorded_at timestamps share the same second.
        await repo.record_node_history(db, node=node_row)

        # Restore the prior state.
        node_row.label = history.label
        node_row.properties = cast(dict[str, Any], history.properties)
        node_row.deleted = history.deleted
        await db.flush()
        await db.refresh(node_row)
        return Node.model_validate(node_row)

    async def _handle_create_edge(
        self,
        db: AsyncSession,
        cmd: _CreateEdgeCmd,
    ) -> Edge:
        await self._ensure_warm(db)

        # Race-free duplicate-triple check (inside single consumer so no race).
        existing = await repo.find_live_edge(
            db,
            engagement_id=self._engagement_id,
            source_id=cmd.source_id,
            target_id=cmd.target_id,
            relation=cmd.relation,
        )
        if existing is not None:
            raise DuplicateEdge(
                f"Live edge ({cmd.source_id}, {cmd.target_id}, {cmd.relation!r}) already exists"
            )

        try:
            edge_row = await repo.insert_edge(
                db,
                engagement_id=self._engagement_id,
                source_id=cmd.source_id,
                target_id=cmd.target_id,
                relation=cmd.relation,
                properties=cmd.properties,
            )
        except IntegrityError as exc:
            # DB-level partial unique index backstop.
            raise DuplicateEdge(
                f"Live edge ({cmd.source_id}, {cmd.target_id}, {cmd.relation!r}) already exists "
                "(IntegrityError — partial unique index)"
            ) from exc
        return Edge.model_validate(edge_row)

    async def _handle_soft_delete_edge(
        self,
        db: AsyncSession,
        cmd: _SoftDeleteEdgeCmd,
    ) -> None:
        await self._ensure_warm(db)
        edge_row: GraphEdge | None = await repo.get_edge(db, cmd.edge_id)
        if edge_row is None or edge_row.engagement_id != self._engagement_id or edge_row.deleted:
            raise EdgeNotFound(f"Edge {cmd.edge_id} not found or deleted")

        await repo.record_edge_history(db, edge=edge_row)
        await repo.soft_delete_edge(db, edge=edge_row)

    async def _handle_undo_edge(
        self,
        db: AsyncSession,
        cmd: _UndoEdgeCmd,
    ) -> Edge:
        await self._ensure_warm(db)
        edge_row: GraphEdge | None = await repo.get_edge(db, cmd.edge_id)
        if edge_row is None or edge_row.engagement_id != self._engagement_id:
            raise EdgeNotFound(f"Edge {cmd.edge_id} not found")

        # Fetch prior state BEFORE recording current (same order as _handle_undo_node).
        history = await self._latest_edge_history(db, cmd.edge_id)
        if history is None:
            raise NoHistory(f"No prior state for edge {cmd.edge_id}")

        # Record current state after fetching (undo is repeatable, walks back).
        await repo.record_edge_history(db, edge=edge_row)

        # Restore the prior state.
        edge_row.relation = history.relation
        edge_row.properties = cast(dict[str, Any], history.properties)
        edge_row.deleted = history.deleted
        await db.flush()
        await db.refresh(edge_row)
        return Edge.model_validate(edge_row)

    # ------------------------------------------------------------------
    # In-memory graph mutations (called ONLY after a successful DB commit)
    # ------------------------------------------------------------------

    def _apply_create_node(self, node: Node) -> None:
        self._graph.add_node(node.id, deleted=False, node_data=node)

    def _apply_update_node(self, node: Node) -> None:
        if self._graph.has_node(node.id):
            self._graph.nodes[node.id][_DELETED_ATTR] = node.deleted
            self._graph.nodes[node.id][_NODE_DATA_ATTR] = node
        else:
            self._graph.add_node(node.id, deleted=node.deleted, node_data=node)

    def _apply_soft_delete_node(self, node_id: UUID) -> None:
        if self._graph.has_node(node_id):
            self._graph.nodes[node_id][_DELETED_ATTR] = True
            # Also mark all incident edges as deleted in memory.
            for u, v, key in list(self._graph.edges(node_id, keys=True)):
                self._graph.edges[u, v, key][_DELETED_ATTR] = True
            for u, v, key in list(self._graph.in_edges(node_id, keys=True)):
                self._graph.edges[u, v, key][_DELETED_ATTR] = True

    def _apply_create_edge(self, edge: Edge) -> None:
        self._graph.add_edge(
            edge.source_id,
            edge.target_id,
            key=edge.id,
            deleted=False,
            edge_data=edge,
        )

    def _apply_soft_delete_edge(self, edge_id: UUID) -> None:
        for u, v, key in self._graph.edges(keys=True):
            if key == edge_id:
                self._graph.edges[u, v, key][_DELETED_ATTR] = True
                break

    def _apply_update_edge(self, edge: Edge) -> None:
        for u, v, key in list(self._graph.edges(keys=True)):
            if key == edge.id:
                self._graph.edges[u, v, key][_DELETED_ATTR] = edge.deleted
                self._graph.edges[u, v, key][_EDGE_DATA_ATTR] = edge
                return
        # Edge not in graph yet (e.g. after undo restores a deleted edge) — add it.
        self._graph.add_edge(
            edge.source_id,
            edge.target_id,
            key=edge.id,
            deleted=edge.deleted,
            edge_data=edge,
        )

    # ------------------------------------------------------------------
    # Read helpers (serve from in-memory graph)
    # ------------------------------------------------------------------

    async def _ensure_warm_read(self) -> None:
        """Ensure warm for reads (using a fresh session, not request-scoped)."""
        if self._warm:
            return
        async with self._session_factory() as db:
            await self._ensure_warm(db)

    def _live_nodes(self) -> list[Node]:
        return [
            cast(Node, data[_NODE_DATA_ATTR])
            for _, data in self._graph.nodes(data=True)
            if not data.get(_DELETED_ATTR, False) and _NODE_DATA_ATTR in data
        ]

    def _live_edges(self) -> list[Edge]:
        return [
            cast(Edge, data[_EDGE_DATA_ATTR])
            for _, _, data in self._graph.edges(data=True)
            if not data.get(_DELETED_ATTR, False) and _EDGE_DATA_ATTR in data
        ]

    def _all_nodes(self) -> list[Node]:
        return [
            cast(Node, data[_NODE_DATA_ATTR])
            for _, data in self._graph.nodes(data=True)
            if _NODE_DATA_ATTR in data
        ]

    def _all_edges(self) -> list[Edge]:
        return [
            cast(Edge, data[_EDGE_DATA_ATTR])
            for _, _, data in self._graph.edges(data=True)
            if _EDGE_DATA_ATTR in data
        ]


# ---------------------------------------------------------------------------
# Module-level registry
# ---------------------------------------------------------------------------

_writers: dict[UUID, _Writer] = {}


def _get_writer(engagement_id: UUID) -> _Writer:
    """Return the existing writer or create one (SYNCHRONOUS — no await between
    check and assignment; this is the critical section that prevents spawning
    two consumer tasks under a race, Risk 1 / ADR-0001).

    This mirrors ``mcp.concurrency._get_state`` exactly.
    """
    # CRITICAL: no ``await`` between this check and the assignment below.
    if engagement_id not in _writers:
        _writers[engagement_id] = _Writer(engagement_id, get_sessionmaker())
    return _writers[engagement_id]


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------


async def submit_create_node(
    engagement_id: UUID,
    *,
    node_type: str,
    label: str,
    properties: dict[str, Any],
) -> Node:
    """Enqueue a create-node command and await the result."""
    writer = _get_writer(engagement_id)
    loop = asyncio.get_event_loop()
    future: asyncio.Future[Node] = loop.create_future()
    await writer._queue.put(
        _CreateNodeCmd(node_type=node_type, label=label, properties=properties, future=future)
    )
    return await future


async def submit_update_node(
    engagement_id: UUID,
    node_id: UUID,
    *,
    label: str | None = None,
    properties: dict[str, Any] | None = None,
) -> Node:
    """Enqueue an update-node command and await the result.

    ``label`` and ``properties`` are both optional; passing ``None`` keeps the
    existing value.  When ``properties`` is provided it fully replaces the prior blob.
    """
    writer = _get_writer(engagement_id)
    loop = asyncio.get_event_loop()
    future: asyncio.Future[Node] = loop.create_future()
    await writer._queue.put(
        _UpdateNodeCmd(node_id=node_id, label=label, properties=properties, future=future)
    )
    return await future


async def submit_soft_delete_node(
    engagement_id: UUID,
    node_id: UUID,
) -> None:
    """Enqueue a soft-delete-node command (cascades to incident edges) and await completion."""
    writer = _get_writer(engagement_id)
    loop = asyncio.get_event_loop()
    future: asyncio.Future[None] = loop.create_future()
    await writer._queue.put(_SoftDeleteNodeCmd(node_id=node_id, future=future))
    await future


async def submit_undo_node(
    engagement_id: UUID,
    node_id: UUID,
) -> Node:
    """Enqueue an undo-node command and await the restored node."""
    writer = _get_writer(engagement_id)
    loop = asyncio.get_event_loop()
    future: asyncio.Future[Node] = loop.create_future()
    await writer._queue.put(_UndoNodeCmd(node_id=node_id, future=future))
    return await future


async def submit_create_edge(
    engagement_id: UUID,
    *,
    source_id: UUID,
    target_id: UUID,
    relation: str,
    properties: dict[str, Any],
) -> Edge:
    """Enqueue a create-edge command and await the result.

    The duplicate-triple check (source_id, target_id, relation) is performed
    INSIDE the consumer so it is race-free under the single-writer guarantee.
    Raises ``DuplicateEdge`` (→ HTTP 409) if a live edge with the same triple exists.
    """
    writer = _get_writer(engagement_id)
    loop = asyncio.get_event_loop()
    future: asyncio.Future[Edge] = loop.create_future()
    await writer._queue.put(
        _CreateEdgeCmd(
            source_id=source_id,
            target_id=target_id,
            relation=relation,
            properties=properties,
            future=future,
        )
    )
    return await future


async def submit_soft_delete_edge(
    engagement_id: UUID,
    edge_id: UUID,
) -> None:
    """Enqueue a soft-delete-edge command and await completion."""
    writer = _get_writer(engagement_id)
    loop = asyncio.get_event_loop()
    future: asyncio.Future[None] = loop.create_future()
    await writer._queue.put(_SoftDeleteEdgeCmd(edge_id=edge_id, future=future))
    await future


async def submit_undo_edge(
    engagement_id: UUID,
    edge_id: UUID,
) -> Edge:
    """Enqueue an undo-edge command and await the restored edge."""
    writer = _get_writer(engagement_id)
    loop = asyncio.get_event_loop()
    future: asyncio.Future[Edge] = loop.create_future()
    await writer._queue.put(_UndoEdgeCmd(edge_id=edge_id, future=future))
    return await future


async def read_graph(engagement_id: UUID) -> GraphSnapshot:
    """Return the live (non-deleted) graph snapshot from the in-memory writer.

    Warm-starts the writer if this is the first read for this engagement.
    Does NOT go through the write queue.
    """
    writer = _get_writer(engagement_id)
    await writer._ensure_warm_read()
    return GraphSnapshot(nodes=writer._live_nodes(), edges=writer._live_edges())


async def read_full(engagement_id: UUID) -> GraphSnapshot:
    """Return ALL nodes and edges (including deleted) from the in-memory writer.

    Warm-starts the writer if needed.  Does NOT go through the write queue.
    """
    writer = _get_writer(engagement_id)
    await writer._ensure_warm_read()
    return GraphSnapshot(nodes=writer._all_nodes(), edges=writer._all_edges())


def shutdown() -> None:
    """Cancel all consumer tasks and clear the registry.

    Called from the application lifespan shutdown block alongside MCP shutdown.
    """
    for writer in _writers.values():
        writer._task.cancel()
    _writers.clear()


def reset_state() -> None:
    """Test hook: cancel all consumer tasks and clear the registry between tests.

    Mirrors ``mcp.concurrency._reset()`` / ``_states.clear()``.
    """
    for writer in _writers.values():
        writer._task.cancel()
    _writers.clear()
