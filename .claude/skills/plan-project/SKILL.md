---
name: plan-project
description: |
  Generates the initial project plan for Adeptus by decomposing
  docs/requirements.md into ordered vertical slices and writing
  docs/slices/PROJECT_PLAN.md. Run this exactly once at the start of
  the project. Triggers on "plan the project", "break down requirements",
  "create the project plan", or being run from an empty PROJECT_PLAN.md.
allowed-tools: Read, Write, Glob, Grep
---

# Plan the project

## Steps

1. Check `docs/slices/PROJECT_PLAN.md` exists. If it has any slices beyond the placeholder, STOP and tell the user "Project plan already exists — use pick-next-slice or modify PROJECT_PLAN.md manually." Never overwrite an existing plan.

2. Read `docs/requirements.md` end to end.

3. Decompose into vertical slices. Each slice must:
   - Deliver a user-visible outcome end-to-end (UI → API → DB if applicable)
   - Be independently testable and demoable
   - Fit in a single PR (rough target: <800 lines diff)
   - Have explicit dependencies on prior slices

4. Order slices so each slice's dependencies are all earlier in the list. Start with a walking skeleton (the thinnest possible vertical path: login → empty workspace → healthcheck).

5. Group slices by phase for readability:
   - Phase A: Foundation (skeleton, auth, engagement CRUD)
   - Phase B: Core mechanics (graph, tools, audit)
   - Phase C: AI integration (LLM, autonomy, personas)
   - Phase D: Findings & attack paths
   - Phase E: RAG & retest
   - Phase F: Collaboration & polish
   - Phase G: Reporting & ops

6. Write `docs/slices/PROJECT_PLAN.md` using this exact format:

```markdown
# Adeptus — Project Plan

Source of truth for vertical slice ordering. Mirrored to GitHub Issues at finish-slice time.

Status values: `todo` | `planned` | `in-progress` | `done` | `blocked`

## Phase A — Foundation

### Slice 00: Walking skeleton
- **Goal**: Login → empty 3-pane workspace → backend healthcheck round-trip
- **Requirements**: §2, §3 (auth bare), §11.1, §11.2
- **Depends on**: —
- **Risky?**: no
- **Status**: todo

### Slice 01: ...
...
```

7. Return to the main loop:
   - Path to the generated PROJECT_PLAN.md
   - Total slice count
   - Phase breakdown summary
   - Any requirements sections that were genuinely ambiguous (flag them — don't silently resolve)

## Hard rules
- Run only once. Refuse to overwrite an existing plan.
- Do not write individual slice spec files — those are slice-planner's job, one at a time.
- Do not invent requirements. Every slice traces back to a §-citation in requirements.md.
- Mark a slice `risky: yes` if it touches auth, MCP, audit, single-writer, RAG isolation, secrets, approvals, or egress friction.
