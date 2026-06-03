# Slice 09: Personal undo stack

**Branch**: `slice-09-personal-undo-stack`
**GitHub Issue**: #27
**Status**: planned
**Risky**: yes

---

## Decisions

The four open questions on this spec have been resolved by the human (2026-06-03):

1. **Staleness semantics = "any later write".** An undo-stack entry is stale if the target entity changed at all after that write — including the same user's own later edits. Strictly newest-first; a pop never clobbers a newer change.
2. **Empty-pop response = `200` with `undone: null`** (NOT `422`). `POST .../graph/undo-stack/pop` always returns `200`; `undone` is `null` when there was nothing to undo, and the returned `stack` lets the client disable the Undo button.
3. **Risk = RISKY / security-sensitive.** Front-matter `Risky: yes`; security review required at finish-slice (touches the single-writer process and the authorship surface).
4. **Audit wiring = clean seam now, wire in Slice 10.** The personal undo stack is self-contained — it is neither dependent on nor itself the audit log. Undo push/pop audit emission is a follow-up owned by Slice 10.

## Goal

Give each engagement member a personal, ~20-deep stack of their own last graph writes that they can undo one-by-one without ever silently reverting a teammate's later work.

## User-visible demo

After this slice is merged (building on the Slice 07/08 graph surface):

- Log in as engagement member **A** and open an engagement's Graph pane.
- Create three nodes and an edge. A new **"Undo" button** (and/or `Ctrl/Cmd+Z` shortcut) in the Graph toolbar is now enabled and shows the count of undoable personal writes (e.g. "Undo (4)").
- Press Undo once: the most recent of A's writes is reversed (the edge disappears). Press again: the third node is removed. The stack drains one entry per press, newest-first.
- The undo stack only ever contains **A's own writes** — writes that member **B** made (in another browser session) never appear in A's stack and are never touched by A's undo.
- **Teammate-safety demo**: A creates node `host-1`. B then edits `host-1`'s label. A presses Undo to revert their `host-1` creation: because B has since modified `host-1`, A's undo of that entry is **blocked / skipped with a clear message** ("Can't undo — a teammate changed this since") rather than silently discarding B's edit. A's stack entry is marked stale and removed; the next Undo press targets A's next-most-recent still-safe write.
- After ~20 writes, the stack caps: only A's most recent ~20 writes are retained; older entries fall off the bottom and are no longer personally-undoable (per-entity undo via Slice 07 history remains available as the fallback).
- The stack is **per-user and per-engagement**: opening a different engagement shows a different (or empty) stack; logging in as B shows B's stack, not A's.
- `GET /api/v1/engagements/{id}/graph/undo-stack` returns A's current undoable entries (newest-first); `POST .../graph/undo-stack/pop` reverts the top still-valid entry and returns the resulting entity + the updated stack depth (or `undone: null` when there was nothing left to undo).

## Out of scope

- Does NOT replace or remove **per-entity undo** from Slice 07 (`POST .../graph/nodes/{id}/undo`, `.../edges/{id}/undo`). Per-entity undo stays; the personal stack is a *second, additive* layer (§8.2 "Manual undo (two layers)").
- Does NOT implement a global "revert the whole graph to a timestamp" — explicitly excluded by §8.2 ("There is no global revert").
- Does NOT implement **redo** (re-applying an undone write). §8.2 specifies undo only; redo is not a requirement.
- Does NOT add the hash-chained **audit log** (Slice 10, §14). This slice does NOT depend on the audit log existing, and does NOT write audit entries. Per Decision 4, the undo stack is a separate operation log, not the audit log; undo push/pop **audit emission is a follow-up owned by Slice 10**, wired against the clean seam this slice leaves.
- Does NOT add `created_by`/`updated_by` provenance columns to `graph_nodes` / `graph_edges` (CLAUDE.md anti-pattern; §8.2 "No provenance attribution"). Authorship for undo lives in a dedicated per-user operation-stack table, never on the entities.
- Does NOT undo AI or tool-ingestion writes through the *personal* stack — only writes a human user performed via the API are pushed onto that user's stack. (AI/tool write paths are later slices; when they land, they simply do not push onto any human's personal stack.)
- Does NOT implement cross-user "see whose writes are where" UI, presence, or notifications (Slice 31/32).
- Does NOT change the single-writer contract, the four `graph_*` tables, or any Slice 07/08 endpoint shape.

## Requirements traceability

- **§8.2 — Manual undo, layer 2 (Personal undo stack)** — quoted verbatim:
  > **Personal undo stack:** each user has a personal stack of their own last ~20 graph writes that they can undo one-by-one. This is scoped to the individual user's actions and never silently reverts a teammate's work.

  This slice implements exactly that: a per-user, per-engagement stack capped at 20, undone one-by-one newest-first, scoped to that user's own writes, with an explicit teammate-safety guard so a teammate's later change is never silently clobbered.
- **§8.2 — Manual undo (two layers)** — establishes that per-entity undo (Slice 07) and the personal stack are distinct layers; this slice adds the second without disturbing the first.
- **§8.2 — Single-writer process per engagement** — every undo applied by this slice is a graph write and therefore MUST be serialized through the existing single-writer (`app/features/graph/writer.py`); this slice adds no new write path that bypasses the writer (ADR-0001).
- **§8.2 — No provenance attribution / audit log is the source of truth** — entities carry no author columns. The personal stack is a separate per-user operation log, not provenance on the graph entities and not the audit log itself.
- **§8.2 — History: soft delete with history** — the personal-undo "inverse" of a write reuses the existing soft-delete + `graph_*_history` machinery (undo of a create = soft-delete; undo of an edit/delete = restore prior history snapshot).
- **§17.1 — Engagement isolation** — every new endpoint resolves the engagement via the membership chokepoint (`engagements.repository.get_engagement_for_member`); non-members get `404`, matching Slice 01/07 posture. The stack is additionally scoped to the calling user, so members never see each other's stacks.
- **§4 — Archived engagements are read-only** — popping the personal stack is a write; it is rejected with `409` against an `archived` engagement (reuses Slice 07's `_require_writable`).
- **ADR-0001** — the personal-undo apply step routes exclusively through the single writer.

## Design: how the personal stack differs from, and builds on, Slice 07

Slice 07 shipped **per-entity undo**: `POST .../graph/nodes/{node_id}/undo` walks one step back through that entity's `graph_node_history` snapshots. It is *entity-addressed* (you pick a node and undo it) and *author-agnostic* (anyone can undo anyone's change to that entity).

This slice adds the **personal undo stack**: *user-addressed* ("undo my last write") and *author-scoped* (only the writes I made). It is built as a thin layer **on top of** the Slice 07 machinery, not a replacement:

- **Authorship without provenance on entities (key decision).** §8.2 and CLAUDE.md forbid `created_by` columns on `graph_nodes`/`graph_edges`. So authorship for undo is recorded in a **dedicated per-user operation-stack table** (`graph_user_undo_stack`) written at the same time as each user write. A stack row says "user U, in engagement E, performed operation type T (create_node / update_node / delete_node / create_edge / delete_edge) on entity X". This is an *operation log keyed by user*, not provenance smeared onto the entity. The graph entities themselves stay clean (reviewer must confirm). Per Decision 4, when the audit log lands in Slice 10 it becomes the system-wide tamper-evident record of *who-did-what* and Slice 10 owns wiring undo push/pop audit emission against the seam left here; this stack stays a lightweight, mutable, user-private convenience structure that predates and is independent of it.
- **The "inverse" of each write reuses Slice 07 history.** Popping a stack entry computes the inverse and applies it through the **existing writer commands**:
  - undo a `create_node` → `submit_soft_delete_node` (the node the user created goes away).
  - undo a `create_edge` → `submit_soft_delete_edge`.
  - undo an `update_node` / `delete_node` → `submit_undo_node` (the existing per-entity one-step-back, which restores the prior `graph_node_history` snapshot — i.e. exactly the state before this user's write).
  - undo a `delete_edge` → `submit_undo_edge`.
  No new mutation primitives are added to the writer; the personal-undo service *composes* the Slice 07 `submit_*` calls. This keeps the single-writer invariant intact for free.
- **Teammate-safety guard ("never silently reverts a teammate's work").** Per Decision 1, before applying an inverse the service checks whether the target entity has been mutated by **anyone** (including the same user) **after** this stack entry was recorded. It does this WITHOUT a `created_by` column by comparing the entity's current `updated_at` (and live/deleted state) against the snapshot captured when the stack entry was pushed: if the entity changed at all after this user's write was recorded, the entry is **stale**. A stale entry is NOT silently applied — it is dropped from the stack and surfaced to the user ("a teammate changed this since"), and the pop advances to the next still-safe entry. This is the literal §8.2 requirement: *never silently reverts a teammate's work* — and because the stack is strictly newest-first, a newer same-user change to the same entity is itself an earlier stack entry the user would have popped first.
- **Push semantics.** Every successful human write through `create_node` / `update_node` / `delete_node` / `create_edge` / `delete_edge` pushes one entry onto the caller's stack for that engagement. The stack is capped at 20 (oldest trimmed). Performing a personal undo (pop) does NOT itself push a new entry (no redo). Per-entity undo (`.../{id}/undo`) likewise does not push onto the personal stack (it is the other layer).

## Contract

OpenAPI delta. All endpoints require `cookieAuth` and engagement membership; non-members get `404`. No Slice 07/08 endpoints change shape.

```yaml
openapi: "3.1.0"
info:
  title: Adeptus API — Slice 09 delta
  version: "0.9.0"

paths:
  /api/v1/engagements/{engagement_id}/graph/undo-stack:
    get:
      operationId: get_undo_stack
      summary: List the calling user's personal undo stack for this engagement (newest-first)
      security: [{ cookieAuth: [] }]
      parameters:
        - { name: engagement_id, in: path, required: true, schema: { type: string, format: uuid } }
      responses:
        "200":
          content:
            application/json:
              schema: { $ref: "#/components/schemas/UndoStack" }
        "401": { description: Not authenticated }
        "404": { description: Engagement not found or caller not a member }

  /api/v1/engagements/{engagement_id}/graph/undo-stack/pop:
    post:
      operationId: pop_undo_stack
      summary: >-
        Undo the calling user's most recent still-valid write (write — the inverse
        is serialized through the single writer). Skips and drops stale entries
        whose target a teammate (or the user themselves) has since modified, never
        silently reverting that later work. Always returns 200; `undone` is null
        when there was nothing left to undo.
      security: [{ cookieAuth: [] }]
      parameters:
        - { name: engagement_id, in: path, required: true, schema: { type: string, format: uuid } }
      responses:
        "200":
          description: >-
            Either one personal write was undone (`undone` is the entry), or there
            was nothing left to undo (`undone` is null). Either way the refreshed
            (possibly empty) stack is returned so the client can update the Undo control.
          content:
            application/json:
              schema: { $ref: "#/components/schemas/UndoResult" }
        "401": { description: Not authenticated }
        "404": { description: Engagement not found or caller not a member }
        "409":
          description: >-
            Engagement is archived (read-only) — personal undo is a write and is rejected.

components:
  schemas:
    UndoOpType:
      type: string
      enum: [create_node, update_node, delete_node, create_edge, delete_edge]

    UndoStackEntry:
      type: object
      required: [id, op_type, entity_kind, entity_id, summary, recorded_at, stale]
      properties:
        id: { type: string, format: uuid }
        op_type: { $ref: "#/components/schemas/UndoOpType" }
        entity_kind: { type: string, enum: [node, edge] }
        entity_id: { type: string, format: uuid, description: "node_id or edge_id the op targeted" }
        summary: { type: string, description: "Human-readable label for the UI, e.g. 'Created host 10.0.0.5'." }
        recorded_at: { type: string, format: date-time }
        stale:
          type: boolean
          description: >-
            True if a teammate (or the user) modified the target entity after this
            entry was recorded; a stale entry will be skipped (and dropped) on pop.

    UndoStack:
      type: object
      required: [entries, depth]
      properties:
        depth: { type: integer, description: "Number of entries currently on the stack (<= 20)." }
        entries:
          type: array
          description: Newest-first.
          items: { $ref: "#/components/schemas/UndoStackEntry" }

    UndoResult:
      type: object
      required: [undone, skipped_stale, stack]
      properties:
        undone:
          description: >-
            The entry that was successfully undone, OR null when there was nothing
            left to undo (stack was empty, or every remaining entry was stale and
            dropped — caller should refresh the graph and disable the Undo control).
          oneOf:
            - { $ref: "#/components/schemas/UndoStackEntry" }
            - { type: "null" }
        skipped_stale:
          type: array
          description: Entries that were dropped as stale during this pop (for UI messaging).
          items: { $ref: "#/components/schemas/UndoStackEntry" }
        stack: { $ref: "#/components/schemas/UndoStack" }
```

Notes:
- Per Decision 2, a pop with nothing to undo returns `200` with `undone: null` and the (empty) `UndoStack` in `stack`, NOT a `422`. An empty stack is an expected condition, not an error: the frontend reads `undone === null` (and `stack.depth === 0`) to disable the control. There is no empty-stack error code on this endpoint.
- The `Node`/`Edge` mutated by an undo is observable via the existing `GET .../graph`; `UndoResult` deliberately returns the *entry* metadata rather than re-embedding the full entity, so the contract stays decoupled from node/edge shape. The frontend invalidates `useGraph` on success.

## Data model changes

One new table. No existing tables modified; no columns added to `graph_nodes` / `graph_edges` (anti-pattern guard). Alembic migration written via the `write-alembic-migration` skill during implementation (add the new model import to `backend/alembic/env.py` first; recreate the autogenerated file as the non-root user — per the Alembic-autogenerate memory).

- `graph_user_undo_stack` — a per-user, per-engagement append log of human graph writes, capped to the most recent 20 per `(engagement_id, user_id)` in application logic:
  - `id` UUID PK (`gen_random_uuid()`)
  - `engagement_id` UUID NOT NULL REFERENCES `engagements(id)` ON DELETE CASCADE
  - `user_id` UUID NOT NULL REFERENCES `users(id)` ON DELETE CASCADE — **this is authorship for the *operation log*, not provenance on the graph entity.** It lives here, never on `graph_nodes`/`graph_edges`.
  - `op_type` VARCHAR(16) NOT NULL — CHECK IN (`create_node`,`update_node`,`delete_node`,`create_edge`,`delete_edge`)
  - `entity_kind` VARCHAR(8) NOT NULL — CHECK IN (`node`,`edge`)
  - `entity_id` UUID NOT NULL — the `graph_nodes.id` or `graph_edges.id` the write targeted. **No FK** (deliberate: the entity may be hard-deleted by an engagement CASCADE, and we don't want a dangling-FK constraint to block that; the row is cleaned up by the entity cascade only via `engagement_id`). Validity is checked at pop-time, not by referential integrity.
  - `target_updated_at` TIMESTAMPTZ NOT NULL — the entity's `updated_at` value **immediately after** this user's write committed. Used as the staleness baseline: if the entity's current `updated_at` differs (any later write, by anyone, per Decision 1), the entry is stale.
  - `summary` VARCHAR(256) NOT NULL — precomputed human-readable label for the UI.
  - `undone` BOOLEAN NOT NULL DEFAULT `false` — set true when this entry has been popped (so it leaves the active stack but we keep an append-only trail; active stack = rows WHERE `undone = false`).
  - `recorded_at` TIMESTAMPTZ NOT NULL DEFAULT `now()`
  - Index: `ix_graph_user_undo_stack_owner` on `(engagement_id, user_id, undone, recorded_at DESC)` — the hot query (top-of-stack for this user, newest-first, active only).

Rationale for a table vs. deriving purely from `graph_*_history`: the existing history tables carry **no actor** (§8.2 no-provenance — confirmed in Slice 07), so they cannot tell us *whose* write a given snapshot was. We therefore need a user-keyed structure. Putting it in a separate operation-log table (not on the entity) honours the anti-pattern while giving us O(1) "my last write" lookups and a clean cap-to-20. Per Decision 4 it is intentionally *not* the audit log (Slice 10): it is mutable, user-private, and not hash-chained.

Trim-to-20 is enforced in the repository at push time (delete the oldest active rows for `(engagement_id, user_id)` beyond the 20th), not by a DB constraint.

## Tasks

Numbered continuously across the whole slice. Every commit subject cites its task id, e.g. `feat(slice-09): add undo-stack repository (task 3)`.

### Backend tasks

1. **[S]** Add `graph_user_undo_stack` to `backend/app/features/graph/models.py` — new `GraphUserUndoStack` ORM model on the shared `Base` (columns + index as above; CHECK constraints via `CheckConstraint`). Add no columns to existing models. Ensure the model is imported by `backend/alembic/env.py` (it is in the already-registered `graph.models` module, so verify autogenerate still sees it).

2. **[S]** Add personal-undo schemas to `backend/app/features/graph/schemas.py` — `UndoOpType` (StrEnum), `UndoStackEntry`, `UndoStack`, `UndoResult` (with `undone: UndoStackEntry | None`), matching the contract. `from_attributes=True` on the entry model so it maps from the ORM row.

3. **[M]** Add undo-stack repository functions to `backend/app/features/graph/repository.py` — `push_undo_entry(db, *, engagement_id, user_id, op_type, entity_kind, entity_id, target_updated_at, summary)` (insert + trim-to-20 of active rows for that owner), `list_active_undo_stack(db, engagement_id, user_id)` (active rows newest-first), `mark_undo_entry_undone(db, entry)`, and `get_top_active_undo_entry(db, engagement_id, user_id)`. Tests in `tests/test_repository.py`: `test_push_and_list_active_stack_newest_first`, `test_push_trims_to_twenty`, `test_stack_is_scoped_per_user_and_engagement`, `test_mark_undone_removes_from_active_stack`.

4. **[M]** Add the staleness check + push hooks to the writer/service seam. **Critical-invariant note:** the undo *apply* (the inverse mutation) MUST route through the existing `writer.submit_*` calls — do NOT add a new write path. Two sub-pieces:
   - **Push on write**: extend each successful human write in `service.py` (`create_node`, `update_node`, `delete_node`, `create_edge`, `delete_edge`) to call `repository.push_undo_entry(...)` AFTER the writer returns success, capturing the resulting entity's `updated_at` (for create/update) or the relevant timestamp. The writer returns the entity (or for deletes, the service re-reads the now-soft-deleted entity's `updated_at`). Pushing is best-effort-but-consistent: it happens in the same request, after the write commits. (Push does NOT go through the writer queue — it touches only `graph_user_undo_stack`, never the graph entities, so it cannot violate the single-writer invariant. Document this explicitly.)
   - **Staleness**: add a pure helper `_is_entry_stale(entry, current_entity) -> bool` comparing `entry.target_updated_at` to the entity's current `updated_at` (and handling the hard-deleted / missing case as stale). Per Decision 1 ANY later mutation (including the same user's) makes the entry stale. Unit-test it in isolation.

5. **[M]** Add personal-undo service functions to `backend/app/features/graph/service.py`:
   - `get_undo_stack(db, engagement_id, user_id) -> UndoStack` — membership chokepoint (`_require_member`), then `list_active_undo_stack`, computing each entry's `stale` flag against the current graph state (read via `writer.read_full`), newest-first.
   - `pop_undo_stack(db, engagement_id, user_id) -> UndoResult` — membership chokepoint + `_require_writable` (archived → `409`). Walk the active stack newest-first: for each entry, recompute staleness; if stale, `mark_undo_entry_undone` (drop it) and add to `skipped_stale`, continue; if fresh, apply the inverse via the matching `writer.submit_*` call (create→soft_delete, update/delete→`submit_undo_node`, edge variants analogously), `mark_undo_entry_undone`, and return `UndoResult(undone=entry, skipped_stale=..., stack=...)`. Per Decision 2, if the stack drains to empty (or started empty) with nothing applied, return `UndoResult(undone=None, skipped_stale=..., stack=<empty stack>)` — do NOT raise. No `EmptyUndoStack` exception and no `422` mapping. Reuse Slice 07 domain exceptions for the underlying mutation; if a `submit_*` raises `NodeNotFound`/`EdgeNotFound` (entity vanished) treat that entry as stale too.
   - Tests in `tests/test_service.py` (mock writer + repository): `test_pop_undoes_top_fresh_entry_via_writer`, `test_pop_skips_and_drops_stale_entry`, `test_pop_never_reverts_teammate_change`, `test_pop_empty_stack_returns_undone_null`, `test_pop_all_stale_returns_undone_null`, `test_pop_archived_409`, `test_get_stack_marks_stale_entries`, `test_pop_non_member_404`, `test_undo_create_calls_soft_delete`, `test_undo_update_calls_submit_undo_node`.

6. **[M]** Add the two endpoints to `backend/app/features/graph/router.py` — `get_undo_stack` (GET) and `pop_undo_stack` (POST), depending on `get_current_user`; pass `current_user.id` as the owner. `pop_undo_stack` returns `200` with the `UndoResult` in all non-error cases (including nothing-to-undo). Domain exceptions translate via the existing registered handlers (archived → `409`, non-member → `404`). Tests in `tests/test_router.py` with `AsyncClient` + session override: `test_get_undo_stack_200`, `test_pop_undo_stack_200`, `test_pop_empty_returns_200_undone_null`, `test_pop_archived_409`, `test_undo_stack_non_member_404`, `test_undo_stack_unauthenticated_401`, and a multi-user `test_pop_is_user_scoped` (user A's pop never touches B's writes).

7. **[S]** Confirm no lifespan / startup change is needed: the stack is fully persisted in Postgres (unlike the in-memory writer), so it survives restarts with no warm-start. Add a one-line comment documenting this. No change to `app/main.py`.

8. **[S]** Add Alembic migration for `graph_user_undo_stack` via the `write-alembic-migration` skill. Confirm `make migrate` runs cleanly against a fresh DB.

9. **[S]** **Audit seam (no emission).** Leave a clean, documented seam where Slice 10 will attach audit emission for undo push and pop, without wiring any audit call here (Decision 4). Concretely: keep `push_undo_entry` and `pop_undo_stack` as the single chokepoints for "a human write was recorded / undone", and add a short code comment + a note in this slice's eventual ADR/handoff pointing Slice 10 at them. Do NOT import or depend on any audit module.

### Frontend tasks

Numbering continues from the backend tasks.

10. **[S]** Run `make generate-api` to regenerate types into `frontend/src/shared/api/`; commit the updated `frontend/openapi.json` snapshot (adds `UndoStack`, `UndoStackEntry`, `UndoResult`, `UndoOpType`).

11. **[M]** Add personal-undo hooks to `frontend/src/features/graph/api.ts` — `useUndoStack(engagementId)` (`GET .../graph/undo-stack`) with query key `graphKeys.undoStack(engagementId)`, and `usePopUndoStack(engagementId)` (`POST .../graph/undo-stack/pop`) which on success invalidates `graphKeys.graph`, `graphKeys.history`, and `graphKeys.undoStack`. The pop always resolves with a `UndoResult`; treat `undone === null` as the "nothing to undo" signal (not an error) and surface `skipped_stale` to the caller. Add the `undoStack` query-key factory entry. Tests in `__tests__/api.test.tsx`: success pop invalidates graph + stack; `undone === null` response handled gracefully (no throw); stale-skip surfaces `skipped_stale` to the caller.

12. **[M]** Add `frontend/src/features/graph/components/UndoButton.tsx` + test — a toolbar button (shadcn `Button`) showing "Undo (N)" where N = stack depth; disabled when depth is 0; on click calls `usePopUndoStack`. When the response carries `skipped_stale`, show a toast/inline message ("Skipped — a teammate changed this since"). When the response has `undone === null` and an empty stack, just disable the button (no error toast). Wire a `Ctrl/Cmd+Z` keyboard handler scoped to the Graph pane that triggers the same pop (guarded so it doesn't fire while a text input/dialog is focused). Test: renders depth, disabled at zero, click fires mutation, stale message rendered, `undone === null` disables without error, Ctrl+Z fires pop, Ctrl+Z ignored while NodeEditDialog open.

13. **[S]** Wire `UndoButton` into `frontend/src/features/graph/components/GraphPane.tsx` toolbar (next to "Add node"), passing `engagementId`. Update `GraphPane.test.tsx` to assert the button is present. No change to the existing canvas/list/history surfaces.

## Test plan

- **Unit — backend** (coverage ≥ 80% on `app/features/graph/`; new code must not drop the gate):
  - Repository (real async test DB): `test_push_and_list_active_stack_newest_first`, `test_push_trims_to_twenty`, `test_stack_is_scoped_per_user_and_engagement`, `test_mark_undone_removes_from_active_stack`, `test_engagement_cascade_deletes_stack_rows`.
  - Staleness helper (pure): `test_is_entry_stale_when_updated_at_differs`, `test_is_entry_fresh_when_unchanged`, `test_missing_entity_is_stale`, `test_same_user_later_edit_makes_entry_stale` (Decision 1 — any later write, including the owner's own).
  - Service (mock writer + repo): `test_pop_undoes_top_fresh_entry_via_writer` (asserts the correct `writer.submit_*` is called), `test_undo_create_calls_soft_delete`, `test_undo_update_calls_submit_undo_node`, `test_undo_create_edge_calls_soft_delete_edge`, `test_pop_skips_and_drops_stale_entry`, `test_pop_never_reverts_teammate_change` (the §8.2 guarantee — a teammate edit after the user's write blocks that entry), `test_pop_empty_stack_returns_undone_null`, `test_pop_all_stale_returns_undone_null`, `test_pop_archived_409`, `test_pop_non_member_404`, `test_get_stack_marks_stale_entries`, `test_pop_entity_vanished_treated_as_stale`.
  - Push integration into existing writes (mock writer): `test_create_node_pushes_undo_entry`, `test_update_node_pushes_undo_entry`, `test_delete_node_pushes_undo_entry`, `test_create_edge_pushes_undo_entry`, `test_per_entity_undo_does_not_push` (Slice 07 `.../{id}/undo` must NOT push onto the personal stack — it is the other layer), `test_pop_does_not_push` (no redo).
  - Router (`AsyncClient`): `test_get_undo_stack_200`, `test_pop_undo_stack_200`, `test_pop_empty_returns_200_undone_null`, `test_pop_archived_409`, `test_undo_stack_non_member_404`, `test_undo_stack_unauthenticated_401`, `test_pop_is_user_scoped`.
- **Unit — frontend** (coverage ≥ 60% on `src/features/graph/`):
  - `api.test.tsx`: pop success invalidates graph + stack queries; `undone === null` empty-stack response handled without throwing; `skipped_stale` exposed.
  - `UndoButton.test.tsx`: depth label, disabled-at-zero, click fires pop, stale message, `undone === null` disables button without an error toast, `Ctrl/Cmd+Z` fires pop and is ignored while a dialog/input is focused.
  - `GraphPane.test.tsx`: UndoButton rendered in the toolbar.
- **Integration** (`@pytest.mark.integration`, real Postgres + real writer):
  - `test_personal_undo_roundtrip` — create 3 nodes via the router as one user; `GET .../graph/undo-stack` shows 3 entries newest-first; `pop` removes the newest; `GET .../graph` reflects it; stack depth drops to 2.
  - `test_personal_undo_never_reverts_teammate_work` — user A creates `host-1`; user B edits `host-1` (via the router as B); A pops their stack → A's `host-1`-create entry is reported stale + skipped (dropped), B's edit survives in `GET .../graph`. **This is the headline acceptance test for §8.2.**
  - `test_stack_caps_at_twenty` — 25 writes by one user; stack holds 20; the 5 oldest are no longer poppable (but their entities remain per-entity-undoable via Slice 07).
  - `test_pop_empty_stack_returns_undone_null` — a member with no writes pops their (empty) stack and gets `200` with `undone: null` and an empty `stack`.
- **E2E** (Playwright) — extend or add `graph-undo.spec.ts`: log in, open Graph pane, create two nodes, press the Undo button twice, see both removed; assert the button disables at depth 0.

## Acceptance criteria

- `make test` passes (ruff + mypy + eslint + tsc + pytest + vitest + playwright); coverage gates hold.
- `make migrate` runs the new `graph_user_undo_stack` migration cleanly against a fresh Postgres container.
- `make dev` brings up the stack; manually:
  1. Log in as user A; open an engagement's Graph pane. The Undo button reads "Undo (0)" and is disabled. (Pressing pop via the API on an empty stack returns `200` with `undone: null`, not an error.)
  2. Create three nodes and an edge — Undo now reads "Undo (4)". Press Undo four times: each press reverses A's most-recent write newest-first; the graph empties; the button disables again.
  3. **Teammate safety**: A creates `host-1`. In a second browser (user B, a co-member), edit `host-1`'s label. Back as A, press Undo: A sees "Skipped — a teammate changed this since" and B's label edit remains in the graph (NOT reverted). A's stale entry is gone; the next press targets A's next own write.
  4. **Isolation**: log in as B in the same engagement — B's Undo stack reflects only B's writes, never A's. Open a different engagement — the stack is independent (empty).
  5. Archive the engagement (DB flip / fixture, as in Slice 07) — pressing Undo returns `409` and the UI shows it; reads still work.
  6. After 21+ writes, only the most recent 20 are personally-undoable; the 21st-oldest is gone from the stack but still restorable via the Slice 07 per-entity undo in the History panel.
- `gh pr view` shows green CI.
- The §8.2 guarantee is demonstrable by `test_personal_undo_never_reverts_teammate_work` (automated) and manual step 3.

## Risks

- **Risk 1 — "never silently reverts a teammate's work" is the load-bearing §8.2 guarantee.** The whole point of this slice. The danger is a staleness check that misses a concurrent teammate edit (e.g. relying on a coarse timestamp). Mitigation: compare the captured `target_updated_at` against the entity's *current* `updated_at`; the writer already sets `updated_at` via `onupdate=func.now()` on every mutation, so any teammate write since the user's own write changes it. Treat a hard-deleted/missing entity as stale. The dedicated test (`test_pop_never_reverts_teammate_change`) and integration test guard this; a stale entry is dropped + surfaced, never applied.
- **Risk 2 — `updated_at` resolution / same-tick collisions.** If two writes land within the same DB clock tick, `updated_at` might not change and a teammate edit could look "fresh". Mitigation: Postgres `now()` is sub-microsecond; cross-user writes go through the single writer serially (ADR-0001), so the teammate's commit strictly follows the user's and bumps `updated_at`. If residual risk remains, fall back to also comparing against the latest `graph_*_history.recorded_at` for the entity (a new history row implies a mutation since). Note in the spec; covered by the staleness unit tests.
- **Risk 3 — Single-writer invariant (ADR-0001).** Undo *apply* must route through `writer.submit_*`; the push and staleness reads must NOT mutate `graph_nodes`/`graph_edges` directly. Mitigation: the service composes only existing `submit_*` calls for the inverse; the only direct DB writes this slice adds are to `graph_user_undo_stack` (never a graph entity). Reviewer confirms no graph-entity write bypasses the writer.
- **Risk 4 — Provenance temptation.** It is tempting to add `created_by` to `graph_nodes` to know "whose write" — forbidden by §8.2 / CLAUDE.md. Mitigation: authorship lives only in the separate `graph_user_undo_stack` operation log; the four `graph_*` tables are untouched. Reviewer checks the migration adds no columns to existing tables.
- **Risk 5 — Confusing the two undo layers in the UI.** Per-entity undo (History panel, Slice 07) and personal undo (toolbar button) coexist; users could be confused if pressing one affects the other. Mitigation: the personal pop does not touch the History-panel semantics; per-entity undo does not push onto the personal stack (`test_per_entity_undo_does_not_push`). Label the toolbar button clearly ("Undo my last change").
- **Risk 6 — Stack rebuild after restart.** Unlike the in-memory writer, the stack is fully in Postgres, so no warm-start is needed — but the staleness baseline (`target_updated_at`) must have been captured at push time (persisted), not recomputed. Mitigation: capture `target_updated_at` at push and store it; never recompute the baseline.
- **Risk 7 — Keyboard shortcut hijack.** A global `Ctrl/Cmd+Z` could fire while the user is typing in the NodeEditDialog or a label input. Mitigation: scope the handler to the Graph pane and ignore it when an input/textarea/contenteditable or the dialog is focused; covered by a UndoButton test.
- **Risk 8 — Audit-emission coupling creep (Slice 10).** Per Decision 4 this slice deliberately leaves an audit seam but emits nothing. Risk: a future implementer mistakes the undo stack for the audit log, or Slice 10 fails to find the seam. Mitigation: task 9 documents the two chokepoints (`push_undo_entry`, `pop_undo_stack`) explicitly; this slice imports no audit module; the data-model rationale states the stack is mutable/user-private and NOT the hash-chained audit log.

## Open questions for the human

All resolved — see the **Decisions** section at the top of this spec (staleness = any later write; empty-pop = `200` with `undone: null`; slice is RISKY / security-reviewed; audit emission deferred to Slice 10 against a clean seam).

## Security review required?

**Yes (Decision 3).** This slice touches the **single-writer graph process** (the undo *apply* routes through `writer.submit_*`) and the **authorship / who-did-what** surface, both flagged as step-gated/risky in CLAUDE.md. The reviewer must confirm:

- (a) every undo-apply mutation routes exclusively through the existing single writer — no new write path to `graph_nodes`/`graph_edges`;
- (b) NO provenance columns are added to the four `graph_*` tables (authorship lives only in `graph_user_undo_stack`);
- (c) the teammate-safety guard genuinely prevents silently reverting another user's later work (§8.2) — and, per Decision 1, any later write at all (including the owner's own) marks the entry stale;
- (d) the stack is strictly scoped per `(engagement_id, user_id)` with the §17.1 membership chokepoint on every endpoint (a member cannot read or pop another member's stack);
- (e) the archived-engagement read-only guard applies to `pop`.

PROJECT_PLAN's risky-slice summary now includes 09.

## Progress

(The stop-checkpoint hook and compact-handoff skill append here. Leave empty at planning time.)
- 2026-06-03T21:20:00Z — 27cd0bf docs(slice-09): add slice spec, mark in-progress (#27)
- 2026-06-03T21:22:11Z — 27cd0bf docs(slice-09): add slice spec, mark in-progress (#27)
