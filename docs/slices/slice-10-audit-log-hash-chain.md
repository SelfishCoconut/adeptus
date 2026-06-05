# Slice 10: Audit log + hash-chain

**Branch**: `slice-10-audit-log-hash-chain`
**GitHub Issue**: #29
**Status**: planned
**Risky**: yes (audit log integrity — step-gated)

---

## Goal

Record every security-relevant action as a tamper-evident, hash-chained audit entry (including the `self_approved` boolean on approvals) and ship a `verify-chain` CLI that detects any tampering.

## User-visible demo

After this slice is merged:

- Perform ordinary actions in the running stack — **log in**, **run a tool against the sandbox** (Slice 04/26), **create / edit / delete a graph node or edge** (Slice 07). Each produces exactly one append-only audit entry.
- Run the new chain verifier from the repo root: `make verify-audit` (wraps `python -m app.features.audit.verify`). On an untouched DB it prints `audit chain OK — N entries verified` and exits `0`.
- Manually corrupt one row in `psql` (e.g. flip a byte of a stored `payload` or rewrite an `actor_user_id`). Re-run `make verify-audit`: it now prints the **first broken link** (entry id, sequence number, expected-vs-actual hash) and exits non-zero. Deleting a middle row, or re-ordering, is likewise caught (the chain's `prev_hash` linkage breaks).
- As an **admin**, call `GET /api/v1/audit?engagement_id=...` (and `GET /api/v1/audit/global` for instance-wide events like logins) to page through the recorded entries, newest-first, with their `actor_user_id`, `action`, `target`, `self_approved`, `seq`, and `entry_hash`. Approval entries (wired by Slice 16) will carry `self_approved=true|false`; this slice ships the column, the schema, and the recording chokepoint so Slice 16 only has to call it.
- Every audit entry shows **who** acted (user attribution) — satisfying §14 "with user attribution" — without any provenance columns being added to graph entities (§8.2 / CLAUDE.md anti-pattern: the audit log is the source of truth).

## Out of scope

- Does **NOT** add the **approval / rejection flow** itself — that is Slice 16, which `Depends on: 11, 10`. This slice defines the `approval_granted` / `approval_rejected` action types, the `self_approved` column, and the `record()` chokepoint so Slice 16 can emit them, but ships **no approval UI or endpoint** and no live approval events (§5.2 is only partially touched: the `self_approved` mechanism, not the flow).
- Does **NOT** add the **AI call** audit source — there is no AI/LLM feature yet (Slice 11+). The `ai_call` action type is reserved in the enum and documented as a seam; no emission is wired because there is no caller.
- Does **NOT** add the **session-replay timeline scrubber** (Slice 33, which `Depends on: 10`). §14 explicitly states the audit log is **separate from session replay**; this slice builds only the audit log.
- Does **NOT** add the **admin dashboard** (Slice 35) or **token/cost tracking** (Slice 36) — other §14 bullets, separate slices.
- Does **NOT** retro-emit audit entries for actions that happened before this slice landed (no backfill). The chain starts empty.
- Does **NOT** add **provenance columns** (`created_by`, `updated_by`, etc.) to `graph_nodes` / `graph_edges` / `findings` or any entity table (§8.2 "No provenance attribution"; CLAUDE.md anti-pattern). Actor attribution lives only in the audit log.
- Does **NOT** make the audit log mutable or editable through any API — it is **append-only**; there is no update or delete endpoint, and the verifier assumes inserts are the only legitimate mutation.
- Does **NOT** encrypt audit payloads or add digital signatures / external timestamping. v1 tamper-evidence is an in-DB hash chain (§14 "entries are hash-chained"), not a cryptographically-signed or notarized log.
- Does **NOT** widen `core/` or `shared/` — all new code lives under `app/features/audit/` per CLAUDE.md.

## Requirements traceability

- **§14 — Audit log** — quoted verbatim:
  > **Audit log:** separate from session replay.
  > * Records every tool run, AI call, graph edit, login, and approval/rejection — with user attribution.
  > * Approval entries include the `self_approved` boolean (§5.2).

  This slice implements the audit log table + service: it records **logins**, **tool runs**, and **graph edits** now (the action sources that exist today), with **user attribution** (`actor_user_id`), and reserves `ai_call` and `approval_granted/rejected` action types as documented seams for the slices that introduce those sources (11, 16).
- **§14 — Tamper-evident audit log** — quoted verbatim:
  > **Tamper-evident audit log:** entries are hash-chained.

  Implemented as a per-row `entry_hash = SHA-256(prev_hash || canonical(entry_fields))` chain with a monotonic `seq`; the `verify-chain` CLI recomputes the chain and reports the first divergence. This is the *risky* core of the slice.
- **§5.2 — Attribution / `self_approved`** — quoted verbatim:
  > **Attribution:** every approval and rejection records the acting user. [...] The audit log includes a `self_approved` boolean (true when initiator == approver) so reviewers can filter for cross-member approvals vs self-approvals.

  This slice adds the `self_approved` boolean column and surfaces it in the read API + filtering, so Slice 16 (approval flow) can populate it. `self_approved` is `NULL` for non-approval actions and a concrete boolean only on `approval_granted` / `approval_rejected` entries.
- **§17.4 — The audit log is the source of truth** — quoted verbatim:
  > Provenance, attribution, and forensic reconstruction live there — not duplicated across every entity.

  Reinforces the §8.2 no-provenance rule: this slice is the canonical home for "who did what", so no slice (now or later) needs `created_by` columns on entities.
- **§17.1 — Engagement isolation** — the engagement-scoped read endpoint (`GET /api/v1/audit?engagement_id=...`) resolves the engagement via the existing membership chokepoint (`engagements.repository`), so a member only sees their engagements' entries; non-members get `404`. Instance-global entries (logins, with no engagement) are admin-only via `GET /api/v1/audit/global`.
- **§4 — Archived engagements are read-only** — recording is internal (not a user write endpoint), and reads of an archived engagement's audit trail remain available (browsable), consistent with §4 read-only semantics.
- **ADR-0001 (single-writer)** — audit recording does **not** touch the graph; it never goes through the single writer and never mutates `graph_*`. It writes only its own `audit_entries` table.

## Design: hash-chain construction and the recording chokepoint

### The chain

`audit_entries` is an **append-only** table with a monotonically increasing per-instance `seq` (BIGINT, gap-free, assigned under a lock at insert time). Each row stores:

- the **content fields** that are hashed: `seq`, `created_at`, `action`, `actor_user_id`, `engagement_id`, `target_type`, `target_id`, `self_approved`, and a canonical JSON `payload`;
- `prev_hash` — the `entry_hash` of the immediately preceding row (the genesis row uses a fixed all-zero `prev_hash`);
- `entry_hash` — `SHA-256( prev_hash_bytes || canonical_serialization(content_fields) )`, stored hex.

**Canonical serialization** is deterministic: a fixed field order, UTC ISO-8601 timestamps with fixed precision, `payload` serialized as JSON with sorted keys and no insignificant whitespace, NULLs encoded as a sentinel. A single pure helper `compute_entry_hash(prev_hash, fields) -> str` is the one source of truth for both the writer and the verifier (they MUST call the identical function — tested by a round-trip test).

**Why a chain and not just per-row hashes:** a per-row hash detects field tampering but not row deletion or reordering. Linking each row to `prev_hash` means removing/reordering/inserting any row breaks every subsequent link, which the verifier detects (§14 "tamper-evident").

### Single-appender invariant (the load-bearing safety property)

`seq` and `prev_hash` must be assigned without races, or two concurrent inserts could pick the same `prev_hash` and fork the chain. The recording path therefore serializes appends: it `SELECT ... FOR UPDATE` on a dedicated single-row `audit_chain_head` table (holding the current `seq` and `head_hash`) inside the same transaction as the insert, computes the new row's `seq`/`prev_hash`/`entry_hash`, inserts the row, and updates `audit_chain_head`. This makes the append atomic and totally-ordered even under concurrency, mirroring the "single-writer eliminates races" philosophy of ADR-0001 (but for the audit chain, not the graph). The append is a normal DB transaction — no separate process is required.

### The recording chokepoint (`audit.service.record`)

One async function — `record(db, *, action, actor_user_id, engagement_id=None, target_type=None, target_id=None, self_approved=None, payload=None)` — is the **only** way to write an audit entry. Callers in other features call it after their own state change commits, within (or alongside) the same request. This slice wires three callers:

1. **Login** — `auth.service`/`router.login`: emit `login` with `actor_user_id=user.id`, no engagement. (Logout and failed-login are reserved follow-ups; this slice emits successful `login` to satisfy §14 "login".)
2. **Tool run** — `mcp.service.execute_tool_run`: emit `tool_run` with the engagement, `actor_user_id`, and a payload `{server, tool, target, tool_run_id, status}`.
3. **Graph edit** — `graph.service` `create_node` / `update_node` / `delete_node` / `create_edge` / `delete_edge`: emit `graph_node_created` / `graph_node_updated` / `graph_node_deleted` / `graph_edge_created` / `graph_edge_deleted` with the engagement, actor, and `target_type`/`target_id`. This is the §14 "graph edit ... with user attribution" requirement — and it is how attribution is recorded **without** putting `created_by` on the entity (the entity stays clean; the audit row carries the actor).

`approval_granted` / `approval_rejected` (with `self_approved`) and `ai_call` are **defined in the action enum and accepted by `record()`** but have **no caller** in this slice; tasks document the exact seam for Slices 16 and 11.

**Note on the Slice 09 seam:** Slice 09 left a documented seam (its `push_undo_entry` / `pop_undo_stack` chokepoints) for audit emission. This slice MAY wire `graph_*` emission at the `graph.service` mutation chokepoints (which is where both ordinary writes and undo-applied writes flow), satisfying that seam. If undo-apply double-counts an entry, the spec's tasks call for asserting one entry per logical graph mutation; see Open Questions.

## Contract

OpenAPI delta. New read-only endpoints; **no write endpoint** (the log is append-only and written only internally). All require `cookieAuth`. The engagement-scoped list requires membership (`404` for non-members, §17.1); the global list and the per-entry fetch require **admin** (§14 is an admin/forensic surface).

```yaml
openapi: "3.1.0"
info:
  title: Adeptus API — Slice 10 delta
  version: "0.10.0"

paths:
  /api/v1/audit:
    get:
      operationId: list_engagement_audit
      summary: List audit entries for an engagement (newest-first, paginated). Requires membership.
      security: [{ cookieAuth: [] }]
      parameters:
        - { name: engagement_id, in: query, required: true, schema: { type: string, format: uuid } }
        - { name: action, in: query, required: false, schema: { $ref: "#/components/schemas/AuditAction" } }
        - name: self_approved
          in: query
          required: false
          schema: { type: boolean }
          description: >-
            Filter approval entries by self_approved (§5.2 — cross-member vs self-approvals).
        - { name: cursor, in: query, required: false, schema: { type: string } }
        - { name: limit, in: query, required: false, schema: { type: integer, minimum: 1, maximum: 100, default: 50 } }
      responses:
        "200":
          content:
            application/json:
              schema: { $ref: "#/components/schemas/AuditPage" }
        "401": { description: Not authenticated }
        "404": { description: Engagement not found or caller not a member }

  /api/v1/audit/global:
    get:
      operationId: list_global_audit
      summary: List instance-wide audit entries (e.g. logins) with no engagement scope. Admin only.
      security: [{ cookieAuth: [] }]
      parameters:
        - { name: action, in: query, required: false, schema: { $ref: "#/components/schemas/AuditAction" } }
        - { name: cursor, in: query, required: false, schema: { type: string } }
        - { name: limit, in: query, required: false, schema: { type: integer, minimum: 1, maximum: 100, default: 50 } }
      responses:
        "200":
          content:
            application/json:
              schema: { $ref: "#/components/schemas/AuditPage" }
        "401": { description: Not authenticated }
        "403": { description: Caller is not an admin }

components:
  schemas:
    AuditAction:
      type: string
      enum:
        - login
        - logout
        - login_failed
        - tool_run                # tool invocation (run row created)
        - tool_run_completed      # tool run reached a terminal status
        - graph_node_created
        - graph_node_updated
        - graph_node_deleted
        - graph_edge_created
        - graph_edge_deleted
        - approval_granted      # reserved — emitted by Slice 16
        - approval_rejected     # reserved — emitted by Slice 16
        - ai_call               # reserved — emitted by Slice 11+

    AuditEntry:
      type: object
      required: [id, seq, action, created_at, entry_hash, prev_hash]
      properties:
        id: { type: string, format: uuid }
        seq: { type: integer, format: int64, description: "Gap-free monotonic position in the chain." }
        action: { $ref: "#/components/schemas/AuditAction" }
        actor_user_id:
          oneOf: [{ type: string, format: uuid }, { type: "null" }]
          description: The acting user (§14 attribution). Null only for system-originated events (none in v1).
        engagement_id:
          oneOf: [{ type: string, format: uuid }, { type: "null" }]
          description: Null for instance-global events (e.g. login).
        target_type:
          oneOf: [{ type: string }, { type: "null" }]
          description: "e.g. node | edge | tool_run."
        target_id:
          oneOf: [{ type: string }, { type: "null" }]
        self_approved:
          oneOf: [{ type: boolean }, { type: "null" }]
          description: >-
            Null for non-approval actions; boolean only on approval_granted/approval_rejected
            (true when initiator == approver, §5.2). Populated by Slice 16.
        payload:
          type: object
          additionalProperties: true
          description: Action-specific, canonically serialized when hashed.
        created_at: { type: string, format: date-time }
        prev_hash: { type: string, description: "Hex SHA-256 of the previous entry (all-zero for genesis)." }
        entry_hash: { type: string, description: "Hex SHA-256 over prev_hash + canonical content." }

    AuditPage:
      type: object
      required: [items, next_cursor]
      properties:
        items:
          type: array
          items: { $ref: "#/components/schemas/AuditEntry" }
        next_cursor:
          oneOf: [{ type: string }, { type: "null" }]
          description: Opaque cursor for the next (older) page; null on the last page.
```

The `verify-chain` capability is a **CLI**, not an HTTP endpoint (forensic tooling, run by an operator with DB access). It is invoked via `python -m app.features.audit.verify` and surfaced as `make verify-audit`. It exits `0` on an intact chain and non-zero on the first broken link, printing the offending `seq`/`id`/expected-vs-actual hash.

## Data model changes

Alembic migration written via the `write-alembic-migration` skill during implementation (add the new model import to `backend/alembic/env.py` first — per the Alembic-autogenerate memory; the new module under `app/features/audit/models.py` must be imported by `env.py`; recreate the autogenerated file as the non-root user).

Two new tables. **No columns added to any existing table** (anti-pattern guard — reviewer confirms the migration touches no `graph_*`, `findings`, or entity tables).

- `audit_entries` — append-only, hash-chained:
  - `id` UUID PK (`gen_random_uuid()`)
  - `seq` BIGINT NOT NULL UNIQUE — gap-free monotonic chain position (assigned under the head lock, not a bare `SERIAL`, which can gap on rollback). Indexed for ordered scans.
  - `action` VARCHAR(32) NOT NULL — CHECK IN the `AuditAction` enum values.
  - `actor_user_id` UUID NULL — **no foreign key** (Open Question 2, RESOLVED). An immutable, denormalized, **hashed** attribution value. Deleting a user must never touch or break the audit row, and the field must stay tamper-evident; a hard FK (`RESTRICT`/`SET NULL`/`CASCADE`) cannot satisfy both, so there is no `REFERENCES`. NULL only for system/anonymous events (e.g. `login_failed`).
  - `engagement_id` UUID NULL — **no foreign key**, same rationale: immutable, denormalized, **hashed**. NULL for instance-global events (login/logout/login_failed).
  - `target_type` VARCHAR(32) NULL
  - `target_id` VARCHAR(64) NULL — string (not FK): targets span heterogeneous tables and may be hard-deleted; the audit row must survive.
  - `self_approved` BOOLEAN NULL — §5.2; NULL except on approval actions.
  - `payload` JSONB NOT NULL DEFAULT `'{}'::jsonb` — action-specific detail, serialized canonically when hashed.
  - `prev_hash` CHAR(64) NOT NULL — hex SHA-256 of the previous row's `entry_hash`; genesis = 64 zeros.
  - `entry_hash` CHAR(64) NOT NULL UNIQUE — hex SHA-256 over `prev_hash || canonical(content)`.
  - `created_at` TIMESTAMPTZ NOT NULL DEFAULT `now()`
  - Indexes:
    - `ix_audit_entries_seq` UNIQUE on `(seq)` — chain order + verifier scan.
    - `ix_audit_entries_engagement_seq` on `(engagement_id, seq DESC)` — engagement-scoped newest-first paging.
    - `ix_audit_entries_action_seq` on `(action, seq DESC)` — action filter + global paging.
  - **No `updated_at`, no soft-delete, no update/delete path** — append-only by design.

- `audit_chain_head` — single-row lock + head pointer for serialized appends:
  - `id` SMALLINT PK CHECK (`id = 1`) — enforces exactly one row.
  - `last_seq` BIGINT NOT NULL DEFAULT 0
  - `head_hash` CHAR(64) NOT NULL DEFAULT (64 zeros) — `entry_hash` of the latest entry (== genesis `prev_hash` when empty).
  - Seeded with the single `(1, 0, <zeros>)` row in the migration.

Rationale for `audit_chain_head` vs. `MAX(seq)`-at-insert: a `SELECT MAX(seq)` race could assign duplicate `seq`/`prev_hash` and fork the chain. The single-row `FOR UPDATE` lock gives a strict total order on appends with negligible contention (audit writes are infrequent relative to a 2–5 person team), and is the audit analogue of the single-writer invariant.

## Tasks

Numbered continuously across the whole slice. Every commit subject cites its task id, e.g. `feat(slice-10): add audit hash helper (task 2)`.

### Backend tasks

1. **[S]** Add `app/features/audit/models.py` — `AuditEntry` and `AuditChainHead` ORM models on the shared `Base` (columns, CHECK constraints via `CheckConstraint`, indexes as above). Add no columns to existing models. Register the module import in `backend/alembic/env.py`.

2. **[M]** Add `app/features/audit/hashing.py` — the **pure** canonical-serialization + `compute_entry_hash(prev_hash: str, fields: AuditContent) -> str` helper (SHA-256, deterministic field order, sorted-key JSON `payload`, fixed timestamp precision, NULL sentinel). This is the single source of truth used by both writer and verifier. Heavily unit-tested in `tests/test_hashing.py`: deterministic for identical input; differs when any field changes; payload key-order-independent; timestamp precision stable; genesis prev_hash handling. **[Risky — reviewer focus.]**

3. **[S]** Add `app/features/audit/schemas.py` — `AuditAction` (StrEnum matching the contract enum incl. reserved `approval_*` / `ai_call`), `AuditEntryRead`, `AuditPage` (cursor pagination), and the internal `AuditContent` value object the hasher consumes. `from_attributes=True` on the read model.

4. **[M]** Add `app/features/audit/repository.py` — `append_entry(db, content) -> AuditEntry` (the serialized-append: `SELECT ... FOR UPDATE` on `audit_chain_head`, compute `seq`/`prev_hash`/`entry_hash` via `hashing.compute_entry_hash`, insert the row, bump the head), plus `list_for_engagement(...)`, `list_global(...)`, and `iter_chain_ordered(db)` (a `seq`-ordered async iterator for the verifier). Tests in `tests/test_repository.py`: `test_append_assigns_sequential_seq`, `test_append_links_prev_hash`, `test_genesis_uses_zero_prev_hash`, `test_concurrent_appends_serialize_no_fork` (interleave two appends, assert distinct contiguous `seq` and an unbroken chain), `test_list_for_engagement_newest_first_paginates`, `test_list_global_filters_by_action`, `test_no_update_or_delete_method_exists`. **[Risky — the no-fork test is load-bearing.]**

5. **[M]** Add `app/features/audit/service.py` — the public chokepoint `record(db, *, action, actor_user_id, engagement_id=None, target_type=None, target_id=None, self_approved=None, payload=None)` building an `AuditContent` and calling `repository.append_entry`, plus `list_engagement_audit(db, *, engagement_id, requester, ...)` (membership chokepoint via `engagements.repository`; `404` for non-members) and `list_global_audit(db, *, requester, ...)` (admin-only; raises `ForbiddenError` otherwise). Tests in `tests/test_service.py`: `test_record_emits_entry_with_attribution`, `test_record_self_approved_passthrough`, `test_record_login_has_null_engagement`, `test_list_engagement_non_member_404`, `test_list_global_non_admin_403`, `test_list_engagement_self_approved_filter`.

6. **[M]** Add `app/features/audit/router.py` — `list_engagement_audit` (GET `/api/v1/audit`) and `list_global_audit` (GET `/api/v1/audit/global`), depending on `get_current_user`. Domain exceptions translate via the existing registered handlers (non-member → `404`, non-admin → `403`). Tests in `tests/test_router.py` (`AsyncClient` + session override): `test_list_audit_200_for_member`, `test_list_audit_404_for_non_member`, `test_list_audit_self_approved_query_filter`, `test_global_audit_200_for_admin`, `test_global_audit_403_for_non_admin`, `test_audit_unauthenticated_401`, `test_audit_pagination_cursor`.

7. **[S]** Wire the audit router in `app/main.py` (`include_router`). No change to error handlers (existing `ForbiddenError`/`NotFoundError` handlers cover the new statuses).

8. **[M]** Add `app/features/audit/verify.py` — the `verify-chain` CLI (`python -m app.features.audit.verify`). Opens a DB session, streams `iter_chain_ordered`, recomputes each `entry_hash` via `hashing.compute_entry_hash`, asserts `prev_hash` linkage and gap-free `seq`. Prints `audit chain OK — N entries verified` + exit `0` on success; on the first break, prints the offending `seq`/`id`/expected-vs-actual hash and the failure kind (content-tamper | broken-link | seq-gap | reorder) and exits `1`. Tests in `tests/test_verify.py`: `test_verify_clean_chain_exit_zero`, `test_verify_detects_field_tamper`, `test_verify_detects_deleted_middle_row` (seq gap / broken link), `test_verify_detects_reordered_rows`, `test_verify_empty_chain_ok`. **[Risky — this is the §14 tamper-detection guarantee.]**

9. **[M]** Wire the live callers (each its own small commit), calling `audit.service.record` **after** the originating state change commits (Open Question 1 resolved: same transaction):
   - `auth.router` → `login` on success (`actor_user_id=user.id`), `logout` (`actor_user_id=session user`), and `login_failed` on `AuthenticationError` (`actor_user_id=NULL`, `payload={username}`, committed in its own transaction then the error re-raised). Tests `test_login_writes_audit_entry`, `test_logout_writes_audit_entry`, `test_failed_login_writes_audit_entry`.
   - `mcp.service.execute_tool_run` → `tool_run` at run-row creation (both sync and async paths), attributed (`actor_user_id=user_id`), `payload={server, tool, target, status}`, `target_type="tool_run"`, `target_id=tool_run_id`; and `tool_run_completed` on the **sync path only** (after `update_tool_run_result`), `payload={..., status, exit_code}`. **Async/background completion is deferred** (Open Question 3, refined): `ToolRun` has no user-attribution column and the background `_stream_to_channel` persists terminal status at ~10 points without the `user_id`; wiring it is a larger, riskier change in fragile kill/timeout/server-down handlers, out of scope for this step-gated audit-chain slice. The seam: thread `user_id` into `_stream_to_channel` and emit `tool_run_completed` at the terminal `update_tool_run_result` chokepoint in a focused follow-up. Tests `test_tool_run_writes_invocation_entry`, `test_sync_tool_run_writes_completion_entry`.
   - `graph.service` create/update/delete node & edge → `record(action=graph_*_*, engagement_id, actor_user_id=user_id, target_type, target_id)`, emitted in the request session alongside `_push_undo` (the Slice-09 seam) so it commits with the undo row after the writer has committed the entity. Tests `test_create_node_writes_audit_entry`, `test_update_node_writes_audit_entry`, `test_delete_edge_writes_audit_entry`, and `test_one_audit_entry_per_graph_mutation` (no double-count, incl. via the Slice 09 undo-apply path).

10. **[S]** **Reserved-seam documentation (no emission).** Add a module docstring + short comment block in `audit/service.py` documenting the two un-wired callers for downstream slices: Slice 16 calls `record(action=approval_granted|approval_rejected, self_approved=...)`; Slice 11+ calls `record(action=ai_call, ...)`. Do NOT import or depend on those (non-existent) features. Reference the Slice 09 audit seam (its `push_undo_entry`/`pop_undo_stack` chokepoints) and note that graph-edit emission in task 9 covers undo-applied writes.

11. **[S]** Add the `verify-audit` target to the root `Makefile` (`make verify-audit` → runs the CLI inside the backend container/venv). Confirm `make migrate` runs the new migration cleanly against a fresh DB and `make verify-audit` reports OK on the freshly-seeded (empty) chain.

12. **[S]** Add Alembic migration for `audit_entries` + `audit_chain_head` (seed the single head row) via the `write-alembic-migration` skill.

### Frontend tasks

Numbering continues from the backend tasks. The audit log is primarily an admin/forensic surface; the frontend in this slice is a minimal read-only viewer so the recorded data is demonstrable end-to-end. A richer UI (and the timeline scrubber) is Slice 33/35.

13. **[S]** Run `make generate-api` to regenerate types into `frontend/src/shared/api/`; commit the updated `frontend/openapi.json` snapshot (adds `AuditAction`, `AuditEntry`, `AuditPage`).

14. **[M]** Add `frontend/src/features/audit/api.ts` — `useEngagementAudit(engagementId, filters)` (`GET /api/v1/audit`) and `useGlobalAudit(filters)` (`GET /api/v1/audit/global`) TanStack Query hooks with an `auditKeys` factory and cursor-based pagination. Tests in `__tests__/api.test.tsx`: builds the query string incl. `self_approved`; paginates via `next_cursor`; surfaces `404`/`403` as errors.

15. **[M]** Add `frontend/src/features/audit/components/AuditLogTable.tsx` + test — a read-only table (shadcn) of entries showing `seq`, `created_at`, `action`, actor, target, and a `self_approved` indicator, with an action filter and a `self_approved` toggle (§5.2 "filter for cross-member vs self-approvals"). Test: renders rows, applies the action filter, toggles `self_approved`, shows a "load more" when `next_cursor` is present.

16. **[S]** Wire the `AuditLogTable` behind an admin-gated route/panel in the workspace (e.g. an "Audit" tab reachable by admins for the open engagement). Hidden for non-admins. Test asserts the entry point is admin-gated.

## Test plan

- **Unit — backend** (coverage ≥ 80% on `app/features/audit/`):
  - Hashing (pure, `tests/test_hashing.py`): `test_hash_is_deterministic`, `test_hash_changes_when_action_changes`, `test_hash_changes_when_actor_changes`, `test_hash_changes_when_payload_changes`, `test_payload_key_order_does_not_change_hash`, `test_genesis_prev_hash_is_zero`, `test_writer_and_verifier_use_same_hash` (round-trip: append then recompute matches).
  - Repository (real async test DB): `test_append_assigns_sequential_seq`, `test_append_links_prev_hash`, `test_genesis_uses_zero_prev_hash`, `test_concurrent_appends_serialize_no_fork`, `test_list_for_engagement_newest_first_paginates`, `test_list_global_filters_by_action`, `test_append_only_no_update_delete`.
  - Service (mock/real repo): `test_record_emits_entry_with_attribution`, `test_record_self_approved_passthrough`, `test_record_login_has_null_engagement`, `test_list_engagement_non_member_404`, `test_list_global_non_admin_403`, `test_list_engagement_self_approved_filter`.
  - Router (`AsyncClient`): `test_list_audit_200_for_member`, `test_list_audit_404_for_non_member`, `test_global_audit_200_for_admin`, `test_global_audit_403_for_non_admin`, `test_audit_unauthenticated_401`, `test_audit_self_approved_query_filter`, `test_audit_pagination_cursor`.
  - Verifier (`tests/test_verify.py`): `test_verify_clean_chain_exit_zero`, `test_verify_detects_field_tamper`, `test_verify_detects_deleted_middle_row`, `test_verify_detects_reordered_rows`, `test_verify_empty_chain_ok`.
  - Caller wiring: `test_login_writes_audit_entry`, `test_tool_run_writes_audit_entry`, `test_create_node_writes_audit_entry`, `test_update_node_writes_audit_entry`, `test_delete_edge_writes_audit_entry`, `test_one_audit_entry_per_graph_mutation`.
- **Unit — frontend** (coverage ≥ 60% on `src/features/audit/`):
  - `api.test.tsx`: query-string assembly incl. `self_approved`; cursor pagination; error surfacing.
  - `AuditLogTable.test.tsx`: rows render, action filter, `self_approved` toggle, load-more.
- **Integration** (`@pytest.mark.integration`, real Postgres):
  - `test_audit_chain_intact_after_mixed_actions` — log in, run a sandbox tool, create + edit + delete a node via the routers; then `verify.run()` returns OK and the chain length matches the action count. **Headline §14 happy-path.**
  - `test_audit_chain_detects_tampering` — append several entries, mutate one row's `payload` directly in SQL, assert `verify.run()` reports the exact `seq` and exits non-zero. **Headline §14 tamper-evidence test.**
  - `test_audit_chain_detects_row_deletion` — delete a middle row in SQL; verifier reports a broken link / seq gap.
  - `test_concurrent_appends_no_fork` — fire N concurrent `record()` calls; assert contiguous `seq` 1..N and a verifiable chain.
- **E2E** (Playwright) — `audit-log.spec.ts`: log in as admin, perform a graph edit, open the Audit tab, see the new entry with the actor and action; assert the `self_approved` column header exists (values populated by Slice 16).

## Acceptance criteria

- `make test` passes (ruff + mypy + eslint + tsc + pytest + vitest + playwright); coverage gates hold (≥80% backend audit feature, ≥60% frontend audit feature).
- `make migrate` runs the new `audit_entries` + `audit_chain_head` migration cleanly against a fresh Postgres container, seeding the single head row.
- `make verify-audit` exits `0` and prints `audit chain OK — N entries verified` on an intact chain.
- `make dev` brings up the stack; manually:
  1. Log in; run a sandbox tool; create, edit, and delete a graph node. Run `make verify-audit` → OK, count reflects the actions performed.
  2. In `psql`, tamper with one `audit_entries` row (alter `payload` or `actor_user_id`). Re-run `make verify-audit` → it prints the offending `seq`/`id` and exits non-zero.
  3. Delete a middle `audit_entries` row in `psql`. Re-run `make verify-audit` → broken-link / seq-gap reported, non-zero exit.
  4. As an admin, open the Audit tab for the engagement → see entries newest-first with actor, action, target; toggle the `self_approved` filter (no approval rows yet — Slice 16 — but the control + column exist).
  5. As a non-admin member, the global audit endpoint returns `403`; the engagement audit returns only that member's engagements' entries; a non-member gets `404`.
- `gh pr view` shows green CI.
- The §14 tamper-evidence guarantee is demonstrated by `test_audit_chain_detects_tampering` + `test_audit_chain_detects_row_deletion` (automated) and manual steps 2–3.

## Risks

- **Risk 1 — Chain forking under concurrency (load-bearing).** Two concurrent appends reading the same head would assign duplicate `seq`/`prev_hash` and fork the chain, silently defeating tamper-evidence. Mitigation: serialize appends via `SELECT ... FOR UPDATE` on the single-row `audit_chain_head` inside the insert transaction; `seq` and `entry_hash` are UNIQUE so a fork also hard-fails at the DB. Guarded by `test_concurrent_appends_serialize_no_fork` (unit) and `test_concurrent_appends_no_fork` (integration). **Reviewer must confirm the lock is held across compute+insert+head-update.**
- **Risk 2 — Writer/verifier hash drift.** If the verifier serializes fields even slightly differently from the writer (field order, timestamp precision, JSON whitespace, NULL encoding), every entry looks "tampered". Mitigation: a single pure `compute_entry_hash` used by both, with a round-trip test (`test_writer_and_verifier_use_same_hash`) and explicit canonicalization tests. **Reviewer focus.**
- **Risk 3 — Recording failure swallowing or breaking the primary action.** If `record()` raises and the caller is naive, either an action proceeds without an audit entry (audit gap) or a user-facing action 500s because of an audit write. Decide and document the policy (see Open Questions): preferred default is to record in the **same transaction** as the action so they commit/rollback atomically (no gap, no orphan), accepting that an audit-write failure fails the action. Tested by the caller-wiring tests.
- **Risk 4 — Provenance temptation.** The §14 "graph edit with attribution" requirement tempts adding `created_by` to `graph_nodes`. Forbidden (§8.2 / §17.4 / CLAUDE.md). Mitigation: attribution lives only in `audit_entries`; the migration adds no columns to existing tables. **Reviewer confirms.**
- **Risk 5 — Forensic record erased by cascade.** `ON DELETE CASCADE` from users/engagements would let deleting a user or engagement erase its audit trail — defeating §17.4 (source of truth). Mitigation: `ON DELETE RESTRICT` on `actor_user_id` and `engagement_id`. (Trade-off: deleting a user/engagement now requires handling its audit rows; see Open Questions.)
- **Risk 6 — Background/async tool runs and emission timing.** `execute_tool_run` has sync, async, and background-completion paths (Slice 04/05/27). Emitting at the wrong point could miss a run or double-count. Mitigation: emit once at run-row creation (a tool *invocation* is the audited event); completion-status audit is a §14 follow-up. Documented in task 9 and Open Questions.
- **Risk 7 — Append throughput / lock contention.** The single-row head lock serializes all appends. For a 2–5 person team this is negligible, but a burst of graph edits + tool runs could queue briefly. Acceptable for v1; noted so a future slice can revisit if needed. No mitigation beyond documenting the trade-off.
- **Risk 8 — Audit data exposure.** Audit payloads may contain sensitive target detail. Mitigation: engagement-scoped reads require membership; global reads require admin; no public/unauthenticated path; no write/edit/delete API. Reviewer confirms the read authorization matches §17.1.

## Open questions for the human — RESOLVED 2026-06-05

All four resolved by the human before implementation. Choices recorded here so the
code-reviewer and security-reviewer check the code against the decided design.

1. **Recording transaction policy (Risk 3).** **DECIDED: same DB transaction (atomic).**
   `record()` writes the audit row in the same transaction as the originating action so
   they commit/roll back together — no silent gaps; an audit-write failure fails the
   action. (For graph mutations the entity is committed by the single writer in its own
   session; the audit row is committed in the request session alongside the Slice-09 undo
   push — the closest atomic analogue. See Design note.)
2. **FK on-delete policy (Risk 5).** **DECIDED: no enforced FK; immutable, hashed columns.**
   The user chose `SET NULL` semantics for deletability, which conflicts with hashing the
   actor/engagement (nulling a hashed column would itself break the chain — see the
   contradiction note below). Resolved by dropping the FK entirely: `actor_user_id` and
   `engagement_id` are plain nullable UUID columns **with no `REFERENCES`**, stored as
   immutable denormalized values that **stay inside the hash**. Deleting a user/engagement
   never touches (or breaks) the audit row — the row keeps a now-dangling id, which is
   exactly what a forensic source-of-truth log should do (§17.4). Rewriting `actor_user_id`
   in SQL is still caught by the verifier (it is hashed). The demo and acceptance criteria
   (rewrite `actor_user_id` → caught) are preserved unchanged.

   > **Why not `SET NULL` + FK:** a column cannot be both tamper-protected (hashed,
   > immutable) and nulled-on-delete (mutated by the FK action). `SET NULL` on a hashed
   > column makes a legitimate delete look like tampering; excluding the column from the
   > hash to fix that would make rewriting `actor_user_id` undetectable, contradicting the
   > demo + acceptance step 2. Dropping the FK keeps both deletability and tamper-evidence.
3. **Tool-run emission point (Risk 6).** **DECIDED: invocation AND completion.** Emit
   `tool_run` when the run row is created (both sync and async paths) and `tool_run_completed`
   when the terminal status is set (after `update_tool_run_result` on the sync path; in the
   `_stream_to_channel` completion handling on the async/background path). The completion
   entry carries the final `status` and `exit_code` in its payload.
4. **Auth events.** **DECIDED: login + logout + failed-login.** This slice audits successful
   `login`, `logout`, and `login_failed` (the last with `actor_user_id=NULL` and the
   attempted `username` in the payload — useful for security forensics).

## Security review required?

**Yes — this is a step-gated, risky slice (audit log integrity), per CLAUDE.md and PROJECT_PLAN's risky-slice summary (slice 10).** The security-reviewer subagent is required at finish-slice time. The reviewer must confirm:

- (a) **the chain cannot fork under concurrency** — the head lock is held across hash-compute + insert + head-update; `seq` and `entry_hash` are UNIQUE; `test_concurrent_appends_*` pass (Risk 1);
- (b) **writer and verifier hash identically** — one shared pure `compute_entry_hash`, round-trip tested; canonicalization is deterministic (Risk 2);
- (c) **the verifier detects field tampering, row deletion, and reordering** — `test_verify_detects_*` and the integration tamper tests pass (§14);
- (d) **the log is append-only** — no update/delete repository method or HTTP endpoint exists; the table has no soft-delete / `updated_at` (Risk 3);
- (e) **no provenance columns are added to entity tables** — attribution lives only in `audit_entries`; the migration touches no `graph_*`/`findings`/entity table (§8.2 / §17.4 / Risk 4);
- (f) **forensic durability** — `ON DELETE RESTRICT` (or the agreed policy) keeps audit rows from being cascade-erased (Risk 5);
- (g) **read authorization** — engagement reads require membership (`404` for non-members, §17.1), global reads require admin (`403` otherwise), no unauthenticated path (Risk 8);
- (h) **`self_approved` plumbing** — the column/schema/filter correctly carry §5.2 semantics (NULL except on approval actions; the Slice 16 seam is documented and un-wired).

## Progress

(The stop-checkpoint hook and compact-handoff skill append here. Leave empty at planning time.)
- 2026-06-04T22:43:22Z — 0246cfc docs(slice-10): add slice spec, mark in-progress
- 2026-06-04T22:43:51Z — 0246cfc docs(slice-10): add slice spec, mark in-progress

### 2026-06-05 — implementation complete (all 16 tasks)

All backend (1–12) + frontend (13–16) tasks implemented, committed per-task, full `make
lint` green, backend 776 tests (audit feature 99% cov), frontend 419 tests (audit 87–100%
cov), 4 real-Postgres integration tests pass (incl. concurrent no-fork). Migration
upgrade/downgrade/upgrade verified on real Postgres; `make verify-audit` returns OK on the
empty chain. Manual real-Postgres round-trip confirmed: clean chain verifies, rewriting
`actor_user_id` is caught (content-tamper).

**Resolved design decisions (override the spec defaults where noted):**
1. record() runs in the **same transaction** as the action (atomic).
2. **No FK** on `actor_user_id`/`engagement_id` — immutable, hashed, denormalized (the
   user's `SET NULL` intent, reconciled to keep tamper-evidence of attribution).
3. Tool runs: `tool_run` invocation on **both** paths (attributed) + `tool_run_completed`
   on the **sync path only**; async/background completion **deferred** (ToolRun has no
   attribution column; ~10 terminal sites in `_stream_to_channel`) — documented seam.
4. Auth: `login` + `logout` + `login_failed` all audited.

Graph: audited at the `_push_undo` chokepoint (all 5 ordinary mutators, one entry each)
AND at `pop_undo_stack` for undo-applied inverses (mapped via `_UNDO_AUDIT_ACTION`) — no
double-count (the inverse bypasses the mutators).

**Remaining:** finish-slice (full gate + code-reviewer + **required security-reviewer** +
PR). Not yet run.
