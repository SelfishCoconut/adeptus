# Slice 07: Graph data model + single-writer process

**Branch**: `slice-07-graph-data-model-single-writer`
**GitHub Issue**: #21
**Status**: planned
**Risky**: yes

---

## Goal

Let an engagement member manually create, edit, soft-delete, and per-entity-undo graph nodes and edges, with every write serialized through exactly one in-process single-writer per engagement that owns the engagement's in-memory NetworkX graph.

## User-visible demo

After this slice (the graph is visualized in Slice 08; this slice is exercised via the API and a minimal node-list panel):

- Log in as an engagement member and open an engagement.
- `POST /api/v1/engagements/{id}/graph/nodes` with `{ "type": "host", "label": "10.0.0.5", "properties": {"os": "linux"} }` returns `201` with the created node and its `id`.
- `POST .../graph/edges` linking two existing nodes (e.g. a `host` to a `service`) returns `201`.
- `PATCH .../graph/nodes/{node_id}` updates the label/properties; the change is reflected on subsequent reads.
- `GET .../graph` returns the full live graph (nodes + edges) for the engagement, served from the in-memory writer state.
- `DELETE .../graph/nodes/{node_id}` soft-deletes the node (and cascades soft-delete to incident edges); it disappears from the default `GET .../graph` but is still present in `GET .../graph/history`.
- `POST .../graph/nodes/{node_id}/undo` reverts the node to its previous soft-delete-history state (e.g. un-deletes it, or restores the pre-edit label), and the node reappears.
- A minimal "Graph" tab in the workspace lists current nodes/edges with create/edit/delete/undo controls (no force-directed visualization yet — that is Slice 08).
- Two concurrent edit requests to the same engagement never corrupt state: both are applied in a well-defined serialized order through the single writer; `GET .../graph` reflects both.
- Writes against an `archived` engagement are rejected (`409`); writes against an engagement you are not a member of return `404`.

## Out of scope

- Does NOT render the force-directed Cytoscape visualization, dragging, or pinning (Slice 08, §8.3).
- Does NOT implement the personal 20-deep per-user undo stack (Slice 09, §8.2) — this slice delivers only **per-entity undo** from soft-delete history.
- Does NOT implement AI writes, AI low-confidence flagging, or semantic merge mediation (§8.2 AI writes / conflict resolution — later AI slices). The writer queue and write API are AI-agnostic so the AI path plugs in later without changing the writer contract.
- Does NOT implement tool-result ingestion into the graph (later tool/AI slices). The writer queue is the future ingestion point.
- Does NOT implement attack-paths as a first-class typed entity beyond a generic node/edge type (Slice 22, §9.3) — `attack_path` may exist as a node `type` value but no path-specific endpoints.
- Does NOT implement findings/vulnerability lifecycle (Slice 19, §9). A `vulnerability`/`finding` node `type` may exist as a label only; the findings feature is separate.
- Does NOT implement the audit log or hash-chain (Slice 10, §14). Graph edits will be audited there; this slice does not write audit entries.
- Does NOT implement WebSocket live-push of graph changes to other connected members (Slice 08/31). Reads are pull-based via `GET .../graph` for this slice.
- Does NOT implement node annotations/notes UI or screenshots (§8.3 / §11.4, later slices) beyond the free-form `properties` JSON blob and a `note` node type.
- Does NOT implement provenance fields on nodes/edges (§8.2 — "no provenance attribution"; the audit log is the source of truth, added in Slice 10).

## Requirements traceability

- **§8.1** — Entity (node) types: the node `type` enum covers `host`, `port`, `service`, `url`, `endpoint`, `vulnerability`, `credential`, `note`, `attack_path` (the entity list in §8.1: hosts, open ports, services/versions, discovered URLs/endpoints, vulnerabilities, credentials/hashes, notes/manual findings, attack paths/chains).
- **§8.2 — Storage**: NetworkX in-memory for traversal, persisted to PostgreSQL. The single-writer owns the in-memory `networkx.MultiDiGraph` per engagement and mirrors every mutation to Postgres in the same transaction.
- **§8.2 — Single-writer process per engagement**: exactly one writer per active engagement owns the in-memory graph; all writes (this slice: user writes; future: AI + tool ingestion) serialize through an internal queue. Reads can be served from Postgres or from the in-memory graph. This is the **critical invariant** of this slice and is implemented per **ADR-0001**.
- **§8.2 — History**: soft delete with history; nodes/edges can be archived and recovered. No full versioning / time-travel.
- **§8.2 — Manual undo (per-entity layer only)**: any node, edge, or finding can be reverted to a prior state from its soft-delete history. (The personal undo stack is explicitly Slice 09.)
- **§8.2 — Imports: none**: each engagement starts with a clean (empty) graph. No retest contamination here (§4 retest exception is Slice 25).
- **§8.2 — No provenance attribution**: nodes/edges carry no `created_by`/`updated_by` columns. (Audit log, Slice 10, is the source of truth.)
- **§4** — Archived engagements are read-only: graph writes against an `archived` engagement are rejected. (Read endpoints remain available.)
- **§17.1 — Engagement isolation**: every graph endpoint resolves the engagement via the membership chokepoint (`engagements.repository.get_engagement_for_member`); non-members get `404` (existence not revealed), matching Slice 01 posture.
- **ADR-0001** — Single-writer per engagement: the architectural mandate this slice realizes.

## Contract

OpenAPI delta. All endpoints require the `cookieAuth` session and engagement membership; non-members receive `404`.

```yaml
openapi: "3.1.0"
info:
  title: Adeptus API — Slice 07 delta
  version: "0.8.0"

paths:
  /api/v1/engagements/{engagement_id}/graph:
    get:
      operationId: get_graph
      summary: Get the engagement's full live graph (non-deleted nodes + edges), served from the in-memory writer
      security: [{ cookieAuth: [] }]
      parameters:
        - { name: engagement_id, in: path, required: true, schema: { type: string, format: uuid } }
      responses:
        "200":
          content:
            application/json:
              schema: { $ref: "#/components/schemas/GraphSnapshot" }
        "401": { description: Not authenticated }
        "404": { description: Engagement not found or caller not a member }

  /api/v1/engagements/{engagement_id}/graph/history:
    get:
      operationId: get_graph_history
      summary: List soft-deleted nodes and the version history of nodes/edges (for undo discovery)
      security: [{ cookieAuth: [] }]
      parameters:
        - { name: engagement_id, in: path, required: true, schema: { type: string, format: uuid } }
        - { name: include_deleted, in: query, required: false, schema: { type: boolean, default: true } }
      responses:
        "200":
          content:
            application/json:
              schema: { $ref: "#/components/schemas/GraphHistory" }
        "401": { description: Not authenticated }
        "404": { description: Engagement not found or caller not a member }

  /api/v1/engagements/{engagement_id}/graph/nodes:
    post:
      operationId: create_node
      summary: Create a node (write — serialized through the single writer)
      security: [{ cookieAuth: [] }]
      parameters:
        - { name: engagement_id, in: path, required: true, schema: { type: string, format: uuid } }
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: "#/components/schemas/NodeCreate" }
      responses:
        "201":
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Node" }
        "401": { description: Not authenticated }
        "404": { description: Engagement not found or caller not a member }
        "409": { description: Engagement is archived (read-only) }
        "422": { description: Validation error (bad type, empty label, oversized properties) }

  /api/v1/engagements/{engagement_id}/graph/nodes/{node_id}:
    patch:
      operationId: update_node
      summary: Update a node's label and/or properties (write — serialized; records a history entry)
      security: [{ cookieAuth: [] }]
      parameters:
        - { name: engagement_id, in: path, required: true, schema: { type: string, format: uuid } }
        - { name: node_id, in: path, required: true, schema: { type: string, format: uuid } }
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: "#/components/schemas/NodeUpdate" }
      responses:
        "200":
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Node" }
        "401": { description: Not authenticated }
        "404": { description: Engagement/node not found or caller not a member }
        "409": { description: Engagement is archived (read-only) }
        "422": { description: Validation error }
    delete:
      operationId: delete_node
      summary: Soft-delete a node and cascade soft-delete to its incident edges (write — serialized)
      security: [{ cookieAuth: [] }]
      parameters:
        - { name: engagement_id, in: path, required: true, schema: { type: string, format: uuid } }
        - { name: node_id, in: path, required: true, schema: { type: string, format: uuid } }
      responses:
        "204": { description: Node soft-deleted }
        "401": { description: Not authenticated }
        "404": { description: Engagement/node not found or caller not a member }
        "409": { description: Engagement is archived (read-only) }

  /api/v1/engagements/{engagement_id}/graph/nodes/{node_id}/undo:
    post:
      operationId: undo_node
      summary: Revert a node to its immediately-prior state from soft-delete/edit history (write — serialized)
      security: [{ cookieAuth: [] }]
      parameters:
        - { name: engagement_id, in: path, required: true, schema: { type: string, format: uuid } }
        - { name: node_id, in: path, required: true, schema: { type: string, format: uuid } }
      responses:
        "200":
          description: Node reverted; returns the now-current node state
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Node" }
        "401": { description: Not authenticated }
        "404": { description: Engagement/node not found, or no prior state to revert to, or caller not a member }
        "409": { description: Engagement is archived (read-only) }

  /api/v1/engagements/{engagement_id}/graph/edges:
    post:
      operationId: create_edge
      summary: Create a directed edge between two existing non-deleted nodes (write — serialized)
      security: [{ cookieAuth: [] }]
      parameters:
        - { name: engagement_id, in: path, required: true, schema: { type: string, format: uuid } }
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: "#/components/schemas/EdgeCreate" }
      responses:
        "201":
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Edge" }
        "401": { description: Not authenticated }
        "404": { description: Engagement not found, caller not a member, or source/target node not found }
        "409": { description: Engagement is archived (read-only), OR a live edge with the same (source_id, target_id, relation) already exists }
        "422": { description: Validation error }

  /api/v1/engagements/{engagement_id}/graph/edges/{edge_id}:
    delete:
      operationId: delete_edge
      summary: Soft-delete an edge (write — serialized)
      security: [{ cookieAuth: [] }]
      parameters:
        - { name: engagement_id, in: path, required: true, schema: { type: string, format: uuid } }
        - { name: edge_id, in: path, required: true, schema: { type: string, format: uuid } }
      responses:
        "204": { description: Edge soft-deleted }
        "401": { description: Not authenticated }
        "404": { description: Engagement/edge not found or caller not a member }
        "409": { description: Engagement is archived (read-only) }

  /api/v1/engagements/{engagement_id}/graph/edges/{edge_id}/undo:
    post:
      operationId: undo_edge
      summary: Revert an edge to its prior state from history (write — serialized)
      security: [{ cookieAuth: [] }]
      parameters:
        - { name: engagement_id, in: path, required: true, schema: { type: string, format: uuid } }
        - { name: edge_id, in: path, required: true, schema: { type: string, format: uuid } }
      responses:
        "200":
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Edge" }
        "401": { description: Not authenticated }
        "404": { description: Engagement/edge not found, no prior state, or caller not a member }
        "409": { description: Engagement is archived (read-only) }

components:
  schemas:
    NodeType:
      type: string
      enum: [host, port, service, url, endpoint, vulnerability, credential, note, attack_path]

    Node:
      type: object
      required: [id, engagement_id, type, label, properties, deleted, created_at, updated_at]
      properties:
        id: { type: string, format: uuid }
        engagement_id: { type: string, format: uuid }
        type: { $ref: "#/components/schemas/NodeType" }
        label: { type: string }
        properties:
          type: object
          additionalProperties: true
          description: "Free-form JSON blob (e.g. os, version, port number). Capped server-side."
        deleted: { type: boolean }
        created_at: { type: string, format: date-time }
        updated_at: { type: string, format: date-time }

    NodeCreate:
      type: object
      required: [type, label]
      properties:
        type: { $ref: "#/components/schemas/NodeType" }
        label: { type: string, minLength: 1, maxLength: 512 }
        properties:
          type: object
          additionalProperties: true

    NodeUpdate:
      type: object
      description: "At least one of label/properties must be present. properties fully replaces the prior blob."
      properties:
        label: { type: string, minLength: 1, maxLength: 512 }
        properties:
          type: object
          additionalProperties: true

    Edge:
      type: object
      required: [id, engagement_id, source_id, target_id, relation, properties, deleted, created_at, updated_at]
      properties:
        id: { type: string, format: uuid }
        engagement_id: { type: string, format: uuid }
        source_id: { type: string, format: uuid }
        target_id: { type: string, format: uuid }
        relation: { type: string, description: "Free-form relation label, e.g. 'runs', 'hosts', 'exploits'." }
        properties:
          type: object
          additionalProperties: true
        deleted: { type: boolean }
        created_at: { type: string, format: date-time }
        updated_at: { type: string, format: date-time }

    EdgeCreate:
      type: object
      required: [source_id, target_id, relation]
      properties:
        source_id: { type: string, format: uuid }
        target_id: { type: string, format: uuid }
        relation: { type: string, minLength: 1, maxLength: 128 }
        properties:
          type: object
          additionalProperties: true

    GraphSnapshot:
      type: object
      required: [nodes, edges]
      properties:
        nodes: { type: array, items: { $ref: "#/components/schemas/Node" } }
        edges: { type: array, items: { $ref: "#/components/schemas/Edge" } }

    NodeHistoryEntry:
      type: object
      required: [id, entity_id, label, properties, deleted, recorded_at]
      properties:
        id: { type: string, format: uuid }
        entity_id: { type: string, format: uuid }
        label: { type: string }
        properties: { type: object, additionalProperties: true }
        deleted: { type: boolean }
        recorded_at: { type: string, format: date-time }

    GraphHistory:
      type: object
      required: [deleted_nodes, node_history]
      properties:
        deleted_nodes: { type: array, items: { $ref: "#/components/schemas/Node" } }
        node_history:
          type: array
          items: { $ref: "#/components/schemas/NodeHistoryEntry" }
```

## Data model changes

Four new tables. No existing tables modified. Alembic migration is written via the `write-alembic-migration` skill during implementation (see ADR-stored gotcha: add the new feature models import to `alembic/env.py` first; recreate the autogenerated file as the non-root user).

- `graph_nodes`:
  - `id` UUID PK (`gen_random_uuid()`)
  - `engagement_id` UUID NOT NULL REFERENCES `engagements(id)` ON DELETE CASCADE
  - `type` VARCHAR(32) NOT NULL — CHECK IN (the §8.1 enum values)
  - `label` VARCHAR(512) NOT NULL
  - `properties` JSONB NOT NULL DEFAULT `'{}'::jsonb`
  - `deleted` BOOLEAN NOT NULL DEFAULT `false` (soft-delete flag)
  - `created_at` TIMESTAMPTZ NOT NULL DEFAULT `now()`
  - `updated_at` TIMESTAMPTZ NOT NULL DEFAULT `now()`
  - Index: `ix_graph_nodes_engagement_id` on `engagement_id`
  - Partial index: `ix_graph_nodes_engagement_live` on `(engagement_id)` WHERE `deleted = false` (fast live-graph load)
  - NO `created_by`/`updated_by` columns (§8.2 no-provenance).

- `graph_edges`:
  - `id` UUID PK
  - `engagement_id` UUID NOT NULL REFERENCES `engagements(id)` ON DELETE CASCADE
  - `source_id` UUID NOT NULL REFERENCES `graph_nodes(id)` ON DELETE CASCADE
  - `target_id` UUID NOT NULL REFERENCES `graph_nodes(id)` ON DELETE CASCADE
  - `relation` VARCHAR(128) NOT NULL
  - `properties` JSONB NOT NULL DEFAULT `'{}'::jsonb`
  - `deleted` BOOLEAN NOT NULL DEFAULT `false`
  - `created_at` / `updated_at` TIMESTAMPTZ NOT NULL DEFAULT `now()`
  - Index: `ix_graph_edges_engagement_id` on `engagement_id`
  - Index: `ix_graph_edges_source_id`, `ix_graph_edges_target_id` (incident-edge lookups for cascade soft-delete)
  - **Partial unique index** `uq_graph_edges_live_triple` on `(engagement_id, source_id, target_id, relation)` WHERE `deleted = false` — enforces that no two *live* edges duplicate the same fact (resolved open question 1). A soft-deleted edge does not block re-creating the same triple; re-creating an identical live triple is rejected with `409`. Distinct relations between the same pair remain allowed (MultiDiGraph semantics preserved).

- `graph_node_history`: append-only snapshot rows recording prior states for per-entity undo (§8.2). One row is written **before** each mutation captures the pre-mutation state (so undo restores the immediately-prior state).
  - `id` UUID PK
  - `engagement_id` UUID NOT NULL REFERENCES `engagements(id)` ON DELETE CASCADE
  - `node_id` UUID NOT NULL REFERENCES `graph_nodes(id)` ON DELETE CASCADE
  - `label` VARCHAR(512) NOT NULL
  - `properties` JSONB NOT NULL
  - `deleted` BOOLEAN NOT NULL
  - `recorded_at` TIMESTAMPTZ NOT NULL DEFAULT `now()`
  - Index: `ix_graph_node_history_node_id` on `(node_id, recorded_at DESC)` (latest-prior lookup)

- `graph_edge_history`: same shape as `graph_node_history` for edges.
  - `id`, `engagement_id`, `edge_id` (FK to `graph_edges(id)` ON DELETE CASCADE), `relation` VARCHAR(128), `properties` JSONB, `deleted` BOOLEAN, `recorded_at`
  - Index: `ix_graph_edge_history_edge_id` on `(edge_id, recorded_at DESC)`

History tables hold no provenance (no `actor_id`); the audit log (Slice 10) is the source of truth for who.

## Tasks

Numbered continuously across the whole slice. Every commit subject cites its task id, e.g. `feat(slice-07): add graph writer registry (task 4)`.

### Backend tasks

1. **[S]** Add `backend/app/features/graph/models.py` — SQLAlchemy ORM models `GraphNode`, `GraphEdge`, `GraphNodeHistory`, `GraphEdgeHistory` on the shared `Base` from `app.core.db`. Add the models import to `backend/alembic/env.py` (per the Alembic-autogenerate gotcha) so autogenerate sees them.

2. **[S]** Add `backend/app/features/graph/schemas.py` — Pydantic v2 models `NodeType` (StrEnum), `NodeCreate`, `NodeUpdate`, `Node`, `EdgeCreate`, `Edge`, `GraphSnapshot`, `NodeHistoryEntry`, `GraphHistory` matching the contract. `from_attributes=True`. Cap `properties` serialized size (e.g. reject blobs > 64 KB) via a validator.

3. **[M]** Add `backend/app/features/graph/repository.py` — async Postgres CRUD: `insert_node`, `update_node_row`, `soft_delete_node` (+ cascade-soft-delete incident edges), `insert_edge`, `soft_delete_edge`, `load_live_graph(engagement_id)` (non-deleted nodes + edges), `load_full_graph(engagement_id)` (for writer warm-start, includes deleted), `record_node_history` / `record_edge_history`, `latest_node_history` / `latest_edge_history`, `get_node` / `get_edge`, `find_live_edge(engagement_id, source_id, target_id, relation)` (duplicate-triple lookup for the uniqueness guard). Tests in `tests/test_repository.py` against the real async test session, including `test_partial_unique_index_blocks_duplicate_live_edge` and `test_duplicate_triple_allowed_after_soft_delete`.

4. **[L]** Add `backend/app/features/graph/writer.py` — **the single-writer process** (ADR-0001). This is the critical-invariant file. Implements a lazy per-engagement registry mirroring `mcp/concurrency.py`'s `_states: dict[UUID, ...]` + `_get_state` pattern:
   - `_writers: dict[UUID, _Writer]` — one writer per engagement, created lazily on first write.
   - Each `_Writer` owns: an `asyncio.Queue` of write commands, a single long-lived consumer `asyncio.Task` that drains the queue **strictly serially**, and the in-memory `networkx.MultiDiGraph` for that engagement.
   - On first creation a writer **warm-starts** its NetworkX graph from Postgres via `load_full_graph` (so reads-from-memory are correct after a restart; §8.2 reads from memory or Postgres).
   - Public API is async and **awaits the result of the enqueued command** via a per-command `asyncio.Future` the consumer resolves, so callers get the created/updated entity or the raised domain error back, while ordering is still enforced by the single consumer: `submit_create_node`, `submit_update_node`, `submit_soft_delete_node`, `submit_undo_node`, `submit_create_edge`, `submit_soft_delete_edge`, `submit_undo_edge`.
   - Each command, inside the consumer, runs in one DB transaction: write history snapshot (pre-state) → mutate Postgres → mutate the in-memory graph → commit. If the DB transaction fails, the in-memory mutation is rolled back / not applied (in-memory is updated only after a successful commit) so memory and Postgres never diverge.
   - `submit_create_edge`, inside the consumer (so the check-then-insert is race-free), rejects a duplicate live `(source_id, target_id, relation)` triple by raising `DuplicateEdge` (→ `409`) before insert; the partial unique index is the DB backstop (resolved open question 1).
   - `read_graph(engagement_id)` and `read_full(engagement_id)` serve from the in-memory graph (warm-starting the writer if needed) — reads do **not** go through the write queue.
   - `shutdown()` — cancels all consumer tasks and clears `_writers` (called from lifespan shutdown).
   - `reset_state()` — test hook to clear `_writers` between tests (mirrors `concurrency._states.clear()`).
   - Tests in `tests/test_writer.py`: serialization (two concurrent `submit_*` calls apply in enqueue order and both land), warm-start from Postgres, memory/Postgres consistency after each op, per-command error propagation without corrupting the queue, undo restores prior state, single consumer task per engagement (no second task spawned on concurrent first-writes — guard creation with a check so the registry is created exactly once).

5. **[M]** Add `backend/app/features/graph/service.py` — orchestration + invariants:
   - Membership chokepoint: every method first calls `engagements.repository.get_engagement_for_member(db, engagement_id, user_id)`; `None` → raise `EngagementNotFound` (NotFoundError → `404`), matching Slice 01/§17.1 posture.
   - Archived guard: if the engagement status is `archived`, raise a domain `EngagementArchived` (→ `409`) on any **write** path (§4 read-only).
   - Delegates all writes to `writer.submit_*`; delegates reads to `writer.read_*` / repository history queries.
   - `undo_node` / `undo_edge`: if there is no prior history entry, raise `NotFoundError` (→ `404`).
   - Edge create validates both endpoints exist and are non-deleted (else `404`). The duplicate-live-triple check is performed **inside the writer consumer** (not in the service) so it is race-free under the single-writer guarantee: the consumer calls `find_live_edge` before insert and raises `DuplicateEdge` (→ `409`) if a live edge with the same `(source_id, target_id, relation)` exists. The partial unique index is the DB-level backstop.
   - Add `EngagementArchived` and `DuplicateEdge` domain exceptions + register their `409` mappings in `app/core/errors/handlers` if not already present (reuse `ConflictError` mapping if the existing `409` handler fits; prefer distinct subclasses for clarity). `DuplicateEdge` is raised from the writer consumer and surfaced through the service unchanged.
   - Tests in `tests/test_service.py`: mock writer + repository; cover membership `404`, archived `409`, undo-no-history `404`, edge-missing-endpoint `404`.

6. **[M]** Add `backend/app/features/graph/router.py` — endpoints per the contract. All depend on `get_current_user` (`app.features.auth.deps`). Translate domain exceptions to HTTP via registered handlers. Tests in `tests/test_router.py` with `httpx.AsyncClient` + session override: cover 201/200/204/401/404/409/422 for the main paths.

7. **[S]** Wire `graph_router` in `backend/app/main.py` (`app.include_router(graph_router)`); call `graph.writer.shutdown()` in the lifespan shutdown block alongside the MCP shutdown.

8. **[S]** Startup reconciliation: the writer registry is in-process, so after a restart it is empty and warm-starts lazily from Postgres — no phantom state to reconcile (unlike tool runs). Add a short comment in `writer.py` documenting this (contrast with `mcp_repo.reconcile_stale_tool_runs`) and a `test_writer_warm_start_after_restart` proving a fresh registry rebuilds the in-memory graph from persisted rows. No new lifespan reconciliation step.

9. **[S]** Add Alembic migration for the four tables via the `write-alembic-migration` skill. Confirm `make migrate` runs cleanly against a fresh DB.

### Frontend tasks

10. **[S]** Run `make generate-api` to regenerate types into `frontend/src/shared/api/`; commit the updated `frontend/openapi.json` snapshot.

11. **[M]** Add `frontend/src/features/graph/api.ts` — TanStack Query hooks: `useGraph(engagementId)` (`GET .../graph`), `useGraphHistory(engagementId)`, `useCreateNode`, `useUpdateNode`, `useDeleteNode`, `useUndoNode`, `useCreateEdge`, `useDeleteEdge`, `useUndoEdge`. Each mutation invalidates `useGraph` (and `useGraphHistory` where relevant) on success.

12. **[M]** Add `frontend/src/features/graph/components/GraphNodeList.tsx` + test — a minimal table/list of live nodes (type badge, label, edit/delete/undo buttons). Empty state: "No graph entities yet — add one." Loading skeleton. (Force-directed view is Slice 08.)

13. **[M]** Add `frontend/src/features/graph/components/NodeEditDialog.tsx` + test — shadcn `Dialog` with type select, label input, properties as a key/value editor (or raw JSON textarea with validation); used for both create and edit. Submits via the relevant mutation; shows 422 inline; closes on success.

14. **[S]** Add `frontend/src/features/graph/components/GraphHistoryPanel.tsx` + test — lists soft-deleted nodes with an "Undo" (restore) button calling `useUndoNode`.

15. **[S]** Wire a "Graph" tab into the workspace shell (`/engagements/:id/workspace`) rendering `GraphNodeList` + an "Add node" button (opens `NodeEditDialog`) + a link/toggle to `GraphHistoryPanel`. This is a stand-in surface until Slice 08 replaces it with the Cytoscape pane.

## Test plan

- **Unit — backend** (coverage ≥ 80% on `app/features/graph/`):
  - Repository (real async test DB): `test_insert_and_load_live_graph`, `test_soft_delete_node_cascades_to_edges`, `test_history_records_prestate`, `test_latest_node_history_returns_most_recent`.
  - Writer (the critical invariant):
    - `test_writes_serialize_in_enqueue_order` — N concurrent `submit_*` calls land in a deterministic, single-consumer order.
    - `test_single_consumer_task_per_engagement` — concurrent first-writes spawn exactly one consumer/registry entry.
    - `test_warm_start_from_postgres` / `test_writer_warm_start_after_restart` — fresh registry rebuilds in-memory graph (incl. correct live vs deleted state) from rows.
    - `test_memory_and_postgres_consistent_after_each_op` — after create/update/delete/undo, `read_graph` matches `load_live_graph`.
    - `test_command_error_does_not_corrupt_queue` — a failing command propagates its error to that caller; subsequent commands still process.
    - `test_inmemory_not_mutated_on_db_failure` — DB-transaction failure leaves in-memory graph unchanged.
    - `test_undo_node_restores_prior_state`, `test_undo_edge_restores_prior_state`, `test_undo_with_no_history_raises`.
    - `test_duplicate_live_edge_rejected` — a second `submit_create_edge` for the same live `(source, target, relation)` raises `DuplicateEdge`; a distinct relation between the same pair succeeds; re-creating the triple after soft-deleting the original succeeds.
  - Service (mocked writer/repo): `test_write_non_member_returns_404`, `test_write_archived_engagement_returns_409`, `test_read_archived_engagement_allowed`, `test_create_edge_missing_endpoint_404`, `test_undo_no_history_404`.
  - Router (`AsyncClient`): `test_create_node_201`, `test_get_graph_200`, `test_update_node_200`, `test_delete_node_204_and_disappears_from_graph`, `test_undo_node_restores`, `test_create_edge_201`, `test_create_duplicate_edge_409`, `test_write_unauthenticated_401`, `test_write_non_member_404`, `test_write_archived_409`, `test_create_node_bad_type_422`.
- **Unit — frontend** (coverage ≥ 60% on `src/features/graph/`):
  - `GraphNodeList.test.tsx` — renders nodes, empty state, loading skeleton.
  - `NodeEditDialog.test.tsx` — create submit, edit pre-fill, 422 inline error, close on success.
  - `GraphHistoryPanel.test.tsx` — lists deleted nodes, undo button fires mutation.
- **Integration** (`@pytest.mark.integration`, real Postgres, real writer):
  - `test_concurrent_writes_serialize_via_writer` — fire many concurrent create/update requests against one engagement through the router; assert final `GET .../graph` is internally consistent and all writes are present (the single-writer invariant end-to-end).
  - `test_soft_delete_then_undo_roundtrip` — create → delete (gone from graph, present in history) → undo (reappears).
- **E2E** (Playwright) — one journey: `graph.spec.ts` — log in, open an engagement's Graph tab, add a node, edit it, delete it (disappears), open history, undo (reappears).

## Acceptance criteria

- `make test` passes (lint + mypy + tsc + pytest + vitest + playwright).
- `make migrate` runs the new migration cleanly against a fresh Postgres container.
- `make dev` brings up the stack; manually:
  1. Log in; open an engagement; open the Graph tab (empty state).
  2. Add a `host` node, then a `service` node, then link them with an edge — all appear.
  3. Edit the host's label/properties — change persists across a page refresh (served from the in-memory writer).
  4. Open two browser tabs and fire near-simultaneous edits; refresh — both edits are present and the graph is consistent (single-writer serialization).
  5. Delete the host — it (and its edges) disappear from the live graph; open History and Undo it — it reappears.
  6. Archive the engagement (via existing engagement settings if available, or DB flip) — graph writes return `409`; reads still work.
- `gh pr view` shows green CI.
- The single-writer invariant is demonstrable: a focused test (`test_concurrent_writes_serialize_via_writer`) and the manual step 4 both prove writes serialize through exactly one writer per engagement.

## Risks

- **Risk 1 — Single-writer is the critical invariant (ADR-0001).** The whole point is that all writes serialize through one consumer per engagement. The danger is accidentally spawning two consumer tasks (a race on lazy registry creation), or letting a write path mutate Postgres/NetworkX outside the consumer. Mitigation: create the per-engagement writer entry atomically before any `await` that could yield to a second concurrent creator (no `await` between the `if engagement_id not in _writers` check and the assignment), mirror `mcp/concurrency._get_state`'s synchronous lazy-init pattern, and assert single-consumer in `test_single_consumer_task_per_engagement`. CLAUDE.md anti-pattern: "Don't write to the graph outside the single-writer process" — the router/service must call only `writer.submit_*` for writes.
- **Risk 2 — Memory/Postgres divergence.** If the in-memory NetworkX graph is mutated before the DB commit succeeds, a rolled-back transaction leaves memory ahead of Postgres. Mitigation: apply the in-memory mutation only after a successful commit; `test_inmemory_not_mutated_on_db_failure` guards this.
- **Risk 3 — Warm-start after restart.** Because the registry is in-process and empty after a restart, the first read/write must rebuild the graph from Postgres including the deleted/history state needed for undo. Mitigation: `load_full_graph` + `test_writer_warm_start_after_restart`.
- **Risk 4 — Cascade soft-delete semantics.** Soft-deleting a node must soft-delete incident edges (else `GET .../graph` would return dangling edges referencing a gone node). Undo of a node does NOT auto-undo its edges (that would over-reach personal intent; per-entity undo is per-entity). Mitigation: document this in the service; `test_soft_delete_node_cascades_to_edges` and an undo test that asserts edges stay deleted.
- **Risk 5 — Per-command result delivery vs ordering.** Callers need the created entity / error back, but ordering must stay strictly serial. Using a per-command `asyncio.Future` resolved by the single consumer satisfies both. Risk: a never-resolved Future hanging a request if the consumer dies. Mitigation: consumer wraps each command in try/finally that always resolves the Future (with result or exception); a writer-task crash resolves all in-flight Futures with an error and is logged.
- **Risk 6 — Provenance temptation.** It is tempting to add `created_by` to nodes for the future audit/undo UI. CLAUDE.md and §8.2 forbid this — the audit log (Slice 10) is the source of truth. Mitigation: no actor columns on any of the four tables; reviewer checks this.
- **Risk 7 — Mypy two-config divergence on the writer's `Future`/generics.** Per project memory, `make lint` and pre-commit mypy can disagree on `type: ignore`. Mitigation: prefer explicit `cast()` over `# type: ignore` for the `asyncio.Future` result typing.

## Open questions for the human — RESOLVED

All three resolved at planning time in favor of the production-grade, requirements-aligned option (§8.2 "single source of truth"; vertical-slice discipline).

1. **Edge multiplicity — RESOLVED: MultiDiGraph + uniqueness on live triples.** Keep `networkx.MultiDiGraph` so distinct relations between the same node pair are allowed (`host -runs-> service` and `host -exposes-> service`), but reject an exact-duplicate *live* `(source_id, target_id, relation)` triple with `409`. Enforced by a partial unique index (`WHERE deleted = false`) plus a race-free check inside the writer consumer. Rationale: duplicate facts are noise that pollutes AI graph-injection (§5.3) and reporting (§12) and contradicts "single source of truth"; distinct relations remain expressible. A soft-deleted edge does not block re-creating the same triple.
2. **Archive transition — RESOLVED: honor the flag only; no transition endpoint here.** Engagement lifecycle (§4) belongs to the `engagements` feature, not `graph`; adding a half-lifecycle endpoint would break vertical-slice cohesion. This slice only *enforces* read-only (`409`) on `archived`. Acceptance step 6 sets the status via DB flip / test fixture until a dedicated lifecycle slice owns the transition.
3. **Undo depth — RESOLVED: single-step, repeatable.** Per-entity undo reverts to the immediately-prior history entry, repeatably (walking back through all prior states satisfies §8.2 "reverted to a prior state from its history"). Arbitrary-target undo is deferred; the personal one-by-one undo stack is explicitly Slice 09.

## Security review required?

**Yes.** This slice implements the single-writer-per-engagement invariant — explicitly listed as step-gated/security-review-required in CLAUDE.md and in the PROJECT_PLAN risky-slice summary (slice 07). The security reviewer must confirm: (a) all graph writes route exclusively through the single writer (no write path touches NetworkX/Postgres outside the consumer), (b) exactly one consumer task per engagement under concurrent first-writes, (c) memory/Postgres consistency on rollback, (d) engagement-isolation `404` posture on every endpoint via the membership chokepoint, (e) the archived-engagement read-only guard on all writes, and (f) no provenance columns leaked onto entities (§8.2 / audit-log-is-source-of-truth).

## Progress

(The stop-checkpoint hook and compact-handoff skill append here. Leave empty at planning time.)
- 2026-06-03T18:16:40Z — a69ec75 Slice 06: Kill switches + timeout-confirm (#20)
- 2026-06-03T18:17:07Z — a69ec75 Slice 06: Kill switches + timeout-confirm (#20)
