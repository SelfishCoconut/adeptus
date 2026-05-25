# Adeptus

Locally-deployable AI-assisted pentest platform. See `docs/requirements.md` for the full spec and `docs/architecture.md` for how Claude Code builds it.

## Stack
- Backend: Python 3.12, FastAPI (async), SQLAlchemy 2.x async, Alembic, Pydantic v2, pytest
- Frontend: Vite + React 18 + TypeScript (strict), TanStack Query, Zustand, Tailwind, shadcn/ui, Vitest + RTL, Playwright
- DB: PostgreSQL 16 + pgvector
- LLM (local): Ollama, default `qwen2.5:7b-instruct-q4_K_M`. Cloud: Anthropic Claude API (optional, per-engagement).
- MCP: stdio subprocesses per tool category. See `docs/architecture.md`.

## Commands (run from repo root)
- `make dev`            — full stack up (compose, hot-reload)
- `make test`           — backend + frontend tests + lint + typecheck
- `make test-backend`   — pytest only
- `make test-frontend`  — vitest + playwright
- `make lint`           — ruff + mypy + eslint + tsc --noEmit
- `make format`         — ruff format + prettier
- `make migrate`        — alembic upgrade head
- `make sandbox`        — bring up Juice Shop on http://localhost:3000

## Conventions — Backend
- PEP 8 enforced by Ruff. Line length 100. Type hints mandatory.
- One folder per feature under `app/features/<name>/`. NEVER add to `core/` or `shared/` without an ADR in `docs/decisions/`.
- Every feature folder: `router.py`, `schemas.py`, `models.py`, `service.py`, `repository.py`, `tests/`. Don't merge layers even when small.
- Async everywhere. No sync SQLAlchemy sessions. No blocking I/O in routes.
- Errors: raise domain exceptions in `service.py`; translate to HTTP in `router.py` via `core.errors.handlers`.
- Sessions: server-side in Postgres, opaque session-id cookie (HttpOnly, Secure, SameSite=Lax).

## Conventions — Frontend
- TypeScript strict. No `any`. Use `unknown` + narrowing.
- TanStack Query for server state. Zustand for ephemeral client state. No Redux.
- API client auto-generated from OpenAPI into `frontend/src/shared/api/`. Don't hand-write.
- Tailwind classes only — no inline styles, no styled-components.
- Component tests: Vitest + React Testing Library colocated as `*.test.tsx`. E2E in `frontend/playwright/` for critical journeys only.

## Conventions — Tests
- Pragmatic TDD: write tests alongside the code; must pass before commit.
- Coverage gate: 80% on `backend/app/features/*`, 60% on `frontend/src/features/*`.
- External services (Ollama, Anthropic, Docker, MCP subprocesses) MUST be mocked in unit tests. Integration tests use the sandbox compose stack.
- Pentest tools NEVER run against external targets in tests. Only `sandbox/juice-shop`.

## Workflow
- Vertical slices. One slice = one PR. Slices live in `docs/slices/`.
- Plan-gated: every slice has `docs/slices/slice-NN-*.md` with a Plan section. Wait for human approval before executing.
- Branch naming: `slice-NN-short-name`.
- Commit style: Conventional Commits. One logical change per commit.
- Step-gated (extra confirmation) for risky slices: anything touching auth, single-writer graph process, hash-chain audit, egress friction, approval flow, RAG isolation.

## How to navigate the codebase
- "Where does X live?" → `app/features/<X>/` and `src/features/<X>/`.
- "What's the source of truth for slice plans?" → `docs/slices/PROJECT_PLAN.md`.
- "Why was a decision made?" → `docs/decisions/`.
- "How does single-writer-per-engagement work?" → `docs/decisions/0001-*.md` and `app/features/graph/writer.py`.

## Anti-patterns (do not do these)
- Don't widen `core/` or `shared/` without an ADR.
- Don't write to the graph outside the single-writer process.
- Don't redact data before sending to the LLM. Privacy lives at the engagement toggle and the egress pattern-friction layer.
- Don't add provenance fields to entities — the audit log is the source of truth.
- Don't run pentest tools against anything other than the sandbox in dev/test.
- Don't hand-write API client types on the frontend — regenerate from OpenAPI.

## When stuck
Stop and ask. Don't invent file paths, API shapes, or library APIs. Use the Context7 MCP to check current docs for FastAPI, SQLAlchemy, React, Tailwind, shadcn, Playwright, etc.

## Subagent + skill protocol
See `AGENTS.md` for when to fork subagents. Use skills proactively when their description matches the user's intent.
