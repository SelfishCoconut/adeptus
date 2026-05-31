# Slice 01: Engagement CRUD + Membership

**Branch**: `slice-01-engagement-crud-membership`
**GitHub Issue**: #6
**Status**: planned
**Risky**: no

---

## Goal

Allow an authenticated user to create an engagement, invite other registered users as members, and list only the engagements they belong to — so the workspace has real engagements to navigate into.

## User-visible demo

- Log in as admin. Click "New Engagement" in the top bar (or an engagements landing page).
- Fill the creation form: name ("ACME Web Assessment"), scope ("192.168.1.0/24, acme.example.com"), client info (free text), and click "Create". The new engagement appears in the list.
- From the engagement's settings panel, type a username into the "Invite member" field and click "Invite". The invited user now appears in the members list with role `member`.
- Log out. Log back in as the invited user (seeded from `ADEPTUS_TEST_USER` env var — see Backend task 8). See only the engagement they were invited to in their list.
- Log in as a third user who was not invited. See an empty engagements list.
- Navigate to `GET /api/v1/engagements` as an unauthenticated request — receive `401`.
- Navigate directly to `GET /api/v1/engagements/{id}` for an engagement you are not a member of — receive `404` (not `403` — membership is not revealed).

## Out of scope

- Does NOT implement privacy mode or the privacy banner (Slice 02).
- Does NOT implement engagement archiving / the `Archived` state transition (referenced in §4, deferred to a later slice).
- Does NOT implement the AI chat panel, graph visualisation, or tool console (Slices 07–11+).
- Does NOT implement engagement deletion (admin-only, deferred to a later slice).
- Does NOT implement per-engagement Docker networking or proxy configuration (§6.1, deferred to Slice 03+).
- Does NOT implement the retest workflow (§4, deferred to Slice 25).
- Does NOT implement admin user-management UI (users are created via the admin API or future slice, not this slice).
- Does NOT implement the audit log for engagement events (Slice 10).
- Does NOT navigate into the workspace for a specific engagement — the workspace shell already exists from Slice 00; routing into `/engagements/:id/workspace` is wired here only as a stub link.

## Requirements traceability

- §4 — Engagement lifecycle: creation wizard (name, scope, client info), `Active` state. Archiving and retest workflow deferred.
- §3 — Membership: explicit invite per user per engagement; users only see engagements they are members of. Admin and User roles from §3 are the user roles; within an engagement, roles are `owner` (creator) and `member` (invited). §3 does not restrict engagement creation to admins — "admins create users" is the admin-specific privilege; any authenticated user may create an engagement.
- §17.1 — Engagement isolation: a user with no membership must not learn of the engagement's existence (hence `404` not `403`).

## Contract

```yaml
openapi: "3.1.0"
info:
  title: Adeptus API — Slice 01 delta
  version: "0.2.0"

paths:
  /api/v1/engagements:
    get:
      operationId: list_engagements
      summary: List engagements the caller is a member of
      security:
        - cookieAuth: []
      responses:
        "200":
          description: Engagements the caller belongs to (may be empty)
          content:
            application/json:
              schema:
                type: array
                items:
                  $ref: "#/components/schemas/EngagementSummary"
        "401":
          description: Not authenticated

    post:
      operationId: create_engagement
      summary: Create a new engagement (caller becomes owner; any authenticated user may call this)
      security:
        - cookieAuth: []
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/EngagementCreate"
      responses:
        "201":
          description: Engagement created
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/EngagementDetail"
        "401":
          description: Not authenticated
        "422":
          description: Validation error

  /api/v1/engagements/{engagement_id}:
    get:
      operationId: get_engagement
      summary: Get a single engagement (caller must be a member)
      security:
        - cookieAuth: []
      parameters:
        - name: engagement_id
          in: path
          required: true
          schema:
            type: string
            format: uuid
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/EngagementDetail"
        "401":
          description: Not authenticated
        "404":
          description: Not found or not a member (membership not revealed)

  /api/v1/engagements/{engagement_id}/members:
    get:
      operationId: list_members
      summary: List members of an engagement (caller must be a member)
      security:
        - cookieAuth: []
      parameters:
        - name: engagement_id
          in: path
          required: true
          schema:
            type: string
            format: uuid
      responses:
        "200":
          content:
            application/json:
              schema:
                type: array
                items:
                  $ref: "#/components/schemas/MemberEntry"
        "401":
          description: Not authenticated
        "404":
          description: Not found or not a member

    post:
      operationId: add_member
      summary: Invite a user to the engagement (owner only)
      security:
        - cookieAuth: []
      parameters:
        - name: engagement_id
          in: path
          required: true
          schema:
            type: string
            format: uuid
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/AddMemberRequest"
      responses:
        "201":
          description: Member added
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/MemberEntry"
        "401":
          description: Not authenticated
        "403":
          description: Caller is not the engagement owner
        "404":
          description: Engagement not found or not a member; or username not found
        "409":
          description: User is already a member

  /api/v1/engagements/{engagement_id}/members/{user_id}:
    delete:
      operationId: remove_member
      summary: Remove a member from the engagement (owner only; owner cannot remove themselves)
      security:
        - cookieAuth: []
      parameters:
        - name: engagement_id
          in: path
          required: true
          schema:
            type: string
            format: uuid
        - name: user_id
          in: path
          required: true
          schema:
            type: string
            format: uuid
      responses:
        "204":
          description: Member removed
        "400":
          description: Owner cannot remove themselves
        "401":
          description: Not authenticated
        "403":
          description: Caller is not the engagement owner
        "404":
          description: Engagement or member not found

components:
  schemas:
    EngagementCreate:
      type: object
      required: [name, scope]
      properties:
        name:
          type: string
          minLength: 1
          maxLength: 128
        scope:
          type: string
          description: "Free-text scope: IPs, CIDR ranges, domains, one per line"
          maxLength: 4096
        client_info:
          type: string
          maxLength: 1024
          nullable: true

    EngagementSummary:
      type: object
      required: [id, name, status, created_at, member_role]
      properties:
        id:
          type: string
          format: uuid
        name:
          type: string
        status:
          type: string
          enum: [active, archived]
        created_at:
          type: string
          format: date-time
        member_role:
          type: string
          enum: [owner, member]

    EngagementDetail:
      type: object
      required: [id, name, status, scope, client_info, created_at, member_role]
      properties:
        id:
          type: string
          format: uuid
        name:
          type: string
        status:
          type: string
          enum: [active, archived]
        scope:
          type: string
        client_info:
          type: string
          nullable: true
        created_at:
          type: string
          format: date-time
        updated_at:
          type: string
          format: date-time
        member_role:
          type: string
          enum: [owner, member]

    MemberEntry:
      type: object
      required: [user_id, username, role, joined_at]
      properties:
        user_id:
          type: string
          format: uuid
        username:
          type: string
        role:
          type: string
          enum: [owner, member]
        joined_at:
          type: string
          format: date-time

    AddMemberRequest:
      type: object
      required: [username]
      properties:
        username:
          type: string
          description: Username of the existing user to invite
```

## Data model changes

Two new tables. No existing tables modified.

- `engagements` table:
  - `id` UUID primary key (`gen_random_uuid()`)
  - `name` VARCHAR(128) NOT NULL
  - `scope` TEXT NOT NULL
  - `client_info` TEXT NULL
  - `status` VARCHAR(16) NOT NULL DEFAULT `'active'` — CHECK IN (`'active'`, `'archived'`)
  - `created_at` TIMESTAMPTZ NOT NULL DEFAULT `now()`
  - `updated_at` TIMESTAMPTZ NOT NULL DEFAULT `now()`
  - Index: `ix_engagements_status` on `status` (supports future archive filtering)

- `engagement_members` table:
  - `engagement_id` UUID NOT NULL REFERENCES `engagements(id)` ON DELETE CASCADE
  - `user_id` UUID NOT NULL REFERENCES `users(id)` ON DELETE CASCADE
  - `role` VARCHAR(16) NOT NULL DEFAULT `'member'` — CHECK IN (`'owner'`, `'member'`)
  - `joined_at` TIMESTAMPTZ NOT NULL DEFAULT `now()`
  - PRIMARY KEY (`engagement_id`, `user_id`)
  - Index: `ix_engagement_members_user_id` on `user_id` (supports "list my engagements" query)

The Alembic migration is written via the `write-alembic-migration` skill during implementation, not here.

## Backend tasks

Ordered. Each independently testable.

1. **[S]** Add `backend/app/features/engagements/models.py` — SQLAlchemy ORM models for `Engagement` and `EngagementMember`, using the same `Base` from `app.core.db`. Add a `members` relationship on `Engagement` (lazy="raise") and an `engagement` back-reference on `EngagementMember`.

2. **[S]** Add `backend/app/features/engagements/schemas.py` — Pydantic v2 models: `EngagementCreate`, `EngagementSummary`, `EngagementDetail`, `MemberEntry`, `AddMemberRequest` — matching the OpenAPI contract above. Use `model_config = ConfigDict(from_attributes=True)` for ORM compatibility.

3. **[M]** Add `backend/app/features/engagements/repository.py` — async methods:
   - `create_engagement(db, name, scope, client_info, owner_id) -> Engagement` — inserts both the engagement row and the owner `EngagementMember` row in one transaction.
   - `get_engagement_for_member(db, engagement_id, user_id) -> Engagement | None` — JOIN on `engagement_members`; returns None if not found or user is not a member.
   - `list_engagements_for_user(db, user_id) -> list[tuple[Engagement, str]]` — returns engagement + caller's role.
   - `get_members(db, engagement_id) -> list[tuple[EngagementMember, str]]` — returns members + username via JOIN to `users`.
   - `get_member(db, engagement_id, user_id) -> EngagementMember | None`
   - `add_member(db, engagement_id, user_id) -> EngagementMember`
   - `remove_member(db, engagement_id, user_id) -> None`
   - Tests in `tests/test_repository.py` using a test Postgres session (reuse `conftest.py` pattern from `features/conftest.py`).

4. **[M]** Add `backend/app/features/engagements/service.py` — business logic:
   - `create_engagement(db, caller, data: EngagementCreate) -> EngagementDetail` — calls repository; no role restriction: **any authenticated user may create an engagement** (§3 restricts admin-only to user management, not engagement creation). The caller automatically becomes the owner.
   - `get_engagement(db, caller, engagement_id) -> EngagementDetail` — calls `get_engagement_for_member`; raises `NotFoundError` if None (membership not revealed).
   - `list_engagements(db, caller) -> list[EngagementSummary]`
   - `list_members(db, caller, engagement_id) -> list[MemberEntry]` — verifies caller is a member first (raises `NotFoundError` if not).
   - `add_member(db, caller, engagement_id, username) -> MemberEntry` — verifies caller is owner (raises `ForbiddenError`); resolves username via `auth.repository.get_user_by_username` (raises `NotFoundError` if username unknown); raises `ConflictError` if already a member.
   - `remove_member(db, caller, engagement_id, user_id) -> None` — verifies caller is owner; raises `BadRequestError` if `user_id == owner_id`.
   - Add `ConflictError` and `BadRequestError` to `app/core/errors/` and register HTTP mappings (`409`, `400`) if not already present.
   - Tests in `tests/test_service.py` — mock the repository; cover all error paths.

5. **[M]** Add `backend/app/features/engagements/router.py` — five endpoints as per the contract. All require `get_current_user` dependency from `app.features.auth.deps`. Translate service exceptions to HTTP via the registered error handlers. Tests in `tests/test_router.py` using `httpx.AsyncClient` with session override; cover 201, 200, 401, 403, 404, 409.

6. **[S]** Wire the new router in `backend/app/main.py` — `app.include_router(engagements_router, prefix="/api/v1")`.

7. **[S]** Write Alembic migration for `engagements` and `engagement_members` tables via the `write-alembic-migration` skill. Confirm `make migrate` runs cleanly.

8. **[S]** Extend the `lifespan` startup seeder in `backend/app/main.py` (or wherever Slice 00 placed it) to seed a second user from `ADEPTUS_TEST_USER` env var — **DEVELOPMENT / TEST environments only**.
   - The guard MUST check `settings.ENVIRONMENT in ("development", "test")` (or equivalent). If `ENVIRONMENT=production`, this block must be completely skipped — no code path should seed `ADEPTUS_TEST_USER` in production.
   - `ADEPTUS_TEST_USER` format mirrors the existing `ADEPTUS_ADMIN_USER` env var (JSON blob or separate `_USERNAME` / `_PASSWORD` vars — match whatever Slice 00 established).
   - If the username already exists (idempotent re-seed), skip silently.
   - The seeded user has role `user` (not admin).
   - Add a comment in the code block: `# DEV/TEST ONLY — never runs when ENVIRONMENT=production`.
   - Document the two env vars (`ADEPTUS_TEST_USER_USERNAME`, `ADEPTUS_TEST_USER_PASSWORD`) in `.env.example` with a note that they are ignored in production.
   - Tests: one unit test asserting the seeder does NOT run when `ENVIRONMENT=production`.

## Frontend tasks

Ordered.

1. **[S]** Run `make generate-api` after the backend contract is finalised to regenerate types into `frontend/src/shared/api/`. Commit the updated `frontend/openapi.json` snapshot.

2. **[M]** Add `frontend/src/features/engagements/api.ts` — TanStack Query hooks:
   - `useEngagements()` — `GET /api/v1/engagements`; cached; stale-time 30 s.
   - `useEngagement(id)` — `GET /api/v1/engagements/:id`.
   - `useCreateEngagement()` — mutation; on success, invalidates `useEngagements`.
   - `useMembers(engagementId)` — `GET /api/v1/engagements/:id/members`.
   - `useAddMember(engagementId)` — mutation; on success, invalidates `useMembers`.
   - `useRemoveMember(engagementId)` — mutation; on success, invalidates `useMembers`.

3. **[M]** Add `frontend/src/features/engagements/components/EngagementList.tsx` — renders a list of `EngagementSummary` cards. Each card shows name, status badge, member role badge, and a "Open" link. Empty state: "No engagements — create one." Shows a loading skeleton while the query is fetching. Test (`EngagementList.test.tsx`): renders cards from mock data; renders empty state; shows skeleton while loading.

4. **[M]** Add `frontend/src/features/engagements/components/NewEngagementDialog.tsx` — a shadcn/ui `Dialog` wrapping a form with fields: Name (required), Scope (textarea, required), Client Info (textarea, optional). Submit calls `useCreateEngagement`; on success closes the dialog and the list refreshes. Shows inline field errors from the 422 response. Test (`NewEngagementDialog.test.tsx`): renders form fields; submits and calls mutation; shows error on 422; closes on success.

5. **[M]** Add `frontend/src/features/engagements/components/MembersList.tsx` + `InviteMemberForm.tsx` — lists current members with role labels; owner-only controls show "Remove" buttons; `InviteMemberForm` has a username text input and "Invite" button that calls `useAddMember`. Tests: list renders members; invite form calls mutation; non-owner sees no invite or remove controls.

6. **[S]** Add `frontend/src/features/engagements/pages/EngagementsPage.tsx` — top-level page: "Adeptus" heading, "New Engagement" button (opens `NewEngagementDialog`), `EngagementList`. Add route `/engagements` in `frontend/src/app/` router, protected by `ProtectedRoute`. Redirect `/workspace` (current root redirect from Slice 00) to `/engagements` if the user has no engagements open, or keep `/workspace` as the 3-pane shell accessible from a list card. The simplest approach: `/engagements` is the post-login landing page; clicking "Open" on a card navigates to `/engagements/:id/workspace` (stub — renders the existing `WorkspaceShell`).

7. **[S]** Update `frontend/src/app/` routing: post-login redirect goes to `/engagements` instead of `/workspace`. The old `/workspace` route remains but now expects an engagement id param; stub it to render `WorkspaceShell` with the engagement id available via `useParams`.

## Test plan

- **Unit — backend**: each layer tested in isolation; coverage target ≥ 80% on `app/features/engagements/`. Repository tested with real async session (reuse `features/conftest.py` test-db fixture). Service tested with mocked repository. Router tested with `AsyncClient` and mocked service.
  - `test_create_engagement_returns_detail`
  - `test_create_engagement_auto_adds_owner_member`
  - `test_create_engagement_any_authenticated_user_may_create` — non-admin user creates successfully (no 403)
  - `test_list_engagements_only_returns_own`
  - `test_get_engagement_member_can_read`
  - `test_get_engagement_non_member_returns_404`
  - `test_get_engagement_unauthenticated_returns_401`
  - `test_add_member_owner_succeeds`
  - `test_add_member_non_owner_returns_403`
  - `test_add_member_unknown_username_returns_404`
  - `test_add_member_duplicate_returns_409`
  - `test_remove_member_owner_succeeds`
  - `test_remove_member_owner_cannot_remove_self_returns_400`
  - `test_list_members_non_member_returns_404`
  - `test_test_user_seeder_skipped_in_production` — asserts the `ADEPTUS_TEST_USER` seeder is not called when `ENVIRONMENT=production`

- **Unit — frontend** (Vitest + RTL); coverage target ≥ 60% on `src/features/engagements/`:
  - `EngagementList.test.tsx` — renders cards, empty state, loading skeleton
  - `NewEngagementDialog.test.tsx` — form submit, 422 error display, close on success
  - `MembersList.test.tsx` — renders members, owner sees remove buttons, member does not
  - `InviteMemberForm.test.tsx` — submits mutation, shows conflict error

- **Integration** (`@pytest.mark.integration`, real Postgres):
  - `test_full_engagement_lifecycle` — create engagement as user A; verify user B cannot see it; invite B; verify B can now list and read it; remove B; verify B can no longer read it.

- **E2E** (Playwright) — one critical journey:
  - `engagements.spec.ts` — log in as admin; create engagement; open membership panel; invite the test user (from `ADEPTUS_TEST_USER` env var seeded at startup); log out; log in as test user; verify engagement visible; open workspace stub.

## Acceptance criteria

- `make test` passes (lint + typecheck + all unit tests).
- `make migrate` runs the new migration cleanly against a fresh Postgres container with no existing `engagements` or `engagement_members` tables.
- `make dev` brings up the full stack; manually:
  1. Log in as admin.
  2. Navigate to the engagements landing page — see empty state.
  3. Click "New Engagement", fill form, click "Create" — card appears in the list.
  4. Open the engagement, navigate to Members — see only admin listed as owner.
  5. Invite the test user seeded from `ADEPTUS_TEST_USER` env var — the user appears in the members list.
  6. Log out; log in as the test user — see the engagement; click "Open" — workspace shell renders with the correct engagement id in the URL.
  7. Log in as a third user with no membership — see empty engagements list.
  8. Confirm that with `ENVIRONMENT=production` (or the env var unset), no second user is seeded at startup (verify by checking the `users` table has only the admin row after a fresh `make dev` equivalent with production settings).
- CI is green on the PR (`make test` equivalent passes in GitHub Actions).

## Risks

- **Risk 1: 404 vs 403 for non-members.** The spec (§17.1 — engagement isolation) requires non-members to get `404` rather than `403` so the existence of the engagement is not revealed. This is an intentional security posture. Ensure every service and router path that resolves an engagement does so via `get_engagement_for_member`, never via a bare `get_engagement_by_id` that would need a separate membership check.
- **Risk 2: Owner self-removal.** If an owner can remove themselves from the membership table, the engagement becomes ownerless and unmanageable. The `remove_member` service method must explicitly raise `BadRequestError` when `user_id == caller.id`. A database-level enforcement (trigger or check) is not required for this slice, but the invariant must be tested.
- **Risk 3: Routing collision with Slice 00.** Slice 00 set up `/workspace` as the post-login destination. This slice changes the post-login redirect to `/engagements`. If the workspace route changes signature (now requires an engagement id), Slice 00's E2E test (`auth.spec.ts`) may break. Mitigation: keep `/workspace` alive as a redirect to `/engagements` during the transition and update the auth E2E test in the same PR.
- **Risk 4: OpenAPI client regeneration.** The frontend OpenAPI snapshot must be regenerated after the backend endpoints are finalised. If the backend type shapes drift from what the tests mock, RTL tests will catch it only at the mock level. Ensure the integration test runs against the real generated client shapes.
- **Risk 5: Dev-only seeder leaking into production.** The `ADEPTUS_TEST_USER` seeder must be behind a hard `ENVIRONMENT` check. If the check is accidentally removed or the env var is set in a production `.env`, a weak test credential would be seeded into the live database. The unit test (`test_test_user_seeder_skipped_in_production`) and the acceptance criterion (step 8) both guard this. The implementer must also ensure `.env.example` documents these vars as dev-only.

## Open questions for the human

None.

## Security review required?

No — this slice does not touch auth, MCP, audit log, single-writer graph, RAG isolation, egress, secrets, or approvals. Membership access control (§17.1 `404` posture) is covered by the standard code-review process.

## Progress

(The stop-checkpoint hook and compact-handoff skill append here. Leave empty at planning time.)
- 2026-05-30T17:25:32Z — 934e287 slice-00 follow-up: auth hardening (sliding expiry, no token logging) (#5)
