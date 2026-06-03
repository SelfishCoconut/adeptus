# Architecture

The full operational architecture for how Claude Code builds Adeptus is in `CLAUDE_CODE_ARCHITECTURE.md` at the repo root (the document generated alongside this scaffold). This file is the *living* architecture summary of the system itself — kept current by the `docs-writer` subagent as slices land.

## High level

```
                    ┌─────────────────────────┐
                    │  React SPA (Vite)       │
                    │  3-pane workspace       │
                    │  TanStack Query + WS    │
                    └────────────┬────────────┘
                                 │  HTTPS + WS
                    ┌────────────▼────────────┐
                    │  FastAPI (async)        │
                    │  features/* per slice   │
                    │  core/ deps, errors, db │
                    └─┬───────┬──────────┬────┘
                      │       │          │
              ┌───────▼──┐ ┌──▼───┐  ┌───▼────────┐
              │ Postgres │ │Ollama│  │ MCP servers│
              │ +pgvector│ │ (LLM)│  │ (stdio)    │
              │ +sessions│ │      │  │  - shell   │
              │ +audit   │ │      │  │  - nmap    │
              │ +graph   │ │      │  │  - http    │
              └──────────┘ └──────┘  │  - burp    │
                                     └────────────┘
```

## Cross-cutting components

- **Single-writer per engagement** (ADR-0001): each active engagement has one process owning the in-memory NetworkX graph; all writes serialize through it.
- **Hash-chained audit log**: every dangerous action, approval, login, and graph edit; tamper-evident chain (see §14 of requirements).
- **Pattern-friction egress** (engagement-level): when cloud is enabled and a message looks like it contains a secret, present a confirmation modal — never silently redact.
- **Server-side sessions** (ADR-0003): opaque cookie ID, session table in Postgres, instant revocation.
- **RAG isolation by SQL filter**: every vector query has `WHERE engagement_id = ?`; per-engagement uploads and global curated KB are queried separately.
- **In-process feature event seam** (ADR-0009): when one feature owns config another feature reacts to, the owner emits and the consumer subscribes at the composition root, keeping the dependency one-directional (e.g. an engagement slot-limit change notifies the mcp concurrency manager).

## Where to read more

- For *why* a decision was made: `docs/decisions/`
- For *what* is being built next: `docs/slices/PROJECT_PLAN.md`
- For *how* a specific feature works: `app/features/<name>/` and the slice spec that built it
- For *how Claude Code itself builds the project*: `CLAUDE_CODE_ARCHITECTURE.md`
