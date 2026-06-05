# Slice 14: Cloud LLM + pattern-friction egress

**Branch**: `slice-14-cloud-llm-pattern-friction-egress`
**GitHub Issue**: #40
**Status**: in-progress
**Risky**: yes

---

## Goal

On a cloud-enabled engagement, route the AI turn to the Claude API instead of local Ollama, and gate any send whose text matches a likely-secret pattern behind an explicit "send anyway?" confirmation modal — friction, never redaction.

## User-visible demo

After this slice is merged, with `make dev` up (Ollama reachable, `qwen3.5:9b` pulled; an
admin-configured Claude API key present via `ADEPTUS_ANTHROPIC_API_KEY`):

- Open a **`local_only`** engagement (the default, Slice 02). Chat behaves **exactly** as
  in Slices 11/12/13: the reply is produced entirely by the local Ollama model, the
  green "Local only" banner is pinned above the panes, and **no egress modal ever
  appears** — even if you type an obvious secret. There is no cloud egress on this path,
  so there is nothing to friction (§5.5).
- Open (or flip, as owner) an engagement to **`cloud_enabled`** (Slice 02 toggle). The
  amber "Cloud enabled — data may leave the local network" banner shows. Send an ordinary
  message ("what is SQL injection?"): the reply streams in token-by-token just like the
  local path — but it is now produced by **Claude** (you can confirm in the Debug panel:
  `model` reads the configured Claude model `claude-sonnet-4-6`, not `qwen3.5:9b`).
- On the same cloud-enabled engagement, type a message containing a likely secret, e.g.
  `here is the key AKIA…EXAMPLE and password=hunter2` (an AWS-key-shaped string + a
  `password=` assignment — illustrative placeholders; the real match vectors live in the
  task-2 test suite), and press send. **Before
  anything leaves the machine**, a confirmation modal appears: *"This message looks like it
  may contain a secret (matched: AWS access key, `password=`). It will be sent to the
  cloud model unmodified. Send anyway?"* with **Send anyway** and **Cancel** buttons.
  - Click **Cancel**: nothing is sent, nothing is persisted, the composer keeps your text so
    you can edit it.
  - Click **Send anyway**: the message is sent **unmodified** (no redaction, §5.5) to
    Claude; the user message appears, the reply streams in, and the turn completes.
- As an admin, open the Audit tab (Slice 10): the friction-confirmed cloud turn shows its
  `ai_call` entry attributed to the acting user, with the payload recording that egress was
  pattern-flagged and explicitly confirmed (`egress_secret_flagged: true`,
  `egress_confirmed: true`) and that the turn went to the cloud backend (`backend: cloud`).
  An ordinary cloud turn records `egress_secret_flagged: false`.
- On a cloud-enabled engagement, if **no Claude API key is configured** (admin left it
  unset), sending a message yields an inline "Cloud LLM is not configured for this
  engagement" failure (the turn is persisted `failed`, the WS sends an `error` frame) — the
  local-only path is never silently used as a fallback (§5.1 "no automatic fallback"), and
  the rest of the workspace stays usable.

## Out of scope

This slice adds the **cloud backend** and the **pattern-friction egress gate** to the
existing chat path. It deliberately does NOT do the following (each is separately tracked):

- Does **NOT** implement **token / cost tracking display** (Slice 36, which `Depends on:
  14`). It MAY store the cloud response's token counts on the assistant row (the existing
  `prompt_tokens` / `completion_tokens` columns) for Slice 36 to surface, but renders
  nothing and enforces no cap (§5.1 "no enforcement / hard caps").
- Does **NOT** add or change the **privacy-mode field, the owner-only PATCH, or the
  persistent banner** — those are Slice 02 and are merely *consumed* here (the banner is
  untouched; this slice reads `engagement.privacy_mode` to choose a backend).
- Does **NOT** implement **personas** (Slice 15). The cloud call uses the same single
  neutral `SYSTEM_PROMPT` + Slice-12 context block + Slice-13 structured-output instruction
  the local path already builds; persona system prompts compose on top later.
- Does **NOT** implement **AI tool-calling / function calling, AI-initiated graph or tool
  actions, or the approval flow** (Slice 16). The cloud assistant only produces text +
  the Slice-13 metadata block, exactly like the local one.
- Does **NOT** **redact, mask, truncate, or rewrite** any message content anywhere
  (§5.5 / CLAUDE.md anti-pattern). The secret-pattern scan only *flags* and *gates*; on
  confirmation the message is sent **byte-for-byte unmodified**. The local model still
  receives content verbatim too (it always did).
- Does **NOT** add a **per-message "always send / suppress friction" preference** or a
  whitelist of allowed patterns. Every flagged send re-prompts (friction is per-send;
  Resolved decision 4). A standing "always confirm cloud egress for category X" delegation
  is the general §5.2 delegation pattern (Slice 18) and is explicitly not built here.
- Does **NOT** change the **WebSocket frame contract** (`token`/`done`/`error` with the
  Slice-13 `plan`/`claims` on `done`) — the cloud client yields tokens through the *same*
  `stream_assistant_reply` generator, so the WS surface is unchanged.
- Does **NOT** implement an **outbound proxy / network egress allow-list** for the cloud
  call (per-engagement SOCKS/HTTP proxy is §6.1, a tool-execution concern, Slice scope
  elsewhere). The Claude call goes out over normal HTTPS from the backend to the public
  Anthropic Messages API (Resolved decision 1).
- Does **NOT** add **provenance columns** to any graph/finding entity (§8.2 / §17.4). The
  egress decision and the chosen backend are recorded only in the `ai_call` audit payload.
- Does **NOT** widen `core/` or `shared/`. The cloud client + the egress scanner live under
  `app/features/chat/`; frontend changes under `src/features/chat/`.

## Requirements traceability

- **§5.1 — LLM strategy: cloud fallback + per-engagement toggle + pattern-friction** —
  quoted:
  > **Cloud fallback:** Claude API allowed when the engagement's privacy mode permits it.
  > **Per-engagement privacy toggle:** strict local-only mode disables cloud calls entirely.
  > **Pattern-friction layer for cloud egress:** when an engagement has cloud enabled,
  > outgoing messages are scanned with a lightweight heuristic regex pass for likely-secret
  > patterns (API keys, JWTs, `password=`, `BEGIN […] PRIVATE KEY`, etc.). If a match fires, the
  > UI presents a confirmation modal ("this message looks like it may contain a secret —
  > send anyway?") before the message leaves the local network. This is friction, not
  > redaction — the message is sent unmodified if confirmed.
  > **If local LLM is unreachable:** prompt the user to choose an alternative (manual switch,
  > no automatic fallback).
  > **Cloud cost:** token usage displayed in the UI, no enforcement / hard caps.

  **Headline clauses.** This slice: (a) routes the turn to the Claude API when
  `engagement.privacy_mode == "cloud_enabled"` and a cloud key is configured, and to local
  Ollama otherwise; a `local_only` engagement **never** makes a cloud call (the toggle
  "disables cloud calls entirely"); (b) runs the lightweight regex secret-pattern scan on
  cloud-enabled sends and shows the confirmation modal **before** egress; (c) on a
  confirmed send transmits the content **unmodified** (friction, not redaction); (d) does
  not auto-fall-back across backends — a cloud-enabled engagement with no key fails the
  turn rather than silently using local (the only "alternative" is the user manually
  flipping the privacy toggle / configuring a key). Cost display stays Slice 36 (counts may
  be stored, not rendered). The cloud surface is the public Anthropic Messages API at
  `https://api.anthropic.com`, model `claude-sonnet-4-6`, both env-overridable per
  engagement/instance (Resolved decision 1).

- **§5.5 — Sensitive data handling / no redaction + persistent indicator** — quoted:
  > **No redaction** before sending to the LLM — the AI needs full context to be useful.
  > Privacy is enforced at the engagement level via the local-only toggle.
  > **Persistent visual indicator:** a banner shows the current engagement's privacy mode at
  > all times.
  > The pattern-friction layer in §5.1 catches accidental egress of likely secrets when cloud
  > mode is enabled, without lying to the AI by silently rewriting content.

  The scan is a **friction gate**, never a rewrite: the matched content is sent verbatim
  once confirmed; nothing is masked or stripped at any layer. The Slice-02 banner is reused
  unchanged (the persistent indicator). The friction layer fires only on cloud-enabled
  egress, exactly as specified.

- **§3 — Admins configure cloud LLM API keys for the instance** — quoted:
  > Admins configure cloud LLM API keys for the instance.

  The Claude API key is an **instance-level** secret read from configuration
  (`ADEPTUS_ANTHROPIC_API_KEY`, set by the admin via env — consistent with ADR-0002's
  env-seeded admin bootstrap and the existing `ADEPTUS_*` settings). It is **never** sent to
  the frontend, never logged, and never returned in any response. A per-engagement
  `cloud_enabled` toggle (Slice 02) gates *use*; the key gates *capability*.

- **§17.5 — Privacy posture visible and safe by default** — quoted:
  > Strict local-only is the default privacy mode. The persistent banner shows the current
  > engagement's privacy mode. The pattern-friction layer catches accidental egress when
  > cloud is enabled. The user is never surprised about whether data is leaving the local
  > network.

  Safe-by-default is inherited from Slice 02 (`local_only` default). This slice adds the
  "never surprised" guarantee for the cloud path: the modal is an explicit, blocking
  acknowledgement that this specific message is about to leave the local network, naming the
  matched pattern categories.

- **§14 — Audit log records every AI call with attribution** — quoted:
  > Records every tool run, AI call, graph edit, login, and approval/rejection — with user
  > attribution.

  The existing `ai_call` audit payload (Slice 11/12/13) is widened with the egress
  decision: `backend` (`local` | `cloud`), `egress_secret_flagged` (bool),
  `egress_confirmed` (bool), and `egress_match_categories` (list of matched pattern *names*,
  e.g. `["aws_access_key", "password_assignment"]` — **names only, never the matched
  secret value**). No new audit action, no new table, no hash-chain change — the audit
  integrity surface (Slice 10 / ADR-0010) is not re-opened (Resolved decision 3).

- **§17.1 — engagement isolation** — chat read/write keep the existing membership +
  ownership chokepoints (`get_engagement_for_member`, owner-scoped reads). The cloud backend
  changes *where* a turn is computed, not *who* may compute it. No cross-engagement data is
  introduced.

- **ADR-0004 — default Ollama model** — unchanged for the local path; the cloud path uses a
  new `ADEPTUS_ANTHROPIC_MODEL`, pinned to `claude-sonnet-4-6` (Resolved decision 1) read
  from settings, env-overridable per engagement/instance.

- **ADR-0001 — single-writer** — unchanged: chat (local or cloud) never writes the graph
  and never goes through the writer queue; it only *reads* the live graph for the Slice-12
  subset.

## Design notes (load-bearing decisions)

### Resolved decisions (locked by the human, 2026-06-05)

The four open questions raised at first planning were resolved by the human; each matched
the proposed default. They are folded into the relevant sections above and below and
restated here for the record:

1. **Cloud model + API surface.** `ADEPTUS_ANTHROPIC_MODEL` defaults to **`claude-sonnet-4-6`**,
   reached over the public Anthropic Messages API at **`https://api.anthropic.com`**, both
   env-overridable per engagement/instance (`ADEPTUS_ANTHROPIC_MODEL` /
   `ADEPTUS_ANTHROPIC_BASE_URL`). No Bedrock/Vertex route in v1.
2. **Scanner posture + v1 pattern set.** Ship the proposed precision-first set exactly as
   specced — `aws_access_key`, `private_key_block`, `jwt`, `password_assignment`,
   `generic_api_key`, `bearer_token`, `slack_token` — tuned precision-over-recall (this is
   friction, not DLP). **No additional patterns for v1** (no GitHub PAT, GCP SA JSON, or
   Stripe key in this slice).
3. **Audit shape.** Widen the existing `ai_call` audit payload (`backend`,
   `egress_secret_flagged`, `egress_confirmed`, `egress_match_categories`). **No new audit
   action and no new table** — the Slice-10 / ADR-0010 integrity surface is not re-opened.
4. **Friction granularity.** **Per-send only.** Every flagged send re-prompts. **No standing
   "always allow category X" toggle in this slice** — that delegation is the general §5.2
   pattern and is deferred to **Slice 18**.

### Decision 1 — Friction fires at the POST boundary, not at the WebSocket

The user is in the loop **only at the POST** (`POST .../chat/messages`). The WebSocket
(`WS /ws/chat/{assistant_message_id}`) is opened automatically by the frontend hook
immediately after the 201, with no user interaction — so a modal cannot live there. And
the egress to the cloud happens inside the WS streamer (`stream_assistant_reply` →
`anthropic_client`), **after** the pending pair is already persisted. Therefore:

- The **frontend** runs a pre-flight secret scan on the composer text *before* calling the
  POST mutation, **only when the engagement is `cloud_enabled`**. If it matches, it shows
  the modal and only proceeds (sending `confirmed_egress: true`) on "Send anyway".
- The **backend** re-runs the *same* scan server-side at the POST (defense in depth — the
  client scan is convenience/UX, the server is the source of truth) and **rejects** the POST
  with `409 Conflict` (a new `EgressConfirmationRequiredError`) when, and only when:
  (a) the engagement is `cloud_enabled`, **and** (b) the content matches a secret pattern,
  **and** (c) the request did **not** carry `confirmed_egress: true`. The 409 body carries
  the matched category names so a client that skipped its pre-flight (or a future
  non-browser client) gets the same friction. On a confirmed (or non-matching, or
  `local_only`) POST the pending pair is persisted as today.

This keeps the egress gate **before** the assistant row exists and **before** any token can
leave the machine, and makes the server the authority. The scan never runs on a
`local_only` engagement (no egress to gate) and never on the WS path.

### Decision 2 — Backend selection happens in `stream_assistant_reply`, by privacy mode

`stream_assistant_reply` currently always calls `ollama_client.stream_chat`. This slice
introduces a thin **router** at the top of the `pending` branch:

- Re-read the assistant message's engagement (already loaded via the owner lookup; add the
  privacy_mode read). If `privacy_mode == "cloud_enabled"` **and** a cloud key is configured
  → stream from `anthropic_client.stream_chat`. Otherwise → stream from
  `ollama_client.stream_chat` (existing behavior).
- If `privacy_mode == "cloud_enabled"` but **no key is configured**, do **not** silently use
  local (§5.1 no auto-fallback): finalize the turn `failed` with a stable, non-leaky reason
  (`CLOUD_NOT_CONFIGURED_MESSAGE = "Cloud LLM is not configured for this engagement"`) and
  emit `error` — the same shape as `LlmUnreachableError`.

Both clients are **token AsyncIterators with the identical signature** (`stream_chat(*,
messages: Sequence[OllamaChatMessage], model, usage) -> AsyncIterator[str]`) so the
buffering / sentinel-stripping / finalize / audit machinery in `stream_assistant_reply` is
reused verbatim — the only branch is *which* client to iterate. (The shared message value
object stays `OllamaChatMessage`; the Anthropic client maps `role`/`content` → the Messages
API shape internally, hoisting any leading `system` entry into the top-level `system`
param.) The `model_name` recorded on the row and in audit becomes `ADEPTUS_ANTHROPIC_MODEL`
(default `claude-sonnet-4-6`) on the cloud path so the Debug panel and audit reflect the
real backend.

### Decision 3 — The secret scanner is a pure, fully-unit-tested module

`app/features/chat/egress_scan.py` owns the heuristic regex pass:
`scan(content: str) -> list[EgressMatch]` where `EgressMatch` carries a stable `category`
name (NOT the matched substring — we never need to move the secret around to gate it, and
keeping only the category name out of the secret keeps it out of audit/logs, §5.5). The
v1 pattern set is **locked** to the §5.1 examples (Resolved decision 2) — no additions:

- `aws_access_key` — `AKIA`/`ASIA` + 16 base32 chars.
- `private_key_block` — `-----BEGIN ... PRIVATE KEY-----`.
- `jwt` — three base64url segments separated by dots, header starting `eyJ`.
- `password_assignment` — `password=` / `passwd:` / `pwd =` style key=value.
- `generic_api_key` — `api[_-]?key`/`secret`/`token` assignment with a long opaque value.
- `bearer_token` — `Authorization: Bearer <token>`.
- `slack_token` — `xox[baprs]-...` (representative high-precision token).

Each pattern is tuned for **precision over recall** (this is friction, not a DLP product;
a missed secret is a known limitation, a false-positive modal is annoying — Risk 4). The
module is the **single source of truth** shared by the POST guard and mirrored in spirit by
the frontend pre-flight (the frontend re-implements a *subset* in TS; the server is
authoritative — see Risk 3). The matched values are never persisted, never logged, never put
in the 409 body or the audit payload — only the category names are.

### Decision 4 — `confirmed_egress` is an explicit per-request acknowledgement

`ChatMessageCreate` gains `confirmed_egress: bool = False`. It means "the user has seen the
friction modal for this exact content and chose to send anyway". The server only consults it
on the `cloud_enabled` + matched path; on every other path it is ignored (a `local_only`
send with `confirmed_egress: true` is fine and simply unused — no egress happens). The flag
does not bypass any other check (membership, archived) and does not suppress the audit
record — a confirmed send is audited *as* confirmed (`egress_confirmed: true`). Friction is
**per-send**: this flag acknowledges one specific message only and never persists as a
standing preference (Resolved decision 4; standing delegation is Slice 18).

### Decision 5 — No new table; no migration

This slice adds **no columns and no tables**. The egress decision lives in the `ai_call`
audit payload (widened dict, no schema change to `audit_entries`; Resolved decision 3). The
cloud token counts reuse the existing `chat_messages.prompt_tokens` /
`completion_tokens` columns (Slice 11). `make migrate` is a **no-op** for this slice. Three
new settings are added to `app/core/config.py`: `ADEPTUS_ANTHROPIC_API_KEY` (default
`None`), `ADEPTUS_ANTHROPIC_MODEL` (default `"claude-sonnet-4-6"`),
`ADEPTUS_ANTHROPIC_BASE_URL` (default `"https://api.anthropic.com"`; overridable so
tests/self-host can point elsewhere).

## Contract

OpenAPI delta. **No new endpoint.** **One changed request schema** (`ChatMessageCreate`
gains `confirmed_egress`) and **one new error response** on the existing POST (`409` for
`EgressConfirmationRequiredError`, with a body listing matched categories). A new error
response schema `EgressConfirmationRequired` is added. The WebSocket frame contract is
**unchanged**. A contract change means `make generate-api` is required.

```yaml
openapi: "3.1.0"
info:
  title: Adeptus API — Slice 14 delta
  version: "0.14.0"

paths:
  /api/v1/engagements/{engagement_id}/chat/messages:
    post:
      # CHANGED: body gains confirmed_egress; a new 409 fires when a cloud-enabled
      # send matches a secret pattern and was not confirmed (§5.1 pattern-friction).
      operationId: send_chat_message
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
        "409":
          description: >-
            Either the engagement is archived (read-only, §4), OR the engagement is
            cloud-enabled and the message matched a likely-secret pattern but was not
            confirmed (§5.1 pattern-friction). The body distinguishes the two via the
            error payload shape below; clients re-send with confirmed_egress=true to
            proceed past the friction case.
          content:
            application/json:
              schema: { $ref: "#/components/schemas/EgressConfirmationRequired" }

components:
  schemas:
    # CHANGED: one new optional flag. Everything else (content, the three node-id lists)
    # is unchanged from Slice 11/12.
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
        recent_node_ids:
          type: array
          items: { type: string, format: uuid }
        mentioned_node_ids:
          type: array
          items: { type: string, format: uuid }
        confirmed_egress:
          type: boolean
          default: false
          description: >-
            The user has seen the cloud egress-friction modal for THIS content and chose to
            send it unmodified anyway (§5.1). Consulted only when the engagement is
            cloud_enabled and the content matched a secret pattern; ignored otherwise. Never
            suppresses the audit record (a confirmed send is audited as confirmed).

    # NEW: the body of the friction 409 (and reused as detail for the archived 409 with an
    # empty categories list / a distinguishing reason). categories are pattern NAMES only —
    # never the matched secret value (§5.5).
    EgressConfirmationRequired:
      type: object
      required: [reason]
      properties:
        reason:
          type: string
          enum: [egress_secret_flagged, engagement_archived]
          description: Why the POST was refused with 409.
        matched_categories:
          type: array
          items: { type: string }
          default: []
          description: >-
            Names of the secret-pattern categories the content matched (e.g.
            "aws_access_key", "password_assignment"). Empty for the archived reason. NEVER
            contains the matched value (§5.5) — only the category name, for the modal copy.
```

The WebSocket frame contract is **unchanged** from Slice 13:

```typescript
// frontend/src/features/chat/hooks/useChatStream.ts — UNCHANGED by this slice.
interface WebSocketChatChunk {
  type: 'token' | 'done' | 'error'
  data?: string
  message?: string
  plan?: PlanStep[]
  claims?: Claim[]
}
```

New frontend types (generated from the changed OpenAPI):

```typescript
// Generated into frontend/src/shared/api/ — ChatMessageCreate gains a field; new schema.
export interface ChatMessageCreate {
  content: string
  pinned_node_ids?: string[]
  recent_node_ids?: string[]
  mentioned_node_ids?: string[]
  confirmed_egress?: boolean   // NEW — defaults false on the server
}

export interface EgressConfirmationRequired {
  reason: 'egress_secret_flagged' | 'engagement_archived'
  matched_categories?: string[]   // category NAMES only, never the secret value
}
```

## Data model changes

**No new table. No new column. No Alembic migration** (Decision 5). `make migrate` is a
no-op for this slice; a fresh DB still migrates cleanly.

- The egress decision (`backend`, `egress_secret_flagged`, `egress_confirmed`,
  `egress_match_categories`) is written into the **existing** `ai_call` audit-entry
  `payload` JSON (no schema change to `audit_entries`; the hash-chain covers the payload as
  written — ADR-0010 — so the new keys are simply part of the hashed content). No new audit
  action or table (Resolved decision 3).
- Cloud token counts (when the Claude response returns them) reuse the existing
  `chat_messages.prompt_tokens` / `completion_tokens` columns (Slice 11) — not rendered
  here (Slice 36).
- **No column on any `graph_*` / `findings` entity** (anti-pattern guard, §8.2 / §17.4):
  the chosen backend and egress decision are audit data, not entity provenance.

## Tasks

Numbered continuously across the whole slice (backend then frontend). Every commit subject
cites its task id, e.g. `feat(slice-14): add egress secret scanner (task 2)`.

### Backend tasks

Ordered. Each independently testable. Complexity: S/M/L.

1. **[S]** Add the three Anthropic settings to `app/core/config.py`:
   `ADEPTUS_ANTHROPIC_API_KEY: str | None = None`,
   `ADEPTUS_ANTHROPIC_MODEL: str = "claude-sonnet-4-6"` (Resolved decision 1),
   `ADEPTUS_ANTHROPIC_BASE_URL: str = "https://api.anthropic.com"`.
   Tests in `tests/test_config.py` (or extend the existing one): defaults load; the key
   defaults to `None`; the model default is `claude-sonnet-4-6` and the base-url default is
   `https://api.anthropic.com`; both are env-overridable. Confirm the key is never exposed by
   any existing serializer (it is a plain setting, not a schema field).
   - Test command: `make test-backend` (`pytest app/features/chat/tests/test_config.py` or the core config test).

2. **[M]** Add `app/features/chat/egress_scan.py` — a pure module owning the heuristic
   regex set (Decision 3, locked v1 pattern set per Resolved decision 2): `EgressMatch`
   dataclass (`category: str`), the compiled patterns (named, precision-tuned), and
   `scan(content: str) -> list[EgressMatch]` returning the matched **category names**
   (deduplicated, stable order), `[]` when none. NO matched value is ever returned/stored.
   Tests in `tests/test_egress_scan.py` (this is core security logic — dense coverage):
   `test_aws_access_key_matched`, `test_private_key_block_matched`,
   `test_jwt_matched`, `test_password_assignment_matched`, `test_generic_api_key_matched`,
   `test_bearer_token_matched`, `test_slack_token_matched`, `test_clean_text_no_match`,
   `test_multiple_matches_deduped_category_names`, `test_match_never_includes_secret_value`
   (the returned objects carry only category names), and a few precision negatives
   (`test_ordinary_prose_with_the_word_password_not_matched`, e.g. "I forgot my password" is
   NOT a `password=` assignment).
   - Test command: `make test-backend` (`pytest app/features/chat/tests/test_egress_scan.py`).

3. **[M]** Add `app/features/chat/anthropic_client.py` — a thin async client over the Claude
   Messages streaming API (public surface `https://api.anthropic.com`, model
   `claude-sonnet-4-6` by default — Resolved decision 1) mirroring `ollama_client.stream_chat`'s
   signature: `async def stream_chat(*, messages: Sequence[OllamaChatMessage],
   model: str | None = None, usage: OllamaUsage | None = None) -> AsyncIterator[str]` yielding
   token strings, mapping the shared `OllamaChatMessage` array to the Messages API shape
   (hoisting a leading `system` entry into the top-level `system` param), reading
   `ADEPTUS_ANTHROPIC_API_KEY`/`ADEPTUS_ANTHROPIC_MODEL`/`ADEPTUS_ANTHROPIC_BASE_URL` from
   settings, populating `usage` from the response. Raises `LlmUnreachableError` (the
   **existing** chat domain error — reused so the streamer's failure branch is unchanged) on
   connection failure / non-2xx; raises a new `CloudNotConfiguredError` when the API key is
   `None`. The single cloud egress point — **mocked in every test** (CLAUDE.md: external
   services never hit). Tests in `tests/test_anthropic_client.py` (HTTP **mocked** via
   `httpx.MockTransport`): yields tokens from a faked SSE/stream; raises
   `CloudNotConfiguredError` when key unset; raises `LlmUnreachableError` on connect
   error / 5xx; the API key is sent in the auth header and **never** appears in any log;
   the configured model passes through (default `claude-sonnet-4-6`); `usage` populated.
   - Test command: `make test-backend` (`pytest app/features/chat/tests/test_anthropic_client.py`).

4. **[M]** Extend `app/features/chat/schemas.py` — add `confirmed_egress: bool = False` to
   `ChatMessageCreate`; add the `EgressConfirmationRequired` response schema (`reason`
   StrEnum `egress_secret_flagged` | `engagement_archived`, `matched_categories: list[str]
   = []`). Tests in `tests/test_schemas.py`: `confirmed_egress` defaults `False`;
   `EgressConfirmationRequired` round-trips; `matched_categories` defaults empty.
   - Test command: `make test-backend` (`pytest app/features/chat/tests/test_schemas.py`).

5. **[M]** Extend `app/features/chat/service.py` `send_message` with the POST-time egress
   gate (Decision 1 + 4): after the membership + archived checks, when
   `engagement.privacy_mode == "cloud_enabled"`, run `egress_scan.scan(content)`; if it
   matches **and** `confirmed_egress` is `False`, raise a new
   `EgressConfirmationRequiredError(matched_categories=[...])` (subclass of the core
   `ConflictError` → 409, carrying the category names). Stash the egress decision
   (`secret_flagged`, `confirmed`, `match_categories`) onto the pending assistant row's
   `graph_context` `inputs` stash so the streamer can read it at finalize for the audit
   payload (it is already passing a stash; extend `_input_stash`). A `local_only` send never
   scans. Tests in `tests/test_service.py`:
   `test_send_cloud_enabled_secret_unconfirmed_raises_egress_409`,
   `test_send_cloud_enabled_secret_confirmed_persists_pair`,
   `test_send_cloud_enabled_clean_text_no_friction`,
   `test_send_local_only_secret_never_scanned` (a secret on a local-only engagement persists
   with no friction — no egress to gate, §5.5),
   `test_egress_decision_stashed_on_pending_row`,
   `test_egress_409_body_carries_category_names_not_values` (no secret value leaks),
   `test_content_not_redacted_on_confirmed_send` (the persisted user content is byte-for-byte
   the input, §5.5).
   - Test command: `make test-backend` (`pytest app/features/chat/tests/test_service.py`).

6. **[L]** Extend `app/features/chat/service.py` `stream_assistant_reply` with backend
   selection (Decision 2): in the `pending` branch, read the engagement's `privacy_mode`;
   choose `anthropic_client.stream_chat` when `cloud_enabled` + key configured, else
   `ollama_client.stream_chat`; on `cloud_enabled` + no key, finalize `failed` with
   `CLOUD_NOT_CONFIGURED_MESSAGE` and emit `error` (reuse `_finalize_failed`; do NOT fall
   back to local — §5.1). Set `model_name` to the Claude model (default `claude-sonnet-4-6`)
   on the cloud path so the row + audit reflect the real backend. Widen `_emit_ai_call`
   payload with `backend` (`local`|`cloud`), `egress_secret_flagged`, `egress_confirmed`,
   `egress_match_categories` (read from the stash; Resolved decision 3). Tests in
   `tests/test_service.py` (both clients **mocked**):
   `test_stream_cloud_engagement_uses_anthropic_client`,
   `test_stream_local_engagement_uses_ollama_client`,
   `test_stream_cloud_without_key_finalizes_failed_no_fallback`,
   `test_ai_call_payload_records_backend_and_egress_decision`,
   `test_cloud_turn_records_token_counts`, `test_cloud_path_does_not_redact_content`
   (the prompt passed to the cloud client is the verbatim window, §5.5).
   - Test command: `make test-backend` (`pytest app/features/chat/tests/test_service.py`).

7. **[S]** `app/features/chat/router.py` — register a handler / inline translation so
   `EgressConfirmationRequiredError` returns `409` with the `EgressConfirmationRequired`
   body (and the existing `EngagementArchivedError` 409 returns the body with
   `reason=engagement_archived`, `matched_categories=[]` — so the single POST 409 has a
   consistent, distinguishable shape). No new endpoint. Tests in `tests/test_router.py`:
   `test_post_cloud_secret_unconfirmed_409_with_categories`,
   `test_post_cloud_secret_confirmed_201`, `test_post_archived_409_reason_archived`,
   `test_post_local_only_secret_201_no_friction`.
   - Test command: `make test-backend` (`pytest app/features/chat/tests/test_router.py`).

### Frontend tasks

Numbering continues from the backend tasks.

8. **[S]** Run `make generate-api` to regenerate `frontend/src/shared/api/` + commit the
   updated `frontend/openapi.json` (adds `confirmed_egress` to `ChatMessageCreate`; adds
   `EgressConfirmationRequired`).
   - Test command: `make generate-api` then `make lint`.

9. **[M]** Add `frontend/src/features/chat/egressScan.ts` — a TS mirror of a **subset** of
   the backend pattern set (the high-precision §5.1 examples: AWS key, private-key block,
   JWT, `password=`, bearer, generic api-key) returning matched category names; used for the
   **pre-flight** client scan only (the server is authoritative — Risk 3). Tests in
   `egressScan.test.ts`: matches each category; clean text → empty; returns names not
   values. (This is convenience UX; it never weakens the server gate.)
   - Test command: `make test-frontend` (`vitest run src/features/chat/egressScan.test.ts`).

10. **[M]** Add `frontend/src/features/chat/components/EgressConfirmModal.tsx` + test — a
    shadcn `Dialog`/`AlertDialog` that names the matched categories (human-readable labels,
    e.g. "AWS access key", "`password=` assignment"), states the message will be sent
    **unmodified** to the cloud model, and offers **Send anyway** / **Cancel**. Pure
    presentational (props: `open`, `categories: string[]`, `onConfirm`, `onCancel`). Tests:
    renders category labels; Send anyway → `onConfirm`; Cancel → `onCancel`; does NOT render
    the message content/secret value.
    - Test command: `make test-frontend` (`vitest run src/features/chat/components/EgressConfirmModal.test.tsx`).

11. **[M]** Wire the gate into the send flow (`ChatComposer.tsx` / `ChatPanel.tsx` + the
    `useSendChatMessage` mutation in `api.ts`): the composer/panel receives the engagement's
    `privacyMode` (already available via `useEngagement` in `EngagementWorkspacePage` —
    thread it down like Slice 02 threads it to the banner). On send, **when `privacyMode ===
    'cloud_enabled'`**, run `egressScan`; if it matches, show `EgressConfirmModal` and only
    call the mutation with `confirmed_egress: true` on confirm (Cancel keeps the composer
    text). On `local_only`, send directly (no scan, no modal). Friction is per-send — there is
    **no "remember my choice" affordance** (Resolved decision 4; that is Slice 18). Also
    handle the server 409 `egress_secret_flagged` defensively: surface the modal from the
    server's `matched_categories` and retry with `confirmed_egress: true` (covers a
    client/server pattern-set drift — Risk 3). Tests in `ChatComposer.test.tsx` /
    `ChatPanel.test.tsx`:
    `cloud + secret → modal shown, no send until confirm`; `cloud + secret + confirm →
    mutation called with confirmed_egress true`; `cloud + clean → sends directly`;
    `local_only + secret → sends directly, no modal`; `server 409 egress → modal from server
    categories, retry confirmed`.
    - Test command: `make test-frontend` (`vitest run src/features/chat/components/ChatPanel.test.tsx`).

12. **[S]** Confirm the cloud path is otherwise transparent in the UI: the reply still
    streams via the unchanged `useChatStream`; the Debug panel (Slice 12) `model` field now
    shows the Claude model (`claude-sonnet-4-6`) on cloud turns (no code change needed — it
    already renders `ChatTurnDebug.model`). Add a `ChatPanel` test asserting the
    unreachable/offline copy also covers the new "Cloud LLM is not configured" `error` frame
    (the hook surfaces the `message` verbatim; assert it renders). Verify coverage ≥ 60% on
    `src/features/chat/`; `make lint` clean (no `any`; narrow via generated types).
    - Test command: `make test-frontend` then `make lint`.

## Test plan

- **Unit — backend** (coverage ≥ 80% on `app/features/chat/`):
  - `tests/test_egress_scan.py` — the dense pattern-matching suite in backend task 2;
    this is the **core security logic** and gets the highest coverage: every category
    matched, clean text negative, precision negatives, dedup, and the no-secret-value
    guarantee.
  - `tests/test_anthropic_client.py` (HTTP **mocked**) — yields tokens; `CloudNotConfigured`
    when key unset; `LlmUnreachableError` on connect/5xx; key in header never logged; model
    pass-through (default `claude-sonnet-4-6`); usage populated.
  - `tests/test_schemas.py` — `confirmed_egress` default; `EgressConfirmationRequired`
    round-trip; `matched_categories` default empty.
  - `tests/test_service.py` (both LLM clients + audit `record` **mocked**) — the seven
    `send_message` egress-gate names (task 5) + the six `stream_assistant_reply` backend-
    selection names (task 6), including the no-redaction assertions (§5.5), the
    local-only-never-scanned case, and the no-auto-fallback case.
  - `tests/test_router.py` — the four POST 409/201 names in task 7.
- **Unit — frontend** (coverage ≥ 60% on `src/features/chat/`):
  - `egressScan.test.ts` — category matching, clean text, names-not-values.
  - `EgressConfirmModal.test.tsx` — labels, confirm/cancel callbacks, never renders the
    secret value.
  - `ChatComposer.test.tsx` / `ChatPanel.test.tsx` — the five send-flow names in task 11.
  - `ChatPanel.test.tsx` — the "Cloud LLM not configured" error-frame render (task 12).
- **Integration** (`@pytest.mark.integration`, real Postgres; **both LLM clients mocked** —
  external services never hit, CLAUDE.md), in `tests/test_integration.py`:
  - `test_cloud_turn_routes_to_anthropic_and_audits` — create a `cloud_enabled` engagement
    (with a configured fake key), POST a clean message, open the WS with a **faked Anthropic
    stream** of N tokens; assert tokens relayed in order, the assistant row finalizes
    `complete` with the joined content, `model` is `claude-sonnet-4-6`, and exactly one
    `ai_call` audit entry records `backend=cloud`, `egress_secret_flagged=false`. **Headline
    §5.1 cloud-path happy-path.**
  - `test_cloud_secret_unconfirmed_blocks_then_confirmed_sends_unmodified` — POST a
    secret-bearing message without `confirmed_egress` → 409 with the matched categories,
    nothing persisted; re-POST with `confirmed_egress=true` → 201, and the persisted user
    content equals the input **byte-for-byte** (no redaction, §5.5), and the `ai_call`
    payload records `egress_secret_flagged=true`, `egress_confirmed=true`. **Headline §5.1
    pattern-friction + §5.5 no-redaction.**
  - `test_local_only_secret_sends_without_friction` — a secret on a `local_only` engagement
    POSTs 201 with no `confirmed_egress`, routes to the **local** client, `backend=local`,
    `egress_secret_flagged=false` (no egress to gate).
  - `test_cloud_without_key_marks_failed_no_fallback` — a `cloud_enabled` engagement with no
    configured key: the WS turn finalizes `failed` with the cloud-not-configured reason and
    an `ai_call` `status=failed`, `backend=cloud`; the local client is **never** called
    (asserted on the mock).
- **E2E** (Playwright, opt-in stack) — extend the chat journey or add
  `egress-friction.spec.ts`: log in, open a `cloud_enabled` engagement (seeded with a fake
  key so the **stubbed** Anthropic stream answers deterministically — no real cloud call in
  CI; external-service rule), send a message containing a secret pattern, assert the
  confirmation modal appears, Cancel keeps the text, then Send anyway streams the reply;
  open a `local_only` engagement, send the same secret, assert **no** modal appears.

## Acceptance criteria

- `make test` passes (ruff + mypy + eslint + tsc + pytest + vitest + playwright); coverage
  gates hold (≥80% backend `chat`, ≥60% frontend `chat`).
- `make lint` passes with no new errors.
- `make migrate` is a **no-op** for this slice (no schema change); a fresh DB still
  migrates cleanly.
- `make generate-api` produces an updated `frontend/openapi.json` with `confirmed_egress`
  on `ChatMessageCreate` and the `EgressConfirmationRequired` schema; regenerated types are
  committed.
- `make dev` brings up the stack (Ollama reachable; a Claude key configured via
  `ADEPTUS_ANTHROPIC_API_KEY`; default model `claude-sonnet-4-6` over
  `https://api.anthropic.com`); manual demo:
  1. On a `local_only` engagement, send a message containing an obvious secret → it sends
     with **no modal**, produced by local Ollama (no egress, §5.5).
  2. Flip the engagement to `cloud_enabled` (owner toggle, Slice 02) → amber banner; send an
     ordinary message → it streams in, produced by **Claude** (Debug panel `model` shows
     `claude-sonnet-4-6`).
  3. Send a message containing `AKIA...`/`password=...` on the cloud engagement → the
     **confirmation modal** appears naming the matched categories *before* anything is sent;
     Cancel keeps the text; Send anyway transmits it **unmodified** and streams the reply.
  4. Unset the Claude key and restart; on the cloud engagement, send a message → an inline
     "Cloud LLM is not configured for this engagement" failure (no silent local fallback,
     §5.1); the rest of the workspace stays usable.
  5. As admin, open the Audit tab → the friction-confirmed turn's `ai_call` entry records
     `backend=cloud`, `egress_secret_flagged=true`, `egress_confirmed=true`, and the matched
     **category names** (never the secret value), §14.
- `gh pr view` shows green CI.

## Risks

- **Risk 1 — Silent cloud egress without friction (the worst failure).** A bug that lets a
  cloud-enabled secret-bearing message reach Claude without the modal/confirmation would be
  a direct §5.1/§17.5 violation. Mitigation: the gate is **server-authoritative** at the
  POST (Decision 1), enforced before the pending row exists; the client scan is only
  convenience. `test_send_cloud_enabled_secret_unconfirmed_raises_egress_409`,
  `test_post_cloud_secret_unconfirmed_409_with_categories`, and the integration
  `test_cloud_secret_unconfirmed_blocks_then_confirmed_sends_unmodified` guard it. This is a
  primary security-review focus.
- **Risk 2 — Accidental redaction / rewrite.** It is tempting to mask the matched substring
  "to be safe". Forbidden (§5.5 / CLAUDE.md). Mitigation: the scanner returns category names
  only and never touches the content; the confirmed message is sent and persisted
  byte-for-byte; `test_content_not_redacted_on_confirmed_send`,
  `test_cloud_path_does_not_redact_content`, and the integration byte-for-byte assertion
  guard it.
- **Risk 3 — Client/server pattern-set drift.** The frontend mirrors only a subset of the
  patterns; a client that misses a pattern (or a future non-browser client) could POST an
  unconfirmed secret. Mitigation: the **server re-scans and 409s** regardless of the client;
  the frontend handles that 409 by showing the modal from the server's `matched_categories`
  and retrying confirmed (task 11). The server is the source of truth.
- **Risk 4 — Scanner false-positives (annoyance) and false-negatives (missed secret).** A
  noisy scanner trains users to click through; a leaky one misses secrets. Mitigation:
  patterns are tuned precision-over-recall (Decision 3); this is explicitly *friction, not
  DLP* (§5.1) — the residual is a documented limitation, not a regression, and the pattern
  set is the natural extension point. The v1 set is **locked** (Resolved decision 2: no
  additions in this slice; precision-first posture confirmed by the human). Precision
  negatives are unit-tested (`test_ordinary_prose_with_the_word_password_not_matched`).
- **Risk 5 — Cloud API key leakage (logs / responses / frontend).** The instance key must
  never reach the client, logs, or any response (§3). Mitigation: the key is a backend-only
  setting, never a schema field, sent only in the Anthropic auth header; the client unit
  test asserts it never appears in logs; no endpoint returns it. Security-review focus.
- **Risk 6 — Silent cross-backend fallback.** Falling back to local when cloud is
  unconfigured/unreachable would mean a cloud-intended message *not* leaving (data-safe) but
  also could mean a local-intended turn going to cloud if the routing inverted. §5.1
  forbids auto-fallback. Mitigation: routing is a single explicit branch on `privacy_mode` +
  key presence; `cloud_enabled` + no key → `failed`, never local;
  `test_stream_cloud_without_key_finalizes_failed_no_fallback` and the integration
  no-fallback assertion guard it. A `local_only` engagement can never reach the cloud branch.
- **Risk 7 — Audit recording the secret value.** Putting the matched substring (or the full
  message) into the `ai_call` payload would persist the secret in the tamper-evident log.
  Mitigation: only **category names** go into audit and the 409 body — never the matched
  value (§5.5); `test_match_never_includes_secret_value`,
  `test_egress_409_body_carries_category_names_not_values` guard it.
- **Risk 8 — Anthropic API shape coupling.** The Messages streaming API (SSE event types,
  the `system` param hoist, usage fields) differs from Ollama's NDJSON. Mitigation: the
  difference is fully contained in `anthropic_client.py` behind the *identical*
  `stream_chat` signature; everything downstream (buffering, sentinel stripping, finalize,
  audit) is reused unchanged. The client is mocked in every test; the exact API details are
  verified against current Anthropic docs at implementation time (Context7 MCP). The pinned
  model `claude-sonnet-4-6` and base URL `https://api.anthropic.com` are env-overridable, so
  a model/endpoint change is config, not code.

## Open questions for the human

None. The four questions raised at first planning were resolved by the human on 2026-06-05
and are recorded under "Resolved decisions" in the Design notes above (cloud model pinned to
`claude-sonnet-4-6` over the public Anthropic Messages API; the precision-first v1 pattern
set shipped as specced with no additions; the egress decision widens the existing `ai_call`
audit payload with no new action/table; friction is per-send only, with standing delegation
deferred to Slice 18).

## Security review required?

**Yes.** This slice is the project's first real **cloud egress** surface and implements the
**secret-pattern friction** layer — both explicitly flagged risky in PROJECT_PLAN (§5.1 /
§5.5 / §17.5). A security reviewer is required at finish-slice time. The surfaces to confirm:

- **Egress gate correctness (Risk 1):** a cloud-enabled, secret-bearing, **unconfirmed**
  send is refused server-side (409) before any token leaves the machine; the gate is
  server-authoritative (the client scan is not trusted); a `local_only` engagement never
  reaches the cloud branch and is never scanned.
- **No redaction (Risk 2, §5.5):** the scanner only flags; confirmed content is sent and
  persisted byte-for-byte; nothing is masked/stripped/rewritten at any layer (local or
  cloud).
- **No silent fallback (Risk 6, §5.1):** `cloud_enabled` + no key → the turn fails; it does
  not silently use local; routing is a single explicit branch on `privacy_mode` + key.
- **API-key handling (Risk 5, §3):** the admin-configured instance key is backend-only,
  never serialized to the client, never logged, sent only in the Anthropic auth header.
- **No secret in audit/logs/error bodies (Risk 7, §5.5):** only category *names* — never the
  matched value — appear in the `ai_call` payload, the 409 body, and logs; the audit
  hash-chain (ADR-0010) is widened only by extra payload keys (no new action/table).
- **Isolation unchanged (§17.1):** membership + per-user ownership chokepoints are reused
  verbatim; the cloud backend changes *where* a turn is computed, not *who* may compute it.

## Progress

(The stop-checkpoint hook and compact-handoff skill append here. Leave empty at planning time.)
- 2026-06-05T16:28:03Z — 6e588cb Slice 13: Visible plan + certainty signaling (#39)
