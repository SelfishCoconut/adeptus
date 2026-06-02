# Slice 03: Static MCP config + shell-exec server

**Branch**: `slice-03-static-mcp-config-shell-exec`
**GitHub Issue**: #11
**Status**: done
**Risky**: yes

---

## Goal

Expose a static MCP capability registry to the admin UI and execute a shell command end-to-end through the shell-exec MCP server subprocess, proving the stdio transport plumbing works.

## User-visible demo

- Navigate to a new **Admin > MCP Servers** page (accessible only to admins).
- See a table listing the one built-in server: `shell-exec` with its declared capabilities (`shell-exec`, `filesystem-write`, `network`), tool name (`run_command`), weight (`light`), and status (`running` / `stopped`).
- A capability-warning notice reads: "MCP servers run with full system privileges. You are responsible for vetting every server installed here."
- In the **Tool Runner** placeholder panel (bottom pane stub from Slice 00), the admin can open a minimal "Raw shell" form: type a command (e.g. `echo hello`), click **Run**, and see the output (stdout / stderr / exit code) returned inline — proving the full path from browser → FastAPI → MCP subprocess → FastAPI → browser works.
- The tool run is recorded: a `tool_runs` row exists in the DB with `engagement_id`, `tool_name`, `command`, `exit_code`, `output_preview`, `started_at`, `finished_at`.

## Out of scope

- Does NOT implement the full Tool Runner panel UI with presets (Slice 04).
- Does NOT implement concurrency slots, per-target locks, or the FIFO queue (Slice 05).
- Does NOT implement kill switches or the full timeout-confirm UX (Slice 06). Per-request timeout override IS supported in this slice (see Contract and Backend tasks); the kill/extend/wait UX is deferred.
- Does NOT implement nmap, gobuster, or httpx MCP servers (Slice 26).
- Does NOT implement the embedded terminal (xterm.js) (Slice 29).
- Does NOT implement approval-gating for dangerous commands (Slice 16).
- Does NOT implement Docker-based tool execution containers — in this slice the shell-exec server runs in the backend container itself (the full Docker-per-engagement networking model is future scope §6.1).
- Does NOT implement per-engagement Docker networking or proxy config (§6.1 deferred).
- Does NOT add or remove MCP servers at runtime — config is read-only in the UI (static config, admin edits the file and restarts per §7).
- Does NOT implement audit log hash-chaining (Slice 10); the `tool_runs` table acts as plain storage here.

## Requirements traceability

- §6.1 — Tools run inside Docker containers with configurable networking. This slice proves the execution path works; full Docker-per-engagement container isolation is explicitly deferred (see Out of scope). §6.1 is partially addressed — the architecture is correct, isolation is incremental.
- §6.2 (light path) — Light tools bypass the per-target lock and use a dedicated lane that does not consume the heavy concurrency pool. The shell-exec server declares `weight: light`; this slice implements only the light path (no heavy pool yet).
- §6.3 — Large outputs become stored artifacts. This slice enforces a 1 MB hard cap per run and truncates output that exceeds it; the full artifact storage mechanism is Slice 04.
- §6.4 — Generic "shell exec" MCP server as a fallback for anything not yet wrapped. This is the primary deliverable.
- §4 — Engagement membership is explicit per-user per-engagement with no admin bypass. `POST /api/v1/tool-runs` enforces this strictly: even admin-role users must be explicit members of the engagement. The denial is a **404** (not a 403) so non-membership is indistinguishable from a non-existent engagement (§17.1 isolation — see Decisions recorded #4).
- §7 — MCP extensibility model: one server per category; static config file; subprocess over stdio; admin-visible capability flags with a clear privilege warning; no signing/verification; admin is trusted to vet servers.

## Contract

```yaml
openapi: "3.1.0"
info:
  title: Adeptus — Slice 03 delta
  version: "0.3.0"

paths:
  /api/v1/admin/mcp-servers:
    get:
      summary: List all registered MCP servers and their declared capabilities
      tags: [admin, mcp]
      security:
        - sessionCookie: []
      responses:
        "200":
          description: OK
          content:
            application/json:
              schema:
                type: array
                items:
                  $ref: "#/components/schemas/McpServerInfo"
        "403":
          description: Forbidden — caller is not an admin

  /api/v1/tool-runs:
    post:
      summary: Execute a command via the named MCP server and wait for the result
      tags: [tools]
      security:
        - sessionCookie: []
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/ToolRunCreate"
      responses:
        "200":
          description: Command completed (success or non-zero exit code)
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/ToolRunResult"
        "400":
          description: Unknown MCP server or tool name
        "404":
          description: >
            Engagement not found OR caller is not a member of the engagement.
            Non-members and missing engagements are indistinguishable (no admin
            bypass — §4; existence is not disclosed — §17.1). See "Decisions
            recorded" #4: the original 403-for-non-member was changed to 404 at
            security-review time to match the sacrosanct engagement-isolation
            posture.
        "503":
          description: MCP server subprocess is not running

components:
  schemas:
    McpServerInfo:
      type: object
      required: [server_name, status, tools]
      properties:
        server_name:
          type: string
          example: shell-exec
        status:
          type: string
          enum: [running, stopped]
        tools:
          type: array
          items:
            $ref: "#/components/schemas/McpToolDeclaration"

    McpToolDeclaration:
      type: object
      required: [name, weight, capability_flags]
      properties:
        name:
          type: string
          example: run_command
        weight:
          type: string
          enum: [light, heavy]
        capability_flags:
          type: array
          items:
            type: string
          example: ["shell-exec", "filesystem-write"]

    ToolRunCreate:
      type: object
      required: [engagement_id, server_name, tool_name, args]
      properties:
        engagement_id:
          type: string
          format: uuid
        server_name:
          type: string
          example: shell-exec
        tool_name:
          type: string
          example: run_command
        args:
          type: object
          additionalProperties: true
          description: Tool-specific argument map
          example:
            command: "echo hello"
        timeout_seconds:
          type: integer
          minimum: 1
          maximum: 300
          default: 30
          description: >
            Per-request timeout override. Default 30 s. The MCP server kills the
            subprocess and returns a non-zero exit code when the limit is reached.
            Full kill/extend/wait UX is deferred to Slice 06.

    ToolRunResult:
      type: object
      required: [tool_run_id, engagement_id, server_name, tool_name, exit_code, stdout, stderr, started_at, finished_at]
      properties:
        tool_run_id:
          type: string
          format: uuid
        engagement_id:
          type: string
          format: uuid
        server_name:
          type: string
        tool_name:
          type: string
        exit_code:
          type: integer
        stdout:
          type: string
          description: Capped at 1 MB. Truncated output is indicated by a trailing message in stderr.
        stderr:
          type: string
        started_at:
          type: string
          format: date-time
        finished_at:
          type: string
          format: date-time
```

## Data model changes

One new table. No changes to existing tables.

- **`tool_runs`** table:
  - `id` UUID PK
  - `engagement_id` UUID FK → `engagements.id` ON DELETE CASCADE, NOT NULL
  - `server_name` VARCHAR(100) NOT NULL
  - `tool_name` VARCHAR(100) NOT NULL
  - `args` JSONB NOT NULL — full argument map as sent to the MCP server
  - `exit_code` INTEGER (nullable until the run finishes)
  - `stdout` TEXT NOT NULL DEFAULT ''
  - `stderr` TEXT NOT NULL DEFAULT ''
  - `started_at` TIMESTAMPTZ NOT NULL DEFAULT now()
  - `finished_at` TIMESTAMPTZ (nullable — null while in flight)
  - Index: `(engagement_id, started_at DESC)` — supports listing runs per engagement in order

No `weight` column on `tool_runs` — weight is declared in the static MCP manifest and looked up at runtime, not stored per run (avoids schema churn when a server re-declares weight).

## Backend tasks

1. **[S]** Create `app/features/mcp/` feature folder with `__init__.py`, `schemas.py`, `models.py`, `service.py`, `repository.py`, `router.py`, `tests/`. Add `McpServerInfo`, `McpToolDeclaration`, `ToolRunCreate`, `ToolRunResult` Pydantic schemas. `ToolRunCreate` includes `timeout_seconds: int = 30` (1–300 range, validated).

2. **[S]** Write `app/features/mcp/registry.py`: loads `MCP_CONFIG_PATH` (env var, default `/etc/adeptus/mcp.yaml`) on startup; parses server entries (name, command, args, declared tools with weight + capability_flags); exposes `get_registry() -> dict[str, McpServerConfig]` singleton. Write unit tests (mock YAML on disk).

3. **[M]** Write `app/features/mcp/subprocess_manager.py`: on-startup, for each configured server spawn `asyncio.create_subprocess_exec` with stdio transport; keep a handle per server name; expose `send_tool_call(server_name, tool_name, args, timeout_seconds) -> McpRawResult` (JSON-RPC 2.0 request/response over stdin/stdout); handle subprocess death gracefully (status → `stopped`). Write unit tests with mocked subprocess.

4. **[S]** Write `app/features/mcp/models.py`: `ToolRun` SQLAlchemy model matching the `tool_runs` table schema above.

5. **[S]** Write `app/features/mcp/repository.py`: `create_tool_run`, `update_tool_run_result`, `list_tool_runs_for_engagement`. Write unit tests with mock async session.

6. **[M]** Write `app/features/mcp/service.py`: `list_servers() -> list[McpServerInfo]` (from registry + subprocess status); `execute_tool_run(db, engagement_id, server_name, tool_name, args, timeout_seconds, user_id) -> ToolRunResult` — validates engagement membership via explicit member check (no admin bypass — §4); inserts a `ToolRun` row; calls `subprocess_manager.send_tool_call`; updates the row with results; returns result. Write unit tests mocking registry and subprocess_manager, including a test that confirms admin users without membership receive `NotMember`.

7. **[M]** Write `app/features/mcp/router.py`: `GET /api/v1/admin/mcp-servers` (admin-only, returns list from `service.list_servers()`); `POST /api/v1/tool-runs` (requires authenticated session AND explicit engagement membership — admin role does NOT bypass the membership check per §4). Translate domain exceptions (`McpServerNotFound`, `McpServerDown`, `EngagementNotFound`, `NotMember`) to correct HTTP codes. Write router-level tests with mocked service, including a test asserting a 403 for an admin who is not an engagement member.

8. **[S]** Wire `mcp_router` into `app/main.py`.

9. **[S]** Add Alembic migration (via `write-alembic-migration` skill) creating the `tool_runs` table.

10. **[S]** Write the `mcp-servers/shell-exec/` Python MCP server: reads JSON-RPC 2.0 messages from stdin, dispatches `run_command` tool (args: `command: str`, optional `timeout_seconds: int = 30`; client override is forwarded through by the backend service). Executes via `asyncio.create_subprocess_shell`; captures stdout/stderr/exit code; enforces `MAX_OUTPUT_BYTES = 1_048_576` (1 MB hard cap) — if either stdout or stderr exceeds this, truncate at 1 MB and append `"\n[output truncated at 1 MB]"` to the affected stream; writes JSON-RPC response to stdout. Include `mcp-servers/shell-exec/manifest.yaml` declaring tool name, weight `light`, capability_flags `["shell-exec", "filesystem-write"]`. Unit tests with mocked subprocess, including a test for truncation at the 1 MB boundary.

11. **[S]** Add `mcp.yaml` default config (committed at `mcp-servers/config/mcp.yaml`) registering the shell-exec server; update `docker-compose.yml` to mount it and set `MCP_CONFIG_PATH` env var; update `.env.example`.

## Frontend tasks

1. **[S]** Run `make generate-api` after backend contract is deployed to regenerate `frontend/src/shared/api/` with the new MCP endpoints and types (including the `timeout_seconds` field on `ToolRunCreate`).

2. **[M]** Add `frontend/src/features/mcp/api.ts`: TanStack Query hooks — `useListMcpServers()` (admin-only, queries `GET /api/v1/admin/mcp-servers`); `useExecuteToolRun()` (mutation, `POST /api/v1/tool-runs`).

3. **[M]** Add `frontend/src/features/mcp/components/McpServerTable.tsx` + `McpServerTable.test.tsx`: renders the server list as a table (server name, tools, weight, capability flags, status badge); includes the capability-warning notice per §7; admin-only gate (redirect/hide for non-admins). Test: renders one server row with correct data; non-admin sees nothing.

4. **[S]** Add `frontend/src/features/admin/pages/McpServersPage.tsx`: thin page wrapper that renders `McpServerTable`. Wire a route `/admin/mcp-servers` guarded by `role === "admin"` in the React Router config.

5. **[M]** Add `frontend/src/features/mcp/components/RawShellForm.tsx` + `RawShellForm.test.tsx`: a minimal form — `command` text input, an optional `timeout_seconds` number input (default 30, range 1–300), an `engagement_id` selector (populated from the current engagement context), **Run** button. On submit calls `useExecuteToolRun()`; renders result (stdout, stderr, exit code) in a `<pre>` block; shows a loading state while in flight; shows a notice if output was truncated (detect `"[output truncated at 1 MB]"` suffix). Test: submit calls mutation with correct args including timeout; result renders; error state renders.

6. **[S]** Embed `RawShellForm` as a placeholder panel in the workspace bottom pane (replace or add beside the existing console placeholder from Slice 00) — only visible when an engagement is selected.

## Test plan

- **Unit (backend)**:
  - `test_registry.py` — valid YAML loads correctly; missing file raises `ConfigError`; malformed YAML raises `ConfigError`; missing mandatory fields raise `ConfigError`.
  - `test_subprocess_manager.py` — `send_tool_call` serializes JSON-RPC request to stdin with `timeout_seconds` forwarded; response parsed correctly; subprocess death → `McpServerDown`; timeout → `McpServerDown` with message.
  - `test_mcp_repository.py` — `create_tool_run` / `update_tool_run_result` / `list_tool_runs_for_engagement` with mock async session.
  - `test_mcp_service.py` — `list_servers` aggregates registry + subprocess status; `execute_tool_run` happy path; unknown server raises `McpServerNotFound`; down server raises `McpServerDown`; non-member raises `NotMember`; admin user who is not an explicit engagement member raises `NotMember` (§4 no-bypass rule).
  - `test_mcp_router.py` — `GET /admin/mcp-servers` returns 403 for non-admin; returns 200 for admin; `POST /tool-runs` returns 200 on success; 400 on unknown server; 403 on non-member; 403 on admin-but-not-member; 503 on down server.
  - `test_shell_exec_server.py` (in `mcp-servers/shell-exec/tests/`) — sends `run_command` JSON-RPC message; receives correct response; non-zero exit code returned (not raised); unknown tool name returns JSON-RPC method-not-found error; output exceeding 1 MB is truncated with the sentinel suffix; `timeout_seconds` override is honoured.
  - Coverage gate: ≥80% on `app/features/mcp/`.

- **Unit (frontend)**:
  - `McpServerTable.test.tsx` — renders server list; capability warning shown; non-admin: component hidden.
  - `RawShellForm.test.tsx` — submit fires mutation with `timeout_seconds`; loading spinner shown; stdout/stderr/exit code rendered; truncation notice shown when sentinel present; error banner on 503.
  - Coverage gate: ≥60% on `src/features/mcp/`.

- **Integration**:
  - `test_mcp_integration.py` — spins up the real shell-exec subprocess; calls `POST /api/v1/tool-runs` with `{"command": "echo hello"}`; asserts `exit_code == 0`, `stdout == "hello\n"`; asserts a `tool_runs` row exists in the DB. Uses test compose stack.

- **E2E**: Skip — the RawShellForm is a developer/admin debug surface, not a user-critical journey. The integration test covers the round-trip adequately.

## Acceptance criteria

- `make test` passes with no failures and coverage gates met.
- `make lint` passes (ruff, mypy, eslint, tsc --noEmit).
- `make dev` brings up the stack; logging in as admin, navigating to `/admin/mcp-servers` shows the shell-exec server table with the capability warning.
- Submitting `echo "slice 03 works"` in the Raw Shell form returns `exit_code: 0` and `stdout: "slice 03 works\n"` in the UI.
- A `tool_runs` row for the above run exists in the DB (verifiable via `psql` or the `/api/v1/tool-runs` list if implemented, but at minimum via direct DB inspection).
- `GET /api/v1/admin/mcp-servers` returns 403 when called as a non-admin user.
- `POST /api/v1/tool-runs` returns 404 when called by an admin user who is not an explicit member of the specified engagement (confirming no admin bypass per §4; the denial is a 404 rather than a 403 so engagement existence is not disclosed per §17.1 — see Decisions recorded #4).
- Submitting a command with an explicit `timeout_seconds: 5` override in the Raw Shell form passes that value through to the MCP server (confirm via backend log or integration test; full kill/extend/wait UX deferred to Slice 06).
- Submitting a command whose combined output exceeds 1 MB results in a truncated response with the `"[output truncated at 1 MB]"` sentinel in the affected stream and the truncation notice visible in the UI.

## Risks

- **Risk 1 — Unrestricted shell access**: The `run_command` tool can execute arbitrary shell commands with the privileges of the backend container process. Mitigation: (a) the tool is only callable by authenticated, explicit engagement-member users — not anonymously and not by role alone; (b) the capability warning in the admin UI is explicit; (c) Docker's container boundary provides OS-level isolation between the backend and the host; (d) a future slice (Slice 29) will layer proper PTY/terminal plumbing with scope controls. The `command` argument MUST NOT be filtered or modified — that would be silent redaction, which violates §17.2 and §5.5. The human-in-the-loop approval for dangerous commands (§5.2) is implemented in Slice 16; until then, any engagement member can run arbitrary commands. Document this clearly in the admin UI.

- **Risk 2 — JSON-RPC framing over stdio**: The MCP stdio transport has no built-in length prefix. If a tool produces very large stdout (e.g. a recursive `ls`), the subprocess manager must buffer lines carefully and not deadlock. Mitigation: read stdout line-by-line; the shell-exec server writes exactly one JSON-RPC response object per line (newline-delimited JSON); enforce `MAX_OUTPUT_BYTES = 1_048_576` (1 MB hard cap, decided) in the shell-exec server and truncate with a sentinel message appended to the affected stream if exceeded. (§6.3 specifies large outputs as artifacts — that full mechanism is Slice 04; the 1 MB cap is the settled limit for this slice.)

- **Risk 3 — Subprocess lifecycle on backend restart**: If the FastAPI process restarts, MCP subprocess handles are lost. Mitigation: the subprocess manager re-spawns all configured servers during the FastAPI `lifespan` startup hook. Any in-flight `tool_runs` rows whose `finished_at` is NULL at startup are marked with `exit_code = -1`, `stderr = "Backend restarted before completion"` (aligned with §13 crash recovery semantics, which are formalized in Slice 38).

- **Risk 4 — Command injection through args**: The shell-exec server runs the `command` argument via `asyncio.create_subprocess_shell`. This is intentional — it IS a shell-exec tool. The risk is that the backend service blindly proxies user-supplied strings. Mitigation: the API requires an authenticated session with explicit engagement membership (no admin bypass); document in the admin UI that this capability is for trusted pentester use only; no sanitization is applied (sanitization would be false safety and would break legitimate use).

## Open questions for the human

None.

## Decisions recorded

The following questions were raised during planning and answered before implementation:

1. **Output size cap**: 1 MB (`MAX_OUTPUT_BYTES = 1_048_576`) hard cap per tool run. Output exceeding this limit is truncated and a `"[output truncated at 1 MB]"` sentinel is appended to the affected stream. The full large-output artifact mechanism (§6.3) lands in Slice 04.

2. **Admin bypass on tool runs**: No bypass. `POST /api/v1/tool-runs` requires explicit engagement membership for all callers including admins (§4). An admin who is not an explicit member of the engagement is denied. (Originally specified as a 403; changed to **404** at security-review time — see #4.) This is verified by a dedicated test case in both unit and acceptance criteria.

3. **`run_command` timeout**: Default 30 s; client may supply `timeout_seconds` (1–300) per request to override. The MCP server kills the subprocess and returns a non-zero exit code when the limit is reached. Full kill/extend/wait UX (Slice 06) is deferred.

4. **Non-member denial is 404, not 403 (security-review amendment)**: The security review flagged that returning a distinct 403 for "you are not a member" while returning 404 for "engagement does not exist" leaks engagement existence to any authenticated user — a violation of the sacrosanct engagement-isolation invariant (§17.1; requirements.md "Engagement isolation is sacrosanct"). The engagements feature already enforces this by routing every single-engagement read through `get_engagement_for_member`, which returns `None` (→ 404) for non-members. `execute_tool_run` was changed to use the same fused chokepoint: both "missing" and "not a member" collapse to `EngagementNotFound` → 404. §4 (no admin bypass) is unchanged — an admin without a member row is still denied; only the status code changed (403 → 404). This supersedes the 403 in the original Contract and acceptance criteria above.

5. **`network` capability flag (security-review amendment)**: The `shell-exec` server's `run_command` executes arbitrary shell commands, which can open network connections. The manifest originally declared only `shell-exec` and `filesystem-write`; the security review flagged that omitting `network` misrepresents the capability surface that the admin UI and future tooling (Slice 26) rely on. `network` was added to both `mcp-servers/shell-exec/manifest.yaml` and `mcp-servers/config/mcp.yaml` (requirements.md §7 lists `network` as a standard flag).

## Security review required?

Yes — this slice touches MCP subprocess spawning and direct shell-exec capability. It must go through the security reviewer at finish-slice time before merge.

## Progress

(The stop-checkpoint hook and compact-handoff skill append here. Leave empty at planning time.)
- 2026-05-31T20:02:25Z — f764cf4 Merge pull request #10 from SelfishCoconut/slice-02-privacy-mode-banner
- 2026-06-01T19:28:51Z — c920f5d feat(slice-03): add shell-exec MCP server with 1MB output cap
- 2026-06-01T19:29:54Z — c920f5d feat(slice-03): add shell-exec MCP server with 1MB output cap
- 2026-06-01T20:04:12Z — c6af393 test(slice-03): add MCP integration test and fix missing db.commit in router
- 2026-06-02T18:09:16Z — ac393d3 chore(slice-03): mark slice 03 done in PROJECT_PLAN
- 2026-06-02T18:18:18Z — ac393d3 chore(slice-03): mark slice 03 done in PROJECT_PLAN
- 2026-06-02T18:30:42Z — ac393d3 chore(slice-03): mark slice 03 done in PROJECT_PLAN
