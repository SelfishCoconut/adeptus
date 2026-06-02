# 0008. MCP servers are a separately-gated code tree

Date: 2026-06-01
Status: Accepted

## Context

Slice 03 introduces the first code under `mcp-servers/` — the `shell-exec`
server, a standalone Python process the backend talks to over stdio (JSON-RPC
2.0). Per the §7 extensibility model and `docs/architecture.md`, every MCP tool
category ships as its own stdio subprocess; Slice 26 will add nmap, gobuster,
and httpx servers, and the `add-mcp-server` skill scaffolds new ones from
`mcp-servers/_template/`.

Until this slice every quality gate — `make lint`, `make test-backend`,
pre-commit (ruff/mypy `files: ^backend/`), and the CI `backend` job
(`working-directory: backend`) — was scoped to `backend/`. The shell-exec
server and its 18 tests were therefore invisible to all of them: unlinted,
un-typechecked, and excluded from coverage. Enabling a gate over the new tree
immediately surfaced 14 mypy errors and 56% coverage in code that executes
**arbitrary shell commands** — exactly the code that must not ship unverified.

We had to decide how `mcp-servers/` enters the gates, and that choice sets the
pattern every future server inherits.

## Decision

Treat `mcp-servers/` as its own first-class, independently-gated component,
parallel to `backend/` and `frontend/` — not as an appendage of the backend.

- `mcp-servers/pyproject.toml` owns the tree's tool config: its own ruff
  ruleset (mirroring backend: line-length 100, `E,W,F,I,B,UP,ASYNC,ANN`), its
  own mypy config, its own pytest + coverage config, and a dev dependency group
  (`pytest`, `pytest-asyncio`, `pytest-cov`, `mypy`, `ruff`). Servers themselves
  stay **stdlib-only**; the dev deps exist only to lint and test them. The
  project is non-installable (`tool.uv.package = false`) — it is a collection of
  scripts, not a library. `uv.lock` is committed so CI runs `uv sync --frozen`.
- `make test` gains `test-mcp-servers` (pytest + `--cov-fail-under=80`); `make
  lint` and `make format` gain `mcp-servers` ruff/mypy lines.
- A dedicated CI job `mcp-servers` runs lint + typecheck + test, mirroring the
  `backend` job.
- pre-commit ruff/ruff-format widen to `^(backend|mcp-servers)/`, and a second
  mypy hook covers `^mcp-servers/.*\.py$` with the tree's own config.

Coverage gate for `mcp-servers/` is **80%**, matching the backend feature gate.

## Consequences

**Positive**
- Security-sensitive subprocess code is linted, type-checked, and coverage-gated
  on every PR — the gap that let shell-exec ship at 56% coverage is closed.
- Each server can later gain its own third-party dependencies without polluting
  the backend's environment; the boundary matches the runtime reality (these are
  separate processes, not backend imports).
- `add-mcp-server` and Slice 26 have a concrete pattern to scaffold into.

**Negative**
- More config surface (a fourth toolchain) and a second `uv.lock` to keep in
  sync with tool versions.
- The new CI `mcp-servers` job is **not yet in the master branch-protection
  required-checks set** (which lists backend, frontend, secrets). Until it is
  added there, a red `mcp-servers` job will not block `--auto` merges. Adding it
  to the ruleset is a follow-up at PR time.

**Neutral**
- Test discovery currently relies on each server's test inserting its own dir on
  `sys.path` and importing a bare `server` module. With one server this is fine;
  when a second server lands, the shared `server` module name will collide and
  the import strategy (or per-server pytest invocation) must be revisited.

## Alternatives considered

- **Fold `mcp-servers/` into the backend run** (extend backend ruff/mypy/pytest
  to `../mcp-servers`): one toolchain, cheapest now, and works *only because*
  current servers are stdlib-only. It quietly couples every future server to the
  backend's Python/dependency env and blurs the subprocess boundary; the first
  server needing its own dep forces a migration to this ADR's structure anyway.
- **Defer with a follow-up issue**: fastest to PR, but sets the precedent "MCP
  servers ship unverified" and misses this slice's own acceptance criterion that
  `make test` runs the server tests. Unacceptable for arbitrary-shell-exec code.
