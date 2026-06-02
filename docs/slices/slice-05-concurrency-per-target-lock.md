# Slice 05: Concurrency model + per-target lock

**Branch**: `slice-05-concurrency-per-target-lock`
**GitHub Issue**: #16
**Status**: planned
**Risky**: no

---

## Goal

Serialize heavy tool runs through a bounded per-engagement slot pool plus a per-(engagement, target-host) lock so two heavy tools against the same host never run at once, while extra runs queue FIFO and surface their queue position live in the UI.

## User-visible demo

After this slice is merged, with `make dev` + `make sandbox`:

- Open an active engagement, go to the Tool Runner bottom pane.
- Start a heavy tool run against `http://localhost:3000` (a heavy variant of httpx — see "Out of scope" note on test tools), then immediately start a **second** heavy run against the **same** host.
- The second run does NOT start executing — its console shows a **"Queued — position 1"** badge and a "waiting on slot / per-target lock" reason. When the first run finishes, the second automatically dequeues and begins streaming.
- Start a heavy run against `localhost` and a heavy run against a **different** host (`127.0.0.1`) at the same time with the default pool of 3 slots: both run concurrently (different target hosts, slots available, no shared lock).
- Saturate the pool: with 3 heavy runs in flight, a 4th heavy run queues with **"Queued — position 1"** even though its target host is free; it starts only when a slot frees.
- Start a **light** tool (plain `httpx` from Slice 04) at any time: it bypasses the queue and the per-target lock entirely and runs immediately, even while heavy runs are saturating the pool against the same host.
- The Tool Runner panel shows a small **queue strip**: a live count of "N running / M queued" for the engagement, refreshed as runs start and finish.
- Admins can change the per-engagement slot count via an engagement setting (default 3); the new limit applies to subsequently admitted runs.

## Out of scope

- Does NOT implement per-tool kill switches, the engagement-wide pause, or the timeout kill/extend/wait UX (Slice 06). A queued run can be discarded only by the existing failure paths; an explicit "cancel queued run" button is deferred to Slice 06.
- Does NOT add the real nmap / gobuster heavy MCP servers (Slice 26). This slice needs *a* heavy tool to demonstrate serialization; it adds a heavy-weighted variant in the existing httpx MCP server manifest (or a tiny `sleep`-based heavy tool) purely for test/demo, gated to the sandbox. See Decision 1.
- Does NOT implement background-on-browser-close completion notifications (Slice 27) — runs already continue server-side; the *notification* surface is Slice 27.
- Does NOT make the concurrency state multi-worker safe. The slot pool, lock table, and FIFO queue live in-process (module-level asyncio primitives), consistent with the in-process pub/sub from Slice 04 (Risk: see below; single-process Compose only).
- Does NOT persist the queue across a backend restart. On restart, in-flight and queued runs are reconciled per the existing crash-recovery stub (NULL `finished_at` rows marked failed); formal crash recovery is Slice 38.
- Does NOT implement per-engagement Docker networking or proxy config (§6.1, still deferred).
- Does NOT change the synchronous (`async_mode=false`) path's contract; serialization is applied to it too (Decision 4) but it has no queue-position UI.
- Does NOT add approval-gating for heavy/dangerous tools (Slice 16).

## Requirements traceability

This slice implements §6.2 **fully** (the parts not yet built by Slices 03/04):

- §6.2 — "Bounded concurrency per engagement with a per-target lock." This is the primary deliverable: the fixed slot pool, the per-target lock, the FIFO queue, and the light-lane bypass are all delivered here.
- §6.2 — "Each engagement has a fixed pool of concurrent execution slots (default: 3, configurable)." → per-engagement slot semaphore, default 3, configurable via an engagement setting (Decision 2).
- §6.2 — "Tools declare a `weight: light | heavy` in their MCP manifest." → already declared in the registry (`McpToolConfig.weight`, Slice 03/04); this slice is the first *consumer* of that field for admission control.
- §6.2 — "Heavy tools consume a slot for their full lifetime; light tools (`httpx`, `curl`, single-request operations) may use a dedicated light-lane that does not consume the heavy pool." → light runs bypass the semaphore and the lock.
- §6.2 — "A per-(engagement, target-host) lock prevents two heavy tools from running against the same host simultaneously, regardless of slot availability." → per-(engagement, host) lock acquired before slot admission completes; held for the heavy run's lifetime.
- §6.2 — "Light tools bypass this lock." → enforced.
- §6.2 — "When all slots are busy or a per-target lock is held, additional runs queue FIFO." → a per-engagement FIFO admission queue; position surfaced to the client.
- §6.2 — "No rate limits on outgoing scans beyond the concurrency model above." → no additional throttling added.
- §6.2 — "Long-running tools continue in the background when the user closes the browser." → unchanged; the async background task already owns the run after the 202 (Slice 04). Serialization happens inside that background task so closing the browser does not abandon a queued run.
- §11.2 — bottom pane tool console: extended with the queued state and queue strip.

Cross-cutting: §17.1 (engagement isolation) — the lock keyspace and the queue are scoped per engagement; a target host string is namespaced by `engagement_id` so no cross-engagement coupling occurs. §4 (no admin bypass) — the existing membership chokepoint in `execute_tool_run` is unchanged and still runs before any admission logic.

## Contract

No new REST endpoints. Changes are: (a) two new fields on `ToolRunResult`, (b) two new `WebSocketOutputChunk` message types (`queued`, `started`), (c) a new read-only queue-status endpoint, (d) a new optional engagement setting for the slot count.

```yaml
openapi: "3.1.0"
info:
  title: Adeptus — Slice 05 delta
  version: "0.5.0"

paths:
  /api/v1/engagements/{engagement_id}/tool-queue:
    get:
      summary: >
        Live concurrency snapshot for an engagement's heavy-tool pool.
        Used by the Tool Runner queue strip. Membership-gated (404 for
        non-members; no existence disclosure — §17.1).
      tags: [tools]
      security:
        - sessionCookie: []
      parameters:
        - name: engagement_id
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
                $ref: "#/components/schemas/ToolQueueSnapshot"
        "404":
          description: Engagement not found or caller is not a member

components:
  schemas:
    ToolQueueSnapshot:
      type: object
      required: [slot_limit, running_count, queued_count, queued]
      properties:
        slot_limit:
          type: integer
          description: Configured heavy-pool size for this engagement (default 3).
          example: 3
        running_count:
          type: integer
          description: Heavy runs currently holding a slot.
        queued_count:
          type: integer
          description: Heavy runs waiting for a slot or a per-target lock.
        queued:
          type: array
          description: FIFO-ordered queued runs (front of queue first).
          items:
            $ref: "#/components/schemas/QueuedRun"

    QueuedRun:
      type: object
      required: [tool_run_id, server_name, tool_name, target_host, position, reason, enqueued_at]
      properties:
        tool_run_id:
          type: string
          format: uuid
        server_name:
          type: string
        tool_name:
          type: string
        target_host:
          type: string
          nullable: true
          description: Resolved target host the run will lock, or null if the tool has no target.
        position:
          type: integer
          description: 1-based FIFO position among queued runs (1 = next to admit).
        reason:
          type: string
          enum: [slot_full, target_locked]
          description: Why the run is still waiting.
        enqueued_at:
          type: string
          format: date-time

    # --- Extended from Slice 03/04 ---
    ToolRunResult:
      description: >
        Slice 03/04 schema plus two additive fields. The `status` enum gains
        a `queued` member. Existing consumers ignore the new fields.
      allOf:
        - $ref: "#/components/schemas/ToolRunResultBase"   # the Slice 04 shape
      type: object
      properties:
        status:
          type: string
          enum: [queued, running, completed, failed, timed_out]
          description: >
            `queued` is new in Slice 05 — the run is admitted to the FIFO queue
            but has not acquired a slot / target lock yet.
        queue_position:
          type: integer
          nullable: true
          description: >
            1-based FIFO position while status == queued; null once running or
            terminal. Heavy runs only; always null for light runs.

    # --- Extended from Slice 04 ---
    WebSocketOutputChunk:
      description: >
        Slice 04 message plus two new `type` values so a client that connected
        to a not-yet-running run sees admission transitions live.
      type: object
      required: [type]
      properties:
        type:
          type: string
          enum: [queued, started, stdout, stderr, done, error]
        data:
          type: string
        exit_code:
          type: integer
        finished_at:
          type: string
          format: date-time
        message:
          type: string
        queue_position:
          type: integer
          description: Present (and updated) for `queued` messages.
        reason:
          type: string
          enum: [slot_full, target_locked]
          description: Present for the first `queued` message.
```

Engagement setting (additive; surfaced through the existing engagement settings contract from Slice 01/02):

```yaml
    EngagementSettingsPatch:    # existing schema — one optional field added
      type: object
      properties:
        concurrency_slot_limit:
          type: integer
          minimum: 1
          maximum: 16
          description: >
            Heavy-tool pool size for this engagement. Default 3 (§6.2). Changing
            it affects runs admitted after the change; in-flight runs are unaffected.
```

## Data model changes

Minimal and additive. No new lock/queue tables — concurrency state is in-process (Decision 3); only the *configurable slot limit* and the new run *status* need persisting.

- **`engagements`** table: add one column.
  - `concurrency_slot_limit` SMALLINT NOT NULL DEFAULT 3 — per-engagement heavy-pool size (§6.2). Range enforced at the API layer (1–16); the column default backfills existing rows to 3.

- **`tool_runs`** table: extend the `status` domain only.
  - `status` already exists (Slice 04, VARCHAR(20), default `'completed'`). Add `'queued'` as an accepted value. No schema change to the column itself (it is a free VARCHAR, not a Postgres enum); the new value is enforced by the Pydantic `ToolRunStatus` literal. **Migration note**: confirm the column is VARCHAR, not a native ENUM — if a CHECK constraint exists on `status`, the migration must `ALTER` it to include `'queued'`. The `write-alembic-migration` skill must inspect the live schema first.
  - No new column for `queue_position` (it is ephemeral, derived from the in-process queue, and meaningless after the run is admitted). `queue_position` is computed at read time for queued runs and is `null` otherwise.

- Index note: no new index. The queue-status endpoint reads from the in-process structure, not the DB. The existing `(engagement_id, started_at DESC)` index still covers history listing.

## Tasks

Number tasks continuously across the slice (backend then frontend). Each commit subject cites `(task N)`.

### Backend tasks

Ordered. Each independently testable. Complexity: S/M/L.

1. **[S]** Add `concurrency_slot_limit` (SMALLINT, default 3) to the `Engagement` SQLAlchemy model and the engagement read/patch Pydantic schemas (range 1–16 on patch). Extend `app/features/engagements/service.py` so the patch updates it. Unit tests: default is 3 on create; patch within range succeeds; out-of-range rejected (422).

2. **[M]** Add `app/features/mcp/concurrency.py` — the in-process admission manager. This is the heart of the slice. Provide:
   - A per-engagement record holding: an `asyncio.Semaphore`-like counting gate sized to the engagement's `concurrency_slot_limit`, a `set[str]` of currently-locked target hosts, and an ordered FIFO `list` (or `asyncio` deque) of waiting admission tickets.
   - `resolve_target_host(server_name, tool_name, args) -> str | None` — derive the lockable host from the tool's args. Reuse the URL-parsing logic already in `service._enforce_sandbox_guard` (extract hostname, strip port, lowercase; userinfo-smuggling-safe). Returns `None` for tools with no `target` (e.g. `run_command`) — those acquire only a slot, no host lock.
   - `async acquire(engagement_id, slot_limit, target_host, *, on_queued, on_started) -> AdmissionHandle` — the FIFO admission primitive. If a slot is free AND (target_host is None OR not locked): admit immediately, call `on_started`, return a handle. Otherwise enqueue a ticket, call `on_queued(position, reason)`, and await admission. When admitted, call `on_started`. Admission MUST honour strict FIFO order across the whole engagement (a later light-host run cannot jump a queued run ahead of an earlier slot-blocked run — Decision 5).
   - `release(handle)` — return the slot, drop the host from the locked set, then run the FIFO admission scan to admit the next eligible waiter(s). The scan admits the front-most ticket whose host is unlocked and for which a slot is free; it must be re-entrant-safe and not admit out of order.
   - `position_of(tool_run_id) -> int | None` and `snapshot(engagement_id) -> ToolQueueSnapshot` for the read endpoint.
   - `set_slot_limit(engagement_id, n)` — grow/shrink the gate; shrinking never preempts a running slot, it just stops admitting until count drops.
   - A `_reset()` test hook mirroring `service._reset_channels`.
   - Light runs DO NOT call `acquire` at all — they are not the manager's concern (the caller branches on weight). Document this contract in the module docstring.
   - Unit tests (no DB, pure asyncio): two heavy runs same host serialize; two heavy runs different hosts run concurrently when slots free; pool saturation queues the (slots+1)-th run; FIFO order preserved under interleaving; `release` admits the correct next waiter; target-locked vs slot-full `reason` reported correctly; shrinking the limit does not kill running slots.

3. **[M]** Wire admission into `service.execute_tool_run` / `_stream_to_channel`. Look up the tool's `weight` from the registry. For **light** tools: unchanged (Slice 04 behaviour, no admission). For **heavy** tools:
   - Resolve `target_host` via `concurrency.resolve_target_host`.
   - In the **async path**: insert the row with `status='queued'` if it cannot be admitted immediately, else `status='running'`. Move the `acquire(...)` call into `_stream_to_channel` (the background task) so the request returns 202 immediately even when queued. The `on_queued` callback broadcasts a `queued` `WebSocketOutputChunk` (with `queue_position` + `reason`) and updates the DB row to `status='queued'`; `on_started` broadcasts a `started` chunk and updates the row to `status='running'` (sets `started_at` to admission time — Decision 6). Only after admission does the existing `subprocess_manager.stream_tool_call` loop begin. The handle is released in the `finally` block alongside `_discard_channel`.
   - In the **sync path** (`async_mode=False`): wrap the `send_tool_call` in `acquire(...)`/`release(...)` so synchronous heavy runs also serialize (Decision 4). The HTTP request simply blocks until admitted — no queue-position payload (the response only ever reflects the terminal state).
   - Unit tests with mocked `subprocess_manager` and a fake clock: async heavy run that is blocked emits a `queued` chunk and writes `status='queued'`; on release emits `started` then streams; sync heavy run blocks until the slot frees; light run never touches the manager.

4. **[S]** Re-broadcast queue-position updates. When a run is admitted and others remain queued, their FIFO positions shift. After each `release`-driven admission, the manager re-broadcasts an updated `queued` chunk (new `queue_position`) to each still-waiting run's channel via the existing `broadcast_tool_run_output`. Add a callback hook from `concurrency` into `service` (avoid a hard import cycle — pass the broadcast fn in, or use a registered callback). Unit test: with 3 queued runs, admitting the front one shifts positions 2→1, 3→2 and each gets a fresh `queued` chunk.

5. **[M]** Add the read endpoint `GET /api/v1/engagements/{engagement_id}/tool-queue` in `app/features/mcp/router.py` (or the engagements router — keep it in `mcp` since it concerns tool runs; cite the decision). Membership-gated via the same `get_engagement_for_member` chokepoint (404 for non-member, no disclosure — §17.1/§4). Returns `concurrency.snapshot(engagement_id)`, with `slot_limit` read from the engagement row. Router + service unit tests: 200 for member with correct counts; 404 for non-member; empty snapshot when nothing running.

6. **[S]** Extend schemas: add `queued` to `ToolRunStatus`; add `queue_position: int | None = None` to `ToolRunResult`; add `queued`/`started` to the `WebSocketOutputChunk.type` literal plus `queue_position` and `reason` optional fields; add `ToolQueueSnapshot` and `QueuedRun` models. Update `_row_to_result` to populate `queue_position` from `concurrency.position_of` when `status == 'queued'`, else `None`. Unit test the mapping.

7. **[S]** Add a heavy demo/test tool so serialization is observable end-to-end (Decision 1). Add a `run_httpx_heavy` tool (or a `sleep_probe` tool) to the httpx MCP server manifest with `weight: heavy`, taking the same `target` arg plus a `hold_seconds` arg, that holds the slot for a bounded, sandbox-guarded duration. Keep it sandbox-gated (the existing `_enforce_sandbox_guard` already covers `target`). Manifest/registry unit test confirms the heavy weight is parsed and surfaced via `GET /api/v1/mcp/tools`.

8. **[S]** Startup reconciliation touch-up: ensure the existing "mark NULL-`finished_at` rows failed on startup" stub (Slice 03 Risk 3) also clears any `status='queued'`/`status='running'` rows to `failed` so a restart leaves no phantom queued runs (the in-process queue is empty after restart). Unit test: a `queued` row at startup becomes `failed`. (Full crash recovery remains Slice 38.)

### Frontend tasks

Ordered. Complexity: S/M/L. Numbering continues.

9. **[S]** Run `make generate-api` to regenerate `frontend/src/shared/api/` with the new `concurrency_slot_limit` field, `queued` status, `queue_position`, the `queued`/`started` WS chunk types, and the `ToolQueueSnapshot` / `QueuedRun` schemas.

10. **[S]** Extend `frontend/src/features/mcp/hooks/useToolRunStream.ts`: handle the new `queued` and `started` chunk types. On `queued`, expose `{ queued: true, queuePosition, queueReason }`; on `started`, flip `queued` to false and clear the position. Update the returned type accordingly. Unit test with mocked WebSocket: a `queued` then `started` then `stdout` sequence transitions state correctly; re-broadcast `queued` chunks update the position.

11. **[M]** Update `frontend/src/features/mcp/components/ToolOutputConsole.tsx` + test: when the stream reports `queued`, render a **"Queued — position N"** badge with the reason ("waiting for a free slot" / "waiting on the target host lock") instead of the running spinner; switch to the streaming view on `started`. Test: queued badge with position renders; reason text correct; transitions to streaming output on `started`.

12. **[M]** Add `frontend/src/features/mcp/api.ts` hook `useToolQueue(engagementId)` querying `GET /api/v1/engagements/{id}/tool-queue` (TanStack Query, modest `refetchInterval`, e.g. 2 s, while the panel is mounted — Decision 7: poll, do not open a second WS). Add `frontend/src/features/mcp/components/ToolQueueStrip.tsx` + test: renders "N running / M queued"; lists queued runs with tool name + position when expanded. Test: counts render; empty state hidden.

13. **[S]** Wire `ToolQueueStrip` into `ToolRunnerPanel.tsx` (a compact strip above the form / in the panel header). Add an admin-only `concurrency_slot_limit` number input to the engagement settings page (reuse the Slice 02 settings form), range 1–16, default 3. Tests: strip appears in the panel; settings input submits the new limit (mutation called with the value).

## Test plan

- **Unit (backend)** — coverage ≥80% on `app/features/mcp/`:
  - `test_concurrency.py` (pure asyncio, no DB) — the core matrix:
    - `test_two_heavy_same_host_serialize` — second blocks until first releases.
    - `test_two_heavy_diff_host_concurrent` — both admit immediately (slots free).
    - `test_pool_saturation_queues` — with `slot_limit=3`, the 4th heavy run (free host) queues with `reason=slot_full`.
    - `test_target_lock_queues_with_free_slot` — same-host 2nd run queues with `reason=target_locked` even though a slot is free.
    - `test_fifo_order_preserved` — interleaved enqueues admit in arrival order; no later run jumps an earlier one (Decision 5).
    - `test_release_admits_next_eligible` — release scans and admits the front-most unblocked waiter.
    - `test_positions_reshift_on_admit` — queued positions decrement on admission.
    - `test_shrink_limit_does_not_preempt` — lowering `slot_limit` mid-run leaves running slots alone.
    - `test_resolve_target_host` — URL/bare-host/userinfo-smuggling cases; `None` for `run_command`.
  - `test_mcp_service_concurrency.py` — `execute_tool_run` with mocked `subprocess_manager`: async heavy blocked → `status='queued'` + `queued` chunk; admission → `started` chunk + streaming; sync heavy blocks until release; light run never calls `acquire`.
  - `test_mcp_router_queue.py` — `GET /tool-queue` 200 for member with correct snapshot; 404 for non-member; empty snapshot shape.
  - `test_mcp_repository_startup_reconcile.py` — `queued`/`running` rows at startup → `failed`.
  - `test_engagements_slot_limit.py` — default 3 on create; patch 1–16 ok; 0 and 17 rejected.
  - Schema mapping test: `_row_to_result` sets `queue_position` for queued rows, `None` otherwise.

- **Unit (frontend)** — coverage ≥60% on `src/features/mcp/`:
  - `useToolRunStream.test.ts` — `queued`→`started`→`stdout` transitions; position re-broadcast updates state.
  - `ToolOutputConsole.test.tsx` — queued badge + reason; transition to streaming on `started`.
  - `ToolQueueStrip.test.tsx` — running/queued counts render; queued list with positions; empty state hidden.
  - settings input test — slot-limit input fires the engagement patch mutation with the entered value.

- **Integration** (test compose stack + `make sandbox`):
  - `test_concurrency_integration.py` — POST two heavy runs (the `run_httpx_heavy`/`sleep_probe` tool, `hold_seconds≈2`) against the **same** sandbox host in `async_mode`. Assert: the first reaches `status='running'`; the second is observed as `status='queued'` (via `GET /tool-queue` and/or its WS `queued` chunk); after the first's `done`, the second transitions to `running` then `done`. Assert wall-clock ordering proves serialization (second `started_at` ≥ first `finished_at`).
  - `test_concurrency_diff_host_integration.py` — two heavy runs against `localhost` and `127.0.0.1` run concurrently (both `running` before either finishes).

- **E2E** — one Playwright test (`tool-queue.spec.ts`): log in → open engagement → start a heavy run, then a second heavy run against the same host → assert the second shows the **"Queued — position 1"** badge → wait for the first to finish → assert the second begins streaming. This is a genuine user-visible journey (the core demo) so it qualifies under the "critical journeys only" rule.

## Acceptance criteria

- `make test` passes with coverage gates met (≥80% `app/features/mcp/`, ≥60% `src/features/mcp/`).
- `make lint` passes (ruff, mypy, eslint, tsc --noEmit).
- `make dev` + `make sandbox`: starting two heavy runs against the same sandbox host serializes them — the second shows **"Queued — position 1"** and only begins after the first completes (the Playwright `tool-queue.spec.ts` proves this).
- Two heavy runs against **different** sandbox hosts (`localhost` vs `127.0.0.1`) run concurrently while slots are available.
- With `concurrency_slot_limit=3`, a 4th heavy run queues (`reason: slot_full`) even when its target host is free, and starts when a slot frees.
- A light `httpx` run started while the heavy pool is saturated against the same host runs immediately (bypasses queue and lock).
- `GET /api/v1/engagements/{id}/tool-queue` returns correct `running_count` / `queued_count` and FIFO `position`s for a member; returns **404** for a non-member (no existence disclosure).
- Patching `concurrency_slot_limit` to a value in 1–16 succeeds; 0 or 17 returns 422; the new limit governs subsequently admitted runs.
- A backend restart while a run is `queued` leaves that row `failed` (no phantom queued runs) on startup.
- `gh pr view` shows green CI.

## Risks

- **Risk 1 — FIFO fairness vs. eligibility under the per-target lock.** Strict global FIFO can head-of-line-block: if the front waiter's host is locked but a slot is free, naively admitting it is impossible, yet skipping it to admit a later run violates FIFO. Decision 5 resolves this: FIFO is the *arrival order* the scan walks, and the scan admits the **first waiter that is currently eligible** (slot free AND host unlocked). A blocked-by-lock front waiter does not starve a free slot — but a slot-blocked front waiter is never overtaken by a later run just because the later run's host is free. Test `test_fifo_order_preserved` and `test_target_lock_queues_with_free_slot` pin both behaviours. Document the exact policy in the `concurrency.py` module docstring so it cannot drift.

- **Risk 2 — In-process state is not multi-worker safe.** The slot pool, lock set, and FIFO queue are module-level asyncio structures, exactly like the Slice 04 pub/sub. Across multiple uvicorn workers the same engagement could over-admit. Mitigation: this is acceptable for the single-process Compose deployment (documented, same posture as Slice 04 Risk 3); a multi-worker deployment would move admission to a Postgres advisory-lock or Redis-based gate. Do NOT add Redis/advisory locks now — note it in the module docstring.

- **Risk 3 — Deadlock / lost slot on task failure.** If `_stream_to_channel` raises after acquiring a slot, the slot or host lock could leak, permanently blocking the engagement. Mitigation: acquire inside a `try` and release in the `finally` that already runs `_discard_channel`. Add a unit test that forces an exception mid-stream and asserts the slot and host lock are released (queue drains afterwards).

- **Risk 4 — `started_at` semantics.** Slice 04 sets `started_at` at row insert. For a queued run, "started" should mean "began executing", not "was enqueued". Decision 6: keep `started_at` = admission/execution time; add no separate `enqueued_at` column on `tool_runs` (the in-process queue holds `enqueued_at` for the snapshot only). This keeps the history list's ordering and durations meaningful. Confirm the keyset cursor still works (it orders by `started_at, id` — admitted runs get a monotonic-enough `started_at`; ties broken by id).

- **Risk 5 — Target-host resolution mismatch with the actual scan.** The lock keys on the host parsed from `args["target"]`, but the heavy tool ultimately decides what to hit. If parsing diverges from what the tool scans, two runs could hit the same host without sharing a lock. Mitigation: reuse the exact, already-reviewed parsing from `_enforce_sandbox_guard` (userinfo-smuggling-safe) so lock-host and guard-host are identical; tools with no `target` take a slot but no host lock (correct — they have no single host to serialize on). Documented as a known limitation for multi-target heavy tools (e.g. nmap with a CIDR) — those serialize only on the slot pool, not per-host, which §6.2 permits.

- **Risk 6 — Slot-limit shrink edge cases.** Lowering the limit below the running count must not crash or preempt. Mitigation: the gate tracks available permits as `limit - in_use`; shrinking can make available go negative, which simply blocks new admissions until releases catch up. Tested by `test_shrink_limit_does_not_preempt`.

## Open questions for the human

All three resolved at start-slice time (2026-06-03) — defaults taken, on long-term-health grounds:

- **Q1 — Heavy demo tool choice (Decision 1). RESOLVED: add the throwaway sandbox-gated heavy tool** (`run_httpx_heavy`/`sleep_probe`, `weight: heavy`, `hold_seconds` arg). Pulling Slice 26 forward is not viable — 26 depends on 06, which depends on this slice — so the dependency graph forces a stand-in. It is removed/superseded when Slice 26 lands.
- **Q2 — Queue strip refresh mechanism (Decision 7). RESOLVED: poll `GET /tool-queue` (~2 s while mounted).** A pushed engagement-wide queue feed would pre-empt the real-time infrastructure that Slices 27 (background tasks) and 31 (presence) are designed to own, and would likely be reworked. Avoid premature/duplicate real-time plumbing now.
- **Q3 — Endpoint placement. RESOLVED: keep `GET /tool-queue` in the `mcp` feature router** (engagement-scoped path). The queue is a tool-runner concern; its schemas/service live in `mcp`. Splitting the endpoint into the engagements router would scatter one feature across two folders, violating the feature-folder cohesion rule in CLAUDE.md.

## Security review required?

No. This slice does not touch auth, MCP subprocess spawning semantics (the existing reviewed shell-exec/httpx path is unchanged — admission is a scheduler in front of it), the hash-chain audit log, the single-writer graph process, RAG isolation, egress/secrets, or approvals. The new read endpoint reuses the already-reviewed `get_engagement_for_member` membership chokepoint and the established 404-no-disclosure pattern. The heavy demo tool is sandbox-gated by the existing `_enforce_sandbox_guard`. Concurrency control is a safety/serialization mechanism, not a security boundary. (Note for the finish-slice reviewer: confirm the lock keyspace is namespaced by `engagement_id` so the per-target lock cannot couple two engagements — that is the one isolation-adjacent invariant introduced here.)

## Progress

(The stop-checkpoint hook and compact-handoff skill append here. Leave empty at planning time.)
- 2026-06-02T22:39:21Z — 2b0eb41 chore(claude): fix and harden .claude tooling (#15)
- 2026-06-02T22:40:03Z — e713ce5 docs(slice-05): spec + plan status in-progress (#16)
- 2026-06-02T22:41:32Z — e713ce5 docs(slice-05): spec + plan status in-progress (#16)
