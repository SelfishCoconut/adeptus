# Slice 11: Local LLM via Ollama + private chat

**Branch**: `slice-11-local-llm-private-chat`
**GitHub Issue**: #32
**Status**: in-progress
**Risky**: no

---

## Goal

Let a user type a message into the left chat pane of an engagement and receive a token-by-token streamed reply from the local Ollama model, with the whole conversation persisted privately per-user per-engagement.

## User-visible demo

After this slice is merged, with `make dev` up (Ollama reachable, the `qwen3.5:9b`
model pulled — ADR-0004):

- Open an engagement workspace. The left pane (previously a bare "AI chat" placeholder)
  now shows a real chat panel: a scrollable message list and a message composer at the
  bottom.
- Type a message ("what is SQL injection?") and press send. The user message appears
  immediately; the assistant reply then streams in **token by token** (visible
  incremental text), rendered as Markdown when complete.
- The privacy-mode banner from Slice 02 stays pinned above the panes the whole time. On a
  `local_only` engagement the reply is produced entirely by the local Ollama instance — no
  data leaves the local network, and there is no egress-friction modal (that is the cloud
  path, Slice 14).
- Reload the page (or close and reopen the browser): the conversation is still there —
  the user and assistant messages reload from the server in order.
- Open the **same** engagement as a **different** user: that user sees their own empty (or
  separate) conversation, never the first user's messages. Chat is private per user
  (§5.4); nothing is shared into a team channel in this slice.
- Stop Ollama (`docker compose stop ollama`) and send a message: the panel shows an
  inline "AI is unreachable — local model is offline" state instead of a hung spinner
  (§5.1 "if local LLM is unreachable"); existing messages remain readable and the rest of
  the workspace stays usable.
- As an admin, query the audit log (Slice 10 Audit tab): each completed AI turn produced
  exactly one `ai_call` audit entry attributed to the acting user (wires the reserved
  Slice-10 seam).

## Out of scope

This slice is deliberately the **thin local happy-path** for AI chat. It does NOT do any
of the following (each is a later, separately-tracked slice):

- Does **NOT** implement the **cloud LLM path or the pattern-friction egress modal**
  (Slice 14, `Depends on: 11, 02`). A `cloud_enabled` engagement still routes to the local
  Ollama model in this slice; cloud routing + secret-pattern friction is Slice 14. The
  privacy-mode field/banner is only *respected* (never redact, §5.5), not used to switch
  backends yet.
- Does **NOT** inject the **"relevant subset" of the graph** into the prompt (Slice 12,
  `Depends on: 08, 11`) and does **NOT** show the **AI debug panel** (§14). The prompt in
  this slice is: a fixed system prompt + the conversation's own recent messages. No graph
  context, no pinned-node weighting (§5.4 pinning is Slice 08/12), no RAG (§10, Slice 23).
- Does **NOT** implement the **visible plan / certainty signaling** (Slice 13,
  `Depends on: 11`).
- Does **NOT** implement **personas** (Slice 15, `Depends on: 11`). This slice ships a
  single hard-coded `general`-equivalent system prompt; persona selection/CRUD is Slice 15.
- Does **NOT** implement the **two-tier autonomy / approval flow** (Slice 16), **tool
  calling / function calling from the AI**, or any AI-initiated graph or tool action. The
  assistant only produces text. The model's tool-use capability (ADR-0004) is unused here.
- Does **NOT** implement **@-mentions, message sharing into a shared engagement channel,
  presence, or typing indicators** (Slice 31, `Depends on: 11`). Chat is strictly private
  per user in this slice (§5.4 "private chat per user"; the optional-sharing half of §5.4
  is Slice 31).
- Does **NOT** implement **conversation reset / fork / branching** or **AI-generated
  summaries of older context** (the §5.4 "reset and branching" and summary half). This
  slice persists a single linear conversation per `(user, engagement)` and sends a bounded
  window of recent messages verbatim. Summarization of older context is a follow-up.
- Does **NOT** implement **token / cost tracking display** (Slice 36) and does **NOT**
  enforce token budgets. It MAY store raw token counts returned by Ollama on the message
  row for a future slice to surface, but renders nothing.
- Does **NOT** implement **screenshot / image attachments** (§11.4, Slice 28) or any AI
  vision. Text only.
- Does **NOT** add **provenance columns** to graph/finding entities (§8.2 / §17.4 /
  CLAUDE.md anti-pattern). The `ai_call` audit entry is the attribution record; chat
  messages carry their own `role` but no cross-entity provenance.
- Does **NOT** widen `core/` or `shared/` — all backend code lives under
  `app/features/chat/`, all frontend code under `src/features/chat/`.

## Requirements traceability

- **§5.1 — LLM strategy, local path** — quoted relevant clauses:
  > **Local-first:** Ollama with a small quantized model as the default.
  > **If local LLM is unreachable:** prompt the user to choose an alternative (manual
  > switch, no automatic fallback).

  This slice implements the local-first Ollama call path with token streaming (the default
  model `qwen3.5:9b` per ADR-0004, configurable via env). When Ollama is unreachable it
  surfaces an explicit "AI is unreachable" state rather than silently failing or
  auto-falling-back (there is no second backend to fall back to in this slice — cloud is
  Slice 14, so the "manual switch" choice is the trivial one-option case; the requirement
  to *not auto-fallback* is honored). "Slow local model is acceptable — does not trigger
  fallback" (§5.1) is honored: the stream simply takes longer, no timeout-driven fallback.
- **§5.4 — private chat per user, scoped to the engagement** — quoted:
  > **Private chat per user**, scoped to the engagement.
  > **Context strategy (hybrid):** recent messages verbatim + AI-generated summaries of
  > older context + graph queried on demand (per the "relevant subset" rules in §5.3).

  This slice implements the **private chat per user, scoped to the engagement** and the
  **recent-messages-verbatim** part of the hybrid context strategy. The summary half and
  the graph-on-demand half are explicitly deferred (Slice 12 for graph subset; summaries
  are a follow-up — see Out of scope). The optional-sharing/@-mention half of §5.4 is
  Slice 31; pinning (§5.4) is Slice 08/12.
- **§5.5 — sensitive data handling / no redaction** — quoted:
  > **No redaction** before sending to the LLM — the AI needs full context to be useful.

  The message text is sent to the local model **verbatim**, unmodified. Privacy lives at
  the engagement toggle + the cloud egress-friction layer (Slice 14) — and the local path
  has **no egress at all**, so there is nothing to friction. This slice adds no redaction
  step anywhere (CLAUDE.md anti-pattern: "Don't redact data before sending to the LLM").
- **§5.3 (partial) — AI behavior** — only the trivial baseline: the assistant returns
  text. Visible plan, certainty signaling, clarifying-question prompting, and the
  relevant-subset graph injection (all §5.3) are explicitly out of scope (Slices 12, 13).
- **§11.2 — workspace layout (left pane = chat with the AI)** — this slice fills the
  left pane of the 3-pane `WorkspaceShell` with the real chat panel. Right (graph, Slice
  08) and bottom (console, Slice 04) are untouched.
- **§14 — audit log "records every ... AI call ... with user attribution"** — each
  completed AI turn emits one `ai_call` audit entry via the existing
  `audit.service.record` chokepoint (the reserved Slice-10 seam), attributed to the acting
  user. No provenance column is added to any entity (§8.2 / §17.4).
- **§4 — archived engagements are read-only** — an archived engagement accepts no new chat
  messages (the POST returns a conflict); existing messages remain browsable (the GET
  history endpoint still works). Consistent with §4 "chats remain browsable; no new ...
  AI actions can run."
- **§17.1 — engagement isolation** — chat read/write require explicit engagement
  membership; non-members (and missing engagements) get `404` (no existence disclosure).
  Reads return only the **caller's own** conversation (`WHERE engagement_id = ? AND
  user_id = ?`), enforcing the per-user privacy of §5.4.
- **ADR-0004 — default Ollama model `qwen3.5:9b`** — the default model and the
  `ADEPTUS_LLM_MODEL` env override are used as the model name in the Ollama call.
- **ADR-0001 — single-writer** — chat does NOT touch the graph and never goes through the
  single writer; it writes only its own `chat_*` tables.

## Design notes (load-bearing decisions)

### Transport: WebSocket stream, mirroring the Slice 04/06 tool-run WS

Token streaming uses a **WebSocket**, mirroring the established
`WS /ws/tool-runs/{tool_run_id}` pattern (slice 04/06) rather than inventing an SSE path
(the repo standard for live server→client streams is WebSockets — §16, and the frontend
already has `useToolRunStream` as a template). The flow:

1. The client `POST`s the user message to a normal HTTP endpoint, which **persists the
   user message and an empty `pending` assistant message in one transaction** and returns
   both ids (HTTP 201). This guarantees the user's text is durable even if the socket
   never opens.
2. The client opens `WS /ws/chat/{assistant_message_id}`. The socket authenticates via the
   session cookie (same pattern as the tool-run WS: cookie extracted in the router, a
   service `authenticate_ws_chat_message(...)` helper that does NOT slide the session or
   emit `Set-Cookie`; a single close code `4003` for every auth/authz failure — no
   existence disclosure).
3. The server calls Ollama with `stream=True`, relays each token chunk to the socket as a
   `token` frame, accumulates the full text, then on completion **persists the final
   assistant content + status `complete`**, emits a `done` frame, and closes `1000`. On an
   Ollama error/unreachable it persists status `failed`, emits an `error` frame, and closes
   `1000`.

**Why persist-first-then-stream:** the user's message and the assistant placeholder are
durable before any model work; a dropped socket or a server crash mid-stream leaves a
recoverable `pending`/`failed` row, not a lost message. On reconnect to a message that has
already completed, the socket replays the stored content then `done` (same "completed run
replays stored output" behavior as the tool-run WS).

### Ollama client lives in the chat feature, mocked in tests

A thin async Ollama client (`chat/ollama_client.py`) wraps the Ollama HTTP streaming
chat API (`POST {ADEPTUS_OLLAMA_URL}/api/chat` with `stream=true`, NDJSON token frames).
It is the single egress point to the model. **Per CLAUDE.md, every test mocks it** — no
unit or component test ever reaches a real Ollama. Two new settings:

- `ADEPTUS_OLLAMA_URL` (default `http://ollama:11434`, the compose service) — new.
- `ADEPTUS_LLM_MODEL` (default `qwen3.5:9b`, ADR-0004) — already documented in ADR-0004;
  read here for the first time.

A connection failure / non-200 from Ollama raises a domain `LlmUnreachableError` in the
client, caught in the service, persisted as a `failed` assistant message, and surfaced to
the WS as an `error` frame with a stable, non-leaky message.

### Context window: recent messages verbatim, bounded

Per turn the service builds the Ollama `messages` array as: `[system_prompt] + last N
messages of this conversation (verbatim, oldest→newest) + the new user message`. `N` is a
small constant (proposed `20`; see Open Questions) — the §5.4 "recent messages verbatim"
half. No summarization (deferred), no graph context (Slice 12), no RAG (Slice 23). The
system prompt is a single fixed `general`-style string defined in the chat feature
(personas are Slice 15).

### Audit emission timing

The `ai_call` audit entry is emitted **on turn completion**, in the same DB transaction as
the final assistant-message persistence (atomic, matching the Slice-10 Decision-1 policy),
attributed to the acting user, `engagement_id` set, `payload={model, message_id,
prompt_message_count, status}`. A `failed` turn still emits an `ai_call` entry with
`status=failed` so the forensic log records the attempt (§14 "records every AI call").

## Contract

OpenAPI delta. Two new HTTP endpoints + one WebSocket endpoint. All require
`cookieAuth`. Engagement-scoped reads/writes require explicit membership (`404` for
non-members / missing engagement, §17.1). The WebSocket is not part of the OpenAPI
document (consistent with the tool-run WS); its frame contract is specified below in
TypeScript and consumed by a hand-written hook, not the generated client.

```yaml
openapi: "3.1.0"
info:
  title: Adeptus API — Slice 11 delta
  version: "0.11.0"

paths:
  /api/v1/engagements/{engagement_id}/chat/messages:
    get:
      operationId: list_chat_messages
      summary: >-
        List the calling user's private chat messages for an engagement,
        oldest-first, paginated. Requires membership; returns only the caller's
        own conversation (§5.4 per-user privacy).
      security: [{ cookieAuth: [] }]
      parameters:
        - { name: engagement_id, in: path, required: true, schema: { type: string, format: uuid } }
        - { name: cursor, in: query, required: false, schema: { type: string } }
        - { name: limit, in: query, required: false, schema: { type: integer, minimum: 1, maximum: 100, default: 50 } }
      responses:
        "200":
          content:
            application/json:
              schema: { $ref: "#/components/schemas/ChatMessagePage" }
        "401": { description: Not authenticated }
        "404": { description: Engagement not found or caller not a member }

    post:
      operationId: send_chat_message
      summary: >-
        Persist a user message and an empty pending assistant message, then
        return both. The assistant reply is streamed separately over
        WS /ws/chat/{assistant_message_id}. Requires membership.
      security: [{ cookieAuth: [] }]
      parameters:
        - { name: engagement_id, in: path, required: true, schema: { type: string, format: uuid } }
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: "#/components/schemas/ChatMessageCreate" }
      responses:
        "201":
          description: User message + pending assistant message persisted.
          content:
            application/json:
              schema: { $ref: "#/components/schemas/SendChatMessageResult" }
        "401": { description: Not authenticated }
        "404": { description: Engagement not found or caller not a member }
        "409": { description: Engagement is archived (read-only, §4) }

components:
  schemas:
    ChatRole:
      type: string
      enum: [user, assistant]

    ChatMessageStatus:
      type: string
      enum: [complete, pending, failed]
      description: >-
        user messages are always 'complete'. An assistant message is 'pending'
        until its stream finishes ('complete') or errors ('failed').

    ChatMessageCreate:
      type: object
      required: [content]
      properties:
        content:
          type: string
          minLength: 1
          maxLength: 32768
          description: The user's message text, sent verbatim to the model (no redaction, §5.5).

    ChatMessage:
      type: object
      required: [id, engagement_id, role, content, status, created_at]
      properties:
        id: { type: string, format: uuid }
        engagement_id: { type: string, format: uuid }
        role: { $ref: "#/components/schemas/ChatRole" }
        content:
          type: string
          description: Empty string while an assistant message is 'pending'.
        status: { $ref: "#/components/schemas/ChatMessageStatus" }
        created_at: { type: string, format: date-time }

    SendChatMessageResult:
      type: object
      required: [user_message, assistant_message]
      properties:
        user_message: { $ref: "#/components/schemas/ChatMessage" }
        assistant_message:
          $ref: "#/components/schemas/ChatMessage"
          description: A 'pending' assistant placeholder; stream it via WS using its id.

    ChatMessagePage:
      type: object
      required: [items, next_cursor]
      properties:
        items:
          type: array
          items: { $ref: "#/components/schemas/ChatMessage" }
        next_cursor:
          oneOf: [{ type: string }, { type: "null" }]
          description: Opaque cursor for the next (older) page; null on the last page.
```

WebSocket frame contract (not in OpenAPI; declared in the frontend hook to match the
backend `WebSocketChatChunk` schema):

```typescript
// frontend/src/features/chat/hooks/useChatStream.ts — matches backend chat WS frames.
interface WebSocketChatChunk {
  type: 'token' | 'done' | 'error'
  // token: an incremental piece of assistant text (append to the buffer).
  data?: string
  // error: a stable, non-leaky reason (e.g. 'AI is unreachable — local model is offline').
  message?: string
}
```

WebSocket endpoint:

```
WS /ws/chat/{assistant_message_id}
  Auth: session cookie on the upgrade request (NOT get_current_user — no expiry slide,
        no Set-Cookie on a WS upgrade), mirroring the tool-run WS.
  Authz: caller must be a member of the assistant message's engagement AND the message
         must belong to the caller (per-user privacy, §5.4).
  Close 4003: ANY auth/authz failure (cookie missing/invalid/expired, message not found,
         non-member, or message not owned by caller) — single code, no disclosure.
  Behaviour:
    - assistant message 'pending': call Ollama (stream), relay 'token' frames, persist
      final content + status, send 'done', close 1000. On Ollama failure: persist
      'failed', send 'error', close 1000.
    - assistant message already 'complete': replay stored content as one 'token' frame
      then 'done', close 1000 (reconnect-safe replay).
    - assistant message already 'failed': send 'error' with the stored reason, close 1000.
```

## Data model changes

Alembic migration written via the `write-alembic-migration` skill during implementation
(register the new `app/features/chat/models.py` import in `backend/alembic/env.py` first —
per the Alembic-autogenerate memory; recreate the autogenerated file as the non-root user).

One new table. **No columns added to any existing table** (anti-pattern guard — the
migration touches no `graph_*` / `findings` / entity tables; AI attribution lives only in
`audit_entries`, §8.2 / §17.4).

- `chat_messages` — one private conversation per `(engagement_id, user_id)`, linear:
  - `id` UUID PK (`gen_random_uuid()`)
  - `engagement_id` UUID NOT NULL — FK → `engagements.id` `ON DELETE CASCADE` (a message
    is meaningless without its engagement; consistent with engagement-scoped data).
  - `user_id` UUID NOT NULL — FK → `users.id` `ON DELETE CASCADE`. The conversation owner
    (§5.4 per-user privacy). NOT a provenance column on a shared entity — `chat_messages`
    *is* the per-user chat table; ownership is its primary key concept, not attribution
    bolted onto shared truth.
  - `role` VARCHAR(16) NOT NULL — CHECK IN (`user`, `assistant`).
  - `content` TEXT NOT NULL DEFAULT `''` — empty while an assistant message is `pending`.
  - `status` VARCHAR(16) NOT NULL DEFAULT `'complete'` — CHECK IN (`complete`, `pending`,
    `failed`). `user` rows are always `complete`.
  - `model` VARCHAR(128) NULL — the Ollama model name for `assistant` rows (audit/debug;
    null for `user` rows).
  - `prompt_tokens` INTEGER NULL, `completion_tokens` INTEGER NULL — raw counts from
    Ollama if returned (stored for a future §14/Slice-36 surface; **not rendered** here).
  - `created_at` TIMESTAMPTZ NOT NULL DEFAULT `now()`
  - Indexes:
    - `ix_chat_messages_engagement_user_created` on `(engagement_id, user_id, created_at)`
      — the per-user conversation read (the load-bearing access path; oldest-first paging
      and the recent-window fetch both use it).

No separate `conversations` table in this slice: a conversation is implicitly the set of
rows with the same `(engagement_id, user_id)`. A `conversations` table becomes worthwhile
when fork/branch/reset (deferred §5.4) lands; deferring it keeps this slice thin. This is
flagged in Open Questions.

## Tasks

Numbered continuously across the whole slice (backend then frontend). Every commit subject
cites its task id, e.g. `feat(slice-11): add chat ollama client (task 3)`.

### Backend tasks

1. **[S]** Add `app/features/chat/models.py` — the `ChatMessage` ORM model on the shared
   `Base` (columns, `CheckConstraint`s, FKs, the composite index above). Register the
   module import in `backend/alembic/env.py`. No columns added to existing models.

2. **[S]** Add `app/features/chat/schemas.py` — `ChatRole` / `ChatMessageStatus`
   (StrEnums matching the contract), `ChatMessageCreate`, `ChatMessageRead`
   (`from_attributes=True`), `SendChatMessageResult`, `ChatMessagePage` (cursor
   pagination, mirroring `audit.schemas`), the internal `WebSocketChatChunk`, and the
   value object for the Ollama `messages` array. Tests in `tests/test_schemas.py`
   (validation bounds: empty content rejected, `maxLength`).

3. **[M]** Add `app/features/chat/ollama_client.py` — a thin async client over the Ollama
   streaming chat API: `async def stream_chat(*, model, messages) -> AsyncIterator[str]`
   yielding token strings, plus a final usage tuple, reading `ADEPTUS_OLLAMA_URL` /
   `ADEPTUS_LLM_MODEL` from settings. Raises `LlmUnreachableError` on connection failure
   or non-200. Tests in `tests/test_ollama_client.py` with the HTTP layer **mocked**
   (no real Ollama, per CLAUDE.md): yields tokens from a fake NDJSON stream; raises
   `LlmUnreachableError` on connection error / 500; passes the configured model through.
   Add `ADEPTUS_OLLAMA_URL` to `app/core/config.py` (`ADEPTUS_LLM_MODEL` too if not
   already present — ADR-0004).
   - Test command: `make test-backend` (targeted: `pytest app/features/chat/tests/test_ollama_client.py`).

4. **[M]** Add `app/features/chat/repository.py` — `insert_user_and_pending_assistant(db,
   *, engagement_id, user_id, content) -> tuple[ChatMessage, ChatMessage]` (both rows in
   one flush), `get_message_for_owner(db, *, message_id, user_id) -> ChatMessage | None`,
   `recent_messages(db, *, engagement_id, user_id, limit) -> list[ChatMessage]`
   (oldest-first window for the prompt), `list_conversation(db, *, engagement_id, user_id,
   cursor, limit)` (oldest-first paginated read), and `finalize_assistant(db, *,
   message_id, content, status, model, prompt_tokens, completion_tokens)`. Tests in
   `tests/test_repository.py`: pending pair inserted; owner scoping (another user's
   message returns None); recent-window ordering + bound; pagination; finalize transitions
   pending→complete and pending→failed.
   - Test command: `make test-backend` (`pytest app/features/chat/tests/test_repository.py`).

5. **[M]** Add `app/features/chat/service.py` with domain logic + the membership/archived
   chokepoints + the streaming orchestration:
   - `send_message(db, *, engagement_id, requester, content) -> SendChatMessageResult` —
     membership chokepoint (`eng_repo.get_engagement_for_member`; `NotFoundError`→404 for
     non-members/missing, §17.1); archived-engagement guard (raise a domain
     `EngagementArchivedError`→409, §4); persists the user + pending assistant rows.
   - `list_messages(db, *, engagement_id, requester, cursor, limit) -> ChatMessagePage` —
     membership chokepoint; returns only the caller's own conversation.
   - `authenticate_ws_chat_message(db, *, session_id, message_id) -> ChatMessage | None` —
     mirrors `mcp.service.authenticate_ws_tool_run`: resolves the session WITHOUT sliding
     expiry, checks membership AND ownership; returns the assistant `ChatMessage` row or
     `None` on any failure (single close code at the router).
   - `stream_assistant_reply(*, message) -> AsyncIterator[WebSocketChatChunk]` — builds the
     prompt (system + recent verbatim window + the user message), calls
     `ollama_client.stream_chat`, yields `token` chunks, on completion persists the final
     content/status + emits `ai_call` audit (`audit.service.record`, atomic with the
     persist), yields `done`; on `LlmUnreachableError` persists `failed`, emits `ai_call`
     with `status=failed`, yields `error`. For an already-`complete`/`failed` message it
     replays stored content/reason (reconnect-safe).
   - Tests in `tests/test_service.py` (Ollama client + audit `record` **mocked**):
     `test_send_message_persists_pending_pair`, `test_send_message_non_member_404`,
     `test_send_message_archived_409`, `test_list_messages_only_own_conversation`,
     `test_ws_auth_rejects_non_owner`, `test_stream_relays_tokens_then_done`,
     `test_stream_persists_complete_and_emits_ai_call`,
     `test_stream_unreachable_persists_failed_and_emits_error`,
     `test_stream_replays_completed_message`, `test_prompt_uses_recent_window_verbatim`
     (asserts no redaction — content passed through unchanged, §5.5).
   - Test command: `make test-backend` (`pytest app/features/chat/tests/test_service.py`).

6. **[M]** Add `app/features/chat/router.py` — `GET` + `POST`
   `/api/v1/engagements/{engagement_id}/chat/messages` (depending on `get_current_user`),
   and `WS /ws/chat/{assistant_message_id}` (cookie auth via `authenticate_ws_chat_message`,
   close `4003` on any failure, then stream via `stream_assistant_reply`). Membership/
   archived domain exceptions translate via the registered handlers
   (`NotFoundError`→404); the archived 409 is translated inline (same pattern as the MCP
   router's inline 409, since no core error maps to 409 without an ADR). Tests in
   `tests/test_router.py` (`AsyncClient` + session override; Ollama mocked):
   `test_post_message_201_for_member`, `test_post_message_404_for_non_member`,
   `test_post_message_409_when_archived`, `test_list_messages_200_only_own`,
   `test_list_messages_404_for_non_member`, `test_messages_unauthenticated_401`,
   and WS tests `test_ws_streams_tokens_and_done`, `test_ws_closes_4003_unauthenticated`,
   `test_ws_closes_4003_for_non_owner`, `test_ws_replays_completed_message`.
   - Test command: `make test-backend` (`pytest app/features/chat/tests/test_router.py`).

7. **[S]** Wire the chat router in `app/main.py` (`include_router`). No new error handler
   (existing `NotFoundError` handler covers 404; the 409 is inline).

8. **[S]** Add Alembic migration for `chat_messages` via the `write-alembic-migration`
   skill. Confirm `make migrate` applies it cleanly against a fresh DB and
   `alembic downgrade -1` reverts it.
   - Test command: `make migrate` then `alembic downgrade -1` (in the backend container).

### Frontend tasks

Numbering continues from the backend tasks.

9. **[S]** Run `make generate-api` to regenerate types into `frontend/src/shared/api/`;
   commit the updated `frontend/openapi.json` snapshot (adds `ChatMessage`,
   `ChatMessageCreate`, `SendChatMessageResult`, `ChatMessagePage`, `ChatRole`,
   `ChatMessageStatus`).
   - Test command: `make generate-api` (then `make lint` to confirm types compile).

10. **[M]** Add `frontend/src/features/chat/api.ts` — `useChatMessages(engagementId)`
    (`GET`, cursor pagination, `chatKeys` factory) and `useSendChatMessage(engagementId)`
    (`POST`, optimistic append of the user message + pending assistant placeholder,
    invalidate/refetch on settle). Tests in `__tests__/api.test.tsx` (mock `api.GET`/
    `api.POST`): paginates via `next_cursor`; send mutation surfaces both returned ids;
    `404` surfaced as an error.
    - Test command: `make test-frontend` (`vitest run src/features/chat/api.test.tsx`).

11. **[M]** Add `frontend/src/features/chat/hooks/useChatStream.ts` — a hook modeled on
    `useToolRunStream`: given an `assistantMessageId | null`, open
    `WS /ws/chat/{id}`, append `token` frames to a text buffer, resolve `isDone` on `done`,
    surface a stable `error` message on `error`, close on done/error/unmount, reset on id
    change. Tests in `useChatStream.test.ts` (mock `WebSocket`): accumulates tokens; sets
    done; surfaces error; closes on unmount.
    - Test command: `make test-frontend` (`vitest run src/features/chat/hooks/useChatStream.test.ts`).

12. **[M]** Add `frontend/src/features/chat/components/ChatMessageList.tsx` + test — a
    scrollable list rendering each message (user vs assistant styling), Markdown for
    completed assistant content (`react-markdown`, §11.1), a streaming-token live region
    for the in-flight assistant message, and a "failed" inline state. Test: renders user +
    assistant rows; shows streaming text from the hook; shows the failed/offline state.
    - Test command: `make test-frontend` (`vitest run src/features/chat/components/ChatMessageList.test.tsx`).

13. **[M]** Add `frontend/src/features/chat/components/ChatComposer.tsx` + test — the
    bottom composer (textarea + send button, disabled while empty or while a turn is
    streaming, disabled with a hint when the engagement is archived). Calls
    `useSendChatMessage`. Test: empty send disabled; submitting clears the input and calls
    the mutation; archived → disabled.
    - Test command: `make test-frontend` (`vitest run src/features/chat/components/ChatComposer.test.tsx`).

14. **[M]** Add `frontend/src/features/chat/components/ChatPanel.tsx` + test — composes
    the list + composer + stream hook: holds the current `assistantMessageId` to stream,
    wires `useSendChatMessage` → on 201 sets that id → `useChatStream` streams it → on
    `done` invalidates `useChatMessages`. Surfaces the §5.1 "AI is unreachable" state from
    the stream hook's `error`. Test: send → optimistic user message → streaming assistant
    text → settled history refetch; offline path shows the unreachable banner.
    - Test command: `make test-frontend` (`vitest run src/features/chat/components/ChatPanel.test.tsx`).

15. **[S]** Wire `<ChatPanel engagementId={engagementId}>` into the left pane of
    `WorkspaceShell` (replacing the bare "AI chat" placeholder `<section>`), guarded on
    `engagementId` being present (mirroring how the Console pane guards the tool runner).
    Update `WorkspaceShell.test.tsx`. The Slice-02 `PrivacyModeBanner` above the panes is
    untouched and stays visible (§5.5).
    - Test command: `make test-frontend` (`vitest run src/features/workspace/WorkspaceShell.test.tsx`).

## Test plan

- **Unit — backend** (coverage ≥ 80% on `app/features/chat/`):
  - Schemas (`tests/test_schemas.py`): content min/max length; enum values.
  - Ollama client (`tests/test_ollama_client.py`, HTTP **mocked**): yields tokens from a
    fake NDJSON stream; raises `LlmUnreachableError` on connect error / non-200; uses the
    configured `ADEPTUS_LLM_MODEL` and `ADEPTUS_OLLAMA_URL`.
  - Repository (real async test DB): pending-pair insert; owner-scoped fetch; recent
    window ordering + bound; pagination; finalize transitions.
  - Service (Ollama client + audit `record` **mocked**): the ten `test_*` names listed in
    backend task 5, including the no-redaction assertion (§5.5) and the membership/
    archived/ownership gates.
  - Router (`AsyncClient`, Ollama **mocked**): the HTTP + WS `test_*` names listed in
    backend task 6 (incl. `4003` on unauthenticated/non-owner WS, and completed-message
    replay).
- **Unit — frontend** (coverage ≥ 60% on `src/features/chat/`):
  - `api.test.tsx`: pagination; send mutation returns both ids; error surfacing.
  - `useChatStream.test.ts` (mock `WebSocket`): token accumulation; done; error; cleanup.
  - `ChatMessageList.test.tsx`, `ChatComposer.test.tsx`, `ChatPanel.test.tsx`: as in tasks
    12–14.
  - `WorkspaceShell.test.tsx`: chat panel renders in the left pane; banner still present.
- **Integration** (`@pytest.mark.integration`, real Postgres; **Ollama still mocked** —
  external services are never hit in tests, CLAUDE.md):
  - `test_chat_round_trip_persists_and_streams` — POST a message (real DB), open the WS
    with a faked Ollama stream of N tokens, assert tokens relayed in order, the assistant
    row finalizes `complete` with the joined content, and exactly one `ai_call` audit
    entry is written attributed to the user. **Headline §5.4 + §14 happy-path.**
  - `test_chat_private_per_user` — two members of the same engagement each POST a message;
    each `GET` returns only their own conversation (§5.4 isolation).
  - `test_chat_unreachable_marks_failed` — fake Ollama raises; assert the assistant row is
    `failed`, the WS gets an `error` frame, and an `ai_call` `status=failed` audit entry
    exists.
- **E2E** (Playwright) — `chat.spec.ts`: log in, open an engagement, send a message,
  assert the user message appears and streamed assistant text accumulates then settles;
  reload and assert the conversation persisted. (Ollama in the Playwright stack is stubbed
  via a deterministic fake stream — no real model in CI; pentest/external-service rule.)

## Acceptance criteria

- `make test` passes (ruff + mypy + eslint + tsc + pytest + vitest + playwright); coverage
  gates hold (≥80% backend `chat` feature, ≥60% frontend `chat` feature).
- `make lint` passes with no new errors.
- `make migrate` applies the new `chat_messages` migration cleanly against a fresh Postgres
  container; `alembic downgrade -1` reverts it.
- `make generate-api` produces an updated `frontend/openapi.json` containing the chat
  schemas; the regenerated types are committed.
- `make dev` brings up the stack (Ollama reachable, `qwen3.5:9b` pulled); manual demo:
  1. Open an engagement → the left pane shows the chat panel; the Slice-02 privacy banner
     is still pinned above it.
  2. Send "what is SQL injection?" → the user message appears immediately; the assistant
     reply streams in token by token, then renders as Markdown.
  3. Reload the page → the conversation is still there in order.
  4. As a second member of the same engagement, the conversation pane is independent — no
     cross-user messages (§5.4).
  5. `docker compose stop ollama`, send a message → an inline "AI is unreachable — local
     model is offline" state appears; the rest of the workspace stays usable (§5.1).
  6. As an admin, open the Audit tab → each completed (and each failed) turn shows one
     `ai_call` entry attributed to the acting user (§14).
- `gh pr view` shows green CI.

## Risks

- **Risk 1 — Streaming over WebSocket vs. tool-run WS divergence.** Reusing the tool-run
  WS pattern (cookie auth, `4003`, replay-on-reconnect) keeps it consistent, but chat
  frames differ (`token`/`done`/`error` vs the richer tool frames). Mitigation: a separate
  `WebSocketChatChunk` schema and a separate `useChatStream` hook (do not overload
  `useToolRunStream`); the no-disclosure `4003` behavior and the persist-first ordering are
  copied deliberately and covered by `test_ws_*`.
- **Risk 2 — Lost or orphaned turns on a dropped socket.** A socket can drop mid-stream.
  Mitigation: persist-first (user + `pending` assistant in the POST transaction), finalize
  on completion, replay completed messages on reconnect, and surface `pending`/`failed`
  rows in the history read so the UI is never silently empty. Covered by
  `test_stream_replays_completed_message` and `test_chat_unreachable_marks_failed`.
- **Risk 3 — Accidental redaction / context leakage temptation.** It is tempting to strip
  "secret-looking" content before the model "to be safe". Forbidden by §5.5 / CLAUDE.md —
  the local path has no egress and never redacts. Mitigation:
  `test_prompt_uses_recent_window_verbatim` asserts content passes through unchanged; no
  redaction code exists; the cloud egress-friction layer is explicitly Slice 14.
- **Risk 4 — Ollama hang on a slow model.** §5.1: a slow local model is acceptable and
  must NOT trigger fallback. Mitigation: no client-side hard timeout that aborts the stream
  into a "failed" state on slowness; the stream simply continues. Only a *connection*
  failure / non-200 maps to `LlmUnreachableError`. A generous **10-minute no-token-progress
  cap** (resolved Open Question 3) marks a truly wedged socket `failed` — it is reset by
  every token, so a slow-but-progressing model is never aborted.
- **Risk 5 — Cross-user privacy leak.** A bug in the `WHERE user_id = ?` scoping or the WS
  ownership check would leak one user's private chat to another (violating §5.4 / §17.1).
  Mitigation: ownership is enforced in both the read service and the WS auth helper;
  `test_list_messages_only_own_conversation`, `test_ws_auth_rejects_non_owner`, and the
  integration `test_chat_private_per_user` guard it.
- **Risk 6 — Audit double/under-count.** Emitting `ai_call` at the wrong point could miss a
  failed turn or double-count on reconnect-replay. Mitigation: emit exactly once at the
  *first* finalization (the pending→complete/failed transition), never on a replay of an
  already-terminal message; `test_stream_persists_complete_and_emits_ai_call` and
  `test_stream_replays_completed_message` (asserts no second `ai_call`) guard it.

## Open questions for the human — RESOLVED at start-slice (2026-06-05)

All four resolved by the human accepting the proposed defaults. Recorded here as binding
decisions for the implementer:

1. **Recent-context window size `N` → `N = 20` messages, verbatim, no summarization.**
   Ship the last 20 messages now. A token-budget bound is deferred until summaries + graph
   context land (Slice 12). (Original question: count vs. token-budget; §5.3's hard token
   budget governs the Slice-12 graph subset, not this slice's plain message window.)

2. **No `conversations` table this slice → DEFER it.** A conversation stays implicitly the
   `(engagement_id, user_id)` row set; no `conversations` row/id in this slice. The table
   lands with the deferred §5.4 reset/fork/branch feature (a fork = a new conversation id).
   Accepted that this incurs a later migration when that feature arrives.

3. **Upper-bound socket timeout for a wedged (not slow) stream → YES, add a safety valve.**
   Implement a generous **no-token-progress cap of 10 minutes**: if the model emits no
   token (and no error) for 10 minutes, mark the turn `failed` and close the socket. This is
   explicitly a wedged-socket safety valve, **not** a slow-model fallback — a slow model
   that is still emitting tokens (or still streaming) is never aborted (§5.1). See Risk 4.

4. **System prompt content → neutral placeholder accepted.** Hard-code a single neutral
   `general`-style system prompt (e.g. "You are a penetration-testing assistant…") until
   Slice 15 owns persona prompts. No special baseline wording required.

## Security review required?

**No.** This slice does not touch auth (it *reuses* the established session-cookie WS-auth
pattern without changing it), MCP, the single-writer graph, RAG isolation, secrets storage,
or the approval flow. It does NOT implement egress (the local Ollama path has no egress;
cloud egress + pattern-friction is Slice 14, which is flagged risky and *will* require
security review). It touches the audit log only by *calling* the already-reviewed
`audit.service.record` chokepoint (Slice 10) — it adds no new audit table, no new hashing,
and no change to the chain mechanism — so it does not re-open the audit integrity surface.
The privacy posture (§5.5) is honored by *not* redacting and by reusing the Slice-02
banner. Per-user isolation (§5.4 / §17.1) is enforced by ordinary membership + ownership
checks of the same shape already reviewed in Slices 01/10 and is covered by the privacy
tests above. If the reviewer of the day disagrees because this is the first AI-egress-shaped
slice, the per-user isolation checks (Risk 5) and the no-redaction guarantee (Risk 3) are
the two surfaces to confirm.

## Progress

(The stop-checkpoint hook and compact-handoff skill append here. Leave empty at planning time.)
- 2026-06-05T09:44:37Z — b70e19c chore(plan): flip slice 10 in-review -> done (#30) (#31)
