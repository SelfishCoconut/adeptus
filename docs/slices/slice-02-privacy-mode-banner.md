# Slice 02: Privacy mode + persistent banner

**Branch**: `slice-02-privacy-mode-banner`
**GitHub Issue**: #9
**Status**: in-progress
**Risky**: no

---

## Goal

Add a `privacy_mode` field to each engagement and display a persistent, always-visible banner inside the workspace that shows the current engagement's privacy posture.

## User-visible demo

- Open "New Engagement" dialog: there is now a "Privacy Mode" toggle defaulting to "Local only (strict)". Flip it to "Cloud enabled" and complete creation.
- Navigate into the workspace for that engagement: a coloured banner in the header reads "Cloud enabled — data may leave the local network".
- Open a second engagement created with default settings: the banner reads "Local only — no data leaves the local network".
- Switching between engagement workspaces swaps the banner text and colour immediately.
- Call `GET /api/v1/engagements/{id}`: the response now includes `"privacy_mode": "local_only"` or `"cloud_enabled"`.
- Call `PATCH /api/v1/engagements/{id}` (owner only): change `privacy_mode` on an existing engagement; the workspace banner updates within TanStack Query's next poll.

## Out of scope

- Does NOT enforce the local-only constraint on actual LLM calls (that's slice 11 / 14).
- Does NOT implement the pattern-friction egress modal (slice 14).
- Does NOT implement the "AI is offline" banner (slice 11).
- Does NOT add token cost tracking display (slice 36).
- Does NOT restrict who can flip privacy mode beyond the owner-only PATCH guard already established in §5.1.
- Does NOT implement engagement archiving or read-only enforcement.

## Requirements traceability

- §5.1 — Per-engagement privacy toggle; strict local-only default; cloud must be explicitly enabled by an admin at engagement creation or in engagement settings. This slice stores the field and gates mutation on the owner role.
- §5.5 — Persistent visual indicator: a banner shows the current engagement's privacy mode at all times.
- §17.5 — Privacy posture is visible at all times and safe by default; strict local-only is the default.

## Contract

### New / changed endpoints

```yaml
components:
  schemas:
    PrivacyMode:
      type: string
      enum: [local_only, cloud_enabled]
      description: >
        local_only — all LLM calls stay on the local Ollama instance.
        cloud_enabled — Claude API calls are permitted (egress friction applies when cloud calls are made, slice 14).

    EngagementCreate:
      # extends existing schema — adds one optional field
      properties:
        privacy_mode:
          $ref: '#/components/schemas/PrivacyMode'
          default: local_only

    EngagementDetail:
      # adds one field to the existing response
      properties:
        privacy_mode:
          $ref: '#/components/schemas/PrivacyMode'

    EngagementSummary:
      # adds one field (needed so the engagement list can show mode without fetching each detail)
      properties:
        privacy_mode:
          $ref: '#/components/schemas/PrivacyMode'

    EngagementUpdate:
      # new schema — PATCH body; all fields optional
      type: object
      properties:
        privacy_mode:
          $ref: '#/components/schemas/PrivacyMode'

paths:
  /api/v1/engagements/{engagement_id}:
    patch:
      summary: Update engagement settings (owner only)
      operationId: update_engagement
      parameters:
        - in: path
          name: engagement_id
          required: true
          schema:
            type: string
            format: uuid
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/EngagementUpdate'
      responses:
        '200':
          description: Updated engagement detail
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/EngagementDetail'
        '403':
          description: Caller is not the engagement owner
        '404':
          description: Engagement not found or caller is not a member
```

### TypeScript types (will be regenerated from OpenAPI — listed here for clarity)

```typescript
// Generated into frontend/src/shared/api/
export type PrivacyMode = 'local_only' | 'cloud_enabled'

// EngagementCreate gains an optional field
export interface EngagementCreate {
  name: string
  scope: string
  client_info: string | null
  privacy_mode?: PrivacyMode   // defaults to 'local_only' on the server
}

// EngagementDetail gains a required field
export interface EngagementDetail {
  id: string
  name: string
  status: 'active' | 'archived'
  scope: string
  client_info: string | null
  created_at: string
  updated_at: string
  member_role: 'owner' | 'member'
  privacy_mode: PrivacyMode    // NEW
}

// EngagementSummary gains a required field
export interface EngagementSummary {
  id: string
  name: string
  status: 'active' | 'archived'
  created_at: string
  member_role: 'owner' | 'member'
  privacy_mode: PrivacyMode    // NEW
}

// New update schema
export interface EngagementUpdate {
  privacy_mode?: PrivacyMode
}
```

## Data model changes

Add one column to `engagements`, no new tables.

```
engagements table — ADD column:
  privacy_mode  VARCHAR(16)  NOT NULL
                DEFAULT 'local_only'
                CHECK privacy_mode IN ('local_only', 'cloud_enabled')
```

Migration sketch (to be written by `write-alembic-migration` skill):

```python
# upgrade()
op.add_column(
    "engagements",
    sa.Column(
        "privacy_mode",
        sa.String(length=16),
        server_default=sa.text("'local_only'"),
        nullable=False,
    ),
)
op.create_check_constraint(
    "ck_engagements_privacy_mode",
    "engagements",
    "privacy_mode IN ('local_only', 'cloud_enabled')",
)

# downgrade()
op.drop_constraint("ck_engagements_privacy_mode", "engagements", type_="check")
op.drop_column("engagements", "privacy_mode")
```

No new indexes needed: privacy_mode is never filtered in a WHERE clause in this slice.

## Backend tasks

Tasks are ordered; each is independently testable before the next begins.

1. **[S] Update `models.py`** — add `privacy_mode: Mapped[str]` column to `Engagement` with `server_default=text("'local_only'")` and a `CheckConstraint`. No relationship changes.

2. **[S] Update `schemas.py`** — add `PrivacyMode = Literal["local_only", "cloud_enabled"]`; extend `EngagementCreate` with `privacy_mode: PrivacyMode = "local_only"`; extend `EngagementDetail` and `EngagementSummary` with `privacy_mode: PrivacyMode`; add new `EngagementUpdate(BaseModel)` with `privacy_mode: PrivacyMode | None = None`.

3. **[S] Update `repository.py`** — pass `privacy_mode` through `create_engagement()`; add `update_engagement(db, engagement_id, privacy_mode)` that updates the row and returns the updated `Engagement` object. Cover new paths in `tests/test_repository.py`.

4. **[M] Update `service.py`** — thread `privacy_mode` into `create_engagement()` and into both `EngagementDetail` / `EngagementSummary` construction in `list_engagements()` and `get_engagement()`; add `update_engagement(db, caller, engagement_id, data: EngagementUpdate) -> EngagementDetail` that enforces owner-only (raises `ForbiddenError` for non-owners, `NotFoundError` for non-members per §17.1 isolation posture). Cover new paths in `tests/test_service.py`.

5. **[M] Update `router.py`** — add `PATCH /api/v1/engagements/{engagement_id}` with `operation_id="update_engagement"`, returning `EngagementDetail`; thread `privacy_mode` through `POST /api/v1/engagements` (already passes `body` to service). Cover in `tests/test_router.py`.

6. **[S] Alembic migration** — invoke `write-alembic-migration` skill to produce `slice-02-add-privacy-mode-to-engagements`. The migration adds the column with `server_default='local_only'` so existing rows are non-null immediately; no backfill needed.

7. **[S] Run `make generate-api`** — dump updated OpenAPI spec to `frontend/openapi.json` and regenerate types in `frontend/src/shared/api/`. Commit the snapshot so CI needs no live backend.

## Frontend tasks

Tasks are ordered; each is independently testable.

1. **[S] Regenerate OpenAPI client** — `make generate-api` produces updated types (done in backend task 7 above; this task is the frontend-side verification pass: confirm `PrivacyMode`, `EngagementUpdate`, and the new fields appear in `frontend/src/shared/api/`).

2. **[M] Add `useUpdateEngagement` mutation to `features/engagements/api.ts`** — `PATCH /api/v1/engagements/{engagement_id}`, invalidates `engagementKey(id)` on success. Add `api.test.tsx` test for the new hook (mock `api.PATCH`; assert query invalidation).

3. **[M] Add `PrivacyModeBadge` component (`features/engagements/components/PrivacyModeBadge.tsx`)** — pure display component; accepts `privacyMode: PrivacyMode`; renders a coloured pill:
   - `local_only` → green background, text "Local only — no data leaves the local network", shield icon (lucide `ShieldCheck`).
   - `cloud_enabled` → amber background, text "Cloud enabled — data may leave the local network", cloud icon (lucide `Cloud`).
   Write `PrivacyModeBadge.test.tsx` asserting correct text and ARIA role (`status`) for each mode.

4. **[M] Add `PrivacyModeBanner` component (`features/engagements/components/PrivacyModeBanner.tsx`)** — thin wrapper that accepts `privacyMode: PrivacyMode` and renders a full-width bar above the 3-pane grid using `PrivacyModeBadge` inside it. Must be visually distinct from the top `<header>` (use a thin coloured strip, `role="status"`, `aria-live="polite"`). Write `PrivacyModeBanner.test.tsx`.

5. **[M] Wire `PrivacyModeBanner` into `WorkspaceShell`** — extend `WorkspaceShellProps` with `privacyMode: PrivacyMode`; render `<PrivacyModeBanner>` between the header and the 3-pane grid. Update `WorkspaceShell.test.tsx` to cover both modes.

6. **[M] Pass `privacyMode` from `EngagementWorkspacePage`** — the page already calls `useEngagement(engagementId)`; extract `engagement.data?.privacy_mode` and pass it to `<WorkspaceShell>`. While the engagement query is loading, pass `'local_only'` as the safe default (safe-by-default per §17.5). Update `EngagementWorkspacePage.test.tsx`.

7. **[M] Add privacy mode toggle to `NewEngagementDialog`** — add a `Switch` (shadcn/ui) labelled "Cloud LLM enabled" with a short description "Allow Claude API calls for this engagement. Off by default (strict local-only).". Default unchecked. Map checked → `cloud_enabled`, unchecked → `local_only` in the `EngagementCreate` body. Update `NewEngagementDialog.test.tsx` to assert the field is present and defaults to unchecked, and that a form submission with it checked sends `privacy_mode: 'cloud_enabled'`.

8. **[S] (Optional, owned by owner role only) Add inline privacy mode toggle in the workspace** — inside `EngagementWorkspacePage`, when `callerRole === 'owner'`, render a small `Switch` next to the banner ("Enable cloud LLM") that fires `useUpdateEngagement`. Non-owners see the banner read-only. Add a test for the owner/non-owner conditional. Mark **[S]** because the mutation hook (task 2) already does the heavy lifting.

## Test plan

### Unit — backend (`app/features/engagements/`)

Coverage gate: ≥80% on `app/features/engagements/`.

| Test name | Layer | What it checks |
|---|---|---|
| `test_create_engagement_default_privacy_mode` | service | `privacy_mode` defaults to `local_only` when not supplied |
| `test_create_engagement_cloud_enabled` | service | `privacy_mode=cloud_enabled` round-trips correctly |
| `test_get_engagement_returns_privacy_mode` | service | `EngagementDetail.privacy_mode` is present |
| `test_list_engagements_returns_privacy_mode` | service | `EngagementSummary.privacy_mode` is present for each row |
| `test_update_engagement_owner_changes_mode` | service | Owner can flip `local_only` → `cloud_enabled` |
| `test_update_engagement_non_owner_forbidden` | service | Member (non-owner) raises `ForbiddenError` |
| `test_update_engagement_non_member_not_found` | service | Non-member raises `NotFoundError` (§17.1 isolation) |
| `test_repo_create_with_privacy_mode` | repository | Column persists correct value |
| `test_repo_update_privacy_mode` | repository | `update_engagement` updates column |
| `test_patch_engagement_200_owner` | router | `PATCH` by owner returns 200 + updated `EngagementDetail` |
| `test_patch_engagement_403_member` | router | `PATCH` by non-owner member returns 403 |
| `test_patch_engagement_404_non_member` | router | `PATCH` by non-member returns 404 |
| `test_post_engagement_includes_privacy_mode` | router | `POST` response includes `privacy_mode` |

### Unit — frontend (`src/features/engagements/`)

Coverage gate: ≥60% on `src/features/engagements/`.

| Test name | File | What it checks |
|---|---|---|
| `renders local_only badge with correct text` | `PrivacyModeBadge.test.tsx` | Text + `role="status"` |
| `renders cloud_enabled badge with correct text` | `PrivacyModeBadge.test.tsx` | Text + amber variant |
| `renders full-width banner for local_only` | `PrivacyModeBanner.test.tsx` | `aria-live="polite"` present |
| `renders full-width banner for cloud_enabled` | `PrivacyModeBanner.test.tsx` | Correct child content |
| `WorkspaceShell renders banner with passed privacyMode` | `WorkspaceShell.test.tsx` | Banner appears between header and grid |
| `EngagementWorkspacePage passes privacyMode to shell` | `EngagementWorkspacePage.test.tsx` | Shell receives `local_only` by default during loading |
| `NewEngagementDialog toggle defaults to unchecked` | `NewEngagementDialog.test.tsx` | Switch off → `local_only` in submit body |
| `NewEngagementDialog toggle on sends cloud_enabled` | `NewEngagementDialog.test.tsx` | Switch on → `cloud_enabled` in submit body |
| `owner sees inline toggle in workspace` | `EngagementWorkspacePage.test.tsx` | Switch rendered when `callerRole === 'owner'` |
| `non-owner cannot see inline toggle` | `EngagementWorkspacePage.test.tsx` | Switch absent when `callerRole === 'member'` |
| `useUpdateEngagement invalidates engagementKey on success` | `api.test.tsx` | QueryClient invalidation called |

### Integration

One happy-path integration test in `test_integration.py` (uses real DB via test compose):

- Create engagement with `privacy_mode=cloud_enabled`, retrieve it, assert the field round-trips. PATCH it back to `local_only`, assert updated response.

### E2E (Playwright)

One new E2E scenario appended to the existing Playwright journey (`frontend/playwright/`):

- **`privacy-banner.spec.ts`**: log in as admin, create engagement with cloud enabled, navigate to workspace, assert banner text contains "Cloud enabled". Create a second engagement (default), navigate to its workspace, assert banner text contains "Local only".

## Acceptance criteria

1. `make test` passes with no new failures and coverage gates met (≥80% backend features, ≥60% frontend features).
2. `make lint` passes (ruff, mypy, eslint, tsc --noEmit) with no new errors.
3. `make migrate` applies the new migration cleanly; `alembic downgrade -1` reverts it without error.
4. Manual demo via `make dev`:
   - Create engagement via UI with "Cloud LLM enabled" toggle on → workspace shows amber "Cloud enabled" banner.
   - Create engagement via UI with toggle off (default) → workspace shows green "Local only" banner.
   - As owner, flip the inline toggle inside the workspace → banner updates without page reload.
   - As a non-owner member, the inline toggle is absent; banner is read-only.
5. `make generate-api` produces an updated `frontend/openapi.json` with `PrivacyMode` enum and `EngagementUpdate` schema; the regenerated types are committed.
6. `gh pr view` shows green CI.

## Risks

- **`NewEngagementDialog` form diverges from OpenAPI**: the dialog currently hand-builds the `EngagementCreate` body. After this slice adds `privacy_mode`, the generated `EngagementCreate` type must be respected. Mitigation: TypeScript strict mode + the `make generate-api` step will cause a compile error at the call site if the shape diverges.
- **WorkspaceShell prop threading**: `WorkspaceShell` currently accepts no engagement-level data; adding `privacyMode` is a non-breaking extension, but the existing `WorkspaceShell.test.tsx` must be updated or it will fail to compile. Mitigation: update the test as part of frontend task 5.
- **Banner flash during engagement load**: while `useEngagement` is loading, passing `local_only` as a safe default will briefly show the local-only banner even for cloud-enabled engagements. This is intentional (fail-safe per §17.5) but should be noted. A loading skeleton is out of scope; the safe default is the correct UX posture.
- **Alembic `server_default` vs existing rows**: the migration uses `server_default` so all pre-existing `engagements` rows immediately read as `local_only` without an explicit backfill. This is correct and safe — existing engagements started before cloud was a configurable option.

## Open questions for the human

1. **Who may enable cloud LLM — any owner, or admins only?** §5.1 says "explicitly enabled by an admin at engagement creation or in engagement settings." This slice has been scoped to allow *any* owner to flip the setting (consistent with the owner-only PATCH guard). If the intent is to restrict cloud enablement to admin-role users only, the service layer needs an additional `caller.role == "admin"` check on the PATCH path, and the UI toggle in `NewEngagementDialog` and the workspace should only appear for admins. Please confirm the intended guard before implementation.

2. **Should `EngagementSummary` (the list response) include `privacy_mode`?** The spec was written to include it so the engagement list page could eventually show a mode indicator without a per-engagement fetch. If the list page will not show the mode in this or any near-future slice, the field can be omitted from `EngagementSummary` to keep the list payload lean. Please confirm.

## Security review required?

No — this slice touches neither auth, MCP, audit log, single-writer graph, RAG isolation, egress enforcement, secrets storage, nor approval flows. It stores and displays a metadata field. The actual enforcement of the local-only constraint (blocking cloud LLM calls) is deferred to slice 14, which is flagged risky and will require security review at that time.

## Progress

(The stop-checkpoint hook and compact-handoff skill append here. Leave empty at planning time.)
- 2026-05-31T17:57:00Z — cc56ebc Merge pull request #7 from SelfishCoconut/slice-01-engagement-crud-membership
