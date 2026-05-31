---
name: audit-drift
description: |
  Audits how far Adeptus has drifted from its original plan and whether
  code quality is degrading. Compares the current slice's changes against
  the source-of-truth docs (the slice spec, PROJECT_PLAN.md, requirements.md,
  architecture.md, the ADRs, and CLAUDE.md conventions/anti-patterns), runs
  the mechanical gates, sweeps for anti-patterns and structural violations,
  and produces a Drift & Health Report with a verdict and prioritized
  recommendations. Critically, it separates two directions of drift: code
  that strayed from the plan (fix the code) versus a plan that no longer
  matches reality (mend the plan via an ADR / slice-planner). Read-only —
  it never edits code, specs, or docs; it recommends. Use after each slice
  merges, or when the user asks "are we drifting", "are we off track",
  "sanity check the codebase", "is the plan still valid", "codebase health",
  "did this slice stay on plan", or "is quality degrading".
allowed-tools: Read, Bash, Grep, Glob
---

# Audit drift from the plan

This skill is diagnostic, not a gate. `finish-slice` decides whether a slice
*ships*; this skill decides whether the project is still *healthy and on plan*
after it shipped. Run it after a slice lands.

## Steps

1. **Establish the baseline (the "plan").** Read, in this order:
   - The active slice spec: `docs/slices/slice-NN-*.md` (its Plan, Tasks,
     Acceptance criteria, and any "Decisions"/"Deviations" recorded mid-slice).
   - That slice's entry in `docs/slices/PROJECT_PLAN.md` (Goal, Requirements §,
     Depends-on, Risky, Status).
   - `CLAUDE.md` — the Conventions and Anti-patterns sections (the checkable rules).
   - `docs/architecture.md` — the structural invariants.
   - Any ADR in `docs/decisions/` the slice touches (auth, sessions, single-writer,
     audit chain, egress, RAG isolation).
   Cite the cited requirement sections (`requirements.md` §N) only as needed.

2. **Bound the audit to what changed.** Determine the slice's diff:
   ```
   git rev-parse --abbrev-ref HEAD          # confirm slice branch
   git diff --stat master...HEAD            # files this slice changed
   git log --oneline master..HEAD           # commits this slice
   ```
   Most findings must point at lines inside this diff. Structural checks
   (Step 4) may scan the whole repo, since drift shows up as the *shape* of
   the tree, not just edited lines.

3. **Run the mechanical gates (report, don't block).** These mirror what a
   degrading codebase trips first:
   ```
   make lint            # ruff check + format --check, mypy, eslint, tsc --noEmit
   make test-backend    # pytest, coverage gate 80% on app/features/*
   make test-frontend   # vitest, 60% gate on src/features/*
   ```
   Record pass/fail and, on failure, the first real error line. A red gate is a
   Critical finding but does NOT stop the audit — keep going; the point is the
   full picture.

4. **Structural conformance (filesystem + grep).** Drift shows here first:
   - Every `backend/app/features/<name>/` has all of `router.py`, `schemas.py`,
     `models.py`, `service.py`, `repository.py`, and `tests/`. Flag any missing
     layer or merged layers (e.g. queries inline in `router.py`).
   - `core/` or `shared/` (backend) and `frontend/src/shared/` grew this slice
     (`git diff --stat master...HEAD -- backend/app/core backend/app/shared frontend/src/shared`).
     If so, is there a *new* ADR in `docs/decisions/` justifying it? No ADR →
     Critical (CLAUDE.md anti-pattern: don't widen core/shared without an ADR).
   - Frontend: component tests colocated as `*.test.tsx`; E2E only under
     `frontend/playwright/`.

5. **Anti-pattern sweep (grep over the diff).** For each hit, capture file:line:
   - Graph writes outside the single writer: `add_node`/`add_edge`/graph mutation
     anywhere but `app/features/graph/writer.py`.
   - Hand-written API client types: edits to `frontend/src/shared/api/schema.ts`
     in the diff that didn't come from `make generate-api` (the snapshot should
     only change via regeneration).
   - Sync SQLAlchemy / blocking I/O in async paths: sync `Session`, `requests.`,
     `time.sleep`, other blocking calls inside routers/services.
   - Redaction before the LLM (privacy belongs at the engagement toggle + egress
     friction layer, never silent redaction).
   - Provenance fields on entities (`created_by`/`modified_by`/`updated_by`
     columns) — the audit log is the source of truth.
   - Frontend: `any` in TS, inline `style=`, styled-components.
   - Pentest tooling pointed at anything but `sandbox/juice-shop` in tests.
   - Errors raised as `HTTPException` in `service.py` instead of domain
     exceptions translated in `router.py`.

6. **Forward drift — did the code stay on the plan?**
   - Did the slice deliver every task / acceptance criterion in its spec? List
     any undelivered item.
   - Scope creep: changes with no matching task in the spec (new features,
     unrelated refactors). List them.
   - `PROJECT_PLAN.md` status for this slice is accurate (e.g. `done`), and its
     `Depends-on` slices are themselves `done`.
   - Commit hygiene: Conventional Commits, one logical change per commit
     (cross-reference `docs/logs/problems.log` for this branch via the
     `review-problems` lens if useful).

7. **Reverse drift — does the PLAN need mending?** This is the half people skip.
   Flag where reality has outrun the docs and the *plan* is now the stale artifact:
   - The slice spec recorded a decision/deviation that contradicts
     `architecture.md` or an ADR → recommend a new/updated ADR.
   - A cross-cutting concern legitimately landed in `core/`/`shared/` → recommend
     an ADR + an `architecture.md` update.
   - A requirement (`requirements.md` §N) was implemented differently than written,
     or turned out under/over-specified → recommend updating the requirement note
     or the slice plan.
   - Repeated `problems.log` entries on the same theme this slice → the plan or
     conventions may need a guardrail.

8. **Emit the Drift & Health Report** using this structure:
   ```
   ## Drift & Health Report — slice NN (<branch>) — <date>

   ### Verdict: ON TRACK | DRIFTING | NEEDS PLAN AMENDMENT
   <one-paragraph rationale>

   ### Gates
   - lint: pass/fail   tests(be): pass/fail   coverage(be): N%   coverage(fe): N%
   - <first error line for any failure>

   ### Code drifted from plan  (fix the CODE)
   Each finding tagged [Critical] / [Warning] / [Note]:
   - [Severity] <what> — <file:line> — <which rule/ADR/anti-pattern it violates>

   ### Plan drifted from reality  (mend the PLAN)
   - [Severity] <what reality shows> — <which doc is now stale> — <suggested ADR/update>

   ### Delivery vs spec
   - Delivered: <tasks/criteria met>
   - Missing:   <undelivered tasks>
   - Unplanned: <scope creep>

   ### Recommendations (prioritized, 1–5)
   1. <concrete next action + who should do it>
   ```

9. **Recommend remediation routes — do not apply them.** Map each finding to the
   right follow-up so the user can act:
   - Code fixes / missing layers → `implementer` subagent.
   - Coverage below gate → `test-writer` subagent.
   - New/updated ADR or `architecture.md` change → `docs-writer` subagent.
   - Plan needs re-sequencing or a slice respec → `slice-planner` subagent (or
     `pick-next-slice`).
   - Security-sensitive drift (auth, single-writer, audit chain, egress, RAG
     isolation, approval flow) → recommend `security-reviewer`.
   Offer, only if the user confirms, to append a one-line verdict to
   `docs/logs/drift.log` (`<date> slice-NN <verdict> — <headline>`) so trend
   across slices is visible over time.

## Hard rules
- Read-only and advisory. Never edit code, slice specs, PROJECT_PLAN.md,
  requirements.md, architecture.md, or ADRs. Recommend the right subagent instead.
  (The only write, the optional `drift.log` line in Step 9, requires explicit
  user confirmation.)
- Every finding cites evidence — a `file:line`, a command's output, or a named
  doc section. Never assert drift you can't point at. If you can't find evidence,
  say "no evidence of X" rather than inventing a violation.
- Always classify each finding by direction: **code → fix the code**, or
  **plan → mend the plan**. Conflating the two is the failure mode this skill exists to prevent.
- A red gate is reported, not a stop. This skill always produces the full report.
- Compare against the source-of-truth docs from Step 1 — never against
  assumptions about how the system "should" work.
- Distinguish severity honestly: Critical = an anti-pattern or broken invariant
  shipped; Warning = a convention slipped; Note = a smell or a plan that's
  drifting but not yet wrong. Don't inflate.
- If there is genuinely no drift and the gates are green, say so plainly with a
  short ON TRACK verdict — don't manufacture findings to look thorough.
