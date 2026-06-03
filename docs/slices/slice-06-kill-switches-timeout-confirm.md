# Slice 06: Kill switches + timeout-confirm

**Branch**: `slice-06-kill-switches-timeout-confirm`
**GitHub Issue**: #19
**Status**: in-progress
**Risky**: no

---

## Goal

Give the user three controls over running work: a per-tool stop button that kills (or de-queues) a single run, an engagement-wide pause that halts every in-flight tool at once and blocks new ones, and a timeout prompt that asks kill / extend / wait instead of silently failing — and which never holds up the queue while it waits for the human's answer.

## User-visible demo

After this slice is merged, with `make dev` + `make sandbox`:

- Open an active engagement, go to the Tool Runner bottom pane, start a heavy run against `http://localhost:3000` (the `run_httpx_heavy` / `sleep_probe` demo tool from Slice 05 with a long `hold_seconds`).
- A **Stop** button is visible next to the running output. Click it: the run stops within ~1 s, the console shows a **"Killed"** badge, and the `tool_runs` row ends in `status='killed'`. The freed slot/host-lock immediately admits the next queued run (the queue strip count drops).
- Start a heavy run, then a second heavy run against the **same** host so it queues with "Queued — position 1". Click **Stop** on the *queued* run: its ticket is removed from the FIFO queue, the console shows **"Killed"**, and `GET /tool-queue` no longer lists it. No subprocess was ever touched (it never started).
- Click the engagement-wide **Pause** button (in the Tool Runner panel header / workspace banner area). All in-flight runs for the engagement stop within ~1 s and show **"Killed"**; every queued run is de-queued and marked killed; the panel shows a persistent **"Engagement paused"** state. Trying to start a new tool run while paused is rejected with a clear "engagement is paused" message (HTTP 409). Click **Resume**: new runs work again.
- Start a run with a short `timeout_seconds` against a tool that holds longer than the timeout. When the timeout fires, instead of an immediate failure the run **enters the `awaiting_decision` state and releases its concurrency slot + host lock back to the queue** (its underlying work is parked/suspended), and the console shows a **"Timed out — what do you want to do?"** prompt with three buttons: **Kill**, **Extend (+30s)**, **Wait (let it finish)**. Crucially, while the run sits awaiting the human's answer it does **not** hold up the FIFO queue or the engagement's slot pool — a same-host run that was queued behind it now advances to `running`. The prompt **stays open indefinitely (no auto-kill grace window)**; the run waits for the human's decision. Choosing **Kill** ends the run `status='killed'`; **Extend** re-acquires a slot (respecting the queue — it may wait if the host is now busy) and resumes with a fresh deadline; **Wait** re-acquires a slot and lets it run to completion with no further timeout.

## Out of scope

- Does NOT pause the **AI** (§6.3 says the global pause "halts all in-flight tools **and pauses the AI**"). There is no AI/chat yet (Slice 11). This slice delivers the tool-side of the engagement pause and a forward-compatible `paused` flag + event so Slice 11+ can subscribe; pausing AI turns is explicitly deferred to the AI slices. Documented in Decision 5 and Risk 6.
- Does NOT add per-engagement Docker containers (§6.1). Kill is implemented against the current in-process subprocess model (Decision 2). Real per-run process isolation / `docker kill` arrives with the §6.1 execution model (still deferred).
- Does NOT change the concurrency / FIFO / per-target-lock semantics from Slice 05 — it only *adds* cancel/de-queue, a pause gate in front of admission, and a release-and-reacquire path for a timed-out run awaiting a decision.
- Does NOT add background-on-close completion notifications (Slice 27) or a notifications panel (Slice 32). The kill/pause/timeout signals surface only via the run's existing WebSocket + the queue-strip poll while the user is looking.
- Does NOT add the audit-log records for kill/pause/timeout actions. Audit + hash-chain is Slice 10; this slice does not write to an audit log (it does not exist yet). When Slice 10 lands, kill/pause/resume/extend/wait become audited actions — noted as a forward dependency, not built here.
- Does NOT add approval-gating (Slice 16) or scope enforcement (Slice 17).
- Does NOT add the real nmap / gobuster heavy tools (Slice 26) — the demo heavy tool from Slice 05 is sufficient to exercise kill and timeout.
- Does NOT make kill/pause multi-worker safe — the cancellation registry, pause set, and timeout-decision rendezvous are in-process module-level state, the same posture as the Slice 04 pub/sub and the Slice 05 admission manager (Risk 4).
- Does NOT persist a paused engagement's "intent to kill" across a backend restart beyond the existing startup reconciliation (queued/running/awaiting_decision rows → failed; Slice 05 task 8 + this slice task 8). A restart while paused leaves the `paused` flag set (it is a DB column) but no runs in flight.

## Requirements traceability

This slice implements §6.3 fully (the parts not built by Slices 04/05):

- §6.3 — "**Kill switches:** per-tool stop button + global engagement pause that halts all in-flight tools and pauses the AI." → the **per-tool stop button** (cancel one run: kill its subprocess loop if running, or remove its FIFO ticket if queued) and the **engagement-wide pause** (halt every in-flight run + de-queue every waiting run + block new admissions) are the primary deliverables. The "pauses the AI" clause is forward-deferred (Decision 5 / Risk 6) because no AI exists yet.
- §6.2 — "**Timeout behavior:** when a tool hits its timeout, the system pauses and asks the user what to do (kill / extend / let it finish)." → replaces the Slice 04/05 behaviour where a timeout was a terminal `timed_out`/`McpServerDown`. The run now enters an `awaiting_decision` state, **releases its concurrency slot + host lock so the queue is never blocked while the human is prompted** (Decision 1 / Decision 6), broadcasts a `timeout` prompt, and waits **indefinitely** for a kill/extend/wait decision (there is no auto-kill grace window — §6.2 says "the system pauses and asks", which we honour literally). On `extend`/`wait` the run re-acquires a slot through the normal FIFO admission path before resuming. (Note: §6.2 carries this requirement, but the slice's `Requirements` field cites §6.3; both are listed here because the timeout-confirm UX is the §6.2 timeout sentence and the kill switches are the §6.3 sentence.)
- §11.2 — bottom pane tool console: extended with the Stop button, the timeout prompt, and the paused state.

Cross-cutting:
- §17.1 (engagement isolation) — kill, pause, and the timeout decision are all membership-gated through the existing `get_engagement_for_member` chokepoint (404 for non-members, no existence disclosure). The pause set and cancellation registry are keyed by `engagement_id` / `tool_run_id`, so no engagement can cancel another's runs.
- §17.2 (human in the loop; any member may act) — **any engagement member** (membership-gated only — they must be part of the engagement) may stop a run, pause/resume the engagement, or answer a timeout prompt, with **no confirmation dialog**; attribution is recorded once Slice 10 exists. No role gate (an admin without membership is still 404). **Confirmed by the human (Q2 resolved).**

## Contract

Three new REST endpoints (kill a run, set pause state, answer a timeout prompt), two new `ToolRunStatus` members, three new `WebSocketOutputChunk` types, and one new engagement field (`paused`).

```yaml
openapi: "3.1.0"
info:
  title: Adeptus — Slice 06 delta
  version: "0.6.0"

paths:
  /api/v1/tool-runs/{tool_run_id}/kill:
    post:
      summary: >
        Stop a single tool run. If the run is RUNNING, its background task is
        cancelled (the subprocess loop is abandoned and the per-engagement slot +
        host lock are released so the next queued run can start). If the run is
        QUEUED, its FIFO ticket is removed. If the run is AWAITING_DECISION (a
        timed-out run whose slot is already released), it is resolved as killed
        directly (no slot to release). Membership-gated (404 for non-members or
        unknown run; no existence disclosure — §17.1). Idempotent: killing an
        already-terminal run returns 200 with the current state.
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
          description: Run stopped (or already terminal). Returns the current ToolRunResult.
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/ToolRunResult"
        "404":
          description: Tool run not found or caller is not a member of its engagement.

  /api/v1/tool-runs/{tool_run_id}/timeout-decision:
    post:
      summary: >
        Answer a pending timeout prompt for a run in state `awaiting_decision`.
        The decision is forwarded to the waiting background task. `kill` ends the
        run; `extend` re-acquires a concurrency slot (respecting the FIFO queue —
        the call may take a moment if the host lock is currently held) and resumes
        with a fresh deadline; `wait` re-acquires a slot and runs to completion
        with no further timeout. Membership-gated. 409 if the run is not currently
        awaiting a decision (e.g. it already completed, was killed, or was already
        resolved by another member's decision).
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
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/TimeoutDecision"
      responses:
        "200":
          description: Decision accepted. Returns the current ToolRunResult.
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/ToolRunResult"
        "404":
          description: Tool run not found or caller is not a member.
        "409":
          description: Run is not awaiting a timeout decision.

  /api/v1/engagements/{engagement_id}/pause:
    post:
      summary: >
        Set or clear the engagement-wide tool pause. When `paused: true`, every
        in-flight tool run for the engagement is killed, every queued run is
        de-queued, every run awaiting a timeout decision is resolved as killed,
        and all subsequent POST /tool-runs are rejected 409 until resumed. When
        `paused: false`, new runs are allowed again (already-killed runs are NOT
        resumed — kill is terminal). Membership-gated. Idempotent (setting the
        same state twice is a no-op success).
      tags: [tools, engagements]
      security:
        - sessionCookie: []
      parameters:
        - name: engagement_id
          in: path
          required: true
          schema:
            type: string
            format: uuid
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/EngagementPauseRequest"
      responses:
        "200":
          description: Pause state updated. Returns the new pause state + counts.
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/EngagementPauseState"
        "404":
          description: Engagement not found or caller is not a member.

components:
  schemas:
    TimeoutDecision:
      type: object
      required: [decision]
      properties:
        decision:
          type: string
          enum: [kill, extend, wait]
          description: >
            kill — stop the run now (status → killed).
            extend — re-acquire a slot through the FIFO queue, grant additional
              time (extend_seconds), and resume.
            wait — re-acquire a slot and let the run finish with no further
              timeout enforcement.
        extend_seconds:
          type: integer
          minimum: 1
          maximum: 300
          default: 30
          description: Only used when decision == extend. Default +30 s.

    EngagementPauseRequest:
      type: object
      required: [paused]
      properties:
        paused:
          type: boolean

    EngagementPauseState:
      type: object
      required: [engagement_id, paused, killed_running, dequeued]
      properties:
        engagement_id:
          type: string
          format: uuid
        paused:
          type: boolean
        killed_running:
          type: integer
          description: >
            Number of in-flight runs killed by this pause action, INCLUDING runs
            that were awaiting a timeout decision (0 when resuming or already
            paused).
        dequeued:
          type: integer
          description: Number of queued runs removed by this pause action.

    # --- Extended from Slice 04/05 ---
    ToolRunResult:
      description: >
        Slice 05 shape; the `status` enum gains `killed` and `awaiting_decision`.
        Existing fields unchanged.
      type: object
      properties:
        status:
          type: string
          enum:
            [queued, running, awaiting_decision, completed, killed,
             failed, timed_out]
          description: >
            `killed` (new) — the run was stopped by a per-tool kill or an
            engagement pause. `awaiting_decision` (new) — the run hit its timeout,
            released its concurrency slot back to the queue, and is waiting
            (indefinitely) for a kill/extend/wait decision. `timed_out` is
            retained from Slice 04/05 only for legacy/transport-error paths; it is
            no longer produced by the user-facing timeout flow (which always ends
            in `killed`, `completed`, or `failed` depending on the human's
            decision).
        awaiting_since:
          type: string
          format: date-time
          nullable: true
          description: >
            Present (non-null) while status == awaiting_decision; the moment the
            run entered the awaiting-decision state. Lets the UI show how long the
            prompt has been open. Cleared when the run resolves.

    # --- Extended from Slice 04/05 ---
    WebSocketOutputChunk:
      description: >
        Slice 05 message plus three new `type` values so a connected client sees
        kill / timeout-prompt / pause transitions live.
      type: object
      required: [type]
      properties:
        type:
          type: string
          enum:
            [queued, started, stdout, stderr, timeout, killed, done, error]
          description: >
            `timeout` (new) — the run hit its timeout and is now awaiting a
            decision; the run has already released its slot. No grace countdown is
            sent — the prompt stays open until the human answers.
            `killed` (new) — the run was stopped (per-tool or via pause);
            `message` carries the cause ("killed by user" / "engagement paused").
            On `extend`/`wait` the stream resumes with `started` (the run
            re-acquired a slot) followed by `stdout`/`stderr` again.
        message:
          type: string
          description: >
            On `timeout` chunks, a human-readable note that the slot was released
            and the queue is free to advance. On `killed` chunks, the cause.
```

Engagement read schema (additive; surfaced through the existing engagement detail/summary contract from Slice 01/02):

```yaml
    EngagementDetail:   # existing schema — one field added
      type: object
      properties:
        paused:
          type: boolean
          description: >
            Engagement-wide tool pause (§6.3). When true, no new tool runs may
            start. Default false. Toggled via POST /engagements/{id}/pause.
```

WebSocket note: no new WS endpoint. The new `timeout`, `killed` chunk types flow over the existing `GET /ws/tool-runs/{tool_run_id}` channel (the per-run pub/sub from Slice 04). The timeout prompt and its decision use the per-run channel for display and the new REST endpoint for the answer. On `extend`/`wait`, the same channel emits a fresh `started` chunk once the slot is re-acquired (which may be after a short wait if the host is busy).

## Data model changes

Minimal and additive. The kill registry, pause set, and timeout-decision rendezvous are all **in-process** (Decision 4, mirroring Slice 05); only the durable `paused` flag and the two new run statuses need the DB.

- **`engagements`** table: add one column.
  - `paused` BOOLEAN NOT NULL DEFAULT FALSE — engagement-wide tool pause (§6.3). Backfills existing rows to `false`.

- **`tool_runs`** table: extend the `status` domain only.
  - `status` is a free `VARCHAR(20)` (Slice 04, no native ENUM / no CHECK constraint — confirmed in `models.py`). Add `'killed'` and `'awaiting_decision'` as accepted values, enforced by the Pydantic `ToolRunStatus` literal. **No column DDL change.** (`'awaiting_decision'` is 17 chars, within `VARCHAR(20)`.) The `write-alembic-migration` skill must still inspect the live schema first to confirm no CHECK constraint snuck onto `status` since Slice 04.
  - No `queue_position`-style ephemeral column added. `awaiting_decision` is a transient runtime state owned by the in-process rendezvous; `awaiting_since` is a transient timestamp surfaced through the read schema but **not** persisted as a new column — it is derived in-process and held by the run's task state. (If a durable `awaiting_since` proves necessary for the UI after a reconnect, that is a follow-up; for this slice the WS chunk carries the prompt and a reconnect simply re-reads `status='awaiting_decision'`.) If the backend restarts while a row is in `awaiting_decision`, the existing startup reconciliation (`reconcile_stale_tool_runs`, Slice 05 task 8) marks `running`/`queued` rows failed — extend its WHERE clause to also catch `awaiting_decision` (Backend task 8).

- Index note: no new index. Kill and timeout-decision look up a single run by PK; pause iterates the in-process registry, not the DB.

## Tasks

Number tasks continuously across the slice (backend then frontend). Each commit subject cites `(task N)`.

### Backend tasks

Ordered. Each independently testable. Complexity: S/M/L.

1. **[S]** Schema + status additions. In `app/features/mcp/schemas.py`: add `'killed'` and `'awaiting_decision'` to `ToolRunStatus`; add `'timeout'` and `'killed'` to `WebSocketOutputChunk.type`; add `awaiting_since: datetime | None = None` to `ToolRunResult`; add `TimeoutDecision` (`decision: Literal["kill","extend","wait"]`, `extend_seconds: int = 30` with 1–300 bound). In `app/features/engagements/schemas.py`: add `paused: bool` to `EngagementDetail`/`EngagementSummary` reads, and add `EngagementPauseRequest` (`paused: bool`) + `EngagementPauseState`. Unit tests for the literal/bound validation.

2. **[S]** Engagement `paused` column + read mapping. Add `paused` (BOOLEAN, default FALSE) to the `Engagement` SQLAlchemy model; populate `paused` in `create_engagement` / `get_engagement` / list mappings in `app/features/engagements/service.py`. Unit tests: new engagements default `paused=false`; the field surfaces in detail/summary reads.

3. **[M]** Cancellation + pause + slot-release primitives in `app/features/mcp/concurrency.py` (or a sibling `killswitch.py` if it keeps `concurrency.py` cohesive — cite the choice; recommended: keep it in `concurrency.py` since it manipulates the same `_states` queue and slot pool). Add:
   - A per-run cancellation registry: `register_run(engagement_id, tool_run_id, task)` / `unregister_run(tool_run_id)` mapping a live run's `tool_run_id` → its `asyncio.Task` (the `_stream_to_channel` task) **and** its `engagement_id`. Replaces the anonymous `_background_tasks` set in `service.py` with a keyed map so a specific run can be found and cancelled. Keep the strong-reference guarantee (the map holds the task). The registry must reflect whether a run *currently* holds a slot, so that pause and timeout-release accounting stay correct (a run in `awaiting_decision` is registered-but-slotless — see below).
   - `kill_run(tool_run_id) -> Literal["cancelled","dequeued","awaiting","absent"]`: if the run has a live task currently holding a slot → `task.cancel()` (the task's `finally` releases the slot/lock and persists `status='killed'`; see task 5) and return `"cancelled"`; else if it has a FIFO ticket → remove the ticket, set its `admitted` event with a "killed" sentinel so the waiting `acquire` coroutine wakes and raises a `RunKilled` exception → `"dequeued"`; else if it is awaiting a timeout decision (registered, slot already released) → submit a `kill` decision to its rendezvous so the parked task resolves itself as killed → `"awaiting"`; else `"absent"`.
   - A per-engagement pause set: `is_paused(engagement_id) -> bool`, `set_paused(engagement_id, paused) -> tuple[int,int]` returning `(killed_running, dequeued)`. Setting paused True: refuse to admit (acquire's fast/slow paths AND the re-acquire-on-extend/wait path check the pause flag and raise `EngagementPaused`), kill every live task for the engagement (via the registry filtered by engagement_id), resolve every awaiting-decision run for the engagement as killed (count them in `killed_running`), and de-queue every ticket in that engagement's `state.queue` (wake each with the killed sentinel). Setting paused False clears the flag only.
   - `RunKilled(AdeptusError)` and `EngagementPaused(AdeptusError)` domain exceptions.
   - **Timeout slot-release / re-acquire rendezvous** (the heart of the Q1 change):
     - `release_for_decision(engagement_id, tool_run_id, handle)` — called by the streaming task when the deadline fires. It releases the admission `handle` (slot + host lock) **immediately** so the FIFO queue advances, marks the run as awaiting-decision in the registry (registered, slotless), and creates an `asyncio.Event` + decision slot.
     - `await_timeout_decision(tool_run_id) -> Literal["kill","extend","wait"]` — awaits the human's decision **with no timeout** (the prompt stays open indefinitely). Returns the submitted decision.
     - `submit_timeout_decision(tool_run_id, decision) -> bool` — the REST handler fills the rendezvous (returns False if no run is awaiting → router 409). Idempotent against double-submit (first writer wins; a second concurrent submit returns False).
     - On `extend`/`wait`, the streaming task calls `acquire(...)` again through the normal FIFO admission path (which respects the pause flag and the host lock). **Slot accounting invariant: exactly one acquire is outstanding per run at any instant — the slot is released in `release_for_decision` before the task awaits the decision, and re-acquired only after the decision resolves to extend/wait. There is never a window where the run holds two slots, and the released slot is never double-counted on the way back.** A `kill` decision (or pause) resolves the parked task without re-acquiring.
   - Extend `_reset()` to clear the new maps (registry, pause set, decision rendezvous).
   - Pure-asyncio unit tests (no DB): killing a running task cancels it and releases the slot (next waiter admits); killing a queued run removes its ticket and wakes its `acquire` with `RunKilled`; killing an absent/terminal run returns `"absent"`; **`release_for_decision` frees the slot so a same-host waiter admits while the first run is parked awaiting a decision**; `await_timeout_decision` blocks until `submit_timeout_decision` and never times out on its own; an extend/wait decision lets the task re-`acquire` (and that re-acquire respects the FIFO queue / pause flag / host lock); killing an awaiting-decision run returns `"awaiting"` and resolves it; `set_paused(True)` kills N running (incl. awaiting-decision) + dequeues M and blocks subsequent `acquire` with `EngagementPaused`; `set_paused(False)` re-allows; timeout rendezvous resolves with the submitted decision; `submit_timeout_decision` for a non-waiting run returns False; **no slot leak / no double-acquire across release→reacquire (assert the engagement's in-use slot count returns to a consistent value after a full park→extend→complete cycle)**.

4. **[M]** Wire the pause gate + cancellation registry into `service.execute_tool_run`. Before admission (both async and sync heavy paths AND light path — pause blocks *all* new runs, not just heavy ones), check `concurrency.is_paused(engagement_id)`; if paused, raise `EngagementPaused` (→ router 409) **before** any DB row is created (mirror the existing pre-flight `check_queue_capacity` placement so a paused engagement creates no `tool_runs` row and spawns no task). In the async path, after `asyncio.create_task(...)`, call `concurrency.register_run(engagement_id, tool_run_id, task)` instead of (or in addition to) the old `_background_tasks` set; unregister in the task's `finally` (task 5). Unit tests with mocked subprocess_manager: a run started while paused raises `EngagementPaused` and writes no row; an admitted run is registered in the kill registry.

5. **[L]** Make `_stream_to_channel` kill-aware and timeout-aware in `app/features/mcp/service.py`:
   - **Kill while running**: wrap the streaming loop so an `asyncio.CancelledError` (from `kill_run`/`set_paused`) is caught, the row is persisted `status='killed'`, a `killed` WS chunk is broadcast (`message` = cause), the slot/host-lock is released, and the channel discarded — all in the existing `finally`. Re-raise `CancelledError` after cleanup only if required by asyncio semantics; otherwise swallow it after persisting (detached task must not crash the loop). Be careful: the per-server lock in `subprocess_manager.stream_tool_call` is released when the async generator is closed (cancellation closes it) — document that cancelling the task abandons the subprocess's current call but does NOT kill the shared MCP subprocess (Decision 2 / Risk 2). The next `send_tool_call`/`stream_tool_call` re-acquires the per-server lock normally.
   - **Kill while queued**: when `acquire` raises `RunKilled` (woken by the killed sentinel), persist `status='killed'`, broadcast `killed`, and return without touching the subprocess.
   - **Timeout-confirm with slot release (the Q1 design)**: replace the current "outer readline timeout → `McpServerDown`" / "`exit_code==124` → `timed_out`" terminal behaviour. When the run hits `timeout_seconds`:
     1. **Close/suspend** the active `stream_tool_call` generator (this abandons the current subprocess call and releases the per-server lock — same mechanism as a kill; the underlying scan may keep running in the background, Risk 2) so the work is parked.
     2. Call `concurrency.release_for_decision(engagement_id, tool_run_id, handle)` to **release the engagement slot + host lock back to the queue** — a same-host queued run can now admit. (This is the invariant the human stressed: waiting on a human decision must never hold up the queue.)
     3. Persist `status='awaiting_decision'` (set `awaiting_since`), broadcast a `timeout` chunk (with a `message` noting the slot was released), and `await concurrency.await_timeout_decision(tool_run_id)` — **with no grace deadline; it waits indefinitely**.
     4. On the decision:
        - `kill` → persist `status='killed'`, broadcast `killed`, return (no re-acquire).
        - `extend` → re-`acquire(...)` through the normal FIFO admission path (may block on the host lock / queue; emit a fresh `started` once admitted), reset the deadline by `extend_seconds`, re-open `stream_tool_call`, and resume reading.
        - `wait` → re-`acquire(...)`, then continue with the timeout disabled (no deadline) until the stream completes.
     5. While parked, a `set_paused(True)` or an explicit `kill_run` resolves the rendezvous as `kill` (handled by task 3).
   - **Re-open semantics on extend/wait**: because the original generator was closed, resuming means starting a fresh `stream_tool_call` for the same tool args. Document in the docstring that for the demo `sleep_probe`/`run_httpx_heavy` tool this restarts the call (acceptable — Decision 1 / Risk 3); a real tool with checkpoint/resume is out of scope. The deadline is enforced at the service layer via a wrapping `asyncio.wait_for` per chunk-read with a mutable deadline (Decision 1) — `subprocess_manager`'s own `outer_timeout` must be raised / made effectively unbounded for streamed calls so it never pre-empts the service-layer prompt. Cite the chosen mechanism in the `_stream_to_channel` module docstring.
   - Unit tests with mocked `subprocess_manager` and a fake clock: running kill → `killed` + slot released; queued kill → `killed`, no subprocess call; **timeout → slot released (a same-host waiter admits) + `awaiting_decision` + `timeout` chunk**; `kill` decision → `killed`; `extend` → re-acquires a slot then resumes and completes; `wait` → re-acquires then completes with no further timeout; **pause while awaiting-decision → run resolves `killed`**; assert no slot leak / no double-acquire across the park→reacquire cycle.

6. **[M]** Add the two MCP-feature endpoints in `app/features/mcp/router.py`:
   - `POST /api/v1/tool-runs/{tool_run_id}/kill` — membership check via the existing `get_tool_run` + `get_engagement_for_member` chokepoint (404 for non-member / unknown run); calls a new `service.kill_tool_run(db, tool_run_id, user_id)` that runs `concurrency.kill_run`, and for a `"dequeued"`/already-terminal run persists `status='killed'` itself (the de-queued run's task handles its own persistence if it has one; the service must converge to a `killed` row). For an `"awaiting"` result the parked task resolves itself (the rendezvous was given `kill`); the service just returns the current row. Returns the current `ToolRunResult`. Idempotent on terminal runs.
   - `POST /api/v1/tool-runs/{tool_run_id}/timeout-decision` — membership-gated; calls `service.submit_timeout_decision(...)` → `concurrency.submit_timeout_decision`; 409 (inline JSONResponse, same pattern as the existing 503/429) when no run is awaiting a decision (returns False — covers "already resolved by another member"). Returns the current `ToolRunResult`.
   - Router unit tests: kill 200 for member / 404 for non-member / idempotent on completed run / 200 on an awaiting-decision run; timeout-decision 200 / 404 / 409.

7. **[M]** Add the pause endpoint + event seam. In `app/features/engagements/`: add `POST /api/v1/engagements/{engagement_id}/pause` to the engagements router; service method `set_engagement_paused(db, engagement_id, caller, paused)` updates the `paused` column and emits a new `engagement_paused_changed(engagement_id, paused)` event (add to `app/features/engagements/events.py`, mirroring `slot_limit_changed`). The `mcp` feature registers a listener at the composition root (in `app/main.py` startup, next to the existing slot-limit listener registration) that calls `concurrency.set_paused(engagement_id, paused)` and captures the `(killed_running, dequeued)` counts. **Sequencing note**: the endpoint must return the `EngagementPauseState` counts produced by the in-process kill, so either (a) the listener writes the counts to a place the service can read, or (b) the service calls a thin mcp-exposed function directly. Recommended (cite): the engagements service emits the event; the mcp listener performs the kills and returns counts via the event-dispatch return value (extend the seam to collect listener return values), keeping the dependency direction mcp → engagements. Unit tests: pause updates the column + emits the event; the registered mcp listener kills/de-queues/resolves-awaiting and returns counts; resume clears the flag and emits.

8. **[S]** Startup reconciliation touch-up: extend `reconcile_stale_tool_runs` in `app/features/mcp/repository.py` so its WHERE clause also matches `status='awaiting_decision'` (in addition to `'queued'`/`'running'`) → `failed` on startup, since the in-process timeout rendezvous is gone after a restart. Also: on startup, the `paused` flag survives (DB column) but no runs are in flight — document that a paused engagement after restart simply continues to reject new runs until resumed (correct). Unit test: an `awaiting_decision` row at startup becomes `failed`.

### Frontend tasks

Ordered. Complexity: S/M/L. Numbering continues.

9. **[S]** Run `make generate-api` to regenerate `frontend/src/shared/api/` with: the `paused` field on engagement reads, the new `killed` / `awaiting_decision` statuses, the `awaiting_since` field, the `EngagementPauseRequest` / `EngagementPauseState` / `TimeoutDecision` schemas, and the new kill / timeout-decision / pause paths. (The new WS chunk types are NOT in the OpenAPI doc — update the hand-declared `WebSocketOutputChunk` interface in `useToolRunStream.ts` in task 10.)

10. **[M]** Extend `frontend/src/features/mcp/hooks/useToolRunStream.ts`: add `'timeout'` and `'killed'` to the local `WebSocketOutputChunk` interface and the state machine. On `timeout`: expose `{ awaitingTimeout: true }` (no countdown — the prompt is open-ended; optionally surface `awaiting_since` from the run query to show elapsed time, but do NOT show a kill countdown). On `killed`: set `isDone=true`, expose a `killed: true` flag, append the cause message, close the socket. On a `started` chunk arriving *after* `awaitingTimeout` was set (the extend/wait re-acquired a slot): clear `awaitingTimeout` and resume normal streaming. Extend the returned `ToolRunStream` type. Unit tests with a mocked WebSocket: a `stdout`→`timeout`→`killed` sequence transitions correctly; a `timeout`→(decision elsewhere)→`started`→`stdout`→`done` continuation works (the prompt clears when the run resumes); the timeout state shows no countdown.

11. **[M]** Add kill + timeout-decision API hooks in `frontend/src/features/mcp/api.ts`: `useKillToolRun()` (POST `/tool-runs/{id}/kill`, invalidate `toolQueueKey` + `toolRunsKey`), `useTimeoutDecision()` (POST `/tool-runs/{id}/timeout-decision`, invalidate `toolQueueKey` since extend/wait re-enters the queue). Unit tests with MSW: kill fires and invalidates; decision fires with the chosen body.

12. **[M]** Update `frontend/src/features/mcp/components/ToolOutputConsole.tsx` + test: add a **Stop** button shown while the run is `running`, `queued`, or `awaiting_decision` (calls `useKillToolRun`); render a **"Killed"** badge when the stream reports `killed`; when `awaitingTimeout` is set, render the **"Timed out — what do you want to do?"** prompt with **Kill** / **Extend (+30s)** / **Wait** buttons (each fires `useTimeoutDecision` with the matching decision) plus copy noting the run's slot was released and the prompt will stay open until answered (an elapsed-time readout from `awaiting_since` is optional; **no kill countdown**). The console needs the `toolRunId` (already a prop) to drive the mutations. Tests: Stop button visible while running/queued/awaiting-decision and fires kill; Killed badge renders; timeout prompt renders all three buttons and each fires the right decision; the prompt shows no kill countdown.

13. **[M]** Add pause controls. Add `useEngagementPause()` + a pause-state read to `frontend/src/features/engagements/api.ts` (the `paused` flag comes from the existing engagement detail query — extend its select / consume the regenerated field). Add a **Pause / Resume** toggle in `ToolRunnerPanel.tsx` header (and/or the workspace banner area — keep it in the panel header for cohesion; cite). When `paused`, render a persistent **"Engagement paused — tool runs are halted"** banner in the panel and disable the `ToolRunnerForm` Run button. Wire the pause mutation to invalidate the engagement detail + tool-queue queries. Tests: toggle fires the pause mutation with `paused:true`/`false`; paused banner renders and Run is disabled when `paused`; a 409 from POST /tool-runs while paused surfaces a clear message in `ToolRunnerForm`.

## Test plan

- **Unit (backend)** — coverage ≥80% on `app/features/mcp/` (and the new engagements paths covered by the existing engagements suite):
  - `test_killswitch.py` (pure asyncio, no DB) — the core matrix from task 3: running-kill cancels the task + releases the slot (next waiter admits); queued-kill removes the ticket + wakes `acquire` with `RunKilled`; absent/terminal-kill → `"absent"`; **`release_for_decision` frees the slot so a same-host waiter admits while the first run is parked**; `await_timeout_decision` blocks indefinitely until `submit_timeout_decision`; extend/wait re-`acquire` respects the FIFO queue / host lock / pause flag; awaiting-decision kill → `"awaiting"` + resolves; `set_paused(True)` kills running + awaiting-decision + dequeues + blocks `acquire` with `EngagementPaused`; `set_paused(False)` re-allows; pause keyspace isolation (pausing engagement A leaves B's runs untouched); `submit_timeout_decision` non-waiting → False; double-submit → second returns False; **slot-accounting invariant: in-use slot count is consistent after a full park→extend→complete cycle (no leak, no double-acquire)**.
  - `test_mcp_service_kill.py` — `_stream_to_channel` with mocked `subprocess_manager`: cancel mid-stream → `status='killed'` + `killed` chunk + slot released + channel discarded; queued-kill → `killed`, subprocess never called; `execute_tool_run` while paused → `EngagementPaused`, no row created; admitted run registered in the kill registry and unregistered on completion.
  - `test_mcp_service_timeout.py` (fake clock) — timeout fires → **slot released (same-host waiter admits) + `awaiting_decision` + `timeout` chunk**; `kill` decision → `killed`; `extend` → re-acquires a slot then resumes and completes; `wait` → re-acquires then completes with no further timeout; pause while awaiting-decision → `killed`; **no slot leak / no double-acquire across park→reacquire**.
  - `test_mcp_router_kill.py` — `POST /tool-runs/{id}/kill` 200 for member, 404 for non-member, idempotent on a completed run, 200 on an awaiting-decision run; `POST /tool-runs/{id}/timeout-decision` 200 / 404 / 409-when-not-waiting.
  - `test_engagements_pause.py` — `POST /engagements/{id}/pause` 200 for member with correct counts, 404 for non-member; column updated; `engagement_paused_changed` emitted; new engagements default `paused=false`.
  - `test_engagements_pause_event.py` — the registered mcp listener kills/de-queues/resolves-awaiting and returns `(killed_running, dequeued)`; resume clears with `(0,0)`.
  - `test_mcp_repository_startup_reconcile.py` (extend) — an `awaiting_decision` row at startup → `failed`.
  - Schema mapping test: `_row_to_result` returns `killed` / `awaiting_decision` (with `awaiting_since`) correctly; `EngagementPauseState` counts shape.

- **Unit (frontend)** — coverage ≥60% on `src/features/mcp/`:
  - `useToolRunStream.test.ts` — `timeout` → `awaitingTimeout` set (no countdown); `killed` → done + killed flag + message; timeout-then-resume (`started` chunk) clears the prompt.
  - `ToolOutputConsole.test.tsx` — Stop button visible + fires kill while running, while queued, and while awaiting-decision; Killed badge; timeout prompt with Kill/Extend/Wait, each firing the right decision; no kill countdown shown.
  - `api.test.tsx` — `useKillToolRun` fires + invalidates queue/runs; `useTimeoutDecision` fires with chosen body + invalidates queue; `useEngagementPause` fires with paused flag.
  - `ToolRunnerPanel.test.tsx` — Pause/Resume toggle fires the mutation; paused banner renders and disables Run; 409 surfaces a clear message.

- **Integration** (test compose stack + `make sandbox`):
  - `test_kill_integration.py` — POST a heavy async run (`run_httpx_heavy`/`sleep_probe`, long `hold_seconds`) against the sandbox host; observe `status='running'`; POST `/kill`; assert the run reaches `status='killed'` quickly and a previously-queued same-host run transitions `queued`→`running` (kill freed the slot + host lock).
  - `test_pause_integration.py` — start one running + one queued heavy run; POST `/engagements/{id}/pause` `{paused:true}`; assert both reach `status='killed'`, `killed_running==1` / `dequeued==1`, and a subsequent POST `/tool-runs` returns 409; resume; assert a new run succeeds.
  - `test_timeout_integration.py` — start a heavy run whose `hold_seconds` exceeds `timeout_seconds`, **with a second same-host run queued behind it**; observe the first run hit the `timeout` chunk / `awaiting_decision` AND the queued run advance to `running` (proving the slot was released and the queue was not blocked while awaiting the decision); POST `/timeout-decision` `{decision:"kill"}`; assert `status='killed'`. (Extend/wait re-acquire is a unit test with a fake clock — the integration variant proves the slot-release-does-not-block-queue round-trip, which is the Q1 invariant.)

- **E2E** — one Playwright test (`kill-switch.spec.ts`): log in → open engagement → start a heavy run → click **Stop** → assert the **Killed** badge appears. This is the headline user-visible journey (qualifies under "critical journeys only"). The timeout and pause flows are covered by unit + integration tests to keep the Playwright surface minimal.

## Acceptance criteria

- `make test` passes with coverage gates met (≥80% `app/features/mcp/`, ≥60% `src/features/mcp/`).
- `make lint` passes (ruff, mypy, eslint, tsc --noEmit).
- `make dev` + `make sandbox`:
  - Starting a heavy run and clicking **Stop** ends it `status='killed'` within ~1 s; a queued same-host run then starts automatically (`kill-switch.spec.ts` proves the Stop→Killed path).
  - Clicking **Stop** on a *queued* run removes it from `GET /tool-queue` and marks it `killed` without ever invoking a subprocess.
  - Clicking **Pause** kills all in-flight runs (`killed`), de-queues all waiting runs, resolves any awaiting-decision runs as killed, shows the paused banner, and makes `POST /api/v1/tool-runs` return **409**; **Resume** restores normal operation.
  - A run that exceeds its `timeout_seconds` shows the **"Timed out — kill / extend / wait"** prompt and **releases its slot so a queued same-host run advances while the prompt is open**; the prompt stays open indefinitely (no auto-kill); **Kill** ends it `killed`, **Extend** re-acquires a slot and continues it, **Wait** re-acquires a slot and lets it finish.
- `POST /api/v1/tool-runs/{id}/kill` and `POST /api/v1/engagements/{id}/pause` and `POST /api/v1/tool-runs/{id}/timeout-decision` all return **404** for a non-member (no existence disclosure).
- The freed slot / host lock from a kill **and from a timeout entering awaiting-decision** is observably re-used (a queued run advances) — proves no slot/lock leak on cancellation or on release-for-decision.
- A backend restart while a run is `awaiting_decision` leaves that row `failed`.
- `gh pr view` shows green CI.

## Risks

- **Risk 1 — Cancelling the streaming task must release the slot AND the per-server subprocess lock, with no leak.** `_stream_to_channel` acquires (a) the engagement slot/host-lock via `concurrency.acquire` and (b) the per-server `asyncio.Lock` inside `subprocess_manager.stream_tool_call` (held for the whole stream). On `task.cancel()`, the `async for` over the generator raises `CancelledError`; closing the generator releases its `async with handle.lock`, and the existing `finally` in `_stream_to_channel` releases the admission handle (Slice 05 Risk 3 already covers this). Mitigation: keep the admission release in the `finally` (already there); add a unit test that cancels mid-stream and asserts BOTH the engagement slot is freed (next waiter admits) AND a subsequent `send_tool_call` on the same server succeeds (per-server lock not leaked). Decision 2 documents that we abandon the *current* tool call, not the shared subprocess.

- **Risk 2 — Kill does not actually terminate the OS process / the scan keeps running.** Because the MCP subprocess is shared per server (Slice 03/04 model), we cannot `kill()` it to stop one tool call without killing every run on that server. Cancelling the task stops us *reading* the output and releases the locks, but the underlying scan process inside the MCP server may keep running until it finishes or the server is asked to stop it. The same applies to a timed-out run that releases its slot: the parked work may continue in the background even though we have stopped reading it. Mitigation: this is an accepted limitation of the in-process model — documented in Decision 2; true per-run kill (`docker kill`) arrives with the §6.1 per-engagement execution model. For the demo `sleep_probe`/`run_httpx_heavy` tool, the minimal honest behaviour for this slice is: stop streaming + free the slot + mark `killed` (or park as `awaiting_decision`), and note in the UI/docstring that the underlying process may complete in the background, and that **extend/wait restart the call** rather than truly resuming a checkpoint. Pin this expectation in `test_kill_integration.py` (assert status `killed` and slot freed; do NOT assert the OS process died).

- **Risk 3 — Service-layer deadline must own the timeout so the run can park instead of failing.** `stream_tool_call` enforces `timeout_seconds + _TIMEOUT_MARGIN_SECONDS` internally and raises `McpServerDown` on timeout — it does not surface a "soft timeout, keep waiting" signal. Mitigation (Decision 1): move deadline enforcement to the service layer. The service wraps each `queue.get()`/generator `__anext__` in an `asyncio.wait_for` with a *mutable* deadline; when the deadline fires the service closes the generator, releases the slot via `release_for_decision`, emits the `timeout` prompt, and awaits the decision instead of letting `subprocess_manager` kill the call. `subprocess_manager`'s own `outer_timeout` must be raised (or made effectively unbounded for streamed calls) so it never pre-empts the service-layer prompt — adjust `_TIMEOUT_MARGIN_SECONDS` handling or pass a large transport timeout for streamed calls and let the service own the user-facing deadline. Document the exact mechanism in the `_stream_to_channel` docstring and pin it with `test_mcp_service_timeout.py`.

- **Risk 4 — In-process kill registry / pause set / timeout rendezvous are not multi-worker safe.** Same posture as Slice 04 pub/sub and Slice 05 admission manager. Across multiple uvicorn workers a kill or timeout-decision issued on worker A cannot reach a task on worker B. Mitigation: acceptable for single-process Compose (documented in the module docstring, same as Slice 05 Risk 2). Multi-worker would need a shared cancellation/decision signal (Postgres LISTEN/NOTIFY or Redis). Do NOT add it now.

- **Risk 5 — Pause must block the light lane too, not just heavy runs.** Slice 05's admission manager only governs heavy runs; light runs never call `acquire`. The pause gate must therefore live in `execute_tool_run` (before the weight branch) so a light httpx run is also rejected while paused — otherwise the pause is incomplete. Mitigation: place the `is_paused` check at the top of `execute_tool_run`, before the heavy/light branch and before any DB write (task 4); test a light run is rejected 409 while paused.

- **Risk 6 — "pause the AI" clause is unsatisfiable today.** §6.3 says the global pause also pauses the AI, but there is no AI/chat feature yet (Slice 11). Mitigation: deliver the durable `paused` flag and the `engagement_paused_changed` event now; the AI slices subscribe to it later. Documented in Decision 5 and the Out-of-scope list. This is a deliberate partial implementation of the §6.3 sentence, not a miss — the requirement is fully wired on the tool side and forward-compatible on the AI side.

- **Risk 7 — Slot accounting must stay correct across release-and-reacquire (no leak, no double-acquire).** The Q1 design has a timed-out run release its slot/host-lock when it enters `awaiting_decision`, then re-acquire on `extend`/`wait`. This introduces a window where the run holds NO slot (correct — that is what frees the queue) and a re-entry into the FIFO admission path. The hazards are: (a) releasing twice (the original handle is released in `release_for_decision`, so the `finally` must not release it again — track handle ownership so the `finally` only releases the *currently held* handle); (b) acquiring twice (a `kill`/pause arriving during the re-acquire must not leave a dangling acquired slot — the re-acquire must be cancellation-aware and release on `CancelledError`); (c) the re-acquire bypassing the queue or the pause flag. Mitigation: the re-acquire goes through the *same* `acquire(...)` entry point (respects FIFO + host lock + pause), the streaming task tracks exactly one outstanding handle at a time, and `test_killswitch.py` / `test_mcp_service_timeout.py` assert the engagement in-use slot count is consistent after a full park→extend→complete cycle and that a pause during the awaiting/re-acquire window resolves to `killed` with the slot count returning to baseline. This is the central correctness risk introduced by the Q1 amendment.

- **Risk 8 — An awaiting-decision run waits forever if no one answers (no fail-safe auto-kill).** Per the Q1 decision there is deliberately no grace-window auto-kill: a timed-out run parks indefinitely until a human (any member) answers or pauses the engagement. The trade is that an unanswered prompt leaves a run in `awaiting_decision` indefinitely — but because the slot is released, this does NOT block the queue or other work; the run simply sits parked. Mitigation: this is the intended behaviour (the human explicitly chose "wait indefinitely, never block the queue"). The only durable footgun is a backend restart, which reconciles `awaiting_decision` rows → `failed` (task 8). The engagement-wide pause also resolves any stuck awaiting-decision runs as killed, giving members a bulk escape hatch.

## Decisions

- **Decision 1 — Service-layer mutable deadline.** The user-facing timeout is enforced in `_stream_to_channel`, not in `subprocess_manager`. See Risk 3.
- **Decision 2 — Kill abandons the current tool call, not the shared MCP subprocess.** See Risk 2. True per-run process kill awaits the §6.1 Docker execution model.
- **Decision 3 — Demo heavy tool only.** Kill/timeout are exercised against the Slice 05 `sleep_probe`/`run_httpx_heavy` demo tool, not real nmap/gobuster (Slice 26).
- **Decision 4 — In-process registry/pause/rendezvous.** Mirrors Slice 04/05 posture; not multi-worker safe (Risk 4).
- **Decision 5 — "Pause the AI" deferred.** Durable `paused` flag + `engagement_paused_changed` event shipped now; AI subscription deferred to Slice 11+ (Risk 6).
- **Decision 6 — Timeout releases the slot and waits indefinitely (Q1).** When a run hits its timeout it enters `awaiting_decision` and **releases its concurrency slot + host lock back to the FIFO queue** so waiting on a human decision never holds up the queue. The work is parked; the prompt stays open with **no auto-kill grace window**. `extend`/`wait` re-acquire a slot through the normal admission path (respecting the queue, host lock, and pause flag); `kill`/pause resolve without re-acquiring. Slot accounting must stay leak-free across release→reacquire (Risk 7). This supersedes the earlier draft's 60 s grace-window-then-auto-kill design.
- **Decision 7 — Any member, no confirmation (Q2).** Any engagement member (membership-gated) may stop any run, pause/resume the engagement, or answer a timeout prompt, with no confirmation dialog — stopping a scan fast is a safety action and friction would be counterproductive (§17.2). Attribution recorded once Slice 10 (audit) lands.

## Open questions for the human

None. Both prior open questions are resolved:

- **Q1 (timeout fail-safe / grace window) — RESOLVED.** The human removed the auto-kill grace window entirely. On timeout the run enters `awaiting_decision`, **releases its concurrency slot + host lock back to the queue** (the work is parked), and waits **indefinitely** for the human's kill/extend/wait decision — waiting on a human decision must never hold up the queue. `extend`/`wait` re-acquire a slot through the normal FIFO path. Captured in Decision 6, the §6.2 traceability note, the `awaiting_decision` status, the slot-release primitives (task 3), the streaming logic (task 5), and Risks 7 and 8.
- **Q2 (who can stop/pause + confirmation) — RESOLVED / CONFIRMED.** Any engagement member (membership-gated) may stop any run / pause the engagement / answer a timeout prompt, with no confirmation dialog. Captured in Decision 7 and the §17.2 traceability note.

## Security review required?

No. This slice does not touch auth (it reuses the existing session-cookie + `get_engagement_for_member` membership chokepoint unchanged), MCP subprocess *spawning* semantics (kill cancels the reader task and releases locks; it does not change how subprocesses are launched or what they run), the hash-chain audit log (does not exist yet — Slice 10), the single-writer graph process, RAG isolation, egress/secrets, or approvals. Kill / pause / timeout-decision are safety/control mechanisms layered in front of the already-reviewed Slice 05 admission scheduler. The three new endpoints all reuse the established membership-gated 404-no-disclosure pattern, and the pause set / kill registry / timeout rendezvous are keyspace-isolated by `engagement_id` / `tool_run_id`.

(Note for the finish-slice reviewer: confirm the kill registry and pause set are namespaced by `engagement_id` so one engagement's pause/kill cannot reach another's runs — that is the one isolation-adjacent invariant introduced here, identical in spirit to the Slice 05 lock-keyspace note. Also confirm cancellation never leaks a slot, host lock, or the per-server subprocess lock — Risk 1 — and that the new release-and-reacquire-on-timeout path keeps slot accounting consistent with no leak and no double-acquire — Risk 7.)

## Progress

(The stop-checkpoint hook and compact-handoff skill append here. Leave empty at planning time.)
- 2026-06-03T14:54:00Z — 6dcfac1 chore(plan): flip slice 05 in-review → done (#18)
- 2026-06-03T15:14:54Z — 6dcfac1 chore(plan): flip slice 05 in-review → done (#18)
- 2026-06-03T15:16:14Z — 6dcfac1 chore(plan): flip slice 05 in-review → done (#18)
- 2026-06-03T16:39:00Z — df76703 feat(slice-06): add pause/resume controls with banner and 409 handling (task 13)
