# Slice 19: Findings model + lifecycle

**Branch**: `slice-19-findings-model`
**GitHub Issue**: #53
**Status**: in-progress
**Risky**: no

---

## Goal

Let an engagement member create a finding with a Simple severity (Critical/High/Medium/Low/Info), attach it to a graph entity, and advance it through its verification (`unverified`/`verified`/`false_positive`) and remediation (`open`/`fixed`/`risk_accepted`) lifecycle.

## User-visible demo

After this slice is merged:

- Log in as an engagement member and open an engagement.
- `POST /api/v1/engagements/{id}/findings` with `{ "title": "Reflected XSS on /search", "severity": "high", "description": "...", "node_id": "<uuid>" }` returns `201` with the created finding, its `id`, and default lifecycle states (`verification_status: "unverified"`, `remediation_status: "open"`).
- `GET .../findings` returns the engagement's live findings (newest-first), each with severity, verification status, remediation status, and the linked graph node (if any).
- `GET .../findings/{finding_id}` returns one finding's full detail.
- `PATCH .../findings/{finding_id}` updates the title/description/severity/node link.
- `PATCH .../findings/{finding_id}/verification` with `{ "verification_status": "verified" }` advances verification; `{ "verification_status": "false_positive" }` marks it a false positive.
- `PATCH .../findings/{finding_id}/remediation` with `{ "remediation_status": "fixed" }` (or `"risk_accepted"`) advances remediation — usable later by the retest workflow (§9.2).
- `DELETE .../findings/{finding_id}` soft-deletes the finding; it disappears from the default list but stays in `GET .../findings?include_deleted=true`.
- In the workspace, a new "Findings" tab lists findings with severity badges and two status pickers (verification + remediation); "New finding" opens a dialog (title, severity select, description, optional node link); editing a finding and flipping its statuses persists across a page refresh.
- Every create/update/status-change/delete writes an attributed entry to the hash-chained audit log (§14), visible to admins.
- Writes against an `archived` engagement are rejected (`409`); reads still work. Writes against an engagement you are not a member of return `404`.

## Out of scope

- Does NOT implement the advanced classification panel: CVSS v3.1/v4.0 vector strings, OWASP Risk Rating (likelihood × impact), or MITRE ATT&CK tagging (**Slice 20**, §9.1). The data model is left **extensible** for these (see Data model changes → `Forward-compatibility`) but no advanced columns, schemas, endpoints, or UI ship here.
- Does NOT implement AI-proposed severities or AI-proposed findings (§9.1 AI behavior — later AI/findings slices). A finding is created/edited only by a human in this slice.
- Does NOT implement deduplication: AI duplicate-flagging, user merge, or delegated dedup autonomy (**Slice 21**, §9.2). No `merged_into`/dedup fields here.
- Does NOT implement attack paths as findings or the attack-path link surface (**Slice 22**, §9.3). A finding may link to a node that happens to be an `attack_path` node, but no path-specific behavior.
- Does NOT implement Burp scanner-finding import (**Slice 30**, §6.4). The findings API is import-agnostic so that slice plugs in later.
- Does NOT implement evidence attachment / screenshots / "flag for report inclusion" (§11.4, §12 evidence rules) beyond a free-form `description`. Evidence and the report-inclusion flag are owned by later slices (§12 / Slice 34).
- Does NOT implement report rendering of findings (**Slice 34**, §12).
- Does NOT promote findings into the NetworkX graph as `vulnerability` nodes, nor route finding writes through the single-writer process. Findings are their own feature table that **references** a graph node by FK; they are not graph entities (see Decision 1).
- Does NOT add a personal-undo-stack integration for findings (the §8.2 personal undo stack is graph-scoped, Slice 09). Findings get soft-delete + history but not the 20-deep per-user stack.

## Requirements traceability

- **§9.1 — Primary classification (Simple)**: a finding carries a `severity` enum `critical | high | medium | low | info`. This is the UI default and what the report will render (Slice 34). The advanced classifications (CVSS, OWASP Risk, ATT&CK) are explicitly deferred to Slice 20; this slice ships only the Simple severity, and the human sets/overrides it (no AI proposal yet).
- **§9.2 — Verification status**: every finding carries `verification_status` ∈ `{ unverified, verified, false_positive }`, defaulting to `unverified` on create.
- **§9.2 — Remediation status**: every finding carries `remediation_status` ∈ `{ open, fixed, risk_accepted }`, defaulting to `open` on create. The note "(updatable for retest workflows)" is honored by exposing a dedicated remediation-update endpoint that remains usable after archive→retest (the field itself is mutable; the *engagement* read-only guard still applies while it is archived — see Decision 3).
- **§8.1 / §8.2 — Findings attach to graph entities**: §8.1 lists "Vulnerabilities (CVEs and findings)" and "Notes and manual findings" among node types; a finding here links to a `GraphNode` via a nullable FK so a finding about a host/service/url is anchored in the shared graph. The link is optional (a finding can exist before its node is mapped). **No provenance columns** on the finding (§8.2 — "no provenance attribution"; the audit log is the source of truth).
- **§8.2 — History / soft-delete**: findings get soft-delete with append-only pre-mutation history, mirroring the graph's `*_history` pattern, so a finding can be recovered (the §8.2 "any node, edge, or finding can be reverted to a prior state" clause names findings explicitly).
- **§4 — Archived engagements are read-only**: finding writes against an `archived` engagement return `409`; reads remain available.
- **§14 — Audit log**: every finding create, update, status change, and soft-delete records an attributed, hash-chained audit entry (the audit vocabulary gains `FINDING_*` actions, mirroring how the graph feature emits `GRAPH_*`).
- **§17.1 — Engagement isolation**: every endpoint resolves the engagement through the membership chokepoint (`engagements.repository.get_engagement_for_member`); non-members get `404` (existence not revealed), matching Slices 01/07.
- **§17.4 — Audit log is the source of truth**: no `created_by`/`updated_by` on the finding row; attribution lives in the audit log.

## Contract

OpenAPI delta. All endpoints require the `cookieAuth` session and engagement membership; non-members receive `404`.

```yaml
openapi: "3.1.0"
info:
  title: Adeptus API — Slice 19 delta
  version: "0.19.0"

paths:
  /api/v1/engagements/{engagement_id}/findings:
    get:
      operationId: list_findings
      summary: List the engagement's findings (newest-first)
      security: [{ cookieAuth: [] }]
      parameters:
        - { name: engagement_id, in: path, required: true, schema: { type: string, format: uuid } }
        - { name: include_deleted, in: query, required: false, schema: { type: boolean, default: false } }
      responses:
        "200":
          content:
            application/json:
              schema: { $ref: "#/components/schemas/FindingList" }
        "401": { description: Not authenticated }
        "404": { description: Engagement not found or caller not a member }
    post:
      operationId: create_finding
      summary: Create a finding with a Simple severity (defaults unverified/open)
      security: [{ cookieAuth: [] }]
      parameters:
        - { name: engagement_id, in: path, required: true, schema: { type: string, format: uuid } }
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: "#/components/schemas/FindingCreate" }
      responses:
        "201":
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Finding" }
        "401": { description: Not authenticated }
        "404": { description: Engagement not found, caller not a member, or node_id not found in this engagement }
        "409": { description: Engagement is archived (read-only) }
        "422": { description: Validation error (bad severity, empty title, oversized description) }

  /api/v1/engagements/{engagement_id}/findings/{finding_id}:
    get:
      operationId: get_finding
      summary: Get a single finding's detail
      security: [{ cookieAuth: [] }]
      parameters:
        - { name: engagement_id, in: path, required: true, schema: { type: string, format: uuid } }
        - { name: finding_id, in: path, required: true, schema: { type: string, format: uuid } }
      responses:
        "200":
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Finding" }
        "401": { description: Not authenticated }
        "404": { description: Engagement/finding not found or caller not a member }
    patch:
      operationId: update_finding
      summary: Update a finding's title, description, severity, and/or node link
      security: [{ cookieAuth: [] }]
      parameters:
        - { name: engagement_id, in: path, required: true, schema: { type: string, format: uuid } }
        - { name: finding_id, in: path, required: true, schema: { type: string, format: uuid } }
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: "#/components/schemas/FindingUpdate" }
      responses:
        "200":
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Finding" }
        "401": { description: Not authenticated }
        "404": { description: Engagement/finding not found, caller not a member, or node_id not found }
        "409": { description: Engagement is archived (read-only) }
        "422": { description: Validation error }
    delete:
      operationId: delete_finding
      summary: Soft-delete a finding (recoverable via history)
      security: [{ cookieAuth: [] }]
      parameters:
        - { name: engagement_id, in: path, required: true, schema: { type: string, format: uuid } }
        - { name: finding_id, in: path, required: true, schema: { type: string, format: uuid } }
      responses:
        "204": { description: Finding soft-deleted }
        "401": { description: Not authenticated }
        "404": { description: Engagement/finding not found or caller not a member }
        "409": { description: Engagement is archived (read-only) }

  /api/v1/engagements/{engagement_id}/findings/{finding_id}/verification:
    patch:
      operationId: set_finding_verification
      summary: Set verification status (unverified | verified | false_positive)
      security: [{ cookieAuth: [] }]
      parameters:
        - { name: engagement_id, in: path, required: true, schema: { type: string, format: uuid } }
        - { name: finding_id, in: path, required: true, schema: { type: string, format: uuid } }
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: "#/components/schemas/VerificationUpdate" }
      responses:
        "200":
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Finding" }
        "401": { description: Not authenticated }
        "404": { description: Engagement/finding not found or caller not a member }
        "409": { description: Engagement is archived (read-only) }
        "422": { description: Validation error (invalid status value) }

  /api/v1/engagements/{engagement_id}/findings/{finding_id}/remediation:
    patch:
      operationId: set_finding_remediation
      summary: Set remediation status (open | fixed | risk_accepted)
      security: [{ cookieAuth: [] }]
      parameters:
        - { name: engagement_id, in: path, required: true, schema: { type: string, format: uuid } }
        - { name: finding_id, in: path, required: true, schema: { type: string, format: uuid } }
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: "#/components/schemas/RemediationUpdate" }
      responses:
        "200":
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Finding" }
        "401": { description: Not authenticated }
        "404": { description: Engagement/finding not found or caller not a member }
        "409": { description: Engagement is archived (read-only) }
        "422": { description: Validation error (invalid status value) }

components:
  schemas:
    Severity:
      type: string
      enum: [critical, high, medium, low, info]

    VerificationStatus:
      type: string
      enum: [unverified, verified, false_positive]

    RemediationStatus:
      type: string
      enum: [open, fixed, risk_accepted]

    Finding:
      type: object
      required:
        [id, engagement_id, title, description, severity, verification_status,
         remediation_status, node_id, deleted, created_at, updated_at]
      properties:
        id: { type: string, format: uuid }
        engagement_id: { type: string, format: uuid }
        title: { type: string }
        description: { type: string }
        severity: { $ref: "#/components/schemas/Severity" }
        verification_status: { $ref: "#/components/schemas/VerificationStatus" }
        remediation_status: { $ref: "#/components/schemas/RemediationStatus" }
        node_id:
          type: [string, "null"]
          format: uuid
          description: "Optional link to a GraphNode (§8.1) this finding concerns."
        deleted: { type: boolean }
        created_at: { type: string, format: date-time }
        updated_at: { type: string, format: date-time }

    FindingCreate:
      type: object
      required: [title, severity]
      properties:
        title: { type: string, minLength: 1, maxLength: 512 }
        description: { type: string, maxLength: 65536, default: "" }
        severity: { $ref: "#/components/schemas/Severity" }
        node_id: { type: [string, "null"], format: uuid }

    FindingUpdate:
      type: object
      description: "At least one field must be present. node_id may be set to null to unlink."
      properties:
        title: { type: string, minLength: 1, maxLength: 512 }
        description: { type: string, maxLength: 65536 }
        severity: { $ref: "#/components/schemas/Severity" }
        node_id: { type: [string, "null"], format: uuid }

    VerificationUpdate:
      type: object
      required: [verification_status]
      properties:
        verification_status: { $ref: "#/components/schemas/VerificationStatus" }

    RemediationUpdate:
      type: object
      required: [remediation_status]
      properties:
        remediation_status: { $ref: "#/components/schemas/RemediationStatus" }

    FindingList:
      type: object
      required: [items]
      properties:
        items: { type: array, items: { $ref: "#/components/schemas/Finding" } }
```

Notes for the frontend type regen: the enum values are snake_case (`false_positive`, `risk_accepted`) on the wire to match the Pydantic `StrEnum` members; the UI maps them to display labels ("False positive", "Risk accepted"). `severity` on `FindingCreate` is **required** (no literal default) so it does not become an awkward forced field in the client; the UI always sends an explicit severity. (See project memory "OpenAPI literal-default → required TS field" — avoided by not giving `severity` a default.)

## Data model changes

Two new tables. The Alembic migration is written via the `write-alembic-migration` skill during implementation (per the Alembic-autogenerate gotcha: add the new feature models import to `backend/alembic/env.py` first; recreate the autogenerated file as the non-root user). One existing CHECK constraint widened (audit actions).

- `findings`:
  - `id` UUID PK (`gen_random_uuid()`)
  - `engagement_id` UUID NOT NULL REFERENCES `engagements(id)` ON DELETE CASCADE
  - `node_id` UUID **NULL** REFERENCES `graph_nodes(id)` ON DELETE SET NULL — optional link to the graph entity the finding concerns (§8.1/§8.2). `SET NULL` (not `CASCADE`): hard-deleting a node must not delete its findings; the finding outlives its node link.
  - `title` VARCHAR(512) NOT NULL
  - `description` TEXT NOT NULL DEFAULT `''`
  - `severity` VARCHAR(16) NOT NULL — CHECK IN (`'critical',''high',''medium','low','info'`)
  - `verification_status` VARCHAR(16) NOT NULL DEFAULT `'unverified'` — CHECK IN (`'unverified','verified','false_positive'`)
  - `remediation_status` VARCHAR(16) NOT NULL DEFAULT `'open'` — CHECK IN (`'open','fixed','risk_accepted'`)
  - `deleted` BOOLEAN NOT NULL DEFAULT `false` (soft-delete flag)
  - `created_at` TIMESTAMPTZ NOT NULL DEFAULT `now()`
  - `updated_at` TIMESTAMPTZ NOT NULL DEFAULT `now()` (`onupdate=func.now()`)
  - Index: `ix_findings_engagement_id` on `engagement_id`
  - Partial index: `ix_findings_engagement_live` on `(engagement_id)` WHERE `deleted = false` (fast live-list load; mirrors `ix_graph_nodes_engagement_live`)
  - Index: `ix_findings_node_id` on `node_id` (lookup "findings for this node" — used by Slice 22/34 and the per-node UI later)
  - **NO** `created_by`/`updated_by` columns (§8.2 / §17.4 no-provenance).
  - **Forward-compatibility (Slice 20, do NOT implement here):** advanced classifications will be added in Slice 20 as *additive* nullable columns/tables — e.g. `cvss_vector` / `cvss_score` (nullable), `owasp_likelihood` / `owasp_impact` (nullable), and a separate `finding_attack_techniques` join table for MITRE ATT&CK tags. This slice adds none of them; it just must not box them out (no NOT-NULL composite that would force a backfill, no enum that would need rewriting). The `severity` field stays the single primary classification (§9.1).

- `finding_history`: append-only pre-mutation snapshots of `findings` state, enabling recovery/per-entity revert (§8.2 "any ... finding can be reverted to a prior state"). One row is written **before** each mutation, capturing the state a revert would restore. No provenance columns — the audit log is the source of truth.
  - `id` UUID PK (`gen_random_uuid()`)
  - `engagement_id` UUID NOT NULL REFERENCES `engagements(id)` ON DELETE CASCADE
  - `finding_id` UUID NOT NULL REFERENCES `findings(id)` ON DELETE CASCADE
  - `title` VARCHAR(512) NOT NULL
  - `description` TEXT NOT NULL
  - `severity` VARCHAR(16) NOT NULL
  - `verification_status` VARCHAR(16) NOT NULL
  - `remediation_status` VARCHAR(16) NOT NULL
  - `node_id` UUID NULL
  - `deleted` BOOLEAN NOT NULL
  - `recorded_at` TIMESTAMPTZ NOT NULL DEFAULT `now()`
  - Index: `ix_finding_history_finding_id` on `(finding_id, recorded_at DESC)` (latest-prior lookup; mirrors `ix_graph_node_history_node_id`)
  - **Note:** this slice ships the history *table* and writes snapshots, but does NOT add a `/undo` endpoint for findings (resolved decision D2). A finding `/undo` is deferred because a non-authorship-aware revert would let one engagement member silently clobber another's edit — it must arrive later as an authorship-aware revert (Slice 09 pattern), which also feeds Slice 25 retest and Slice 33 replay. History persistence now makes that cheap. A short comment in `repository.py` documents this deferral and points at D2.

- `audit_entries` — **widen** the existing `ck_audit_entries_action` CHECK constraint to add the new `FINDING_*` actions (mirrors how Slice 18 added `autonomy_*`). The migration drops and recreates the CHECK with the expanded vocabulary; `AUDIT_ACTIONS` in `audit/models.py` and the `AuditAction` enum in `audit/schemas.py` gain the same members. New actions:
  - `finding_created`, `finding_updated`, `finding_verification_changed`, `finding_remediation_changed`, `finding_deleted`

  This is the only modification to existing tables/code outside the new feature folder, and it is a constrained, additive vocabulary change — see Security review.

## Tasks

Numbered continuously across the whole slice. Every commit subject cites its task id, e.g. `feat(slice-19): add findings models (task 1)`.

### Backend tasks

1. **[S]** Add the new audit actions to the audit vocabulary: append `finding_created`, `finding_updated`, `finding_verification_changed`, `finding_remediation_changed`, `finding_deleted` to `AUDIT_ACTIONS` in `backend/app/features/audit/models.py` AND the matching members to the `AuditAction` `StrEnum` in `backend/app/features/audit/schemas.py`. Keep them in sync (the existing `test_action_enum_matches_db_vocabulary` guards this). No new migration here yet — the CHECK-constraint widening rides in the slice migration (task 9). Add/extend a unit test asserting the enum/tuple parity.

2. **[S]** Add `backend/app/features/findings/__init__.py` and `backend/app/features/findings/models.py` — SQLAlchemy ORM models `Finding` and `FindingHistory` on the shared `Base` from `app.core.db`, with the columns, CHECK constraints, FKs, and indexes from "Data model changes". Use the `_PROPS_JSON`-style SQLite-variant pattern only if needed (no JSON columns here, so plain types). Add the models import to `backend/alembic/env.py` so autogenerate sees them.

3. **[S]** Add `backend/app/features/findings/schemas.py` — Pydantic v2: `Severity`, `VerificationStatus`, `RemediationStatus` (StrEnums with snake_case values), `FindingCreate`, `FindingUpdate` (at-least-one-field model validator; `node_id` may be explicitly `null`), `VerificationUpdate`, `RemediationUpdate`, `Finding` (`from_attributes=True`), `FindingList`. Cap `description` length (≤ 64 KB) via a validator. `FindingUpdate.node_id` uses a sentinel-or-`Optional` pattern so "omit = leave unchanged" is distinguishable from "set to null = unlink" (document the chosen approach in a docstring).

4. **[S]** Add `backend/app/features/findings/errors.py` — domain exceptions: `FindingNotFound(NotFoundError)`, reuse the engagement `EngagementNotFound`/`EngagementArchived` pattern. Confirm the `409`/`404` mappings already exist in `app/core/errors/handlers` (the graph feature registered `ConflictError`/`NotFoundError`); reuse them — do NOT widen `core/` (no ADR needed if reusing existing handlers).

5. **[M]** Add `backend/app/features/findings/repository.py` — async Postgres CRUD: `insert_finding`, `get_finding` (by id, engagement-scoped), `list_findings(engagement_id, include_deleted)`, `update_finding_row`, `set_verification`, `set_remediation`, `soft_delete_finding`, `record_finding_history` (pre-mutation snapshot), `node_exists_in_engagement(engagement_id, node_id)` (validates the FK target is a live node in the same engagement before linking). Tests in `tests/test_repository.py` against the real async session: insert/get/list, `include_deleted` filter, history-snapshot write, node-link validation, soft-delete hides from live list.

6. **[M]** Add `backend/app/features/findings/service.py` — orchestration + invariants, mirroring `graph/service.py`:
   - Membership chokepoint: every method first calls `engagements.repository.get_engagement_for_member`; `None` → `EngagementNotFound` (→404), no admin bypass (§17.1/§4).
   - Archived guard `_require_writable`: write paths raise `EngagementArchived` (→409) when `status == "archived"`; read paths skip it (§4).
   - `node_id` validation: on create/update with a non-null `node_id`, call `repo.node_exists_in_engagement`; missing/cross-engagement → `FindingNotFound`-style `404` (use a distinct message; do not reveal cross-engagement node existence — §17.1).
   - On every mutation: write a `FindingHistory` pre-state snapshot, then mutate, then emit the audit entry via `audit_service.record(... action=<FINDING_*>, actor_user_id=user_id, engagement_id=..., target_type="finding", target_id=str(finding_id))`, all committed atomically in the request session (the `_push_undo` pattern in `graph/service.py` is the template). Verification/remediation changes use their specific actions.
   - Findings do NOT go through the single-writer queue (Decision 1) — they are an ordinary feature table; document this in the module docstring and reference ADR-0001 to make the deliberate non-routing explicit.
   - Tests in `tests/test_service.py` (mock repo + audit): membership 404, archived 409, node-link 404, default statuses on create, each status transition emits its mapped audit action, soft-delete writes history + audit.

7. **[M]** Add `backend/app/features/findings/router.py` — endpoints per the contract, depending on `get_current_user` (`app.features.auth.deps`). Translate domain exceptions via the registered handlers. Tests in `tests/test_router.py` with `httpx.AsyncClient` + session override: 201/200/204/401/404/409/422 across create, list, get, update, verification, remediation, delete; assert default statuses on create; assert `include_deleted` behavior.

8. **[S]** Wire `findings_router` in `backend/app/main.py` (`app.include_router(findings_router)`).

9. **[S]** Add the Alembic migration via the `write-alembic-migration` skill: creates `findings` + `finding_history` and **widens** `ck_audit_entries_action` (drop + recreate with the five new `finding_*` actions). Confirm `make migrate` runs cleanly against a fresh DB and that the audit hash-chain still verifies after the constraint change (the constraint is the only audit change; existing rows are untouched).

### Frontend tasks

10. **[S]** Run `make generate-api` to regenerate types into `frontend/src/shared/api/`; commit the updated `frontend/openapi.json` snapshot. Verify the new `finding_*` audit actions did not break any existing audit-typed consumer (whole-project tsc gate — see project memory).

11. **[M]** Add `frontend/src/features/findings/api.ts` — TanStack Query hooks: `useFindings(engagementId, includeDeleted?)`, `useFinding(engagementId, findingId)`, `useCreateFinding`, `useUpdateFinding`, `useSetVerification`, `useSetRemediation`, `useDeleteFinding`. Each mutation invalidates `useFindings` (and `useFinding` where relevant) on success. Add a small `findingsLabels.ts` mapping wire enum values → display labels (e.g. `false_positive` → "False positive").

12. **[M]** Add `frontend/src/features/findings/components/FindingsList.tsx` + test — a table of live findings: severity badge (color-coded by Simple severity), title, verification-status pill, remediation-status pill, linked-node label (or "—"), edit/delete buttons. Empty state: "No findings yet — add one." Loading skeleton.

13. **[M]** Add `frontend/src/features/findings/components/FindingDialog.tsx` + test — shadcn `Dialog` used for both create and edit: title input, severity `Select`, description textarea, optional node link (a node picker fed by the existing `useGraph` hook, with a "none" option). Submits via the relevant mutation; shows 422 inline; closes on success.

14. **[S]** Add `frontend/src/features/findings/components/StatusPickers.tsx` + test — two inline `Select`s (verification, remediation) on each row/detail that call `useSetVerification` / `useSetRemediation`; optimistic-ish UI is optional, but on error revert and surface a toast.

15. **[S]** Wire a "Findings" tab into the workspace shell (`/engagements/:id/workspace`) rendering `FindingsList` + a "New finding" button (opens `FindingDialog`). Sits alongside the existing Graph tab.

## Test plan

- **Unit — backend** (coverage ≥ 80% on `app/features/findings/`):
  - Repository (real async test DB): `test_insert_and_get_finding`, `test_list_excludes_deleted_by_default`, `test_list_includes_deleted_when_requested`, `test_history_snapshot_records_prestate`, `test_node_link_validation_rejects_cross_engagement_node`, `test_soft_delete_hides_from_live_list`, `test_node_set_null_on_node_hard_delete` (FK `ON DELETE SET NULL`).
  - Service (mocked repo + audit): `test_create_defaults_unverified_open`, `test_create_non_member_404`, `test_create_archived_409`, `test_create_with_unknown_node_404`, `test_update_emits_finding_updated_audit`, `test_set_verification_verified_emits_audit`, `test_set_verification_false_positive`, `test_set_remediation_risk_accepted_emits_audit`, `test_delete_writes_history_and_finding_deleted_audit`, `test_read_archived_engagement_allowed`.
  - Audit parity: `test_finding_actions_in_enum_and_db_vocabulary` (extends the existing parity guard).
  - Router (`AsyncClient`): `test_create_finding_201`, `test_list_findings_200`, `test_get_finding_200`, `test_update_finding_200`, `test_set_verification_200`, `test_set_remediation_200`, `test_delete_finding_204_and_hidden`, `test_create_unauthenticated_401`, `test_create_non_member_404`, `test_create_archived_409`, `test_create_bad_severity_422`, `test_verification_bad_value_422`.
- **Unit — frontend** (coverage ≥ 60% on `src/features/findings/`):
  - `FindingsList.test.tsx` — renders findings with severity/status pills, empty state, loading skeleton.
  - `FindingDialog.test.tsx` — create submit, edit pre-fill, node-link select (incl. "none"), 422 inline error, close on success.
  - `StatusPickers.test.tsx` — changing verification/remediation fires the mutation; error reverts the control.
- **Integration** (`@pytest.mark.integration`, real Postgres):
  - `test_finding_lifecycle_roundtrip` — create (unverified/open) → verify → mark remediation fixed → soft-delete (hidden) → re-list with `include_deleted` (present); assert one audit entry per mutation with correct actions and a still-valid hash-chain (`verify-chain`).
  - `test_finding_linked_to_graph_node` — create a graph node, create a finding linked to it, hard-delete the node, assert the finding's `node_id` becomes null and the finding survives.
- **E2E** (Playwright) — one journey: `findings.spec.ts` — log in, open an engagement's Findings tab, add a finding (severity High), flip verification to Verified, flip remediation to Fixed, delete it (disappears).

## Acceptance criteria

- `make test` passes (lint + mypy + tsc + pytest + vitest + playwright).
- `make migrate` runs the new migration cleanly against a fresh Postgres container; `make test-backend` confirms the audit chain still verifies after the CHECK-constraint widening.
- `make dev` brings up the stack; manually:
  1. Log in; open an engagement; open the Findings tab (empty state).
  2. Click "New finding"; enter a title, pick severity High, write a description, optionally link a graph node; create — it appears with `Unverified` / `Open` pills.
  3. Flip the verification picker to `Verified`, then to `False positive` — both persist across a page refresh.
  4. Flip the remediation picker to `Fixed`, then `Risk accepted` — persists across refresh.
  5. Edit the finding's title/severity/node link — change persists.
  6. Delete the finding — it disappears from the list; (DB or `include_deleted=true` API) confirms the row is soft-deleted, not hard-deleted, and a `finding_history` snapshot exists.
  7. As an admin, open the audit log — five attributed entries (create, verification change, remediation change, edit, delete) are present and the chain verifies.
  8. Archive the engagement (DB flip / settings if available) — finding writes return `409`; the list still loads.
- `gh pr view` shows green CI.

## Risks

- **Risk 1 — Findings vs. the single-writer graph (Decision 1).** It is tempting to model a finding as a `vulnerability` graph node so it flows through the single writer. That would conflate two concerns: findings have their own lifecycle (verification/remediation) that graph nodes do not, and routing them through the writer queue adds latency and coupling for no invariant benefit (there is no cross-finding consistency requirement the writer protects). Mitigation: findings are a separate table that *references* a node by FK; the module docstring states this explicitly and cites ADR-0001 so a reviewer sees the deliberate non-routing. CLAUDE.md anti-pattern "Don't write to the graph outside the single-writer process" is not violated because findings are not graph entities and never touch `graph_nodes`/`graph_edges` rows.
- **Risk 2 — Audit vocabulary change touches a shared, hash-chained table.** Widening `ck_audit_entries_action` and adding enum members is a constrained additive change, but the audit log is tamper-evident (§14) and listed as step-gated in CLAUDE.md. Mitigation: the migration only drops+recreates the CHECK constraint (no row rewrites, no hash recomputation), keep `AUDIT_ACTIONS`/`AuditAction` in lockstep (guarded by the parity test), and an integration test re-runs `verify-chain` after the migration to prove the chain is intact. No change to the hashing logic or content fields.
- **Risk 3 — `node_id` FK delete semantics.** `ON DELETE CASCADE` would silently destroy findings when a node is hard-deleted (e.g. by an engagement-internal cleanup); that loses pentest data. Mitigation: `ON DELETE SET NULL` so the finding survives node deletion (the finding's text still documents the issue); `test_node_set_null_on_node_hard_delete` guards it. Note nodes are normally *soft*-deleted, so this is an edge case but must be correct.
- **Risk 4 — `node_id` unlink vs. omit ambiguity in PATCH.** A naive `Optional[UUID] = None` cannot tell "leave the link alone" from "remove the link". Mitigation: use a sentinel/`model_fields_set` approach in `FindingUpdate` (documented in task 3) so `PATCH {}` with `node_id` absent leaves it unchanged while `PATCH {"node_id": null}` unlinks; covered by router tests.
- **Risk 5 — Extensibility boxing-out for Slice 20.** A wrong primary-key or NOT-NULL choice now could force a painful backfill when CVSS/OWASP/ATT&CK arrive. Mitigation: keep `severity` the only required classification; Slice 20 additions are nullable columns + a join table (documented in Data model changes). No composite uniqueness or NOT-NULL that would block additive evolution.
- **Risk 6 — Whole-project type gate on the OpenAPI regen.** New `finding_*` audit-action enum values flow into the generated client; any exhaustive `switch` over audit actions on the frontend would fail tsc (project memory: whole-project type gates). Mitigation: task 10 verifies existing audit consumers compile; if an exhaustive switch exists, extend it in the same commit.

## Resolved decisions

Decided by the human on 2026-06-06 ("choose the answer that is best for the user, regardless of implementation"). All three land on the planner's defaults, but were re-derived from end-user value:

- **D1 — Structural (separate table + FK, not a graph node): CONFIRMED.** Findings stay a feature table that references a `GraphNode` by FK (see Decision 1 / Risk 1). User-value rationale: (a) findings survive graph cleanup — `ON DELETE SET NULL` means deleting a stale host node never vaporizes a documented vulnerability; (b) a finding's verification/remediation lifecycle stays decoupled from graph soft-delete / per-entity undo, avoiding conflation of "revert a finding's status" with "undo a graph edit"; (c) attack-path inclusion (Slice 22) and @-mention pinning still work through the FK without findings being nodes.
- **D2 — No finding `/undo` endpoint in this slice: CONFIRMED (defer).** History snapshots are still persisted. Rationale is stronger than "undo-later is cheap": a naïve per-finding undo is *collaboration-hostile* — Adeptus engagements are multi-user, and Slice 09 deliberately guarantees undo never reverts a teammate's work. A crude `/findings/{id}/undo` that rolls back to the last history snapshot regardless of author would let one member silently clobber another's verification, which is strictly worse for the team than no undo. Finding revert must arrive later as an **authorship-aware** revert following the Slice 09 pattern (and it feeds Slice 25 retest + Slice 33 replay). Nothing is lost in the meantime because history is recorded now.
- **D3 — Free lifecycle transitions (no enforced state machine): CONFIRMED.** Any verification/remediation value may be set directly (e.g. `verified → unverified`, `fixed → open`). User-value rationale: pentest work is non-linear and the product's own **Slice 25 retest** workflow requires reopening a finding marked `fixed` when a retest proves it still exploitable — a forward-only state machine would block that. §9.2 mandates no directed lifecycle.

## Security review required?

**Yes — narrowly, for the audit touch.** This slice modifies the hash-chained audit log's action vocabulary (§14 — listed as step-gated in CLAUDE.md: "hash-chain audit"). The reviewer must confirm: (a) the `ck_audit_entries_action` widening is the only audit-table change and performs no row rewrites or hash recomputation; (b) `AUDIT_ACTIONS` and `AuditAction` stay in lockstep (parity test); (c) `verify-chain` still passes after the migration; (d) every finding mutation emits exactly one attributed audit entry with the correct action, committed atomically with the mutation; and (e) the engagement-isolation `404` posture (membership chokepoint, no cross-engagement node-link disclosure) holds on every endpoint. The findings feature itself does not touch auth, MCP, single-writer, RAG isolation, egress, secrets, or approvals.

## Progress

(The stop-checkpoint hook and compact-handoff skill append here. Leave empty at planning time.)
- 2026-06-06T19:06:35Z — 03fa514 Slice 18: Delegation pattern — standing autonomy (#52)
- 2026-06-06T19:17:20Z — 03fa514 Slice 18: Delegation pattern — standing autonomy (#52)
