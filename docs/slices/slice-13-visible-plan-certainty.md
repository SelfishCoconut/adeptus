# Slice 13: Visible plan + certainty signaling

**Branch**: `slice-13-visible-plan-certainty`
**GitHub Issue**: #38
**Status**: planned
**Risky**: no

---

## Goal

Make the AI maintain an explicit running plan / todo list the user can watch update, and have it flag low-confidence claims with a stated certainty percentage — both in the chat reply and as a certainty badge on referenced graph items.

## User-visible demo

After this slice is merged, with `make dev` up (Ollama reachable, `qwen3.5:9b` pulled,
an engagement that already works as in Slices 11/12):

- Open an engagement workspace. The left chat pane works exactly as in Slice 11/12.
- Ask a multi-step question, e.g. "how should I approach testing the login flow on this
  target?". Send it. The assistant reply streams in token-by-token as before — but now,
  **above the reply text**, a **Plan panel** appears showing the AI's running todo list:
  an ordered list of steps, each with a status (`todo` / `in-progress` / `done`), e.g.
  1. *Enumerate the login endpoint* — `done`
  2. *Test for SQL injection on the username field* — `in-progress`
  3. *Check session-cookie flags* — `todo`
- Send a follow-up ("ok, the SQLi didn't work, what next?"). The Plan panel **updates** —
  step 2 flips to `done`, a new step is appended, and the panel reflects the latest turn's
  plan. The plan is the AI's current plan as of the most recent turn (it is *replaced* each
  turn, not endlessly appended — see Design notes).
- When the AI makes a claim it is unsure about, the reply renders an inline **certainty
  badge**, e.g. "This service is likely Apache 2.4 **(60% certain)**". Low-confidence
  claims (below a threshold) are visually flagged (amber); high-confidence ones render
  plainly or with a subtle high-confidence affordance.
- If the AI's claim references a **graph node** (by id), that node shows a **certainty
  badge** in the right Graph pane and on its detail popover, e.g. a `service` node tagged
  "60%" — the §5.3 "certainty percentage … on graph items" half. Hovering shows which turn
  asserted it.
- Open the **Debug** panel (Slice 12) on the assistant reply: in addition to the injected
  graph subset and raw prompt/output, it now shows the **parsed plan** and the **list of
  certainty-tagged claims** the server extracted from this turn — so a power user can see
  exactly what was parsed out of the model's structured output (§14).
- Ask a plain question the AI is confident about with no real plan ("what port does HTTPS
  use?"): the reply renders normally, the Plan panel shows a single trivial/empty plan (or
  collapses), and no spurious certainty badges appear — the feature degrades gracefully
  when the model emits no structured block.
- Everything stays **private per user** (§5.4) and the **privacy banner** (§5.5) is
  untouched: on a `local_only` engagement nothing leaves the local network; the plan and
  certainty data are produced by the **local** Ollama only.

## Out of scope

This slice adds the *visible plan* and *certainty signaling* §5.3 clauses to the existing
local chat path. It deliberately does NOT do the following (each is separately tracked):

- Does **NOT** implement **proactive clarifying questions** (the third §5.3 "AI Behavior"
  bullet — "when ambiguous, the AI asks clarifying questions rather than guessing
  silently"). That behavior is a prompt/UX concern that can ride on the same structured-
  output seam later; this slice ships only the *visible plan* and *uncertainty signaling*
  bullets named in the PROJECT_PLAN entry. The clarifying-question bullet is noted as a
  follow-up (no slice is gated on it).
- Does **NOT** make the AI **write certainty onto the graph as a node property** or create/
  edit graph nodes. The AI cannot write the graph until the AI-action/approval work (Slice
  16) and never outside the single-writer process (ADR-0001). Certainty on graph items is
  rendered as a **per-turn overlay** read from the chat turn's parsed claims (the chat turn
  that asserted it), NOT persisted as a `graph_nodes` column or property (§8.2 / §17.4
  anti-pattern — see Design notes). The §8.2 "AI writes automatically, flagging low-
  confidence inferences" graph-write-side behavior is Slice 16+.
- Does **NOT** implement the **cloud LLM path or pattern-friction egress** (Slice 14). The
  prompt that asks for structured plan/certainty output is sent only to the **local**
  Ollama.
- Does **NOT** implement **personas** (Slice 15). The structured-output instruction is
  appended to the single neutral Slice-11 `SYSTEM_PROMPT`; when personas land they own the
  base prompt and this slice's plan/certainty instruction composes on top.
- Does **NOT** implement **AI suggestions as a separate kanban/task queue** (§11.6
  explicitly forbids that — "AI suggestions appear inline in chat, no separate kanban").
  The Plan panel is an **inline, per-turn rendering of the AI's own plan**, not a
  user-editable task board, not a cross-turn aggregated backlog, and not actionable
  buttons. The user cannot check off / reorder / edit plan steps in this slice.
- Does **NOT** add **token budgeting or summarization** (still the Slice-12 decision: full
  verbatim subset). The structured-output block the model returns is parsed, not budgeted.
- Does **NOT** change the **WebSocket frame contract** in a breaking way for plain text. The
  reply still streams as `token`/`done`/`error`; the structured plan/certainty data is
  parsed server-side from the accumulated full reply and delivered on the `done` frame (and
  persisted for the debug/history read) — see Design notes for exactly how.
- Does **NOT** add **provenance columns** to any graph/finding entity. The parsed plan and
  claims are persisted on the **assistant `chat_message` row** (the turn that produced
  them), reusing the existing `graph_context` JSONB seam pattern — see Data model changes.
- Does **NOT** widen `core/` or `shared/`. Backend changes live under
  `app/features/chat/`; frontend changes under `src/features/chat/` (the Graph-pane badge
  overlay consumes the existing `src/features/graph/` node store, it does not modify it).

## Requirements traceability

- **§5.3 — Visible plan** — quoted:
  > **Visible plan:** the AI maintains an explicit running plan / todo list visible to the
  > user, updated as it progresses.

  **Headline clause (a).** This slice instructs the model (via an appended system
  instruction) to emit, alongside its prose answer, a structured **plan**: an ordered list
  of steps each with a `status` (`todo` / `in_progress` / `done`). The server parses it,
  persists it on the assistant turn, and the frontend renders it as the inline **Plan
  panel** above the reply, updated each turn ("updated as it progresses"). "Visible to the
  user" = the Plan panel in the chat pane.

- **§5.3 — Uncertainty signaling** — quoted:
  > **Uncertainty signaling:** AI proactively flags low-confidence claims with a stated
  > certainty percentage (in chat and on graph items).

  **Headline clause (b).** The model is instructed to tag claims it is unsure about with a
  certainty percentage and (when the claim is about a known graph node) the node id. The
  server parses these **claims** (text + `certainty` 0–100 + optional `node_id`), persists
  them on the turn, and the frontend renders: (i) an inline certainty badge in the chat
  reply ("in chat"), and (ii) a certainty badge overlaid on the referenced node in the
  Graph pane ("and on graph items"). Low-confidence (below a configurable threshold) is
  visually flagged.

- **§5.4 — Private chat per user** — the plan and claims are attached to the caller's own
  assistant turn and read back only by the owner (the same membership + ownership chokepoint
  as Slices 11/12). The graph-item certainty overlay is rendered from the **caller's own**
  recent turns only — one user's certainty annotations are never shown to another (§17.1).

- **§14 — AI debug panel** — quoted:
  > AI debug panel: raw prompts, model outputs, tool calls, and the exact "relevant subset"
  > of the graph used per turn.

  This slice extends the Slice-12 debug panel (`GET .../debug`) to also surface the
  **parsed plan** and the **parsed certainty claims** for the turn — so a power user can see
  exactly what the structured-output parser extracted from the raw model output (the raw
  output is already shown by Slice 12; this adds the *parsed* view next to it). No new
  endpoint; the existing `ChatTurnDebug` schema gains two fields.

- **§5.5 — No redaction** — the structured-output instruction asks the model to *add* a
  plan/certainty block; it never strips or rewrites the user's content or the model's prose.
  The plan/claim text is rendered verbatim. The local path has no egress; cloud egress
  friction is Slice 14.

- **§8.2 / §17.4 — audit is the source of truth; no provenance on entities** — quoted:
  > **No provenance attribution:** nodes, findings, and chat messages are not tagged with
  > who/what created them. The audit log is the source of truth.

  Certainty is NOT written as a `graph_nodes` column/property (that would be exactly the
  forbidden "tag the node with AI-asserted data" provenance smear, and it would also require
  a graph write through the single writer, which chat must never do). It lives on the chat
  turn that asserted it; the Graph pane reads it as an ephemeral overlay. The `ai_call`
  audit payload is widened with plan/claim *counts* only (forensic record of how much
  structured output a turn produced) — no new audit action/table, no hash-chain change.

- **§11.6 — AI suggestions inline, no kanban** — quoted:
  > AI suggestions appear inline in chat (no separate kanban / task queue).

  The Plan panel is inline in the chat pane and per-turn; it is explicitly NOT a separate
  task board. This slice honors §11.6 by rendering the plan as read-only AI output, not as a
  user task queue.

- **§11.1 — frontend stack** — the Plan panel and certainty badges render with Tailwind +
  shadcn; the reply prose stays `react-markdown` (Slice 11). No new heavy dependency.

- **§17.6 — "The AI shows its work"** — quoted:
  > Visible plan, certainty percentages, explicit clarifying questions when ambiguous, and
  > an inspectable "relevant subset" of the graph in the debug panel.

  This slice delivers the first two of the four (visible plan, certainty percentages);
  Slice 12 delivered the fourth (inspectable subset); the clarifying-questions item is the
  noted follow-up (Out of scope).

- **ADR-0001 — single-writer** — unchanged: chat reads the live graph (to validate that a
  claim's `node_id` belongs to the engagement) via the existing `load_live_graph` read path;
  it never writes the graph and never goes through the writer queue.

- **ADR-0004 — default Ollama model** — the structured-output instruction targets the
  default `qwen3.5:9b` (tool-capable per ADR-0004, but here we use plain instructed JSON
  output, not function-calling — see Design notes / Open Questions).

## Design notes (load-bearing decisions)

### How the model produces plan + certainty: instructed structured block, parsed server-side

The local model streams free text token-by-token (Slice 11). To get a *visible plan* and
*certainty percentages* we need structured data out of that stream. Decision: append a
**structured-output instruction** to the system prompt telling the model to end its reply
with a single fenced, machine-readable **metadata block** (a delimited JSON object)
containing the plan and the certainty claims, e.g.:

```
<adeptus-meta>
{ "plan": [ {"step": "...", "status": "in_progress"}, ... ],
  "claims": [ {"text": "...", "certainty": 60, "node_id": "..."}, ... ] }
</adeptus-meta>
```

(Exact delimiter/schema decided at implementation; the marker is a stable sentinel chosen
to be unlikely in normal prose and easy to strip.) Rationale:

- **Why not function/tool calling (ADR-0004 says the model is tool-capable)?** Tool calling
  is reserved for Slice 16 (AI-initiated actions / approvals). Using it here would entangle
  this slice with that surface. An instructed-text block keeps this slice purely additive on
  the existing text-streaming path and works even if a model variant has weak tool support.
  Flagged in Open Questions (Q1) in case the human prefers Ollama structured-output mode.
- **Why a trailing sentinel block rather than streaming structured frames?** The WS frame
  contract (Slice 11/12) stays `token`/`done`/`error`. Tokens stream verbatim as today; the
  server **accumulates the full reply** (it already does — `chunks` in
  `stream_assistant_reply`), then at finalize **splits off the metadata block**, parses it,
  persists the parsed plan/claims, strips the block from the stored prose `content`, and
  delivers the parsed structures on the **`done` frame** (and via the history/debug reads).
  So the user sees clean prose stream, then the Plan panel and badges resolve when the turn
  completes. (Streaming the plan live mid-turn is deferred — Open Question Q2.)

### Parsing is tolerant and never breaks a turn

A `chat/plan_parser.py` module owns extraction: find the sentinel block, JSON-parse it,
validate/coerce (`status` ∈ {todo,in_progress,done} default `todo`; `certainty` clamped to
0–100; `node_id` kept only if it is a well-formed UUID). On **any** failure — no block, malformed
JSON, wrong shape — it returns an **empty plan + empty claims** and leaves the prose
untouched. A turn never fails because the model didn't cooperate (graceful degradation;
the demo's "plain question" case). The stripped prose is what gets persisted as the
assistant `content` and what the Markdown renderer shows; the sentinel block is never shown
to the user as raw text.

### Where plan + claims are persisted: the existing `graph_context` JSONB, renamed-in-spirit

Slice 12 added a nullable JSONB `graph_context` column on `chat_messages` holding the
per-turn debug record (subset nodes/edges + context_block + raw_prompt). The plan and claims
are the **same kind of per-turn AI-output metadata**, with the same 1:1 lifecycle, same
per-user/per-engagement ACL, and the same read endpoints (history + debug). Decision: **add
`plan` and `claims` keys to that same JSONB blob** rather than a new column or table.

- The column is general per-turn AI metadata; Slice 12 happened to name it `graph_context`.
  This slice writes additional top-level keys (`plan`, `claims`) into the same JSON object.
  (Optional, non-blocking: a follow-up migration could rename the column to
  `turn_metadata` for clarity — flagged in Open Questions Q3; not required for this slice and
  avoided here to keep the migration trivial.)
- This is **not** a provenance column on a graph entity (§8.2): it lives on the chat turn
  that produced the output, describing what the AI said, which is debug/transparency data,
  not shared graph truth. Certainty is read *from here* and overlaid on the graph; it is
  never written *into* `graph_nodes`.

The plan/claims must reach the client in two ways:

1. **On completion of a live turn** — delivered on the `done` WS frame (so the Plan panel
   and badges appear the moment the stream ends) **and** available via the message read.
2. **On history reload** — the message list read (`GET .../chat/messages`) must expose the
   plan and the in-chat claims for each assistant row so a reloaded conversation re-renders
   its plans/badges without opening the debug panel. Decision: extend `ChatMessageRead` with
   optional `plan` and `claims` fields (populated from the JSONB; null/empty for user rows
   and pre-slice rows). The full raw-prompt/subset debug detail stays behind the lazy
   `GET .../debug` (Slice 12) — only the *render-needed* plan/claims ride on the normal read.

### Graph-item certainty overlay (the "on graph items" half) is a per-turn, per-user overlay

A claim may carry a `node_id`. The server validates it against the engagement's live graph
(`load_live_graph`, the existing read path) at finalize — a claim whose `node_id` is unknown
or foreign keeps its text but drops the `node_id` (so a hallucinated id never points the
overlay at nothing / at a foreign node, §17.1). The frontend, when rendering the Graph pane,
reads the caller's **recent assistant turns** (already loaded for the chat) and builds a map
`node_id -> latest certainty` (most-recent turn wins on conflict). It overlays a certainty
badge on those nodes via the existing graph node-style/decorator path — **without touching
the graph store, the single writer, or any `graph_*` table**. The overlay is the caller's
own, derived from their own chat; it disappears on logout/reload exactly as the chat does
(it is recomputed from the loaded turns). This satisfies "certainty … on graph items"
without provenance-tagging the node (§8.2) and without a graph write (ADR-0001).

### Audit payload extension (no new audit surface)

The existing Slice-11/12 `ai_call` audit payload is widened with `plan_steps` and
`claims_count` integers (forensic record of how much structured output a turn produced). No
new audit action, no new table, no hash-chain change — only the payload dict grows, so the
audit integrity surface (Slice 10) is not re-opened.

## Contract

OpenAPI delta. **No new endpoint.** **Two changed schemas**: `ChatMessageRead` gains
optional `plan` and `claims`; `ChatTurnDebug` gains `plan` and `claims`. **New nested
schemas**: `PlanStep`, `PlanStepStatus`, `Claim`. The WebSocket frame contract is unchanged
in *type* (`token`/`done`/`error`) but the **`done` frame payload is extended** with the
parsed `plan` and `claims` (WS frames are not in the OpenAPI doc; the
`WebSocketChatChunk` value object + the frontend hook contract are updated below). A
contract change means `make generate-api` is required.

```yaml
openapi: "3.1.0"
info:
  title: Adeptus API — Slice 13 delta
  version: "0.13.0"

paths:
  /api/v1/engagements/{engagement_id}/chat/messages:
    get:
      # UNCHANGED operation; ChatMessageRead items now carry plan + claims.
      operationId: list_chat_messages
      responses:
        "200": { content: { application/json: { schema: { $ref: "#/components/schemas/ChatMessagePage" } } } }

  /api/v1/engagements/{engagement_id}/chat/messages/{message_id}/debug:
    get:
      # UNCHANGED operation; ChatTurnDebug now carries the parsed plan + claims (§14).
      operationId: get_chat_turn_debug
      responses:
        "200": { content: { application/json: { schema: { $ref: "#/components/schemas/ChatTurnDebug" } } } }

components:
  schemas:
    PlanStepStatus:
      type: string
      enum: [todo, in_progress, done]

    PlanStep:
      type: object
      required: [step, status]
      properties:
        step:
          type: string
          description: One ordered todo-list item the AI is tracking (verbatim, §5.5).
        status: { $ref: "#/components/schemas/PlanStepStatus" }

    Claim:
      type: object
      required: [text, certainty]
      properties:
        text:
          type: string
          description: The claim the AI flagged with a certainty (verbatim).
        certainty:
          type: integer
          minimum: 0
          maximum: 100
          description: Stated certainty percentage for this claim (§5.3).
        node_id:
          oneOf: [{ type: string, format: uuid }, { type: "null" }]
          description: >-
            The graph node this claim is about, if any, validated against the
            engagement's live graph (foreign/unknown ids dropped, §17.1). Drives the
            Graph-pane certainty badge ("on graph items").

    # CHANGED: assistant rows now carry the parsed plan + in-chat claims so a reloaded
    # conversation re-renders the Plan panel and certainty badges without the debug call.
    ChatMessageRead:
      type: object
      required: [id, engagement_id, role, content, status, created_at]
      properties:
        id: { type: string, format: uuid }
        engagement_id: { type: string, format: uuid }
        role: { $ref: "#/components/schemas/ChatRole" }
        content:
          type: string
          description: >-
            The assistant prose with the structured metadata block stripped (the block is
            internal; users see clean prose). Empty while pending.
        status: { $ref: "#/components/schemas/ChatMessageStatus" }
        created_at: { type: string, format: date-time }
        plan:
          type: array
          items: { $ref: "#/components/schemas/PlanStep" }
          default: []
          description: The AI's running plan as of this turn (empty for user/pending/pre-slice rows).
        claims:
          type: array
          items: { $ref: "#/components/schemas/Claim" }
          default: []
          description: Certainty-tagged claims parsed from this turn (empty when none).

    # CHANGED: the debug panel now also shows the parsed plan + claims next to the raw output.
    ChatTurnDebug:
      type: object
      required:
        [message_id, model, status, nodes, edges, context_block, raw_prompt, model_output,
         plan, claims]
      properties:
        message_id: { type: string, format: uuid }
        model: { oneOf: [{ type: string }, { type: "null" }] }
        status: { $ref: "#/components/schemas/ChatMessageStatus" }
        nodes: { type: array, items: { $ref: "#/components/schemas/GraphSubsetNode" } }
        edges: { type: array, items: { $ref: "#/components/schemas/GraphSubsetEdge" } }
        context_block: { type: string }
        raw_prompt: { type: string }
        model_output:
          type: string
          description: >-
            The model's raw output for this turn INCLUDING the structured metadata block
            (§14 "model outputs" — the debug view shows the unstripped output so a power
            user can see exactly what was parsed).
        plan:
          type: array
          items: { $ref: "#/components/schemas/PlanStep" }
        claims:
          type: array
          items: { $ref: "#/components/schemas/Claim" }
```

WebSocket frame contract (not in OpenAPI; mirrored in the frontend hook to match the
backend `WebSocketChatChunk` value object). The `done` frame is extended:

```typescript
// frontend/src/features/chat/hooks/useChatStream.ts — matches backend chat WS frames.
interface PlanStep { step: string; status: 'todo' | 'in_progress' | 'done' }
interface Claim { text: string; certainty: number; node_id: string | null }

interface WebSocketChatChunk {
  type: 'token' | 'done' | 'error'
  data?: string          // token: incremental assistant prose (block already stripped server-side)
  message?: string       // error: stable, non-leaky reason
  plan?: PlanStep[]       // done: the parsed running plan for this turn (may be empty)
  claims?: Claim[]        // done: the parsed certainty claims for this turn (may be empty)
}
```

Note: the **token stream is the stripped prose** — the server withholds the sentinel
metadata block from the token frames (buffering once the sentinel marker is seen) so the
user never sees the raw block flash by. The parsed `plan`/`claims` arrive on `done`. (The
exact buffering strategy — strip-at-end vs. stop-emitting-at-sentinel — is an implementation
detail decided in backend task 3; both yield clean prose to the user.)

## Data model changes

Alembic migration written via the `write-alembic-migration` skill during implementation
(register any new model import in `backend/alembic/env.py` first — per the
Alembic-autogenerate memory; recreate the autogenerated file as the non-root user).

**No new table. No new column.** The parsed plan and claims are persisted as additional
top-level keys (`plan`, `claims`) inside the **existing** nullable JSONB
`chat_messages.graph_context` column (added by Slice 12) — the per-turn AI-metadata blob.
Because the change is purely the *shape of JSON written into an existing column*, **no
Alembic migration is required for the column itself**. Existing assistant rows simply have
no `plan`/`claims` keys (read back as empty — graceful, backfill-free).

- `chat_messages.graph_context` (existing JSONB, NULL) — now also stores, on an **assistant**
  row at finalize: `plan` (list of `{step, status}`) and `claims` (list of
  `{text, certainty, node_id|null}`), alongside the Slice-12 `nodes`/`edges`/`context_block`/
  `raw_prompt`. NULL / keys-absent for user rows and pre-slice rows. This is per-turn debug/
  transparency metadata on the turn that produced it — **not** a provenance column on a graph
  entity (§8.2 / §17.4).
- **No column on `graph_nodes`** (anti-pattern guard, §8.2): certainty on graph items is a
  read-time overlay derived from the caller's chat turns, never a node property.

If the human accepts Open Question Q3 (rename the column to `turn_metadata` for clarity),
**that** would add a trivial column-rename migration — but it is explicitly optional and not
part of this slice's required scope.

## Tasks

Numbered continuously across the whole slice (backend then frontend). Every commit subject
cites its task id, e.g. `feat(slice-13): add plan parser (task 2)`.

### Backend tasks

Ordered. Each independently testable. Complexity: S/M/L.

1. **[S]** Extend `app/features/chat/schemas.py` — add `PlanStepStatus` (StrEnum),
   `PlanStep`, `Claim` (with `certainty` 0–100 bounds, optional `node_id`); add `plan`/
   `claims` (default `[]`) to `ChatMessageRead` and to `ChatTurnDebug`; extend
   `WebSocketChatChunk` with optional `plan`/`claims`. Tests in `tests/test_schemas.py`:
   `certainty` out of range rejected/clamped; `status` enum; `plan`/`claims` default empty;
   `ChatMessageRead` round-trips with and without plan/claims.
   - Test command: `make test-backend` (`pytest app/features/chat/tests/test_schemas.py`).

2. **[M]** Add `app/features/chat/plan_parser.py` — a pure, fully-unit-tested module:
   `extract(raw_reply: str) -> tuple[str, list[PlanStep], list[Claim]]` returning the
   **prose with the sentinel block stripped**, the parsed plan, and the parsed claims.
   Tolerant: missing/malformed/oversized block → `(raw_reply_unchanged, [], [])`; coerce
   `status` (default `todo`), clamp `certainty` to 0–100, keep `node_id` only if a valid
   UUID. Owns the sentinel-marker constants. Tests in `tests/test_plan_parser.py`:
   `test_parses_well_formed_block`, `test_strips_block_from_prose`,
   `test_no_block_returns_prose_unchanged`, `test_malformed_json_degrades_to_empty`,
   `test_certainty_clamped`, `test_invalid_status_defaults_todo`,
   `test_invalid_node_id_dropped`, `test_block_never_leaks_into_prose`,
   `test_prose_not_redacted` (prose outside the block passes through verbatim, §5.5).
   - Test command: `make test-backend` (`pytest app/features/chat/tests/test_plan_parser.py`).

3. **[L]** Extend `app/features/chat/service.py` `stream_assistant_reply`:
   - Append a **structured-output instruction** to the system prompt (new constant
     `PLAN_CERTAINTY_INSTRUCTION` composed after `SYSTEM_PROMPT` and the Slice-12 context
     block) asking the model to emit the trailing `<adeptus-meta>` block.
   - **Strip the sentinel block from the streamed tokens** so the user sees clean prose
     (buffer once the sentinel marker appears; do not emit the block as `token` frames).
   - On finalize: call `plan_parser.extract(full_reply)`, **validate each claim's `node_id`
     against the live graph** (reuse `load_live_graph` already loaded for the Slice-12
     subset; drop foreign/unknown ids, §17.1), persist the stripped prose as `content` and
     `plan`/`claims` into `graph_context`, and **deliver `plan`/`claims` on the `done`
     frame**.
   - Widen `_emit_ai_call` payload with `plan_steps` / `claims_count`.
   - On a **terminal-state replay** (reconnect to a `complete` turn), the `done` frame must
     also carry the stored `plan`/`claims` (read back from `graph_context`) so a reconnect
     re-renders the panel.
   - Tests in `tests/test_service.py` (Ollama + audit mocked; real test DB for the graph
     read): `test_stream_strips_meta_block_from_tokens`,
     `test_stream_persists_plan_and_claims`, `test_done_frame_carries_plan_and_claims`,
     `test_claim_node_id_validated_against_graph` (foreign id dropped),
     `test_no_block_yields_empty_plan_and_clean_prose`,
     `test_replay_complete_turn_returns_stored_plan`,
     `test_ai_call_payload_has_plan_and_claim_counts`,
     `test_prompt_appends_structured_instruction`, `test_prose_not_redacted`.
   - Test command: `make test-backend` (`pytest app/features/chat/tests/test_service.py`).

4. **[S]** Extend `app/features/chat/service.py` reads — `list_messages` maps each assistant
   row's stored `plan`/`claims` into `ChatMessageRead`; `get_turn_debug` (`_to_turn_debug`)
   maps `plan`/`claims` into `ChatTurnDebug` and returns the **unstripped** `model_output`
   (the debug view shows the raw block). Helpers tolerate the keys being absent (pre-slice /
   user rows → empty). Tests in `tests/test_service.py`: `test_list_messages_includes_plan`,
   `test_turn_debug_includes_parsed_plan_and_claims`,
   `test_pre_slice_row_reads_empty_plan`.
   - Test command: `make test-backend` (`pytest app/features/chat/tests/test_service.py`).

5. **[S]** `app/features/chat/router.py` — no new endpoint; confirm the changed response
   models (`ChatMessageRead` with plan/claims, `ChatTurnDebug` with plan/claims) flow
   through the existing `GET .../chat/messages` and `GET .../debug` routes. Tests in
   `tests/test_router.py`: `test_list_messages_response_has_plan_field`,
   `test_get_debug_response_has_plan_and_claims`.
   - Test command: `make test-backend` (`pytest app/features/chat/tests/test_router.py`).

6. **[S]** Add the structured-output instruction wording + the certainty threshold and any
   parser limits to config where appropriate (e.g.
   `ADEPTUS_CHAT_LOW_CONFIDENCE_THRESHOLD` default `70`, surfaced to the frontend via the
   parsed data only — or kept frontend-side; decide in task 6 vs task 10). No new external
   dependency. Confirm `make migrate` is a **no-op** for this slice (no schema change).
   Tests: settings load with defaults.
   - Test command: `make test-backend` (`pytest app/features/chat/tests/test_config.py`).

### Frontend tasks

Numbering continues from the backend tasks.

7. **[S]** Run `make generate-api` to regenerate `frontend/src/shared/api/` + commit the
   updated `frontend/openapi.json` (adds `PlanStep`, `PlanStepStatus`, `Claim`; extends
   `ChatMessageRead` and `ChatTurnDebug`).
   - Test command: `make generate-api` then `make lint`.

8. **[M]** Update `frontend/src/features/chat/hooks/useChatStream.ts` — surface `plan` and
   `claims` from the `done` frame (alongside the existing `isDone`/buffer/error). Tests in
   `useChatStream.test.ts` (mock `WebSocket`): tokens accumulate as clean prose; `done`
   frame surfaces the plan + claims; empty plan handled.
   - Test command: `make test-frontend` (`vitest run src/features/chat/hooks/useChatStream.test.ts`).

9. **[M]** Add `frontend/src/features/chat/components/PlanPanel.tsx` + test — renders an
   ordered list of `PlanStep`s with per-step status affordance (todo / in-progress / done),
   collapses or shows a subtle "no plan this turn" state when empty. Pure presentational
   (props: `plan: PlanStep[]`). Tests: renders ordered steps with statuses; empty state;
   step text rendered verbatim.
   - Test command: `make test-frontend` (`vitest run src/features/chat/components/PlanPanel.test.tsx`).

10. **[M]** Add `frontend/src/features/chat/components/CertaintyBadge.tsx` + test and wire
    in-chat claim rendering into `ChatMessageList.tsx`: render each assistant message's
    `claims` as inline certainty badges (amber when `certainty <` threshold, neutral/green
    above), text verbatim. Tests: low-confidence badge styled distinctly; high-confidence
    plain; percentage shown; no claims → no badges.
    - Test command: `make test-frontend` (`vitest run src/features/chat/components/CertaintyBadge.test.tsx`).

11. **[M]** Wire `PlanPanel` into the chat pane (`ChatMessageList.tsx` / `ChatPanel.tsx`):
    show the Plan panel for the **latest** assistant turn above/with its reply; update it
    live when the stream's `done` delivers a new plan; on history reload, render from
    `ChatMessageRead.plan`. Update `ChatMessageList.test.tsx` / `ChatPanel.test.tsx`:
    plan appears on completion; updates on a new turn; renders from loaded history.
    - Test command: `make test-frontend` (`vitest run src/features/chat/components/ChatPanel.test.tsx`).

12. **[L]** Add the **graph-item certainty overlay** in `src/features/chat/` consuming the
    Slice-08 graph node store: a small `useCertaintyByNode(engagementId)` hook builds a
    `node_id -> latest certainty` map from the caller's loaded assistant turns (most-recent
    wins), and a thin presentational decorator renders a certainty badge on matching nodes in
    the Graph pane **without mutating the graph store / single writer / any `graph_*` table**
    (read-only overlay, §8.2 / ADR-0001). Tests: map prefers the most-recent turn on
    conflict; nodes with no claim get no badge; the overlay never writes the store.
    - Test command: `make test-frontend` (`vitest run src/features/chat/hooks/useCertaintyByNode.test.ts`).

13. **[M]** Extend the Slice-12 `AiDebugPanel.tsx` to also render the parsed **plan** and
    **claims** sections (next to the existing subset/raw-prompt/output). Update
    `AiDebugPanel.test.tsx`: shows parsed plan steps and claims; empty states; raw output
    (with block) still visible.
    - Test command: `make test-frontend` (`vitest run src/features/chat/components/AiDebugPanel.test.tsx`).

14. **[S]** Verify coverage ≥ 60% on `src/features/chat/`; `make lint` clean (no `any`;
    narrow via generated types). Confirm the Slice-02 privacy banner, Slice-08 graph store,
    and Slice-12 debug panel/subset are untouched except for the additive plan/claims render.
    - Test command: `make test-frontend` then `make lint`.

## Test plan

- **Unit — backend** (coverage ≥ 80% on `app/features/chat/`):
  - `tests/test_schemas.py` — `certainty` bounds; `status` enum; `plan`/`claims` defaults;
    `ChatMessageRead`/`ChatTurnDebug` round-trip with and without the new fields.
  - `tests/test_plan_parser.py` — the nine `test_*` names in backend task 2; this is the
    core extraction logic and gets the densest coverage (well-formed parse, prose stripping,
    graceful degradation on every malformed shape, clamping/coercion, no-redaction of prose).
  - `tests/test_service.py` (Ollama + audit mocked, real test DB graph) — the nine `test_*`
    names in backend task 3 plus the three read names in task 4, including the no-redaction
    prompt/prose assertions, the foreign-`node_id` drop, the reconnect-replay plan return,
    and the widened audit payload.
  - `tests/test_router.py` — the two `test_*` names in backend task 5.
  - `tests/test_config.py` — threshold/instruction settings load with defaults.
- **Unit — frontend** (coverage ≥ 60% on `src/features/chat/`):
  - `useChatStream.test.ts` — plan/claims surfaced from `done`.
  - `PlanPanel.test.tsx` — ordered steps + statuses + empty state.
  - `CertaintyBadge.test.tsx` — low vs high confidence styling; percentage shown.
  - `ChatPanel.test.tsx` / `ChatMessageList.test.tsx` — plan appears on completion, updates,
    renders from history; in-chat claim badges render.
  - `useCertaintyByNode.test.ts` — most-recent-turn-wins map; no-claim nodes unbadged;
    read-only (no store mutation).
  - `AiDebugPanel.test.tsx` — parsed plan + claims sections render.
- **Integration** (`@pytest.mark.integration`, real Postgres; **Ollama mocked** — external
  services never hit, CLAUDE.md), in `tests/test_integration.py`:
  - `test_turn_parses_plan_and_persists` — POST a message, stream a **faked Ollama reply
    that includes a well-formed `<adeptus-meta>` block** with a 3-step plan and one claim
    referencing a seeded node; assert the assistant row's stored `content` is the stripped
    prose (no raw block), the persisted `plan` has 3 steps, the `claim`'s `node_id` survived
    validation, the `done` frame carried plan+claims, and exactly one `ai_call` audit entry
    carries `plan_steps=3` / `claims_count=1`. **Headline §5.3 + §14 happy-path.**
  - `test_turn_with_foreign_node_id_drops_it` — the faked reply claims a node id NOT in the
    engagement; assert the claim text survives but `node_id` is null (§17.1).
  - `test_turn_without_block_degrades_cleanly` — faked reply has no block; assert clean prose
    persisted, empty plan/claims, turn still `complete`, `ai_call` with zero counts.
  - `test_plan_private_per_user` — a second member of the same engagement reading their own
    history never sees the first user's plan/claims (§5.4 / §17.1).
- **E2E** (Playwright, opt-in stack) — extend `chat.spec.ts` or add `plan-certainty.spec.ts`:
  log in, open an engagement, send a message whose **stubbed deterministic Ollama reply**
  carries a plan + a certainty claim; assert the Plan panel renders the steps, the in-chat
  certainty badge shows the percentage, reload re-renders both from history, and the
  referenced node shows a certainty badge in the Graph pane. (Ollama stubbed — no real model
  in CI; pentest/external-service rule.)

## Acceptance criteria

- `make test` passes (ruff + mypy + eslint + tsc + pytest + vitest + playwright); coverage
  gates hold (≥80% backend `chat`, ≥60% frontend `chat`).
- `make lint` passes with no new errors.
- `make migrate` is a **no-op** for this slice (no schema change — plan/claims live in the
  existing `graph_context` JSONB); a fresh DB still migrates cleanly.
- `make generate-api` produces an updated `frontend/openapi.json` containing `PlanStep`,
  `PlanStepStatus`, `Claim`, and the extended `ChatMessageRead` / `ChatTurnDebug`;
  regenerated types committed.
- `make dev` brings up the stack; manual demo:
  1. Open an engagement; ask a multi-step question → the assistant prose streams in clean
     (no raw metadata block visible), then the **Plan panel** appears above the reply with
     ordered todo/in-progress/done steps (§5.3 visible plan).
  2. Send a follow-up → the Plan panel **updates** to the new turn's plan.
  3. When the AI flags a low-confidence claim → an inline **certainty badge** (e.g. "60%")
     renders in the reply, amber-flagged below the threshold (§5.3 uncertainty in chat).
  4. A claim that references a graph node → the node shows a **certainty badge** in the
     Graph pane (§5.3 uncertainty on graph items) — without any graph write.
  5. Open the **Debug** panel on the reply → it shows the **parsed plan** and **claims**
     next to the raw model output and the Slice-12 subset (§14).
  6. Reload the page → plans and badges re-render from history.
  7. Ask a plain confident question → clean reply, trivial/empty plan, no spurious badges
     (graceful degradation).
  8. As admin, the Audit tab `ai_call` entries now carry `plan_steps` / `claims_count` (§14).
- `gh pr view` shows green CI.

## Risks

- **Risk 1 — The local model may not reliably emit the structured block.** A small quantized
  model can ignore the instruction or emit malformed JSON. Mitigation: the parser is
  tolerant by design — any failure degrades to clean prose + empty plan/claims and the turn
  still succeeds (`test_no_block_returns_prose_unchanged`,
  `test_malformed_json_degrades_to_empty`, `test_turn_without_block_degrades_cleanly`). The
  feature is *additive*: the chat is never worse than Slice 12 when the block is absent.
- **Risk 2 — Sentinel block leaking into visible prose.** If stripping is wrong, the raw
  `<adeptus-meta>{…}` text could flash by in the token stream or persist in `content`.
  Mitigation: the server buffers/strips the block before emitting tokens and before
  persisting `content`; `test_stream_strips_meta_block_from_tokens`,
  `test_strips_block_from_prose`, and `test_block_never_leaks_into_prose` guard it.
- **Risk 3 — Hallucinated / foreign `node_id` on a claim.** A claim could point the
  Graph-pane badge at a node from another engagement or a non-existent id. Mitigation: every
  `node_id` is validated against the engagement's live graph at finalize and dropped if
  foreign/unknown (§17.1); `test_claim_node_id_validated_against_graph` /
  `test_turn_with_foreign_node_id_drops_it` guard it. The overlay is also the caller's own,
  derived from their own turns (per-user, §5.4).
- **Risk 4 — Temptation to write certainty onto the graph node.** It is tempting to store
  certainty as a `graph_nodes` property "so the graph is the source of truth". Forbidden:
  that is a provenance smear (§8.2) and would require a graph write through the single writer
  (ADR-0001), which chat must never do. Mitigation: certainty lives on the chat turn; the
  graph badge is a read-only overlay; `useCertaintyByNode` never mutates the store
  (test asserts read-only); no `graph_*` migration exists in this slice.
- **Risk 5 — No-redaction temptation on plan/claim text.** Plan steps or claims might echo
  secret-looking values. Forbidden to strip (§5.5 / CLAUDE.md). Mitigation: prose and
  plan/claim text pass through verbatim; `test_prose_not_redacted` asserts it; the local path
  has no egress (cloud egress friction is Slice 14).
- **Risk 6 — Reusing one JSONB blob for subset + plan + claims couples three concerns.** A
  parser change could clobber the Slice-12 subset keys. Mitigation: the finalize writer
  merges (does not overwrite) the existing keys; `test_stream_persists_graph_context_debug
  _record` (Slice 12) plus `test_stream_persists_plan_and_claims` both run on the same row,
  asserting both sets of keys survive. (If the blob grows further with Slice 16 tool-call
  detail, splitting into a dedicated `chat_turn_debug` table becomes worthwhile — flagged in
  Slice 12 and noted in Open Questions Q3.)
- **Risk 7 — Plan panel mistaken for an actionable task queue (§11.6).** Users might expect
  to check off / edit steps. Mitigation: the panel is read-only AI output with no edit
  affordances; copy makes it clear it is the AI's plan; §11.6 explicitly forbids a separate
  kanban, which this honors.

## Open questions for the human

1. **Structured-output mechanism: instructed sentinel block (proposed) vs. Ollama
   structured-output / JSON mode vs. tool-calling.** The plan proposes an **instructed
   trailing `<adeptus-meta>` JSON block** parsed server-side, because it (a) keeps this slice
   purely additive on the Slice-11/12 text-streaming path, (b) avoids entangling Slice 16's
   tool-calling surface, and (c) works even on a model with weak tool support. Ollama does
   support a structured-output `format` parameter (constrained JSON) which would be more
   reliable but forces the *whole* reply into JSON (losing free-streaming prose) unless we do
   a second call. Is the instructed-sentinel approach acceptable for v1, with Ollama JSON
   mode as a later reliability upgrade? (Proposed default: yes — instructed sentinel.)

2. **Live plan streaming vs. plan-on-`done`.** The plan proposes resolving the Plan panel on
   the `done` frame (the prose streams live; the plan appears when the turn completes). Live
   token-by-token plan updates mid-turn would need either a richer WS frame or a two-pass
   model call. Is plan-on-completion acceptable for v1? (Proposed default: yes.)

3. **Rename `chat_messages.graph_context` → `turn_metadata`?** The column (added by Slice 12
   as "graph context") now also holds plan/claims, so its name under-describes its contents.
   A rename is a trivial, optional migration. Do you want the rename in this slice (adds one
   migration), or keep the Slice-12 name and treat the column as a general per-turn blob
   (no migration)? (Proposed default: keep the name, no migration — minimize this slice.)

4. **Low-confidence threshold default + placement.** Below what certainty % is a claim
   visually flagged as low-confidence, and should the threshold be a backend config
   (`ADEPTUS_CHAT_LOW_CONFIDENCE_THRESHOLD`, proposed default `70`) or a pure frontend
   constant? (Proposed default: backend setting, default `70`, so it is one place to tune.)

## Security review required?

**No.** This slice does not touch auth (it reuses the established membership + per-user
ownership chokepoints and the Slice-11 session-cookie WS auth without changing them), MCP,
the single-writer graph process (chat only *reads* the live graph via the existing
`load_live_graph` path to validate a claim's `node_id`; no write, no writer queue), RAG
isolation (no pgvector/embeddings here — Slice 23), secrets storage, or the approval flow.
It does NOT implement egress: the structured-output instruction and the reply are exchanged
only with the **local** Ollama (cloud egress + pattern friction is Slice 14, which is risky
and will require review). It touches the audit log only by widening the already-reviewed
`ai_call` payload dict with two integer counts — no new audit action, no new table, no
hash-chain change — so the audit integrity surface (Slice 10) is not re-opened. The two
surfaces a reviewer might still want to eyeball, because this slice parses model output and
overlays it onto the graph: (a) the **no-redaction** guarantee on prose / plan / claim text
(§5.5, Risk 5), and (b) the **foreign-`node_id` rejection** + **per-user isolation** of the
certainty overlay so one user's (or one engagement's) data never points the badge at the
wrong node (§5.4 / §17.1 / §8.2, Risks 3–4) — both covered by named tests above.

## Progress

(The stop-checkpoint hook and compact-handoff skill append here. Leave empty at planning time.)
- 2026-06-05T13:07:38Z — 1caa56e docs(slice-13): add slice spec; mark slice 13 in-progress
- 2026-06-05T13:08:51Z — 1caa56e docs(slice-13): add slice spec; mark slice 13 in-progress
- 2026-06-05T13:55:51Z — b9b8b0c test(slice-13): E2E plan/certainty journey (stack-gated)
