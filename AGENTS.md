# Subagent coordination

Subagent definitions are in `.claude/agents/`. Use them aggressively — every investigation, review, or research task should be delegated, not done in the main loop. The main loop's job is orchestration, not exploration.

## When to delegate

| Task | Subagent |
|---|---|
| Read 5+ files to answer a "where does X happen" question | `architect` |
| Plan a new slice (decompose requirements → tasks) | `slice-planner` |
| Implement a single feature folder from an approved plan | `implementer` |
| Write or expand tests for a feature | `test-writer` |
| Review a finished slice before PR | `code-reviewer` |
| Security/threat-model a slice that touches auth, MCP, audit, single-writer, RAG isolation, secrets | `security-reviewer` |
| Update `docs/architecture.md` or write an ADR | `docs-writer` |

## Handoff format (use this every time)

When delegating, include:
- **Goal**: one sentence
- **Files in scope**: explicit list of paths
- **Files OUT of scope**: paths the subagent must not touch
- **Done when**: testable acceptance criterion
- **Return**: what to put in the summary

## What never goes in a subagent
- Final approval of a plan (that's the human)
- Committing/pushing to git (main loop only, after human ack)
- Anything destructive in `/sandbox` (run interactively so the user sees it)

## Risky slices need a security review

Any slice that touches **auth, MCP, audit log, single-writer graph process, egress friction, approval flow, RAG isolation, or secrets** MUST go through `security-reviewer` before the PR is opened. The `finish-slice` skill enforces this.
