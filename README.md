# Adeptus

Locally-deployable AI-assisted penetration testing platform.

> **Status**: scaffolded, not yet implemented. The first vertical slice (walking skeleton) has not been built. See `docs/slices/PROJECT_PLAN.md` for the full plan.

## What this is

A multi-user pentest workbench for small teams that pairs each pentester with a private AI conversation while maintaining a shared engagement-scoped knowledge graph. Designed for engagement isolation, human-in-the-loop on dangerous actions, and extensibility via MCP servers. Read `docs/requirements.md` for the locked spec.

## How it's built

This project uses **Claude Code** with strict vertical-slice discipline. Every feature ships end-to-end (UI + API + DB + tests) as one PR. Subagents do investigation and review in isolated context windows; skills automate the repeatable workflows (planning, slice execution, finishing); hooks enforce non-negotiables deterministically.

Read `CLAUDE_CODE_ARCHITECTURE.md` for the full operational architecture.

## Day 0 setup

```bash
# 1. Drop your locked requirements doc in place
$EDITOR docs/requirements.md   # replace the placeholder

# 2. Install pre-commit hooks
pre-commit install
pre-commit install --hook-type commit-msg

# 3. Make Claude Code hooks executable
chmod +x .claude/hooks/*.sh

# 4. Start Claude Code in this directory
claude

# 5. Install plugins (one-time, user scope)
> /plugin install feature-dev@claude-plugins-official
> /plugin install code-review@claude-plugins-official
> /plugin install frontend-design@claude-plugins-official
> /plugin install commit-commands@claude-plugins-official

# 6. Add MCP servers (one-time)
> /mcp add context7
> /mcp add postgres
> /mcp add playwright
> /mcp add github

# 7. Generate the project plan from your requirements
> use the plan-project skill

# 8. Pick the next (first) slice and plan it
> use pick-next-slice
# Claude picks slice 00 (walking skeleton), delegates to slice-planner subagent.
# slice-planner writes docs/slices/slice-00-walking-skeleton.md
# You read it, answer the open questions, approve the plan.

# 9. Start the slice
> use start-slice 00
# Plan-gated execution begins. Claude waits for your "y" before writing code.
```

## The per-slice loop

```
pick-next-slice → slice-planner → HUMAN approves → start-slice → /clear
  → contract-first (OpenAPI delta first) → implementer (one task at a time)
  → test-writer (coverage to gate) → code-reviewer
  → security-reviewer (if risky) → finish-slice → HUMAN merges PR
  → compact-handoff → back to start
```

See §9 of `CLAUDE_CODE_ARCHITECTURE.md` for details.

## Repository layout

```
.claude/        — Claude Code config: agents, skills, hooks, settings.json
backend/        — FastAPI; one folder per feature in app/features/
frontend/       — Vite + React + TS; mirrors backend feature folders
mcp-servers/    — Internal MCP servers (one per tool category)
sandbox/        — Juice Shop sandbox compose for safe pentest-tool testing
docs/
  requirements.md      — locked spec (authoritative)
  architecture.md      — living architecture
  decisions/           — ADRs
  slices/              — vertical slice specs + PROJECT_PLAN.md
  runbooks/            — operational how-tos
.github/workflows/     — CI (lint, typecheck, test, secret-scan)
```

## Commands

See `make help` or `CLAUDE.md` for the full list. The essentials:

- `make dev` — full stack up with hot reload
- `make test` — lint + typecheck + all tests + coverage gate
- `make sandbox` — bring up Juice Shop at http://localhost:3000

## License

Apache-2.0 (see `LICENSE` and `docs/decisions/0005-license-apache-2.md`).
