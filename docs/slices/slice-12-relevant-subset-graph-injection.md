# Slice 12: "Relevant subset" graph injection

**Branch**: `slice-12-relevant-subset-graph-injection`
**GitHub Issue**: #36
**Status**: planned
**Risky**: no

---

## Goal

On every AI turn, send the LLM an explicitly-scoped "relevant subset" of the engagement graph (not the whole graph) and expose the exact subset that went into the prompt in an inspectable per-turn AI debug panel.

## User-visible demo

After this slice is merged, with `make dev` up (Ollama reachable, `qwen3.5:9b` pulled,
an engagement that has a graph from Slices 07/08):

- Open an engagement workspace that already has some graph nodes (e.g. a `host`, two
  `service`s, an `endpoint`, a `vulnerability`). The left chat pane works exactly as in
  Slice 11.
- **Pin** a node in the right Graph pane (the Slice-08 pin affordance). Then ask the AI a
  question that mentions a *different* node by keyword, e.g. "what should I try against the
  login endpoint?". Send it.
- The assistant reply streams in as before — but now the model's answer is **grounded in
  the graph**: it can refer to the pinned node and the keyword-matched `login` endpoint
  because those node rows were included in the prompt.
- A new **"Debug" toggle** on the in-flight (and on any past) assistant message opens an
  **AI debug panel** (§14) showing, for that exact turn:
  - the **list of graph nodes/edges that were injected**, grouped by *why* each was
    included (pinned / recently-touched / @-mentioned / keyword-match);
  - the **raw system + context block** that was prepended to the conversation, and the
    raw model output.
- Pin/unpin a node and ask another question: the debug panel for the *new* turn shows the
  updated pinned set — pinning visibly changes what the AI is given (§5.4 "pinning tells
  the AI to weight it heavily").
- Ask a question in an engagement with an **empty graph**: the reply still works (Slice 11
  behavior) and the debug panel shows an empty subset with a clear "no graph entities
  matched" note — no crash, no graph block in the prompt.
- The whole thing stays **private per user** (§5.4) and the **privacy banner** (§5.5) is
  untouched: on a `local_only` engagement nothing leaves the local network; the graph
  subset is assembled and sent to the **local** Ollama only.

## Out of scope

This slice wires graph context into the existing local chat path and ships the debug
panel. It deliberately does NOT do the following (each is separately tracked):

- Does **NOT** implement the **visible running plan / certainty signaling** (Slice 13,
  `Depends on: 11`). The debug panel shows the *injected graph subset + raw prompt/output*;
  it does NOT render an AI todo list or per-claim confidence percentages (§5.3 visible-plan
  and uncertainty-signaling clauses).
- Does **NOT** implement the **cloud LLM path or the pattern-friction egress modal**
  (Slice 14). The graph subset is still sent only to the **local** Ollama. Because the
  subset can now carry node labels/properties, the no-redaction rule (§5.5) and the future
  cloud-egress friction (Slice 14) are noted but unchanged here — the local path has no
  egress.
- Does **NOT** implement **AI-generated summaries of *older chat messages*** (the §5.4
  "summaries of older context" half) — the chat-message window is still the Slice-11
  verbatim last-N. This slice adds **no summarization at all**: the assembled graph subset
  is sent **in full, verbatim** (a deliberate divergence from the §5.3 "hard token budget …
  overflow summarized" clause — see Requirements traceability) — there is no budget, no
  overflow compaction, no aggregation.
- Does **NOT** add **server-side pin storage**, change the **single-writer** graph process,
  or add any **graph write path**. Pins remain ephemeral client-side state (Slice 08
  resolved Q1); this slice *reads* the current pin set at send time. No `graph_*` table is
  written; ADR-0001 single-writer is untouched (chat never writes the graph).
- Does **NOT** implement the **@-mention / share-into-channel UI** (Slice 31). It DOES
  accept an explicit list of node ids the client considers "@-mentioned in the last K
  messages" so the §5.3 union is complete, but there is no message-sharing surface, no
  channel, no presence. (The simplest correct client behavior for this slice is documented
  in Planning decisions.)
- Does **NOT** implement **RAG** (§10, Slice 23). "Relevant subset" is graph-only; no
  pgvector, no embeddings, no curated KB.
- Does **NOT** implement **personas** (Slice 15). The system prompt stays the single
  neutral Slice-11 string; this slice prepends a *graph-context block* to it, not a
  persona.
- Does **NOT** add **provenance columns** to any graph/finding entity (§8.2 / §17.4 /
  CLAUDE.md anti-pattern). The injected-subset record is attached to the **assistant
  `chat_message`** (the turn that used it), not smeared onto graph nodes; AI attribution
  remains the `ai_call` audit entry.
- Does **NOT** widen `core/` or `shared/`. Backend changes live under
  `app/features/chat/` (consuming the existing `app/features/graph/` read repository);
  frontend changes under `src/features/chat/` (consuming the existing `pinStore` from
  `src/features/graph/`).

## Requirements traceability

- **§5.3 — Graph access, "relevant subset" definition** — quoted:
  > on every turn, the AI is given an explicitly-scoped subset of the graph rather than the
  > whole graph. The subset is the union of: all pinned nodes for the engagement (always
  > included); the last N nodes touched in the current conversation; nodes referenced by
  > @-mention in the last K messages; nodes matching keywords in the current user message
  > (cheap full-text match). A hard token budget applies; overflow content is summarized
  > rather than dropped. The exact subset that went into the prompt is visible in the AI
  > debug panel (§14) for transparency and debugging.

  This is the **headline requirement**. This slice implements the four-way union and the
  per-turn record that the debug panel reads. Each union arm is individually decided in
  **Design notes** and individually tested. "Last N touched in the current conversation" and
  "last K messages for @-mentions" are bounded by small constants (see Design notes).

  **Deliberate deviation (planning-time decision, user direction):** the §5.3 clause "*A
  hard token budget applies; overflow content is summarized rather than dropped*" is
  **intentionally NOT implemented** in this slice. The assembled subset is sent to the model
  **in full and verbatim** — no budget, no overflow summarization, no aggregation. Rationale:
  v1 targets web-app engagements with modest graphs (§1, Slice 08 Risk 4), and the union is
  already bounded by the pinned set the user chose plus the small `N`/`K` caps on the
  recent/mentioned arms (the keyword arm matches node labels for the current message only).
  This is a known divergence from the literal §5.3 text; if real prompts grow too large,
  reintroducing a budget + summarization is a clean, localized follow-up (the
  `subset_builder` is the single place it would live). See Risk 2.
- **§5.4 — Pinned nodes as implicit @-mentions** — quoted:
  > Pinned graph nodes behave as implicit @-mentions: pinning a node tells the AI to weight
  > it heavily in subsequent reasoning.

  The pinned set is the **always-included** arm of the §5.3 union and is rendered first /
  weighted in the context block. Pins are read from the Slice-08 `pinStore` at send time
  and passed to the backend (pins are client-side, Slice 08 Q1 — so they MUST be
  transmitted, not re-derived server-side; see Design note "Where the inputs come from").
- **§5.4 — Context strategy (hybrid)** — quoted:
  > recent messages verbatim + AI-generated summaries of older context + graph queried on
  > demand (per the "relevant subset" rules in §5.3).

  Slice 11 delivered the *recent-messages-verbatim* half. This slice delivers the
  *graph-queried-on-demand* half ("per the relevant-subset rules in §5.3"). The
  *summaries-of-older-chat-context* half remains deferred (Out of scope).
- **§14 — AI debug panel** — quoted:
  > AI debug panel: raw prompts, model outputs, tool calls, and the exact "relevant subset"
  > of the graph used per turn — for power users / debugging.

  This slice ships the debug panel for the parts that exist today: **raw prompt**, **model
  output**, and the **exact relevant subset** used per turn. "Tool calls" are out of scope
  (the AI cannot call tools until Slice 16); the panel leaves a clearly-labeled empty
  region / omits that section, to be filled by Slice 16.
- **§17.1 — Engagement isolation** — the graph read for the subset is scoped to the
  message's `engagement_id` via the existing membership-checked path; the debug-panel read
  is scoped to the caller's own message (per-user, §5.4) and to engagement membership. A
  non-member / non-owner gets `404` (no existence disclosure), matching Slice 11.
- **§5.5 — No redaction** — node labels/properties are included in the context block
  **verbatim**; no "secret-looking value" is stripped before the local model. The cloud
  egress-friction layer is Slice 14; the local path has no egress.
- **§8.2 / §17.4 — audit is the source of truth; no provenance on entities** — the injected
  subset is persisted on the **assistant chat_message row** (the consumer of the subset),
  not as a provenance column on graph nodes. The existing `ai_call` audit entry is extended
  only with subset *counts* in its payload (forensic record of how much graph context a
  turn used) — no new audit table, no chain change.
- **ADR-0001 — single-writer** — unchanged: chat reads the graph (Postgres `load_live_graph`
  read path) but never writes it and never goes through the writer queue.

## Design notes (load-bearing decisions)

### Where the four union inputs come from

§5.3's union has four arms; their inputs originate in different places:

1. **All pinned nodes (always included).** Pins are **client-side** ephemeral state
   (Slice 08 resolved Q1 — `pinStore` in `localStorage`). They are NOT in the DB, so the
   server cannot re-derive them. **The client MUST send the current pinned node-id set on
   the POST.** → new optional `pinned_node_ids: string[]` on `ChatMessageCreate`.
2. **Last N nodes touched in the current conversation.** "Touched" has no DB record yet
   (no provenance on graph nodes, §8.2; chat does not reference nodes). For this slice,
   **"touched in the conversation" = node ids the client has surfaced this session**:
   nodes the user pinned/unpinned, selected, or @-mentioned recently. To keep the backend
   the single place that *assembles* the subset (and avoid inventing a server-side
   "touched" log), the client sends a small **recently-interacted node-id list**
   (`recent_node_ids`, bounded, most-recent-first) alongside the message. Server truncates
   to `N`. (Decision 1 records the alternative of a server-side touch log.)
3. **Nodes @-mentioned in the last K messages.** There is no @-mention UI until Slice 31.
   The contract accepts an optional `mentioned_node_ids: string[]` so the union is complete
   and forward-compatible; in this slice the client supplies it from any inline node
   references it can cheaply detect (or leaves it empty). Server truncates to the last K
   distinct.
4. **Keyword match against the current user message (cheap full-text match).** This is
   pure server work: the backend takes the user message text, extracts keyword tokens, and
   does a **cheap SQL `ILIKE` match against `graph_nodes.label`** (and optionally a few
   indexed property fields) for the engagement. "Cheap full-text match" (§5.3) → start with
   `ILIKE`/`to_tsvector` on `label`; no new embedding. (Decision 2 records whether to
   use Postgres FTS vs. plain `ILIKE`.)

All four arms are **resolved server-side into actual `GraphNode` rows** so that: client
input is never trusted to *contain* node content (only ids/keywords), every id is
re-validated to belong to the engagement (ignored if not — §17.1), and the assembled subset
is canonical for both the prompt and the debug-panel record.

### Subset assembly (the `subset_builder` module)

A new pure-ish module `app/features/chat/subset_builder.py` owns the §5.3 algorithm:

1. Load the engagement's **live** nodes + edges once (`graph.repository.load_live_graph` —
   the existing read path; engagement-scoped, non-deleted only).
2. Compute the four id sets, intersect each with the live node ids (drop unknown/foreign
   ids, §17.1), and form the union, tagging each selected node with its **inclusion
   reason(s)** (`pinned`, `recent`, `mentioned`, `keyword`) — a node can carry several.
3. Pull in **edges among the selected nodes** (so the model sees relationships, not islands)
   — only edges whose *both* endpoints are in the selected set.
4. Order by priority (pinned first, then mentioned, recent, keyword) and **render each node
   to a compact textual line, verbatim** (type + label + properties, no redaction §5.5).
   **No token budget (planning-time decision):** every selected node is rendered in full —
   no budget check, no summarization, no aggregation. The recent/mentioned arms are already
   bounded by the `N`/`K` caps; the pinned and keyword arms are included whole.
5. Return a structured `GraphSubset` value object: the rendered context block string +
   per-node inclusion-reason(s) + the selected edges + counts (`nodes_injected`,
   `edges_injected`).

`stream_assistant_reply` (Slice 11) is extended to call the builder when `status ==
"pending"`, **prepend the context block to the system prompt** (a second system message, or
appended to the existing `SYSTEM_PROMPT`), and **persist the `GraphSubset` record on the
assistant message** before/with finalization so the debug panel can read it back. On an
empty graph the builder returns an empty subset and no context block is prepended (Slice 11
behavior preserved exactly).

`N` (recent cap) and `K` (mentioned cap) are added to `app/core/config.py` as settings
(defaults `N=15`, `K=10`); no token-budget setting is added.

### Persisting the per-turn subset for the debug panel (§14)

The exact subset must be inspectable **after the fact** for any past turn, not just the
in-flight one. Decision: persist a **JSONB `graph_context` column on `chat_messages`** (on
the *assistant* row — the turn that consumed it), plus the **rendered raw prompt** so §14's
"raw prompts" is satisfied without re-deriving. Rationale vs. alternatives:

- A separate `chat_turn_debug` table was considered but rejected for this slice: the debug
  record is 1:1 with the assistant message, has the same lifecycle and the same
  per-user/per-engagement ACL, and `chat_messages` already carries `model`/token columns
  for exactly this "audit/debug" purpose (Slice 11 model docstring). One JSONB column reuses
  the existing membership+ownership read path. (If the debug record grows tool-call detail
  in Slice 16, splitting it out becomes worthwhile — flagged in Planning decisions.)
- This is **not** a provenance column on a *graph* entity (§8.2 anti-pattern): it lives on
  the chat turn that *used* the graph, describing *what the AI was shown*, which is exactly
  debug/transparency data, not shared graph truth.

A new **read endpoint** `GET .../chat/messages/{message_id}/debug` returns the stored
debug record for one assistant message (membership + ownership scoped, 404 otherwise).
The frontend opens it lazily when the user toggles the Debug panel (so history loads stay
lean and the large prompt blob is fetched on demand).

### Audit payload extension (no new audit surface)

The existing Slice-11 `ai_call` audit payload is extended with subset **counts** only
(`graph_nodes_injected`, `graph_edges_injected`) so the forensic log records *how much*
graph context each turn used (§14 "records every AI call").
No new audit action, no new audit table, no change to the hash chain — this only widens the
existing payload dict, so the audit integrity surface (Slice 10) is not re-opened.

## Contract

OpenAPI delta. **One changed schema** (`ChatMessageCreate` gains three optional id lists),
**one new schema** (`ChatTurnDebug` + its nested types), **one new endpoint** (`GET`
debug). The WebSocket frame contract is **unchanged** (still `token`/`done`/`error`); the
graph subset is injected server-side into the prompt and is not streamed over the socket.
A contract change means `make generate-api` is required.

```yaml
openapi: "3.1.0"
info:
  title: Adeptus API — Slice 12 delta
  version: "0.12.0"

paths:
  /api/v1/engagements/{engagement_id}/chat/messages:
    post:
      # CHANGED: body now carries the client-supplied union inputs for §5.3.
      operationId: send_chat_message
      summary: >-
        Persist a user message + a pending assistant message. The request may
        carry pinned/recent/mentioned node ids; the server resolves them (plus a
        keyword match on the message) into the §5.3 relevant subset at stream time.
      security: [{ cookieAuth: [] }]
      parameters:
        - { name: engagement_id, in: path, required: true, schema: { type: string, format: uuid } }
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: "#/components/schemas/ChatMessageCreate" }
      responses:
        "201": { content: { application/json: { schema: { $ref: "#/components/schemas/SendChatMessageResult" } } } }
        "401": { description: Not authenticated }
        "404": { description: Engagement not found or caller not a member }
        "409": { description: Engagement is archived (read-only, §4) }

  /api/v1/engagements/{engagement_id}/chat/messages/{message_id}/debug:
    get:
      # NEW: the AI debug panel data source (§14) for one assistant turn.
      operationId: get_chat_turn_debug
      summary: >-
        Return the AI debug record for one of the caller's own assistant messages:
        the exact relevant subset of the graph injected, the raw prompt, and the
        model output (§5.3, §14). Membership + ownership scoped.
      security: [{ cookieAuth: [] }]
      parameters:
        - { name: engagement_id, in: path, required: true, schema: { type: string, format: uuid } }
        - { name: message_id, in: path, required: true, schema: { type: string, format: uuid } }
      responses:
        "200": { content: { application/json: { schema: { $ref: "#/components/schemas/ChatTurnDebug" } } } }
        "401": { description: Not authenticated }
        "404": { description: Message not found, not owned by caller, or not an assistant turn }

components:
  schemas:
    ChatMessageCreate:
      type: object
      required: [content]
      properties:
        content:
          type: string
          minLength: 1
          maxLength: 32768
          description: The user's message text, sent verbatim to the model (no redaction, §5.5).
        pinned_node_ids:
          type: array
          items: { type: string, format: uuid }
          default: []
          description: >-
            Node ids the user has pinned (client-side pinStore, §5.4). Always-included
            arm of the §5.3 union. Unknown/foreign ids are ignored server-side (§17.1).
        recent_node_ids:
          type: array
          items: { type: string, format: uuid }
          default: []
          description: >-
            Node ids recently interacted with this session, most-recent-first; the
            "last N nodes touched in the conversation" arm (§5.3). Server truncates to N.
        mentioned_node_ids:
          type: array
          items: { type: string, format: uuid }
          default: []
          description: >-
            Node ids @-mentioned in recent messages (§5.3). Empty until the @-mention
            UI (Slice 31); accepted now for forward-compatibility. Server truncates to K.

    GraphSubsetReason:
      type: string
      enum: [pinned, recent, mentioned, keyword]

    GraphSubsetNode:
      type: object
      required: [id, type, label, reasons]
      properties:
        id: { type: string, format: uuid }
        type: { type: string }
        label: { type: string }
        reasons:
          type: array
          items: { $ref: "#/components/schemas/GraphSubsetReason" }
          description: Why this node was included (one node may have several reasons).

    GraphSubsetEdge:
      type: object
      required: [id, source_id, target_id, relation]
      properties:
        id: { type: string, format: uuid }
        source_id: { type: string, format: uuid }
        target_id: { type: string, format: uuid }
        relation: { type: string }

    ChatTurnDebug:
      type: object
      required:
        [message_id, model, status, nodes, edges, context_block, raw_prompt, model_output]
      properties:
        message_id: { type: string, format: uuid }
        model: { oneOf: [{ type: string }, { type: "null" }] }
        status: { $ref: "#/components/schemas/ChatMessageStatus" }
        nodes:
          type: array
          items: { $ref: "#/components/schemas/GraphSubsetNode" }
        edges:
          type: array
          items: { $ref: "#/components/schemas/GraphSubsetEdge" }
        context_block:
          type: string
          description: The exact graph-context text prepended to the system prompt this turn.
        raw_prompt:
          type: string
          description: The full prompt sent to the model (§14 "raw prompts").
        model_output:
          type: string
          description: The model's raw output for this turn (empty while pending/failed).
```

WebSocket: **unchanged** from Slice 11 (`WS /ws/chat/{assistant_message_id}`, frames
`token`/`done`/`error`). No new frame type.

Frontend TS types: the above schemas regenerate into `@/shared/api`; no hand-written
types except component props.

## Data model changes

Alembic migration written via the `write-alembic-migration` skill during implementation
(register any new model import in `backend/alembic/env.py` first — per the
Alembic-autogenerate memory; recreate the autogenerated file as the non-root user).

**No new table. One new nullable column on `chat_messages`** (additive, backfill-free —
existing Slice-11 assistant rows simply have `NULL`, meaning "no debug record captured").
**No column added to any `graph_*` / `findings` / entity table** (anti-pattern guard, §8.2 /
§17.4):

- `chat_messages.graph_context` — JSONB, NULL. Stores the per-turn `ChatTurnDebug` payload
  for an **assistant** row: the resolved subset (nodes with reasons, edges), the
  `context_block`, and the `raw_prompt`. `model_output` is read from the existing `content`
  column (no duplication). NULL for `user` rows and for any assistant row that completed
  before this slice. On Postgres `JSONB`; `with_variant(JSON, "sqlite")` for the in-memory
  test engine, matching the graph models' `_PROPS_JSON` pattern.
- **No index** on `graph_context`: it is read only by the point-lookup debug endpoint
  (`WHERE id = ? AND user_id = ?`), already covered by the PK + the existing
  `ix_chat_messages_engagement_user_created`.

Pins/recent/mentioned ids are **request-time inputs only** — not persisted as such; what is
persisted is the *resolved* subset (the canonical record), so the debug panel reflects what
the server actually injected, not what the client claimed.

## Tasks

Numbered continuously across the whole slice (backend then frontend). Every commit subject
cites its task id, e.g. `feat(slice-12): add graph subset builder (task 3)`.

### Backend tasks

Ordered. Each independently testable. Complexity: S/M/L.

1. **[S]** Extend `app/features/chat/schemas.py` — add `pinned_node_ids`,
   `recent_node_ids`, `mentioned_node_ids` (default `[]`) to `ChatMessageCreate`; add
   `GraphSubsetReason` (StrEnum), `GraphSubsetNode`, `GraphSubsetEdge`, and `ChatTurnDebug`
   read schemas. Tests in `tests/test_schemas.py`: id lists default to empty; invalid UUIDs
   rejected; `ChatTurnDebug` round-trips. **[S]**

2. **[M]** Add `app/features/chat/subset_builder.py` — the §5.3 algorithm as a pure,
   fully-unit-tested module: given the live graph (nodes+edges), the user message text, and
   the three id lists, return a `GraphSubset` value object (selected nodes with
   inclusion-reason tags, selected inter-node edges, the rendered `context_block`, and
   counts). Owns: id intersection with live nodes (drops foreign ids, §17.1), the cheap
   keyword `ILIKE` token matcher, the `N`/`K` truncation, the priority ordering, and the
   verbatim rendering of each selected node (**no token budget — full subset, no
   summarization, no aggregation**). Add `N`/`K` (recent/mentioned caps, defaults 15/10) to
   `app/core/config.py` as settings (no token-budget setting). Tests in
   `tests/test_subset_builder.py`: `test_pinned_always_included`,
   `test_keyword_ilike_matches_label`, `test_recent_truncated_to_n`,
   `test_mentioned_truncated_to_k`, `test_foreign_ids_ignored`,
   `test_union_dedupes_with_multiple_reasons`, `test_edges_only_among_selected_nodes`,
   `test_full_subset_rendered_verbatim` (no node summarized or dropped),
   `test_empty_graph_yields_empty_subset`, `test_content_not_redacted` (labels/properties
   pass through verbatim, §5.5).
   - Test command: `make test-backend` (`pytest app/features/chat/tests/test_subset_builder.py`).

3. **[S]** Add `app/features/chat/models.py` `graph_context` JSONB-nullable column to
   `ChatMessage` (`with_variant(JSON, "sqlite")`). No other model touched.

4. **[M]** Extend `app/features/chat/repository.py` — `finalize_assistant(...)` gains a
   `graph_context: dict | None` param persisted to the new column; add
   `get_message_for_owner` is reused (no change) and a thin read used by the debug endpoint
   if needed. Tests in `tests/test_repository.py`: finalize persists `graph_context`;
   `graph_context` is NULL for user rows / pre-slice rows.
   - Test command: `make test-backend` (`pytest app/features/chat/tests/test_repository.py`).

5. **[L]** Extend `app/features/chat/service.py`:
   - `send_message(...)` accepts the three id lists (stash on the user/assistant insert path
     or carry forward — simplest: pass them through to be resolved at stream time; since the
     WS does not receive them, persist them transiently on the pending assistant row's
     `graph_context` as `{"inputs": {...}}` OR re-resolve from a short-lived store).
     **Decision (Decision 4):** persist the raw client inputs onto the pending
     assistant `graph_context` at POST time, then the streamer resolves+overwrites them with
     the canonical subset at finalize. This keeps the WS frame unchanged and survives a
     dropped socket.
   - `stream_assistant_reply(...)`: when `pending`, read the stashed inputs, call
     `subset_builder.build(...)` with `graph.repository.load_live_graph(...)`, prepend the
     `context_block` to the prompt (verbatim, §5.5), stream as before, and on finalize
     persist the canonical `ChatTurnDebug` (subset + raw_prompt) into `graph_context`.
   - Extend `_emit_ai_call` payload with `graph_nodes_injected` / `graph_edges_injected`.
   - Add `get_turn_debug(db, *, engagement_id, requester, message_id) -> ChatTurnDebug` —
     membership + ownership chokepoint (404 for non-member/non-owner/non-assistant, §17.1 /
     §5.4); reads `content` as `model_output`.
   - Tests in `tests/test_service.py` (Ollama + audit mocked; real test DB for the graph
     read): `test_stream_injects_pinned_node_into_prompt`,
     `test_stream_keyword_match_included`, `test_stream_empty_graph_no_context_block`,
     `test_stream_persists_graph_context_debug_record`,
     `test_ai_call_payload_has_subset_counts`, `test_get_turn_debug_owner_only_404`,
     `test_get_turn_debug_non_assistant_404`, `test_prompt_graph_block_not_redacted`.
   - Test command: `make test-backend` (`pytest app/features/chat/tests/test_service.py`).

6. **[M]** Extend `app/features/chat/router.py` — `send_chat_message` passes the new body
   fields through; add `GET .../chat/messages/{message_id}/debug` →
   `service.get_turn_debug`. Tests in `tests/test_router.py`:
   `test_post_with_node_ids_201`, `test_get_debug_200_for_owner`,
   `test_get_debug_404_for_non_owner`, `test_get_debug_404_for_non_member`,
   `test_get_debug_401_unauthenticated`.
   - Test command: `make test-backend` (`pytest app/features/chat/tests/test_router.py`).

7. **[S]** Add the Alembic migration for `chat_messages.graph_context` via the
   `write-alembic-migration` skill. Confirm `make migrate` applies cleanly and
   `alembic downgrade -1` reverts.
   - Test command: `make migrate` then `alembic downgrade -1` (in the backend container).

### Frontend tasks

Numbering continues from the backend tasks.

8. **[S]** Run `make generate-api` to regenerate `frontend/src/shared/api/` + commit the
   updated `frontend/openapi.json` (adds the `ChatMessageCreate` fields, `ChatTurnDebug`,
   `GraphSubsetNode/Edge/Reason`).
   - Test command: `make generate-api` then `make lint`.

9. **[M]** Update `frontend/src/features/chat/api.ts` — `useSendChatMessage` accepts an
   object payload `{ content, pinnedNodeIds, recentNodeIds, mentionedNodeIds }` (back-compat
   default empties) and includes them in the POST body; add
   `useChatTurnDebug(engagementId, messageId | null)` (lazy `GET`, enabled only when the
   panel is open). Tests in `api.test.tsx`: send includes node ids in the body; debug query
   disabled until enabled, then returns the record; 404 surfaced.
   - Test command: `make test-frontend` (`vitest run src/features/chat/api.test.tsx`).

10. **[M]** Update `frontend/src/features/chat/components/ChatComposer.tsx` (and/or
    `ChatPanel.tsx`) to read the current pinned set from the Slice-08 `usePinStore`
    (`pinnedNodeIds(engagementId)`) and a small recently-interacted node list, and pass them
    into the send mutation. Decide the smallest correct source for `recentNodeIds` (Open
    Question 1) — proposed: the pinned set ∪ the currently-selected node, capped. Tests:
    sending forwards the current pins; empty when nothing pinned.
    - Test command: `make test-frontend` (`vitest run src/features/chat/components/ChatComposer.test.tsx`).

11. **[L]** Add `frontend/src/features/chat/components/AiDebugPanel.tsx` + test — the §14
    panel: given an assistant `messageId`, calls `useChatTurnDebug`, renders the injected
    nodes grouped by inclusion reason (pinned / recent / mentioned / keyword), the injected
    edges, the node/edge counts, and collapsible **raw prompt** + **model output** blocks
    (§14). Empty-subset state ("no graph entities matched this turn"). Loading/error/404
    states. Tests: renders grouped nodes; shows the node/edge counts; shows empty state;
    collapses raw prompt.
    - Test command: `make test-frontend` (`vitest run src/features/chat/components/AiDebugPanel.test.tsx`).

12. **[M]** Wire a **Debug toggle** into `ChatMessageList.tsx` (per assistant message) →
    opens `AiDebugPanel` for that message id (lazy). Keep it unobtrusive (a small "Debug"
    affordance on assistant rows). Update `ChatMessageList.test.tsx` and, if state is hoisted,
    `ChatPanel.test.tsx`: toggling Debug mounts the panel for the right message; only one
    panel open at a time (or per-message — implementer's choice, test whichever).
    - Test command: `make test-frontend` (`vitest run src/features/chat/components/ChatMessageList.test.tsx`).

13. **[S]** Verify coverage ≥ 60% on `src/features/chat/`; `make lint` clean (no `any`;
    narrow API payloads via the generated types). Confirm the Slice-02 privacy banner and
    Slice-08 graph pane are untouched.
    - Test command: `make test-frontend` then `make lint`.

## Test plan

- **Unit — backend** (coverage ≥ 80% on `app/features/chat/`):
  - `tests/test_schemas.py` — new id-list defaults; `ChatTurnDebug` round-trip.
  - `tests/test_subset_builder.py` — the ten `test_*` names in backend task 2; this is the
    core §5.3 logic and gets the densest coverage (each union arm, the `N`/`K` caps, full
    verbatim rendering, foreign-id rejection, no-redaction).
  - `tests/test_repository.py` — `graph_context` persisted on finalize; NULL otherwise.
  - `tests/test_service.py` (Ollama + audit mocked, real test DB graph) — the eight `test_*`
    names in backend task 5, including the no-redaction prompt assertion and the
    owner/member/non-assistant 404 gates.
  - `tests/test_router.py` — the five `test_*` names in backend task 6.
- **Unit — frontend** (coverage ≥ 60% on `src/features/chat/`):
  - `api.test.tsx` — send body includes node ids; debug query lazy + 404 surfacing.
  - `ChatComposer.test.tsx` — current pins forwarded on send.
  - `AiDebugPanel.test.tsx` — grouped nodes, node/edge counts, empty, collapsible raw prompt.
  - `ChatMessageList.test.tsx` — Debug toggle mounts the panel for the right message.
- **Integration** (`@pytest.mark.integration`, real Postgres; **Ollama mocked** — external
  services never hit, CLAUDE.md), in `tests/test_integration.py`:
  - `test_turn_injects_relevant_subset_and_persists_debug` — seed a graph (host + service +
    endpoint + vulnerability), POST a message with one pinned id and a keyword that matches
    the endpoint label, stream a faked Ollama reply, then `GET .../debug` and assert the
    pinned node and the keyword-matched node are present with correct reasons, the prompt
    contained their labels, and exactly one `ai_call` audit entry carries the subset counts.
    **Headline §5.3 + §14 happy-path.**
  - `test_debug_private_per_user` — a second member of the same engagement cannot `GET` the
    first user's turn debug (404, §5.4 / §17.1).
  - `test_full_subset_injected_end_to_end` — seed several nodes spanning all four arms;
    assert the debug record represents **every** selected node verbatim (no node dropped, no
    summarization), matching the full-subset decision.
- **E2E** (Playwright, `E2E_STACK=1` opt-in) — extend `chat.spec.ts` or add
  `ai-debug.spec.ts`: log in, open an engagement with a graph, pin a node, send a message,
  open the Debug panel on the assistant reply, assert the pinned node appears under "pinned".
  (Ollama stubbed with a deterministic fake stream — no real model in CI.)

## Acceptance criteria

- `make test` passes (ruff + mypy + eslint + tsc + pytest + vitest + playwright); coverage
  gates hold (≥80% backend `chat`, ≥60% frontend `chat`).
- `make lint` passes with no new errors.
- `make migrate` applies the `chat_messages.graph_context` migration cleanly against a fresh
  Postgres; `alembic downgrade -1` reverts it.
- `make generate-api` produces an updated `frontend/openapi.json` containing the new
  `ChatMessageCreate` fields and `ChatTurnDebug`; regenerated types committed.
- `make dev` brings up the stack; manual demo:
  1. Open an engagement that has a graph; pin a node (Slice-08 affordance).
  2. Ask a question whose wording matches a *different* node's label; send → the reply
     streams in as in Slice 11.
  3. Open the **Debug** panel on the assistant reply → it shows the injected subset grouped
     by reason (the pinned node under "pinned", the keyword match under "keyword"), the
     edges among them, and the raw prompt + model output (§14).
  4. Unpin/pin a different node, ask again → the new turn's Debug panel reflects the changed
     pinned set (§5.4 pinning changes what the AI is shown).
  5. Open the same engagement as a different member → cannot see the first user's turn debug
     (§5.4 / §17.1).
  6. Empty-graph engagement → reply still works; Debug panel shows the empty-subset state.
  7. As admin, the Audit tab `ai_call` entries now carry subset counts (§14).
- `gh pr view` shows green CI.

## Risks

- **Risk 1 — Pins are client-side, so the union is only as good as what the client sends.**
  If the client forgets to send `pinned_node_ids`, the "always included" arm silently
  empties. Mitigation: the composer reads `usePinStore` at send time (task 10) and a
  unit test asserts the current pins are forwarded; the server still resolves the other
  arms (keyword is server-only) so a degraded client never breaks the turn, just narrows
  context. Documented in Decision 1.
- **Risk 2 — No token budget → unbounded prompt size on large graphs (deliberate
  divergence from §5.3).** Per the planning-time decision, the full subset is sent verbatim
  with no budget or summarization, so a very large pinned set or a keyword matching many node
  labels could produce an over-long prompt that overflows the model context. Mitigation: v1
  targets modest web-app graphs (§1, Slice 08 Risk 4); the recent/mentioned arms are capped
  at `N`/`K`; the pinned arm is bounded by deliberate user action and the keyword arm by the
  current message's tokens. The `subset_builder` is the single, isolated place a budget +
  summarization can be reintroduced if real engagements outgrow this. Tracked as a known
  follow-up; `test_full_subset_rendered_verbatim` pins the current behavior.
- **Risk 3 — Keyword arm can over-match without a budget cap.** With no token budget, a
  common keyword (e.g. "host") matching many node labels pulls all of them into the prompt.
  Mitigation: keyword tokens are extracted from the *current message only* and matched with
  `ILIKE` against `graph_nodes.label` for the one engagement; v1 graphs are modest; a cap is
  a localized follow-up in `subset_builder`. Covered by `test_keyword_ilike_matches_label`.
- **Risk 4 — Carrying request-time inputs to stream time across the persist-first/dropped-
  socket flow.** The POST returns before streaming; the WS does not receive the id lists.
  Mitigation (Decision 4): stash the raw inputs on the pending assistant row at POST
  time, resolve+overwrite with the canonical subset at finalize, so a reconnect re-resolves
  from the stash and a dropped socket leaves a recoverable `pending` row (Slice 11 Risk 2
  pattern preserved).
- **Risk 5 — Cross-user / cross-engagement leak via the debug endpoint.** The debug record
  contains graph labels/values; a missing ownership/membership check would leak another
  user's context. Mitigation: `get_turn_debug` runs the same membership+ownership chokepoint
  as the Slice-11 reads (404 on any failure); `test_get_turn_debug_owner_only_404` and the
  integration `test_debug_private_per_user` guard it. Foreign node ids in the request are
  intersected away (`test_foreign_ids_ignored`).
- **Risk 6 — No-redaction temptation on graph values.** Node properties may contain
  secret-looking values (credentials node, §8.1). It is tempting to strip them before the
  model. Forbidden (§5.5 / CLAUDE.md). Mitigation: labels/properties pass through verbatim;
  `test_content_not_redacted` / `test_prompt_graph_block_not_redacted` assert it; the local
  path has no egress, and cloud-egress friction is Slice 14.
- **Risk 7 — Keyword match performance on large graphs.** An `ILIKE '%token%'` scan per
  message could be slow. Mitigation: v1 scope is web apps with modest graphs (§1, Slice 08
  Risk 4); start with `ILIKE` on the indexed-by-engagement node read, and leave Postgres FTS
  (`to_tsvector`/GIN) as a tuning follow-up (Decision 2).

## Planning decisions (resolved with the human)

All planning questions were resolved before start-slice. Recorded here for traceability and
for the audit-drift check:

1. **"Last N nodes touched" source — RESOLVED: client-supplied approximation.** The client
   sends a small `recent_node_ids` list = (pinned set ∪ currently/last selected node),
   most-recent-first, server-truncated to `N`. A real server-side per-conversation node-touch
   log is deferred (would need a new table + turn→node linkage that doesn't exist until AI
   tool/graph actions, Slice 16).
2. **Keyword match — RESOLVED: plain `ILIKE`.** Start with `ILIKE` on `graph_nodes.label`
   (simplest, no migration); defer Postgres FTS (`to_tsvector`/GIN) until graph sizes warrant
   it.
3. **Token budget — RESOLVED: none (explicit user direction).** No token budget, no overflow
   summarization, no aggregation: the assembled subset is sent to the model **in full and
   verbatim**. `N` (recent cap, default 15) and `K` (mentioned cap, default 10) remain as
   settings; **no** `ADEPTUS_GRAPH_CONTEXT_TOKEN_BUDGET` setting is added. This is a
   deliberate divergence from the literal §5.3 "hard token budget … overflow summarized"
   clause (see Requirements traceability and Risk 2); reintroducing a budget is a localized
   follow-up in `subset_builder` if real prompts grow too large.
4. **Carrying request-time id lists to stream time — RESOLVED: stash on the pending row.**
   Persist the raw client inputs on the pending assistant row's `graph_context` at POST, then
   resolve+overwrite with the canonical subset at finalize (survives dropped sockets, keeps
   the WS frame unchanged).
5. **Debug record storage — RESOLVED: one JSONB column.** One nullable `graph_context` JSONB
   column on `chat_messages` (1:1 with the turn, same ACL/lifecycle, additive migration).
   Split into a table when Slice 16 adds tool-call detail.

## Security review required?

**No.** This slice does not touch auth (it reuses the established membership + per-user
ownership chokepoints and the Slice-11 session-cookie WS auth without changing them), MCP,
the single-writer graph process (chat only *reads* the graph via the existing
`load_live_graph` path; no write, no writer queue), RAG isolation (no pgvector/embeddings
here — that is Slice 23), secrets storage, or the approval flow. It does NOT implement
egress: the relevant subset is sent only to the **local** Ollama (cloud egress + pattern
friction is Slice 14, which is risky and will require review). It touches the audit log only
by widening the already-reviewed `ai_call` payload dict — no new audit action, no new table,
no hash-chain change — so the audit integrity surface (Slice 10) is not re-opened. The two
surfaces a reviewer might still want to eyeball, because this is the first slice that puts
*graph content* into a prompt and into a new read endpoint: (a) the **no-redaction**
guarantee on node labels/properties (§5.5, Risk 6) and (b) the **per-user/per-engagement
isolation** of the new debug endpoint and the foreign-id rejection in the subset builder
(§5.4 / §17.1, Risk 5) — both are covered by named tests above.

## Progress

(The stop-checkpoint hook and compact-handoff skill append here. Leave empty at planning time.)
- 2026-06-05T11:36:56Z — fd3ca4e chore(workflow): finish-slice flips plan to done inside the slice PR (#35)
- 2026-06-05T11:39:42Z — fd3ca4e chore(workflow): finish-slice flips plan to done inside the slice PR (#35)
