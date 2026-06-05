# Slice 15: Personas (CRUD + seeded)

**Branch**: `slice-15-personas`
**GitHub Issue**: #42
**Status**: planned
**Risky**: no

---

## Goal

Let a user pick a named AI persona — `recon` / `web-exploit` / `report-writer` / `general` seeded out of the box, plus their own custom personas they can create/edit/delete — for the chat composer, so that the persona's system prompt shapes the very next turn and the user can switch persona mid-conversation.

## User-visible demo

After this slice is merged, with `make dev` up (Ollama reachable, `qwen3.5:9b` pulled; an
engagement that already works as in Slices 11/12/13/14):

- Open an engagement workspace. The left chat pane works exactly as in Slices 11–14, but the
  composer now carries a **persona switcher** (a small dropdown / select next to the send
  button). On first open it shows four seeded personas: **General** (the default,
  pre-selected), **Recon**, **Web Exploit**, and **Report Writer**.
- Pick **Recon** and send "where should I start on this target?". The reply streams in
  token-by-token as before — but the AI now answers in a reconnaissance-focused voice
  (enumeration, surface-mapping, passive collection first), because the **Recon** persona's
  distinct system prompt was prepended to this turn's prompt. The selected persona is shown
  on the assistant turn (a small persona chip/label on the message).
- Without resetting the conversation, switch the switcher to **Report Writer** and send
  "summarise what we've found so far". This turn uses the report-writer system prompt — the
  persona is switched **mid-chat**, per-turn, with no conversation reset (§5.3 / §5.4). The
  prior recon turns stay in the conversation and remain in the context window verbatim.
- Open **Manage personas** (a small link/button from the switcher). A panel lists the four
  seeded personas (read-only — they cannot be edited or deleted; they show a "built-in"
  badge) plus any personas you created. Click **New persona**, enter a name ("Cloud Pentest")
  and a system prompt, save → it appears in the list and in the composer switcher
  immediately.
- Edit your custom persona's prompt, save → the change is live for the next turn. Delete it →
  it disappears from the list and the switcher; if it was the currently-selected persona the
  switcher falls back to **General**.
- A **second user** in the same engagement opens their own chat: they see the same four
  seeded personas but **none** of the first user's custom personas — custom personas are
  private to their creator (§5.4 per-user privacy). Each user has their own persona library.
- Open the **Debug** panel (Slice 12/13) on an assistant reply: the raw prompt now shows the
  selected persona's system prompt as the leading system content (instead of the old fixed
  neutral prompt), so a power user can see exactly which persona shaped the turn (§14).
- The privacy banner (§5.5) and the cloud/local routing (Slice 14) are untouched: a persona
  only changes the **system prompt text**; on a `local_only` engagement nothing leaves the
  local network, and the cloud egress-friction gate (Slice 14) still fires on cloud-enabled
  sends regardless of persona.

## Out of scope

This slice ships **persona selection + per-user persona CRUD + seeding the four built-ins**.
It deliberately does NOT do the following (each is separately tracked or explicitly excluded):

- Does **NOT** make personas **engagement-scoped or team-shared**. A persona library is
  **per user** (§5.3 "Users can create, edit, and delete **their own** personas"); a custom
  persona is usable by its creator in **any** engagement they are a member of. There is no
  "share this persona with the team" affordance in v1 (noted as a possible follow-up, not
  gated). The four seeded personas are **global built-ins** visible to everyone (read-only).
- Does **NOT** persist a **per-engagement or per-conversation "current persona"** server-side.
  The selected persona is chosen **per send** (it rides on the POST body, like
  `confirmed_egress` in Slice 14) and is recorded on the assistant turn that used it. The
  switcher's current selection is **ephemeral client state** (Zustand, like the Slice-08 pin
  store), defaulting to `general`. Remembering the last-used persona across reloads is a
  possible polish follow-up, not in scope.
- Does **NOT** let users **edit or delete the four seeded personas** (`recon`, `web-exploit`,
  `report-writer`, `general`). They are built-in, read-only, and shared. A user who wants a
  tweaked recon prompt creates **their own** custom persona (CRUD). (Whether built-ins are
  user-clonable into an editable copy is flagged in Open Questions — not built unless the
  human asks.)
- Does **NOT** change the **WebSocket frame contract** (`token`/`done`/`error` with the
  Slice-13 `plan`/`claims` on `done`). The persona only changes the system-prompt text built
  inside `stream_assistant_reply`; the streamer's machinery (buffering, sentinel stripping,
  finalize, audit, cloud/local routing) is reused unchanged.
- Does **NOT** implement the **§5.3 structured-output / plan / certainty** behavior (that is
  Slice 13, already shipped). A persona's system prompt is **composed with** the existing
  Slice-13 `PLAN_CERTAINTY_INSTRUCTION` and the Slice-12 graph context block — the persona
  text **replaces only the base `SYSTEM_PROMPT` constant**, everything else still appends.
- Does **NOT** implement **conversation reset / fork / branching** (the deferred §5.4 half).
  Switching persona is **not** a reset — the conversation continues and prior turns stay in
  the verbatim window. Reset/fork is a separate follow-up.
- Does **NOT** implement **@-mentions, message sharing, presence, typing indicators**
  (Slice 31) or **AI tool-calling / approvals** (Slice 16). A persona is purely a
  system-prompt selector; it grants no new capability or autonomy.
- Does **NOT** add **provenance columns** to graph/finding entities (§8.2 / §17.4). The
  persona used for a turn is recorded on the **assistant `chat_message` row** (the turn that
  used it, via the existing per-turn JSONB seam) and in the `ai_call` audit payload — never
  on a shared graph entity.
- Does **NOT** widen `core/` or `shared/`. Backend code lives under
  `app/features/personas/` (the new feature) and a minimal seam into
  `app/features/chat/`; frontend code under `src/features/personas/` and a minimal seam into
  `src/features/chat/`.

## Requirements traceability

- **§5.3 — Personas (specialized agents)** — quoted:
  > **Personas (specialized agents):** named personas with distinct system prompts.
  > **Seeded out-of-the-box:** `recon`, `web-exploit`, `report-writer`, `general`.
  > Users can create, edit, and delete their own personas.

  **Headline clauses.** This slice (a) models a **persona = name + distinct system prompt**;
  (b) **seeds the four named built-ins** idempotently at startup as global read-only personas
  (mirroring the §3 admin-bootstrap pattern in `main.py` lifespan); (c) gives each user
  **create / edit / delete** over their **own** custom personas; and (d) wires persona
  selection into the chat send path so the chosen persona's system prompt shapes the turn.

- **§5.4 — Personas, Sessions & Mentions / private chat per user** — quoted:
  > **Private chat per user**, scoped to the engagement.
  > **Context strategy (hybrid):** recent messages verbatim + AI-generated summaries of older
  > context + graph queried on demand (per the "relevant subset" rules in §5.3).

  The persona switcher lives in the per-user private chat. A user's **custom** personas are
  private to that user (the same per-user ownership scoping as chat messages, §5.4 / §17.1);
  switching persona is per-send and never resets or shares the conversation. The
  recent-messages-verbatim window (Slice 11) is untouched — switching persona changes only the
  leading system prompt for the new turn, not the historical message window.

- **§5.5 — No redaction** — quoted:
  > **No redaction** before sending to the LLM — the AI needs full context to be useful.

  The persona's system prompt is **added** to the prompt; it never strips or rewrites the
  user's content or the model's output. Persona name / prompt text are stored and rendered
  verbatim. The local path has no egress; cloud egress friction (Slice 14) is unchanged and
  fires independently of persona.

- **§14 — Audit log records every AI call with attribution; AI debug panel** — quoted:
  > Records every tool run, AI call, graph edit, login, and approval/rejection — with user
  > attribution.
  > AI debug panel: raw prompts, model outputs, tool calls, and the exact "relevant subset"
  > of the graph used per turn.

  The existing `ai_call` audit payload (Slices 11–14) is widened with the persona used for
  the turn (`persona_id` + `persona_name` — the name is non-secret, human-readable). The
  Slice-12/13 debug panel's `raw_prompt` already shows the full prompt; with this slice the
  leading system content is the **persona's** prompt, so the panel transparently reflects which
  persona shaped the turn. No new audit action/table; no hash-chain change (payload-only).

- **§17.1 — engagement isolation** — chat read/write keep the existing membership + ownership
  chokepoints. A persona changes *what system prompt* a turn uses, not *who* may chat. A
  custom persona is owned by its creator and never leaks across users; a `persona_id` supplied
  on a send that the caller does not own (and is not a built-in) is rejected/ignored
  (falls back to `general`) — a user can never make a turn use another user's private persona
  prompt (§5.4 / §17.1).

- **§17.6 — "The AI shows its work"** — the Debug panel's raw prompt now reveals the active
  persona's system prompt, keeping the AI's behavior inspectable.

- **ADR-0002 — env-seeded admin bootstrap** — the four built-in personas are seeded by the
  same **idempotent startup-bootstrap** mechanism the admin user uses (`main.py` lifespan
  calls a `personas.service.bootstrap_system_personas(db)` that upserts the four built-ins by
  stable slug; safe to run on every boot, like `bootstrap_admin`). No new bootstrap concept.

- **ADR-0001 — single-writer** — personas never touch the graph and never go through the
  single writer; chat (local or cloud) is unchanged in its graph-read-only posture.

- **ADR-0004 — default Ollama model** — unchanged; a persona selects a **system prompt**, not
  a model. (Per-persona model selection is explicitly out of scope — see Open Questions.)

## Design notes (load-bearing decisions)

### Decision 1 — Personas are a NEW feature folder, with a thin seam into chat

Per CLAUDE.md ("one folder per feature"), personas get their own
`app/features/personas/` with the full layer set (`models.py`, `schemas.py`,
`repository.py`, `service.py`, `router.py`, `tests/`). Chat depends on personas (chat
resolves a `persona_id` to a system prompt when building the turn), so the dependency flows
**chat → personas** (personas never imports chat), the same direction as chat → audit /
chat → engagements already in `service.py`. The only new import in chat is
`from app.features.personas import service as personas_service` (or repository) to resolve the
system prompt; no persona logic lives in the chat feature.

### Decision 2 — One table, two ownership classes: global built-ins + per-user custom

A single `personas` table holds both the seeded built-ins and user-created personas,
discriminated by `is_builtin` + a nullable `user_id`:

- **Built-in** rows: `is_builtin = true`, `user_id = NULL`, a stable `slug`
  (`general` / `recon` / `web-exploit` / `report-writer`). Visible to **all** users,
  **read-only** (no edit/delete via the API). Seeded idempotently at startup.
- **Custom** rows: `is_builtin = false`, `user_id = <creator>`, `slug = NULL`. Visible to and
  editable/deletable by **only their creator** (§5.4 / §17.1).

The "list my personas" read returns `WHERE is_builtin = true OR user_id = :caller` — the
built-ins plus the caller's own. This is the same per-user scoping shape as chat
(`WHERE user_id = :caller`) with the built-in union added, so it inherits the reviewed
isolation pattern. (Rejected alternative: a separate `system_personas` table — it duplicates
columns and forks every read into a union of two tables; one table with a discriminator is
simpler and keeps the resolve-by-id path single.)

### Decision 3 — Persona is selected PER SEND, recorded on the turn; not stored server-side per conversation

The selected persona rides on the **POST body** (`persona_id: UUID | None` on
`ChatMessageCreate`, defaulting to the `general` built-in when null/absent), exactly like
`confirmed_egress` (Slice 14) and the three node-id lists (Slice 12) already do. Rationale:

- It lets the user **switch persona mid-chat** turn-by-turn with no server-side conversation
  state and no reset (the headline §5.3 demo).
- `send_message` validates the `persona_id` (membership of the *caller* over the persona:
  built-in OR owned-by-caller; an unknown/foreign id falls back to the `general` built-in,
  §17.1 — never an error that leaks another user's persona's existence), then **stashes the
  resolved `persona_id` + `persona_name` on the pending assistant row's `graph_context`
  `inputs` stash** (the same Slice-12/14 stash seam) so the streamer can read it at stream
  time and finalize, and so the audit payload + the read schema can surface it.
- At stream time, `stream_assistant_reply` reads the stashed `persona_id`, loads the persona's
  system prompt, and passes it to `_build_prompt` (Decision 4). Persona resolution at stream
  time **re-checks** ownership/built-in (defense in depth, like the membership re-check) — a
  persona deleted between POST and stream falls back to `general`.

The selected persona is **recorded on the assistant turn** (in the per-turn JSONB) so a
reloaded conversation can show which persona produced each turn (the message chip in the demo)
and the Debug panel reflects it. This is **not** provenance on a shared entity (§8.2) — it is
metadata on the chat turn that used it, exactly like the Slice-13 plan/claims.

### Decision 4 — The persona prompt REPLACES the base `SYSTEM_PROMPT`; everything else composes unchanged

`chat/service.py` currently builds the system message as
`SYSTEM_PROMPT [+ "\n\n" + context_block] + PLAN_CERTAINTY_INSTRUCTION`. This slice changes
**only the base term**: the fixed `SYSTEM_PROMPT` constant becomes the **selected persona's
system prompt** (the `general` built-in's prompt is the seeded equivalent of today's neutral
`SYSTEM_PROMPT`, so the default behavior is unchanged). The Slice-12 `context_block` and the
Slice-13 `PLAN_CERTAINTY_INSTRUCTION` still append **after** the persona prompt, in the same
order. `_build_prompt` gains a `system_prompt: str` parameter (the resolved persona prompt);
the streamer resolves it once per turn and passes it in. No other prompt-assembly logic
changes, so the Slice-13 metadata block and the Slice-14 cloud/local routing are untouched.

### Decision 5 — Built-ins seeded idempotently at startup (not in a migration)

The repo has **no precedent for seeding rows in an Alembic migration** (checked: no
`bulk_insert` in any version file), but it **does** seed the admin user idempotently in the
`main.py` lifespan (`bootstrap_admin`). The four built-in personas follow that exact pattern:
`personas.service.bootstrap_system_personas(db)` **upserts** the four built-ins **by their
stable `slug`** (insert if missing; update the prompt/name if the slug already exists so a
prompt-wording change ships with a redeploy, never a duplicate). It is called from the
lifespan right after `bootstrap_admin`, is idempotent, and is non-fatal on error (a bad seed
must not crash the app). The Alembic migration in this slice adds **only the empty `personas`
table** (no data); the rows arrive at startup. This keeps migrations data-free (reversible,
testable) and makes the built-in prompt text a code constant (versioned, reviewable) rather
than frozen in a migration.

### Decision 6 — The four built-in system prompts are first-draft constants, tunable later

The four seeded prompts are short, distinct, and live as constants in
`personas/seed.py` (`SYSTEM_PERSONAS`: list of `{slug, name, system_prompt}`):

- **general** — the neutral assistant prompt (verbatim today's `SYSTEM_PROMPT`, so default
  behavior is byte-identical): "You are a penetration-testing assistant embedded in the
  Adeptus platform…".
- **recon** — reconnaissance/enumeration focus: surface-mapping, passive collection, service
  and endpoint enumeration first; defer exploitation.
- **web-exploit** — web-app exploitation focus: OWASP-style vuln classes, payload crafting,
  exploitation reasoning for in-scope authorized targets.
- **report-writer** — reporting focus: concise, client-ready summaries, severity framing,
  remediation phrasing; pulls together findings into prose.

Exact wording is decided at implementation time and is non-load-bearing (a prompt tweak is a
one-line constant change shipped on redeploy via the idempotent upsert). Flagged in Open
Questions only insofar as the human may want to review/approve the four prompt texts.

## Contract

OpenAPI delta. **One new feature with five endpoints** (CRUD over the caller's persona
library), **one changed request schema** (`ChatMessageCreate` gains `persona_id`), and
**one changed read schema** (`ChatMessageRead` gains `persona_id` / `persona_name` so a
reloaded conversation shows which persona produced each turn). The WebSocket frame contract is
**unchanged**. A contract change means `make generate-api` is required.

```yaml
openapi: "3.1.0"
info:
  title: Adeptus API — Slice 15 delta
  version: "0.15.0"

paths:
  /api/v1/personas:
    get:
      operationId: list_personas
      summary: >-
        List the personas available to the caller: the four global built-ins plus the
        caller's own custom personas (§5.3 / §5.4). Built-ins first, then the caller's
        custom personas (newest-first).
      security: [{ cookieAuth: [] }]
      responses:
        "200":
          content:
            application/json:
              schema: { $ref: "#/components/schemas/PersonaList" }
        "401": { description: Not authenticated }

    post:
      operationId: create_persona
      summary: Create a custom persona owned by the caller (§5.3 "create … their own").
      security: [{ cookieAuth: [] }]
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: "#/components/schemas/PersonaCreate" }
      responses:
        "201":
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Persona" }
        "401": { description: Not authenticated }
        "409": { description: The caller already has a custom persona with this name }

  /api/v1/personas/{persona_id}:
    patch:
      operationId: update_persona
      summary: >-
        Edit one of the caller's own custom personas (§5.3 "edit … their own"). Built-ins
        and other users' personas are not visible (404), so they cannot be edited.
      security: [{ cookieAuth: [] }]
      parameters:
        - { name: persona_id, in: path, required: true, schema: { type: string, format: uuid } }
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: "#/components/schemas/PersonaUpdate" }
      responses:
        "200":
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Persona" }
        "401": { description: Not authenticated }
        "404": { description: Persona not found or not owned by caller (built-ins included) }
        "409": { description: The caller already has another custom persona with this name }

    delete:
      operationId: delete_persona
      summary: >-
        Delete one of the caller's own custom personas (§5.3 "delete … their own"). A
        built-in or another user's persona returns 404 (cannot be deleted).
      security: [{ cookieAuth: [] }]
      parameters:
        - { name: persona_id, in: path, required: true, schema: { type: string, format: uuid } }
      responses:
        "204": { description: Deleted }
        "401": { description: Not authenticated }
        "404": { description: Persona not found or not owned by caller (built-ins included) }

components:
  schemas:
    Persona:
      type: object
      required: [id, name, system_prompt, is_builtin, created_at]
      properties:
        id: { type: string, format: uuid }
        name:
          type: string
          description: Human-readable persona name (verbatim, §5.5). Unique per owner.
        system_prompt:
          type: string
          description: The persona's distinct system prompt (§5.3), sent verbatim (§5.5).
        is_builtin:
          type: boolean
          description: >-
            True for the four global seeded personas (read-only, shared); false for a
            caller-owned custom persona (editable/deletable by the caller only).
        slug:
          oneOf: [{ type: string }, { type: "null" }]
          description: >-
            Stable slug for a built-in (general/recon/web-exploit/report-writer); null for
            custom personas. Drives the default-persona lookup.
        created_at: { type: string, format: date-time }

    PersonaList:
      type: object
      required: [items]
      properties:
        items:
          type: array
          items: { $ref: "#/components/schemas/Persona" }

    PersonaCreate:
      type: object
      required: [name, system_prompt]
      properties:
        name:
          type: string
          minLength: 1
          maxLength: 80
          description: Persona name; must be unique among the caller's own personas.
        system_prompt:
          type: string
          minLength: 1
          maxLength: 8192
          description: The persona's system prompt, stored and sent verbatim (§5.5).

    PersonaUpdate:
      type: object
      description: All fields optional; only provided fields are updated.
      properties:
        name:
          oneOf: [{ type: string, minLength: 1, maxLength: 80 }, { type: "null" }]
        system_prompt:
          oneOf: [{ type: string, minLength: 1, maxLength: 8192 }, { type: "null" }]

    # CHANGED (chat): the POST body gains an optional persona selector. Null/absent → the
    # `general` built-in. A foreign/unknown id falls back to `general` server-side (§17.1).
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
        persona_id:
          oneOf: [{ type: string, format: uuid }, { type: "null" }]
          description: >-
            The persona whose system prompt should shape THIS turn (§5.3). Must be a built-in
            or one of the caller's own personas; an unknown/foreign id falls back to the
            `general` built-in (§17.1, never errors). Null/absent → `general`. Chosen per send
            so the user can switch persona mid-chat without resetting the conversation.

    # CHANGED (chat): assistant rows surface which persona produced the turn, so a reloaded
    # conversation shows the persona chip and the Debug panel reflects it.
    ChatMessageRead:
      type: object
      required: [id, engagement_id, role, content, status, created_at]
      properties:
        id: { type: string, format: uuid }
        engagement_id: { type: string, format: uuid }
        role: { $ref: "#/components/schemas/ChatRole" }
        content: { type: string }
        status: { $ref: "#/components/schemas/ChatMessageStatus" }
        created_at: { type: string, format: date-time }
        plan:
          type: array
          items: { $ref: "#/components/schemas/PlanStep" }
          default: []
        claims:
          type: array
          items: { $ref: "#/components/schemas/Claim" }
          default: []
        persona_id:
          oneOf: [{ type: string, format: uuid }, { type: "null" }]
          description: The persona used for this assistant turn (null for user/pre-slice rows).
        persona_name:
          oneOf: [{ type: string }, { type: "null" }]
          description: >-
            The persona's display name at turn time (denormalized onto the turn so a renamed/
            deleted persona still labels the historical turn). Null for user/pre-slice rows.
```

The WebSocket frame contract is **unchanged** from Slice 13/14:

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

## Data model changes

Alembic migration written via the `write-alembic-migration` skill during implementation
(register the new `app/features/personas/models.py` import in `backend/alembic/env.py` first —
per the Alembic-autogenerate memory; recreate the autogenerated file as the non-root user).

**One new table.** **No columns added to any existing table** — the persona used for a turn is
recorded inside the **existing** `chat_messages.graph_context` JSONB (the per-turn metadata
seam from Slices 12/13/14), so no `chat_messages` column is added and no anti-pattern
(§8.2 / §17.4) is triggered. The built-in persona rows are **seeded at startup** (Decision 5),
not in the migration — the migration creates the empty table only.

- `personas` — a user's persona library plus the four global built-ins:
  - `id` UUID PK (`gen_random_uuid()`)
  - `user_id` UUID NULL — FK → `users.id` `ON DELETE CASCADE`. The owner for a **custom**
    persona; **NULL** for a built-in (shared). Not a provenance column on a shared entity —
    `personas` *is* the per-user library; ownership is its primary concept.
  - `name` VARCHAR(80) NOT NULL — human-readable, sent/rendered verbatim (§5.5).
  - `slug` VARCHAR(40) NULL — stable slug for a built-in
    (`general`/`recon`/`web-exploit`/`report-writer`); NULL for custom personas. Drives the
    default-persona (`general`) lookup and the idempotent seed upsert.
  - `system_prompt` TEXT NOT NULL — the persona's distinct system prompt (§5.3).
  - `is_builtin` BOOLEAN NOT NULL DEFAULT `false` — true for the four seeded rows.
  - `created_at` TIMESTAMPTZ NOT NULL DEFAULT `now()`
  - `updated_at` TIMESTAMPTZ NOT NULL DEFAULT `now()`
  - Constraints / indexes:
    - **CHECK** `(is_builtin = true AND user_id IS NULL AND slug IS NOT NULL) OR
      (is_builtin = false AND user_id IS NOT NULL AND slug IS NULL)` — a row is exactly one of
      "global built-in with a slug" or "user-owned custom" (no half-states).
    - **UNIQUE** `(slug)` WHERE `is_builtin` — one row per built-in slug; the seed upsert keys
      on it (a partial unique index, or a plain unique on `slug` since custom rows are NULL).
    - **UNIQUE** `(user_id, name)` WHERE `user_id IS NOT NULL` — a user cannot have two custom
      personas with the same name (the create/patch 409); built-ins (NULL `user_id`) are
      exempt. (Partial unique index.)
    - `ix_personas_user_id` on `(user_id)` — the "list my personas" read path.

No FK from `chat_messages` to `personas`: the persona used for a turn is denormalized
(`persona_id` + `persona_name`) into the turn's JSONB so a renamed/deleted persona still
labels the historical turn (and so chat does not gain a hard FK dependency on the personas
table). The id is a soft reference; the name is the durable label.

## Tasks

Numbered continuously across the whole slice (backend then frontend). Every commit subject
cites its task id, e.g. `feat(slice-15): add personas model (task 1)`.

### Backend tasks

Ordered. Each independently testable. Complexity: S/M/L.

1. **[S]** Add `app/features/personas/models.py` — the `Persona` ORM model on the shared
   `Base` (columns, the `CheckConstraint`, the two partial `UniqueConstraint`s/indexes, the
   `ix_personas_user_id` index per the Data model section). Register the module import in
   `backend/alembic/env.py`. No columns added to existing models.

2. **[S]** Add `app/features/personas/schemas.py` — `Persona` (`from_attributes=True`),
   `PersonaList`, `PersonaCreate` (name/system_prompt bounds), `PersonaUpdate` (all optional).
   Tests in `tests/test_schemas.py`: name/prompt min/max length; `PersonaUpdate` allows empty
   (no-op) and partial; `Persona` round-trips a built-in (slug set, user_id None) and a custom
   (slug None, user_id set).

3. **[S]** Add `app/features/personas/seed.py` — the `SYSTEM_PERSONAS` constant: the four
   built-ins `{slug, name, system_prompt}` (Decision 6), with `general`'s prompt **byte-equal**
   to chat's current `SYSTEM_PROMPT` so the default is unchanged. Tests in `tests/test_seed.py`:
   exactly four entries; slugs are `{general, recon, web-exploit, report-writer}`; `general`'s
   prompt matches the chat default (import-compared, so a drift fails the test).

4. **[M]** Add `app/features/personas/repository.py` — `list_for_user(db, *, user_id)`
   (built-ins ∪ caller's own, built-ins first then custom newest-first),
   `get_for_user(db, *, persona_id, user_id)` (a built-in OR a row owned by the caller; else
   None — the resolve/ownership chokepoint), `get_builtin_by_slug(db, *, slug)`,
   `create_custom(db, *, user_id, name, system_prompt)`,
   `update_custom(db, *, persona_id, user_id, name, system_prompt)` (owner-scoped),
   `delete_custom(db, *, persona_id, user_id)` (owner-scoped),
   `upsert_builtin(db, *, slug, name, system_prompt)` (the seed upsert). Tests in
   `tests/test_repository.py` (real async test DB): list returns built-ins + only the caller's
   own; `get_for_user` returns a built-in for anyone but a custom only for its owner (another
   user → None); name-uniqueness per user enforced; upsert inserts then updates idempotently;
   delete is owner-scoped.

5. **[M]** Add `app/features/personas/service.py` — domain logic + the ownership chokepoints:
   - `list_personas(db, *, requester) -> PersonaList`.
   - `create_persona(db, *, requester, name, system_prompt) -> Persona` (raises a domain
     `PersonaNameConflictError` → 409 on a duplicate name for that user).
   - `update_persona(db, *, requester, persona_id, name, system_prompt) -> Persona` — resolves
     a **caller-owned custom** row (a built-in or foreign row → `NotFoundError` → 404, so a
     built-in cannot be edited and another user's persona is invisible); 409 on name clash.
   - `delete_persona(db, *, requester, persona_id) -> None` — same owner-scoping (404 for
     built-in/foreign).
   - `resolve_for_turn(db, *, persona_id, user_id) -> Persona` — the chat seam: returns the
     persona if it is a built-in or owned by the caller; on `None`/unknown/foreign id falls
     back to the `general` built-in (never raises — §17.1, no existence disclosure of another
     user's persona). This is the single function chat calls.
   - `bootstrap_system_personas(db) -> int` — idempotent seed: upsert each `SYSTEM_PERSONAS`
     entry by slug; returns the count seeded/updated (Decision 5).
   - Tests in `tests/test_service.py`: `test_list_returns_builtins_plus_own`,
     `test_create_custom_persona`, `test_create_duplicate_name_409`,
     `test_update_builtin_404` (a built-in cannot be edited),
     `test_update_other_users_persona_404`, `test_delete_builtin_404`,
     `test_resolve_returns_owned_custom`, `test_resolve_foreign_id_falls_back_to_general`
     (§17.1 — a foreign/unknown id never errors and never uses another user's prompt),
     `test_resolve_null_returns_general`, `test_bootstrap_seeds_four_then_idempotent`
     (second call inserts nothing, updates in place).

6. **[M]** Add `app/features/personas/router.py` — `GET` / `POST /api/v1/personas`,
   `PATCH` / `DELETE /api/v1/personas/{persona_id}` (all depend on `get_current_user`).
   `NotFoundError` → 404 and the new `PersonaNameConflictError` → 409 translate via the
   registered handlers (add a 409 handler if none maps — same pattern as chat's inline 409,
   or reuse the core `ConflictError` handler if it exists). Tests in `tests/test_router.py`
   (`AsyncClient` + session override): `test_list_personas_200_builtins_and_own`,
   `test_create_persona_201`, `test_create_duplicate_name_409`,
   `test_patch_own_persona_200`, `test_patch_builtin_404`, `test_patch_other_user_404`,
   `test_delete_own_persona_204`, `test_delete_builtin_404`, `test_personas_unauthenticated_401`.

7. **[S]** Wire the personas router in `app/main.py` (`include_router(personas_router)`) and
   call `personas_service.bootstrap_system_personas(db)` in the lifespan right after
   `bootstrap_admin` (idempotent, non-fatal on error — wrap in the same try/except logging
   shape). Test: a startup-bootstrap unit test (mirroring the admin-bootstrap test) asserts the
   four built-ins exist after one lifespan pass and are unchanged after a second.

8. **[M]** Extend `app/features/chat/schemas.py` — add `persona_id: UUID | None = None` to
   `ChatMessageCreate`; add `persona_id: UUID | None` and `persona_name: str | None` to
   `ChatMessageRead`. Tests in `tests/test_schemas.py`: `persona_id` defaults `None`;
   `ChatMessageRead` round-trips with and without the persona fields.

9. **[L]** Extend `app/features/chat/service.py` to thread the persona through the turn
   (Decisions 3 + 4):
   - `send_message(...)` gains `persona_id: UUID | None = None`; after the membership/archived/
     egress gates, call `personas_service.resolve_for_turn(db, persona_id=persona_id,
     user_id=requester.id)` and **stash the resolved `persona_id` + `persona_name`** on the
     pending assistant row's `graph_context` `inputs` stash (extend `_input_stash`). (Resolving
     here — not just at stream time — lets the read schema and the audit reflect the *actual*
     persona even if the row is deleted before streaming.)
   - `stream_assistant_reply(...)` reads the stashed `persona_id`, re-resolves via
     `resolve_for_turn` (defense in depth: a persona deleted between POST and stream falls back
     to `general`), and passes the persona's `system_prompt` into `_build_prompt(...,
     system_prompt=...)`. `_build_prompt` gains a `system_prompt: str` param that **replaces**
     the module `SYSTEM_PROMPT` constant as the base term (context_block + PLAN_CERTAINTY_
     INSTRUCTION still append after it, unchanged).
   - Persist `persona_id` / `persona_name` into the turn's JSONB at finalize (merge alongside
     plan/claims/subset — never clobber); surface them in `_to_message_read` and `_to_turn_debug`
     reads.
   - Widen `_emit_ai_call` payload with `persona_id` (str) + `persona_name` (the name is
     non-secret).
   - Tests in `tests/test_service.py` (personas resolve + Ollama/Anthropic + audit **mocked**):
     `test_send_resolves_and_stashes_persona`,
     `test_send_foreign_persona_falls_back_to_general` (§17.1),
     `test_prompt_uses_persona_system_prompt` (the built prompt's leading system content is the
     persona's prompt, not the old fixed constant),
     `test_prompt_composes_persona_then_context_then_instruction` (order preserved),
     `test_default_general_prompt_byte_equal_to_legacy` (default behavior unchanged),
     `test_persona_recorded_on_turn_and_in_audit`,
     `test_persona_deleted_between_post_and_stream_falls_back` (re-resolve at stream time),
     `test_persona_prompt_not_redacted` (§5.5 — passed through verbatim).
   - Test command: `make test-backend` (`pytest app/features/chat/tests/test_service.py`).

10. **[S]** `app/features/chat/router.py` — thread `persona_id=body.persona_id` into the
    `service.send_message(...)` call (no new endpoint; the POST already exists). Confirm the
    changed `ChatMessageCreate`/`ChatMessageRead` flow through. Tests in
    `tests/test_router.py`: `test_post_message_accepts_persona_id`,
    `test_post_foreign_persona_still_201_general_fallback`,
    `test_list_messages_response_carries_persona_fields`.

11. **[S]** Add Alembic migration for the empty `personas` table via the
    `write-alembic-migration` skill (table + check + partial uniques + index; **no seed data**).
    Confirm `make migrate` applies it cleanly against a fresh DB and `alembic downgrade -1`
    reverts it.
    - Test command: `make migrate` then `alembic downgrade -1` (in the backend container).

### Frontend tasks

Numbering continues from the backend tasks.

12. **[S]** Run `make generate-api` to regenerate types into `frontend/src/shared/api/`; commit
    the updated `frontend/openapi.json` (adds `Persona`, `PersonaList`, `PersonaCreate`,
    `PersonaUpdate`; `persona_id` on `ChatMessageCreate`; `persona_id`/`persona_name` on
    `ChatMessageRead`).
    - Test command: `make generate-api` then `make lint`.

13. **[M]** Add `frontend/src/features/personas/api.ts` — TanStack Query hooks
    `usePersonas()` (`GET`, `personaKeys` factory), `useCreatePersona()`, `useUpdatePersona()`,
    `useDeletePersona()` (each invalidates the list on settle; create/update surface the 409 as
    a name-conflict error). Tests in `__tests__/api.test.tsx` (mock `api.GET`/`POST`/`PATCH`/
    `DELETE`): list returns built-ins + own; create surfaces 409; delete invalidates.
    - Test command: `make test-frontend` (`vitest run src/features/personas/api.test.tsx`).

14. **[S]** Add `frontend/src/features/personas/store.ts` — a Zustand store holding the
    **ephemeral selected `personaId` per engagement** (defaulting to the `general` built-in's
    id once the list loads), mirroring the Slice-08 pin store. Tests: defaults to general;
    select updates; clearing falls back to general.
    - Test command: `make test-frontend` (`vitest run src/features/personas/store.test.ts`).

15. **[M]** Add `frontend/src/features/personas/components/PersonaSwitcher.tsx` + test — a
    shadcn `Select` listing built-ins then the caller's custom personas, bound to the store,
    with a "Manage personas" affordance. Pure-ish presentational (props: list + selected +
    onChange + onManage). Tests: renders built-ins + custom; selecting calls onChange; shows a
    "built-in" affordance on seeded entries.
    - Test command: `make test-frontend` (`vitest run src/features/personas/components/PersonaSwitcher.test.tsx`).

16. **[M]** Add `frontend/src/features/personas/components/ManagePersonasPanel.tsx` +
    `PersonaForm.tsx` + tests — a panel listing personas (built-ins read-only with a badge;
    custom with Edit/Delete), a create/edit form (name + system_prompt), wired to the
    `usePersonas`/`useCreatePersona`/`useUpdatePersona`/`useDeletePersona` hooks. Tests: built-in
    rows have no edit/delete; create form validates non-empty; edit updates; delete confirms and
    removes; name-conflict 409 shows an inline error.
    - Test command: `make test-frontend` (`vitest run src/features/personas/components/ManagePersonasPanel.test.tsx`).

17. **[M]** Wire the switcher into the chat send flow (`ChatComposer.tsx` / `ChatPanel.tsx` +
    the `useSendChatMessage` mutation in `chat/api.ts`): render `<PersonaSwitcher>` in the
    composer; on send, pass the store's selected `persona_id` in the POST body (alongside the
    Slice-12 node-id lists + Slice-14 `confirmed_egress`). The persona is read **per send** so
    switching mid-chat takes effect on the next turn. Update `ChatComposer.test.tsx` /
    `ChatPanel.test.tsx`: send includes the selected `persona_id`; switching persona changes the
    next send's body; default is `general`.
    - Test command: `make test-frontend` (`vitest run src/features/chat/components/ChatPanel.test.tsx`).

18. **[S]** Render the **persona chip** on assistant turns in `ChatMessageList.tsx` from
    `ChatMessageRead.persona_name` (small label, omitted when null), so a reloaded conversation
    shows which persona produced each turn. Update `ChatMessageList.test.tsx`: assistant rows
    with a persona name show the chip; user/pre-slice rows show none. Verify coverage ≥ 60% on
    `src/features/personas/` and `src/features/chat/`; `make lint` clean (no `any`; narrow via
    generated types).
    - Test command: `make test-frontend` then `make lint`.

## Test plan

- **Unit — backend** (coverage ≥ 80% on `app/features/personas/` and on `app/features/chat/`):
  - `personas/tests/test_schemas.py` — name/prompt bounds; `PersonaUpdate` partial/empty;
    built-in vs custom round-trip.
  - `personas/tests/test_seed.py` — four entries; the four slugs; `general` prompt ≡ chat
    default (drift fails).
  - `personas/tests/test_repository.py` (real async test DB) — list union scoping; `get_for_user`
    built-in-for-anyone vs custom-owner-only; per-user name uniqueness; idempotent upsert;
    owner-scoped delete.
  - `personas/tests/test_service.py` — the ten `test_*` names in backend task 5, centered on the
    ownership chokepoints (built-in/foreign edit-delete → 404) and the `resolve_for_turn`
    fallback-to-general (§17.1) — the load-bearing isolation behavior.
  - `personas/tests/test_router.py` — the nine `test_*` names in backend task 6.
  - `personas/tests/test_bootstrap.py` — task 7: four built-ins after one pass; unchanged after a
    second (idempotent), mirroring the admin-bootstrap test.
  - `chat/tests/test_schemas.py` — `persona_id` default `None`; `ChatMessageRead` with/without
    persona fields.
  - `chat/tests/test_service.py` — the eight `test_*` names in backend task 9, including the
    no-redaction persona-prompt assertion (§5.5), the order-preserving prompt composition, the
    byte-equal default (behavior unchanged), and the foreign-persona fallback (§17.1).
  - `chat/tests/test_router.py` — the three `test_*` names in backend task 10.
- **Unit — frontend** (coverage ≥ 60% on `src/features/personas/` and `src/features/chat/`):
  - `personas/api.test.tsx` — list, create-409, delete-invalidate.
  - `personas/store.test.ts` — default general; select; fallback.
  - `PersonaSwitcher.test.tsx` — built-ins + custom; onChange; built-in affordance.
  - `ManagePersonasPanel.test.tsx` / `PersonaForm.test.tsx` — built-ins read-only; create/edit/
    delete; name-conflict inline error.
  - `ChatComposer.test.tsx` / `ChatPanel.test.tsx` — send carries selected `persona_id`;
    switching changes the next send; default general.
  - `ChatMessageList.test.tsx` — persona chip on assistant rows; none on user rows.
- **Integration** (`@pytest.mark.integration`, real Postgres; **LLM clients mocked** — external
  services never hit, CLAUDE.md):
  - `test_persona_shapes_turn_prompt_and_audit` — seed built-ins; POST a message with the
    `recon` built-in's id; open the WS with a faked stream; assert the raw prompt's leading
    system content is the **recon** system prompt (not the neutral default), the assistant row
    records `persona_name="Recon"`, and the single `ai_call` audit entry carries the persona id
    + name. **Headline §5.3 + §14 happy-path.**
  - `test_switch_persona_mid_conversation` — same conversation: first turn with `recon`, second
    turn with `report-writer`; assert each turn's prompt used the respective persona and the
    prior turns remain in the verbatim window unchanged (no reset, §5.4).
  - `test_custom_persona_private_per_user` — user A creates a custom persona and uses it; user B
    (same engagement) cannot see it in `GET /personas`, and a POST from B with A's persona id
    falls back to `general` (the prompt sent is general, not A's) — §5.4 / §17.1.
  - `test_default_persona_behavior_unchanged` — a POST with no `persona_id` produces the exact
    same leading system prompt as the pre-slice path (byte-equal to the legacy `SYSTEM_PROMPT`).
- **E2E** (Playwright, opt-in stack) — `personas.spec.ts` (Ollama stubbed deterministically — no
  real model in CI; external-service rule): log in, open an engagement, switch the composer to
  **Recon**, send a message, assert the persona chip shows on the reply; open **Manage personas**,
  create a custom persona, select it, send, assert it is used; delete it and assert the switcher
  falls back to General. As a second user, assert the first user's custom persona is not listed.

## Acceptance criteria

- `make test` passes (ruff + mypy + eslint + tsc + pytest + vitest + playwright); coverage gates
  hold (≥80% backend `personas` + `chat`, ≥60% frontend `personas` + `chat`).
- `make lint` passes with no new errors.
- `make migrate` applies the new `personas` table migration cleanly against a fresh Postgres
  container; `alembic downgrade -1` reverts it. After app startup the four built-in personas
  exist (seeded idempotently in the lifespan).
- `make generate-api` produces an updated `frontend/openapi.json` containing the persona schemas
  and the changed `ChatMessageCreate` / `ChatMessageRead`; regenerated types are committed.
- `make dev` brings up the stack; manual demo:
  1. Open an engagement → the composer shows the persona switcher with **General** (default),
     **Recon**, **Web Exploit**, **Report Writer**.
  2. Pick **Recon**, send a message → the reply is recon-flavored; the assistant turn shows a
     "Recon" persona chip (§5.3).
  3. Switch to **Report Writer** and send again — **mid-chat**, no reset; the prior turns stay in
     the conversation (§5.4).
  4. Open **Manage personas** → create a custom persona, select it, send → it shapes the turn;
     edit its prompt → live next turn; delete it → switcher falls back to **General**.
  5. As a second member, the four built-ins appear but **none** of the first user's custom
     personas (§5.4 per-user privacy).
  6. Open the **Debug** panel on a reply → the raw prompt's leading system content is the selected
     persona's prompt (§14).
- `gh pr view` shows green CI.

## Risks

- **Risk 1 — Cross-user persona leak (the primary isolation risk).** A bug in the
  `is_builtin OR user_id = caller` scoping, in `get_for_user`, or in `resolve_for_turn` could let
  one user list, edit, or *use* another user's private persona prompt (violating §5.4 / §17.1).
  Mitigation: ownership is enforced in the repository read (built-in ∪ owner only), the
  edit/delete chokepoints (built-in/foreign → 404, no existence disclosure), and the
  resolve-for-turn fallback (foreign/unknown id → `general`, never another user's prompt, never
  an error). Guarded by `test_resolve_foreign_id_falls_back_to_general`,
  `test_update_other_users_persona_404`, and the integration `test_custom_persona_private_per_user`.
- **Risk 2 — A built-in becomes editable/deletable.** If the API let a user PATCH/DELETE a
  built-in, a shared system prompt could be mutated for everyone (or a user could blank their own
  view of it). Mitigation: edit/delete resolve only **caller-owned custom** rows; a built-in
  returns 404; the table CHECK forbids a built-in from carrying a `user_id`. Guarded by
  `test_patch_builtin_404` / `test_delete_builtin_404`.
- **Risk 3 — Default behavior drift.** If `general`'s seeded prompt diverges from the legacy
  `SYSTEM_PROMPT`, the no-persona default behavior silently changes. Mitigation: `general`'s
  prompt is **byte-equal** to chat's constant, asserted by `test_seed.py` (import-compared) and
  `test_default_general_prompt_byte_equal_to_legacy` / the integration
  `test_default_persona_behavior_unchanged`. (At implementation, the chat `SYSTEM_PROMPT` constant
  may be *moved* into `personas/seed.py` as the single source of truth and re-imported by chat,
  so there is exactly one copy — preferred; flagged in Open Questions.)
- **Risk 4 — Persona prompt redaction temptation.** A persona prompt could echo secret-looking
  text a user pasted in. Forbidden to strip (§5.5 / CLAUDE.md). Mitigation: the persona prompt is
  passed through verbatim into the system message; `test_persona_prompt_not_redacted` asserts it.
  The cloud egress-friction gate (Slice 14) still scans the **user message** independently of
  persona; a persona prompt is not scanned (it is the user's own configured instruction, not a
  per-turn egress) — noted in Open Questions in case the human wants persona prompts scanned on
  cloud sends too.
- **Risk 5 — Seed upsert clobbering custom data / duplicating built-ins.** A non-idempotent seed
  could insert duplicate built-ins on each boot or overwrite a custom row. Mitigation: the seed
  upserts **by built-in slug** under the partial-unique `(slug) WHERE is_builtin` and only touches
  `is_builtin = true` rows; custom rows (NULL slug) are never matched.
  `test_bootstrap_seeds_four_then_idempotent` guards it.
- **Risk 6 — TOCTOU between POST-time resolve and stream-time prompt build.** A persona deleted
  (or renamed) between the POST and the WS stream could leave the streamer with a dangling id.
  Mitigation: the streamer **re-resolves** at stream time and falls back to `general` on a missing
  persona; the persona **name** is denormalized onto the turn at finalize so the label is durable.
  `test_persona_deleted_between_post_and_stream_falls_back` guards it.
- **Risk 7 — Reusing the per-turn JSONB for yet another concern.** Adding `persona_id`/
  `persona_name` keys to the same `graph_context` blob as subset + plan + claims (Slices 12/13/14)
  risks a finalize writer clobbering a sibling key. Mitigation: the finalize step **merges** (does
  not overwrite) keys, as Slice 13 already established; a service test asserts subset + plan +
  persona keys all survive on the one row. (If the blob keeps growing, splitting into a dedicated
  `chat_turn_metadata` table becomes worthwhile — flagged repeatedly since Slice 12; still not
  needed here.)

## Open questions for the human — RESOLVED 2026-06-05

All six resolved by the human at planning time. The four design/scope/security forks were
decided explicitly; the two low-stakes ones took the proposed default. No task, data-model, or
contract change resulted — every answer matched the plan as written.

1. **Per-user persona library vs. team-shared custom personas.** → **RESOLVED: per-user only.**
   Custom personas are **private to their creator** (§5.3 "their own"; matches the per-user
   chat-privacy model of §5.4). The four seeded personas remain global read-only built-ins. No
   team-sharing affordance in v1. (Keeps this slice's isolation surface = the reviewed per-user
   chat shape; consistent with `Risky: no`.)

2. **Are the four seeded prompt texts acceptable as first-draft constants?** → **RESOLVED:
   accept implementer-drafted (default).** The implementer drafts the four prompts (Decision 6),
   `general` byte-equal to today's neutral prompt; wording is tunable later on redeploy via the
   idempotent upsert. No pre-implementation review of the prompt texts required.

3. **Single source of truth for the `general` prompt.** → **RESOLVED: move it, single source.**
   Chat's `SYSTEM_PROMPT` constant is **moved into `personas/seed.py`** and re-imported by chat —
   exactly one copy, drift impossible by construction (the `general` built-in IS the default).
   Adds the small chat → personas import (same direction the resolve seam already takes).
   `test_seed.py` still asserts `general` ≡ the default as a regression guard.

4. **Should a persona carry a model preference (local / cloud)?** → **RESOLVED: prompt-only.**
   A persona is **name + system prompt** only (§5.3). Local/cloud routing stays governed by the
   engagement privacy toggle (§5.1, Slice 14). No per-persona model field in v1.

5. **Should a built-in be clonable into an editable custom copy?** → **RESOLVED: defer
   (default).** Built-ins stay read-only; a user who wants a tweaked recon prompt creates a fresh
   custom persona. "Duplicate built-in to edit" is out of scope for v1.

6. **Should persona system prompts be egress-scanned on cloud-enabled sends?** → **RESOLVED:
   leave as-is for v1.** The Slice-14 gate keeps scanning the **user message** only; persona
   prompts (user-authored configuration, not per-turn content) are **not** scanned, keeping the
   already-reviewed egress surface closed. Revisit with Slice 18 (delegation) if needed.

## Security review required?

**No.** This slice does not touch auth (it reuses the established `get_current_user` dependency
and the per-user ownership scoping pattern without changing them), MCP, the single-writer graph
process, RAG isolation, secrets storage, or the approval flow. It does **not** implement egress:
a persona only changes the **system-prompt text**; the cloud/local routing and the Slice-14
pattern-friction egress gate are reused **unchanged** and fire independently of persona (so the
already-reviewed egress surface is not re-opened). It touches the audit log only by widening the
already-reviewed `ai_call` payload with `persona_id` / `persona_name` (a non-secret display name)
— no new audit action, no new table, no hash-chain change.

The one surface a reviewer would care about is **per-user isolation of custom personas** (§5.4 /
§17.1, Risks 1–2 + 6): a custom persona must never be listable, editable, deletable, or *usable*
by anyone but its creator, and a foreign/unknown `persona_id` on a send must fall back to
`general` (never another user's prompt, never an existence-disclosing error). This is the same
ownership-scoping shape already reviewed for per-user chat (Slice 11) and is covered by the named
service/router/integration tests above. If the reviewer of the day wants to eyeball it because it
introduces a new user-owned table, those tests (plus the byte-equal-default guard, Risk 3, and the
no-redaction guarantee on persona-prompt text, Risk 4 / §5.5) are the surfaces to confirm. The
PROJECT_PLAN marks this slice **not risky**, consistent with this assessment.

## Progress

(The stop-checkpoint hook and compact-handoff skill append here. Leave empty at planning time.)
- 2026-06-05T18:24:03Z — 6dcc361 Slice 14: Cloud LLM + pattern-friction egress (#41)
- 2026-06-05T18:25:12Z — 6dcc361 Slice 14: Cloud LLM + pattern-friction egress (#41)
