# Slice 04: Tool runner panel (light tools only)

**Branch**: `slice-04-tool-runner-panel`
**GitHub Issue**: #13
**Status**: in-progress
**Risky**: no

---

## Goal

Expose a structured Tool Runner panel in the workspace bottom pane that lets a user select httpx, fill its arguments via a preset form, run it against the sandbox, and see live output streamed into the console.

## User-visible demo

- Open an active engagement in the workspace.
- The bottom pane shows a **Tool Runner** tab alongside any existing output.
- Select the **httpx** tool from a dropdown list (populated from the MCP registry).
- Choose a preset (`quick`, `full`) or fill arguments manually (target URL, timeout).
- Click **Run**.
- The bottom pane console streams stdout and stderr lines in near-real-time via WebSocket.
- After the run completes, the console shows the exit code and a "Completed" badge.
- The run appears in a **Run history** list within the bottom pane; clicking an old run replays its stored output.
- A sandbox guard notice is shown: the target field pre-fills with `http://localhost:3000` (Juice Shop) in dev and the field is validated; submitting any non-sandbox target in dev mode shows a warning.

## Out of scope

- Does NOT implement the concurrency slot model or per-target lock (Slice 05).
- Does NOT implement per-tool kill switches or the timeout-confirm UX (kill / extend / wait) (Slice 06).
- Does NOT implement nmap or gobuster MCP servers (Slice 26).
- Does NOT implement the embedded xterm.js terminal (Slice 29).
- Does NOT implement approval-gating for dangerous commands (Slice 16).
- Does NOT implement large-output artifact storage with a separate viewer (§6.3 full artifact mechanism) — the 1 MB cap from Slice 03 still applies; this slice adds streaming of output up to that cap.
- Does NOT implement file uploads or wordlist attachment (Slice 28).
- Does NOT implement the AI suggesting tool arguments (that requires Slice 11 + LLM integration).
- Does NOT add the httpx tool to the graph auto-population (graph writes land in Slice 07).
- Does NOT implement per-engagement Docker networking or proxy config (§6.1 deferred).

## Requirements traceability

- §6.2 (light path, manual tool use) — structured "tool runner" panel where users pick a tool, fill args, and run it; light tools use the dedicated light-lane that does not consume the heavy pool. This slice implements the panel and confirms the light path for httpx.
- §6.3 (output handling) — large outputs capped (1 MB from Slice 03); raw output displayed in the console; streaming output to bottom pane.
- §6.4 — httpx is in the v1 tool set. This slice wires the dedicated httpx MCP server (separate from the generic shell-exec server) with a structured argument schema and preset support.
- §7 — MCP extensibility: one server per tool category; static config; subprocess over stdio; weight and capability_flags declared in the manifest.
- §11.2 — bottom pane is the tool output console; this slice makes it functional.
- §11.4 (partial) — the target field supports user-supplied values; file attachment for wordlists is out of scope until Slice 28.
- §15 — sandbox engagement against juice-shop for smoke tests; the sandbox guard enforces this in dev/test.

## Contract

```yaml
openapi: "3.1.0"
info:
  title: Adeptus — Slice 04 delta
  version: "0.4.0"

paths:
  /api/v1/tool-runs:
    get:
      summary: List tool runs for an engagement (paginated, newest first)
      tags: [tools]
      security:
        - sessionCookie: []
      parameters:
        - name: engagement_id
          in: query
          required: true
          schema:
            type: string
            format: uuid
        - name: limit
          in: query
          schema:
            type: integer
            default: 20
            minimum: 1
            maximum: 100
        - name: cursor
          in: query
          description: Opaque cursor from previous response for keyset pagination
          schema:
            type: string
      responses:
        "200":
          description: OK
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/ToolRunPage"
        "404":
          description: Engagement not found or caller is not a member

  /api/v1/tool-runs/{tool_run_id}:
    get:
      summary: Fetch a single tool run result
      tags: [tools]
      security:
        - sessionCookie: []
      parameters:
        - name: tool_run_id
          in: path
          required: true
          schema:
            type: string
            format: uuid
      responses:
        "200":
          description: OK
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/ToolRunResult"
        "404":
          description: Tool run not found or caller is not a member of its engagement

  /api/v1/mcp/tools:
    get:
      summary: >
        List all tools available across all running MCP servers, enriched with
        their preset definitions. Used by the tool runner panel to populate the
        tool selector and preset dropdowns.
      tags: [tools, mcp]
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
                  $ref: "#/components/schemas/ToolDescriptor"

websockets:
  /ws/tool-runs/{tool_run_id}:
    description: >
      WebSocket endpoint. After a POST /api/v1/tool-runs returns (with status 202),
      the client connects here to receive streamed output chunks for the run.
      Each message is a JSON object with type "stdout" | "stderr" | "done" | "error".
      The "done" message includes exit_code and finished_at.
      Authentication: the session cookie must be present on the upgrade request.
      Authorization: caller must be a member of the engagement that owns this run.

components:
  schemas:
    ToolRunCreate:
      description: >
        Extended from Slice 03. Now also supports an async/streaming mode
        (async_mode: true) where the server responds 202 immediately and output
        is consumed via WebSocket. Synchronous mode (default) is unchanged.
      type: object
      required: [engagement_id, server_name, tool_name, args]
      properties:
        engagement_id:
          type: string
          format: uuid
        server_name:
          type: string
          example: httpx
        tool_name:
          type: string
          example: run_httpx
        args:
          type: object
          additionalProperties: true
        timeout_seconds:
          type: integer
          minimum: 1
          maximum: 300
          default: 30
        async_mode:
          type: boolean
          default: false
          description: >
            When true the endpoint responds 202 with a partial ToolRunResult
            (finished_at null, stdout/stderr empty). Output is streamed via
            the WebSocket endpoint.

    ToolRunResult:
      type: object
      required:
        [tool_run_id, engagement_id, server_name, tool_name, exit_code, stdout,
         stderr, started_at, finished_at]
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
          nullable: true
          description: null while the run is still in progress
        stdout:
          type: string
          description: Full output (capped at 1 MB). Empty string while streaming.
        stderr:
          type: string
        started_at:
          type: string
          format: date-time
        finished_at:
          type: string
          format: date-time
          nullable: true

    ToolRunPage:
      type: object
      required: [items, next_cursor]
      properties:
        items:
          type: array
          items:
            $ref: "#/components/schemas/ToolRunResult"
        next_cursor:
          type: string
          nullable: true

    ToolDescriptor:
      type: object
      required: [server_name, tool_name, weight, capability_flags, presets, arg_schema]
      properties:
        server_name:
          type: string
          example: httpx
        tool_name:
          type: string
          example: run_httpx
        weight:
          type: string
          enum: [light, heavy]
        capability_flags:
          type: array
          items:
            type: string
        presets:
          type: array
          description: Named presets the user can choose from
          items:
            $ref: "#/components/schemas/ToolPreset"
        arg_schema:
          type: object
          description: >
            JSON Schema object describing the args for this tool. Used by the
            frontend to render a dynamic form.
          additionalProperties: true

    ToolPreset:
      type: object
      required: [name, args]
      properties:
        name:
          type: string
          example: quick
        description:
          type: string
        args:
          type: object
          additionalProperties: true

    WebSocketOutputChunk:
      type: object
      required: [type]
      properties:
        type:
          type: string
          enum: [stdout, stderr, done, error]
        data:
          type: string
          description: Present for stdout and stderr types
        exit_code:
          type: integer
          description: Present for done type
        finished_at:
          type: string
          format: date-time
          description: Present for done type
        message:
          type: string
          description: Present for error type
```

## Data model changes

No new tables. Changes to existing `tool_runs` table only.

- **`tool_runs`** table additions (additive migration, all nullable or with defaults so existing rows are unaffected):
  - `preset_name` VARCHAR(100) — the preset the user selected (null for custom/manual runs)
  - `status` VARCHAR(20) NOT NULL DEFAULT `'completed'` — values: `'running'` | `'completed'` | `'failed'` | `'timed_out'`; allows the history list to show in-progress runs. Existing rows default to `'completed'`.

- **`mcp_servers`** config table: no DB changes. Tool descriptors including presets and arg schemas come from the static YAML config (parsed at startup by the registry, as in Slice 03). Presets are declared in the MCP server's manifest and served through the new `GET /api/v1/mcp/tools` endpoint.

Index note: the existing `(engagement_id, started_at DESC)` index on `tool_runs` is sufficient for the paginated history list.

## Backend tasks

1. **[S]** Add `preset_name` and `status` columns to `tool_runs` via Alembic migration (via `write-alembic-migration` skill). Update the `ToolRun` SQLAlchemy model in `app/features/mcp/models.py`.

2. **[S]** Extend `app/features/mcp/schemas.py`: add `ToolPreset`, `ToolDescriptor`, `ToolRunPage`, and `WebSocketOutputChunk` Pydantic models. Extend `ToolRunCreate` with `async_mode: bool = False` and `preset_name: str | None = None`. Extend `ToolRunResult` with `status` and `preset_name` fields.

3. **[S]** Write `mcp-servers/httpx/` Python MCP server: reads JSON-RPC 2.0 from stdin; dispatches `run_httpx` tool. Accepted args: `target: str` (required), `flags: list[str] = []`, `timeout_seconds: int = 30`. Executes `httpx <flags> <target>` via `asyncio.create_subprocess_exec` (not shell=True — no shell injection). Enforces `MAX_OUTPUT_BYTES = 1_048_576` (same cap as shell-exec from Slice 03). Writes progress lines to stdout as newline-delimited JSON chunks (type `stdout` / `stderr`) followed by a final `done` chunk. Include `mcp-servers/httpx/manifest.yaml` declaring: tool name `run_httpx`, weight `light`, capability_flags `["network"]`, presets (`quick`: `{flags: ["-sc", "-title"]}`, `full`: `{flags: ["-sc", "-title", "-tech-detect", "-follow-redirects"]}`), and a JSON Schema for `args`. Write unit tests (mocked subprocess).

4. **[M]** Add `GET /api/v1/mcp/tools` to `app/features/mcp/router.py`: aggregates descriptors from all running MCP servers via the registry (each server's manifest includes `presets` and `arg_schema`); returns a flat list of `ToolDescriptor`. No auth beyond logged-in session (any engagement member can see available tools). Write unit tests with mocked registry.

5. **[M]** Add `GET /api/v1/tool-runs` (list) and `GET /api/v1/tool-runs/{tool_run_id}` (single) to `app/features/mcp/router.py`. Both require the caller to be an explicit member of the relevant engagement (same 404-for-non-member pattern as Slice 03 §4 decision). Add `list_tool_runs_for_engagement` with keyset pagination to `app/features/mcp/repository.py`. Write unit tests for repository and router layers.

6. **[M]** Extend `app/features/mcp/service.py` with async streaming support: when `async_mode=True`, `execute_tool_run` sets `status='running'`, inserts the row, and launches the MCP call in a `asyncio.create_task`. The module-level pub/sub entry per `tool_run_id` holds **both** an `asyncio.Queue` of live subscribers' channels **and** a retained `list` of all chunks emitted so far (the replay buffer). A new `broadcast_tool_run_output(tool_run_id, chunk)` function appends each chunk to the replay buffer and posts it to live subscribers. On completion, updates the row (`status`, `exit_code`, `stdout`, `stderr`, `finished_at`), posts a `done` chunk, and discards the in-process buffer for that run. Write unit tests with mocked subprocess_manager, including a test that verifies chunks are enqueued, the replay buffer accumulates, and the DB row is updated on completion.

7. **[M]** Add `GET /ws/tool-runs/{tool_run_id}` WebSocket endpoint in `app/features/mcp/router.py` using FastAPI's `WebSocket`. On connect: verify session cookie; verify caller is a member of the engagement that owns `tool_run_id` (404 close if not). **First replay any buffered chunks accumulated so far for this `tool_run_id` (so a mid-run reconnect shows continuous output), then subscribe** to the in-process channel for new chunks; forward chunks as JSON messages to the client; close cleanly on `done` or `error`. If the run already completed (no live buffer), fall back to the stored `stdout`/`stderr` on the row and send a synthetic `done`. Write unit tests with mocked WebSocket and queue, including a reconnect-replay case.

8. **[S]** Register httpx MCP server in `mcp-servers/config/mcp.yaml` and add an `httpx` install step to the backend image's `Dockerfile` (decision 2 — in-image, not a sidecar) so the binary is available to the MCP subprocess. Write an integration test that confirms `httpx --version` exits 0 inside the container.

9. **[S]** Add sandbox guard to `app/features/mcp/service.py`: when the `ADEPTUS_ENV` env var is `dev` or `test`, validate that the `target` argument in `run_httpx` args (and any `command` in `run_command`) is within the allowed sandbox host list (`localhost`, `127.0.0.1`, `juice-shop` Docker hostname). Raise `SandboxGuardViolation` (→ HTTP 403 with a descriptive message) if the target is outside this list. The guard is a no-op when `ADEPTUS_ENV=production`. Write unit tests for both environments.

## Frontend tasks

1. **[S]** Run `make generate-api` after the backend contract changes land to regenerate `frontend/src/shared/api/` with the new endpoints, the extended `ToolRunCreate` schema, `ToolDescriptor`, `ToolRunPage`, and `WebSocketOutputChunk` types.

2. **[M]** Add `frontend/src/features/mcp/api.ts` extensions: `useListTools()` (queries `GET /api/v1/mcp/tools`); `useListToolRuns(engagementId, options)` (queries `GET /api/v1/tool-runs?engagement_id=...` with keyset pagination via `useInfiniteQuery`); `useToolRun(toolRunId)` (queries `GET /api/v1/tool-runs/{id}`). Keep the existing `useExecuteToolRun()` mutation; add `useExecuteToolRunAsync()` mutation (sets `async_mode: true`, returns immediately with the `tool_run_id`, then the component subscribes to the WebSocket). Write unit tests for hooks using MSW.

3. **[S]** Add `frontend/src/features/mcp/hooks/useToolRunStream.ts`: accepts `toolRunId: string | null`; opens a WebSocket to `/ws/tool-runs/{toolRunId}` when `toolRunId` is non-null; appends incoming `stdout`/`stderr` chunks to a local state buffer; sets `isDone` and `exitCode` on the `done` message; closes the socket on `done` or unmount. Returns `{ lines: string[]; isDone: boolean; exitCode: number | null }`. Write unit tests with a mocked WebSocket.

4. **[M]** Add `frontend/src/features/mcp/components/ToolRunnerForm.tsx` + `ToolRunnerForm.test.tsx`: renders a `<select>` populated from `useListTools()` (grouped by server name); when a tool is selected, renders a preset `<select>` and a dynamic arg form generated from the tool's `arg_schema` (bespoke renderer per decision 1 — handles `string`, `array of strings`, `integer` only, styled with Tailwind/shadcn; no `@rjsf/core`); the `target` field in the httpx arg schema pre-fills with `http://localhost:3000` and shows the sandbox guard notice in dev (detect via `import.meta.env.DEV`); **Run** button fires `useExecuteToolRunAsync()` and stores the returned `tool_run_id` in component state to activate the stream. Test: selecting httpx shows preset options; submit fires the mutation; sandbox notice is visible in dev; error state renders.

5. **[M]** Add `frontend/src/features/mcp/components/ToolOutputConsole.tsx` + `ToolOutputConsole.test.tsx`: accepts `toolRunId: string | null`; uses `useToolRunStream` to display live output in a scrollable `<pre>` block; auto-scrolls to bottom as new lines arrive; shows a spinner while running; shows exit code badge on completion; highlights stderr lines in a different colour (use Tailwind `text-red-400`). Test: renders output lines; auto-scroll called; exit code badge shown; stderr highlighted.

6. **[M]** Add `frontend/src/features/mcp/components/ToolRunHistory.tsx` + `ToolRunHistory.test.tsx`: renders the paginated list from `useListToolRuns`; each row shows tool name, preset, started_at, status badge, exit code; clicking a row calls `onSelectRun(toolRunId)` which re-renders `ToolOutputConsole` with the selected (historical) run's stored stdout/stderr (fetched via `useToolRun`). Test: list renders; pagination loads more; row click calls handler.

7. **[M]** Replace the bottom pane stub (from Slice 00 / Slice 03) with `ToolRunnerPanel.tsx`: a tabbed container with two tabs — **Runner** (renders `ToolRunnerForm` + `ToolOutputConsole` for the active run) and **History** (renders `ToolRunHistory`). Wire `onSelectRun` from History to swap the console into replay mode. Add `ToolRunnerPanel.test.tsx`. Test: Runner tab renders form; History tab renders list; switching tabs preserves state.

8. **[S]** Wire `ToolRunnerPanel` into the workspace layout (replacing the existing console placeholder from Slice 00/03 in `WorkspaceLayout.tsx`). Ensure the panel is only rendered when an engagement is selected.

## Test plan

- **Unit (backend)**:
  - `test_httpx_server.py` (in `mcp-servers/httpx/tests/`) — sends `run_httpx` JSON-RPC request; receives correct streaming chunks then `done`; non-zero exit code returned; unknown tool → method-not-found; output exceeding 1 MB truncated; `timeout_seconds` honoured.
  - `test_sandbox_guard.py` — `ADEPTUS_ENV=dev`: non-sandbox target raises `SandboxGuardViolation`; localhost/127.0.0.1/juice-shop allowed; `ADEPTUS_ENV=production`: any target passes.
  - `test_mcp_service_streaming.py` — `execute_tool_run` with `async_mode=True`: row status set to `running`; chunks enqueued in pub/sub; DB row updated with final values and `status=completed` on done; failed subprocess sets `status=failed`.
  - `test_mcp_repository_list.py` — `list_tool_runs_for_engagement` returns newest-first; keyset pagination returns correct page; non-member query returns empty (membership enforced upstream).
  - `test_mcp_router_list.py` — `GET /tool-runs` returns 200 for member; 404 for non-member; pagination cursor works. `GET /tool-runs/{id}` returns 200 for member; 404 for non-member or missing run.
  - `test_mcp_router_tools.py` — `GET /mcp/tools` returns list including httpx descriptor with presets and arg_schema; requires authenticated session.
  - `test_mcp_router_ws.py` — WebSocket upgrade rejected (4003) for non-member; chunks forwarded for member; socket closed on `done`; **mid-run reconnect replays already-buffered chunks before new ones** (decision 3); connecting after completion falls back to stored stdout/stderr + synthetic `done`.
  - Coverage gate: ≥80% on `app/features/mcp/`.

- **Unit (frontend)**:
  - `useToolRunStream.test.ts` — WebSocket opened on non-null `toolRunId`; lines appended per stdout chunk; isDone/exitCode set on done; socket closed on unmount.
  - `ToolRunnerForm.test.tsx` — tool selector populated; preset selector updates args; sandbox notice shown in dev; submit fires async mutation; error banner on failure.
  - `ToolOutputConsole.test.tsx` — lines render; spinner while running; exit code badge on done; stderr highlighted.
  - `ToolRunHistory.test.tsx` — list renders; pagination; row click triggers handler.
  - `ToolRunnerPanel.test.tsx` — tabs switch; active run passes toolRunId to console; history row click triggers replay mode.
  - Coverage gate: ≥60% on `src/features/mcp/`.

- **Integration**:
  - `test_httpx_integration.py` — spins up the real httpx MCP subprocess against Juice Shop (`http://localhost:3000`); calls `POST /api/v1/tool-runs` with `async_mode=false`; asserts `exit_code == 0`; asserts stdout is non-empty; asserts a `tool_runs` row exists with `status=completed`. Uses test compose stack with Juice Shop running (`make sandbox`).
  - `test_tool_run_stream_integration.py` — full async round-trip: POST with `async_mode=true`, connect WebSocket, collect chunks until `done`, assert at least one stdout chunk was received and exit_code is 0.

- **E2E**: One Playwright test (`tool-runner.spec.ts`) covering the critical user journey: log in → open engagement → select httpx in tool runner → choose `quick` preset → run against `http://localhost:3000` → assert output appears in console → assert exit code badge shows 0.

## Acceptance criteria

- `make test` passes with no failures and coverage gates met.
- `make lint` passes (ruff, mypy, eslint, tsc --noEmit).
- `make dev` + `make sandbox`: log in, open an engagement, select the httpx tool, choose the `quick` preset, click **Run** against `http://localhost:3000` — streaming output appears in the bottom pane console within 5 seconds, exit code badge shows 0 after completion.
- The run appears in the **History** tab; clicking it replays the stored output.
- Submitting a non-sandbox target URL (e.g. `https://example.com`) while `ADEPTUS_ENV=dev` returns HTTP 403 with a sandbox guard error message visible in the UI.
- `GET /api/v1/mcp/tools` returns a JSON array including an entry with `server_name: "httpx"` and at least two presets (`quick`, `full`).
- `GET /api/v1/tool-runs?engagement_id=<id>` returns 404 when called by a user who is not an explicit member of that engagement (no admin bypass; engagement existence is not disclosed — same 404 pattern as Slice 03).
- WebSocket connection to `/ws/tool-runs/<id>` for a run owned by another engagement closes with code 4003 (not found / not member).
- `make test-backend` integration test runs the full httpx round-trip against Juice Shop and passes.
- `gh pr view` shows green CI.

## Risks

- **Risk 1 — httpx binary availability in the container**: The httpx binary must be present in the Docker image used by the backend. If the image is not rebuilt or the `apt`/`go install` step is missing, the MCP subprocess will fail to spawn. Mitigation: add a Dockerfile step that installs `httpx` (from projectdiscovery releases or Go install) and add an integration test that asserts `httpx --version` exits 0.

- **Risk 2 — WebSocket auth with session cookie**: FastAPI WebSocket upgrade must validate the session cookie the same way HTTP routes do. Some middleware (e.g. HTTPS redirect, CSRF) may not run on WebSocket upgrades. Mitigation: explicitly extract and validate the session cookie in the WebSocket handler using the same `get_current_user` dependency; write a unit test that asserts upgrade is rejected without a valid cookie.

- **Risk 3 — In-process pub/sub scalability**: Using a module-level `dict[uuid, asyncio.Queue]` for chunk broadcasting works for a single-process dev deployment but will not work across multiple uvicorn workers. Mitigation: this is explicitly acceptable for v1 (single-process Compose deployment); document it clearly. If the deployment ever becomes multi-worker, replace with Redis pub/sub. Do not add Redis now.

- **Risk 4 — JSON-Schema-to-form rendering scope**: Generating a dynamic form from the tool's `arg_schema` JSON Schema could become complex for nested or conditional schemas. Mitigation: keep httpx's arg_schema flat (3–4 top-level string/array fields); the form renderer only needs to handle `string`, `array of strings`, and `integer` types for this slice. Document that complex schemas are out of scope until a dedicated form-renderer slice.

- **Risk 5 — Sandbox guard bypass**: The sandbox guard uses `ADEPTUS_ENV` env var; if the variable is not set, the guard defaults to `dev` mode (safe default — fail closed). Document this behaviour and test the default. The guard applies to the `target` field by convention; tools that do not surface a `target` field are not guarded at this layer (shell-exec from Slice 03 has no target guard — that is addressed in Slice 16 via approval-gating).

## Resolved decisions (were open questions)

1. **JSON-Schema-to-form rendering → bespoke renderer.** The `ToolRunnerForm` renders the dynamic form with a small in-house renderer that handles only the flat types this slice needs (`string`, `array of strings`, `integer`). Rationale: `@rjsf/core` ships its own widget markup and would have to be re-themed to satisfy the Tailwind-only / shadcn-ui conventions in CLAUDE.md, which amounts to rebuilding the widgets anyway. The spec already defers complex/nested schemas to a future dedicated form-renderer slice, so the library would be replaced either way. Keep httpx's `arg_schema` flat. Do **not** add `@rjsf/core`.

2. **httpx installation → in the backend Docker image.** httpx is installed via a Dockerfile step in the backend image (not a sidecar). Rationale: the httpx MCP server `exec`s the binary as a local stdio subprocess of the backend (backend task 3); a sidecar cannot be `exec`'d across containers and would force a network-RPC redesign that diverges from the Slice 03 MCP model. Real per-process isolation arrives with the per-engagement Docker execution model (§6.1, deferred). See backend task 8 and Risk 1.

3. **WebSocket reconnect → replay from in-process buffer.** The per-run pub/sub entry retains the accumulated chunks for the lifetime of the run; on WebSocket connect the handler first replays the buffered chunks, then subscribes to new ones, so a mid-run reconnect shows continuous output rather than an empty console. No per-chunk DB writes are needed — the buffer lives in the existing module-level dict and is discarded once the run completes and the final row is persisted. See backend tasks 6 and 7.

## Security review required?

No — this slice does not touch auth, MCP subprocess spawning (httpx MCP is structurally identical to shell-exec from Slice 03, already reviewed), audit log, single-writer graph process, RAG isolation, egress, secrets, or approvals. The sandbox guard is a safety rail, not a security boundary. The WebSocket auth reuses the existing session cookie mechanism reviewed in Slice 00.

## Progress

(The stop-checkpoint hook and compact-handoff skill append here. Leave empty at planning time.)
- 2026-06-02T19:12:15Z — 0ffab0d Merge pull request #12 from SelfishCoconut/slice-03-static-mcp-config-shell-exec

### Handoff — backend complete (2026-06-02)

**All 9 backend tasks done and committed on `slice-04-tool-runner-panel`.** Gate is green:
359 backend tests pass (7 integration deselected); `app/features/mcp` coverage **94%**
(gate 80%); whole-project mypy clean (both the pre-commit and `make lint` configs);
mcp-servers httpx suite 29 tests pass; all pre-commit hooks pass per commit.

Commits (newest first): `bb414f7` register httpx + Dockerfile binary · `1194dd9` sandbox
guard · `3906b0c` drop spurious type-ignore · `7e4287d` WebSocket endpoint · `22883be`
async streaming + pub/sub · `48ebca7` list/detail endpoints · `dc1aee9` GET /mcp/tools ·
`54f76f3` schemas · `8c30553` migration · `a098c56` open slice.

**Key cross-task contracts established (frontend must match):**
- httpx MCP wire protocol: per-line JSON-RPC **notifications**
  `{"jsonrpc":"2.0","method":"tools/output","params":{"id":N,"type":"stdout"|"stderr","data":"..."}}`
  then a final JSON-RPC **response** with full capped output. The WS endpoint converts the
  final result into a `done` chunk.
- WS endpoint `GET /ws/tool-runs/{id}`: session-cookie auth on upgrade; **closes with 4003**
  for unauth / not-found / non-member (no disclosure); replays buffered chunks then streams
  live; for an already-completed run sends stored stdout/stderr + synthetic `done`.
  Message shape = `WebSocketOutputChunk` (`type` stdout|stderr|done|error; `exclude_none`).
- POST `/api/v1/tool-runs` with `async_mode:true` → **202** + partial `ToolRunResult`
  (status `running`, exit_code/finished_at null); client then opens the WS.
- `GET /api/v1/mcp/tools` → `ToolDescriptor[]` (httpx present with quick/full presets +
  flat arg_schema: target/flags/timeout_seconds).
- `GET /api/v1/tool-runs?engagement_id&limit&cursor` → `ToolRunPage` (opaque base64 cursor,
  newest-first); `GET /api/v1/tool-runs/{id}` → `ToolRunResult`. Both **404** for non-member.
- Sandbox guard: non-sandbox `target` in dev/test → **403** (`SandboxGuardViolation`).

**NEXT: Frontend tasks 1–8.** Start with FE task 1 = `make generate-api` (regenerate the
typed client from the new backend contract) — this MUST run before the hooks/components.
Then api hooks (FE2), useToolRunStream (FE3), ToolRunnerForm (FE4), ToolOutputConsole
(FE5), ToolRunHistory (FE6), ToolRunnerPanel (FE7), wire into WorkspaceLayout (FE8).
Per-task commits continue. finish-slice (full gate + code-reviewer + PR + close issue #13)
runs after FE8.
