# Slice NN: <Title>

> Template for vertical slice specs. Copy and fill in. Delete this blockquote.

**Branch**: `slice-NN-<kebab>`
**GitHub Issue**: # (filled in by start-slice)
**Status**: planned | in-progress | done
**Risky**: yes | no

---

## Goal

One sentence. Verb-driven. The outcome a user can see.

## User-visible demo

What can a user (or you) actually do after this slice is merged? Be specific:
- "Log in with seeded admin credentials"
- "See the engagement list page with zero engagements"
- "Click 'New engagement', fill the wizard, see the new engagement appear"

## Out of scope

What this slice intentionally does NOT do. Important for resisting scope creep:
- "...does NOT implement engagement archiving (that's slice XX)"
- "...does NOT wire up the AI chat panel (slice YY)"

## Requirements traceability

Which requirements.md sections this slice fulfills (cite §§):
- §X.Y — <what>
- §X.Z — <what>

## Contract

OpenAPI delta for any new or changed endpoints. For frontend-only slices, list the TS types added.

```yaml
paths:
  /api/v1/engagements:
    post:
      ...
```

## Data model changes

Sketch the Alembic migration. Tables, columns, FKs, indexes. The full migration is written via the `write-alembic-migration` skill, not here.

- `engagements` table: id, name, status, created_at, ...
- `engagement_members` table: (engagement_id, user_id, role) — composite PK

## Backend tasks

Ordered. Each independently testable. Complexity: S/M/L.

1. **[S]** Add `engagements/models.py` with the Engagement SQLAlchemy model
2. **[S]** Add `engagements/schemas.py` with Create/Read Pydantic models
3. **[M]** Add `engagements/repository.py` with CRUD methods + tests
4. **[M]** Add `engagements/service.py` with creation logic + membership invariant + tests
5. **[M]** Add `engagements/router.py` with POST + GET endpoints + tests
6. **[S]** Wire router in `app/main.py`
7. **[S]** Add Alembic migration via `write-alembic-migration` skill

## Frontend tasks

Ordered. Complexity: S/M/L.

1. **[S]** Regenerate OpenAPI types into `shared/api/`
2. **[M]** Add `features/engagements/api.ts` with TanStack Query hooks
3. **[M]** Add `features/engagements/components/EngagementList.tsx` + test
4. **[M]** Add `features/engagements/components/NewEngagementWizard.tsx` + test
5. **[S]** Wire into the workspace router

## Test plan

- **Unit (backend)**: each layer tested in isolation; coverage ≥80% on `app/features/engagements/`
- **Unit (frontend)**: components rendered via RTL, user interactions via `userEvent`; coverage ≥60%
- **Integration**: one happy-path test that creates an engagement via real DB (using the test compose stack)
- **E2E**: skip for this slice unless it's a user-facing critical journey

## Acceptance criteria

- `make test` passes
- `make dev` brings up the stack; logging in and creating an engagement works end-to-end via the UI
- `gh pr view` shows green CI

## Risks

- **Risk 1**: <description and mitigation>
- **Risk 2**: ...

## Open questions for the human

(Fill in or write "None")

- ...

## Security review required?

(yes if touches: auth, MCP, audit, single-writer, RAG isolation, secrets, approvals, egress)

## Progress

(The stop-checkpoint hook and compact-handoff skill append here. Leave empty at planning time.)
