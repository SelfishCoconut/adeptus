# Slice 18: Delegation pattern — standing autonomy

**Branch**: `slice-18-standing-autonomy`
**GitHub Issue**: #51
**Status**: in-progress
**Risky**: yes (approval flow / human-in-the-loop weakening — step-gated + security review)

---

## Goal

Let an engagement member grant the AI **standing autonomy** on a category of gated
decision (a `reason` class) for the rest of an engagement, so future commands whose
gate reasons are *all* delegated run **auto-approved** (no human click) — fully audited
and revocable at any time.

## User-visible demo

On the running stack, in an engagement:
- A gated command posts an approval card (e.g. `aggressive_scan`). The card now has an
  **"Always allow aggressive scans for this engagement"** action.
- Click it → a standing-autonomy grant is created; the current command auto-approves and
  runs; chat shows it ran under standing autonomy ("auto-approved · standing autonomy").
- The **next** `aggressive_scan` command this engagement proposes **runs immediately**,
  no card, and appears in the audit log as `approval_auto_granted` citing the grant.
- An **Autonomy** panel lists active grants (category, who granted, when) with **Revoke**.
- After revoke, the next `aggressive_scan` command gates with a human card again.

## Out of scope

- **Delegating `unclassified_manifest`** — the fail-safe for un-manifested tools is
  **never** delegable; such commands always gate (see Risk 1).
- **Cross-engagement / global autonomy** — grants are strictly per-engagement (§5.2).
- **Time-boxed / count-limited grants** — a grant lasts until revoked or the engagement
  ends; auto-expiry is a later refinement.
- **Standing autonomy for non-AI (manual tool-runner) commands** — this slice governs the
  AI proposal→classify→gate path only.
- **Conditional/partial grants** (e.g. "aggressive scans only on host X") — categories only.

## Requirements traceability

- §5.2 — "Delegation pattern (generalized): the user can grant the AI standing autonomy on
  a category of decisions for the rest of the engagement … a reusable mechanism."
- §5.2 — attribution & audit (every auto-grant recorded; grantor attributed).
- §17 (design principle 3) — "Delegation as a first-class pattern."

## Delegable categories

The four §5.2 dangerous categories: `target_write`, `aggressive_scan`,
`credential_attack`, `out_of_scope`. **`unclassified_manifest` is excluded** (never
delegable). A command auto-approves **only when every reason in its classification is
covered by an active grant** — a command with reasons `[aggressive_scan, out_of_scope]`
needs *both* delegated, else it still gates (fail-safe AND semantics).

## Contract (OpenAPI delta)

New `autonomy` feature. Endpoints under the engagement:

```yaml
paths:
  /api/v1/engagements/{engagement_id}/autonomy-grants:
    get:    # list active grants for the engagement (members only)
      responses: { "200": { AutonomyGrantRead[] } }
    post:   # create/grant standing autonomy for one reason category
      requestBody: { reason: ApprovalReason }   # rejects unclassified_manifest -> 422
      responses: { "201": AutonomyGrantRead, "409": already-active-for-reason }
  /api/v1/engagements/{engagement_id}/autonomy-grants/{grant_id}:
    delete: # revoke (members only); idempotent-ish, 404 if not found
      responses: { "204": {} }
```

`AutonomyGrantRead`: `id, engagement_id, reason, granted_by_user_id, granted_by_username,
created_at, revoked_at (null when active)`.

## Data model changes

New table `autonomy_grants` (Alembic migration via `write-alembic-migration`):
- `id` UUID PK
- `engagement_id` UUID FK → engagements (indexed)
- `reason` VARCHAR — one of the delegable `ApprovalReason` values (CHECK excludes
  `unclassified_manifest`)
- `granted_by_user_id` UUID FK → users
- `created_at` timestamptz
- `revoked_at` timestamptz NULL, `revoked_by_user_id` UUID FK NULL
- **Partial unique index** on `(engagement_id, reason) WHERE revoked_at IS NULL` — at most
  one active grant per category per engagement.

No change to `approval_requests` or `tool_runs`.

## Integration (the slice-16 boundary short-circuit)

In `approvals/service.py::create_requests_for_turn` (the function the Slice-16 classifier
docstring named as Slice-18's short-circuit point):
1. Load the engagement's **active** grants once per turn (`autonomy.repository`).
2. For each action the classifier returns as `REQUIRES_APPROVAL`: if **every** reason is in
   the active-grant set (and none is `unclassified_manifest`) → **auto-approve**: take the
   same path as a human approve (`_execute_approved_run`) but attributed as an auto-grant,
   emitting a new `approval_auto_granted` audit action that records the command, the
   reasons, and the covering grant id(s). `self_approved` is `false`; a new
   `auto_approved` marker is `true`.
3. Otherwise create the pending human row exactly as today.

Auto-approved actions are returned to the chat service to run (like the autonomous list)
but **with an approval/audit record** — they are *not* silently autonomous. Grant create
and revoke each emit `autonomy_granted` / `autonomy_revoked` audit actions.

## Tasks

Numbered continuously. Every commit cites `(task N)`.

### Backend tasks
1. **[S]** Scaffold `autonomy/` feature folder (`add-feature-folder`): models, schemas,
   repository, service, router, tests.
2. **[M]** `autonomy/models.py` `AutonomyGrant` + `autonomy/schemas.py` (`AutonomyGrantRead`,
   `AutonomyGrantCreate` with `reason: ApprovalReason`, validator rejecting
   `unclassified_manifest`). Reuse `ApprovalReason` from `approvals/schemas.py`.
3. **[M]** `autonomy/repository.py`: `create_grant`, `list_active(engagement_id)`,
   `get_active_reasons(engagement_id) -> set[ApprovalReason]`, `revoke(grant_id)` + tests.
4. **[M]** `autonomy/service.py`: grant (guard: member, reject duplicate active, reject
   `unclassified_manifest`), list, revoke — each emits its audit action; domain exceptions
   + tests.
5. **[M]** `autonomy/router.py`: GET/POST/DELETE wired to membership auth; HTTP error
   translation + tests. Wire router in `app/main.py`.
6. **[S]** Add `AuditAction` members `approval_auto_granted`, `autonomy_granted`,
   `autonomy_revoked` (+ enum/DB-vocabulary tests).
7. **[L]** Integrate into `approvals/service.py::create_requests_for_turn`: load active
   grant reasons once/turn; auto-approve actions whose reasons are fully covered (reuse
   `_execute_approved_run`, new `auto_approved` attribution + `approval_auto_granted`
   audit). Extend `ClassifiedTurnResult` to carry auto-approved runs to the chat service.
   Thorough unit tests: full-cover → auto; partial-cover → gates; `unclassified_manifest`
   never auto; revoked grant → gates again.
8. **[S]** Alembic migration (`write-alembic-migration`) for `autonomy_grants` incl. the
   partial unique index; verify up/down/up on real Postgres.

### Frontend tasks
9. **[S]** Regenerate OpenAPI types (`make generate-api`).
10. **[M]** `features/autonomy/api.ts` TanStack Query hooks (list/grant/revoke, cache
    invalidation) + the `AutonomyGrant` types.
11. **[M]** Approval card: "Always allow <category> for this engagement" action (calls
    grant, then approves the current request) + a "ran under standing autonomy" indicator
    on auto-approved actions. Tests.
12. **[M]** `features/autonomy/components/AutonomyPanel.tsx`: list active grants + Revoke,
    wired into the workspace. Tests.

### Integration
13. **[M]** Integration test: grant `aggressive_scan` → a subsequent proposed
    `aggressive_scan` command auto-approves + runs + emits `approval_auto_granted`
    (no pending row); revoke → next one gates. Sandbox only.

## Test plan
- **Unit (backend)** ≥80% on `app/features/autonomy/` + the approvals-integration branch.
- **Unit (frontend)** ≥60% on `features/autonomy/`.
- **Integration**: grant→auto-approve→audit→revoke→gate, against the sandbox.
- **Audit**: assert `approval_auto_granted` rides the hash chain with grant id + reasons.

## Acceptance criteria
- `make test` green (incl. coverage gates).
- Live: grant a category, watch the next gated command of that category auto-approve and
  run with an audit trail; revoke and watch it gate again.
- `gh pr view` green CI.

## Risks
- **Risk 1 — weakening human-in-the-loop.** Standing autonomy removes the per-command
  human click for a category. *Mitigations:* per-engagement only; explicit member grant
  (attributed); revocable instantly; **every** auto-approved command still produces an
  approval/audit record (`approval_auto_granted`); `unclassified_manifest` is never
  delegable; AND-coverage (all reasons must be granted) so a delegated category can't
  smuggle an un-delegated one.
- **Risk 2 — `out_of_scope` delegation.** Delegating `out_of_scope` means the AI may act
  outside declared scope without asking. It is soft by design (§5.2), but sensitive.
  **Decision: delegable, with an explicit confirm** — the grant action for `out_of_scope`
  must surface a distinct "you are granting out-of-scope autonomy" confirmation (louder
  than the other categories). The hard sandbox guard (dev/test) and egress friction are
  unaffected and still apply to the executed run.
- **Risk 3 — race / TOCTOU on grant vs revoke.** A grant lookup per turn could race a
  concurrent revoke. *Mitigation:* read active grants inside the same transaction as the
  classify→gate step; the partial unique index prevents duplicate active grants.
- **Risk 4 — feature-folder placement.** `autonomy/` is a new feature importing
  `ApprovalReason` from `approvals`; `approvals/service` imports `autonomy/repository` for
  the per-turn lookup. Verify no import cycle (autonomy must not import `approvals.service`).

## Resolved decisions (2026-06-06)
- **`out_of_scope` is delegable** — with an explicit "you are granting out-of-scope
  autonomy" confirm on the grant action (louder than other categories). See Risk 2.
- **New `autonomy/` feature folder** (convention-aligned) — not folded into `approvals/`.
- **Any engagement member may grant/revoke** — consistent with "any member can approve"
  (§5.2); every grant/revoke is attributed and audited.

## Security review required?
**Yes** — this directly weakens the approval gate (the core human-in-the-loop control).
security-reviewer must verify: `unclassified_manifest` can never be delegated; AND-coverage
(no partial-cover auto-approve); grants are per-engagement and membership-guarded; revoke is
immediate and effective on the very next turn; every auto-approval is audited and
attributable; no import cycle / no bypass of the sandbox guard or egress friction (those
still apply to the executed run).

## Progress
(Leave empty at planning time.)
- 2026-06-06T12:27:29Z — 169fa76 feat(slice-18): autonomy data layer — model, schemas, repository (task 1, task 2, task 3)
- 2026-06-06T13:50:36Z — 5440eab feat(slice-18): alembic migration — autonomy_grants + audit actions (task 8)
