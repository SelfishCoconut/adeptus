# Adeptus — Claude Code Build Architecture

> **What this is.** The complete operational architecture for building Adeptus with Claude Code. It defines how slices are planned, executed, tested, and reviewed; what lives in `CLAUDE.md`; which subagents, skills, and hooks are in play; which plugins/MCP servers to install; and the file-by-file scaffolding to drop into the repo on day 0.
>
> **Not a re-statement of requirements.** Read `docs/requirements.md` for the *what*. This document is the *how Claude Code builds it*.

---

## 0. Decisions locked in (recap)

| Decision | Value |
|---|---|
| Backend | Python 3.12 + FastAPI (async) |
| Frontend | Vite + React + TypeScript |
| DB | PostgreSQL + pgvector |
| Repo | Monorepo: `/backend`, `/frontend`, `/mcp-servers`, `/docs`, `/.claude` |
| FastAPI layout | Per-feature folders under `backend/app/features/` (vertical slices / screaming arch) |
| Slice 0 | Walking skeleton — auth + create engagement + empty 3-pane UI + healthcheck, end-to-end |
| Build order in a slice | Contract-first: OpenAPI schema → backend + frontend in parallel |
| Tests | Pragmatic — alongside, gate before merge, coverage 80% backend `features/*`, 60% frontend |
| Stubs | Heavy test-double layer for externals (LLMs, Docker, MCP, Postgres in unit tests); placeholders in code only when a slice is intentionally split across PRs |
| Slice tracking | Markdown canonical in `/docs/slices/`, mirrored to GitHub Issues |
| Workflow | Plan-gated per slice (you approve the plan before execution) |
| Hooks | Strict — auto-format, lint, typecheck, test, secret-scan, dangerous-command guard |
| Next-steps skill | Layered: project planner → slice picker → task generator (D) |
| CI | GitHub Actions |
| Style | Python PEP 8 (Ruff), TS strict, async-first |
| Dev pentest tools | Allowed only against the bundled Juice Shop sandbox container |
| Secrets in dev | No restriction on Claude reading them |

---

## 1. The mental model

Three layers of context, three layers of automation. Every decision below maps to one of them.

```
┌────────────────────────────────────────────────────────────────┐
│  STATIC CONTEXT (always in the system prompt — be ruthless)    │
│  ─ CLAUDE.md  ── project DNA, commands, conventions, rules     │
│  ─ AGENTS.md  ── how subagents coordinate                      │
└────────────────────────────────────────────────────────────────┘
┌────────────────────────────────────────────────────────────────┐
│  DYNAMIC CONTEXT (loaded on demand — keeps the prompt small)   │
│  ─ Skills    ── triggered by intent, give Claude a workflow    │
│  ─ Subagents ── isolated context windows for sub-tasks         │
│  ─ Plan/Spec ── one markdown file per slice, the source of     │
│                  truth that survives /clear                    │
└────────────────────────────────────────────────────────────────┘
┌────────────────────────────────────────────────────────────────┐
│  DETERMINISTIC ENFORCEMENT (no LLM in the loop)                │
│  ─ Hooks     ── pre/post tool, on stop, on session start       │
│  ─ CI        ── GitHub Actions, identical gates                │
│  ─ Pre-commit── git hooks, same gates again                    │
└────────────────────────────────────────────────────────────────┘
```

The three guiding principles:

1. **The repo is the memory.** Anything you'd lose on `/clear` belongs in a markdown file under `/docs/slices/` or `/docs/decisions/`.
2. **Subagents have their own context windows; use them for everything investigative.** Code review, codebase exploration, security audit, test-writing, deep research — fork them off so they don't pollute the main loop.
3. **Hooks are the only thing that can't hallucinate.** Anything truly non-negotiable (formatting, tests, secret-scan, dangerous-command blocking) belongs in a hook, not a prompt.

---

## 2. Repository layout

```
Adeptus/
├── .claude/
│   ├── settings.json                 # Hooks, permissions, plugin pins
│   ├── agents/                       # Subagents
│   │   ├── architect.md
│   │   ├── slice-planner.md
│   │   ├── implementer.md
│   │   ├── test-writer.md
│   │   ├── code-reviewer.md
│   │   ├── security-reviewer.md
│   │   └── docs-writer.md
│   ├── skills/                       # Reusable workflows (auto-invoked)
│   │   ├── plan-project/SKILL.md
│   │   ├── pick-next-slice/SKILL.md
│   │   ├── start-slice/SKILL.md
│   │   ├── next-task-in-slice/SKILL.md
│   │   ├── finish-slice/SKILL.md
│   │   ├── add-feature-folder/SKILL.md
│   │   ├── write-alembic-migration/SKILL.md
│   │   ├── add-mcp-server/SKILL.md
│   │   ├── pentest-sandbox/SKILL.md
│   │   └── compact-handoff/SKILL.md
│   └── hooks/                        # Deterministic guardrails (shell)
│       ├── pre-bash-guard.sh
│       ├── post-edit-format.sh
│       ├── pre-commit-secrets.sh
│       ├── stop-checkpoint.sh
│       └── session-start.sh
├── CLAUDE.md                         # Project DNA — see §3
├── AGENTS.md                         # Subagent coordination protocol — see §4
├── README.md
├── .gitignore
├── .editorconfig
├── docker-compose.yml                # API + DB + Ollama + frontend + juice-shop sandbox
├── docker-compose.dev.yml            # Hot-reload overrides
├── backend/
│   ├── pyproject.toml                # uv / pdm, Ruff, mypy, pytest config
│   ├── alembic.ini
│   ├── alembic/versions/
│   ├── app/
│   │   ├── main.py                   # FastAPI app factory; mounts feature routers
│   │   ├── core/                     # Cross-cutting (config, db, deps, errors, logging)
│   │   ├── features/                 # << ONE FOLDER PER VERTICAL SLICE FEATURE >>
│   │   │   ├── auth/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── router.py         # FastAPI routes for this feature
│   │   │   │   ├── schemas.py        # Pydantic v2 request/response
│   │   │   │   ├── models.py         # SQLAlchemy 2.x ORM
│   │   │   │   ├── service.py        # Business logic
│   │   │   │   ├── repository.py     # DB access
│   │   │   │   ├── deps.py           # Feature-local FastAPI dependencies
│   │   │   │   └── tests/
│   │   │   │       ├── test_router.py
│   │   │   │       ├── test_service.py
│   │   │   │       └── conftest.py
│   │   │   ├── engagements/
│   │   │   ├── graph/
│   │   │   ├── findings/
│   │   │   ├── chat/
│   │   │   ├── tools/
│   │   │   ├── reports/
│   │   │   ├── rag/
│   │   │   ├── audit/
│   │   │   └── ...
│   │   └── shared/                   # Models touched by 2+ features (User, Engagement)
│   └── tests/                        # Cross-feature integration tests only
├── frontend/
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── features/                 # Mirror of backend features
│   │   │   ├── auth/
│   │   │   │   ├── api.ts            # Generated from OpenAPI + custom hooks
│   │   │   │   ├── components/
│   │   │   │   ├── hooks/
│   │   │   │   ├── stores/           # zustand
│   │   │   │   └── __tests__/
│   │   │   ├── engagements/
│   │   │   ├── graph/
│   │   │   ├── chat/
│   │   │   └── ...
│   │   └── shared/                   # Layout, theme, design system
│   └── playwright/                   # E2E for critical user journeys
├── mcp-servers/                      # Each tool category = its own MCP server
│   ├── _template/                    # Skeleton; new MCPs scaffolded from this
│   ├── shell-exec/
│   ├── nmap/
│   ├── http-recon/                   # httpx, curl, gobuster, ffuf
│   └── burp-import/
├── sandbox/
│   ├── docker-compose.juice-shop.yml # OWASP Juice Shop for dev/integration tests
│   └── README.md
├── docs/
│   ├── requirements.md               # Your spec, verbatim — read-only canonical
│   ├── architecture.md               # High-level system overview (Claude updates)
│   ├── decisions/                    # ADRs — one per architectural decision
│   │   └── 0001-single-writer-per-engagement.md
│   ├── slices/                       # Vertical slice specs — source of truth
│   │   ├── _template.md
│   │   ├── PROJECT_PLAN.md           # Ordered list of all slices; status board
│   │   ├── slice-00-walking-skeleton.md
│   │   ├── slice-01-engagement-crud.md
│   │   ├── ...
│   └── runbooks/                     # Operational how-tos
└── .github/
    └── workflows/
        ├── ci.yml                    # lint + typecheck + test + build on PR
        ├── secret-scan.yml           # gitleaks on push
        └── claude-code-review.yml    # Opt-in PR review via Claude action
```

### Why this layout, briefly

- **Features mirror across backend/frontend.** Same word in both trees means the same concept. Claude can grep `auth` and find everything. Closing a slice cleanly is a `git mv` of one folder, not surgery across 12 layers.
- **`core/` and `shared/` are deliberately tiny.** Anything you put there leaks across slices. The default answer to "where does this go?" is "in the feature folder." Promotion to `shared/` requires an ADR.
- **MCP servers are not in `backend/`.** They run as separate stdio subprocesses; coupling them to FastAPI's import graph is a future-pain trap.
- **`docs/slices/` IS the project plan.** GitHub Issues mirror it for visibility but markdown is canonical — issues drift, files in git don't.

---

## 3. `CLAUDE.md` — the project DNA

This file gets injected into every session's system prompt. It survives `/clear`, `/compact`, and context resets. **Every byte costs you across the whole project**, so it stays short. Anything verbose lives in a skill or a doc that gets loaded on demand.

The template (drop verbatim, edit values in `< >`):

```markdown
# Adeptus

Locally-deployable AI-assisted pentest platform. See `docs/requirements.md` for full spec.

## Stack
- Backend: Python 3.12, FastAPI (async), SQLAlchemy 2.x async, Alembic, Pydantic v2, pytest
- Frontend: Vite + React 18 + TypeScript (strict), TanStack Query, Zustand, Tailwind, shadcn/ui, Vitest, Playwright
- DB: PostgreSQL 16 + pgvector
- LLM (local): Ollama. Cloud: Anthropic Claude API (optional, per-engagement).
- MCP: stdio subprocesses per tool category. See `docs/architecture.md#mcp`.

## Commands (run from repo root)
- `make dev`           — full stack up (compose, hot-reload)
- `make test`          — backend + frontend tests + lint + typecheck
- `make test-backend`  — pytest only
- `make test-frontend` — vitest + playwright
- `make lint`          — ruff + mypy + eslint + tsc --noEmit
- `make format`        — ruff format + prettier
- `make migrate`       — alembic upgrade head
- `make sandbox`       — bring up Juice Shop on http://localhost:3000

## Conventions — Backend
- PEP 8 enforced by Ruff. Line length 100. Type hints mandatory.
- One folder per feature under `app/features/<name>/`. NEVER add to `core/` or `shared/`
  without an ADR in `docs/decisions/`.
- Every feature folder: `router.py`, `schemas.py`, `models.py`, `service.py`,
  `repository.py`, `tests/`. Don't merge layers into one file even when small.
- Async everywhere. No sync SQLAlchemy sessions. No blocking I/O in routes.
- Errors: raise domain exceptions in `service.py`, translate to HTTP in `router.py`
  via `core.errors.handlers`.

## Conventions — Frontend
- TypeScript strict. No `any`. Use `unknown` + narrowing.
- TanStack Query for server state. Zustand for ephemeral client state. No Redux.
- API client auto-generated from OpenAPI into `frontend/src/shared/api/`. Don't hand-write.
- Tailwind classes only — no inline styles, no styled-components.

## Conventions — Tests
- Pragmatic TDD: write tests alongside the code, must pass before commit.
- Coverage gate: 80% on `backend/app/features/*`, 60% on `frontend/src/features/*`.
- External services (Ollama, Anthropic, Docker, MCP subprocesses) MUST be mocked
  in unit tests. Integration tests use the sandbox compose stack.
- Pentest tools NEVER run against external targets in tests. Only `sandbox/juice-shop`.

## Workflow
- Vertical slices. One slice = one PR. Slices live in `docs/slices/`.
- Plan-gated: every slice has a `docs/slices/slice-NN-*.md` with a plan section.
  Wait for human approval on the plan before executing the slice.
- Branch naming: `slice-NN-short-name`.
- Commit style: Conventional Commits. One logical change per commit.

## How to navigate the codebase
- "Where does X live?" → `app/features/<X>/` and `src/features/<X>/`.
- "What's the source of truth for slice plans?" → `docs/slices/PROJECT_PLAN.md`.
- "Why was a decision made?" → `docs/decisions/`.
- "How does single-writer-per-engagement work?" → `docs/decisions/0001-*.md`
  and `app/features/graph/writer.py`.

## Anti-patterns (do not do these)
- Don't widen `core/` or `shared/` without an ADR.
- Don't write to the graph outside the single-writer process (§8.2 of requirements).
- Don't redact data before sending to the LLM. Privacy lives at the engagement
  toggle and the egress pattern-friction layer.
- Don't add provenance fields to entities — the audit log is the source of truth.
- Don't run pentest tools against anything other than the sandbox in dev/test.

## When stuck
Stop and ask. Don't invent file paths, API shapes, or library APIs.
Use the `Context7` MCP to check current docs for FastAPI, SQLAlchemy, React,
Tailwind, shadcn, Playwright, etc.
```

Total: ~80 lines. Stays under ~1200 tokens. That's the budget.

---

## 4. `AGENTS.md` — subagent coordination protocol

A short file (~30 lines) that tells Claude **when** to fork off a subagent versus do work in the main loop. Subagents have their own context windows; the main loop only sees their summary. Without this file, Claude won't reach for them and your main context will bloat.

```markdown
# Subagent coordination

Subagent definitions are in `.claude/agents/`. Use them aggressively — every
investigation, review, or research task should be delegated, not done in the
main loop. The main loop's job is orchestration, not exploration.

## When to delegate

| Task | Subagent |
|---|---|
| Read 5+ files to answer a "where does X happen" question | `architect` |
| Plan a new slice (decompose requirements → tasks) | `slice-planner` |
| Implement a single feature folder from an approved plan | `implementer` |
| Write or expand tests for a feature | `test-writer` |
| Review a finished slice before PR | `code-reviewer` |
| Security/threat-model a slice that touches auth, MCP, or audit | `security-reviewer` |
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
```

---

## 5. Subagents — the roster

Each lives at `.claude/agents/<name>.md`. Frontmatter format from current Claude Code docs: `name`, `description`, `tools`, `model`.

| Agent | Purpose | Tools | Model |
|---|---|---|---|
| `architect` | Read-only codebase exploration; answers "where/why/how" questions without polluting main context | Read, Grep, Glob | sonnet |
| `slice-planner` | Reads `docs/requirements.md` + `docs/slices/PROJECT_PLAN.md`, produces the next slice's full spec | Read, Grep, Glob, Write (to `docs/slices/` only) | sonnet |
| `implementer` | Executes an approved slice plan; writes code + tests; can edit, run tests | Read, Write, Edit, Bash | sonnet |
| `test-writer` | Writes pytest / vitest tests against a spec; runs them; reports pass/fail | Read, Write, Edit, Bash | sonnet |
| `code-reviewer` | Reviews a diff against CLAUDE.md conventions + the slice plan; returns findings with severity | Read, Grep, Glob, Bash (git diff) | sonnet |
| `security-reviewer` | Threat-models slices that touch auth, MCP, secrets, audit, single-writer, RAG isolation | Read, Grep, Glob | sonnet |
| `docs-writer` | Updates architecture doc, writes ADRs, keeps slice docs synced with reality | Read, Write, Edit, Grep | sonnet (cheap, mostly text) |

A representative example:

```markdown
---
name: slice-planner
description: |
  Plans the next vertical slice for Adeptus. Reads docs/requirements.md and
  docs/slices/PROJECT_PLAN.md, picks the next slice (or refines the one the
  user names), and writes a complete slice spec to docs/slices/slice-NN-*.md
  using docs/slices/_template.md. Use proactively whenever the user says
  "plan the next slice", "what's next", or "start slice N".
tools: Read, Grep, Glob, Write
model: sonnet
---

You are the slice planner for Adeptus.

## Inputs
- `docs/requirements.md` — the locked spec (authoritative, never modify)
- `docs/slices/PROJECT_PLAN.md` — ordered backlog with status
- `docs/slices/_template.md` — the spec template
- `docs/architecture.md` — current high-level architecture
- Existing slices under `docs/slices/slice-*.md` (read for context only)

## Method
1. If user named a slice, locate it. Otherwise pick the next `Status: todo`
   from PROJECT_PLAN.md whose dependencies are all `Status: done`.
2. Re-read the relevant sections of requirements.md.
3. Use `architect` agent if you need to read implementation details from
   /backend or /frontend — never read more than 3 source files yourself.
4. Produce `docs/slices/slice-NN-<kebab>.md` matching the template exactly:
   - Goal (1 sentence)
   - User-visible outcome (the demo)
   - Out of scope (what this slice intentionally does NOT do)
   - Contract (OpenAPI snippet for new/changed endpoints)
   - Backend tasks (ordered, each independently testable)
   - Frontend tasks (ordered)
   - Data model changes (Alembic migration sketch)
   - Test plan (unit + integration + e2e if applicable)
   - Acceptance criteria (executable: which `make` command proves it works)
   - Risks & open questions for the human
5. Update PROJECT_PLAN.md: set this slice's `Status: planned`.
6. Return: path to the new file + a 5-line summary + the open questions.

## Never
- Write code. You write specs.
- Mark a slice `done` or `in-progress` — that's the implementer/finisher.
- Combine two slices into one. If two things naturally couple, write two specs
  and document the dependency.
- Skip the open-questions section. If there are none, write "None".
```

---

## 6. Skills — the workflows Claude triggers automatically

Skills are not subagents. They're *workflows* injected into the running context when their description matches the user's intent. Use them for repeatable procedures.

Each lives at `.claude/skills/<name>/SKILL.md`. The structure follows current Anthropic guidance: gerund-form name, third-person description, body kept tight.

The roster:

| Skill | When it triggers | What it does |
|---|---|---|
| `plan-project` | "plan the project", "break down the requirements" — once at start | Reads `docs/requirements.md` and emits `docs/slices/PROJECT_PLAN.md` with every slice ordered by dependency. Then stops. |
| `pick-next-slice` | "what's next", "next slice" | Looks at PROJECT_PLAN.md, picks the next unblocked todo, delegates spec-writing to `slice-planner` subagent |
| `start-slice` | "start slice N", "let's do slice X" | Loads the slice spec, runs `/clear` ritual, creates branch, opens GitHub Issue, asks for plan approval |
| `next-task-in-slice` | "next step", "what now" mid-slice | Reads the in-progress slice spec + git log + last commit, returns the next ordered task |
| `finish-slice` | "finish slice", "ship it", "wrap up" | Runs full test gate, generates PR body from slice spec, closes the GitHub Issue, marks slice done in PROJECT_PLAN.md |
| `add-feature-folder` | "create feature X", "scaffold X" | Generates the canonical 6-file feature folder (router/schemas/models/service/repository/deps + tests/) for both backend and frontend |
| `write-alembic-migration` | "migration for X", "alembic" | Generates Alembic migration with both up and down, runs autogenerate against current models, validates downgrade works |
| `add-mcp-server` | "new MCP server", "wrap tool X as MCP" | Scaffolds a new MCP server from `mcp-servers/_template/`, declares weight + capability flags, adds it to the static MCP config |
| `pentest-sandbox` | "test against juice shop", "run X against sandbox" | Brings up the Juice Shop compose stack, points the tool at it, never anywhere else |
| `compact-handoff` | "context is getting full", "compact" | Writes session state to `docs/slices/slice-NN-*.md#progress`, runs `/compact` with structured preservation instructions |

**The three "next steps" skills (your D-layered planner)** map to: `plan-project` (project layer), `pick-next-slice` (slice layer), `next-task-in-slice` (task layer). All three exist, each is triggered by different language, each produces a different artifact.

### Example: `pick-next-slice/SKILL.md`

```markdown
---
name: pick-next-slice
description: |
  Picks the next vertical slice to work on for Adeptus by reading
  docs/slices/PROJECT_PLAN.md, finding the next slice whose Status is todo
  and whose dependencies are all done, and delegating spec-writing to the
  slice-planner subagent. Use when the user asks "what's next", "pick next
  slice", or seems unsure what to work on after finishing a slice.
allowed-tools: Read, Grep
---

# Pick the next slice

## Steps

1. Read `docs/slices/PROJECT_PLAN.md`.
2. Find the first slice where:
   - `Status: todo`
   - Every entry in its `Depends on:` list has `Status: done`
3. If none found, summarize what's blocked and ask the user.
4. If found, output:
   - The slice number, name, and one-line goal.
   - The current PROJECT_PLAN entry verbatim.
5. Ask the user: "Plan it? (delegates to slice-planner subagent)"
6. On yes, delegate to the `slice-planner` subagent with the slice number.

## Do not
- Write the spec yourself (that's the subagent's job).
- Mark anything done or in-progress.
- Modify PROJECT_PLAN.md.
```

### Example: `start-slice/SKILL.md` — the per-slice ritual

```markdown
---
name: start-slice
description: |
  Starts work on an approved slice for Adeptus. Loads the slice spec,
  resets context, creates the git branch, opens a tracking GitHub Issue,
  and asks for human approval of the plan before any code is written.
  Use when the user says "start slice N", "let's do slice X", or after
  pick-next-slice has produced an approved spec.
allowed-tools: Read, Bash
---

# Start a slice (plan-gated)

## Steps

1. Confirm the slice spec exists at `docs/slices/slice-NN-*.md`.
2. Run `/clear` to drop accumulated context from the previous slice.
3. After clear, re-load: CLAUDE.md (auto), the slice spec, the PROJECT_PLAN entry.
4. Create the branch: `git checkout -b slice-NN-<kebab>`.
5. Open a GitHub Issue mirroring the slice spec
   (`gh issue create --title "Slice NN: ..." --body-file docs/slices/slice-NN-*.md`).
6. Print the slice's PLAN section to the user. Stop. Ask:
   "Approve this plan? (y/N/edit)"
7. Only on `y`, delegate the first task to the `implementer` subagent.
8. On `edit`, surface the changes to the spec file and re-confirm.
9. On `N` or no answer, do not write any code.

## Never
- Write code before the human types `y`.
- Skip the /clear (this is the whole point of plan-gated workflow).
- Combine multiple slices in one branch.
```

---

## 7. Hooks — the deterministic gates

These do not depend on Claude understanding anything. They run shell scripts and exit-code their way to blocking or allowing actions. Defined in `.claude/settings.json`.

### The settings.json

```json
{
  "permissions": {
    "allow": [
      "Bash(make:*)",
      "Bash(uv:*)",
      "Bash(pytest:*)",
      "Bash(npm:*)",
      "Bash(pnpm:*)",
      "Bash(git diff:*)",
      "Bash(git status)",
      "Bash(git log:*)",
      "Bash(git add:*)",
      "Bash(git commit:*)",
      "Bash(git checkout:*)",
      "Bash(gh:*)",
      "Bash(alembic:*)",
      "Bash(ruff:*)",
      "Bash(mypy:*)",
      "Bash(docker compose:*)"
    ],
    "deny": [
      "Bash(rm -rf /:*)",
      "Bash(git push --force:*)",
      "Bash(git reset --hard HEAD~:*)"
    ]
  },
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": ".claude/hooks/session-start.sh" }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": ".claude/hooks/pre-bash-guard.sh" }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          { "type": "command", "command": ".claude/hooks/post-edit-format.sh" }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": ".claude/hooks/stop-checkpoint.sh" }
        ]
      }
    ]
  }
}
```

### `pre-bash-guard.sh` — block pentest tools against non-sandbox targets, plus dangerous commands

Reads the pending tool call as JSON on stdin. Exits `2` to block with a message Claude will see on stderr.

```bash
#!/usr/bin/env bash
# .claude/hooks/pre-bash-guard.sh
set -euo pipefail

payload="$(cat)"
cmd="$(echo "$payload" | jq -r '.tool_input.command // ""')"

# 1. Block destructive git
if echo "$cmd" | grep -qE '(git\s+push\s+--force|git\s+reset\s+--hard\s+HEAD~|git\s+clean\s+-fdx)'; then
  echo "Blocked: destructive git operation. Ask the user before retrying." >&2
  exit 2
fi

# 2. Block pentest tools unless target is localhost / juice-shop / 127.0.0.1
if echo "$cmd" | grep -qE '^(nmap|gobuster|ffuf|sqlmap|nikto|hydra|wpscan)\b'; then
  if ! echo "$cmd" | grep -qE '(localhost|127\.0\.0\.1|juice-shop|host\.docker\.internal:3000)'; then
    echo "Blocked: pentest tool against non-sandbox target. Run against the Juice Shop sandbox only (\`make sandbox\` then target localhost:3000)." >&2
    exit 2
  fi
fi

# 3. Block bare `rm -rf` outside /tmp or node_modules
if echo "$cmd" | grep -qE 'rm\s+-rf\s+/' && ! echo "$cmd" | grep -qE 'rm\s+-rf\s+(/tmp|node_modules|\./node_modules|dist|\.venv)'; then
  echo "Blocked: rm -rf outside known scratch paths. If intentional, run manually." >&2
  exit 2
fi

exit 0
```

### `post-edit-format.sh` — format + lint + typecheck on every edit

Best-effort; non-blocking unless lint fails. Claude reads the stderr summary on failure and self-corrects on the next turn.

```bash
#!/usr/bin/env bash
# .claude/hooks/post-edit-format.sh
set -uo pipefail
cd "$CLAUDE_PROJECT_DIR" || exit 0

payload="$(cat)"
file="$(echo "$payload" | jq -r '.tool_input.file_path // .tool_input.path // ""')"

case "$file" in
  *.py)
    ruff format "$file" 2>/dev/null || true
    if ! ruff check "$file" 2>&1; then
      echo "Ruff lint failed on $file. Fix before continuing." >&2
    fi
    ;;
  *.ts|*.tsx|*.js|*.jsx)
    (cd frontend && npx prettier --write "../$file" 2>/dev/null) || true
    (cd frontend && npx eslint "../$file" 2>&1) || \
      echo "ESLint failed on $file. Fix before continuing." >&2
    ;;
  *.md|*.json|*.yml|*.yaml)
    npx prettier --write "$file" 2>/dev/null || true
    ;;
esac

# Always non-blocking — Claude reads stderr and fixes on next turn
exit 0
```

### `stop-checkpoint.sh` — write progress to the slice doc

Whenever Claude finishes a turn (Stop event), this appends a one-line "what just happened" summary to the current slice's `## Progress` section. Combined with `compact-handoff` skill, this means a `/clear` or `/compact` never loses where you are.

```bash
#!/usr/bin/env bash
# .claude/hooks/stop-checkpoint.sh
set -uo pipefail
cd "$CLAUDE_PROJECT_DIR" || exit 0

branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
slice_num="$(echo "$branch" | grep -oE '^slice-[0-9]+' | grep -oE '[0-9]+' || true)"
[ -z "$slice_num" ] && exit 0

slice_file="$(ls docs/slices/slice-${slice_num}-*.md 2>/dev/null | head -1)"
[ -z "$slice_file" ] && exit 0

last_commit="$(git log -1 --format='%h %s' 2>/dev/null || echo 'no commits yet')"
ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Append to ## Progress section if it exists
if grep -q '^## Progress' "$slice_file"; then
  printf -- '- %s — %s\n' "$ts" "$last_commit" >> "$slice_file"
fi
exit 0
```

### `session-start.sh` — print orientation on every new session

```bash
#!/usr/bin/env bash
# .claude/hooks/session-start.sh
set -uo pipefail
cd "$CLAUDE_PROJECT_DIR" || exit 0

branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
echo "== Adeptus session =="
echo "Branch: $branch"

if echo "$branch" | grep -qE '^slice-[0-9]+'; then
  slice_num="$(echo "$branch" | grep -oE '[0-9]+' | head -1)"
  slice_file="$(ls docs/slices/slice-${slice_num}-*.md 2>/dev/null | head -1)"
  [ -n "$slice_file" ] && echo "Active slice spec: $slice_file"
fi

# Top of project plan
if [ -f docs/slices/PROJECT_PLAN.md ]; then
  echo "--- Project status (top of PROJECT_PLAN.md) ---"
  head -40 docs/slices/PROJECT_PLAN.md
fi
exit 0
```

### Pre-commit (separate from Claude Code hooks)

Same checks at the git layer with `pre-commit` framework. This catches anything Claude bypasses or anything you write manually. Config in `.pre-commit-config.yaml`:

- Ruff format + check
- mypy on `backend/app/`
- ESLint + Prettier on frontend
- `gitleaks` for secret scanning
- `pytest --collect-only` to catch broken imports
- Conventional Commits message check

---

## 8. Plugins & MCP servers — the curated set

Two categories. Plugins = bundles that extend Claude Code itself. MCP servers = tools Claude can call.

### Plugins to install on day 0

| Plugin | Why |
|---|---|
| `feature-dev` (Anthropic) | Structured slice/feature dev workflow; pairs naturally with our vertical slicing. |
| `code-review` (Anthropic) | 5-agent parallel review with confidence scoring; runs as the gate before PR. |
| `frontend-design` (Anthropic) | Avoids the "generic AI UI" look on the 3-pane workspace + report views. |
| `commit-commands` (Anthropic) | Conventional Commits done right; less manual phrasing per commit. |

### MCP servers — for Claude Code (developer side, not the Adeptus runtime)

These help Claude code *for you*. They're separate from the MCP servers Adeptus uses internally (those are in `/mcp-servers`).

| MCP server | Why |
|---|---|
| `Context7` | Up-to-date FastAPI / SQLAlchemy 2 / React 18 / Tailwind / shadcn docs. Solves "Claude uses the deprecated API" hallucination. |
| `postgres` | Run schema introspection queries directly. Critical for migration sanity-checking. |
| `playwright` | Drive a real browser for E2E. The frontend has a 3-pane workspace + Cytoscape graph; you want this. |
| `github` | Open issues, list PRs, read CI output without leaving the loop. |
| `filesystem` (Anthropic) | Default; included for completeness. |

### MCP servers — for Adeptus itself (runtime, in `/mcp-servers`)

These are deliverables your app needs to ship. They are *not* installed into Claude Code. Each is its own subprocess started by the FastAPI tool runner.

| Server | Tools wrapped | Weight |
|---|---|---|
| `shell-exec` | generic shell fallback | heavy |
| `nmap` | nmap with preset profiles | heavy |
| `http-recon` | httpx, curl, gobuster, ffuf | mixed (httpx/curl = light, gobuster/ffuf = heavy) |
| `burp-import` | parse Burp project file → graph nodes | light |

The `_template/` folder + the `add-mcp-server` skill mean adding a new one is a 2-minute scaffold + spec.

---

## 9. The per-slice workflow (the loop you'll run dozens of times)

This is the loop. Memorize it; everything else is in service of it.

```
┌──────────────────────────────────────────────────────────────────┐
│ 1. pick-next-slice          (skill)                              │
│    → reads PROJECT_PLAN.md, identifies the candidate             │
├──────────────────────────────────────────────────────────────────┤
│ 2. slice-planner            (subagent, forked)                   │
│    → produces docs/slices/slice-NN-*.md                          │
│    → asks open questions if any                                  │
├──────────────────────────────────────────────────────────────────┤
│ 3. HUMAN: read spec, answer open questions, approve plan         │
├──────────────────────────────────────────────────────────────────┤
│ 4. start-slice              (skill)                              │
│    → /clear                                                      │
│    → git checkout -b slice-NN-...                                │
│    → gh issue create                                             │
│    → loads ONLY: CLAUDE.md + slice spec + relevant features      │
├──────────────────────────────────────────────────────────────────┤
│ 5. Contract first:                                               │
│    → write OpenAPI delta in slice spec                           │
│    → backend: write Pydantic schemas + failing tests             │
│    → frontend: generate types from OpenAPI, mock the endpoint    │
├──────────────────────────────────────────────────────────────────┤
│ 6. implementer              (subagent, repeated)                 │
│    → ONE task at a time from the slice spec                      │
│    → after each task: run tests, commit, update Progress section │
│    → use next-task-in-slice skill between tasks                  │
├──────────────────────────────────────────────────────────────────┤
│ 7. test-writer              (subagent, alongside)                │
│    → expand coverage to gate level (80% backend / 60% frontend)  │
├──────────────────────────────────────────────────────────────────┤
│ 8. code-reviewer            (subagent)                           │
│    → reviews diff against CLAUDE.md + slice spec                 │
│    → /code-review plugin runs the 5-agent parallel review        │
├──────────────────────────────────────────────────────────────────┤
│ 9. security-reviewer        (subagent, IF slice touches:         │
│    auth, MCP, audit, single-writer, RAG isolation, secrets)      │
├──────────────────────────────────────────────────────────────────┤
│ 10. finish-slice            (skill)                              │
│     → make test (full gate)                                      │
│     → generates PR body from slice spec + diff                   │
│     → marks slice done in PROJECT_PLAN.md                        │
│     → closes the GitHub Issue on merge                           │
├──────────────────────────────────────────────────────────────────┤
│ 11. HUMAN: review PR, merge                                      │
├──────────────────────────────────────────────────────────────────┤
│ 12. compact-handoff or /clear, then back to step 1               │
└──────────────────────────────────────────────────────────────────┘
```

**Context discipline rules in the loop:**

- Step 4's `/clear` is non-negotiable. Without it, slice N's context bleeds into slice N+1 and you get the classic "Claude reinvents what already exists" failure mode.
- Steps 2, 6, 7, 8, 9 are all in subagent forks. The main loop only sees the summary they return. This is how a 40+ slice project stays sane.
- The slice spec file at `docs/slices/slice-NN-*.md` is the *only* thing that survives across the clear. Anything in your head that isn't there is lost. Write it down.

---

## 10. Stubs & test doubles — the policy

Three categories, three rules:

| Thing | Rule |
|---|---|
| External services (Ollama, Anthropic API, Docker engine, Postgres in unit tests) | Always mocked. Use `pytest-httpx`, `respx`, or fakes in `tests/conftest.py`. |
| MCP subprocesses | Always mocked at the protocol layer in unit tests. Integration tests can use a real `shell-exec` against the sandbox. |
| Single-writer per-engagement graph process | Tested with a real queue + in-memory NetworkX. No mocking of the writer itself — its behavior is the thing being tested. |
| Placeholder functions in production code | Only when a slice is intentionally split across PRs *and* the placeholder is marked with `raise NotImplementedError("Implemented in slice NN")` plus a `# TODO: slice-NN` comment. Lint rule flags TODOs older than 14 days. |

Integration test layer:

- A `docker-compose.test.yml` brings up Postgres + Ollama (with a tiny model) + the Juice Shop sandbox.
- Marked `@pytest.mark.integration`; run via `make test-integration`; not gated by default in CI on PRs (too slow), gated on `main`.

---

## 11. CI pipeline (`.github/workflows/ci.yml`)

Same gates as local hooks, run in CI on every push/PR.

```yaml
name: ci
on:
  pull_request:
  push:
    branches: [main]

jobs:
  backend:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: pgvector/pgvector:pg16
        env: { POSTGRES_PASSWORD: postgres }
        ports: [5432:5432]
        options: >-
          --health-cmd pg_isready --health-interval 10s
          --health-timeout 5s --health-retries 5
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync --frozen
        working-directory: backend
      - run: uv run ruff check .
        working-directory: backend
      - run: uv run ruff format --check .
        working-directory: backend
      - run: uv run mypy app/
        working-directory: backend
      - run: uv run alembic upgrade head
        working-directory: backend
        env: { DATABASE_URL: postgresql+asyncpg://postgres:postgres@localhost/postgres }
      - run: uv run pytest --cov=app/features --cov-fail-under=80
        working-directory: backend

  frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v3
      - uses: actions/setup-node@v4
        with: { node-version: 20, cache: pnpm }
      - run: pnpm install --frozen-lockfile
        working-directory: frontend
      - run: pnpm lint
        working-directory: frontend
      - run: pnpm tsc --noEmit
        working-directory: frontend
      - run: pnpm test -- --coverage
        working-directory: frontend

  secrets:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: gitleaks/gitleaks-action@v2
```

Separate workflow `claude-code-review.yml` runs Anthropic's official PR-review action on every PR with `@claude review` in the description.

---

## 12. The slice plan derived from your requirements

Mapping your 17 requirement sections to vertical slices. This is the seed for `docs/slices/PROJECT_PLAN.md`. Numbered by execution order (not requirement section).

| # | Slice | Demo at end | Touches req §§ | Depends on |
|---|---|---|---|---|
| 00 | **Walking skeleton** | Login → land in empty 3-pane UI → backend healthcheck | 2, 3 (auth bare), 11.1, 11.2 | — |
| 01 | **Engagement CRUD + membership** | Create engagement, invite a user, list mine | 4, 3 (membership) | 00 |
| 02 | **Privacy mode + persistent banner** | Toggle strict-local; banner always visible | 5.1, 5.5, 17.5 | 01 |
| 03 | **Static MCP config + shell-exec server** | Admin sees declared capabilities; can run a shell command via UI | 6.1, 6.2 (light path), 7 | 00 |
| 04 | **Tool runner panel (light tools only)** | Run httpx against sandbox; see output in bottom pane | 6.2, 6.3, 11.2 (bottom pane), 11.4 partial | 03 |
| 05 | **Concurrency model + per-target lock** | Two heavy tools against same host serialize correctly | 6.2 fully | 04 |
| 06 | **Kill switches + timeout-confirm** | Stop button works; timeout shows kill/extend/wait dialog | 6.3 | 05 |
| 07 | **Graph data model + single-writer** | Manual node create/edit; per-engagement writer process | 8.1, 8.2 (writer + soft-delete + per-entity undo) | 01 |
| 08 | **Graph visualization (Cytoscape)** | Right pane shows force-directed graph, pinning works | 8.3, 5.4 (pinning = implicit mention) | 07 |
| 09 | **Personal undo stack** | Each user's 20-deep undo of their own writes | 8.2 (personal undo) | 07 |
| 10 | **Audit log + hash-chain** | Every action recorded; verify chain CLI | 14 (audit + tamper-evident) | 01 |
| 11 | **Local LLM via Ollama + private chat** | Send message, see streamed reply; conversation persisted | 5.1 local path, 5.4 private chat | 02 |
| 12 | **"Relevant subset" graph injection** | Debug panel shows exact subset sent per turn | 5.3 (graph access rules), 14 (debug panel) | 08, 11 |
| 13 | **Visible plan + certainty signaling** | AI shows running plan; certainty % on claims | 5.3 (visible plan + uncertainty) | 11 |
| 14 | **Cloud LLM + pattern-friction egress** | With cloud enabled, secret-looking text triggers confirmation | 5.1 (cloud + friction), 5.5 | 11, 02 |
| 15 | **Personas (CRUD + seeded)** | Switch persona mid-chat; create custom | 5.3 (personas), 5.4 | 11 |
| 16 | **Two-tier autonomy + approval flow** | Dangerous command posts approval card; any member approves | 5.2 fully | 11, 10 |
| 17 | **Soft scope enforcement** | Out-of-scope target → AI warns + asks confirmation | 5.2 (scope soft) | 16 |
| 18 | **Delegation pattern (standing autonomy)** | "Always approve dedup" toggle works for the engagement | 5.2 (delegation), 17.3 | 16 |
| 19 | **Findings model + lifecycle** | Create finding with Simple severity; verification + remediation status | 9.1 (Simple), 9.2 | 07 |
| 20 | **Findings advanced classifications** | CVSS v3.1/v4 + OWASP Risk on advanced panel; ATT&CK tags | 9.1 (CVSS + OWASP + ATT&CK) | 19 |
| 21 | **Dedup proposal + merge** | AI flags duplicates; user merges | 9.2 (dedup) | 19, 18 |
| 22 | **Attack paths (manual + AI proposals)** | Drag-link nodes; AI proposes paths | 9.3, 8.3 | 19 |
| 23 | **RAG: pgvector store + curated KB** | Embed + retrieve from OWASP/CVE corpus | 10 (curated, pgvector, isolation) | 11 |
| 24 | **RAG: per-engagement uploads** | Upload writeup; retrievable in that engagement only | 10, 11.4 | 23 |
| 25 | **Retest workflow** | Archived engagement's graph available as RAG context | 4 (retest), 10 (retest exception) | 23 |
| 26 | **Heavy tools: nmap + gobuster MCPs** | Run nmap against sandbox with stealth/normal/aggressive presets | 6.4 (nmap, gobuster), 6.2 (presets) | 06 |
| 27 | **Background tasks + completion notifications** | Close browser; come back; long tool finished, notif shown | 6.2 (background), 11.7 | 26 |
| 28 | **File uploads per engagement** | Upload wordlist; AI suggests using it in ffuf | 11.4 | 04 |
| 29 | **Embedded terminal (xterm.js)** | Shell into the engagement's container | 6.2 (raw shell) | 03 |
| 30 | **Burp project import** | Drop .burp file → graph + findings populated | 6.4 (Burp import) | 19 |
| 31 | **Presence + typing + @-mentions** | See who's online; typing indicator; share message into channel | 11.3, 5.4 (mentions) | 11 |
| 32 | **Notifications panel** | Bell icon; approval requests + tool completion + mentions | 11.7 | 16, 27, 31 |
| 33 | **Session replay (timeline scrubber)** | Browse engagement event-by-event | 11.5, 14 (audit feeds it) | 10 |
| 34 | **Report generation (Markdown)** | "Generate report" produces 6-section Markdown | 12 | 19, 22, 33 |
| 35 | **Admin dashboard** | Active sessions, tool runs, queue depth, errors | 14 (admin dashboard) | 27 |
| 36 | **Token + cost tracking** | Per-engagement + per-user display | 14 (cost), 5.1 (display) | 14 |
| 37 | **Backups: snapshots + per-engagement export** | Periodic snapshots; manual export | 13 (backup, export) | 10, 19 |
| 38 | **Crash recovery semantics** | In-flight commands marked failed on restart | 13 | 27 |
| 39 | **TLS + self-signed by default** | App reachable via HTTPS; cert swap documented | 3 (TLS) | 00 |
| 40 | **Single-user dev mode (no auth)** | Compose flag drops auth for local dev | 2 (dev mode) | 00 |

40 slices. Some collapse if you don't care about a feature; some split if a slice gets fat in planning. Order respects dependencies; the dependency column shows the critical-path links.

**Risky slices** (the ones to step-gate, even though you're on plan-gate by default): 07 (single-writer), 10 (hash-chain audit), 14 (egress friction), 16 (approval flow), 23 (RAG isolation enforcement).

---

## 13. Day 0 setup — the literal first session

Run these in order. This bootstraps everything above.

```bash
# 1. Clone or init the repo, drop docs/requirements.md in place
mkdir Adeptus && cd Adeptus
git init
mkdir -p docs/{slices,decisions,runbooks}
cp /path/to/your/requirements.md docs/requirements.md

# 2. Create the .claude scaffold (files in this doc, §3-7)
mkdir -p .claude/{agents,skills,hooks}
# ... drop in CLAUDE.md, AGENTS.md, .claude/settings.json, all subagent .md files,
# all skill folders, all hook .sh scripts (and chmod +x the hooks)
chmod +x .claude/hooks/*.sh

# 3. First Claude Code session
claude
> /plugin install feature-dev@claude-plugins-official
> /plugin install code-review@claude-plugins-official
> /plugin install frontend-design@claude-plugins-official
> /plugin install commit-commands@claude-plugins-official

# 4. Add MCP servers (Context7, postgres, playwright, github)
> /mcp add context7
> /mcp add postgres
> /mcp add playwright
> /mcp add github

# 5. Bootstrap the project plan
> use the plan-project skill to break docs/requirements.md into vertical slices

# Claude (via slice-planner subagent) produces docs/slices/PROJECT_PLAN.md
# matching the table in §12 of this doc.

# 6. Approve the plan. Then:
> use pick-next-slice

# This picks slice 00 (walking skeleton) and asks if you want to plan it.

> yes, plan it

# slice-planner subagent writes docs/slices/slice-00-walking-skeleton.md.
# You read it, answer any open questions, type "approve".

> use start-slice 00

# Slice 00 begins under plan-gate. From here on, the loop in §9 repeats.
```

---

## 14. What you actually deliver to the team / future you

When this is done, the repo has:

- 40-ish slice docs that read like a postmortem of every architectural decision.
- 40-ish PRs each linked to a slice spec, with conventional commits inside.
- An ADR folder explaining the non-obvious choices.
- A `CLAUDE.md` + `.claude/` setup any developer (or any LLM coding tool) can drop into and be productive in 5 minutes.
- A test suite with the coverage gates baked in.
- A GitHub Actions pipeline that's the same shape as the local hooks — no "works on my machine."
- A Juice Shop sandbox so the dangerous tooling can be exercised safely from day 0.

That is what "vertical slicing, with skills + agents + hooks, plan-gated, pragmatic TDD, no hallucination" actually looks like.

---

## 15. Open questions for you (now or as the project moves)

None blocking. A few worth answering before slice 00 if convenient:

1. **Admin bootstrapping.** First-time setup: env-var seeded admin, or interactive CLI on first boot? (Affects slice 00.)
2. **Session storage.** Server-side sessions in Postgres, or signed cookies? (FastAPI default is the latter but you said "long-lived sessions for days" which leans server-side for revocability.)
3. **Frontend testing depth.** Vitest unit + Playwright E2E is the plan. Want React Testing Library on top for component tests, or skip and lean on E2E for UI behavior?
4. **Ollama default model.** Pin a specific one in `docker-compose.yml`? (e.g., `llama3.2:3b-instruct-q4_K_M` for speed, `qwen2.5:7b-instruct-q4_K_M` for quality.)
5. **License header.** Apache-2.0? MIT? None? (Trivial but easier to set on day 0 than retrofit across 200 files.)

These can be ADRs after slice 00 or answered now — your call.

---

*End of architecture document. Drop into `docs/architecture.md` and commit as the very first thing.*
